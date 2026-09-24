"""Red-team canonical developmental coordinates and coordinate-only permissions."""

# Future Library
from __future__ import annotations

# Standard Library
import json

from copy import deepcopy
from dataclasses import FrozenInstanceError
from itertools import product
from typing import Any
from uuid import UUID

# Third Party Library
import pytest

# Package Library
from kgfeg.kgs.lp_coordinates import build_lp_coordinate_index
from kgfeg.kgs.lp_index import build_lp_graph_index
from kgfeg.kgs.schemas import (
    AcademicStandardsLCKGBundle,
    LearningComponent,
    Relationship,
    StandardsFrameworkItem,
)
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
_PROFILES = {
    "ghana_english": (
        "examples/ghana/config_english_curriculum.json",
        "Grade",
        ("BASIC 1", "BASIC 2", "BASIC 3"),
        "Indicator",
    ),
    "ghana_math": (
        "examples/ghana/config_math_curriculum.json",
        "Grade",
        ("BASIC 4", "BASIC 5", "BASIC 6"),
        "Indicator",
    ),
    "madhi_math": (
        "examples/india/madhi/config_math.json",
        "Class",
        ("Class-1", "Class-2", "Class-3", "Class-4", "Class-5"),
        "Content",
    ),
    "nigeria_math": (
        "examples/nigeria/config_math_curriculum_1_3.json",
        "Grade",
        ("PRIMARY ONE", "PRIMARY TWO", "PRIMARY THREE"),
        "Performance Objective",
    ),
    "pratham_science": (
        "examples/india/pratham/config_science.json",
        "Class",
        ("Class IX", "Class X"),
        "Indicator",
    ),
    "rwanda_math": (
        "examples/rwanda/config_math_curriculum_p1_p3.json",
        "Grade",
        ("P1", "P2", "P3"),
        "Knowledge Objective",
    ),
}


def _bundle(
    *,
    components: tuple[LearningComponent, ...] = (),
    edges: tuple[Relationship, ...] = (),
    framework_uuid: UUID = _FRAMEWORK_UUID,
    items: tuple[StandardsFrameworkItem, ...],
    supports: tuple[Relationship, ...] = (),
) -> AcademicStandardsLCKGBundle:
    """Build a schema-valid synthetic bundle with complete root attachments.

    Parameters
    ----------
    components
        Optional supporting Learning Components.
    edges
        Explicit positive or unresolved hierarchy relationships.
    framework_uuid
        Framework CASE identifier.
    items
        Final SFIs whose metadata is under test.
    supports
        Optional LC-to-SFI relationships.

    Returns
    -------
    AcademicStandardsLCKGBundle
        Complete graph with coherent counts and preserved metadata.
    """

    hierarchy = list(edges)
    attached = {edge.target_entity_value for edge in edges}
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
        edge.model_dump(mode="json")
        for edge in hierarchy
        if edge.metadata.get("unresolved_root_fallback") is True
    ]
    return AcademicStandardsLCKGBundle.model_validate(
        {
            "entity_provenance": {
                "items": {
                    str(item.case_identifier_uuid): deepcopy(item.metadata)
                    for item in items
                }
            },
            "framework": {
                **_COMMON,
                "adoption_status": "Adopted",
                "case_identifier_uri": f"urn:uuid:{framework_uuid}",
                "case_identifier_uuid": framework_uuid,
                "identifier": framework_uuid,
                "jurisdiction": "Synthetic jurisdiction",
                "metadata": {"doc_key": "synthetic-coordinate-document"},
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
                "learning_components": {
                    "lc_dedup_candidate_pair_count": 0,
                    "lc_dedup_conflict_count": 0,
                    "lc_dedup_judged_same_count": 0,
                    "lc_generation_failed_sfis_count": 0,
                    "lc_max_splits_observed": 1 if components else 0,
                    "lc_multi_claim_lc_count": 0,
                    "lc_multi_parent_lc_count": sum(
                        len(component.metadata["source_sfi_uuids"]) > 1
                        for component in components
                    ),
                    "lc_selection_mode": "explicit_allowlist",
                    "llm_request_count": 0,
                    "llm_response_count": 0,
                    "total_lc_claims": len(supports),
                    "total_lc_source_sfis_considered": len(items),
                    "total_lc_source_sfis_eligible": len(
                        {edge.target_entity_value for edge in supports}
                    ),
                    "total_lc_source_sfis_empty_text": 0,
                    "total_lc_source_sfis_excluded": len(items)
                    - len({edge.target_entity_value for edge in supports}),
                    "total_lcs": len(components),
                    "total_supports_edges": len(supports),
                },
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
    """Load a complete profile through AS/LP cross-validation.

    Parameters
    ----------
    profile
        Name of the reviewed curriculum profile.

    Returns
    -------
    CreateKGConfig
        Validated configuration without running a pipeline.
    """

    payload = json.loads((PACKAGE_PATH / _PROFILES[profile][0]).read_text())
    return CreateKGConfig.model_validate(payload["kgs"])


def _edge(
    *,
    fallback: bool = False,
    source: UUID,
    source_entity: str = "StandardsFrameworkItem",
    target: UUID,
) -> Relationship:
    """Create a deterministic synthetic hierarchy edge.

    Parameters
    ----------
    fallback
        Whether this is an unresolved framework attachment.
    source
        Parent CASE identifier.
    source_entity
        Framework or SFI parent type.
    target
        Child CASE identifier.

    Returns
    -------
    Relationship
        Complete hierarchy relationship.
    """

    return Relationship.model_validate(
        {
            **{
                key: value
                for key, value in _COMMON.items()
                if key not in {"academic_subject", "in_language"}
            },
            "identifier": UUID(int=(source.int ^ target.int) + 2**120),
            "metadata": {"unresolved_root_fallback": fallback},
            "relationship_type": "hasChild",
            "source_entity": source_entity,
            "source_entity_key": "case_identifier_uuid",
            "source_entity_value": str(source),
            "target_entity": "StandardsFrameworkItem",
            "target_entity_key": "case_identifier_uuid",
            "target_entity_value": str(target),
        }
    )


def _item(
    *,
    metadata: dict[str, Any],
    normalized_statement_type: str = "Standard",
    number: int = 1,
    statement_type: str = "Performance Objective",
) -> StandardsFrameworkItem:
    """Create a final SFI with misleading noncanonical display information.

    Parameters
    ----------
    metadata
        Exact metadata to test, including malformed coordinate payloads.
    normalized_statement_type
        Exported grain, independently of coordinate participation.
    number
        Distinguishing synthetic CASE UUID suffix.
    statement_type
        Local statement type, independently of normalized grain.

    Returns
    -------
    StandardsFrameworkItem
        Schema-valid SFI retaining untrusted metadata unchanged.
    """

    case_uuid = UUID(int=number)
    return StandardsFrameworkItem.model_validate(
        {
            **_COMMON,
            "case_identifier_uri": f"urn:uuid:{case_uuid}",
            "case_identifier_uuid": case_uuid,
            "description": "PRIMARY THREE / Grade 12 / Class X",
            "grade_level": ["12"],
            "identifier": UUID(int=number + 2**112),
            "jurisdiction": "Synthetic jurisdiction",
            "metadata": deepcopy(metadata),
            "normalized_statement_type": normalized_statement_type,
            "statement_code": "PRIMARY THREE.99",
            "statement_type": statement_type,
        }
    )


@PARAM(argnames="profile", argvalues=tuple(_PROFILES))
def test_aliases_canonicalize_scope_labels_and_values(profile: str) -> None:
    """Every configured alias resolves to its pinned canonical rank.

    Parameters
    ----------
    profile
        Curriculum whose AS alias vocabulary supplies test inputs.
    """

    config = _config(profile)
    _, coordinate_type, ordered_values, item_type = _PROFILES[profile]
    policy = next(
        entry
        for entry in config.academic_standards.statement_type_policy
        if entry.statement_type == coordinate_type
    )
    records: list[StandardsFrameworkItem] = []
    expected = {}
    for controlled in policy.controlled_values:
        rank = ordered_values.index(controlled.canonical_value)
        for label, value in product(
            [coordinate_type, *policy.aliases],
            [controlled.canonical_value, *controlled.aliases],
        ):
            item = _item(
                metadata={
                    "identity_scope_values": {
                        f" {label.swapcase()} ": f" {value.swapcase()} "
                    }
                },
                number=len(records) + 1,
                statement_type=item_type,
            )
            records.append(item)
            expected[item.case_identifier_uuid] = (controlled.canonical_value, rank)
    coordinates = build_lp_coordinate_index(
        graph_index=build_lp_graph_index(_bundle(items=tuple(records))),
        kg_config=config,
    )
    assert {
        key: (value.canonical_value, value.rank)
        for key, value in coordinates.coordinate_by_sfi_uuid.items()
    } == expected


@PARAM(
    argnames="metadata",
    argvalues=(
        {"identity_scope_values": {"Grade": "PRIMARY ONE", "Class": "PRIMARY TWO"}},
        {
            "identity_scope_values": {"Grade": "PRIMARY ONE"},
            "canonical_statement_value": "PRIMARY TWO",
        },
        {
            "canonical_statement_value": "PRIMARY ONE",
            "statement_value_canonicalization": {
                "canonical_statement_value": "PRIMARY TWO"
            },
        },
        {
            "identity_scope_values": {"Grade": "PRIMARY ONE"},
            "statement_value_canonicalization": {
                "canonical_statement_value": "PRIMARY TWO"
            },
        },
    ),
)
def test_conflicting_canonical_sources_fail_atomically(
    metadata: dict[str, Any],
) -> None:
    """Disagreeing scope aliases or own values cannot degrade to missing state.

    Parameters
    ----------
    metadata
        Conflicting authoritative coordinate sources.
    """

    invalid = _item(metadata=metadata, number=2, statement_type="Grade")
    graph = build_lp_graph_index(
        _bundle(
            items=(
                _item(metadata={"identity_scope_values": {"Grade": "PRIMARY ONE"}}),
                invalid,
            )
        )
    )
    with pytest.raises(expected_exception=ValueError, match="conflicting") as error:
        build_lp_coordinate_index(graph_index=graph, kg_config=_config())
    assert str(invalid.case_identifier_uuid) in str(error.value)
    assert graph.sfi_by_uuid[invalid.case_identifier_uuid].metadata == metadata


def test_coordinate_node_without_canonical_value_stays_missing() -> None:
    """A coordinate node's display text cannot substitute for absent own metadata."""

    item = _item(
        metadata={
            "canonical_statement_value": None,
            "statement_value_canonicalization": {"canonical_statement_value": None},
        },
        statement_type="Grade",
    )
    coordinates = build_lp_coordinate_index(
        graph_index=build_lp_graph_index(_bundle(items=(item,))), kg_config=_config()
    )
    record = coordinates.coordinate_by_sfi_uuid[item.case_identifier_uuid]
    assert (
        record.canonical_value,
        record.rank,
        record.source_fields,
        record.status,
    ) == (None, None, (), "missing")


@PARAM(
    argnames="profile",
    argvalues=tuple(name for name in _PROFILES if name != "madhi_math"),
)
@PARAM(argnames="source", argvalues=("top", "nested", "all"))
def test_coordinate_nodes_use_own_canonical_values(
    *, profile: str, source: str
) -> None:
    """Explicit Grade/Class nodes resolve without scope or display inference.

    Parameters
    ----------
    profile
        Curriculum with explicit coordinate nodes.
    source
        Own canonical metadata location or all agreeing locations.
    """

    _, coordinate_type, values, _ = _PROFILES[profile]
    config = _config(profile)
    policy = next(
        entry
        for entry in config.academic_standards.statement_type_policy
        if entry.statement_type == coordinate_type
    )
    items = []
    for rank, value in enumerate(values):
        if source == "nested":
            value = policy.controlled_values[rank].aliases[-1]
        metadata: dict[str, Any] = {}
        if source in {"top", "all"}:
            metadata["canonical_statement_value"] = value
        if source in {"nested", "all"}:
            metadata["statement_value_canonicalization"] = {
                "canonical_statement_value": value
            }
        if source == "all":
            metadata["identity_scope_values"] = {coordinate_type: value}
        items.append(
            _item(
                metadata=metadata,
                normalized_statement_type="Standard Grouping",
                number=rank + 1,
                statement_type=(
                    policy.aliases[0] if source == "nested" else coordinate_type
                ),
            )
        )
    coordinates = build_lp_coordinate_index(
        graph_index=build_lp_graph_index(_bundle(items=tuple(items))),
        kg_config=_config(profile),
    )
    for rank, item in enumerate(items):
        record = coordinates.coordinate_by_sfi_uuid[item.case_identifier_uuid]
        assert (record.canonical_value, record.rank, record.status) == (
            values[rank],
            rank,
            "resolved",
        )
        assert (
            record.source_fields
            == {
                "top": ("canonical_statement_value",),
                "nested": (
                    "statement_value_canonicalization.canonical_statement_value",
                ),
                "all": (
                    "canonical_statement_value",
                    f"identity_scope_values[{coordinate_type!r}]",
                    "statement_value_canonicalization.canonical_statement_value",
                ),
            }[source]
        )


@PARAM(argnames="profile", argvalues=tuple(_PROFILES))
def test_exact_orders_and_complete_coordinate_permission_matrix(profile: str) -> None:
    """Configured order governs every direction, gap, and missing combination.

    Parameters
    ----------
    profile
        Reviewed curriculum with independently pinned canonical order.
    """

    _, coordinate_type, values, item_type = _PROFILES[profile]
    inputs = (*values, values[0], None, None)
    items = tuple(
        _item(
            metadata=(
                {"identity_scope_values": {coordinate_type: value}} if value else {}
            ),
            number=number + 1,
            statement_type=item_type,
        )
        for number, value in enumerate(inputs)
    )
    coordinates = build_lp_coordinate_index(
        graph_index=build_lp_graph_index(_bundle(items=tuple(reversed(items)))),
        kg_config=_config(profile),
    )
    assert coordinates.ordered_values == values
    assert coordinates.statement_type == coordinate_type
    assert len(coordinates.coordinate_by_sfi_uuid) == len(items)
    for item, value in zip(items, inputs, strict=True):
        record = coordinates.coordinate_by_sfi_uuid[item.case_identifier_uuid]
        assert record.canonical_value == value
        assert record.rank == (values.index(value) if value else None)
        assert record.status == ("resolved" if value else "missing")
        assert record.source_fields == (
            (f"identity_scope_values[{coordinate_type!r}]",) if value else ()
        )
    for (source, source_value), (target, target_value) in product(
        zip(items, inputs, strict=True), repeat=2
    ):
        if source is target:
            continue
        allowed = (
            source_value is not None
            and target_value is not None
            and values.index(source_value) <= values.index(target_value)
        )
        assert (
            coordinates.allows_builds_towards(
                source_sfi_uuid=source.case_identifier_uuid,
                target_sfi_uuid=target.case_identifier_uuid,
            )
            is allowed
        )
        assert (
            coordinates.allows_relates_to(
                first_sfi_uuid=source.case_identifier_uuid,
                second_sfi_uuid=target.case_identifier_uuid,
            )
            is True
        )
    if profile == "madhi_math":
        assert {item.statement_type for item in items} == {"Content"}


@PARAM(argnames="profile", argvalues=tuple(_PROFILES))
def test_fixture_coordinates_preserve_graph_and_unresolved_context(
    profile: str,
) -> None:
    """Reduced real scope metadata composes with DAG, fallback, and LC indexes.

    Parameters
    ----------
    profile
        Approved reduced fixture and matching reviewed configuration.
    """

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / f"{profile}.json")
    _, coordinate_type, values, _ = _PROFILES[profile]
    items = tuple(
        _item(
            metadata=item.metadata,
            number=item.case_identifier_uuid.int,
            statement_type=item.statement_type,
        ).model_copy(
            update={
                "description": item.description,
                "normalized_statement_type": item.normalized_statement_type,
                "statement_code": item.statement_code,
            }
        )
        for item in fixture.items
    )
    edge_common = {
        key: value
        for key, value in _COMMON.items()
        if key not in {"academic_subject", "in_language"}
    }
    bundle = _bundle(
        components=tuple(
            LearningComponent.model_validate({**_COMMON, **component.model_dump()})
            for component in fixture.learning_components
        ),
        edges=tuple(
            Relationship.model_validate({**edge_common, **edge.model_dump(mode="json")})
            for edge in fixture.relationships_has_child
        ),
        framework_uuid=fixture.framework.case_identifier_uuid,
        items=items,
        supports=tuple(
            Relationship.model_validate({**edge_common, **edge.model_dump(mode="json")})
            for edge in fixture.relationships_supports
        ),
    )
    original = bundle.model_dump(mode="json")
    graph = build_lp_graph_index(bundle)
    coordinates = build_lp_coordinate_index(
        graph_index=graph, kg_config=_config(profile)
    )
    for item in fixture.items:
        value = item.metadata["identity_scope_values"].get(coordinate_type)
        record = coordinates.coordinate_by_sfi_uuid[item.case_identifier_uuid]
        assert (record.canonical_value, record.rank) == (
            value,
            values.index(value) if value else None,
        )
    assert bundle.model_dump(mode="json") == original
    assert graph.root_fallback_sfi_uuids == tuple(
        sorted(fixture.expectations.unresolved_item_ids, key=str)
    )
    if profile == "pratham_science":
        assert max(map(len, graph.parent_sfi_uuids_by_sfi_uuid.values())) == 2
    for unresolved in graph.root_fallback_sfi_uuids:
        assert unresolved in graph.unresolved_ancestry_sfi_uuids
        assert unresolved not in graph.framework_child_sfi_uuids
        assert coordinates.coordinate_by_sfi_uuid[unresolved].status == "resolved"


@PARAM(
    argnames="metadata",
    argvalues=(
        {"identity_scope_values": None},
        {"identity_scope_values": {"Grade": None}},
        {"identity_scope_values": []},
        {"identity_scope_values": "PRIMARY ONE"},
        {"identity_scope_values": {1: "PRIMARY ONE"}},
        {"identity_scope_values": {"Unknown Dimension": "PRIMARY ONE"}},
        {"statement_value_canonicalization": []},
    ),
)
def test_malformed_coordinate_containers_fail(metadata: dict[str, Any]) -> None:
    """Malformed metadata cannot become an ordinary absent coordinate.

    Parameters
    ----------
    metadata
        Invalid scope or own-value container.
    """

    item = _item(metadata=metadata, statement_type="Grade")
    with pytest.raises(expected_exception=ValueError):
        build_lp_coordinate_index(
            graph_index=build_lp_graph_index(_bundle(items=(item,))),
            kg_config=_config(),
        )


@PARAM(
    argnames="metadata",
    argvalues=(
        {},
        {"identity_scope_values": {}},
        {"identity_scope_values": {"Theme": "PRIMARY ONE"}},
        {"canonical_statement_value": "PRIMARY ONE"},
        {
            "statement_value_canonicalization": {
                "canonical_statement_value": "PRIMARY ONE"
            }
        },
        {
            "identity_scope_key": "Grade=PRIMARY ONE",
            "canonical_statement_value_key": "primary one",
            "source_page_indexes": [1],
            "candidate_source_texts": ["PRIMARY ONE"],
        },
    ),
)
def test_missing_coordinate_ignores_nonauthoritative_hints(
    metadata: dict[str, Any],
) -> None:
    """Non-coordinate SFIs cannot acquire a rank from hints outside their scope.

    Parameters
    ----------
    metadata
        Missing coordinate plus potentially misleading metadata.
    """

    item = _item(metadata=metadata)
    parent = _item(
        metadata={"canonical_statement_value": "PRIMARY ONE"},
        number=2,
        statement_type="Grade",
    )
    graph = build_lp_graph_index(
        _bundle(
            edges=(
                _edge(
                    source=parent.case_identifier_uuid, target=item.case_identifier_uuid
                ),
            ),
            items=(item, parent),
        )
    )
    coordinates = build_lp_coordinate_index(graph_index=graph, kg_config=_config())
    record = coordinates.coordinate_by_sfi_uuid[item.case_identifier_uuid]
    assert (
        record.canonical_value,
        record.rank,
        record.source_fields,
        record.status,
    ) == (None, None, (), "missing")
    assert not coordinates.allows_builds_towards(
        source_sfi_uuid=item.case_identifier_uuid,
        target_sfi_uuid=parent.case_identifier_uuid,
    )
    assert coordinates.allows_relates_to(
        first_sfi_uuid=item.case_identifier_uuid,
        second_sfi_uuid=parent.case_identifier_uuid,
    )


def test_permutations_and_snapshot_isolation() -> None:
    """Encounter order cannot change coordinates or leak later input mutations."""

    config = _config()
    items = (
        _item(
            metadata={
                "identity_scope_values": {"Grade": "Primary: One", "Class": "PRIMARY 1"}
            },
            number=2,
        ),
        _item(metadata={}, number=1),
    )
    bundle = _bundle(items=items)
    original = bundle.model_dump(mode="json")
    graph = build_lp_graph_index(bundle)
    coordinates = build_lp_coordinate_index(graph_index=graph, kg_config=config)
    bundle.items.reverse()
    bundle.relationships_has_child.reverse()
    bundle.entity_provenance["items"] = dict(
        reversed(list(bundle.entity_provenance["items"].items()))
    )
    items[0].metadata["identity_scope_values"] = {
        "Class": "PRIMARY 1",
        "Grade": "Primary: One",
    }
    permuted = build_lp_coordinate_index(
        graph_index=build_lp_graph_index(bundle), kg_config=config
    )
    assert coordinates == permuted
    assert list(coordinates.coordinate_by_sfi_uuid) == [UUID(int=1), UUID(int=2)]
    assert coordinates.coordinate_by_sfi_uuid[UUID(int=2)].source_fields == (
        "identity_scope_values['Class']",
        "identity_scope_values['Grade']",
    )
    assert (
        original["items"][0]["metadata"]["identity_scope_values"]
        == items[0].metadata["identity_scope_values"]
    )
    items[0].metadata["identity_scope_values"]["Grade"] = "PRIMARY THREE"
    config.learning_progressions.developmental_coordinate.ordered_values.reverse()
    assert (
        coordinates.coordinate_by_sfi_uuid[UUID(int=2)].canonical_value == "PRIMARY ONE"
    )
    assert coordinates.ordered_values == _PROFILES["nigeria_math"][2]
    with pytest.raises(TypeError):
        coordinates.coordinate_by_sfi_uuid[UUID(int=2)] = coordinates.coordinate_by_sfi_uuid[UUID(int=1)]  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        coordinates.coordinate_by_sfi_uuid[UUID(int=2)].rank = 99  # type: ignore[misc]


def test_synthetic_coordinate_order_is_not_a_curriculum_constant() -> None:
    """An unfamiliar coordinate and nonlexical order use only validated config."""

    payload = _config().model_dump(mode="json")
    text = json.dumps(payload).replace('"Grade"', '"Learning Cycle"')
    for old, new in zip(
        _PROFILES["nigeria_math"][2], ("Étage 2", "Étage 10", "Étage 1"), strict=True
    ):
        text = text.replace(json.dumps(old), json.dumps(new))
    payload = json.loads(text)
    order = ("Étage 10", "Étage 1", "Étage 2")
    payload["lp"]["developmental_coordinate"]["ordered_values"] = list(order)
    config = CreateKGConfig.model_validate(payload)
    items = tuple(
        _item(
            metadata={
                "identity_scope_values": {
                    "Learning Cycle": value.replace("É", "E\u0301").replace(" ", "—")
                }
            },
            number=rank + 1,
        )
        for rank, value in enumerate(order)
    )
    coordinates = build_lp_coordinate_index(
        graph_index=build_lp_graph_index(_bundle(items=items)), kg_config=config
    )
    assert coordinates.ordered_values == order
    assert [
        coordinates.coordinate_by_sfi_uuid[item.case_identifier_uuid].rank
        for item in items
    ] == [0, 1, 2]
    assert coordinates.allows_builds_towards(
        source_sfi_uuid=items[0].case_identifier_uuid,
        target_sfi_uuid=items[2].case_identifier_uuid,
    )
    assert not coordinates.allows_builds_towards(
        source_sfi_uuid=items[2].case_identifier_uuid,
        target_sfi_uuid=items[0].case_identifier_uuid,
    )


@PARAM(argnames="bad_first", argvalues=(False, True))
@PARAM(argnames="relation", argvalues=("buildsTowards", "relatesTo"))
def test_unknown_endpoints_fail(*, bad_first: bool, relation: str) -> None:
    """Neither endpoint position may smuggle an unknown SFI through permissions.

    Parameters
    ----------
    bad_first
        Whether the unknown UUID occupies the first endpoint.
    relation
        Coordinate permission method to exercise.
    """

    item = _item(metadata={})
    coordinates = build_lp_coordinate_index(
        graph_index=build_lp_graph_index(_bundle(items=(item,))), kg_config=_config()
    )
    first, second = (
        (UUID(int=99), item.case_identifier_uuid)
        if bad_first
        else (item.case_identifier_uuid, UUID(int=99))
    )
    with pytest.raises(expected_exception=ValueError, match="Unknown SFI"):
        if relation == "buildsTowards":
            coordinates.allows_builds_towards(
                source_sfi_uuid=first, target_sfi_uuid=second
            )
        else:
            coordinates.allows_relates_to(first_sfi_uuid=first, second_sfi_uuid=second)


@PARAM(argnames="source", argvalues=("scope", "top", "nested"))
@PARAM(
    argnames="value",
    argvalues=(
        "UNLISTED",
        "",
        "   ",
        1,
        False,
        ["PRIMARY ONE", "PRIMARY TWO"],
        {"value": "PRIMARY ONE"},
        "PRIMARY ONE / PRIMARY TWO",
    ),
)
def test_unrecognized_or_ambiguous_values_fail(*, source: str, value: Any) -> None:
    """Every supplied invalid value fails instead of enabling either relationship.

    Parameters
    ----------
    source
        Authoritative metadata location.
    value
        Unknown, malformed, blank, or multi-valued coordinate.
    """

    metadata = {
        "scope": {"identity_scope_values": {"Grade": value}},
        "top": {"canonical_statement_value": value},
        "nested": {
            "statement_value_canonicalization": {"canonical_statement_value": value}
        },
    }[source]
    item = _item(metadata=metadata, statement_type="Grade")
    with pytest.raises(expected_exception=ValueError, match="coordinate") as error:
        build_lp_coordinate_index(
            graph_index=build_lp_graph_index(_bundle(items=(item,))),
            kg_config=_config(),
        )
    assert str(item.case_identifier_uuid) in str(error.value)


@PARAM(argnames="missing", argvalues=(False, True))
@PARAM(
    argnames="policy",
    argvalues=("exclude_unresolved", "include_unresolved_with_warnings"),
)
def test_unresolved_context_is_preserved_for_eligibility(
    *, missing: bool, policy: str
) -> None:
    """Coordinate compatibility leaves unresolved participation to the caller.

    Parameters
    ----------
    missing
        Whether unresolved nodes lack canonical coordinates.
    policy
        Profile-wide unresolved participation setting.
    """

    payload = _config("ghana_math").model_dump(mode="json")
    payload["lp"]["unresolved_participation"] = policy
    config = CreateKGConfig.model_validate(payload)
    metadata = {} if missing else {"identity_scope_values": {"Grade": "BASIC 5"}}
    parent = _item(metadata=metadata, number=1, statement_type="Content Standard")
    child = _item(metadata=metadata, number=2, statement_type="Indicator")
    bundle = _bundle(
        edges=(
            _edge(
                fallback=True,
                source=_FRAMEWORK_UUID,
                source_entity="StandardsFramework",
                target=parent.case_identifier_uuid,
            ),
            _edge(
                source=parent.case_identifier_uuid, target=child.case_identifier_uuid
            ),
        ),
        items=(parent, child),
    )
    original = bundle.model_dump(mode="json")
    graph = build_lp_graph_index(bundle)
    coordinates = build_lp_coordinate_index(graph_index=graph, kg_config=config)
    assert graph.unresolved_ancestry_sfi_uuids == frozenset(
        (parent.case_identifier_uuid, child.case_identifier_uuid)
    )
    assert not graph.framework_child_sfi_uuids
    assert graph.parent_sfi_uuids_by_sfi_uuid[parent.case_identifier_uuid] == ()
    assert coordinates.coordinate_by_sfi_uuid[child.case_identifier_uuid].status == (
        "missing" if missing else "resolved"
    )
    assert coordinates.allows_builds_towards(
        source_sfi_uuid=parent.case_identifier_uuid,
        target_sfi_uuid=child.case_identifier_uuid,
    ) is (not missing)
    assert coordinates.allows_relates_to(
        first_sfi_uuid=parent.case_identifier_uuid,
        second_sfi_uuid=child.case_identifier_uuid,
    )
    assert bundle.model_dump(mode="json") == original
    child.metadata["identity_scope_values"] = {"Grade": "UNKNOWN"}
    with pytest.raises(expected_exception=ValueError, match="coordinate"):
        build_lp_coordinate_index(graph_index=graph, kg_config=config)
