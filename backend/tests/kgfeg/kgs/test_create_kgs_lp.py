"""Verify KG phase boundaries, offline LP integration, and durable run outcomes."""

# Future Library
from __future__ import annotations

# Standard Library
import json
import socket

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, create_autospec

# Third Party Library
import pytest

from pydantic_ai.usage import RunUsage

# Package Library
from kgfeg.config import Settings
from kgfeg.entries import create_kgs
from kgfeg.kgs import lp_export, lp_generation
from kgfeg.kgs.llm import KGUsageTracker
from kgfeg.kgs.lp_finalization import LPFinalizationCycleError, LPRelationships
from kgfeg.kgs.lp_generation import LPGenerationFailed
from kgfeg.kgs.schemas import AcademicStandardsLCLPKGBundle
from kgfeg.kgs.utils import KGDirs
from kgfeg.schemas import RunCtx
from tests.kgfeg.kgs import test_lp_finalization as _claims
from tests.kgfeg.kgs import test_lp_generation as _fixtures
from tests.kgfeg.kgs import test_lp_orchestration as _storage

_DOC_KEY = "synthetic-selection-document"
_PHASES = (
    "plan_extraction_windows",
    "build_llm_extraction_windows",
    "extract_sfi_candidates_from_windows",
    "build_candidate_registry",
    "merge_sfi_candidates",
    "mint_final_sfi_ids",
    "resolve_has_child_edges",
    "compile_academic_standards_kg",
    "select_lc_source_sfis",
    "build_lc_generation_requests",
    "decompose_lc_source_sfis",
    "group_duplicate_skills",
    "mint_learning_components",
    "build_lc_supports_edges",
    "summarize_learning_components",
    "compile_as_lc_kg",
    "generate_learning_progressions",
    "finalize_learning_progressions",
    "build_lp_relationships",
    "write_lp_artifacts",
    "compile_as_lc_lp_kg",
)
_LP_PHASES = _PHASES[-5:]
_CHARGES = {
    "lc_generation": (11, 5),
    "lp_generation": (13, 7),
    "lp_generation_validation": (17, 11),
    "sfi_extraction": (7, 3),
}


class _Harness:
    """Stub upstream phase work while observing the real entry-point control flow."""

    def __init__(self, *, root: Path) -> None:
        """Create isolated scriptable phase results and a real runtime config file.

        Parameters
        ----------
        root
            Temporary directory containing every test-owned input and output.
        """
        self.root = root / _DOC_KEY / "kgs"
        self.root.mkdir(parents=True)
        self.proposals = _claims._Harness(batch=1, count=3, root=self.root)
        self.config = self.proposals.config
        self.config.overwrite = False
        self.bundle = self.proposals.bundle
        self.bundle.entity_provenance["custom_audit"] = {
            "nested": [None, False, 0, "é 漢字", {"empty": []}]
        }
        self.calls: list[str] = []
        self.charges: list[str] = []
        self.mocks: dict[str, Any] = {}
        self.trackers: list[KGUsageTracker] = []
        self.failure: str | None = None
        self.error = RuntimeError("Synthetic phase failure")
        self.results: dict[str, Any] = {name: object() for name in _PHASES}
        self.results.update(
            compile_academic_standards_kg=SimpleNamespace(
                summary=self.bundle.summary.academic_standards
            ),
            compile_as_lc_kg=self.bundle,
            compile_as_lc_lp_kg=SimpleNamespace(validation_report=_report()),
            select_lc_source_sfis=(object(), object()),
            write_lp_artifacts=SimpleNamespace(validation_report=_report()),
        )
        self.source = root / "synthetic.pdf"
        self.source.write_bytes(b"Synthetic source identity; never parsed as a PDF.")
        self.document = root / "document_ir.json"
        self.document.write_text("{}")
        self.config_path = root / "runtime.json"
        self._save_config()

    def _charge(self, *, bucket: str, tracker: KGUsageTracker) -> None:
        """Record known usage at a replaced external-call boundary.

        Parameters
        ----------
        bucket
            Agent bucket receiving this synthetic call.
        tracker
            Actual tracker supplied by entry-point orchestration.
        """
        self.charges.append(bucket)
        self.trackers.append(tracker)
        input_tokens, output_tokens = _CHARGES[bucket]
        getattr(tracker, bucket).add_run_usage(
            RunUsage(input_tokens=input_tokens, output_tokens=output_tokens, requests=1)
        )

    def _install(self, *, monkeypatch: pytest.MonkeyPatch, real_lp: bool) -> None:
        """Install typed phase seams while retaining status and artifact persistence.

        Parameters
        ----------
        monkeypatch
            Restoring substitutions.
        real_lp
            Keep all LP production stages running against scripted proposals.
        """
        inputs = SimpleNamespace(
            document_ir=SimpleNamespace(doc_key=_DOC_KEY), kg_config=self.config
        )
        for name, result in (
            ("build_run_manifest", {"doc_key": _DOC_KEY}),
            ("compute_doc_key", _DOC_KEY),
            ("cross_check_stitching_run", self.document),
            ("load_and_validate_inputs", inputs),
        ):
            replacement = create_autospec(
                return_value=result, spec=getattr(create_kgs, name)
            )
            monkeypatch.setattr(name=name, target=create_kgs, value=replacement)
        for name in _PHASES:
            operation = getattr(create_kgs, name)
            replacement = create_autospec(
                side_effect=self._phase(
                    name=name,
                    operation=operation if real_lp and name in _LP_PHASES else None,
                ),
                spec=operation,
            )
            self.mocks[name] = replacement
            monkeypatch.setattr(name=name, target=create_kgs, value=replacement)
        monkeypatch.setattr(
            name="generate_learning_progressions_for_request",
            target=lp_generation,
            value=self._proposal,
        )

    def _phase(self, *, name: str, operation: Any) -> Any:
        """Build a signature-checked observer or deterministic phase replacement.

        Parameters
        ----------
        name
            Phase recorded in the execution trace.
        operation
            Real LP operation, or None for a replaced phase.

        Returns
        -------
        Any
            Callable installed behind an autospecced public boundary.
        """

        def _invoke(**kwargs: Any) -> Any:
            """Observe phase arguments, preserve usage, and inject a selected failure.

            Parameters
            ----------
            kwargs
                Actual arguments from the entry point.

            Returns
            -------
            Any
                Real or synthetic phase result.
            """
            self.calls.append(name)
            if "usage_tracker" in kwargs:
                self.trackers.append(kwargs["usage_tracker"])
            buckets = {
                "extract_sfi_candidates_from_windows": ("sfi_extraction",),
                "decompose_lc_source_sfis": ("lc_generation",),
                "generate_learning_progressions": (
                    ("lp_generation", "lp_generation_validation")
                    if operation is None
                    else ()
                ),
            }.get(name, ())
            for bucket in buckets:
                self._charge(bucket=bucket, tracker=kwargs["usage_tracker"])
            if name == self.failure:
                raise self.error
            if operation is not None:
                return operation(**kwargs)
            return self.results[name]

        return _invoke

    def _proposal(self, **kwargs: Any) -> Any:
        """Observe complete materialization and script only the external model seam.

        Parameters
        ----------
        kwargs
            Bounded production request, draft, model, and shared tracker.

        Returns
        -------
        Any
            Deterministic producer proposal or checker verdict.
        """
        _storage._assert_population(self.root)
        assert kwargs["model_config"].model == Settings.LLM_KG_MODEL
        self._charge(
            bucket=(
                "lp_generation"
                if kwargs["draft"] is None
                else "lp_generation_validation"
            ),
            tracker=kwargs["usage_tracker"],
        )
        return self.proposals._call(**kwargs)

    def _save_config(self) -> None:
        """Write current KG policy with entirely temporary extraction paths."""
        payload = {
            "document_ir": {},
            "kgs": self.config.model_dump(by_alias=True, mode="json"),
            "page_ir_extraction": {
                "country": "Synthetic country",
                "languages": ["en"],
                "output_dir": str(self.root.parents[1]),
                "pdf_fp": str(self.source),
            },
            "page_ir_verification": {},
        }
        self.config_path.write_text(json.dumps(payload))


def _assert_run(*, error: type[Exception] | None, harness: _Harness) -> dict[str, Any]:
    """Reconcile persisted completion and usage independently from observed calls.

    Parameters
    ----------
    error
        Expected propagated exception type, or None on success.
    harness
        Call trace and temporary run artifact.

    Returns
    -------
    dict[str, Any]
        Round-tripped run metadata for additional scenario assertions.
    """
    path = harness.root / "kg_run.json"
    run = json.loads(path.read_bytes())
    RunCtx.model_validate_json(path.read_bytes())
    assert run["run_id"]
    assert datetime.fromisoformat(run["completed_at"]) >= datetime.fromisoformat(
        run["started_at"]
    )
    extra = run["extra"]
    assert extra["status"] == ("error" if error else "success")
    if error:
        assert extra["error"]["type"] == error.__name__
        assert extra["error"]["message"]
        assert "Traceback" in extra["error"]["traceback"]
        assert "build_kgs" in extra["error"]["traceback"]
    else:
        assert "error" not in extra
    usage = extra["usage"]
    assert set(usage["agents"]) == {
        "lc_dedup",
        "lc_generation",
        "lc_generation_validation",
        "lp_generation",
        "lp_generation_validation",
        "sfi_dedup",
        "sfi_dedup_validation",
        "sfi_extraction",
        "sfi_extraction_validation",
        "sfi_has_child",
        "sfi_has_child_validation",
    }
    totals = {key: 0 for key in usage["totals"]}
    for bucket, observed in usage["agents"].items():
        count = harness.charges.count(bucket)
        input_tokens, output_tokens = _CHARGES.get(bucket, (0, 0))
        expected = {
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "input_tokens": count * input_tokens,
            "output_tokens": count * output_tokens,
            "requests": count,
            "runs": count,
            "total_tokens": count * (input_tokens + output_tokens),
        }
        assert observed == {"agent_name": bucket, **expected}
        for key, value in expected.items():
            totals[key] += value
    assert usage["totals"] == totals
    assert all(tracker is harness.trackers[0] for tracker in harness.trackers)
    return run


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject external transport and pin only the existing shared KG model setting.

    Parameters
    ----------
    monkeypatch
        Restoring socket and model-setting substitutions.
    """
    guard = Mock(side_effect=AssertionError("Entry-point tests must stay offline."))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)
    monkeypatch.setattr(name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5.2")


def _report() -> SimpleNamespace:
    """Return a mutable passing report for isolated orchestration fault injection.

    Returns
    -------
    SimpleNamespace
        Minimal boundary fields; actual validators run in the integration cases.
    """
    return SimpleNamespace(errors=[], passed=True, warnings=["Nonblocking warning"])


@pytest.mark.parametrize(argnames="overwrite", argvalues=[False, True])
def test_build_preserves_phase_order_bundle_arguments_and_overwrite(
    monkeypatch: pytest.MonkeyPatch, overwrite: bool, tmp_path: Path
) -> None:
    """Require explicit upstream authority, exact sequencing, and shared operation flags.

    Parameters
    ----------
    monkeypatch
        Restoring phase observers.
    overwrite
        Requested overwrite or checkpoint-resume mode.
    tmp_path
        Isolated run directory.
    """
    harness = _Harness(root=tmp_path)
    harness.config.overwrite = overwrite
    harness._install(monkeypatch=monkeypatch, real_lp=False)
    tracker = KGUsageTracker()
    directory = KGDirs(root=harness.root)
    result = create_kgs.build_kgs(
        config=harness.config,
        document_ir_fp=harness.document,
        expected_doc_key=_DOC_KEY,
        kg_dirs=directory,
        usage_tracker=tracker,
    )
    assert harness.calls == list(_PHASES)
    assert result == harness.root / "kg_run_manifest.json"
    assert json.loads(result.read_bytes()) == {"doc_key": _DOC_KEY}
    for name in _LP_PHASES:
        kwargs = harness.mocks[name].call_args.kwargs
        assert kwargs["as_lc_bundle"] is harness.bundle
        assert kwargs["doc_key"] == _DOC_KEY
        assert kwargs["kg_config"] is harness.config
        assert kwargs["kg_dirs"] is directory
    assert (
        harness.mocks["write_lp_artifacts"].call_args.kwargs["relationships"]
        is harness.results["build_lp_relationships"]
    )
    for replacement in harness.mocks.values():
        kwargs = replacement.call_args.kwargs
        if "overwrite" in kwargs:
            assert kwargs["overwrite"] is overwrite
        if "usage_tracker" in kwargs:
            assert kwargs["usage_tracker"] is tracker
    assert (
        harness.mocks["generate_learning_progressions"].call_args.kwargs["overwrite"]
        is overwrite
    )


@pytest.mark.parametrize(argnames="phase", argvalues=_PHASES)
def test_create_propagates_every_phase_failure_and_persists_partial_usage(
    monkeypatch: pytest.MonkeyPatch, phase: str, tmp_path: Path
) -> None:
    """Halt at the first failed phase while retaining exception identity and run evidence.

    Parameters
    ----------
    monkeypatch
        Restoring phase failure injector.
    phase
        Phase that fails after any known usage has been charged.
    tmp_path
        Isolated run directory.
    """
    harness = _Harness(root=tmp_path)
    harness.failure = phase
    harness._install(monkeypatch=monkeypatch, real_lp=False)
    with pytest.raises(RuntimeError) as caught:
        create_kgs.create(harness.config_path)
    assert caught.value is harness.error
    assert harness.calls == list(_PHASES[: _PHASES.index(phase) + 1])
    run = _assert_run(error=RuntimeError, harness=harness)
    assert run["extra"]["error"]["message"] == str(harness.error)


@pytest.mark.parametrize(argnames="stage", argvalues=["draft", "verdict"])
def test_create_real_failed_pair_halts_and_resumes_at_unfinished_stage(
    monkeypatch: pytest.MonkeyPatch, stage: str, tmp_path: Path
) -> None:
    """Keep failed processing distinct from ambiguity and reuse valid saved work.

    Parameters
    ----------
    monkeypatch
        Restoring upstream and external-call seams.
    stage
        Producer or checker stage failing on the second candidate request.
    tmp_path
        Isolated run directory.
    """
    harness = _Harness(root=tmp_path)
    harness.proposals.failure = (stage, 1)
    harness._install(monkeypatch=monkeypatch, real_lp=True)
    with pytest.raises(LPGenerationFailed):
        create_kgs.create(harness.config_path)
    _assert_run(error=LPGenerationFailed, harness=harness)
    assert harness.calls == list(_PHASES[:-4])
    assert not (harness.root / "as_lc_lp_kg_bundle.json").exists()
    failures = json.loads((harness.root / "lp_generation_failures.json").read_bytes())
    assert failures
    completed_calls = list(harness.proposals.calls)
    assert completed_calls[:2] == [("draft", 0), ("verdict", 0)]
    draft_bytes = (harness.root / "lp_generation_draft_responses.jsonl").read_bytes()
    harness.proposals.failure = None
    harness.calls.clear()
    harness.charges.clear()
    harness.trackers.clear()
    harness.proposals.calls.clear()
    create_kgs.create(harness.config_path)
    _assert_run(error=None, harness=harness)
    assert harness.proposals.calls == (
        ([("draft", 1)] if stage == "draft" else [])
        + [("verdict", 1), ("draft", 2), ("verdict", 2)]
    )
    assert (
        (harness.root / "lp_generation_draft_responses.jsonl")
        .read_bytes()
        .startswith(draft_bytes)
    )
    assert harness.calls == list(_PHASES)


@pytest.mark.parametrize(
    argnames="fault", argvalues=["cycle", "metadata", "provenance_collision"]
)
def test_create_real_invalid_graph_retains_diagnostics_and_error(
    fault: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Persist failed run state for real finalization, standalone, and combined rejection.

    Parameters
    ----------
    fault
        Invalid cycle, relationship metadata, or colliding provenance namespace.
    monkeypatch
        Restoring upstream, model, and metadata-corruption seams.
    tmp_path
        Isolated run directory.
    """
    harness = _Harness(root=tmp_path)
    harness.proposals.decisions = {(1, 2): ("buildsTowards", "first_to_second")}
    expected_error: type[Exception] = ValueError
    final_phase = "compile_as_lc_lp_kg"
    if fault == "cycle":
        harness.proposals.decisions.update(
            {
                (1, 3): ("buildsTowards", "second_to_first"),
                (2, 3): ("buildsTowards", "first_to_second"),
            }
        )
        expected_error = LPFinalizationCycleError
        final_phase = "finalize_learning_progressions"
    elif fault == "metadata":
        final_phase = "write_lp_artifacts"
    else:
        harness.bundle.entity_provenance["relationships_builds_towards"] = {
            "retained": True
        }
    harness._install(monkeypatch=monkeypatch, real_lp=True)
    if fault == "metadata":
        original = harness.mocks["build_lp_relationships"].side_effect

        def _corrupt(**kwargs: Any) -> LPRelationships:
            """Alter one minted field before real standalone validation observes it.

            Parameters
            ----------
            kwargs
                Actual relationship minting inputs.

            Returns
            -------
            LPRelationships
                Otherwise intact minted relationships with incorrect ownership.
            """
            relationships = original(**kwargs)
            relationships.relationships_builds_towards[0].author = (
                "Incorrect synthetic author"
            )
            return relationships

        harness.mocks["build_lp_relationships"].side_effect = _corrupt
    with pytest.raises(expected_error):
        create_kgs.create(harness.config_path)
    _assert_run(error=expected_error, harness=harness)
    assert harness.calls == list(_PHASES[: _PHASES.index(final_phase) + 1])
    assert not (harness.root / "as_lc_lp_kg_bundle.json").exists()
    assert (harness.root / "lp_final_claims.json").exists()
    if fault == "metadata":
        report = json.loads((harness.root / "lp_validation_report.json").read_bytes())
        assert report["passed"] is False
        assert report["errors"]
    if fault == "provenance_collision":
        report = json.loads((harness.root / "lp_validation_report.json").read_bytes())
        assert report["passed"] is True
        assert harness.bundle.entity_provenance["relationships_builds_towards"] == {
            "retained": True
        }


@pytest.mark.parametrize(
    argnames="profile", argvalues=["ghana_math", "pratham_science"]
)
def test_create_real_lp_preserves_upstream_graph_and_projection_content(
    monkeypatch: pytest.MonkeyPatch, profile: str, tmp_path: Path
) -> None:
    """Carry multi-parent and warned upstream material through the complete LP suffix.

    Parameters
    ----------
    monkeypatch
        Restoring upstream and model-call seams.
    profile
        Reduced graph with LC alignments and distinctive ancestry.
    tmp_path
        Isolated run directory.
    """
    harness = _Harness(root=tmp_path)
    harness.bundle = _fixtures._expanded_fixture(profile)
    harness.proposals.bundle = harness.bundle
    harness.config = _fixtures._config(batch=20, profile=profile)
    harness.config.overwrite = False
    harness.proposals.config = harness.config
    harness.proposals.default_decision = "relatesTo"
    harness._save_config()
    harness.results["compile_as_lc_kg"] = harness.bundle
    harness.bundle.entity_provenance["custom_audit"] = {"nested": [None, False, "é"]}
    upstream = harness.bundle.model_dump(mode="json")
    names = (
        "as_kg_bundle.json",
        "as_nodes.jsonl",
        "as_relationships.jsonl",
        "as_lc_kg_bundle.json",
        "as_lc_nodes.jsonl",
        "as_lc_relationships.jsonl",
    )
    before = {name: (name + " opaque upstream sentinel\n").encode() for name in names}
    for name, payload in before.items():
        (harness.root / name).write_bytes(payload)
    harness._install(monkeypatch=monkeypatch, real_lp=True)
    create_kgs.create(harness.config_path)
    _assert_run(error=None, harness=harness)
    assert harness.calls == list(_PHASES)
    combined = json.loads((harness.root / "as_lc_lp_kg_bundle.json").read_bytes())
    AcademicStandardsLCLPKGBundle.model_validate(combined)
    assert harness.bundle.model_dump(mode="json") == upstream
    for key in (
        "framework",
        "items",
        "learning_components",
        "relationships_has_child",
        "relationships_supports",
    ):
        assert combined[key] == upstream[key]
    for key in ("entity_provenance", "unresolved_items"):
        for field, value in upstream[key].items():
            assert combined[key][field] == value
    assert combined["summary"]["as_lc_summary"] == upstream["summary"]
    for layer in ("academic_standards", "learning_components"):
        assert combined["summary"][layer] == upstream["summary"][layer]
    assert {name: (harness.root / name).read_bytes() for name in names} == before
    nodes = _storage._rows(harness.root / "as_lc_lp_nodes.jsonl")
    expected_nodes = [{**upstream["framework"], "entity_type": "StandardsFramework"}]
    for key, entity in (
        ("items", "StandardsFrameworkItem"),
        ("learning_components", "LearningComponent"),
    ):
        expected_nodes.extend({**item, "entity_type": entity} for item in upstream[key])
    assert sorted(json.dumps(obj=row, sort_keys=True) for row in nodes) == sorted(
        json.dumps(obj=row, sort_keys=True) for row in expected_nodes
    )
    edges = _storage._rows(harness.root / "as_lc_lp_relationships.jsonl")
    expected_edges: list[dict[str, Any]] = sum(
        (
            combined[key]
            for key in (
                "relationships_has_child",
                "relationships_supports",
                "relationships_builds_towards",
                "relationships_relates_to",
            )
        ),
        [],
    )
    assert sorted(json.dumps(obj=row, sort_keys=True) for row in edges) == sorted(
        json.dumps(obj=row, sort_keys=True) for row in expected_edges
    )
    assert combined["summary"]["total_node_count"] == len(nodes)
    assert combined["summary"]["total_relationship_count"] == len(edges)
    assert combined["relationships_relates_to"]
    assert not combined["relationships_builds_towards"]


@pytest.mark.parametrize(
    argnames="outcome", argvalues=["empty", "mixed", "needs_review", "no_relation"]
)
def test_create_real_lp_success_counts_decisions_without_semantic_gate(
    monkeypatch: pytest.MonkeyPatch, outcome: str, tmp_path: Path
) -> None:
    """Publish only accepted proposals while keeping negative and ambiguous outcomes visible.

    Parameters
    ----------
    monkeypatch
        Restoring upstream and model-call seams.
    outcome
        Empty, mixed, negative, or ambiguous candidate population.
    tmp_path
        Isolated run directory.
    """
    harness = _Harness(root=tmp_path)
    if outcome == "empty":
        harness.bundle = _fixtures._bundle(1)
        harness.proposals.bundle = harness.bundle
        harness.results["compile_as_lc_kg"] = harness.bundle
    elif outcome == "mixed":
        harness.proposals.decisions = {
            (1, 2): ("buildsTowards", "first_to_second"),
            (1, 3): ("relatesTo", None),
        }
        harness.proposals.default_decision = "needs_review"
    else:
        harness.proposals.default_decision = outcome
    harness._install(monkeypatch=monkeypatch, real_lp=True)
    create_kgs.create(harness.config_path)
    _assert_run(error=None, harness=harness)
    bundle = json.loads((harness.root / "as_lc_lp_kg_bundle.json").read_bytes())
    counts = bundle["summary"]["learning_progressions"]["object_counts"]
    assert (
        counts["candidate_pairs"]
        == counts["final_claims"]
        == (0 if outcome == "empty" else 3)
    )
    assert counts["accepted_claims"] == (2 if outcome == "mixed" else 0)
    assert counts["no_relation_claims"] == (3 if outcome == "no_relation" else 0)
    assert counts["needs_review_claims"] == (
        3 if outcome == "needs_review" else 1 if outcome == "mixed" else 0
    )
    assert counts["unresolved_failed_pairs"] == 0
    assert len(bundle["relationships_builds_towards"]) == (
        1 if outcome == "mixed" else 0
    )
    assert len(bundle["relationships_relates_to"]) == (1 if outcome == "mixed" else 0)
    assert (
        bundle["unresolved_items"]["learning_progressions"]["total_needs_review"]
        == counts["needs_review_claims"]
    )
    report = bundle["validation_report"]
    assert report["passed"] and not report["errors"]
    assert report["pedagogical_correctness_established"] is False
    assert report["semantic_validation_performed"] is False
    assert harness.calls == list(_PHASES)
    assert len(harness.proposals.calls) == (0 if outcome == "empty" else 6)


def test_create_real_projection_write_failure_cannot_report_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep run failure visible when a combined projection cannot be persisted.

    Parameters
    ----------
    monkeypatch
        Restoring low-level writer fault injector.
    tmp_path
        Isolated run directory.
    """
    harness = _Harness(root=tmp_path)
    harness._install(monkeypatch=monkeypatch, real_lp=True)
    original = lp_export._atomic_write

    def _write(*, path: Path, payload: bytes) -> None:
        """Fail one output write while letting all real validation and earlier writes run.

        Parameters
        ----------
        path
            Artifact destination.
        payload
            Validated serialized bytes.
        """
        if path.name == "as_lc_lp_relationships.jsonl":
            raise OSError("Synthetic projection write failure")
        original(path=path, payload=payload)

    monkeypatch.setattr(name="_atomic_write", target=lp_export, value=_write)
    with pytest.raises(
        expected_exception=OSError, match="Synthetic projection write failure"
    ):
        create_kgs.create(harness.config_path)
    _assert_run(error=OSError, harness=harness)
    assert harness.calls == list(_PHASES)
    assert (harness.root / "as_lc_lp_kg_bundle.json").exists()
    assert not (harness.root / "as_lc_lp_relationships.jsonl").exists()


@pytest.mark.parametrize(
    argnames="errors,passed",
    argvalues=[
        ([], False),
        (["Synthetic validation error"], False),
        (["Synthetic validation error"], True),
    ],
)
@pytest.mark.parametrize(
    argnames="phase",
    argvalues=["compile_as_lc_kg", "write_lp_artifacts", "compile_as_lc_lp_kg"],
)
def test_create_rejects_failed_or_error_bearing_reports(
    errors: list[str],
    monkeypatch: pytest.MonkeyPatch,
    passed: bool,
    phase: str,
    tmp_path: Path,
) -> None:
    """Require both a passed report and an empty error list at every release boundary.

    Parameters
    ----------
    errors
        Report errors independent of its claimed passed state.
    monkeypatch
        Restoring phase seams.
    passed
        Claimed validation outcome.
    phase
        Upstream, standalone, or combined boundary returning the report.
    tmp_path
        Isolated run directory.
    """
    harness = _Harness(root=tmp_path)
    report = harness.results[phase].validation_report
    report.errors = errors
    report.passed = passed
    harness._install(monkeypatch=monkeypatch, real_lp=False)
    with pytest.raises(ValueError):
        create_kgs.create(harness.config_path)
    _assert_run(error=ValueError, harness=harness)
    assert harness.calls == list(_PHASES[: _PHASES.index(phase) + 1])
