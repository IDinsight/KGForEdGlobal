"""Red-team relation-specific LP participation and deterministic eligibility artifacts."""

# Future Library
from __future__ import annotations

# Standard Library
import json
import socket

from collections import Counter
from copy import deepcopy
from hashlib import sha256
from operator import itemgetter
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

# Third Party Library
import pytest

# Package Library
from kgfeg.kgs.lp_coordinates import build_lp_coordinate_index
from kgfeg.kgs.lp_index import build_lp_graph_index
from kgfeg.kgs.lp_selection import (
    LPSelectionReport,
    LPSFIEligibility,
    build_lp_selection,
    select_lp_sfis,
)
from kgfeg.kgs.schemas import AcademicStandardsLCKGBundle, StandardsFrameworkItem
from kgfeg.kgs.utils import KGDirs
from kgfeg.schemas import CreateKGConfig
from tests.constants import PACKAGE_PATH, PARAM
from tests.fixtures.lp.loader import LP_FIXTURES_DIR, load_lp_regression_fixture

_COMMON = {
    "academic_subject": "Synthetic subject",
    "attribution_statement": "Synthetic attribution",
    "author": "Synthetic authority",
    "in_language": "en",
    "license": "Synthetic license",
    "provider": "Synthetic provider",
}
_FRAMEWORK_UUID = UUID("00000000-0000-4000-8000-000000000100")
_POLICIES = ("exclude_unresolved", "include_unresolved_with_warnings")
_PROFILES = {
    "ghana_english": (
        "examples/ghana/config_english_curriculum.json",
        "Grade",
        ("BASIC 1", "BASIC 2", "BASIC 3"),
        ("Content Standard", "Indicator"),
        ("Grade", "Strand", "Sub-Strand"),
    ),
    "ghana_math": (
        "examples/ghana/config_math_curriculum.json",
        "Grade",
        ("BASIC 4", "BASIC 5", "BASIC 6"),
        ("Content Standard", "Indicator"),
        ("Grade", "Strand", "Sub-Strand"),
    ),
    "madhi_math": (
        "examples/india/madhi/config_math.json",
        "Class",
        ("Class-1", "Class-2", "Class-3", "Class-4", "Class-5"),
        ("Content",),
        ("Curricular Goal", "Competency", "Class"),
    ),
    "nigeria_math": (
        "examples/nigeria/config_math_curriculum_1_3.json",
        "Grade",
        ("PRIMARY ONE", "PRIMARY TWO", "PRIMARY THREE"),
        ("Performance Objective",),
        ("Grade", "Theme", "Sub-Theme", "Topic"),
    ),
    "pratham_science": (
        "examples/india/pratham/config_science.json",
        "Class",
        ("Class IX", "Class X"),
        (
            "NCERT Learning Outcome",
            "Content Domain Specific Learning Outcome",
            "Indicator",
        ),
        ("Class", "Content Domain", "Chapter"),
    ),
    "rwanda_math": (
        "examples/rwanda/config_math_curriculum_p1_p3.json",
        "Grade",
        ("P1", "P2", "P3"),
        (
            "Grade Key Competence",
            "Key Unit Competence",
            "Knowledge Objective",
            "Skills Objective",
            "Attitudes and Values Objective",
        ),
        ("Grade", "Topic Area", "Sub-Topic Area", "Unit"),
    ),
}
_RELATIONS = {"buildsTowards", "relatesTo"}


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject network connections during deterministic selection tests.

    Parameters
    ----------
    monkeypatch
        Fixture that restores socket behavior after each test.
    """

    def _reject(*args: Any, **kwargs: Any) -> None:
        """Fail any attempted external connection.

        Parameters
        ----------
        args
            Positional connection arguments.
        kwargs
            Named connection arguments.

        Raises
        ------
        AssertionError
            Whenever a connection is attempted.
        """

        raise AssertionError("Eligibility tests must not access the network.")

    monkeypatch.setattr(name="connect", target=socket.socket, value=_reject)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=_reject)
    monkeypatch.setattr(name="create_connection", target=socket, value=_reject)


def _bundle(
    *,
    components: tuple[dict[str, Any], ...] = (),
    edges: tuple[dict[str, Any], ...] = (),
    framework_uuid: UUID = _FRAMEWORK_UUID,
    items: tuple[StandardsFrameworkItem, ...],
    supports: tuple[dict[str, Any], ...] = (),
) -> AcademicStandardsLCKGBundle:
    """Create complete synthetic upstream inputs without preselecting SFIs.

    Parameters
    ----------
    components
        Optional complete Learning Component payloads.
    edges
        Positive or fallback hierarchy relationships.
    framework_uuid
        Framework identifier, including the reduced fixture's identifier.
    items
        Final SFIs with exact metadata under test.
    supports
        Optional complete LC support payloads.

    Returns
    -------
    AcademicStandardsLCKGBundle
        Schema-valid bundle with complete roots and reconciled population counts.
    """

    hierarchy = list(edges)
    attached = {str(edge["target_entity_value"]) for edge in hierarchy}
    for item in items:
        if str(item.case_identifier_uuid) not in attached:
            hierarchy.append(
                _edge(
                    source=framework_uuid,
                    source_entity="StandardsFramework",
                    target=item.case_identifier_uuid,
                )
            )
    unresolved = [
        deepcopy(edge)
        for edge in hierarchy
        if edge.get("metadata", {}).get("unresolved_root_fallback") is True
    ]
    supported = {str(edge["target_entity_value"]) for edge in supports}
    lc_summary: dict[str, Any] = {
        name: 0
        for name in (
            "lc_dedup_candidate_pair_count",
            "lc_dedup_conflict_count",
            "lc_dedup_judged_same_count",
            "lc_generation_failed_sfis_count",
            "lc_multi_claim_lc_count",
            "llm_request_count",
            "llm_response_count",
            "total_lc_source_sfis_empty_text",
        )
    }
    lc_summary.update(
        lc_max_splits_observed=1 if components else 0,
        lc_multi_parent_lc_count=sum(
            len(component["metadata"]["source_sfi_uuids"]) > 1
            for component in components
        ),
        lc_selection_mode="explicit_allowlist",
        total_lc_claims=len(supports),
        total_lc_source_sfis_considered=len(items),
        total_lc_source_sfis_eligible=len(supported),
        total_lc_source_sfis_excluded=len(items) - len(supported),
        total_lcs=len(components),
        total_supports_edges=len(supports),
    )
    return AcademicStandardsLCKGBundle.model_validate(
        {
            "entity_provenance": {
                "framework": {"doc_key": "synthetic-selection-document"},
                "items": {
                    str(item.case_identifier_uuid): {
                        "audit": {"flags": ["source-code-anomaly", "merged-source"]},
                        "source": deepcopy(item.metadata),
                        "source_pages": [9, 2],
                    }
                    for item in items
                },
                "kg_run_manifest": {"run": "synthetic-local-test"},
                "learning_components": {
                    str(component["identifier"]): deepcopy(component["metadata"])
                    for component in components
                },
                "relationships_has_child": {
                    str(edge["identifier"]): deepcopy(edge.get("metadata", {}))
                    for edge in hierarchy
                },
            },
            "framework": {
                **_COMMON,
                "adoption_status": "Adopted",
                "case_identifier_uri": f"urn:uuid:{framework_uuid}",
                "case_identifier_uuid": framework_uuid,
                "identifier": framework_uuid,
                "jurisdiction": "Synthetic jurisdiction",
                "metadata": {"doc_key": "synthetic-selection-document"},
                "name": "Synthetic framework",
            },
            "items": items,
            "learning_components": components,
            "relationships_has_child": hierarchy,
            "relationships_supports": supports,
            "summary": {
                "academic_standards": {
                    "final_sfi_count": len(items),
                    "framework_count": 1,
                    "has_child_relationship_count": len(hierarchy),
                    "learning_commons_node_count": 1 + len(items),
                    "learning_commons_relationship_count": len(hierarchy),
                    "learning_commons_unresolved_fallback_relationship_count": len(
                        unresolved
                    ),
                    "relationship_unresolved_edge_count": len(unresolved),
                },
                "learning_components": lc_summary,
                "total_node_count": 1 + len(items) + len(components),
                "total_relationship_count": len(hierarchy) + len(supports),
            },
            "unresolved_items": {
                "academic_standards": {"relationship_unresolved_edges": unresolved},
                "learning_components": {},
            },
            "validation_report": {
                "learning_commons_export_schema_version": "synthetic-test",
                "passed": True,
            },
        }
    )


def _config(profile: str = "nigeria_math") -> CreateKGConfig:
    """Read and cross-validate a real profile without executing its pipeline.

    Parameters
    ----------
    profile
        Name of the profile pinned in the independent policy table.

    Returns
    -------
    CreateKGConfig
        Complete validated KG configuration.
    """

    return CreateKGConfig.model_validate(
        json.loads((PACKAGE_PATH / _PROFILES[profile][0]).read_text())["kgs"]
    )


def _edge(
    *,
    fallback: bool = False,
    source: UUID,
    source_entity: str = "StandardsFrameworkItem",
    target: UUID,
) -> dict[str, Any]:
    """Build a synthetic hierarchy edge with independently distinguishable identity.

    Parameters
    ----------
    fallback
        Whether the framework attachment is unresolved.
    source
        Source CASE UUID.
    source_entity
        Framework or SFI entity type.
    target
        Target SFI CASE UUID.

    Returns
    -------
    dict[str, Any]
        Complete relationship input including the explicit fallback marker.
    """

    return {
        **{
            key: value
            for key, value in _COMMON.items()
            if key not in {"academic_subject", "in_language"}
        },
        "identifier": uuid5(
            namespace=NAMESPACE_URL, name=f"selection-test:{source}:{target}"
        ),
        "metadata": {"unresolved_root_fallback": fallback},
        "relationship_type": "hasChild",
        "source_entity": source_entity,
        "source_entity_key": "case_identifier_uuid",
        "source_entity_value": str(source),
        "target_entity": "StandardsFrameworkItem",
        "target_entity_key": "case_identifier_uuid",
        "target_entity_value": str(target),
    }


def _fixture_bundle(profile: str) -> AcademicStandardsLCKGBundle:
    """Promote an approved reduced projection while retaining its structural evidence.

    Parameters
    ----------
    profile
        Reduced fixture and matching profile name.

    Returns
    -------
    AcademicStandardsLCKGBundle
        Full schema envelope with synthetic required attribution fields.
    """

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / f"{profile}.json")
    edge_common = {
        key: value
        for key, value in _COMMON.items()
        if key not in {"academic_subject", "in_language"}
    }
    return _bundle(
        components=tuple(
            {**_COMMON, **component.model_dump(mode="json")}
            for component in fixture.learning_components
        ),
        edges=tuple(
            {**edge_common, **edge.model_dump(mode="json")}
            for edge in fixture.relationships_has_child
        ),
        framework_uuid=fixture.framework.case_identifier_uuid,
        items=tuple(
            _item(
                metadata=item.metadata,
                normalized_statement_type=item.normalized_statement_type,
                number=item.case_identifier_uuid.int,
                statement_type=item.statement_type,
            ).model_copy(
                update={
                    "description": item.description,
                    "statement_code": item.statement_code,
                }
            )
            for item in fixture.items
        ),
        supports=tuple(
            {**edge_common, **edge.model_dump(mode="json")}
            for edge in fixture.relationships_supports
        ),
    )


def _hash(payload: Any) -> str:
    """Independently hash material JSON using explicit stable UTF-8 encoding.

    Parameters
    ----------
    payload
        Parsed JSON material rather than production summary fields.

    Returns
    -------
    str
        SHA-256 digest of the canonical material.
    """

    return sha256(
        json.dumps(
            allow_nan=False,
            ensure_ascii=False,
            obj=payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _item(
    *,
    metadata: dict[str, Any] | None = None,
    normalized_statement_type: str = "Standard",
    number: int = 1,
    statement_type: str = "Performance Objective",
) -> StandardsFrameworkItem:
    """Create an SFI whose CASE key differs from its other identifier.

    Parameters
    ----------
    metadata
        Unfiltered metadata, defaulting to one valid coordinate.
    normalized_statement_type
        Exported normalized type, independent of the local statement type.
    number
        Deterministic CASE UUID suffix.
    statement_type
        Local AS type whose LP permission must come from configuration.

    Returns
    -------
    StandardsFrameworkItem
        Authoritative node with deliberately misleading display grade and code.
    """

    case_uuid = UUID(int=number)
    return StandardsFrameworkItem.model_validate(
        {
            **_COMMON,
            "case_identifier_uri": f"urn:uuid:{case_uuid}",
            "case_identifier_uuid": case_uuid,
            "description": "A synthetic skill; display PRIMARY THREE / Grade 12.",
            "grade_level": ["12"],
            "identifier": UUID(int=number + 2**112),
            "jurisdiction": "Synthetic jurisdiction",
            "metadata": deepcopy(
                metadata
                if metadata is not None
                else {"identity_scope_values": {"Grade": "PRIMARY ONE"}}
            ),
            "normalized_statement_type": normalized_statement_type,
            "statement_code": "PRIMARY THREE.99",
            "statement_type": statement_type,
        }
    )


def _reconcile(report: LPSelectionReport) -> None:
    """Recount every report population directly from individual SFI decisions.

    Parameters
    ----------
    report
        Selection report whose aggregate counts must match its complete rows.
    """

    rows = report.sfis
    eligible = [row for row in rows if row.eligibility_reasons]
    unresolved = [row for row in rows if row.unresolved_ancestry]
    assert report.total_sfis_considered == len(rows)
    assert report.total_sfis_eligible == len(eligible)
    assert report.total_sfis_excluded == len(rows) - len(eligible)
    assert report.unresolved_sfis_considered == len(unresolved)
    assert report.unresolved_sfis_eligible == sum(
        bool(row.eligibility_reasons) for row in unresolved
    )
    assert report.unresolved_sfis_excluded == sum(
        not row.eligibility_reasons for row in unresolved
    )
    assert report.unresolved_sfis_policy_excluded == sum(
        "unresolved_context_excluded" in row.exclusion_reasons.values() for row in rows
    )
    assert [row.sfi.case_identifier_uuid for row in rows] == sorted(
        {row.sfi.case_identifier_uuid for row in rows}, key=str
    )
    for row in rows:
        assert set(row.eligibility_reasons).isdisjoint(row.exclusion_reasons)
        assert set(row.eligibility_reasons) | set(row.exclusion_reasons) == _RELATIONS
        assert row.coordinate.sfi_uuid == row.sfi.case_identifier_uuid
    for relation in sorted(_RELATIONS):
        assert report.eligible_sfis_per_relationship[relation] == sum(
            relation in row.eligibility_reasons for row in rows
        )
        assert report.exclusion_reason_counts_per_relationship[relation] == dict(
            Counter(
                row.exclusion_reasons[relation]
                for row in rows
                if relation in row.exclusion_reasons
            )
        )


def _reverse_mapping_order(value: Any) -> Any:
    """Reverse mapping encounter order without reordering meaningful list material.

    Parameters
    ----------
    value
        JSON-compatible payload to permute.

    Returns
    -------
    Any
        Semantically identical payload with reversed mapping insertion order.
    """

    if isinstance(value, dict):
        return {
            key: _reverse_mapping_order(child)
            for key, child in reversed(list(value.items()))
        }
    if isinstance(value, list):
        return [_reverse_mapping_order(child) for child in value]
    return value


@PARAM(argnames="profile", argvalues=tuple(_PROFILES))
def test_aliases_preserve_canonical_participation_and_raw_sfi(profile: str) -> None:
    """AS type aliases retain raw source labels and canonical LP participation.

    Parameters
    ----------
    profile
        Reviewed curriculum whose complete alias set is exercised.
    """

    config = _config(profile)
    _, axis, values, included, _ = _PROFILES[profile]
    expected = {}
    items: list[StandardsFrameworkItem] = []
    for policy in config.academic_standards.statement_type_policy:
        for label in (policy.statement_type, *policy.aliases):
            item = _item(
                metadata={"identity_scope_values": {axis: values[-1]}},
                number=len(items) + 1,
                statement_type=f" {label.swapcase()} ",
            )
            items.append(item)
            expected[item.case_identifier_uuid] = (
                policy.statement_type,
                _RELATIONS if policy.statement_type in included else set(),
            )
    report = build_lp_selection(
        as_lc_bundle=_bundle(items=tuple(items)), kg_config=config
    )
    for row in report.sfis:
        canonical, permitted = expected[row.sfi.case_identifier_uuid]
        assert row.statement_type == canonical
        assert set(row.eligibility_reasons) == permitted
        assert row.sfi == items[row.sfi.case_identifier_uuid.int - 1]
        assert row.coordinate.rank == len(values) - 1
    _reconcile(report)


@PARAM(argnames="profile", argvalues=tuple(_PROFILES))
def test_artifacts_round_trip_counts_hashes_and_upstream_preservation(
    *, profile: str, tmp_path: Path
) -> None:
    """Both artifacts preserve six-fixture context and reconcile independent hashes.

    Parameters
    ----------
    profile
        Approved reduced curriculum fixture.
    tmp_path
        Isolated directory that receives only local test artifacts.
    """

    bundle = _fixture_bundle(profile)
    config = _config(profile)
    before_bundle = bundle.model_dump(mode="json")
    before_config = config.model_dump(mode="json")
    root = tmp_path / "nested" / profile
    root.mkdir(parents=True)
    sentinel = root / "as_lc_kg_bundle.json"
    sentinel.write_bytes(b"upstream artifact must stay byte-identical\n")
    eligible, report = select_lp_sfis(
        as_lc_bundle=bundle, kg_config=config, kg_dirs=KGDirs(root=root)
    )
    saved_eligible = json.loads((root / "lp_eligible_sfis.json").read_text())
    saved_report = json.loads((root / "lp_eligibility_report.json").read_text())
    assert [LPSFIEligibility.model_validate(row) for row in saved_eligible] == eligible
    assert LPSelectionReport.model_validate(saved_report) == report
    assert saved_eligible == [
        row.model_dump(mode="json") for row in report.eligible_sfis
    ]
    assert report.eligible_sfis_content_hash == _hash(saved_eligible)
    assert report.config_content_hash == _hash(before_config)
    hash_material = deepcopy(before_bundle)
    for field, key in (
        ("items", "case_identifier_uuid"),
        ("learning_components", "identifier"),
        ("relationships_has_child", "identifier"),
        ("relationships_supports", "identifier"),
    ):
        hash_material[field].sort(key=itemgetter(key))
    assert report.upstream_content_hash == _hash(hash_material)
    _, axis, values, included, _ = _PROFILES[profile]
    expected_ids = {
        item.case_identifier_uuid
        for item in bundle.items
        if item.statement_type in included
    }
    assert {row.sfi.case_identifier_uuid for row in eligible} == expected_ids
    for row in report.sfis:
        source = next(
            item
            for item in bundle.items
            if item.case_identifier_uuid == row.sfi.case_identifier_uuid
        )
        assert row.sfi == source
        assert (
            row.source_provenance
            == before_bundle["entity_provenance"]["items"][
                str(source.case_identifier_uuid)
            ]
        )
        coordinate = source.metadata["identity_scope_values"].get(axis)
        assert row.coordinate.canonical_value == coordinate
        assert row.coordinate.rank == (values.index(coordinate) if coordinate else None)
        if row.unresolved_ancestry:
            assert any(
                "unresolved self or ancestry" in warning for warning in row.warnings
            )
            assert any(
                "must not be used as positive" in warning for warning in row.warnings
            )
    if profile == "pratham_science":
        assert max(len(row.parent_sfi_uuids) for row in report.sfis) == 2
        assert {row.statement_type for row in eligible} == set(included)
    if profile == "madhi_math":
        assert all(row.statement_type != "Class" for row in report.sfis)
    if profile == "ghana_math":
        assert report.unresolved_sfis_eligible > 0
    _reconcile(report)
    assert bundle.model_dump(mode="json") == before_bundle
    assert config.model_dump(mode="json") == before_config
    assert sentinel.read_bytes() == b"upstream artifact must stay byte-identical\n"
    assert {path.name for path in root.iterdir()} == {
        "as_lc_kg_bundle.json",
        "lp_eligible_sfis.json",
        "lp_eligibility_report.json",
    }


@PARAM(
    argnames="change",
    argvalues=("as_alias", "budget", "lc_policy", "matrix", "order", "unresolved"),
)
def test_config_material_changes_invalidate_hash(change: str) -> None:
    """Actual policy material changes the digest even when selection stays identical.

    Parameters
    ----------
    change
        One independently variable, schema-validated KG configuration input.
    """

    bundle = _bundle(items=(_item(),))
    original = _config()
    baseline = build_lp_selection(as_lc_bundle=bundle, kg_config=original)
    payload = original.model_dump(mode="json")
    if change == "as_alias":
        payload["as"]["statement_type_policy"][-1]["aliases"].append(
            "Synthetic new alias"
        )
    elif change == "budget":
        payload["lp"]["candidate_policy"]["budgets"]["max_total_candidates"] += 1
    elif change == "lc_policy":
        payload["lc"]["lc_source_statement_types"] = ["Topic"]
    elif change == "matrix":
        payload["lp"]["relates_to"]["allowed_statement_type_pairs"] = [
            {"first_statement_type": "Topic", "second_statement_type": "Topic"}
        ]
    elif change == "order":
        payload["lp"]["developmental_coordinate"]["ordered_values"].reverse()
    else:
        payload["lp"]["unresolved_participation"] = "exclude_unresolved"
    config = CreateKGConfig.model_validate(payload)
    updated = build_lp_selection(as_lc_bundle=bundle, kg_config=config)
    assert updated.config_content_hash != baseline.config_content_hash
    assert updated.config_content_hash == _hash(config.model_dump(mode="json"))
    assert updated.upstream_content_hash == baseline.upstream_content_hash
    if change in {"as_alias", "budget", "lc_policy", "unresolved"}:
        assert updated.eligible_sfis_content_hash == baseline.eligible_sfis_content_hash
    else:
        assert updated.eligible_sfis_content_hash != baseline.eligible_sfis_content_hash


@PARAM(argnames="missing", argvalues=(False, True))
@PARAM(argnames="policy", argvalues=_POLICIES)
def test_dag_unresolved_propagation_precedence_and_coordinate_only_boundary(
    *, missing: bool, policy: str
) -> None:
    """One unresolved DAG branch warns every descendant despite a clean second parent.

    Parameters
    ----------
    missing
        Whether the shared child lacks its canonical coordinate.
    policy
        Profile-wide unresolved participation state.
    """

    config_payload = _config().model_dump(mode="json")
    config_payload["lp"]["unresolved_participation"] = policy
    config = CreateKGConfig.model_validate(config_payload)
    items = (
        _item(number=1),
        _item(metadata={} if missing else None, number=2),
        _item(number=3),
        _item(number=4, statement_type="Topic"),
        _item(number=5, statement_type="Topic"),
    )
    fallback = _edge(
        fallback=True,
        source=_FRAMEWORK_UUID,
        source_entity="StandardsFramework",
        target=UUID(int=5),
    )
    bundle = _bundle(
        edges=(
            _edge(source=UUID(int=1), target=UUID(int=2)),
            _edge(source=UUID(int=5), target=UUID(int=2)),
            _edge(source=UUID(int=2), target=UUID(int=3)),
            fallback,
        ),
        items=items,
    )
    graph = build_lp_graph_index(bundle)
    coordinates = build_lp_coordinate_index(graph_index=graph, kg_config=config)
    report = build_lp_selection(as_lc_bundle=bundle, kg_config=config)
    records = {row.sfi.case_identifier_uuid.int: row for row in report.sfis}
    assert {key for key, row in records.items() if row.unresolved_ancestry} == {2, 3, 5}
    assert records[2].parent_sfi_uuids == (UUID(int=1), UUID(int=5))
    assert records[5].parent_sfi_uuids == ()
    assert records[5].root_fallback_relationship_uuids == (fallback["identifier"],)
    assert records[2].root_fallback_relationship_uuids == ()
    assert UUID(int=5) not in graph.framework_child_sfi_uuids
    assert set(records[1].eligibility_reasons) == _RELATIONS
    assert records[1].warnings == ()
    assert records[4].exclusion_reasons == dict.fromkeys(
        sorted(_RELATIONS), "statement_type_not_configured"
    )
    for number in (2, 3, 5):
        assert str(UUID(int=number)) in records[number].warnings[0]
        assert "unresolved self or ancestry" in records[number].warnings[0]
        assert "must not be used as positive" in records[number].warnings[0]
    if policy == "exclude_unresolved":
        for number in (2, 3, 5):
            assert records[number].eligibility_reasons == {}
            assert records[number].exclusion_reasons == dict.fromkeys(
                sorted(_RELATIONS), "unresolved_context_excluded"
            )
        assert report.eligible_sfis_per_relationship == {
            "buildsTowards": 1,
            "relatesTo": 1,
        }
        assert report.unresolved_sfis_policy_excluded == 3
        assert report.unresolved_sfis_eligible == 0
        assert report.total_sfis_eligible == 1
    else:
        assert set(records[2].eligibility_reasons) == (
            {"relatesTo"} if missing else _RELATIONS
        )
        assert records[2].exclusion_reasons == (
            {"buildsTowards": "missing_coordinate"} if missing else {}
        )
        assert set(records[3].eligibility_reasons) == _RELATIONS
        assert records[5].exclusion_reasons == dict.fromkeys(
            sorted(_RELATIONS), "statement_type_not_configured"
        )
        assert report.eligible_sfis_per_relationship == {
            "buildsTowards": 2 if missing else 3,
            "relatesTo": 3,
        }
        assert report.unresolved_sfis_policy_excluded == 0
        assert report.unresolved_sfis_eligible == 2
        assert report.total_sfis_eligible == 3
    assert len(records[2].warnings) == (2 if missing else 1)
    if missing:
        assert records[2].coordinate.rank is None
        assert "Missing rank is not positive evidence" in records[2].warnings[1]
    assert coordinates.allows_relates_to(
        first_sfi_uuid=UUID(int=5), second_sfi_uuid=UUID(int=2)
    )
    assert coordinates.allows_builds_towards(
        source_sfi_uuid=UUID(int=5), target_sfi_uuid=UUID(int=2)
    ) is (not missing)
    _reconcile(report)


@PARAM(
    argnames="shape",
    argvalues=("empty_bundle", "omitted", "unresolved"),
)
def test_empty_selections_write_complete_zero_artifacts(
    *, shape: str, tmp_path: Path
) -> None:
    """Zero eligible SFIs is a successful selection outcome with exact exclusions.

    Parameters
    ----------
    shape
        Distinct empty-population or policy-exclusion boundary.
    tmp_path
        Isolated output root.
    """

    payload = _config().model_dump(mode="json")
    items = (
        ()
        if shape == "empty_bundle"
        else (
            _item(
                statement_type=(
                    "Topic" if shape == "omitted" else "Performance Objective"
                )
            ),
        )
    )
    edges: tuple[dict[str, Any], ...] = ()
    if shape == "unresolved":
        payload["lp"]["unresolved_participation"] = "exclude_unresolved"
        edges = (
            _edge(
                fallback=True,
                source=_FRAMEWORK_UUID,
                source_entity="StandardsFramework",
                target=UUID(int=1),
            ),
        )
    eligible, report = select_lp_sfis(
        as_lc_bundle=_bundle(edges=edges, items=items),
        kg_config=CreateKGConfig.model_validate(payload),
        kg_dirs=KGDirs(root=tmp_path / shape),
    )
    assert eligible == []
    assert report.total_sfis_considered == len(items)
    assert report.total_sfis_excluded == len(items)
    assert report.eligible_sfis_per_relationship == {"buildsTowards": 0, "relatesTo": 0}
    assert json.loads((tmp_path / shape / "lp_eligible_sfis.json").read_text()) == []
    assert report.eligible_sfis_content_hash == sha256(b"[]").hexdigest()
    assert (
        LPSelectionReport.model_validate_json(
            (tmp_path / shape / "lp_eligibility_report.json").read_text()
        )
        == report
    )
    _reconcile(report)


@PARAM(argnames="profile", argvalues=tuple(_PROFILES))
@PARAM(argnames="unresolved", argvalues=(False, True))
def test_exact_six_profile_participation_all_grains_and_groupings(
    *, profile: str, unresolved: bool
) -> None:
    """Closed-world matrices include every approved grain and exclude every omitted type.

    Parameters
    ----------
    profile
        Profile with an independently pinned complete matrix and coordinate order.
    unresolved
        Whether every SFI is attached through an unresolved framework fallback.
    """

    config = _config(profile)
    _, axis, values, included, excluded = _PROFILES[profile]
    assert (
        config.learning_progressions.unresolved_participation
        == "include_unresolved_with_warnings"
    )
    assert {
        (pair.source_statement_type, pair.target_statement_type)
        for pair in config.learning_progressions.builds_towards.allowed_statement_type_pairs
    } == {(name, name) for name in included}
    assert {
        (pair.first_statement_type, pair.second_statement_type)
        for pair in config.learning_progressions.relates_to.allowed_statement_type_pairs
    } == {(name, name) for name in included}
    items = tuple(
        _item(
            metadata={"identity_scope_values": {axis: value}},
            number=number + 1,
            statement_type=name,
        )
        for number, (name, value) in enumerate(
            (name, value) for name in (*included, *excluded) for value in values
        )
    )
    edges = (
        tuple(
            _edge(
                fallback=True,
                source=_FRAMEWORK_UUID,
                source_entity="StandardsFramework",
                target=item.case_identifier_uuid,
            )
            for item in items
        )
        if unresolved
        else ()
    )
    report = build_lp_selection(
        as_lc_bundle=_bundle(edges=edges, items=items), kg_config=config
    )
    for row in report.sfis:
        assert set(row.eligibility_reasons) == (
            _RELATIONS if row.statement_type in included else set()
        )
        assert row.unresolved_ancestry is unresolved
        assert bool(row.warnings) is unresolved
        assert row.parent_sfi_uuids == ()
        assert row.coordinate.rank == values.index(row.coordinate.canonical_value)
    expected_eligible = len(included) * len(values)
    expected_excluded = len(excluded) * len(values)
    assert report.total_sfis_eligible == expected_eligible
    assert report.total_sfis_excluded == expected_excluded
    assert report.eligible_sfis_per_relationship == dict.fromkeys(
        sorted(_RELATIONS), expected_eligible
    )
    assert report.exclusion_reason_counts_per_relationship == {
        relation: {"statement_type_not_configured": expected_excluded}
        for relation in sorted(_RELATIONS)
    }
    assert report.unresolved_sfis_eligible == (expected_eligible if unresolved else 0)
    assert report.unresolved_sfis_excluded == (expected_excluded if unresolved else 0)
    assert report.unresolved_sfis_policy_excluded == 0
    _reconcile(report)


def test_future_as_type_is_excluded_without_lp_permission() -> None:
    """Adding a valid AS Standard does not silently expand either LP matrix."""

    payload = _config().model_dump(mode="json")
    payload["as"]["statement_type_policy"].append(
        {
            "aliases": [],
            "description": "Synthetic future standard grain",
            "normalized_statement_type": "Standard",
            "statement_type": "Future Learning Target",
        }
    )
    payload["as"]["sfi_has_child_parent_policy"]["Future Learning Target"] = []
    config = CreateKGConfig.model_validate(payload)
    report = build_lp_selection(
        as_lc_bundle=_bundle(items=(_item(statement_type="Future Learning Target"),)),
        kg_config=config,
    )
    assert report.total_sfis_eligible == 0
    assert report.sfis[0].exclusion_reasons == dict.fromkeys(
        sorted(_RELATIONS), "statement_type_not_configured"
    )
    _reconcile(report)


@PARAM(argnames="existing", argvalues=(False, True))
@PARAM(
    argnames="metadata",
    argvalues=(
        {"identity_scope_values": {"Grade": "UNLISTED"}},
        {"identity_scope_values": {"Grade": ["PRIMARY ONE", "PRIMARY TWO"]}},
        {"identity_scope_values": {"Grade": "PRIMARY ONE", "Class": "PRIMARY TWO"}},
    ),
)
@PARAM(argnames="omitted", argvalues=(False, True))
@PARAM(argnames="policy", argvalues=_POLICIES)
def test_invalid_coordinate_fails_before_policy_or_artifact_writes(
    *,
    existing: bool,
    metadata: dict[str, Any],
    omitted: bool,
    policy: str,
    tmp_path: Path,
) -> None:
    """Invalid coordinates cannot hide behind omitted types or unresolved exclusion.

    Parameters
    ----------
    existing
        Whether valid-looking prior artifacts already exist.
    metadata
        Unknown, ambiguous, or conflicting canonical coordinate inputs.
    omitted
        Whether the invalid SFI's type is outside the LP matrices.
    policy
        Unresolved participation state.
    tmp_path
        Isolated output directory used to detect premature writes.
    """

    payload = _config().model_dump(mode="json")
    payload["lp"]["unresolved_participation"] = policy
    config = CreateKGConfig.model_validate(payload)
    invalid = _item(
        metadata=metadata,
        number=2,
        statement_type="Topic" if omitted else "Performance Objective",
    )
    bundle = _bundle(
        edges=(
            _edge(
                fallback=True,
                source=_FRAMEWORK_UUID,
                source_entity="StandardsFramework",
                target=invalid.case_identifier_uuid,
            ),
        ),
        items=(_item(), invalid),
    )
    root = tmp_path / "artifacts"
    if existing:
        root.mkdir()
        (root / "lp_eligible_sfis.json").write_text("[]")
        (root / "lp_eligibility_report.json").write_text('{"old": true}')
    before = bundle.model_dump(mode="json")
    with pytest.raises(expected_exception=ValueError, match="coordinate") as error:
        select_lp_sfis(as_lc_bundle=bundle, kg_config=config, kg_dirs=KGDirs(root=root))
    assert str(invalid.case_identifier_uuid) in str(error.value)
    assert bundle.model_dump(mode="json") == before
    if existing:
        assert (root / "lp_eligible_sfis.json").read_text() == "[]"
        assert (root / "lp_eligibility_report.json").read_text() == '{"old": true}'
    else:
        assert not root.exists()


def test_leaf_normalized_type_and_lc_policy_do_not_control_participation() -> None:
    """A permitted nonleaf grouping participates while an LC-supported leaf is excluded."""

    items = (
        _item(normalized_statement_type="Standard Grouping", number=1),
        _item(number=2, statement_type="Topic"),
        _item(number=3),
    )
    component_uuid = UUID(int=800)
    component = {
        **_COMMON,
        "description": "Synthetic generic LC",
        "identifier": component_uuid,
        "metadata": {"source_sfi_uuids": [str(UUID(int=2))]},
    }
    support = _edge(source=component_uuid, target=UUID(int=2))
    support.update(
        relationship_type="supports",
        source_entity="LearningComponent",
        source_entity_key="identifier",
    )
    bundle = _bundle(
        components=(component,),
        edges=(_edge(source=UUID(int=1), target=UUID(int=2)),),
        items=items,
        supports=(support,),
    )
    payload = _config().model_dump(mode="json")
    payload["lc"]["lc_source_statement_types"] = ["Topic"]
    config = CreateKGConfig.model_validate(payload)
    report = build_lp_selection(as_lc_bundle=bundle, kg_config=config)
    assert {row.sfi.case_identifier_uuid for row in report.eligible_sfis} == {
        UUID(int=1),
        UUID(int=3),
    }
    assert report.sfis[0].sfi.normalized_statement_type == "Standard Grouping"
    assert report.sfis[1].sfi.normalized_statement_type == "Standard"
    assert report.sfis[1].eligibility_reasons == {}
    assert bundle.summary.learning_components.total_lc_source_sfis_eligible == 1
    assert {edge.target_entity_value for edge in bundle.relationships_supports} == {
        str(UUID(int=2))
    }
    payload["lc"]["lc_source_statement_types"] = ["Performance Objective"]
    changed = build_lp_selection(
        as_lc_bundle=bundle, kg_config=CreateKGConfig.model_validate(payload)
    )
    assert changed.sfis == report.sfis
    assert changed.eligible_sfis_content_hash == report.eligible_sfis_content_hash
    _reconcile(report)


@PARAM(argnames="profile", argvalues=tuple(_PROFILES))
def test_missing_coordinates_keep_only_otherwise_permitted_relates_to(
    profile: str,
) -> None:
    """Coordinate absence keeps conceptual participation but never supplies rank evidence.

    Parameters
    ----------
    profile
        Reviewed profile whose participating and omitted types are exercised.
    """

    _, _, _, included, excluded = _PROFILES[profile]
    report = build_lp_selection(
        as_lc_bundle=_bundle(
            items=tuple(
                _item(metadata={}, number=number + 1, statement_type=name)
                for number, name in enumerate((*included, *excluded))
            )
        ),
        kg_config=_config(profile),
    )
    for row in report.sfis:
        assert row.coordinate.status == "missing"
        assert row.coordinate.canonical_value is None
        assert row.coordinate.rank is None
        assert row.coordinate.source_fields == ()
        assert row.eligibility_reasons == (
            {"relatesTo": "configured_statement_type_coordinate_optional"}
            if row.statement_type in included
            else {}
        )
        assert row.exclusion_reasons == (
            {"buildsTowards": "missing_coordinate"}
            if row.statement_type in included
            else dict.fromkeys(sorted(_RELATIONS), "statement_type_not_configured")
        )
        assert any("Missing rank is not positive evidence" in w for w in row.warnings)
    assert report.total_sfis_eligible == len(included)
    assert report.eligible_sfis_per_relationship == {
        "buildsTowards": 0,
        "relatesTo": len(included),
    }
    _reconcile(report)


@PARAM(argnames="profile", argvalues=tuple(_PROFILES))
def test_permutations_write_identical_artifact_bytes(
    *, profile: str, tmp_path: Path
) -> None:
    """Graph and mapping encounter order cannot alter either artifact or any digest.

    Parameters
    ----------
    profile
        Reduced fixture containing real hierarchy and LC populations.
    tmp_path
        Isolated roots for the two artifact sets.
    """

    bundle = _fixture_bundle(profile)
    config = _config(profile)
    payload = _reverse_mapping_order(bundle.model_dump(mode="json"))
    for field in (
        "items",
        "learning_components",
        "relationships_has_child",
        "relationships_supports",
    ):
        payload[field].reverse()
    permuted_bundle = AcademicStandardsLCKGBundle.model_validate(payload)
    permuted_config = CreateKGConfig.model_validate(
        _reverse_mapping_order(config.model_dump(mode="json"))
    )
    first = select_lp_sfis(
        as_lc_bundle=bundle, kg_config=config, kg_dirs=KGDirs(root=tmp_path / "first")
    )
    second = select_lp_sfis(
        as_lc_bundle=permuted_bundle,
        kg_config=permuted_config,
        kg_dirs=KGDirs(root=tmp_path / "second"),
    )
    assert first == second
    for filename in ("lp_eligible_sfis.json", "lp_eligibility_report.json"):
        assert (tmp_path / "first" / filename).read_bytes() == (
            tmp_path / "second" / filename
        ).read_bytes()


def test_relation_specific_source_and_target_participation() -> None:
    """Each endpoint side participates only in the relationship matrix naming its type."""

    payload = _config("pratham_science").model_dump(mode="json")
    payload["lp"]["builds_towards"]["allowed_statement_type_pairs"] = [
        {
            "source_statement_type": "NCERT Learning Outcome",
            "target_statement_type": "Indicator",
        }
    ]
    payload["lp"]["relates_to"]["allowed_statement_type_pairs"] = [
        {
            "first_statement_type": "Content Domain Specific Learning Outcome",
            "second_statement_type": "Chapter",
        }
    ]
    config = CreateKGConfig.model_validate(payload)
    types = (
        "NCERT Learning Outcome",
        "Indicator",
        "Content Domain Specific Learning Outcome",
        "Chapter",
        "Content Domain",
    )
    report = build_lp_selection(
        as_lc_bundle=_bundle(
            items=tuple(
                _item(
                    metadata={"identity_scope_values": {"Class": "Class IX"}},
                    number=number + 1,
                    statement_type=name,
                )
                for number, name in enumerate(types)
            )
        ),
        kg_config=config,
    )
    expected: tuple[set[str], ...] = (
        {"buildsTowards"},
        {"buildsTowards"},
        {"relatesTo"},
        {"relatesTo"},
        set(),
    )
    for row, permitted in zip(report.sfis, expected, strict=True):
        assert set(row.eligibility_reasons) == permitted
        assert set(row.exclusion_reasons) == _RELATIONS - permitted
    assert report.eligible_sfis_per_relationship == {"buildsTowards": 2, "relatesTo": 2}
    _reconcile(report)


def test_repeated_selection_recomputes_changed_policy_and_material(
    tmp_path: Path,
) -> None:
    """Existing eligibility files cannot mask a changed unresolved policy or source.

    Parameters
    ----------
    tmp_path
        Shared artifact directory reused for each deterministic recomputation.
    """

    item = _item()
    bundle = _bundle(
        edges=(
            _edge(
                fallback=True,
                source=_FRAMEWORK_UUID,
                source_entity="StandardsFramework",
                target=item.case_identifier_uuid,
            ),
        ),
        items=(item,),
    )
    config = _config()
    _, first = select_lp_sfis(
        as_lc_bundle=bundle, kg_config=config, kg_dirs=KGDirs(root=tmp_path)
    )
    payload = config.model_dump(mode="json")
    payload["lp"]["unresolved_participation"] = "exclude_unresolved"
    eligible, second = select_lp_sfis(
        as_lc_bundle=bundle,
        kg_config=CreateKGConfig.model_validate(payload),
        kg_dirs=KGDirs(root=tmp_path),
    )
    assert len(first.eligible_sfis) == 1
    assert eligible == []
    assert first.config_content_hash != second.config_content_hash
    assert first.eligible_sfis_content_hash != second.eligible_sfis_content_hash
    assert json.loads((tmp_path / "lp_eligible_sfis.json").read_text()) == []
    bundle.items[0].description = "Changed authoritative text"
    _, third = select_lp_sfis(
        as_lc_bundle=bundle, kg_config=config, kg_dirs=KGDirs(root=tmp_path)
    )
    assert third.upstream_content_hash != first.upstream_content_hash
    assert third.eligible_sfis_content_hash != first.eligible_sfis_content_hash
    assert (
        LPSelectionReport.model_validate_json(
            (tmp_path / "lp_eligibility_report.json").read_text()
        )
        == third
    )


def test_result_records_isolate_nested_upstream_metadata() -> None:
    """Caller mutation cannot alter upstream source/audit provenance in either direction."""

    bundle = _bundle(items=(_item(),))
    before = bundle.model_dump(mode="json")
    report = build_lp_selection(as_lc_bundle=bundle, kg_config=_config())
    row = report.sfis[0]
    row.sfi.metadata["identity_scope_values"]["Grade"] = "PRIMARY THREE"
    row.source_provenance["audit"]["flags"].append("changed-output")
    assert bundle.model_dump(mode="json") == before
    pristine = build_lp_selection(as_lc_bundle=bundle, kg_config=_config())
    saved = pristine.model_dump(mode="json")
    bundle.items[0].metadata["identity_scope_values"]["Grade"] = "PRIMARY TWO"
    bundle.entity_provenance["items"][str(UUID(int=1))]["source_pages"].append(33)
    assert pristine.model_dump(mode="json") == saved


@PARAM(
    argnames="change",
    argvalues=(
        "doc_key",
        "eligible_text",
        "excluded_text",
        "hierarchy_audit",
        "lc_text",
        "provenance",
        "summary",
        "support_audit",
        "unresolved_audit",
    ),
)
def test_upstream_material_changes_invalidate_hash(change: str) -> None:
    """The upstream digest covers eligible and excluded nodes plus all audit populations.

    Parameters
    ----------
    change
        One material upstream field changed without invalidating structural input.
    """

    bundle = _fixture_bundle("ghana_math")
    if change == "excluded_text":
        bundle = _bundle(
            items=(
                _item(
                    metadata={"identity_scope_values": {"Grade": "BASIC 4"}},
                    statement_type="Indicator",
                ),
                _item(
                    metadata={"identity_scope_values": {"Grade": "BASIC 4"}},
                    number=2,
                    statement_type="Strand",
                ),
            )
        )
    config = _config("ghana_math")
    baseline = build_lp_selection(as_lc_bundle=bundle, kg_config=config)
    changed = bundle.model_copy(deep=True)
    if change == "doc_key":
        changed.framework.metadata["doc_key"] = "synthetic-other-document"
    elif change in {"eligible_text", "excluded_text"}:
        item = next(
            item
            for item in changed.items
            if (item.statement_type in _PROFILES["ghana_math"][3])
            == (change == "eligible_text")
        )
        item.description += " Revised source wording."
    elif change == "hierarchy_audit":
        changed.relationships_has_child[0].metadata[
            "audit_note"
        ] = "reviewed source code"
    elif change == "lc_text":
        changed.learning_components[0].description += " Revised LC wording."
    elif change == "provenance":
        key = str(baseline.eligible_sfis[0].sfi.case_identifier_uuid)
        changed.entity_provenance["items"][key]["source_pages"].append(18)
    elif change == "summary":
        changed.summary.learning_components.warnings.append("upstream audit warning")
    elif change == "support_audit":
        changed.relationships_supports[0].metadata["audit_note"] = "reviewed support"
    else:
        changed.unresolved_items.academic_standards.relationship_unresolved_edges[0][
            "audit_note"
        ] = "unresolved evidence reviewed"
    report = build_lp_selection(as_lc_bundle=changed, kg_config=config)
    assert report.upstream_content_hash != baseline.upstream_content_hash
    assert report.config_content_hash == baseline.config_content_hash
    assert (
        report.eligible_sfis_content_hash != baseline.eligible_sfis_content_hash
    ) is (change in {"eligible_text", "provenance"})


@PARAM(
    argnames="defect",
    argvalues=(
        "duplicate",
        "errors",
        "failed",
        "missing_provenance",
        "unknown_endpoint",
    ),
)
def test_upstream_validation_failure_prevents_artifact_writes(
    *, defect: str, tmp_path: Path
) -> None:
    """A passed flag alone cannot authorize malformed authoritative upstream inputs.

    Parameters
    ----------
    defect
        Upstream graph, provenance, or validation-state defect.
    tmp_path
        Isolated root that must remain absent.
    """

    bundle = _bundle(items=(_item(),))
    if defect == "duplicate":
        bundle.items.append(bundle.items[0].model_copy(deep=True))
    elif defect == "errors":
        bundle.validation_report.errors.append("Synthetic upstream failure")
    elif defect == "failed":
        bundle.validation_report.passed = False
    elif defect == "missing_provenance":
        bundle.entity_provenance["items"] = {}
    else:
        bundle.relationships_has_child[0].target_entity_value = str(UUID(int=999))
    root = tmp_path / "should-not-exist"
    with pytest.raises(ValueError):
        select_lp_sfis(
            as_lc_bundle=bundle, kg_config=_config(), kg_dirs=KGDirs(root=root)
        )
    assert not root.exists()
