"""Validate the reduced six-curriculum Learning Progressions fixtures."""

# Standard Library
from copy import deepcopy

# Third Party Library
import pytest

# Package Library
from tests.fixtures.lp.loader import (
    FIXTURE_FILENAMES,
    LP_FIXTURES_DIR,
    LPRegressionFixture,
    load_all_lp_regression_fixtures,
    load_lp_regression_fixture,
    validate_lp_regression_fixture,
)

EXPECTED_REDUCED_COUNTS = {
    "ghana_english": (8, 2, 4, 4),
    "ghana_math": (3, 1, 2, 1),
    "madhi_math": (4, 2, 2, 2),
    "nigeria_math": (5, 1, 5, 1),
    "pratham_science": (4, 1, 3, 1),
    "rwanda_math": (15, 1, 0, 11),
}
EXPECTED_SOURCE_SNAPSHOTS = {
    "ghana_english": {
        "bundle_sha256": "49ede64fa0b843ba6bbb553c924734454277af9fd13dda580679752e467c5d73",
        "doc_key": "e49a792637011b32ed2ed906d58f9992e5208d2b7c15425ccc971f4f51de43c8",
        "framework_case_identifier_uuid": "862e9144-b9e0-5512-bfa9-55597af6d1d6",
        "object_counts": (430, 272, 430, 307),
        "statement_type_counts": {
            "Content Standard": 102,
            "Grade": 3,
            "Indicator": 217,
            "Strand": 15,
            "Sub-Strand": 93,
        },
        "unresolved_root_fallback_count": 2,
    },
    "ghana_math": {
        "bundle_sha256": "2a77c429f60f7bd84cb89ef270fa253950e2e34e7c17c31e12ac809f1936ab80",
        "doc_key": "8d59d76cb439110ad9409e9c12561023df60d12f234ced7285796f7e320fd9fa",
        "framework_case_identifier_uuid": "61ff6c68-36c2-590a-95a2-d3f248117ca7",
        "object_counts": (302, 230, 302, 273),
        "statement_type_counts": {
            "Content Standard": 72,
            "Grade": 3,
            "Indicator": 184,
            "Strand": 12,
            "Sub-Strand": 31,
        },
        "unresolved_root_fallback_count": 13,
    },
    "madhi_math": {
        "bundle_sha256": "35f13bd5f94e82d47b823cc3027e0e53e24909602536d21763d0fa5d4d4d5683",
        "doc_key": "33c5da78839a7611308dd30f342b72f931a661235e094cc641832ec2a5548444",
        "framework_case_identifier_uuid": "3b7ddd08-3682-5dbb-8455-abba82e54370",
        "object_counts": (255, 399, 255, 419),
        "statement_type_counts": {
            "Competency": 30,
            "Content": 220,
            "Curricular Goal": 5,
        },
        "unresolved_root_fallback_count": 0,
    },
    "nigeria_math": {
        "bundle_sha256": "a468903e1cb118bb08cb5d6f9586e281b25efce147edf1eff0b66bb7486ce9e7",
        "doc_key": "09d6b52b54b2f6b00d5279a58650d0ef10ab5a6a5d3da02d10b24c42edb1058a",
        "framework_case_identifier_uuid": "ea1bf379-42ef-5eb6-bbaa-e2eb65f50092",
        "object_counts": (242, 186, 242, 204),
        "statement_type_counts": {
            "Grade": 3,
            "Performance Objective": 155,
            "Sub-Theme": 21,
            "Theme": 15,
            "Topic": 48,
        },
        "unresolved_root_fallback_count": 0,
    },
    "pratham_science": {
        "bundle_sha256": "b61539326e140a706e137e7ebe7cf4a2d00aaf6b2db17c0600c34a173be3a154",
        "doc_key": "3f8c25c19ed8395bf6625b7958092b8d219cae743b9ea36fcfd56efd59b6d2fc",
        "framework_case_identifier_uuid": "4839ccc8-5fcc-58a1-a981-7994625c79b2",
        "object_counts": (874, 853, 1109, 858),
        "statement_type_counts": {
            "Chapter": 31,
            "Class": 2,
            "Content Domain": 10,
            "Content Domain Specific Learning Outcome": 235,
            "Indicator": 554,
            "NCERT Learning Outcome": 42,
        },
        "unresolved_root_fallback_count": 0,
    },
    "rwanda_math": {
        "bundle_sha256": "7035a17b275b9a4e5a30cdceac39283f066e054d82448c791e49df9ef4141e8b",
        "doc_key": "7b9629e6bd5ad5566e6420892908a307687543ede8cc0c676ddf5b0321ba5874",
        "framework_case_identifier_uuid": "27fe12d6-cd6b-5812-965b-41748c65ea85",
        "object_counts": (626, 716, 626, 797),
        "statement_type_counts": {
            "Attitudes and Values Objective": 116,
            "Grade": 3,
            "Grade Key Competence": 29,
            "Key Unit Competence": 38,
            "Knowledge Objective": 155,
            "Skills Objective": 204,
            "Sub-Topic Area": 29,
            "Topic Area": 14,
            "Unit": 38,
        },
        "unresolved_root_fallback_count": 0,
    },
}


def test_fixture_loader_rejects_coordinated_alignment_and_expectation_drift() -> None:
    """A matching fixture expectation cannot legitimize a wrong source alignment."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "ghana_math.json")
    payload = fixture.model_dump(mode="json")
    relationship = payload["relationships_supports"][0]
    component = payload["learning_components"][0]
    old_target = relationship["target_entity_value"]
    new_target = payload["expectations"]["unresolved_item_ids"][0]
    relationship["target_entity_value"] = new_target
    component["metadata"]["source_sfi_uuids"] = [new_target]
    payload["expectations"]["lc_alignments"][component["identifier"]] = [new_target]

    assert old_target != new_target
    with pytest.raises(
        expected_exception=ValueError,
        match="Approved reduced fixture projection drifted",
    ):
        validate_lp_regression_fixture(LPRegressionFixture.model_validate(payload))


def test_fixture_loader_rejects_count_drift() -> None:
    """Reduced fixture count drift fails closed."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "madhi_math.json")
    payload = fixture.model_dump(mode="json")
    payload["expectations"]["counts"]["items"] += 1

    with pytest.raises(
        expected_exception=ValueError,
        match="Reduced fixture counts do not match fixture expectations",
    ):
        validate_lp_regression_fixture(LPRegressionFixture.model_validate(payload))


def test_fixture_loader_rejects_dag_parent_drift() -> None:
    """Removing one Pratham direct parent invalidates the DAG fixture."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "pratham_science.json")
    payload = fixture.model_dump(mode="json")
    parent_sets = payload["expectations"]["direct_parent_sets"]
    multi_parent_child = next(
        child_id for child_id, parent_ids in parent_sets.items() if len(parent_ids) == 2
    )
    parent_sets[multi_parent_child].pop()

    with pytest.raises(
        expected_exception=ValueError,
        match="Direct parent sets do not match fixture expectations",
    ):
        validate_lp_regression_fixture(LPRegressionFixture.model_validate(payload))


def test_fixture_loader_rejects_duplicate_logical_edges() -> None:
    """Duplicate endpoints fail even when relationship UUIDs remain unique."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "madhi_math.json")
    payload = fixture.model_dump(mode="json")
    duplicate = deepcopy(payload["relationships_has_child"][0])
    duplicate["identifier"] = "00000000-0000-0000-0000-000000000001"
    payload["relationships_has_child"].append(duplicate)
    payload["expectations"]["counts"]["relationships_has_child"] += 1

    with pytest.raises(
        expected_exception=ValueError,
        match="Fixture contains duplicate logical relationships",
    ):
        validate_lp_regression_fixture(LPRegressionFixture.model_validate(payload))


def test_fixture_loader_rejects_lc_alignment_drift() -> None:
    """Removing a Rwanda LC target invalidates the pinned alignment."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "rwanda_math.json")
    payload = fixture.model_dump(mode="json")
    alignment = next(iter(payload["expectations"]["lc_alignments"].values()))
    alignment.pop()

    with pytest.raises(
        expected_exception=ValueError,
        match="LC alignments do not match fixture expectations",
    ):
        validate_lp_regression_fixture(LPRegressionFixture.model_validate(payload))


def test_fixture_loader_rejects_malformed_metadata_types() -> None:
    """Shallowly correct metadata keys do not permit invalid value types."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "madhi_math.json")
    payload = fixture.model_dump(mode="json")
    payload["items"][0]["metadata"]["audit_flags"] = [42]

    with pytest.raises(
        expected_exception=ValueError,
        match="metadata is malformed",
    ):
        validate_lp_regression_fixture(LPRegressionFixture.model_validate(payload))


def test_fixture_loader_rejects_mismatched_endpoint_entity() -> None:
    """Relationship endpoint labels must agree with their referenced UUIDs."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "madhi_math.json")
    payload = fixture.model_dump(mode="json")
    non_root_edge = next(
        relationship
        for relationship in payload["relationships_has_child"]
        if relationship["source_entity"] == "StandardsFrameworkItem"
    )
    non_root_edge["source_entity"] = "StandardsFramework"

    with pytest.raises(
        expected_exception=ValueError,
        match="Malformed hasChild relationship",
    ):
        validate_lp_regression_fixture(LPRegressionFixture.model_validate(payload))


def test_fixture_loader_rejects_source_shape_duplicates() -> None:
    """Snapshot shape lists reject duplicates rather than comparing as sets."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "madhi_math.json")
    payload = fixture.model_dump(mode="json")
    payload["source_bundle"]["top_level_keys"].append("framework")

    with pytest.raises(
        expected_exception=ValueError,
        match=r"AS\+LC bundle top-level shape drifted",
    ):
        validate_lp_regression_fixture(LPRegressionFixture.model_validate(payload))


def test_fixture_loader_rejects_unaligned_learning_component() -> None:
    """Every reduced Learning Component must retain at least one alignment."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "madhi_math.json")
    payload = fixture.model_dump(mode="json")
    unaligned_component = deepcopy(payload["learning_components"][0])
    unaligned_component["identifier"] = "00000000-0000-0000-0000-000000000002"
    payload["learning_components"].append(unaligned_component)
    payload["expectations"]["counts"]["learning_components"] += 1

    with pytest.raises(
        expected_exception=ValueError,
        match="Every fixture Learning Component must have a supports edge",
    ):
        validate_lp_regression_fixture(LPRegressionFixture.model_validate(payload))


def test_fixture_loader_rejects_unresolved_flag_drift() -> None:
    """Clearing Ghana's root-fallback flag invalidates unresolved expectations."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "ghana_math.json")
    payload = fixture.model_dump(mode="json")
    unresolved_edge = next(
        relationship
        for relationship in payload["relationships_has_child"]
        if relationship["metadata"]["unresolved_root_fallback"]
    )
    unresolved_edge["metadata"]["unresolved_root_fallback"] = False

    with pytest.raises(
        expected_exception=ValueError,
        match="Unresolved fallback flags do not match fixture expectations",
    ):
        validate_lp_regression_fixture(LPRegressionFixture.model_validate(payload))


def test_fixture_loader_rejects_wrong_distinctive_property() -> None:
    """A valid property label from another curriculum still fails closed."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "nigeria_math.json")
    payload = fixture.model_dump(mode="json")
    payload["distinctive_property"] = "multi_parent_dag"

    with pytest.raises(
        expected_exception=ValueError,
        match="Curriculum and distinctive-property label disagree",
    ):
        validate_lp_regression_fixture(LPRegressionFixture.model_validate(payload))


def test_fixture_loader_validates_all_six_source_snapshots() -> None:
    """All six fixtures retain independently pinned source and reduced counts."""

    fixtures = load_all_lp_regression_fixtures()
    assert sorted(path.name for path in LP_FIXTURES_DIR.glob("*.json")) == list(
        FIXTURE_FILENAMES
    )
    assert [fixture.curriculum for fixture in fixtures] == sorted(
        EXPECTED_SOURCE_SNAPSHOTS
    )

    for fixture in fixtures:
        expected_source = EXPECTED_SOURCE_SNAPSHOTS[fixture.curriculum]
        snapshot = fixture.source_bundle
        assert snapshot.bundle_sha256 == expected_source["bundle_sha256"]
        assert snapshot.doc_key == expected_source["doc_key"]
        assert (
            str(snapshot.framework_case_identifier_uuid)
            == expected_source["framework_case_identifier_uuid"]
        )
        assert (
            snapshot.object_counts["standards_framework_items"],
            snapshot.object_counts["learning_components"],
            snapshot.object_counts["relationships_has_child"],
            snapshot.object_counts["relationships_supports"],
        ) == expected_source["object_counts"]
        assert (
            snapshot.statement_type_counts == expected_source["statement_type_counts"]
        )
        assert (
            snapshot.unresolved_root_fallback_count
            == expected_source["unresolved_root_fallback_count"]
        )
        assert (
            len(fixture.items),
            len(fixture.learning_components),
            len(fixture.relationships_has_child),
            len(fixture.relationships_supports),
        ) == EXPECTED_REDUCED_COUNTS[fixture.curriculum]
