"""Bind offline entry-point recovery to real prep timestamps and upstream provenance."""

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import json
import socket

from copy import deepcopy
from datetime import datetime, timezone
from operator import itemgetter
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

# Third Party Library
import pytest

# Package Library
from kgfeg.config import Settings
from kgfeg.entries import create_kgs
from kgfeg.kgs import lc_export, prompts, sfi_export, utils
from kgfeg.kgs.lp_generation import LPGenerationFailed
from kgfeg.kgs.schemas import AcademicStandardsKGBundle, AcademicStandardsLCKGBundle
from kgfeg.kgs.utils import KGDirs, persist_kg_run_manifest
from tests.kgfeg.kgs import test_create_kgs_lp as _entry
from tests.kgfeg.kgs import test_lp_export as _export
from tests.kgfeg.kgs import test_lp_generation as _fixtures
from tests.kgfeg.kgs import test_lp_orchestration as _storage
from tests.kgfeg.kgs import test_lp_reuse as _reuse

_FIRST = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
_LATER = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
_MANIFEST = "kg_run_manifest.json"


class _Harness(_entry._Harness):
    """Retain real AS provenance construction and AS+LC compilation before LP."""

    def _as_export(self, **kwargs: Any) -> AcademicStandardsKGBundle:
        """Assemble reduced AS material with the production provenance builder.

        Extraction and AS topology validation remain represented by reduced fixtures;
        manifest embedding, provenance merging, and all LP stages execute real code.

        Parameters
        ----------
        kwargs
            Entry-point AS export arguments, including the actual DocumentIR.

        Returns
        -------
        AcademicStandardsKGBundle
            Reduced standards boundary carrying the actual saved prep manifest.
        """
        records = []
        for item in self.bundle.items:
            saved = self.bundle.entity_provenance.get("items", {}).get(
                str(item.case_identifier_uuid), {}
            )
            records.append(
                SimpleNamespace(
                    **{
                        name: saved.get(name, [])
                        for name in (
                            "audit_flags",
                            "audit_notes",
                            "audit_peer_merge_group_ids",
                            "candidate_source_refs",
                            "candidate_source_texts",
                            "source_context_keys",
                            "source_page_indexes",
                            "source_registry_candidate_ids",
                            "source_segment_ids",
                            "source_window_ids",
                            "source_window_indexes",
                        )
                    },
                    **{
                        name: saved.get(name, "synthetic-source-record")
                        for name in (
                            "identity_key",
                            "merge_decision",
                            "merge_group_id",
                            "merge_reason",
                        )
                    },
                    final_sfi_uuid=item.case_identifier_uuid,
                )
            )
        provenance = sfi_export._build_entity_provenance(
            document_ir=kwargs["document_ir"],
            kg_run_manifest=_json(self.root / _MANIFEST),
            relationships=self.bundle.relationships_has_child,
            sf=self.bundle.framework,
            sfi_final_records=records,
            sfis=self.bundle.items,
        )
        report = self.bundle.validation_report.model_copy(deep=True)
        report.object_counts["learning_commons_unresolved_fallback_relationships"] = (
            self.bundle.summary.academic_standards.learning_commons_unresolved_fallback_relationship_count
        )
        bundle = AcademicStandardsKGBundle(
            entity_provenance=provenance,
            framework=self.bundle.framework,
            items=self.bundle.items,
            relationships_has_child=self.bundle.relationships_has_child,
            summary=self.bundle.summary.academic_standards,
            unresolved_items=self.bundle.unresolved_items.academic_standards,
            validation_report=report,
        )
        nodes = sfi_export._build_learning_commons_nodes(
            grade_level_mapping=self.config.academic_standards.grade_level_mapping,
            sf=bundle.framework,
            sfis=bundle.items,
        )
        edges = sfi_export._build_learning_commons_relationships(
            nodes=nodes, relationships=bundle.relationships_has_child
        )
        for name, rows in (
            ("as_nodes.jsonl", nodes),
            ("as_relationships.jsonl", edges),
        ):
            (self.root / name).write_text(
                "".join(
                    row.model_dump_json(by_alias=True, exclude_none=True) + "\n"
                    for row in rows
                )
            )
        (self.root / "as_kg_bundle.json").write_text(bundle.model_dump_json())
        return bundle

    def _install(
        self, *, monkeypatch: pytest.MonkeyPatch, real_lp: bool = True
    ) -> None:
        """Keep the manifest builder, upstream merge, and every LP integrity seam real.

        Parameters
        ----------
        monkeypatch
            Restoring offline substitutions.
        real_lp
            Required true for this integration harness.
        """
        assert real_lp
        for component in self.bundle.learning_components:
            component.metadata.setdefault(
                "identity", {"identity_key": f"synthetic-lc:{component.identifier}"}
            )
        super()._install(monkeypatch=monkeypatch, real_lp=True)
        self.mocks["compile_academic_standards_kg"].side_effect = self._phase(
            name="compile_academic_standards_kg", operation=self._as_export
        )
        self.mocks["compile_as_lc_kg"].side_effect = self._phase(
            name="compile_as_lc_kg", operation=self._merge
        )
        self.results["mint_learning_components"] = self.bundle.learning_components
        self.results["build_lc_supports_edges"] = self.bundle.relationships_supports
        self.results["summarize_learning_components"] = (
            self.bundle.summary.learning_components
        )
        (self.root / "lc_generation_failures.json").write_text("[]")
        (self.root / "lc_entity_provenance.json").write_text(
            json.dumps(
                {
                    "doc_key": _entry._DOC_KEY,
                    "learning_components": self.bundle.entity_provenance.get(
                        "learning_components", {}
                    ),
                }
            )
        )

    def _merge(self, **kwargs: Any) -> AcademicStandardsLCKGBundle:
        """Observe the actual merged bundle handed to downstream generation and reuse.

        Parameters
        ----------
        kwargs
            Real AS bundle, reduced LC material, directories, and overwrite flag.

        Returns
        -------
        AcademicStandardsLCKGBundle
            Production-compiled upstream authority.
        """
        self.merged = lc_export.compile_as_lc_kg(**kwargs)
        return self.merged


def _assert_bound(harness: _Harness) -> None:
    """Independently hash the complete upstream bundle, including its prep timestamp.

    Parameters
    ----------
    harness
        Run with genuine provenance construction, merge, and bounded requests.
    """
    manifest = _json(harness.root / _MANIFEST)
    upstream = _json(harness.root / "as_lc_kg_bundle.json")
    assert upstream["entity_provenance"]["kg_run_manifest"] == manifest
    assert (
        _json(harness.root / "as_kg_bundle.json")["entity_provenance"][
            "kg_run_manifest"
        ]
        == manifest
    )
    # Graph population encounter order is not an identity input.
    for field, identifier in (
        ("items", "case_identifier_uuid"),
        ("learning_components", "identifier"),
        ("relationships_has_child", "identifier"),
        ("relationships_supports", "identifier"),
    ):
        upstream[field] = sorted(upstream[field], key=itemgetter(identifier))
    expected = _hash(upstream)
    requests = _storage._rows(harness.root / "lp_generation_requests.jsonl")
    assert requests
    assert {row["upstream_content_hash"] for row in requests} == {expected}
    altered = deepcopy(upstream)
    altered["entity_provenance"]["kg_run_manifest"][
        "created_at"
    ] = "2000-01-01T00:00:00+00:00"
    assert _hash(altered) != expected
    _storage._assert_population(harness.root)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid external transport and use only the existing model setting.

    Parameters
    ----------
    monkeypatch
        Restoring network guards.
    """
    guard = Mock(side_effect=AssertionError("Manifest tests must stay offline."))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)
    monkeypatch.setattr(name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5.2")


@pytest.fixture(name="manifest_clock")
def _clock(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Control prep and run-start clocks without sleeps or timestamp normalization.

    Parameters
    ----------
    monkeypatch
        Restoring prep-clock substitution.

    Returns
    -------
    Mock
        Clock whose later return value cannot affect persisted original material.
    """
    now = Mock(return_value=_FIRST)
    monkeypatch.setattr(
        name="datetime",
        target=utils,
        value=SimpleNamespace(fromisoformat=datetime.fromisoformat, now=now),
    )
    return now


def _hash(value: Any) -> str:
    """Compute an independent digest of complete canonical material.

    Parameters
    ----------
    value
        Actual JSON-compatible artifact content.

    Returns
    -------
    str
        SHA-256 digest without omitted provenance fields.
    """
    return hashlib.sha256(
        json.dumps(
            allow_nan=False,
            ensure_ascii=False,
            obj=value,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _json(path: Path) -> Any:
    """Read actual persisted JSON without reconstructing an expected artifact.

    Parameters
    ----------
    path
        Temporary test artifact.

    Returns
    -------
    Any
        Decoded material.
    """
    return json.loads(path.read_bytes())


@pytest.mark.parametrize(
    argnames="change",
    argvalues=[
        "artifact",
        "config",
        "model",
        "prep_source",
        "prompt",
        "source",
        "timestamp",
    ],
)
@pytest.mark.parametrize(argnames="stage", argvalues=["draft", "verdict"])
def test_changed_inputs_after_failure_reject_before_calls_and_keep_failure_evidence(
    change: str,
    manifest_clock: Mock,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    tmp_path: Path,
) -> None:
    """Reject real material drift without concealing the original failed attempt.

    Parameters
    ----------
    change
        Source, policy, prompt, model, checkpoint, or valid timestamp mutation.
    manifest_clock
        Controlled prep clock.
    monkeypatch
        Offline phase and definition substitutions.
    stage
        Interrupted producer or checker.
    tmp_path
        Isolated synthetic workspace.
    """
    harness = _Harness(root=tmp_path)
    harness.proposals.failure = (stage, 1)
    harness._install(monkeypatch=monkeypatch)
    with pytest.raises(LPGenerationFailed):
        create_kgs.create(harness.config_path)
    failure = (harness.root / "lp_generation_failures.json").read_bytes()
    if change == "config":
        harness.config.learning_progressions.producer_instructions += (
            " New evidence rule."
        )
        harness._save_config()
    elif change == "model":
        monkeypatch.setattr(
            name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5.1"
        )
    elif change == "prompt":
        changed = tmp_path / "changed_prompt.py"
        changed.write_bytes(
            Path(prompts.__file__).read_bytes().replace(b"buildsTowards", b"relatesTo")
        )
        monkeypatch.setattr(name="__file__", target=prompts, value=str(changed))
    elif change == "prep_source":
        create_kgs.load_and_validate_inputs.return_value.document_ir.pdf_name = (
            "changed-source.pdf"
        )
    elif change == "source":
        harness.bundle.items[0].description += " Changed source-backed statement."
    elif change == "artifact":
        path = harness.root / "lp_generation_draft_responses.jsonl"
        path.write_bytes(path.read_bytes()[:-5])
    else:
        manifest = _json(harness.root / _MANIFEST)
        manifest["created_at"] = "2001-01-01T00:00:00+00:00"
        (harness.root / _MANIFEST).write_text(json.dumps(manifest))
    before = _reuse._state(harness.root)
    manifest_clock.return_value = _LATER
    _reuse._reset_entry(harness)
    harness.proposals.failure = None
    with pytest.raises(ValueError) as caught:
        create_kgs.create(harness.config_path)
    _entry._assert_run(error=type(caught.value), harness=harness)
    assert not harness.proposals.calls
    after = _reuse._state(harness.root)
    for name in before:
        if name.startswith("lp_") or name.startswith("as_lc_lp_") or name == _MANIFEST:
            assert after[name] == before[name]
    assert (harness.root / "lp_generation_failures.json").read_bytes() == failure


@pytest.mark.parametrize(argnames="profile", argvalues=_fixtures._PROFILES)
def test_final_reuse_keeps_manifest_bound_across_six_curriculum_merges(
    manifest_clock: Mock, monkeypatch: pytest.MonkeyPatch, profile: str, tmp_path: Path
) -> None:
    """Recover projections without changing provenance, calls, counts, or identities.

    Parameters
    ----------
    manifest_clock
        Clock advanced between otherwise identical invocations.
    monkeypatch
        Offline extraction and adjudication substitutions.
    profile
        Reduced curriculum retaining its distinctive graph and evidence shape.
    tmp_path
        Isolated synthetic workspace.
    """
    harness = _Harness(root=tmp_path)
    harness.bundle = _fixtures._expanded_fixture(profile)
    harness.config = _fixtures._config(batch=20, profile=profile)
    harness.config.overwrite = False
    harness.proposals.bundle = harness.bundle
    harness.proposals.config = harness.config
    harness.proposals.default_decision = "relatesTo"
    harness._save_config()
    harness._install(monkeypatch=monkeypatch)
    create_kgs.create(harness.config_path)
    _assert_bound(harness)
    before = _reuse._state(harness.root)
    combined = _json(harness.root / "as_lc_lp_kg_bundle.json")
    assert (
        combined["entity_provenance"]["kg_run_manifest"]["created_at"]
        == _FIRST.isoformat()
    )
    assert combined["validation_report"]["pedagogical_correctness_established"] is False
    assert (
        combined["entity_provenance"]["items"]
        == harness.merged.entity_provenance["items"]
    )
    for key in (
        "items",
        "relationships_has_child",
        "learning_components",
        "relationships_supports",
    ):
        assert combined[key] == harness.bundle.model_dump(mode="json")[key]
    for name in _reuse._PROJECTIONS:
        (harness.root / name).unlink()
    manifest_clock.return_value = _LATER
    _reuse._reset_entry(harness)
    create_kgs.create(harness.config_path)
    _entry._assert_run(error=None, harness=harness)
    _assert_bound(harness)
    assert harness.proposals.calls == []
    assert harness.calls == list(_entry._PHASES[:-5])
    after = _reuse._state(harness.root)
    for name in before:
        if name.startswith("lp_") or name in (_MANIFEST, _reuse._BUNDLE):
            assert after[name] == before[name]
    assert after["as_lc_kg_bundle.json"] == before["as_lc_kg_bundle.json"]
    for name, expected in _export._projection_bytes(combined).items():
        assert after[name][0] == expected


@pytest.mark.parametrize(
    argnames="downstream",
    argvalues=["lp_generation_requests.jsonl", "as_lc_lp_kg_bundle.json"],
)
def test_missing_manifest_with_downstream_evidence_fails_without_writing(
    downstream: str, tmp_path: Path
) -> None:
    """Never fabricate prep provenance for an existing LP checkpoint or release.

    Parameters
    ----------
    downstream
        Evidence proving this directory is not a fresh prep run.
    tmp_path
        Isolated artifact directory.
    """
    (tmp_path / downstream).write_text("preserved downstream evidence")
    before = _reuse._state(tmp_path)
    with pytest.raises(expected_exception=ValueError, match="missing KG run manifest"):
        persist_kg_run_manifest(
            kg_dirs=KGDirs(root=tmp_path),
            manifest={"created_at": _LATER.isoformat(), "doc_key": "synthetic"},
            overwrite=False,
        )
    assert _reuse._state(tmp_path) == before


@pytest.mark.parametrize(argnames="existing_as", argvalues=[False, True])
def test_missing_prep_manifest_before_lp_still_initializes(
    existing_as: bool, tmp_path: Path
) -> None:
    """Allow initial prep and pre-LP compatibility without modifying existing AS files.

    Parameters
    ----------
    existing_as
        Include unrelated pre-LP AS evidence in the run directory.
    tmp_path
        Isolated artifact directory.
    """
    if existing_as:
        (tmp_path / "as_kg_bundle.json").write_text("existing AS evidence")
    before = _reuse._state(tmp_path)
    manifest = {"created_at": _FIRST.isoformat(), "doc_key": "synthetic"}
    result = persist_kg_run_manifest(
        kg_dirs=KGDirs(root=tmp_path), manifest=manifest, overwrite=False
    )
    assert _json(result) == manifest
    for name, state in before.items():
        assert _reuse._state(tmp_path)[name] == state


@pytest.mark.parametrize(argnames="overwrite", argvalues=[False, True])
def test_new_and_overwrite_runs_bind_the_complete_effective_config(
    manifest_clock: Mock,
    monkeypatch: pytest.MonkeyPatch,
    overwrite: bool,
    tmp_path: Path,
) -> None:
    """Create fresh manifests while retaining full-config invalidation after regeneration.

    Parameters
    ----------
    manifest_clock
        Deterministic creation times for fresh and overwritten runs.
    monkeypatch
        Offline extraction and model seams.
    overwrite
        Replace an existing completed run with changed policy and source material.
    tmp_path
        Isolated synthetic workspace.
    """
    harness = _Harness(root=tmp_path)
    harness._install(monkeypatch=monkeypatch)
    create_kgs.create(harness.config_path)
    first = _json(harness.root / _MANIFEST)
    assert first["created_at"] == _FIRST.isoformat()
    if overwrite:
        manifest_clock.return_value = _LATER
        harness.config.overwrite = True
        harness.config.metadata.framework_title += " Revised"
        harness.config.learning_progressions.request_batch_size = 2
        harness._save_config()
        _reuse._reset_entry(harness)
        create_kgs.create(harness.config_path)
        _entry._assert_run(error=None, harness=harness)
        assert _json(harness.root / _MANIFEST)["created_at"] == _LATER.isoformat()
        assert harness.proposals.calls == [
            ("draft", 0),
            ("verdict", 0),
            ("draft", 1),
            ("verdict", 1),
        ]
        harness.config.overwrite = False
        harness._save_config()
    _assert_bound(harness)
    before = _reuse._state(harness.root)
    manifest_clock.return_value = _LATER
    _reuse._reset_entry(harness)
    if overwrite:
        # Changing the overwrite field changes the complete effective configuration.
        with pytest.raises(expected_exception=ValueError, match="current material"):
            create_kgs.create(harness.config_path)
        _entry._assert_run(error=ValueError, harness=harness)
    else:
        create_kgs.create(harness.config_path)
        _entry._assert_run(error=None, harness=harness)
    assert not harness.proposals.calls
    after = _reuse._state(harness.root)
    for name in before:
        if name in _reuse._PROJECTIONS and not overwrite:
            assert after[name][0] == before[name][0]
        elif (
            name == _MANIFEST or name.startswith("lp_") or name.startswith("as_lc_lp_")
        ):
            assert after[name] == before[name]


@pytest.mark.parametrize(argnames="saved", argvalues=[None, "{}", "invalid JSON"])
def test_overwrite_can_replace_missing_or_invalid_prep_material(
    saved: str | None, tmp_path: Path
) -> None:
    """Let explicit overwrite replace prep material without editing downstream evidence.

    Parameters
    ----------
    saved
        Absent or invalid old prep artifact.
    tmp_path
        Isolated artifact directory.
    """
    if saved is not None:
        (tmp_path / _MANIFEST).write_text(saved)
    (tmp_path / "lp_generation_failures.json").write_text("retained failure evidence")
    before = _reuse._state(tmp_path)
    manifest = {"created_at": _LATER.isoformat(), "doc_key": "changed-source"}
    result = persist_kg_run_manifest(
        kg_dirs=KGDirs(root=tmp_path), manifest=manifest, overwrite=True
    )
    assert _json(result) == manifest
    assert (
        _reuse._state(tmp_path)["lp_generation_failures.json"]
        == before["lp_generation_failures.json"]
    )


@pytest.mark.parametrize(argnames="stage", argvalues=["draft", "verdict"])
def test_restart_uses_earliest_unfinished_stage_with_real_prep_provenance(
    manifest_clock: Mock, monkeypatch: pytest.MonkeyPatch, stage: str, tmp_path: Path
) -> None:
    """Retain completed calls and original failure facts when the prep clock advances.

    Parameters
    ----------
    manifest_clock
        Clock advanced by ten days before restart.
    monkeypatch
        Offline upstream and external-call substitutions.
    stage
        Producer or checker failure after one complete request.
    tmp_path
        Isolated synthetic workspace.
    """
    harness = _Harness(root=tmp_path)
    harness.proposals.failure = (stage, 1)
    harness._install(monkeypatch=monkeypatch)
    with pytest.raises(LPGenerationFailed):
        create_kgs.create(harness.config_path)
    _entry._assert_run(error=LPGenerationFailed, harness=harness)
    _assert_bound(harness)
    before = _reuse._state(harness.root)
    failures = _json(harness.root / "lp_generation_failures.json")
    manifest_clock.return_value = _LATER
    _reuse._reset_entry(harness)
    harness.proposals.failure = None
    create_kgs.create(harness.config_path)
    _entry._assert_run(error=None, harness=harness)
    _assert_bound(harness)
    assert harness.proposals.calls == (
        ([("draft", 1)] if stage == "draft" else [])
        + [("verdict", 1), ("draft", 2), ("verdict", 2)]
    )
    after = _reuse._state(harness.root)
    for name in (_MANIFEST, *_storage._INPUTS, "as_lc_kg_bundle.json"):
        assert after[name] == before[name]
    for name in _reuse._STAGES:
        assert after[name][0].startswith(before[name][0])
    recovered = _json(harness.root / "lp_generation_failures.json")
    assert len(recovered) == len(failures) == 1
    assert {
        key: value
        for key, value in recovered[0].items()
        if not key.startswith("resolved_")
    } == {
        key: value
        for key, value in failures[0].items()
        if not key.startswith("resolved_")
    }
    assert recovered[0]["resolved_run_number"] == 2
    assert (
        recovered[0]["resolved_response_content_hash"]
        == _storage._rows(harness.root / "lp_generation_responses.jsonl")[1][
            "payload_content_hash"
        ]
    )


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "invalid_json",
        "not_object",
        "missing_timestamp",
        "null_timestamp",
        "numeric_timestamp",
        "blank_timestamp",
        "invalid_date",
        "naive_timestamp",
        "date_only",
        "unknown_field",
        "missing_field",
        "changed_field",
        "changed_type",
    ],
)
def test_saved_manifest_corruption_fails_closed_before_upstream_work(
    attack: str, manifest_clock: Mock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject unauthenticated prep material without rewriting it or LP failure evidence.

    Parameters
    ----------
    attack
        Malformed manifest structure, material drift, or invalid timestamp.
    manifest_clock
        Different fresh clock value on the rejected restart.
    monkeypatch
        Offline phase seams.
    tmp_path
        Isolated synthetic workspace.
    """
    harness = _entry._Harness(root=tmp_path)
    harness.failure = "plan_extraction_windows"
    harness._install(monkeypatch=monkeypatch, real_lp=False)
    with pytest.raises(RuntimeError):
        create_kgs.create(harness.config_path)
    manifest = _json(harness.root / _MANIFEST)
    timestamps = {
        "blank_timestamp": "",
        "date_only": "2026-09-01",
        "invalid_date": "2026-02-30T12:00:00+00:00",
        "naive_timestamp": "2026-09-01T12:00:00",
        "null_timestamp": None,
        "numeric_timestamp": 42,
    }
    if attack in timestamps:
        manifest["created_at"] = timestamps[attack]
    elif attack == "missing_timestamp":
        del manifest["created_at"]
    elif attack == "unknown_field":
        manifest["unexpected"] = "untrusted"
    elif attack == "missing_field":
        del manifest["doc_key"]
    elif attack == "changed_field":
        manifest["framework_title"] += " Changed"
    elif attack == "changed_type":
        manifest["page_count"] = True
    payload = json.dumps(manifest)
    if attack == "invalid_json":
        payload = "{"
    elif attack == "not_object":
        payload = "[]"
    (harness.root / _MANIFEST).write_text(payload)
    (harness.root / "lp_generation_failures.json").write_text(
        "retained failure evidence"
    )
    before = _reuse._state(harness.root)
    manifest_clock.return_value = _LATER
    _reuse._reset_entry(harness)
    with pytest.raises(ValueError) as caught:
        create_kgs.create(harness.config_path)
    _entry._assert_run(error=type(caught.value), harness=harness)
    assert not harness.calls
    assert not harness.proposals.calls
    after = _reuse._state(harness.root)
    assert {k: v for k, v in after.items() if k != "kg_run.json"} == {
        k: v for k, v in before.items() if k != "kg_run.json"
    }


@pytest.mark.parametrize(
    argnames="timestamp",
    argvalues=[
        "2026-09-01T12:00:00+00:00",
        "2026-09-01T17:30:00+05:30",
        "2026-09-01T12:00:00Z",
    ],
)
def test_valid_saved_manifest_preserves_original_bytes_and_timestamp(
    timestamp: str, tmp_path: Path
) -> None:
    """Keep exact saved formatting and timezone text without mutating current inputs.

    Parameters
    ----------
    timestamp
        Valid original timezone-aware timestamp representation.
    tmp_path
        Isolated artifact directory.
    """
    saved = {
        "doc_key": "synthetic",
        "created_at": timestamp,
        "warnings": ["é", "retained"],
        "counts": {"blocks": 1},
    }
    path = tmp_path / _MANIFEST
    path.write_text(json.dumps(ensure_ascii=False, indent=3, obj=saved) + "\n\n")
    before = _reuse._state(tmp_path)
    current = {**saved, "created_at": _LATER.isoformat()}
    original_current = deepcopy(current)
    assert (
        persist_kg_run_manifest(
            kg_dirs=KGDirs(root=tmp_path), manifest=current, overwrite=False
        )
        == path
    )
    assert _reuse._state(tmp_path) == before
    assert current == original_current
