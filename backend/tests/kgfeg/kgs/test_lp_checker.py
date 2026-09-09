"""Red-team independent checking with bounded evidence and offline model responses."""

# Standard Library
import json
import socket

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import UUID

# Third Party Library
import pytest

from pydantic import ValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage, RunUsage

# Package Library
from kgfeg.config import Settings
from kgfeg.kgs import agents, llm, prompts
from kgfeg.kgs.lp_generation import (
    LPGenerationRequest,
    build_lp_generation_requests,
    write_lp_generation_request_artifacts,
)
from kgfeg.kgs.schemas import (
    LPDecision,
    LPDirection,
    LPGenerationResponse,
    LPGenerationValidationIssue,
    LPGenerationValidationVerdict,
)
from kgfeg.kgs.utils import KGDirs
from kgfeg.kgs.validators import (
    verify_lp_generation_response_integrity,
    verify_lp_generation_validation_integrity,
)
from kgfeg.model_registry import ModelConfig
from kgfeg.page_ir_extraction.validators import QualityError
from tests.kgfeg.kgs import test_lp_candidates as _candidates
from tests.kgfeg.kgs import test_lp_generation as _fixtures

_DOC_KEY = "synthetic-selection-document"
_FILES = (
    "lp_candidate_pairs.jsonl",
    "lp_candidate_summary.json",
    "lp_generation_requests.jsonl",
    "lp_generation_requests_manifest.json",
)
_PROFILES = (
    "ghana_english",
    "ghana_math",
    "madhi_math",
    "nigeria_math",
    "pratham_science",
    "rwanda_math",
)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject external connections during every checker test.

    Parameters
    ----------
    monkeypatch
        Restoring network guard.
    """
    guard = Mock(side_effect=AssertionError("Checker tests must remain offline."))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)


def _draft(request: LPGenerationRequest) -> LPGenerationResponse:
    """Create complete negative judgments retaining all supplied warnings.

    Parameters
    ----------
    request
        Actual bounded pair population.

    Returns
    -------
    LPGenerationResponse
        Synthetic process fixture, with no claimed semantic truth.
    """
    return LPGenerationResponse.model_validate(
        {
            "judgments": [
                {
                    "confidence": 0.75,
                    "decision": "no_relation",
                    "direction": None,
                    "first_sfi_uuid": pair.first_sfi_uuid,
                    "pair_id": pair.pair_id,
                    "rationale": "Synthetic negative judgment for process testing.",
                    "second_sfi_uuid": pair.second_sfi_uuid,
                    "warnings": list(request.warnings),
                }
                for pair in request.pairs
            ],
            "request_content_hash": request.request_content_hash,
            "request_id": request.request_id,
        }
    )


def _issue(*, pair_id: str | None = None, severity: str = "error") -> dict[str, Any]:
    """Construct a synthetic independent assessment issue.

    Parameters
    ----------
    pair_id
        Requested pair or request-wide reference.
    severity
        Error for correction or advisory warning for acceptance.

    Returns
    -------
    dict[str, Any]
        Schema-shaped issue payload.
    """
    return {
        "issue_type": "evidence_interpretation",
        "message": "Synthetic assessment requires another interpretation.",
        "pair_id": pair_id,
        "severity": severity,
    }


def _request() -> LPGenerationRequest:
    """Build a real batch with multiple distinct pairs and endpoints.

    Returns
    -------
    LPGenerationRequest
        Several same-rank pairs with both directional permissions.
    """
    return build_lp_generation_requests(
        as_lc_bundle=_fixtures._bundle(),
        doc_key=_DOC_KEY,
        kg_config=_fixtures._config(batch=3),
    ).requests[0]


def _verdict(
    *, correction: LPGenerationResponse | None = None, request: LPGenerationRequest
) -> LPGenerationValidationVerdict:
    """Create complete acceptance or replacement without deriving semantic labels.

    Parameters
    ----------
    correction
        Optional complete replacement fixture.
    request
        Material identity to copy.

    Returns
    -------
    LPGenerationValidationVerdict
        Intrinsically parsed checker proposal.
    """
    return LPGenerationValidationVerdict.model_validate(
        {
            "corrected_response": correction,
            "issues": [_issue()] if correction is not None else [],
            "passed": correction is None,
            "rationale": "Synthetic independent assessment of bounded evidence.",
            "request_content_hash": request.request_content_hash,
            "request_id": request.request_id,
        }
    )


@pytest.mark.parametrize(argnames="attack", argvalues=["schema", "identity", "partial"])
@pytest.mark.parametrize(argnames="prompted", argvalues=[False, True])
@pytest.mark.parametrize(argnames="retries", argvalues=[0, 1, 3])
@pytest.mark.parametrize(argnames="succeeds", argvalues=[False, True])
def test_agent_retries_schema_and_request_integrity_with_exact_budget(
    attack: str,
    prompted: bool,
    retries: int,
    succeeds: bool,
) -> None:
    """Exercise real output parsing, integrity callbacks, and usage on local models.

    Parameters
    ----------
    attack
        Malformed schema or intrinsically valid integrity violation.
    prompted
        Provider output mode, JSON text or structured tool result.
    retries
        Permitted additional checker attempts.
    succeeds
        Whether the final allowed attempt returns a complete valid correction.
    """
    request = _request()
    draft = _draft(request)
    correction = draft.model_copy(deep=True)
    correction.judgments[0].decision = "needs_review"
    valid = _verdict(correction=correction, request=request).model_dump(mode="json")
    invalid = deepcopy(valid)
    if attack == "schema":
        invalid["passed"] = "false"
    elif attack == "identity":
        invalid["request_content_hash"] = "0" * 64
    else:
        invalid["corrected_response"]["judgments"].pop()
    calls: list[AgentInfo] = []

    def _respond(messages: list[Any], info: AgentInfo) -> ModelResponse:
        """Follow the required local model callback protocol.

        Parameters
        ----------
        messages
            Agent conversation containing request and retry feedback.
        info
            Output contract and available tools.

        Returns
        -------
        ModelResponse
            Controlled structured proposal with synthetic usage.
        """
        assert messages
        assert info.function_tools == []
        calls.append(info)
        output = valid if succeeds and len(calls) > retries else invalid
        return ModelResponse(
            parts=(
                [TextPart(content=json.dumps(output))]
                if prompted
                else [ToolCallPart(args=output, tool_name=info.output_tools[0].name)]
            ),
            usage=RequestUsage(input_tokens=7, output_tokens=3),
        )

    config = Mock(spec=ModelConfig)
    config.model = FunctionModel(function=_respond)
    config.kgs_settings.return_value = {"max_tokens": 1234}
    config.wrap_output_type.return_value = ModelConfig(
        model="anthropic:claude-sonnet-4-6" if prompted else "openai:gpt-5.2"
    ).wrap_output_type(LPGenerationValidationVerdict)
    agent = agents.create_lp_generation_validation_agent(
        draft_response=draft,
        instructions="Independently check bounded evidence.",
        lp_generation_request=request,
        max_retries=retries,
        model_config=config,
        verify_integrity_fn=verify_lp_generation_validation_integrity,
    )
    config.kgs_settings.assert_called_once_with("learning_progressions")
    config.wrap_output_type.assert_called_once_with(LPGenerationValidationVerdict)
    if succeeds:
        run = agent.run_sync("Synthetic independent checker request")
        assert isinstance(run.output, LPGenerationValidationVerdict)
        assert run.output.model_dump(mode="json") == valid
        assert run.usage().requests == retries + 1
        assert run.usage().input_tokens == 7 * (retries + 1)
        assert run.usage().output_tokens == 3 * (retries + 1)
    else:
        with pytest.raises(expected_exception=UnexpectedModelBehavior):
            agent.run_sync("Synthetic independent checker request")
    assert len(calls) == retries + 1


@pytest.mark.parametrize(argnames="corrects", argvalues=[False, True])
@pytest.mark.parametrize(
    argnames="decision,direction",
    argvalues=[
        ("buildsTowards", "first_to_second"),
        ("buildsTowards", "second_to_first"),
        ("relatesTo", None),
        ("no_relation", None),
        ("needs_review", None),
    ],
)
@pytest.mark.parametrize(argnames="reordered", argvalues=[False, True])
def test_complete_acceptance_and_correction_preserve_pair_identity(
    corrects: bool,
    decision: LPDecision,
    direction: LPDirection | None,
    reordered: bool,
) -> None:
    """Permit every admissible outcome and reordered judgments without mutating input.

    Parameters
    ----------
    corrects
        Whether the checker replaces the draft or accepts it.
    decision
        Semantic outcome chosen only for process validation.
    direction
        Direction relative to canonical endpoints.
    reordered
        Whether list order differs while exact pair coverage remains intact.
    """
    request = _request()
    draft = _draft(request)
    proposal = draft.model_copy(deep=True)
    proposal.judgments[0].decision = decision
    proposal.judgments[0].direction = direction
    proposal.judgments[0].confidence = 0.0
    proposal.judgments[-1].confidence = 1.0
    if reordered:
        proposal.judgments.reverse()
    if not corrects:
        draft = proposal
    verdict = _verdict(correction=proposal if corrects else None, request=request)
    before = (draft.model_dump(mode="json"), verdict.model_dump(mode="json"))
    verify_lp_generation_validation_integrity(
        draft_response=draft,
        lp_generation_request=request,
        validation_verdict=verdict,
    )
    assert before == (draft.model_dump(mode="json"), verdict.model_dump(mode="json"))
    assert len({row.pair_id for row in proposal.judgments}) == len(request.pairs)


@pytest.mark.parametrize(argnames="filename", argvalues=_FILES)
@pytest.mark.parametrize(
    argnames="mutation",
    argvalues=[
        "absent",
        "empty",
        "malformed",
        "tampered",
    ],
)
def test_execution_blocks_incomplete_population_before_checker_creation(
    filename: str,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    tmp_path: Path,
) -> None:
    """A valid first request cannot conceal incomplete or corrupt later artifacts.

    Parameters
    ----------
    filename
        Required artifact to attack.
    monkeypatch
        Restoring checker factory guard.
    mutation
        Missing, interrupted, or materially invalid artifact.
    tmp_path
        Isolated candidate/request directory.
    """
    bundle, config, dirs = (
        _fixtures._bundle(),
        _fixtures._config(),
        KGDirs(root=tmp_path),
    )
    population = write_lp_generation_request_artifacts(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=config,
        kg_dirs=dirs,
    )
    path = tmp_path / filename
    if mutation == "absent":
        path.unlink()
    elif mutation == "empty":
        path.write_bytes(b"")
    elif mutation == "malformed":
        path.write_bytes(path.read_bytes() + b'{"unfinished":')
    elif filename.endswith(".jsonl"):
        rows = path.read_bytes().splitlines(keepends=True)
        assert len(rows) > 1
        rows[-1] = rows[0]
        path.write_bytes(b"".join(rows))
    else:
        row = json.loads(path.read_bytes())
        row["total_candidate_pairs"] += 1
        path.write_text(json.dumps(row))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    factory = Mock(side_effect=AssertionError("Invalid artifacts reached checker."))
    monkeypatch.setattr(
        name="create_lp_generation_validation_agent", target=llm, value=factory
    )
    tracker = llm.KGUsageTracker()
    with pytest.raises(expected_exception=(OSError, ValueError)):
        llm.check_learning_progressions_for_request(
            as_lc_bundle=bundle,
            doc_key=_DOC_KEY,
            draft_response=_draft(population.requests[0]),
            kg_config=config,
            kg_dirs=dirs,
            request_index=0,
            usage_tracker=tracker,
        )
    factory.assert_not_called()
    totals = tracker.to_dict()["totals"]
    assert isinstance(totals, dict)
    assert totals["requests"] == 0
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize(
    argnames="mutation",
    argvalues=[
        "batch",
        "checker_instructions",
        "checker_retry",
        "doc_key",
        "producer_instructions",
        "upstream",
    ],
)
def test_execution_blocks_stale_material_before_checker_creation(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    tmp_path: Path,
) -> None:
    """Recompute actual material inputs before any checker can run.

    Parameters
    ----------
    monkeypatch
        Restoring settings, prompt, and factory guards.
    mutation
        Material input changed after complete artifact writing.
    tmp_path
        Isolated complete population directory.
    """
    bundle, config, dirs = (
        _fixtures._bundle(),
        _fixtures._config(),
        KGDirs(root=tmp_path),
    )
    population = write_lp_generation_request_artifacts(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=config,
        kg_dirs=dirs,
    )
    doc_key = _DOC_KEY
    policy = config.learning_progressions
    if mutation == "batch":
        policy.request_batch_size = 3
    elif mutation == "checker_instructions":
        policy.checker_instructions += " Changed synthetic assessment."
    elif mutation == "checker_retry":
        policy.retry.checker_max_retries += 1
    elif mutation == "doc_key":
        doc_key += "-changed"
    elif mutation == "producer_instructions":
        policy.producer_instructions += " Changed synthetic producer policy."
    else:
        bundle.items[0].description += " Changed authoritative source."
    factory = Mock(side_effect=AssertionError("Stale material reached checker."))
    monkeypatch.setattr(
        name="create_lp_generation_validation_agent", target=llm, value=factory
    )
    with pytest.raises(expected_exception=ValueError):
        llm.check_learning_progressions_for_request(
            as_lc_bundle=bundle,
            doc_key=doc_key,
            draft_response=_draft(population.requests[0]),
            kg_config=config,
            kg_dirs=dirs,
            request_index=0,
            usage_tracker=llm.KGUsageTracker(),
        )
    factory.assert_not_called()


@pytest.mark.parametrize(
    argnames="error",
    argvalues=[
        TimeoutError("Synthetic timeout"),
        UnexpectedModelBehavior("Synthetic exhausted output"),
    ],
)
def test_execution_failures_propagate_without_semantic_substitution(
    error: Exception,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Transport or exhausted output failures cannot become negative or review judgments.

    Parameters
    ----------
    error
        Offline simulated processing failure.
    monkeypatch
        Restoring fake execution seam.
    tmp_path
        Isolated artifact directory.
    """
    bundle, config, dirs = (
        _fixtures._bundle(),
        _fixtures._config(),
        KGDirs(root=tmp_path),
    )
    population = write_lp_generation_request_artifacts(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=config,
        kg_dirs=dirs,
    )
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    fake = Mock()
    fake.run_sync.side_effect = error
    monkeypatch.setattr(
        name="create_lp_generation_validation_agent",
        target=llm,
        value=Mock(return_value=fake),
    )
    with pytest.raises(expected_exception=type(error)) as caught:
        llm.check_learning_progressions_for_request(
            as_lc_bundle=bundle,
            doc_key=_DOC_KEY,
            draft_response=_draft(population.requests[0]),
            kg_config=config,
            kg_dirs=dirs,
            request_index=0,
            usage_tracker=llm.KGUsageTracker(),
        )
    assert caught.value is error
    fake.run_sync.assert_called_once()
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize(argnames="index", argvalues=[-1, True, 1.0, "0", 999])
def test_execution_rejects_invalid_request_index_before_checker(
    index: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject invalid request selectors without creating an agent.

    Parameters
    ----------
    index
        Non-integer, negative, or out-of-range selector.
    monkeypatch
        Restoring checker factory guard.
    tmp_path
        Isolated materialized population.
    """
    bundle, config, dirs = (
        _fixtures._bundle(),
        _fixtures._config(),
        KGDirs(root=tmp_path),
    )
    population = write_lp_generation_request_artifacts(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=config,
        kg_dirs=dirs,
    )
    factory = Mock(side_effect=AssertionError("Invalid selector reached checker."))
    monkeypatch.setattr(
        name="create_lp_generation_validation_agent", target=llm, value=factory
    )
    with pytest.raises(expected_exception=ValueError, match="request_index"):
        llm.check_learning_progressions_for_request(
            as_lc_bundle=bundle,
            doc_key=_DOC_KEY,
            draft_response=_draft(population.requests[0]),
            kg_config=config,
            kg_dirs=dirs,
            request_index=index,
            usage_tracker=llm.KGUsageTracker(),
        )
    factory.assert_not_called()


@pytest.mark.parametrize(
    argnames="attack", argvalues=["none", "bad_draft", "bad_verdict"]
)
@pytest.mark.parametrize(argnames="corrects", argvalues=[False, True])
def test_execution_wires_independent_checker_and_accounts_only_checker_usage(
    attack: str,
    corrects: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Check draft gating, final defensive validation, evidence wiring, and usage buckets.

    Parameters
    ----------
    attack
        Valid run or request-relative violation at an untrusted boundary.
    corrects
        Whether the fake checker accepts or completely corrects.
    monkeypatch
        Restoring independent checker factory spy.
    tmp_path
        Isolated artifact directory.
    """
    bundle, config, dirs = (
        _fixtures._bundle(),
        _fixtures._config(batch=3),
        KGDirs(root=tmp_path),
    )
    config.learning_progressions.retry.checker_max_retries = 4
    config.learning_progressions.retry.producer_max_retries = 0
    population = write_lp_generation_request_artifacts(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=config,
        kg_dirs=dirs,
    )
    request = population.requests[-1]
    draft = _draft(request)
    correction = draft.model_copy(deep=True)
    correction.judgments[0].decision = "needs_review"
    verdict = _verdict(correction=correction if corrects else None, request=request)
    if attack == "bad_draft":
        draft.judgments.pop()
    elif attack == "bad_verdict":
        verdict.request_content_hash = "0" * 64
    fake = Mock()
    fake.run_sync.return_value = SimpleNamespace(
        output=verdict,
        usage=lambda: RunUsage(input_tokens=35, output_tokens=15, requests=5),
    )
    factory = Mock(return_value=fake)
    monkeypatch.setattr(
        name="create_lp_generation_validation_agent", target=llm, value=factory
    )
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    tracker = llm.KGUsageTracker()
    kwargs: dict[str, Any] = {
        "as_lc_bundle": bundle,
        "doc_key": _DOC_KEY,
        "draft_response": draft,
        "kg_config": config,
        "kg_dirs": dirs,
        "request_index": len(population.requests) - 1,
        "usage_tracker": tracker,
    }
    if attack == "none":
        assert llm.check_learning_progressions_for_request(**kwargs) is verdict
    else:
        with pytest.raises(expected_exception=QualityError):
            llm.check_learning_progressions_for_request(**kwargs)
    if attack == "bad_draft":
        factory.assert_not_called()
        totals = tracker.to_dict()["totals"]
        assert isinstance(totals, dict)
        assert totals["requests"] == 0
    else:
        call = factory.call_args.kwargs
        assert call["draft_response"] == draft
        assert call["lp_generation_request"] == request
        assert call["max_retries"] == 4
        assert call["verify_integrity_fn"] is verify_lp_generation_validation_integrity
        assert call["model_config"].model == Settings.LLM_KG_MODEL
        assert config.learning_progressions.checker_instructions in call["instructions"]
        sent = json.loads(
            fake.run_sync.call_args.args[0].split("## Draft and request JSON\n")[1]
        )
        assert sent == {
            "draft_response": draft.model_dump(mode="json"),
            "lp_generation_request": request.model_dump(mode="json"),
        }
        assert tracker.lp_generation_validation.requests == 5
        assert tracker.lp_generation_validation.input_tokens == 35
        assert tracker.lp_generation_validation.output_tokens == 15
        assert tracker.lp_generation.requests == 0
        totals = tracker.to_dict()["totals"]
        assert isinstance(totals, dict)
        assert totals["requests"] == 5
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "missing",
        "extra",
        "duplicate",
        "both_relations",
        "foreign_endpoint",
        "other_requested_endpoint",
        "identifier_instead_of_case",
        "reversed",
        "self",
        "request_id",
        "request_hash",
    ],
)
@pytest.mark.parametrize(argnames="surface", argvalues=["draft", "correction"])
def test_integrity_rejects_pair_coverage_identity_and_endpoint_attacks(  # pylint: disable=R1260
    attack: str,
    surface: str,
) -> None:
    """A complete valid correction never launders an invalid producer response.

    Parameters
    ----------
    attack
        Intrinsic or request-relative coverage and endpoint violation.
    surface
        Untrusted producer or checker response boundary.
    """
    request = _request()
    draft = _draft(request)
    payload = draft.model_dump(mode="json")
    first = payload["judgments"][0]
    if attack == "missing":
        payload["judgments"].pop()
    elif attack == "extra":
        extra = deepcopy(first)
        extra["pair_id"] = "unrequested-pair"
        payload["judgments"].append(extra)
    elif attack in {"duplicate", "both_relations"}:
        extra = deepcopy(first)
        if attack == "both_relations":
            first.update(decision="buildsTowards", direction="first_to_second")
            extra.update(decision="relatesTo", direction=None)
        payload["judgments"].append(extra)
    elif attack == "foreign_endpoint":
        first["second_sfi_uuid"] = str(UUID(int=999999))
    elif attack == "other_requested_endpoint":
        endpoints = {str(sfi.context.sfi_uuid) for sfi in request.sfis}
        replacement = endpoints - {first["first_sfi_uuid"], first["second_sfi_uuid"]}
        assert replacement
        first["first_sfi_uuid"], first["second_sfi_uuid"] = sorted(
            [
                first["first_sfi_uuid"],
                sorted(replacement)[0],
            ]
        )
    elif attack == "identifier_instead_of_case":
        first["second_sfi_uuid"] = str(
            UUID(int=UUID(first["second_sfi_uuid"]).int + 2**112)
        )
    elif attack == "reversed":
        first["first_sfi_uuid"], first["second_sfi_uuid"] = (
            first["second_sfi_uuid"],
            first["first_sfi_uuid"],
        )
        first["decision"] = "relatesTo"
    elif attack == "self":
        first["second_sfi_uuid"] = first["first_sfi_uuid"]
    elif attack == "request_id":
        payload["request_id"] = str(UUID(int=999))
    else:
        payload["request_content_hash"] = "0" * 64
    with pytest.raises(expected_exception=(ValidationError, QualityError)):
        damaged = LPGenerationResponse.model_validate(payload)
        verify_lp_generation_validation_integrity(
            draft_response=damaged if surface == "draft" else draft,
            lp_generation_request=request,
            validation_verdict=_verdict(
                correction=draft if surface == "draft" else damaged,
                request=request,
            ),
        )


@pytest.mark.parametrize(argnames="decision", argvalues=["buildsTowards", "relatesTo"])
@pytest.mark.parametrize(argnames="endpoint", argvalues=["first", "second"])
@pytest.mark.parametrize(argnames="origin", argvalues=["foreign", "other_requested"])
@pytest.mark.parametrize(argnames="surface", argvalues=["draft", "correction"])
def test_integrity_rejects_single_endpoint_substitution_after_schema_validation(
    decision: LPDecision,
    endpoint: str,
    origin: str,
    surface: str,
) -> None:
    """Require each individual endpoint to match its pair, beyond batch membership.

    Parameters
    ----------
    decision
        Proposed publishing relationship with otherwise valid direction.
    endpoint
        Sole endpoint altered while the other endpoint remains unchanged.
    origin
        Foreign SFI or a real SFI belonging to other pairs in the same request.
    surface
        Producer draft or complete checker correction under attack.
    """
    bundle = _candidates._bundle(
        items=tuple(_candidates._item(number=number) for number in (10, 20, 30, 40))
    )
    request = build_lp_generation_requests(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=_fixtures._config(batch=6),
    ).requests[0]
    assert len(request.pairs) == 6
    draft = _draft(request)
    index = next(
        index
        for index, pair in enumerate(request.pairs)
        if (pair.first_sfi_uuid, pair.second_sfi_uuid) == (UUID(int=20), UUID(int=30))
    )
    field = f"{endpoint}_sfi_uuid"
    replacement = UUID(
        int={
            ("first", "foreign"): 5,
            ("first", "other_requested"): 10,
            ("second", "foreign"): 50,
            ("second", "other_requested"): 40,
        }[(endpoint, origin)]
    )
    requested = {sfi.context.sfi_uuid for sfi in request.sfis}
    assert (replacement in requested) == (origin == "other_requested")
    payload = draft.model_dump(mode="json")
    payload["judgments"][index].update(
        decision=decision,
        direction="first_to_second" if decision == "buildsTowards" else None,
    )
    control = LPGenerationResponse.model_validate(payload)
    verify_lp_generation_validation_integrity(
        draft_response=control if surface == "draft" else draft,
        lp_generation_request=request,
        validation_verdict=_verdict(
            correction=control if surface == "correction" else None,
            request=request,
        ),
    )
    payload["judgments"][index][field] = str(replacement)
    damaged = LPGenerationResponse.model_validate(payload)
    before = control.judgments[index].model_dump(mode="json")
    after = damaged.judgments[index].model_dump(mode="json")
    assert {key for key in before if before[key] != after[key]} == {field}
    verdict = _verdict(
        correction=damaged if surface == "correction" else None,
        request=request,
    )
    with pytest.raises(expected_exception=QualityError, match="endpoints do not match"):
        verify_lp_generation_validation_integrity(
            draft_response=damaged if surface == "draft" else draft,
            lp_generation_request=request,
            validation_verdict=verdict,
        )


@pytest.mark.parametrize(
    argnames="attack", argvalues=["request", "response", "verdict"]
)
def test_integrity_revalidates_mutated_model_instances(attack: str) -> None:
    """Parsed Python objects cannot bypass subsequent intrinsic or material checks.

    Parameters
    ----------
    attack
        Already-parsed object corrupted after initial validation.
    """
    request = _request()
    draft = _draft(request)
    verdict = _verdict(request=request)
    if attack == "request":
        request.sfis[0].context.description.text += " Tampered bounded content."
    elif attack == "response":
        draft.judgments[0].confidence = 2.0
    else:
        verdict.rationale = " "
    with pytest.raises(expected_exception=QualityError):
        verify_lp_generation_validation_integrity(
            draft_response=draft,
            lp_generation_request=request,
            validation_verdict=verdict,
        )


@pytest.mark.parametrize(argnames="surface", argvalues=["draft", "correction"])
def test_nomination_truncation_warnings_belong_to_each_affected_pair(
    surface: str,
) -> None:
    """A bounded nomination requires warning retention beyond upstream audit flags.

    Parameters
    ----------
    surface
        Producer or corrected response boundary.
    """
    request = build_lp_generation_requests(
        as_lc_bundle=_fixtures._bundle(),
        doc_key=_DOC_KEY,
        kg_config=_fixtures._config(
            batch=3, limits={"max_source_evidence_characters_per_sfi": 200}
        ),
    ).requests[0]
    draft = _draft(request)
    expected_warnings = {
        f"Pair {pair.pair_id} nomination evidence is truncated; "
        "complete evidence remains in the candidate artifact."
        for pair in request.pairs
    }
    assert set(request.warnings) == expected_warnings
    assert len(expected_warnings) > 1
    assert all(not pair.warnings for pair in request.pairs)
    assert all(not sfi.warnings and not sfi.context.warnings for sfi in request.sfis)
    for judgment in draft.judgments:
        judgment.warnings = [
            f"Pair {judgment.pair_id} nomination evidence is truncated; "
            "complete evidence remains in the candidate artifact."
        ]
    verify_lp_generation_validation_integrity(
        draft_response=draft,
        lp_generation_request=request,
        validation_verdict=_verdict(
            correction=draft.model_copy(deep=True) if surface == "correction" else None,
            request=request,
        ),
    )
    found = 0
    for index, pair in enumerate(request.pairs):
        if not (
            pair.nomination.evidence.truncated or pair.nomination.references_truncated
        ):
            continue
        warning = (
            f"Pair {pair.pair_id} nomination evidence is truncated; "
            "complete evidence remains in the candidate artifact."
        )
        assert warning in request.warnings
        damaged = draft.model_copy(deep=True)
        damaged.judgments[index].warnings.remove(warning)
        with pytest.raises(
            expected_exception=QualityError, match="omitted required warnings"
        ):
            verify_lp_generation_validation_integrity(
                draft_response=damaged if surface == "draft" else draft,
                lp_generation_request=request,
                validation_verdict=_verdict(
                    correction=damaged if surface == "correction" else None,
                    request=request,
                ),
            )
        found += 1
    assert found == len(request.pairs)


@pytest.mark.parametrize(
    argnames="mode",
    argvalues=[
        "same",
        "forward",
        "reverse",
        "one_missing",
        "both_missing",
        "only_builds",
        "only_relates",
    ],
)
def test_permissions_intersect_rank_and_relation_policy_at_both_response_boundaries(  # pylint: disable=R0912, R1260
    mode: str,
) -> None:
    """Validate independently enumerated outcomes against actual request permissions.

    Parameters
    ----------
    mode
        Coordinate or relation-specific policy scenario.
    """
    bundle, config = _fixtures._bundle(2), _fixtures._config()
    values: list[str | None] = ["PRIMARY ONE", "PRIMARY ONE"]
    if mode in {"only_builds", "only_relates"}:
        config = _fixtures._config(profile="ghana_math")
        values = ["BASIC 4", "BASIC 4"]
        for item in bundle.items:
            item.statement_type = "Indicator"
    if mode == "forward":
        values[1] = "PRIMARY THREE"
    elif mode == "reverse":
        values[0] = "PRIMARY THREE"
    elif mode == "one_missing":
        values[1] = None
    elif mode == "both_missing":
        values = [None, None]
    for item, value in zip(bundle.items, values, strict=True):
        item.metadata["identity_scope_values"] = (
            {} if value is None else {"Grade": value}
        )
    if mode == "only_builds":
        config.learning_progressions.relates_to.allowed_statement_type_pairs = [
            pair
            for pair in config.learning_progressions.relates_to.allowed_statement_type_pairs
            if pair.first_statement_type == "Content Standard"
        ]
    elif mode == "only_relates":
        config.learning_progressions.builds_towards.allowed_statement_type_pairs = [
            pair
            for pair in config.learning_progressions.builds_towards.allowed_statement_type_pairs
            if pair.source_statement_type == "Content Standard"
        ]
    request = build_lp_generation_requests(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=config,
    ).requests[0]
    expected: set[tuple[str, str | None]] = {
        ("no_relation", None),
        ("needs_review", None),
    }
    if mode != "only_builds":
        expected.add(("relatesTo", None))
    if mode not in {"one_missing", "both_missing", "only_relates"}:
        if mode != "reverse":
            expected.add(("buildsTowards", "first_to_second"))
        if mode != "forward":
            expected.add(("buildsTowards", "second_to_first"))
    assert {
        (a.decision, a.direction) for a in request.pairs[0].admissible_decisions
    } == expected
    draft = _draft(request)
    outcomes: list[tuple[LPDecision, LPDirection | None]] = [
        ("buildsTowards", "first_to_second"),
        ("buildsTowards", "second_to_first"),
        ("relatesTo", None),
        ("no_relation", None),
        ("needs_review", None),
    ]
    for decision, direction in outcomes:
        proposal = draft.model_copy(deep=True)
        proposal.judgments[0].decision = decision
        proposal.judgments[0].direction = direction
        for corrected in (False, True):
            kwargs: dict[str, Any] = {
                "draft_response": draft if corrected else proposal,
                "lp_generation_request": request,
                "validation_verdict": _verdict(
                    correction=proposal if corrected else None, request=request
                ),
            }
            if (decision, direction) in expected:
                verify_lp_generation_validation_integrity(**kwargs)
            else:
                with pytest.raises(
                    expected_exception=QualityError, match="not permitted"
                ):
                    verify_lp_generation_validation_integrity(**kwargs)


@pytest.mark.parametrize(argnames="profile", argvalues=_PROFILES)
def test_prompt_preserves_exact_six_curriculum_evidence_and_independent_rubric(
    profile: str,
) -> None:
    """Compare serialized checker context with the original request, including every DAG branch.

    Parameters
    ----------
    profile
        Reduced curriculum with tree, DAG, unresolved, or scope-only context.
    """
    bundle = _fixtures._expanded_fixture(profile)
    config = _fixtures._config(
        batch=3, limits={"max_ancestor_path_depth": 1}, profile=profile
    )
    population = build_lp_generation_requests(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=config,
    )
    assert population.requests
    seen_dag = seen_unresolved = seen_truncated = False
    for request in population.requests:
        draft = _draft(request)
        rendered = prompts.validate_lp_generation_response(
            checker_instructions=config.learning_progressions.checker_instructions,
            draft_response=draft,
            lp_generation_request=request,
            producer_instructions=config.learning_progressions.producer_instructions,
        )
        assert isinstance(rendered.system_message, str)
        assert isinstance(rendered.user_message, str)
        payload = json.loads(
            rendered.user_message.split("## Draft and request JSON\n")[1]
        )
        assert set(payload) == {"draft_response", "lp_generation_request"}
        assert payload["lp_generation_request"] == request.model_dump(mode="json")
        assert payload["draft_response"] == draft.model_dump(mode="json")
        assert (
            config.learning_progressions.producer_instructions
            in rendered.system_message
        )
        assert (
            config.learning_progressions.checker_instructions in rendered.system_message
        )
        for text in (
            "independent",
            "mandatory prerequisite",
            "without dependency",
            "takes precedence",
            "Generic repetition",
            "needs_review",
            "processing failure",
            "untrusted data",
            "Framework-root fallback",
            "DAG",
            "Never reconstruct omitted content",
            "including unchanged judgments",
            "not prove pedagogical correctness",
        ):
            assert text in rendered.system_message
        for sfi in request.sfis:
            direct = {
                UUID(edge.source_entity_value)
                for edge in bundle.relationships_has_child
                if edge.source_entity == "StandardsFrameworkItem"
                and UUID(edge.target_entity_value) == sfi.context.sfi_uuid
            }
            assert set(sfi.parent_sfi_uuids) == direct
            seen_dag |= len(direct) > 1
            seen_unresolved |= sfi.context.unresolved_ancestry
            seen_truncated |= any(path.depth_truncated for path in sfi.ancestor_paths)
            if sfi.context.unresolved_ancestry:
                assert bundle.framework.case_identifier_uuid not in sfi.parent_sfi_uuids
        verify_lp_generation_validation_integrity(
            draft_response=draft,
            lp_generation_request=request,
            validation_verdict=_verdict(request=request),
        )
    if profile == "pratham_science":
        assert seen_dag and seen_truncated
    if profile == "ghana_math":
        assert seen_unresolved


@pytest.mark.parametrize(argnames="profile", argvalues=_PROFILES)
def test_response_permissions_follow_every_configured_grain_without_cross_type_judgments(
    profile: str,
) -> None:
    """Exercise response containment over every participating local Standard grain.

    Parameters
    ----------
    profile
        One of the six independently configured curriculum matrices.
    """
    bundle = _fixtures._expanded_fixture(profile)
    config = _fixtures._config(batch=3, profile=profile)
    population = build_lp_generation_requests(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=config,
    )
    types = {item.case_identifier_uuid: item.statement_type for item in bundle.items}
    expected_types = {
        "ghana_english": {"Content Standard", "Indicator"},
        "ghana_math": {"Content Standard", "Indicator"},
        "madhi_math": {"Content"},
        "nigeria_math": {"Performance Objective"},
        "pratham_science": {
            "NCERT Learning Outcome",
            "Content Domain Specific Learning Outcome",
            "Indicator",
        },
        "rwanda_math": {
            "Grade Key Competence",
            "Key Unit Competence",
            "Knowledge Objective",
            "Skills Objective",
            "Attitudes and Values Objective",
        },
    }[profile]
    seen = set()
    for request in population.requests:
        draft = _draft(request)
        for index, pair in enumerate(request.pairs):
            kind = types[pair.first_sfi_uuid]
            assert kind == types[pair.second_sfi_uuid]
            assert kind in expected_types
            seen.add(kind)
            draft.judgments[index].decision = "relatesTo"
        verify_lp_generation_response_integrity(
            lp_generation_request=request,
            lp_generation_response=draft,
        )
        verify_lp_generation_validation_integrity(
            draft_response=draft,
            lp_generation_request=request,
            validation_verdict=_verdict(request=request),
        )
    assert seen == expected_types


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "passed_string",
        "passed_integer",
        "empty_rationale",
        "extra_field",
        "issue_empty",
        "issue_severity",
        "issue_blank_pair",
        "confidence_low",
        "confidence_high",
        "confidence_boolean",
        "confidence_nan",
        "unsupported_category",
        "empty_pair_rationale",
        "direction_on_negative",
        "direction_on_relates",
        "missing_direction",
        "duplicate_warning",
    ],
)
def test_schemas_reject_malformed_checker_and_corrected_payloads(  # pylint: disable=R0912, R1260
    attack: str,
) -> None:
    """Strict schemas exclude ambiguous booleans, empty reasons, and invalid relation shapes.

    Parameters
    ----------
    attack
        Intrinsic malformed model-output scenario.
    """
    request = _request()
    payload = _verdict(correction=_draft(request), request=request).model_dump(
        mode="json"
    )
    judgment = payload["corrected_response"]["judgments"][0]
    if attack == "passed_string":
        payload["passed"] = "false"
    elif attack == "passed_integer":
        payload["passed"] = 0
    elif attack == "empty_rationale":
        payload["rationale"] = " \n "
    elif attack == "extra_field":
        payload["force_include"] = True
    elif attack == "issue_empty":
        payload["issues"][0]["message"] = " "
    elif attack == "issue_severity":
        payload["issues"][0]["severity"] = "info"
    elif attack == "issue_blank_pair":
        payload["issues"][0]["pair_id"] = " "
    elif attack.startswith("confidence_"):
        judgment["confidence"] = {
            "confidence_low": -0.1,
            "confidence_high": 1.1,
            "confidence_boolean": True,
            "confidence_nan": float("nan"),
        }[attack]
    elif attack == "unsupported_category":
        judgment["decision"] = "recurring_practice"
    elif attack == "empty_pair_rationale":
        judgment["rationale"] = " "
    elif attack == "direction_on_negative":
        judgment["direction"] = "first_to_second"
    elif attack == "direction_on_relates":
        judgment.update(decision="relatesTo", direction="second_to_first")
    elif attack == "missing_direction":
        judgment["decision"] = "buildsTowards"
    else:
        judgment["warnings"] = ["Synthetic warning", "Synthetic warning"]
    with pytest.raises(expected_exception=ValidationError):
        LPGenerationValidationVerdict.model_validate(payload)


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "accept_error",
        "accept_correction",
        "reject_no_correction",
        "reject_no_error",
        "foreign_issue",
        "stale_id",
        "stale_hash",
        "stale_correction_id",
        "stale_correction_hash",
    ],
)
def test_verdict_integrity_rejects_contradictions_and_invalid_issue_references(
    attack: str,
) -> None:
    """Issue text cannot legitimize a partial, stale, or contradictory checker result.

    Parameters
    ----------
    attack
        Request-relative verdict inconsistency.
    """
    request = _request()
    draft = _draft(request)
    verdict = _verdict(request=request)
    if attack == "accept_error":
        verdict.issues = [LPGenerationValidationIssue.model_validate(_issue())]
    elif attack == "accept_correction":
        verdict.corrected_response = draft
    elif attack == "reject_no_correction":
        verdict.passed = False
        verdict.issues = [LPGenerationValidationIssue.model_validate(_issue())]
    elif attack == "reject_no_error":
        verdict.passed = False
        verdict.corrected_response = draft
    elif attack == "foreign_issue":
        verdict.issues = [
            LPGenerationValidationIssue.model_validate(
                _issue(pair_id="not-requested", severity="warning")
            )
        ]
    elif attack == "stale_id":
        verdict.request_id = UUID(int=999)
    elif attack == "stale_hash":
        verdict.request_content_hash = "0" * 64
    else:
        verdict = _verdict(correction=draft.model_copy(deep=True), request=request)
        assert verdict.corrected_response is not None
        if attack == "stale_correction_id":
            verdict.corrected_response.request_id = UUID(int=999)
        else:
            verdict.corrected_response.request_content_hash = "0" * 64
    with pytest.raises(expected_exception=QualityError):
        verify_lp_generation_validation_integrity(
            draft_response=draft,
            lp_generation_request=request,
            validation_verdict=verdict,
        )


@pytest.mark.parametrize(argnames="pair_specific", argvalues=[False, True])
def test_verdict_warning_issues_allow_acceptance_without_reclassifying_judgments(
    pair_specific: bool,
) -> None:
    """Advisory issues neither force correction nor replace ambiguity or negative outcomes.

    Parameters
    ----------
    pair_specific
        Pair-scoped versus request-wide issue reference.
    """
    request = _request()
    draft = _draft(request)
    draft.judgments[0].decision = "needs_review"
    payload = _verdict(request=request).model_dump(mode="json")
    payload["issues"] = [
        _issue(
            pair_id=request.pairs[0].pair_id if pair_specific else None,
            severity="warning",
        )
    ]
    verdict = LPGenerationValidationVerdict.model_validate(payload)
    verify_lp_generation_validation_integrity(
        draft_response=draft,
        lp_generation_request=request,
        validation_verdict=verdict,
    )
    assert draft.judgments[0].decision == "needs_review"
    assert draft.judgments[1].decision == "no_relation"


@pytest.mark.parametrize(
    argnames="profile", argvalues=["ghana_math", "pratham_science"]
)
@pytest.mark.parametrize(argnames="surface", argvalues=["draft", "correction"])
def test_warning_loss_is_rejected_even_when_checker_issues_repeat_the_warning(
    profile: str,
    surface: str,
) -> None:
    """Remove actual endpoint and truncation warnings one at a time from valid responses.

    Parameters
    ----------
    profile
        Real reduced unresolved or DAG context with forced bounded truncation.
    surface
        Draft or corrected judgment under attack.
    """
    population = build_lp_generation_requests(
        as_lc_bundle=_fixtures._expanded_fixture(profile),
        doc_key=_DOC_KEY,
        kg_config=_fixtures._config(
            batch=3, limits={"max_ancestor_path_depth": 1}, profile=profile
        ),
    )
    checked = 0
    for request in population.requests:
        draft = _draft(request)
        contexts = {sfi.context.sfi_uuid: sfi for sfi in request.sfis}
        for index, pair in enumerate(request.pairs):
            required = set(pair.warnings)
            for endpoint in (pair.first_sfi_uuid, pair.second_sfi_uuid):
                sfi = contexts[endpoint]
                required.update(sfi.warnings)
                required.update(sfi.context.warnings)
                for ancestor in sfi.ancestors:
                    required.update(ancestor.warnings)
            for warning in required:
                damaged = draft.model_copy(deep=True)
                damaged.judgments[index].warnings.remove(warning)
                verdict = _verdict(
                    correction=damaged if surface == "correction" else None,
                    request=request,
                )
                payload = verdict.model_dump(mode="json")
                payload["issues"].append(
                    {
                        **_issue(pair_id=pair.pair_id, severity="warning"),
                        "message": warning,
                    }
                )
                verdict = LPGenerationValidationVerdict.model_validate(payload)
                with pytest.raises(
                    expected_exception=QualityError, match="omitted required warnings"
                ):
                    verify_lp_generation_validation_integrity(
                        draft_response=damaged if surface == "draft" else draft,
                        lp_generation_request=request,
                        validation_verdict=verdict,
                    )
                checked += 1
    assert checked > 0
