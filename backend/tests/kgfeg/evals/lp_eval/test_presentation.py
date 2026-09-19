"""Independently reconcile saved presentation outputs and report-only boundaries."""

# Standard Library
import base64
import copy
import gzip
import hashlib
import io
import json
import re
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

from pathlib import Path
from typing import Any
from unittest.mock import Mock

# Third Party Library
import pytest

from typer.testing import CliRunner

# Package Library
from kgfeg.config import BackendSettings
from kgfeg.entries import evaluate_lps as entry
from kgfeg.evals.lp_eval import judge, presentation, sampling, scoring
from kgfeg.evals.lp_eval.schemas import (
    EvaluationSettings,
    JudgeUsage,
    ReportProvenance,
    resolve_evaluation_settings,
)
from tests.fixtures.lp_eval.snapshot_fixtures import build_snapshot

# Private pure transformations expose exact-difference and membership contracts.
# Underscored fixtures still require parameter documentation.
# pylint: disable=protected-access,useless-param-doc


def _bytes(root: Path) -> dict[str, str]:
    """Capture file membership and byte identities in isolated evidence.

    Parameters
    ----------
    root
        Test-owned tree.

    Returns
    -------
    dict[str, str]
        Relative paths and SHA-256 digests.
    """
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid network dispatch throughout the synthetic checks.

    Parameters
    ----------
    monkeypatch
        Restoring socket replacements.
    """
    guard = Mock(side_effect=AssertionError("Presentation tests cannot connect"))
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(name=name, target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)


def _response(*, index: int, request: Any) -> str:
    """Construct varied valid outputs without semantic truth claims.

    Parameters
    ----------
    index
        Deterministic outcome selector.
    request
        Scheduled response identity and allowed decisions.

    Returns
    -------
    str
        A synthetic, schema-valid response.
    """
    prompt = request.prompt
    shown = json.loads(prompt.user_message)
    result = {
        **shown["response_identity"],
        "confidence": 0.5,
        "explanation": "Synthetic diagnostic response.",
    }
    if prompt.task == "classification":
        choices = shown["assessment_permissions"]
        choice = choices[index % len(choices)]
        result.update(choice)
        result["evidence_references"] = [prompt.references[0]]
    else:
        result.update(
            grounding="unsupported",
            claims=[
                {
                    "claim": "Synthetic unsupported rationale claim",
                    "evidence_references": [],
                    "explanation": "No shown supporting evidence.",
                    "support": "unsupported",
                }
            ],
        )
    return json.dumps(result)


@pytest.fixture(params=[1, 2], scope="module")
# The fixture retains separate source, invocation and derivative identities.
# pylint: disable-next=too-many-locals
def _saved(request: Any, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Publish real reports from small authenticated synthetic invocation material.

    Parameters
    ----------
    request
        Number of unfamiliar synthetic curricula.
    tmp_path_factory
        Isolated storage factory.

    Returns
    -------
    dict[str, Any]
        Complete and partial report references with generated presentation data.
    """
    root = tmp_path_factory.mktemp(f"presentation-{request.param}").resolve()
    for identity in range(request.param):
        build_snapshot(
            count=4,
            identity=73000 + identity,
            root=root / "sources",
            title=f"Unfamiliar archipelago {identity}",
        )
    inventory = sampling.discover_lp_runs(
        evaluation_root=root / "results/lp_evals", results_root=root / "sources"
    )
    frozen = sampling.freeze_lp_inputs(inventory=inventory, repository_root=root)
    settings = resolve_evaluation_settings(
        overrides={
            "base_blind_replicates": 2,
            "critique_replicates": 2,
            "diagnostic_pairs_per_cohort": 2,
            "additional_diagnostic_replicates": 1,
            "variant_replicates": 3,
            "synthetic_cases_per_family": 1,
            "synthetic_control_replicates": 3,
        }
    )
    schedule = sampling.prepare_evaluation_schedule(
        inputs=frozen,
        judge=judge.resolve_judge_settings(
            BackendSettings(
                _env_file=None,
                PATHS_PROJECT_DIR=root,
                LEARNING_COMMONS_EXPORT_SCHEMA_VERSION="synthetic-test",
            )
        ),
        settings=settings,
    )
    store = judge.persist_evaluation_schedule(repository_root=root, schedule=schedule)
    # An empty saved report proves missing judgments and zero denominators survive.
    empty = scoring.write_evaluation_reports(
        provenance=ReportProvenance(evidence_kind="development"), reference=store
    )
    requests = [r for c in schedule.curricula for r in c.requests]
    requests.sort(key=lambda r: bool(r.dependencies))
    missing = next(
        r for r in requests if r.component == "controls" and r.replicate == 3
    )
    with judge.open_evaluation_store(store) as session:
        for index, item in enumerate(requests):
            if item == missing:
                continue
            attempt = session.start_attempt(item.prompt.request_id)
            if index == 0:
                session.record_failure(
                    attempt=attempt,
                    category="invalid_output",
                    message="Synthetic malformed response",
                    raw_response="{}",
                    usage=JudgeUsage(),
                )
                attempt = session.start_attempt(item.prompt.request_id)
            session.record_success(
                attempt=attempt,
                response_json=_response(index=index, request=item),
                usage=JudgeUsage(input_tokens=7, output_tokens=3),
            )
    partial = scoring.write_evaluation_reports(
        provenance=ReportProvenance(evidence_kind="development"), reference=store
    )
    with judge.open_evaluation_store(store) as session:
        session.record_success(
            attempt=session.start_attempt(missing.prompt.request_id),
            response_json=_response(index=4, request=missing),
            usage=JudgeUsage(),
        )
    complete = scoring.write_evaluation_reports(
        provenance=ReportProvenance(evidence_kind="development"), reference=store
    )
    destination = root / "review-presentation"
    presentation.render_evaluation_presentations(
        output_directory=destination, report_directory=partial.directory
    )
    return {
        "root": root,
        "schedule": schedule,
        "store": store,
        "empty": empty,
        "partial": partial,
        "complete": complete,
        "destination": destination,
        "data": json.loads((destination / "presentation_data.json").read_text()),
    }


def test_chart_memberships_and_raw_counts(_saved: dict[str, Any]) -> None:
    """Reconcile every chart against exact detail references, with case denominators.

    Parameters
    ----------
    _saved
        Validated reports with repeat overrides and one missing control response.
    """
    data = _saved["data"]
    rows = data["tables"]["Judgments"]
    refs = {
        r["Ref"]: r
        for name in ("Judgments", "Comparisons")
        for r in data["tables"][name]
    }
    assert len(data["views"]) == 6
    assert len({r["Document key"] for r in rows}) == len(_saved["schedule"].curricula)
    for view in data["views"]:
        assert view["panels"]
        for panel in view["panels"]:
            cases = panel["label"].startswith("Repeated-assessment consistency")
            for row in panel["rows"]:
                for cell in row["cells"]:
                    assert (
                        set(cell["refs"])
                        <= set(cell["denominator_refs"])
                        <= set(cell["planned_refs"])
                    )
                    for key, members in (
                        ("count", "refs"),
                        ("denominator", "denominator_refs"),
                        ("planned", "planned_refs"),
                    ):
                        selected = [refs[r] for r in cell[members]]
                        assert all(
                            r["Curriculum"] == row["curriculum"] for r in selected
                        )
                        expected = (
                            len({r["Pair"] for r in selected})
                            if cases
                            else len(selected)
                        )
                        assert cell[key] == expected
                    assert cell["unique_pairs"] == len(
                        {refs[r]["Pair"] for r in cell["refs"]}
                    )
                    assert cell["rate"] == (
                        cell["count"] / cell["denominator"]
                        if cell["denominator"]
                        else None
                    )
    assert any(
        c["rate"] is None
        for v in data["views"]
        for p in v["panels"]
        for r in p["rows"]
        for c in r["cells"]
    )
    assert any(r["Recovered failure"] for r in rows)
    assert data["report"]["usage_summary"]["cost"]["total_by_currency"] is None
    assert data["report"]["saved_failure_events"]
    for group in data["tables"]["Concerns"]:
        assert group["Member judgments"] == len(group["Judgments"])
        assert group["Unique pairs/cases"] == len(
            {refs[r]["Pair"] for r in group["Judgments"]}
        )
        assert group["Personal notes (not a disposition)"] == ""


def test_cli_options_and_partial_exit_preserve_source(
    _saved: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bypass preparation, execution, store opening and scoring even for partial reports.

    Parameters
    ----------
    _saved
        Isolated report material.
    monkeypatch
        Traps for forbidden execution paths.
    tmp_path
        Fresh derivative destination.
    """
    before = _bytes(_saved["root"])
    guard = Mock(side_effect=AssertionError("Rendering crossed the execution boundary"))
    for module, names in (
        (entry, ("prepare_evaluation", "run_evaluation")),
        (judge, ("open_evaluation_store",)),
        (
            scoring,
            ("score_evaluation", "write_evaluation_reports", "open_evaluation_store"),
        ),
    ):
        for name in names:
            monkeypatch.setattr(name=name, target=module, value=guard)
    monkeypatch.setattr(name="run", target=subprocess, value=guard)
    runner = CliRunner()
    args = [
        str(_saved["partial"].directory),
        "--render-report",
        "--output-directory",
        str(tmp_path / "rendered"),
    ]
    result = runner.invoke(app=entry.cli, args=args)
    assert result.exit_code == 0, result.output
    actual = json.loads((tmp_path / "rendered/presentation_data.json").read_text())
    assert actual["report"]["execution_complete"] is False
    assert actual["report"]["missing_judgments"] == 1
    assert (
        actual["report"]["valid_judgments"] == actual["report"]["planned_judgments"] - 1
    )
    options: list[tuple[str, str | None]] = [
        ("--" + name.replace("_", "-"), "1")
        for name in EvaluationSettings.model_fields  # pylint: disable=not-an-iterable
    ]
    options.extend([("--resume-manifest", "/missing"), ("--new-invocation", None)])
    for option, value in options:
        result = runner.invoke(
            app=entry.cli, args=args + [option] + ([value] if value else [])
        )
        assert result.exit_code == 2, result.output
    result = runner.invoke(
        app=entry.cli,
        args=[
            str(_saved["partial"].directory),
            "--output-directory",
            str(tmp_path / "bad"),
        ],
    )
    assert result.exit_code == 2
    assert _bytes(_saved["root"]) == before
    guard.assert_not_called()


@pytest.mark.parametrize("message_field", [None, "system_message", "user_message"])
@pytest.mark.parametrize("reverse", [False, True])
def test_comparison_json_types_and_message_independence(
    _saved: dict[str, Any], message_field: str | None, reverse: bool
) -> None:
    """Expose nested scalar changes in full and family diffs independently of messages.

    Parameters
    ----------
    _saved
        Synthetic saved report supplying comparison identities.
    message_field
        Optional message to change without deriving it from evidence.
    reverse
        Exercise both Boolean-to-number and number-to-Boolean changes.
    """
    report = copy.deepcopy(_saved["data"]["report"])
    report["comparisons"] = report["comparisons"][:1]
    item = report["comparisons"][0]
    by_id = {r["Request ID"]: r for r in _saved["data"]["tables"]["Judgments"]}
    first, second = [
        copy.deepcopy(by_id[item[key]])
        for key in ("first_request_id", "second_request_id")
    ]
    endpoint = first["Saved request"]["canonical_endpoint_uuids"][0]
    before_value, after_value = ([False, {"deep": [True]}], [0, {"deep": [1.0]}])
    if reverse:
        before_value, after_value = after_value, before_value
    before = {"sfis": [{"sfi_uuid": endpoint, "audit/~": before_value}]}
    after = {"sfis": [{"sfi_uuid": endpoint, "audit/~": after_value}]}
    first["Saved request"]["evidence"]["payload_json"] = json.dumps(before)
    second["Saved request"]["evidence"]["payload_json"] = json.dumps(after)
    second["Saved request"]["prompt"] = copy.deepcopy(first["Saved request"]["prompt"])
    if message_field:
        second["Saved request"]["prompt"][message_field] += " "
    row = presentation._comparison_rows(judgments=[first, second], report=report)[0]
    family = "Metadata and source evidence (may contain substantive text)"
    assert row["Payload identical"] is False
    assert row["Model-visible messages byte-identical"] is (message_field is None)
    assert row["Changed endpoint families"] == [family]
    expected = [
        {
            "path": "/sfis",
            "before_present": True,
            "after_present": True,
            "before": before["sfis"],
            "after": after["sfis"],
        }
    ]
    assert json.dumps(
        row["Complete payload differences"], sort_keys=True
    ) == json.dumps(expected, sort_keys=True)
    expected[0].update(
        path=f"/{endpoint}/audit~1~0", before=before_value, after=after_value
    )
    assert json.dumps(
        row["Endpoint differences"][family], sort_keys=True
    ) == json.dumps(expected, sort_keys=True)
    assert all(not v for k, v in row["Endpoint differences"].items() if k != family)
    # Equal payloads must not conceal a message-only difference.
    second["Saved request"]["evidence"]["payload_json"] = json.dumps(before)
    equal = presentation._comparison_rows(judgments=[first, second], report=report)[0]
    assert equal["Payload identical"] is True
    assert equal["Complete payload differences"] == []
    assert equal["Changed endpoint families"] == []
    assert equal["Model-visible messages byte-identical"] is (message_field is None)


def test_control_consistency_requires_multiple_complete_judgments() -> None:
    """Keep absent, singly assessed and partially assessed controls unavailable."""
    for outcomes in ([], ["relatesTo"], ["relatesTo", "Unavailable"]):
        judgments = [
            {"Evaluator outcome": outcome, "Ref": f"J{index}"}
            for index, outcome in enumerate(outcomes)
        ]
        cases = [(("synthetic-case",), judgments)] if judgments else []
        actual = presentation._control_consistency(cases)
        assert actual["rate"] is None
        assert actual["count"] == actual["denominator"] == 0
        assert actual["refs"] == actual["denominator_refs"] == []
        assert actual["planned"] == bool(judgments)
        assert actual["planned_refs"] == [row["Ref"] for row in judgments]


def test_coverage_exclusive_categories_and_overlapping_routes() -> None:
    """Pin all four positive categories without pooling diagnostic and uniform draws."""
    rows = []
    for index, (production, published, outcome) in enumerate(
        [
            ("Never nominated", None, "relatesTo"),
            ("no_relation", None, "relatesTo"),
            ("buildsTowards: A → B", "edge", "buildsTowards: B → A"),
            ("relatesTo", "edge", "relatesTo"),
            ("no_relation", None, "Ambiguous"),
            ("no_relation", None, "Unavailable"),
            ("no_relation", None, "no_relation"),
        ]
    ):
        rows.append(
            {
                "Ref": f"J{index}",
                "Pair": f"P{index}",
                "Curriculum": "Unfamiliar isle",
                "Component": "independent",
                "Role": "base",
                "Condition": "reconstructed_bounded_upstream",
                "Repetition": 1,
                "Routes": ["independent/uniform", "independent/tag/shared_lc"],
                "Production outcome": production,
                "Published relationship UUID": published,
                "Evaluator outcome": outcome,
            }
        )
    panels = presentation._coverage(names=["Unfamiliar isle", "Empty isle"], rows=rows)
    assert len(panels) == 2
    for panel in panels:
        actual, empty = panel["rows"]
        assert (actual["assessed"], actual["planned"], actual["positive"]) == (6, 7, 4)
        assert [c["refs"] for c in actual["cells"]] == [["J0"], ["J1"], ["J2"], ["J3"]]
        assert all(c["denominator"] == 4 and c["rate"] == 0.25 for c in actual["cells"])
        assert all(c["rate"] is None and c["planned"] == 0 for c in empty["cells"])
    assert panels[0]["rows"] == panels[1]["rows"]


def test_coverage_groups_use_base_positive_pairs(_saved: dict[str, Any]) -> None:
    """Derive coverage denominators from canonical valid common-condition assessments.

    Parameters
    ----------
    _saved
        Saved source report and rendered view.
    """
    data = _saved["data"]
    by_id = {r["Request ID"]: r for r in data["tables"]["Judgments"]}
    for panel in data["views"][0]["panels"]:
        replicate = int(panel["label"].rsplit(" ", 1)[1])
        uniform = panel["label"].startswith("Uniform")
        for row in panel["rows"]:
            planned = [
                r
                for r in data["report"]["assessments"]
                if r["component"] == "independent"
                and r["role"] == "base"
                and r["condition"] == "reconstructed_bounded_upstream"
                and r["replicate"] == replicate
                and by_id[r["request_id"]]["Curriculum"] == row["curriculum"]
                and any(
                    (
                        route == "independent/uniform"
                        if uniform
                        else route.startswith("independent/tag/")
                    )
                    for route in r["routes"]
                )
            ]
            positives = [
                r
                for r in planned
                if r["assessment"]
                and r["assessment"]["decision"] in ("buildsTowards", "relatesTo")
            ]
            assert row["positive"] == len(positives)
            assert row["planned"] == len(planned)
            assert sum(c["count"] for c in row["cells"]) == len(positives)
            assert all(
                set(c["denominator_refs"])
                == {by_id[r["request_id"]]["Ref"] for r in positives}
                for c in row["cells"]
            )


def test_empty_and_complete_report_status(
    _saved: dict[str, Any], tmp_path: Path
) -> None:
    """Retain empty denominators and independently distinguish complete execution.

    Parameters
    ----------
    _saved
        Empty, partial and complete immutable generations.
    tmp_path
        Fresh derivative directories.
    """
    for name in ("empty", "complete"):
        destination = presentation.render_evaluation_presentations(
            output_directory=tmp_path / name, report_directory=_saved[name].directory
        )
        data = json.loads((destination / "presentation_data.json").read_text())
        assert data["report"]["execution_complete"] == (name == "complete")
        if name == "empty":
            assert data["report"]["valid_judgments"] == 0
            assert all(
                c["rate"] is None
                for v in data["views"]
                for p in v["panels"]
                for r in p["rows"]
                for c in r["cells"]
            )


@pytest.mark.parametrize(
    ("after", "before", "expected"),
    [
        (
            {"a": {"~/": None}},
            {"a": {}},
            [
                {
                    "path": "/a/~0~1",
                    "before_present": False,
                    "after_present": True,
                    "before": None,
                    "after": None,
                }
            ],
        ),
        (
            {"a": {}},
            {"a": {"~/": None}},
            [
                {
                    "path": "/a/~0~1",
                    "before_present": True,
                    "after_present": False,
                    "before": None,
                    "after": None,
                }
            ],
        ),
        (
            {"": {"x/y~": False}},
            {"": {"x/y~": 0}},
            [
                {
                    "path": "//x~1y~0",
                    "before_present": True,
                    "after_present": True,
                    "before": 0,
                    "after": False,
                }
            ],
        ),
        (
            [{"x": [True]}],
            [{"x": [1]}],
            [
                {
                    "path": "/",
                    "before_present": True,
                    "after_present": True,
                    "before": [{"x": [1]}],
                    "after": [{"x": [True]}],
                }
            ],
        ),
        (
            {"v": [1, 2]},
            {"v": [2, 1]},
            [
                {
                    "path": "/v",
                    "before_present": True,
                    "after_present": True,
                    "before": [2, 1],
                    "after": [1, 2],
                }
            ],
        ),
        (
            {"v": [None]},
            {"v": []},
            [
                {
                    "path": "/v",
                    "before_present": True,
                    "after_present": True,
                    "before": [],
                    "after": [None],
                }
            ],
        ),
        (
            {"v": "1"},
            {"v": 1},
            [
                {
                    "path": "/v",
                    "before_present": True,
                    "after_present": True,
                    "before": 1,
                    "after": "1",
                }
            ],
        ),
        (
            {"v": 9007199254740993},
            {"v": 9007199254740992},
            [
                {
                    "path": "/v",
                    "before_present": True,
                    "after_present": True,
                    "before": 9007199254740992,
                    "after": 9007199254740993,
                }
            ],
        ),
        (
            {"b": [None, True, {"n": 1.0}], "a": {}},
            {"a": {}, "b": [None, True, {"n": 1}]},
            [],
        ),
        (
            {"v": []},
            {"v": {}},
            [
                {
                    "path": "/v",
                    "before_present": True,
                    "after_present": True,
                    "before": {},
                    "after": [],
                }
            ],
        ),
    ],
)
def test_evidence_difference_exact_values_and_presence(
    after: Any, before: Any, expected: list[dict[str, Any]]
) -> None:
    """Pin exact values, presence flags, pointer escaping and JSON equality boundaries.

    Parameters
    ----------
    after
        Changed JSON value.
    before
        Original JSON value.
    expected
        Independently specified complete difference records.
    """
    original = json.dumps([before, after], sort_keys=True)
    actual = presentation._differences(after=after, before=before)
    assert json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)
    assert json.dumps([before, after], sort_keys=True) == original


@pytest.mark.parametrize(
    ("after_value", "before_value"),
    [(0, False), (1, True), ([0], [False]), ({"value": 1}, {"value": True})],
)
def test_evidence_difference_preserves_json_scalar_types(
    after_value: Any, before_value: Any
) -> None:
    """Keep boolean-to-number changes visible even inside nested evidence.

    Parameters
    ----------
    after_value
        Numeric metadata or a nested container holding it.
    before_value
        Corresponding Boolean metadata that Python considers equal.
    """
    before = {"source_metadata": {"verified": before_value}}
    after = {"source_metadata": {"verified": after_value}}
    changes = presentation._differences(after=after, before=before)
    assert len(changes) == 1
    assert changes[0]["path"].startswith("/source_metadata/verified")
    assert json.dumps(changes[0]["before"]) != json.dumps(changes[0]["after"])


def test_evidence_families_and_absent_null_differences() -> None:
    """Distinguish substantive endpoint evidence from metadata and absent values."""
    original: dict[str, Any] = {
        "sfis": [
            {
                "context": {
                    "sfi_uuid": "a",
                    "description": {"text": "Shown text", "hash": "old"},
                },
                "learning_components": [
                    {"identifier": "lc", "description": {"text": "Skill"}}
                ],
                "ancestors": [
                    {"sfi_uuid": "parent", "description": {"text": "Parent"}}
                ],
                "ancestor_paths": [["parent"]],
                "parent_sfi_uuids": ["parent"],
            }
        ]
    }
    before = presentation._evidence_parts(endpoint_uuids=["a"], payload=original)
    metadata = copy.deepcopy(original)
    metadata["sfis"][0]["context"]["audit"] = "new audit"
    after = presentation._evidence_parts(endpoint_uuids=["a"], payload=metadata)
    assert after["Statement text"] == before["Statement text"]
    assert after["Learning Components"] == before["Learning Components"]
    assert after["Hierarchy"] == before["Hierarchy"]
    assert (
        after["Metadata and source evidence (may contain substantive text)"]
        != before["Metadata and source evidence (may contain substantive text)"]
    )
    for key, field in (
        ("Statement text", "text"),
        ("Learning Components", "lc"),
        ("Hierarchy", "hierarchy"),
    ):
        changed = copy.deepcopy(original)
        if field == "text":
            changed["sfis"][0]["context"]["description"]["text"] = "Longer shown text"
        elif field == "lc":
            changed["sfis"][0]["learning_components"][0]["description"][
                "text"
            ] = "Changed skill"
        else:
            changed["sfis"][0]["ancestors"][0]["description"]["text"] = "Changed parent"
        assert (
            presentation._evidence_parts(endpoint_uuids=["a"], payload=changed)[key]
            != before[key]
        )
    changes = presentation._differences(after={"a/b~c": None}, before={})
    assert changes == [
        {
            "path": "/a~1b~0c",
            "before_present": False,
            "after_present": True,
            "before": None,
            "after": None,
        }
    ]


def test_exact_evidence_differences_and_baseline_repeats(
    _saved: dict[str, Any],
) -> None:
    """Inspect every variant repeat without mislabeling it as baseline variability.

    Parameters
    ----------
    _saved
        Report with three repetitions per variant.
    """
    data = _saved["data"]
    judgments = {r["Ref"]: r for r in data["tables"]["Judgments"]}
    comparisons = {r["Ref"]: r for r in data["tables"]["Comparisons"]}
    variant_repeats = []
    for row in comparisons.values():
        first, second = [
            judgments[row[key]] for key in ("First judgment", "Second judgment")
        ]
        a, b = first["Saved request"], second["Saved request"]
        assert row["Payload identical"] == (
            json.loads(a["evidence"]["payload_json"])
            == json.loads(b["evidence"]["payload_json"])
        )
        assert row["Model-visible messages byte-identical"] == all(
            a["prompt"][key] == b["prompt"][key]
            for key in ("system_message", "user_message")
        )
        if row["Kind"] == "identical_repetition" and first["Role"] == "variant":
            variant_repeats.append(row["Ref"])
        for ref in row["Baseline repetition comparisons"]:
            baseline = comparisons[ref]
            for key in ("First judgment", "Second judgment"):
                r = judgments[baseline[key]]
                assert r["Role"] in ("base", "identical_repeat", "critique", "control")
                assert r["Presentation"] == "canonical"
                assert r["Pair"] == row["Pair"]
    assert variant_repeats
    linked = {
        x
        for row in comparisons.values()
        for x in row["Baseline repetition comparisons"]
    }
    assert linked.isdisjoint(variant_repeats)
    assert any(r["Baseline repetition comparisons"] for r in comparisons.values())
    for row in data["tables"]["Judgments"]:
        saved = row["Saved request"]
        assert row["Judge first endpoint"] == saved["evidence"]["endpoint_uuids"][0]
        assert row["Canonical A UUID"] == saved["canonical_endpoint_uuids"][0]
        assert row["Evidence hash"] == saved["evidence"]["material_content_hash"]
    assert any(
        r["Judge first endpoint"] != r["Canonical A UUID"] for r in judgments.values()
    )


def test_fresh_import_orders() -> None:
    """Load both module orders in fresh processes to expose circular imports."""
    for modules in (("scoring", "presentation"), ("presentation", "scoring")):
        code = "; ".join(f"import kgfeg.evals.lp_eval.{name}" for name in modules)
        result = subprocess.run(
            args=[sys.executable, "-B", "-c", code],
            capture_output=True,
            check=False,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_output_boundary_and_tamper_rejection(
    _saved: dict[str, Any], tmp_path: Path
) -> None:
    """Reject input aliases and altered derivatives without touching saved evidence.

    Parameters
    ----------
    _saved
        Authenticated report material.
    tmp_path
        Disposable derivative and symlink storage.
    """
    source = _saved["partial"].directory
    before = _bytes(_saved["root"])
    alias = tmp_path / "alias"
    alias.symlink_to(target=source, target_is_directory=True)
    for output in (
        source,
        source / "nested",
        source.parent,
        _saved["root"] / "sources",
        alias,
    ):
        with pytest.raises(ValueError):
            presentation.render_evaluation_presentations(
                output_directory=output, report_directory=source
            )
    destination = tmp_path / "tamper"
    presentation.render_evaluation_presentations(
        output_directory=destination, report_directory=source
    )
    (destination / "report.html").write_text("tampered derivative")
    with pytest.raises(ValueError, match="bytes differ"):
        presentation.render_evaluation_presentations(
            output_directory=destination, report_directory=source
        )
    assert (destination / "report.html").read_text() == "tampered derivative"
    assert _bytes(_saved["root"]) == before


# Each chart family has an independent counting oracle in this reconciliation.
# pylint: disable-next=too-many-locals,too-many-branches,too-complex
def test_panel_outcomes_and_canonical_directions(_saved: dict[str, Any]) -> None:
    """Recompute decisions, grounding, diagnostics and controls from raw assessments.

    Parameters
    ----------
    _saved
        Partial report with varied valid assessments and repeat overrides.
    """
    data = _saved["data"]
    by_ref = {r["Ref"]: r for r in data["tables"]["Judgments"]}
    raw = {r["request_id"]: r for r in data["report"]["assessments"]}
    flipped = 0
    for row in by_ref.values():
        item = raw[row["Request ID"]]
        if not item["assessment"] or item["task"] != "classification":
            continue
        direction = item["displayed_assessment"]["direction"]
        if direction is not None:
            if row["Judge first endpoint"] != row["Canonical A UUID"]:
                direction = {
                    "first_to_second": "second_to_first",
                    "second_to_first": "first_to_second",
                }[direction]
                flipped += 1
            assert item["assessment"]["direction"] == direction
            expected = (
                "buildsTowards: A → B"
                if direction == "first_to_second"
                else "buildsTowards: B → A"
            )
            assert row["Evaluator outcome"] == expected
    assert flipped > 0
    for view_index in (1, 2):
        for panel in data["views"][view_index]["panels"]:
            for row in panel["rows"]:
                for column, cell in zip(panel["columns"], row["cells"], strict=True):
                    assert all(
                        by_ref[ref]["Evaluator outcome"] == column
                        for ref in cell["refs"]
                    )
                    assert sum(c["count"] for c in row["cells"]) == cell["denominator"]
    for panel in data["views"][4]["panels"]:
        for row in panel["rows"]:
            for tag, cell in zip(panel["columns"], row["cells"], strict=True):
                assert all(tag in by_ref[ref]["Tags"] for ref in cell["planned_refs"])
                assert all(
                    by_ref[ref]["Evaluator outcome"] != "Unavailable"
                    for ref in cell["denominator_refs"]
                )
                for ref in cell["refs"]:
                    item = by_ref[ref]
                    if item["Task"] == "classification":
                        assert item["Production outcome"] != "Never nominated"
                        assert item["Production outcome"] != item["Evaluator outcome"]
    consistency = data["views"][5]["panels"][-1]
    for row in consistency["rows"]:
        for family, cell in zip(consistency["columns"], row["cells"], strict=True):
            cases: dict[str, list[dict[str, Any]]] = {}
            for item in by_ref.values():
                if (
                    item["Component"] == "controls"
                    and item["Curriculum"] == row["curriculum"]
                    and item["Control family"] == family
                ):
                    cases.setdefault(item["Pair"], []).append(item)
            eligible = [
                rs
                for rs in cases.values()
                if len(rs) > 1
                and all(r["Evaluator outcome"] != "Unavailable" for r in rs)
            ]
            stable = [
                rs for rs in eligible if len({r["Evaluator outcome"] for r in rs}) == 1
            ]
            assert cell["denominator"] == len(eligible)
            assert cell["count"] == len(stable)


def test_reuse_hashes_and_embedded_data(_saved: dict[str, Any]) -> None:
    """Verify exact reuse, hashes, compressed HTML and source preservation.

    Parameters
    ----------
    _saved
        Isolated source and derivative files.
    """
    destination = _saved["destination"]
    before = _bytes(_saved["root"])
    presentation.render_evaluation_presentations(
        output_directory=destination, report_directory=_saved["partial"].directory
    )
    assert _bytes(_saved["root"]) == before
    manifest = json.loads((destination / "presentation_manifest.json").read_text())
    for name, fingerprint in manifest["outputs"].items():
        payload = (destination / name).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == fingerprint["sha256"]
        assert len(payload) == fingerprint["size_bytes"]
    assert "renderer_sources" not in manifest
    html = (destination / "report.html").read_text()
    match = re.search(r"[A-Za-z0-9+/]{100,}={0,2}", html)
    assert match is not None
    encoded = match.group()
    assert json.loads(gzip.decompress(base64.b64decode(encoded))) == _saved["data"]
    assert "<script src=" not in html
    assert "slice').onchange=()=>{membership=null;page=0;draw()}" in html
    assert "curriculum').onchange=()=>{membership=null;page=0;draw()}" in html


def test_saved_validation_rejects_bad_membership_and_mapping(
    _saved: dict[str, Any],
) -> None:
    """Challenge source validation before altered rows can become displayed results.

    Parameters
    ----------
    _saved
        Saved requests and validated assessments.
    """
    data = _saved["data"]
    report = data["report"]
    requests = [r["Saved request"] for r in data["tables"]["Judgments"]]
    for mode in ("duplicate", "payload", "count", "canonical"):
        changed_report, changed_requests = copy.deepcopy((report, requests))
        if mode == "duplicate":
            changed_requests.append(changed_requests[0])
        elif mode == "payload":
            changed_requests[0]["evidence"]["payload_json"] = "{}"
        elif mode == "count":
            changed_report["valid_judgments"] += 1
        else:
            row = next(
                r
                for r in changed_report["assessments"]
                if r["assessment"] and r["task"] == "classification"
            )
            row["assessment"]["decision"] = "different_decision"
        with pytest.raises(ValueError):
            presentation._validate_requests(
                report=changed_report, requests=changed_requests
            )


def test_source_byte_tamper_fails_before_publication(
    _saved: dict[str, Any], tmp_path: Path
) -> None:
    """Reject a corrupted test-owned report and restore its original bytes afterward.

    Parameters
    ----------
    _saved
        Synthetic source report, never a real invocation.
    tmp_path
        Uncreated destination used to detect premature publication.
    """
    path = _saved["empty"].directory / "lp_eval_report.json"
    original = path.read_bytes()
    try:
        path.write_bytes(original + b" ")
        with pytest.raises(ValueError, match="bytes differ"):
            presentation.render_evaluation_presentations(
                output_directory=tmp_path / "should-not-exist",
                report_directory=path.parent,
            )
        assert not (tmp_path / "should-not-exist").exists()
    finally:
        path.write_bytes(original)


def test_workbook_structure_and_literal_text(_saved: dict[str, Any]) -> None:
    """Check all tabs, frozen headers, filters, literal values and original identifiers.

    Parameters
    ----------
    _saved
        Generated workbook and unabridged records.
    """
    namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    payload = (
        _saved["destination"] / "learning_progressions_evaluation.xlsx"
    ).read_bytes()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        book = ET.fromstring(archive.read("xl/workbook.xml"))
        names = [e.attrib["name"] for e in book.findall("s:sheets/s:sheet", namespace)]
        assert names[0] == "Overview"
        assert set(names) == set(_saved["data"]["tables"])
        strings = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        texts = ["".join(e.itertext()) for e in strings]
        assert "Personal notes (not a disposition)" in texts
        for row in _saved["data"]["tables"]["Judgments"]:
            assert row["Request ID"] in texts
            assert row["Canonical A UUID"] in texts
        for name in archive.namelist():
            if re.fullmatch(r"xl/worksheets/sheet\d+.xml", name):
                sheet = ET.fromstring(archive.read(name))
                assert sheet.find("s:autoFilter", namespace) is not None
                pane = sheet.find("s:sheetViews/s:sheetView/s:pane", namespace)
                assert pane is not None
                assert pane.attrib["state"] == "frozen"
                assert pane.attrib["ySplit"] == "1"
                assert not sheet.findall(".//s:f", namespace)
        assert not any(
            "vbaProject" in name or "connections" in name for name in archive.namelist()
        )
    literal = presentation.render_workbook(
        {"Literal check": [{"Text": "=1+1", "ID": "001234"}]}
    )
    with zipfile.ZipFile(io.BytesIO(literal)) as archive:
        assert b"<f>" not in archive.read("xl/worksheets/sheet1.xml")
        assert b"=1+1" in archive.read("xl/sharedStrings.xml")
        assert b"001234" in archive.read("xl/sharedStrings.xml")
