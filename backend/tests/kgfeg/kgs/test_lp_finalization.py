"""Red-team final claims through real persisted offline adjudication artifacts."""

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import json
import socket
import sys

from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import UUID

# Third Party Library
import pytest

# Package Library
from kgfeg.config import Settings
from kgfeg.kgs import lp_generation
from kgfeg.kgs.llm import KGUsageTracker
from kgfeg.kgs.lp_finalization import (
    LPFinalClaims,
    LPFinalizationCycleError,
    finalize_learning_progressions,
    validate_lp_final_claims_artifact,
)
from kgfeg.kgs.lp_generation import LPGenerationFailed
from kgfeg.kgs.schemas import LPGenerationResponse
from kgfeg.kgs.utils import KGDirs
from kgfeg.page_ir_extraction.validators import QualityError
from tests.kgfeg.kgs import test_lp_checker as _checker
from tests.kgfeg.kgs import test_lp_generation as _fixtures
from tests.kgfeg.kgs import test_lp_orchestration as _storage

_FINAL = "lp_final_claims.json"
_STAGES = (
    "lp_generation_draft_responses.jsonl",
    "lp_generation_validation_verdicts.jsonl",
    "lp_generation_responses.jsonl",
)
_RECEIPT = "lp_generation_checkpoint_manifest.json"
_FAILURE = "lp_generation_failures.json"
_PROFILES = (
    "ghana_english",
    "ghana_math",
    "madhi_math",
    "nigeria_math",
    "pratham_science",
    "rwanda_math",
)
_REJECTIONS = (ValueError, QualityError, LPGenerationFailed, OSError)


class _Harness:
    """Supply independent semantic proposals while retaining real material checks."""

    def __init__(self, *, batch: int = 3, count: int = 4, root: Path) -> None:
        """Create isolated upstream inputs and a script keyed by exact endpoint pair.

        Parameters
        ----------
        batch
            Bounded request size.
        count
            Number of synthetic same-rank standards.
        root
            Temporary evidence directory.
        """
        self.bundle = _fixtures._bundle(count)
        self.calls: list[tuple[str, int]] = []
        self.config = _fixtures._config(batch=batch)
        self.config.learning_progressions.retry.producer_max_retries = 0
        self.config.learning_progressions.retry.checker_max_retries = 0
        self.corrected_requests: set[int] | None = None
        self.corrections: dict[tuple[int, int], tuple[str, str | None]] | None = None
        self.decisions: dict[tuple[int, int], tuple[str, str | None]] = {}
        self.default_decision = "no_relation"
        self.failure: tuple[str, int] | None = None
        self.root = root

    def _call(self, **kwargs: Any) -> Any:
        """Return a complete scripted proposal at the external model-call seam.

        Parameters
        ----------
        kwargs
            Actual bounded request and optional producer draft.

        Returns
        -------
        Any
            Producer response or complete independent checker verdict.
        """
        request = kwargs["request"]
        stage = "draft" if kwargs["draft"] is None else "verdict"
        key = (stage, request.request_index)
        self.calls.append(key)
        if self.failure == key:
            raise TimeoutError("Synthetic offline transport failure.")
        response = _checker._draft(request)
        corrects = (
            stage == "verdict"
            and self.corrections is not None
            and (
                self.corrected_requests is None
                or request.request_index in self.corrected_requests
            )
        )
        choices = self.corrections if corrects else self.decisions
        assert choices is not None
        for judgment in response.judgments:
            pair = (judgment.first_sfi_uuid.int, judgment.second_sfi_uuid.int)
            decision, direction = choices.get(pair, (self.default_decision, None))
            judgment.decision = decision
            judgment.direction = direction
            judgment.confidence = 0.0 if decision == "buildsTowards" else 1.0
            judgment.rationale = f"Synthetic {decision} process fixture for {pair}."
        if stage == "draft":
            return response
        return _checker._verdict(
            correction=response if corrects else None,
            request=request,
        )

    def _finalize(self, *, validate: bool = False) -> LPFinalClaims:
        """Exercise the public writer or read-only reconstructed artifact boundary.

        Parameters
        ----------
        validate
            Validate existing final claims instead of writing them.

        Returns
        -------
        LPFinalClaims
            Reconciled complete artifact.
        """
        operation = (
            validate_lp_final_claims_artifact
            if validate
            else finalize_learning_progressions
        )
        return operation(
            as_lc_bundle=self.bundle,
            doc_key="synthetic-selection-document",
            kg_config=self.config,
            kg_dirs=KGDirs(root=self.root),
        )

    def _run(self) -> tuple[LPGenerationResponse, ...]:
        """Persist proposals using the public real generation orchestrator.

        Returns
        -------
        tuple[LPGenerationResponse, ...]
            Complete producer/checker-selected judgments.
        """
        return lp_generation.generate_learning_progressions(
            as_lc_bundle=self.bundle,
            doc_key="synthetic-selection-document",
            kg_config=self.config,
            kg_dirs=KGDirs(root=self.root),
            overwrite=False,
            usage_tracker=KGUsageTracker(),
        )


def _assert_provenance(*, artifact: LPFinalClaims, root: Path) -> None:
    """Bind each claim to its actual request and independently hashed checkpoints.

    Parameters
    ----------
    artifact
        Final claims returned by the public boundary.
    root
        Persisted request and adjudication evidence used to derive the oracle.
    """
    requests = _storage._rows(root / "lp_generation_requests.jsonl")
    owners = {
        pair["pair_id"]: request for request in requests for pair in request["pairs"]
    }
    assert len(owners) == sum(len(request["pairs"]) for request in requests)
    assert set(owners) == {claim.judgment.pair_id for claim in artifact.claims}
    stages = [
        {row["payload"]["request_id"]: row for row in _storage._rows(root / name)}
        for name in _STAGES
    ]
    receipt = json.loads((root / _RECEIPT).read_bytes())
    for claim in artifact.claims:
        request = owners[claim.judgment.pair_id]
        request_id = request["request_id"]
        draft, verdict, response = [stage[request_id] for stage in stages]
        producer = next(
            judgment
            for judgment in draft["payload"]["judgments"]
            if judgment["pair_id"] == claim.judgment.pair_id
        )
        selected = next(
            judgment
            for judgment in response["payload"]["judgments"]
            if judgment["pair_id"] == claim.judgment.pair_id
        )
        assert claim.judgment.model_dump(mode="json") == selected
        assert claim.provenance.model_dump(mode="json") == {
            "checker_checkpoint_content_hash": _hash(verdict),
            "checker_outcome": (
                "accepted" if verdict["payload"]["passed"] else "corrected"
            ),
            "checker_prompt_content_hash": verdict["prompt_content_hash"],
            "checker_verdict_content_hash": _hash(verdict["payload"]),
            "execution_content_hash": _hash(receipt["material"]),
            "judgment_content_hash": _hash(selected),
            "producer_checkpoint_content_hash": _hash(draft),
            "producer_judgment": producer,
            "producer_prompt_content_hash": draft["prompt_content_hash"],
            "producer_response_content_hash": _hash(draft["payload"]),
            "request_content_hash": request["request_content_hash"],
            "request_id": request_id,
            "response_checkpoint_content_hash": _hash(response),
            "response_content_hash": _hash(response["payload"]),
        }


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every external connection fail and use a fixed non-secret model identity.

    Parameters
    ----------
    monkeypatch
        Restoring model and socket guards.
    """
    guard = Mock(side_effect=AssertionError("Finalization tests must remain offline."))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)
    monkeypatch.setattr(name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5.2")


def _hash(value: Any) -> str:
    """Compute canonical content hashes independently of production helpers.

    Parameters
    ----------
    value
        JSON-compatible material.

    Returns
    -------
    str
        SHA-256 of compact sorted UTF-8 checkpoint JSON including its newline.
    """
    return hashlib.sha256(_storage._bytes(value)).hexdigest()


def _install(*, harness: _Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace model transport only, leaving reconstruction and persistence intact.

    Parameters
    ----------
    harness
        Independent endpoint-keyed response script.
    monkeypatch
        Restoring external call substitution.
    """
    monkeypatch.setattr(
        name="generate_learning_progressions_for_request",
        target=lp_generation,
        value=harness._call,
    )


def _mutate_correction(*, attack: str, judgments: list[dict[str, Any]]) -> None:
    """Inject malformed untrusted corrections without production pre-validation.

    Parameters
    ----------
    attack
        Invalid pair interpretation or population edit.
    judgments
        Complete checker correction to alter on disk.
    """
    judgment = next((j for j in judgments if j["warnings"]), judgments[0])
    if attack == "opposite_direction":
        judgment = next(
            j
            for j in judgments
            if UUID(j["first_sfi_uuid"]).int == 1
            and UUID(j["second_sfi_uuid"]).int == 2
        )
        judgment.update(decision="buildsTowards", direction="second_to_first")
    elif attack in {"both_relations", "duplicate"}:
        extra = deepcopy(judgment)
        if attack == "both_relations":
            judgment.update(decision="buildsTowards", direction="first_to_second")
            extra.update(decision="relatesTo", direction=None)
        judgments.append(extra)
    elif attack == "extra":
        extra = deepcopy(judgment)
        extra["pair_id"] = "invented-pair"
        judgments.append(extra)
    elif attack == "missing":
        judgments.pop()
    elif attack == "reverse_canonical":
        judgment["first_sfi_uuid"], judgment["second_sfi_uuid"] = (
            judgment["second_sfi_uuid"],
            judgment["first_sfi_uuid"],
        )
    elif attack == "unauthorized_relation":
        judgment.update(decision="relatesTo", direction=None)
    else:
        field, value = {
            "endpoint": ("second_sfi_uuid", str(UUID(int=9999))),
            "recurring_relation": ("decision", "recurring_practice"),
            "self_pair": ("second_sfi_uuid", judgment["first_sfi_uuid"]),
            "warning": ("warnings", []),
        }[attack]
        assert field != "warnings" or judgment["warnings"]
        judgment[field] = value


def _published(artifact: LPFinalClaims) -> set[tuple[int, int, str]]:
    """Read direct publishing endpoints without deriving expected reachability.

    Parameters
    ----------
    artifact
        Reconstructed complete final claims.

    Returns
    -------
    set[tuple[int, int, str]]
        Direct endpoint integers and public decision for publishing claims.
    """
    return {
        (claim.source_sfi_uuid.int, claim.target_sfi_uuid.int, claim.judgment.decision)
        for claim in artifact.claims
        if claim.source_sfi_uuid is not None and claim.target_sfi_uuid is not None
    }


def _rewrite_stage(*, name: str, root: Path, rows: list[dict[str, Any]]) -> None:
    """Recompute attacker-controlled payload hashes and outer receipts.

    Parameters
    ----------
    name
        Stage filename under attack.
    root
        Isolated evidence directory.
    rows
        Malformed population that the public boundary must reject.
    """
    for row in rows:
        row["payload_content_hash"] = _hash(row["payload"])
    (root / name).write_bytes(b"".join(_storage._bytes(row) for row in rows))
    _storage._reseal(name=name, root=root)


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "checker_outcome",
        "confidence",
        "decision",
        "drop",
        "duplicate",
        "endpoint",
        "extra_field",
        "graph_status",
        "hash",
        "manual_override",
        "nomination",
        "order",
        "provenance",
        "rationale",
        "summary",
        "warning",
    ],
)
def test_artifact_edits_rejected_even_after_own_hash_is_recomputed(
    attack: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A self-consistent file cannot supersede the persisted checker decision.

    Parameters
    ----------
    attack
        Altered semantic, evidence, diagnostic, or structural field.
    monkeypatch
        Offline model seam.
    tmp_path
        Isolated persisted artifacts.
    """
    harness = _Harness(root=tmp_path)
    _install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    original = harness._finalize()
    payload = original.model_dump(mode="json")
    claim = payload["claims"][0]
    judgment = claim["judgment"]
    if attack in {"confidence", "rationale", "warning"}:
        field, value = {
            "confidence": ("confidence", 0.25),
            "rationale": ("rationale", "Hand-edited interpretation."),
            "warning": ("warnings", ["Hand-edited warning."]),
        }[attack]
        judgment[field] = value
        claim["provenance"]["judgment_content_hash"] = _hash(judgment)
    elif attack == "decision":
        judgment["decision"] = "needs_review"
        claim["provenance"]["judgment_content_hash"] = _hash(judgment)
        payload["decision_counts"]["needs_review"] += 1
        payload["decision_counts"]["no_relation"] -= 1
    elif attack == "drop":
        payload["claims"].pop()
    elif attack == "duplicate":
        payload["claims"][1] = deepcopy(claim)
    elif attack == "order":
        payload["claims"].reverse()
    elif attack != "hash":
        container, field, value = {
            "endpoint": (claim, "source_sfi_uuid", str(UUID(int=9999))),
            "extra_field": (payload, "pedagogically_correct", True),
            "graph_status": (payload, "graph_status", "cyclic"),
            "manual_override": (
                payload,
                "semantic_overrides",
                [{"pair_id": judgment["pair_id"], "include": True}],
            ),
            "nomination": (
                claim["candidate"],
                "warnings",
                ["Manufactured nomination evidence."],
            ),
            "provenance": (
                claim["provenance"],
                "producer_checkpoint_content_hash",
                "0" * 64,
            ),
            "checker_outcome": (claim["provenance"], "checker_outcome", "corrected"),
            "summary": (payload, "total_claims", payload["total_claims"] + 1),
        }[attack]
        container[field] = value
    payload["content_hash"] = _hash(
        {k: v for k, v in payload.items() if k != "content_hash"}
    )
    if attack in {
        "checker_outcome",
        "confidence",
        "decision",
        "provenance",
        "rationale",
        "warning",
    }:
        LPFinalClaims.model_validate(payload)
    if attack == "hash":
        payload["content_hash"] = "0" * 64
    (tmp_path / _FINAL).write_bytes(_storage._bytes(payload))
    before = _storage._snapshot(tmp_path)
    calls = list(harness.calls)
    with pytest.raises(ValueError, match="differ from current validated adjudication"):
        harness._finalize(validate=True)
    assert _storage._snapshot(tmp_path) == before
    assert harness.calls == calls
    assert harness._finalize() == original


@pytest.mark.parametrize(
    argnames="first",
    argvalues=["buildsTowards", "relatesTo", "no_relation", "needs_review"],
)
@pytest.mark.parametrize(
    argnames="second",
    argvalues=["buildsTowards", "relatesTo", "no_relation", "needs_review"],
)
def test_complete_checker_corrections_replace_all_draft_interpretations(
    first: str,
    monkeypatch: pytest.MonkeyPatch,
    second: str,
    tmp_path: Path,
) -> None:
    """Only the complete checker-selected interpretation publishes for each pair.

    Parameters
    ----------
    first
        Producer's original process-fixture decision.
    monkeypatch
        Offline response seam.
    second
        Checker's replacement process-fixture decision.
    tmp_path
        Isolated artifacts.
    """
    harness = _Harness(count=3, root=tmp_path)
    harness.decisions = {
        (1, 2): (first, "first_to_second" if first == "buildsTowards" else None)
    }
    harness.corrections = {
        (1, 2): (second, "second_to_first" if second == "buildsTowards" else None),
        (1, 3): ("needs_review", None),
        (2, 3): ("relatesTo", None),
    }
    _install(harness=harness, monkeypatch=monkeypatch)
    responses = harness._run()
    artifact = harness._finalize()
    _assert_provenance(artifact=artifact, root=tmp_path)
    assert [claim.judgment for claim in artifact.claims] == [
        j for r in responses for j in r.judgments
    ]
    assert artifact.total_claims == 3
    expected = {(2, 3, "relatesTo")}
    if second == "buildsTowards":
        expected.add((2, 1, second))
    elif second == "relatesTo":
        expected.add((1, 2, second))
    assert _published(artifact) == expected
    for claim in artifact.claims:
        assert claim.provenance.checker_outcome == "corrected"
        assert claim.provenance.judgment_content_hash == _hash(
            claim.judgment.model_dump(mode="json")
        )
    changed = next(
        c
        for c in artifact.claims
        if c.judgment.first_sfi_uuid.int == 1 and c.judgment.second_sfi_uuid.int == 2
    )
    assert changed.provenance.producer_judgment.decision == first
    assert harness._finalize(validate=True) == artifact


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "duplicate",
        "extra",
        "missing",
        "order",
        "partial",
        "payload_hash",
        "prerequisite",
        "prompt",
        "request",
        "stale_execution",
        "truncated",
    ],
)
@pytest.mark.parametrize(argnames="name", argvalues=_STAGES)
@pytest.mark.parametrize(argnames="validate", argvalues=[False, True])
def test_corrupt_checkpoint_populations_fail_at_public_artifact_boundary(
    attack: str,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    tmp_path: Path,
    validate: bool,
) -> None:
    """Complete current evidence remains mandatory even with forged outer receipts.

    Parameters
    ----------
    attack
        Prefix, alignment, or content-integrity defect.
    monkeypatch
        Offline response seam.
    name
        Successful checkpoint stage under attack.
    tmp_path
        Isolated persisted artifacts.
    validate
        Exercise read-only validation as well as finalization.
    """
    harness = _Harness(batch=1, count=3, root=tmp_path)
    _install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    harness._finalize()
    rows = _storage._rows(tmp_path / name)
    if attack == "duplicate":
        rows.insert(1, deepcopy(rows[0]))
    elif attack == "extra":
        rows.append(deepcopy(rows[-1]))
    elif attack == "missing":
        rows.pop(1)
    elif attack == "order":
        rows.reverse()
    elif attack == "partial":
        rows.pop()
    elif attack in {"prerequisite", "prompt", "stale_execution"}:
        field = {
            "prerequisite": "prerequisite_content_hash",
            "prompt": "prompt_content_hash",
            "stale_execution": "execution_content_hash",
        }[attack]
        rows[0][field] = "0" * 64
    elif attack == "request":
        rows[0]["payload"]["request_id"] = str(UUID(int=7777))
    _rewrite_stage(name=name, root=tmp_path, rows=rows)
    if attack in {"truncated", "payload_hash"}:
        if attack == "truncated":
            (tmp_path / name).write_bytes((tmp_path / name).read_bytes()[:-1])
        else:
            rows[0]["payload_content_hash"] = "0" * 64
            (tmp_path / name).write_bytes(
                b"".join(_storage._bytes(row) for row in rows)
            )
        _storage._reseal(name=name, root=tmp_path)
    before = _storage._snapshot(tmp_path)
    calls = list(harness.calls)
    with pytest.raises(_REJECTIONS):
        harness._finalize(validate=validate)
    assert _storage._snapshot(tmp_path) == before
    assert harness.calls == calls


@pytest.mark.parametrize(argnames="direct", argvalues=[False, True])
def test_direct_edges_are_neither_closed_nor_reduced(
    direct: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reachability neither manufactures nor removes an independently judged edge.

    Parameters
    ----------
    direct
        Whether the shortcut was directly accepted.
    monkeypatch
        Offline response seam.
    tmp_path
        Isolated artifacts.
    """
    harness = _Harness(batch=1, root=tmp_path)
    harness.decisions = {
        (1, 2): ("buildsTowards", "first_to_second"),
        (2, 3): ("buildsTowards", "first_to_second"),
        (3, 4): ("relatesTo", None),
    }
    if direct:
        harness.decisions[(1, 3)] = ("buildsTowards", "first_to_second")
    _install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    artifact = harness._finalize()
    expected = {(1, 2, "buildsTowards"), (2, 3, "buildsTowards"), (3, 4, "relatesTo")}
    if direct:
        expected.add((1, 3, "buildsTowards"))
    assert _published(artifact) == expected
    assert artifact.decision_counts == {
        "buildsTowards": 2 + direct,
        "relatesTo": 1,
        "no_relation": 3 - direct,
        "needs_review": 0,
    }
    assert artifact.cycle_diagnostics.graph_edge_count == 2 + direct
    assert artifact.cycle_diagnostics.graph_node_count == 3
    assert artifact.cycle_diagnostics.cyclic_component_count == 0


@pytest.mark.parametrize(argnames="count", argvalues=[0, 1])
def test_empty_populations_require_completed_execution_and_round_trip(
    count: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No candidates is a valid completed population with no model or graph edges.

    Parameters
    ----------
    count
        Number of standards insufficient to form any pair.
    monkeypatch
        Offline seam.
    tmp_path
        Isolated artifacts.
    """
    harness = _Harness(count=count, root=tmp_path)
    _install(harness=harness, monkeypatch=monkeypatch)
    assert not harness._run()
    artifact = harness._finalize()
    assert artifact.total_claims == 0
    assert artifact.claims == ()
    assert set(artifact.decision_counts.values()) == {0}
    assert artifact.cycle_diagnostics.graph_node_count == 0
    assert harness._finalize(validate=True) == artifact
    assert not harness.calls


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "both_relations",
        "duplicate",
        "endpoint",
        "extra",
        "missing",
        "opposite_direction",
        "recurring_relation",
        "reverse_canonical",
        "self_pair",
        "unauthorized_relation",
        "warning",
    ],
)
def test_forged_complete_checker_corrections_cannot_bypass_pair_integrity(
    attack: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Rehashed complete corrections still obey coverage, identity, and permissions.

    Parameters
    ----------
    attack
        Invalid checker-selected interpretation after real complete generation.
    monkeypatch
        Offline response seam.
    tmp_path
        Isolated evidence artifacts.
    """
    harness = _Harness(count=3, root=tmp_path)
    harness.bundle.items[1].metadata["identity_scope_values"]["Grade"] = "PRIMARY THREE"
    if attack == "unauthorized_relation":
        harness.config = _fixtures._config(batch=3, profile="pratham_science")
        relation = harness.config.learning_progressions.relates_to
        relation.allowed_statement_type_pairs = [
            pair
            for pair in relation.allowed_statement_type_pairs
            if pair.first_statement_type == "Indicator"
        ]
        for item in harness.bundle.items:
            item.statement_type = "NCERT Learning Outcome"
            item.metadata["identity_scope_values"] = {"Class": "Class IX"}
    if attack == "warning":
        harness.bundle = _fixtures._expanded_fixture("ghana_math")
        harness.config = _fixtures._config(batch=100, profile="ghana_math")
    harness.corrections = {}
    _install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    harness._finalize()
    verdict_rows = _storage._rows(tmp_path / _STAGES[1])
    correction = verdict_rows[0]["payload"]["corrected_response"]
    _mutate_correction(attack=attack, judgments=correction["judgments"])
    _rewrite_stage(name=_STAGES[1], root=tmp_path, rows=verdict_rows)
    response_rows = _storage._rows(tmp_path / _STAGES[2])
    response_rows[0]["payload"] = deepcopy(correction)
    response_rows[0]["prerequisite_content_hash"] = _hash(verdict_rows[0])
    _rewrite_stage(name=_STAGES[2], root=tmp_path, rows=response_rows)
    before = _storage._snapshot(tmp_path)
    for validate in (False, True):
        with pytest.raises((ValueError, QualityError)):
            harness._finalize(validate=validate)
        assert _storage._snapshot(tmp_path) == before


@pytest.mark.parametrize(argnames="cyclic", argvalues=[False, True])
def test_long_graph_traversal_exceeds_recursive_depth_without_losing_edges(
    cyclic: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Iterative global traversal handles paths longer than the available call stack.

    Parameters
    ----------
    cyclic
        Close the long path into one strongly connected component.
    monkeypatch
        Offline model seam.
    tmp_path
        Isolated long-graph evidence directory.
    """
    count = 300
    harness = _Harness(batch=count * 2, count=count, root=tmp_path)
    budgets = harness.config.learning_progressions.candidate_policy.budgets
    budgets.max_candidates_per_sfi = 4
    budgets.max_total_candidates = count * 2
    for index, item in enumerate(harness.bundle.items):
        item.description = f"token{index:05d} token{(index + 1) % count:05d}"
    harness.decisions = {
        (n, n + 1): ("buildsTowards", "first_to_second") for n in range(1, count)
    }
    if cyclic:
        harness.decisions[(1, count)] = ("buildsTowards", "second_to_first")
    _install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    candidates = _storage._rows(tmp_path / "lp_candidate_pairs.jsonl")
    actual_pairs = {
        (UUID(c["first_sfi_uuid"]).int, UUID(c["second_sfi_uuid"]).int)
        for c in candidates
    }
    assert set(harness.decisions) <= actual_pairs
    previous_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(256)
        if cyclic:
            with pytest.raises(LPFinalizationCycleError) as caught:
                harness._finalize()
            artifact = caught.value.artifact
            assert artifact.cycle_diagnostics.cyclic_node_count == count
            assert artifact.cycle_diagnostics.cyclic_edge_count == count
            assert (
                len(artifact.cycle_diagnostics.components[0].representative_cycle)
                == count + 1
            )
        else:
            artifact = harness._finalize()
            assert artifact.graph_status == "acyclic"
            assert artifact.cycle_diagnostics.cyclic_component_count == 0
        assert artifact.cycle_diagnostics.graph_node_count == count
        assert artifact.cycle_diagnostics.graph_edge_count == count - 1 + cyclic
        assert len(_published(artifact)) == count - 1 + cyclic
    finally:
        sys.setrecursionlimit(previous_limit)


@pytest.mark.parametrize(
    argnames="name",
    argvalues=[
        *_STAGES,
        _FAILURE,
        _RECEIPT,
        "lp_candidate_pairs.jsonl",
        "lp_candidate_summary.json",
        "lp_generation_requests.jsonl",
        "lp_generation_requests_manifest.json",
    ],
)
def test_missing_evidence_is_never_reinitialized_by_finalization(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    tmp_path: Path,
) -> None:
    """Finalization cannot initialize missing adjudication or trust final claims alone.

    Parameters
    ----------
    monkeypatch
        Offline response seam.
    name
        Required evidence file to remove.
    tmp_path
        Isolated evidence directory.
    """
    harness = _Harness(count=2, root=tmp_path)
    _install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    harness._finalize()
    (tmp_path / name).unlink()
    before = _storage._snapshot(tmp_path)
    for validate in (False, True):
        with pytest.raises(_REJECTIONS):
            harness._finalize(validate=validate)
        assert _storage._snapshot(tmp_path) == before


@pytest.mark.parametrize(argnames="batch", argvalues=[1, 5])
def test_multiple_sccs_exclude_bridges_and_retain_every_direct_claim(
    batch: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Global diagnostics cover all cyclic edges and provenance across request cuts.

    Parameters
    ----------
    batch
        Distinct request cuts through the same directed graph.
    monkeypatch
        Offline response seam.
    tmp_path
        Isolated artifact directory.
    """
    harness = _Harness(batch=batch, count=8, root=tmp_path)
    directed = {
        (1, 2),
        (2, 3),
        (3, 1),
        (1, 4),
        (4, 3),
        (3, 5),
        (5, 6),
        (6, 7),
        (7, 5),
        (7, 8),
    }
    harness.decisions = {
        (min(a, b), max(a, b)): (
            "buildsTowards",
            "first_to_second" if a < b else "second_to_first",
        )
        for a, b in directed
    }
    harness.decisions[(2, 8)] = ("relatesTo", None)
    _install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    upstream = _storage._snapshot(tmp_path)
    with pytest.raises(LPFinalizationCycleError) as caught:
        harness._finalize()
    artifact = LPFinalClaims.model_validate_json((tmp_path / _FINAL).read_bytes())
    assert caught.value.artifact == artifact
    _assert_provenance(artifact=artifact, root=tmp_path)
    assert _published(artifact) == {(a, b, "buildsTowards") for a, b in directed} | {
        (2, 8, "relatesTo")
    }
    diagnostics = artifact.cycle_diagnostics
    assert diagnostics.cyclic_component_count == 2
    assert diagnostics.cyclic_node_count == 7
    assert diagnostics.cyclic_edge_count == 8
    assert diagnostics.graph_edge_count == 10
    assert diagnostics.graph_node_count == 8
    assert [tuple(n.int for n in c.node_uuids) for c in diagnostics.components] == [
        (1, 2, 3, 4),
        (5, 6, 7),
    ]
    assert [
        tuple(n.int for n in c.representative_cycle) for c in diagnostics.components
    ] == [(1, 2, 3, 1), (5, 6, 7, 5)]
    cyclic = directed - {(3, 5), (7, 8)}
    assert {
        (e.source_sfi_uuid.int, e.target_sfi_uuid.int)
        for c in diagnostics.components
        for e in c.edges
    } == cyclic
    claims = {c.judgment.pair_id: c for c in artifact.claims}
    for component in diagnostics.components:
        assert component.node_count == len(component.node_uuids)
        assert component.edge_count == len(component.edges)
        for edge in component.edges:
            provenance = claims[edge.pair_id].provenance
            assert edge.request_id == provenance.request_id
            assert (
                edge.producer_checkpoint_content_hash
                == provenance.producer_checkpoint_content_hash
            )
            assert (
                edge.checker_checkpoint_content_hash
                == provenance.checker_checkpoint_content_hash
            )
            assert (
                edge.response_checkpoint_content_hash
                == provenance.response_checkpoint_content_hash
            )
            assert edge.judgment_content_hash == provenance.judgment_content_hash
        pairs = {
            (e.source_sfi_uuid, e.target_sfi_uuid): e.pair_id for e in component.edges
        }
        assert component.representative_pair_ids == tuple(
            pairs[(a, b)]
            for a, b in zip(
                component.representative_cycle, component.representative_cycle[1:]
            )
        )
    persisted = (tmp_path / _FINAL).read_bytes()
    harness.bundle.items.reverse()
    for validate in (False, True):
        with pytest.raises(LPFinalizationCycleError):
            harness._finalize(validate=validate)
        assert (tmp_path / _FINAL).read_bytes() == persisted
    assert {
        k: v for k, v in _storage._snapshot(tmp_path).items() if k != _FINAL
    } == upstream


@pytest.mark.parametrize(
    argnames="coordinate", argvalues=["forward", "missing", "reverse", "same"]
)
@pytest.mark.parametrize(
    argnames="decision",
    argvalues=["buildsTowards", "relatesTo", "no_relation", "needs_review"],
)
def test_permissions_follow_local_coordinates_and_relation_specific_matrices(
    coordinate: str,
    decision: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Admissible outcomes honor rank direction and missing-coordinate restrictions.

    Parameters
    ----------
    coordinate
        Independent local rank arrangement relative to technical UUID order.
    decision
        Synthetic chosen relation or nonpublishing outcome.
    monkeypatch
        Offline model seam.
    tmp_path
        Isolated artifact directory.
    """
    harness = _Harness(count=2, root=tmp_path)
    if coordinate == "forward":
        harness.bundle.items[1].metadata["identity_scope_values"][
            "Grade"
        ] = "PRIMARY THREE"
    elif coordinate == "reverse":
        harness.bundle.items[0].metadata["identity_scope_values"][
            "Grade"
        ] = "PRIMARY THREE"
    elif coordinate == "missing":
        harness.bundle.items[1].metadata["identity_scope_values"] = {}
    direction = None
    if decision == "buildsTowards":
        direction = "second_to_first" if coordinate == "reverse" else "first_to_second"
    harness.decisions[(1, 2)] = (decision, direction)
    _install(harness=harness, monkeypatch=monkeypatch)
    if coordinate == "missing" and decision == "buildsTowards":
        with pytest.raises(LPGenerationFailed):
            harness._run()
        with pytest.raises(LPGenerationFailed):
            harness._finalize()
        assert not (tmp_path / _FINAL).exists()
        return
    harness._run()
    artifact = harness._finalize()
    assert artifact.total_claims == 1
    assert artifact.claims[0].judgment.decision == decision
    if decision == "buildsTowards":
        assert _published(artifact) == {
            (2, 1, decision) if coordinate == "reverse" else (1, 2, decision)
        }
    elif decision == "relatesTo":
        assert _published(artifact) == {(1, 2, decision)}
    else:
        assert _published(artifact) == set()


@pytest.mark.parametrize(
    argnames="decision", argvalues=["no_relation", "needs_review", "relatesTo"]
)
@pytest.mark.parametrize(argnames="profile", argvalues=_PROFILES)
def test_profile_warnings_evidence_and_provenance_survive_finalization(
    decision: str,
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
    tmp_path: Path,
) -> None:
    """Real reduced tree, DAG, and unresolved inputs retain their process evidence.

    Parameters
    ----------
    decision
        Explicit scripted accepted, negative, or ambiguous outcome.
    monkeypatch
        Offline response seam.
    profile
        Approved reduced curriculum fixture plus synthetic same-grain peers.
    tmp_path
        Isolated artifacts.
    """
    harness = _Harness(root=tmp_path)
    harness.default_decision = decision
    harness.bundle = _fixtures._expanded_fixture(profile)
    harness.config = _fixtures._config(batch=3, profile=profile)
    _install(harness=harness, monkeypatch=monkeypatch)
    responses = harness._run()
    upstream = _storage._snapshot(tmp_path)
    artifact = harness._finalize()
    assert artifact.total_claims > 0
    assert artifact.total_claims == sum(len(r.judgments) for r in responses)
    assert len(_published(artifact)) == (
        artifact.total_claims if decision == "relatesTo" else 0
    )
    candidates = _storage._rows(tmp_path / "lp_candidate_pairs.jsonl")
    assert [c.candidate.model_dump(mode="json") for c in artifact.claims] == candidates
    _assert_provenance(artifact=artifact, root=tmp_path)
    for claim in artifact.claims:
        assert set(claim.candidate.warnings) <= set(claim.judgment.warnings)
        assert claim.judgment.decision == decision
        if decision == "relatesTo":
            assert claim.source_sfi_uuid == claim.candidate.first_sfi_uuid
            assert claim.target_sfi_uuid == claim.candidate.second_sfi_uuid
        else:
            assert claim.source_sfi_uuid is None and claim.target_sfi_uuid is None
    if profile == "ghana_math":
        assert any(c.candidate.warnings for c in artifact.claims)
    assert harness._finalize(validate=True) == artifact
    assert {
        k: v for k, v in _storage._snapshot(tmp_path).items() if k != _FINAL
    } == upstream


@pytest.mark.parametrize(argnames="batch", argvalues=[1, 2])
def test_provenance_binds_each_pair_to_its_own_mixed_checker_outcome(
    batch: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Distinct requests retain their own accepted or corrected audit trail.

    Parameters
    ----------
    batch
        Request boundary variation across the same six candidate pairs.
    monkeypatch
        Offline proposal seam.
    tmp_path
        Isolated persisted artifacts.
    """
    harness = _Harness(batch=batch, root=tmp_path)
    harness.corrected_requests = {1}
    harness.decisions = {
        (1, 2): ("buildsTowards", "first_to_second"),
        (1, 3): ("no_relation", None),
        (1, 4): ("relatesTo", None),
        (2, 3): ("needs_review", None),
        (2, 4): ("buildsTowards", "first_to_second"),
        (3, 4): ("no_relation", None),
    }
    harness.corrections = {pair: ("relatesTo", None) for pair in harness.decisions}
    _install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    requests = _storage._rows(tmp_path / "lp_generation_requests.jsonl")
    verdicts = _storage._rows(tmp_path / _STAGES[1])
    assert len(requests) == 6 // batch
    assert [row["payload"]["passed"] for row in verdicts] == [
        index != 1 for index in range(len(requests))
    ]
    expected = {
        pair["pair_id"]: (
            "relatesTo"
            if request["request_index"] == 1
            else harness.decisions[
                (UUID(pair["first_sfi_uuid"]).int, UUID(pair["second_sfi_uuid"]).int)
            ][0]
        )
        for request in requests
        for pair in request["pairs"]
    }
    artifact = harness._finalize()
    assert {
        claim.judgment.pair_id: claim.judgment.decision for claim in artifact.claims
    } == expected
    _assert_provenance(artifact=artifact, root=tmp_path)
    _assert_provenance(artifact=harness._finalize(validate=True), root=tmp_path)


@pytest.mark.parametrize(argnames="stage", argvalues=["draft", "verdict"])
def test_resumed_failures_require_actual_completed_dispositions(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    tmp_path: Path,
) -> None:
    """A failed pair blocks claims until genuine resume completes and links evidence.

    Parameters
    ----------
    monkeypatch
        Offline response seam.
    stage
        Stage that exhausts its permitted attempts.
    tmp_path
        Isolated evidence directory.
    """
    harness = _Harness(batch=1, count=3, root=tmp_path)
    harness.failure = (stage, 1)
    _install(harness=harness, monkeypatch=monkeypatch)
    with pytest.raises(LPGenerationFailed):
        harness._run()
    before = _storage._snapshot(tmp_path)
    calls = list(harness.calls)
    with pytest.raises(LPGenerationFailed):
        harness._finalize()
    assert _storage._snapshot(tmp_path) == before
    harness.failure = None
    harness._run()
    artifact = harness._finalize()
    assert artifact.total_claims == 3
    assert harness.calls.count(("draft", 0)) == 1
    if stage == "verdict":
        assert harness.calls.count(("draft", 1)) == 1
    assert len(harness.calls) > len(calls)
    assert harness._finalize(validate=True) == artifact
    failure = json.loads((tmp_path / _FAILURE).read_bytes())
    assert failure[0]["resolved_run_number"] == 2
    failure[0]["resolved_response_content_hash"] = "0" * 64
    (tmp_path / _FAILURE).write_bytes(_storage._bytes(failure))
    _storage._reseal(name=_FAILURE, root=tmp_path)
    with pytest.raises(ValueError, match="disposition lacks matching completion"):
        harness._finalize()


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "budget",
        "checker_instructions",
        "coordinate",
        "model",
        "policy",
        "producer_instructions",
        "source",
        "transaction",
    ],
)
def test_stale_material_and_unfinished_transactions_fail_closed(
    attack: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Current inputs must reconstruct the same candidate, request, and execution set.

    Parameters
    ----------
    attack
        Material input changed after completed execution.
    monkeypatch
        Restoring model and response seams.
    tmp_path
        Isolated evidence directory.
    """
    harness = _Harness(count=3, root=tmp_path)
    _install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    harness._finalize()
    policy = harness.config.learning_progressions
    if attack == "budget":
        policy.candidate_policy.budgets.max_total_candidates = 0
    elif attack in {"checker_instructions", "producer_instructions"}:
        setattr(policy, attack, getattr(policy, attack) + " Changed material.")
    elif attack == "coordinate":
        harness.bundle.items[0].metadata["identity_scope_values"][
            "Grade"
        ] = "PRIMARY TWO"
    elif attack == "model":
        monkeypatch.setattr(name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5")
    elif attack == "policy":
        policy.unresolved_participation = "exclude_unresolved"
    elif attack == "source":
        harness.bundle.items[0].description += " Changed source evidence."
    elif attack == "transaction":
        (tmp_path / "lp_generation_checkpoint_transaction.json").write_text("{}\n")
    before = _storage._snapshot(tmp_path)
    for validate in (False, True):
        with pytest.raises(_REJECTIONS):
            harness._finalize(validate=validate)
        assert _storage._snapshot(tmp_path) == before
