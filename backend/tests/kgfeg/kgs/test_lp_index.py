"""Red-team deterministic Learning Progressions graph indexing."""

# Future Library
from __future__ import annotations

# Standard Library
from copy import deepcopy
from dataclasses import FrozenInstanceError
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

# Third Party Library
import pytest

# Package Library
from kgfeg.kgs.lc_export import _validate_merged_graph
from kgfeg.kgs.lp_index import LPGraphIndex, build_lp_graph_index
from kgfeg.kgs.schemas import (
    AcademicStandardsExportSummary,
    AcademicStandardsKGBundle,
    AcademicStandardsLCExportSummary,
    AcademicStandardsLCKGBundle,
    AcademicStandardsLCUnresolvedItems,
    AcademicStandardsUnresolvedItems,
    AcademicStandardsValidationReport,
    LCGenerationSummary,
    LCUnresolvedItems,
    LearningComponent,
    Relationship,
    StandardsFramework,
    StandardsFrameworkItem,
)
from tests.constants import PARAM
from tests.fixtures.lp.loader import (
    LP_FIXTURES_DIR,
    FixtureItem,
    FixtureLearningComponent,
    FixtureRelationship,
    LPRegressionFixture,
    load_lp_regression_fixture,
)

_DANGLING_UUID = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")


def _build_bundle(
    *,
    extra_has_child_relationships: tuple[Relationship, ...] = (),
    extra_items: tuple[StandardsFrameworkItem, ...] = (),
    extra_support_relationships: tuple[Relationship, ...] = (),
    fixture: LPRegressionFixture,
    validation_errors: tuple[str, ...] = (),
    validation_passed: bool = True,
) -> AcademicStandardsLCKGBundle:
    """Promote one reduced fixture into a valid synthetic AS+LC bundle.

    The approved reduced projections retain only load-bearing edges. Synthetic
    resolved framework attachments are added for positive hierarchy roots so the
    resulting bundle satisfies the complete upstream attachment contract.

    Parameters
    ----------
    extra_has_child_relationships
        Additional valid hierarchy relationships to add before root attachment.
    extra_items
        Additional complete SFIs to include in the synthetic bundle.
    extra_support_relationships
        Additional valid LC-to-SFI support relationships to index.
    fixture
        Approved reduced curriculum fixture to promote.
    validation_errors
        Upstream validation errors to place in the report.
    validation_passed
        Whether the upstream validation report passed.

    Returns
    -------
    AcademicStandardsLCKGBundle
        Complete synthetic bundle using production schemas.
    """

    framework = _make_framework(fixture.framework)
    items = [_make_item(item) for item in fixture.items] + list(extra_items)
    learning_components = [
        _make_learning_component(component) for component in fixture.learning_components
    ]
    relationships_has_child = [
        _make_relationship(relationship)
        for relationship in fixture.relationships_has_child
    ] + list(extra_has_child_relationships)
    relationships_supports = [
        _make_relationship(relationship)
        for relationship in fixture.relationships_supports
    ] + list(extra_support_relationships)
    for component in learning_components:
        additional_source_uuids = {
            relationship.target_entity_value
            for relationship in extra_support_relationships
            if relationship.source_entity_value == str(component.identifier)
        }
        if additional_source_uuids:
            component.metadata["source_sfi_uuids"] = sorted(
                set(component.metadata["source_sfi_uuids"]) | additional_source_uuids
            )
    attached_sfi_uuids = {
        UUID(relationship.target_entity_value)
        for relationship in relationships_has_child
    }

    for item in items:
        if item.case_identifier_uuid in attached_sfi_uuids:
            continue

        relationships_has_child.append(
            _make_relationship(
                identifier=uuid5(
                    NAMESPACE_URL,
                    "resolved-root:"
                    f"{framework.case_identifier_uuid}:{item.case_identifier_uuid}",
                ),
                metadata={"unresolved_root_fallback": False},
                relationship_type="hasChild",
                source_entity="StandardsFramework",
                source_entity_key="case_identifier_uuid",
                source_entity_value=framework.case_identifier_uuid,
                target_entity="StandardsFrameworkItem",
                target_entity_key="case_identifier_uuid",
                target_entity_value=item.case_identifier_uuid,
            )
        )

    unresolved_relationships = [
        relationship.model_dump(mode="json")
        for relationship in relationships_has_child
        if relationship.metadata.get("unresolved_root_fallback") is True
    ]
    academic_standards_summary = AcademicStandardsExportSummary(
        final_sfi_count=len(items),
        finalization_exclusion_summary={},
        framework_count=1,
        has_child_relationship_count=len(relationships_has_child),
        learning_commons_node_count=1 + len(items),
        learning_commons_relationship_count=len(relationships_has_child),
        learning_commons_unresolved_fallback_relationship_count=len(
            unresolved_relationships
        ),
        relationship_unresolved_edge_count=len(unresolved_relationships),
    )
    learning_components_summary = LCGenerationSummary(
        lc_dedup_candidate_pair_count=0,
        lc_dedup_conflict_count=0,
        lc_dedup_judged_same_count=0,
        lc_generation_failed_sfis_count=0,
        lc_max_splits_observed=1 if learning_components else 0,
        lc_multi_claim_lc_count=0,
        lc_multi_parent_lc_count=sum(
            len(component.metadata["source_sfi_uuids"]) > 1
            for component in learning_components
        ),
        lc_selection_mode="explicit_allowlist",
        llm_request_count=0,
        llm_response_count=0,
        total_lc_claims=len(relationships_supports),
        total_lc_source_sfis_considered=len(items),
        total_lc_source_sfis_eligible=len(items),
        total_lc_source_sfis_empty_text=0,
        total_lc_source_sfis_excluded=0,
        total_lcs=len(learning_components),
        total_supports_edges=len(relationships_supports),
    )
    entity_provenance = {
        "framework": {"doc_key": fixture.source_bundle.doc_key},
        "items": {
            str(item.case_identifier_uuid): deepcopy(item.metadata) for item in items
        },
        "kg_run_manifest": {"doc_key": fixture.source_bundle.doc_key},
        "learning_components": {
            str(component.identifier): deepcopy(component.metadata)
            for component in learning_components
        },
        "relationships_has_child": {},
    }

    return AcademicStandardsLCKGBundle(
        entity_provenance=entity_provenance,
        framework=framework,
        items=items,
        learning_components=learning_components,
        relationships_has_child=relationships_has_child,
        relationships_supports=relationships_supports,
        summary=AcademicStandardsLCExportSummary(
            academic_standards=academic_standards_summary,
            learning_components=learning_components_summary,
            total_node_count=1 + len(items) + len(learning_components),
            total_relationship_count=(
                len(relationships_has_child) + len(relationships_supports)
            ),
        ),
        unresolved_items=AcademicStandardsLCUnresolvedItems(
            academic_standards=AcademicStandardsUnresolvedItems(
                relationship_unresolved_edges=unresolved_relationships
            ),
            learning_components=LCUnresolvedItems(),
        ),
        validation_report=AcademicStandardsValidationReport(
            errors=list(validation_errors),
            learning_commons_export_schema_version="test-schema-v1",
            object_counts={
                "frameworks": 1,
                "learning_components": len(learning_components),
                "relationships_has_child": len(relationships_has_child),
                "relationships_supports": len(relationships_supports),
                "standards_framework_items": len(items),
            },
            passed=validation_passed,
            validation_checks=["synthetic AS+LC fixture validation passed"],
        ),
    )


def _index_snapshot(index: LPGraphIndex) -> dict[str, Any]:
    """Serialize index structure without deriving expected graph semantics.

    Parameters
    ----------
    index
        Graph index to snapshot for permutation comparison.

    Returns
    -------
    dict[str, Any]
        JSON-comparable deterministic index view.
    """

    return {
        "ancestor_paths": {
            str(sfi_uuid): {
                "paths": [
                    {
                        "ancestor_sfi_uuids": [
                            str(ancestor_uuid)
                            for ancestor_uuid in path.ancestor_sfi_uuids
                        ],
                        "depth_truncated": path.depth_truncated,
                    }
                    for path in paths.paths
                ],
                "paths_truncated": paths.paths_truncated,
            }
            for sfi_uuid in index.sfi_by_uuid
            for paths in (
                index.ancestor_paths(
                    max_depth=max(1, len(index.sfi_by_uuid)),
                    max_paths=1_000,
                    sfi_uuid=sfi_uuid,
                ),
            )
        },
        "children": {
            str(parent_uuid): [str(child_uuid) for child_uuid in child_uuids]
            for parent_uuid, child_uuids in index.child_sfi_uuids_by_parent_sfi_uuid.items()
        },
        "framework_children": [
            str(sfi_uuid) for sfi_uuid in index.framework_child_sfi_uuids
        ],
        "learning_components": [
            str(component_uuid) for component_uuid in index.learning_component_by_uuid
        ],
        "learning_components_by_sfi": {
            str(sfi_uuid): [str(component.identifier) for component in components]
            for sfi_uuid, components in index.learning_components_by_sfi_uuid.items()
        },
        "parents": {
            str(child_uuid): [str(parent_uuid) for parent_uuid in parent_uuids]
            for child_uuid, parent_uuids in index.parent_sfi_uuids_by_sfi_uuid.items()
        },
        "provenance": {
            str(sfi_uuid): dict(provenance)
            for sfi_uuid, provenance in index.sfi_provenance_by_uuid.items()
        },
        "root_fallbacks": [str(sfi_uuid) for sfi_uuid in index.root_fallback_sfi_uuids],
        "root_fallback_relationships": {
            str(sfi_uuid): [
                str(relationship.identifier) for relationship in relationships
            ]
            for sfi_uuid, relationships in (
                index.root_fallback_relationships_by_sfi_uuid.items()
            )
        },
        "sfis": [str(sfi_uuid) for sfi_uuid in index.sfi_by_uuid],
        "sfis_by_learning_component": {
            str(component_uuid): [
                str(sfi.case_identifier_uuid) for sfi in supported_sfis
            ]
            for component_uuid, supported_sfis in (
                index.sfis_by_learning_component_uuid.items()
            )
        },
        "unresolved_ancestry": sorted(
            str(sfi_uuid) for sfi_uuid in index.unresolved_ancestry_sfi_uuids
        ),
    }


def _make_framework(fixture_framework: Any) -> StandardsFramework:
    """Create one complete framework from a reduced fixture record.

    Parameters
    ----------
    fixture_framework
        Reduced framework identity and metadata.

    Returns
    -------
    StandardsFramework
        Schema-valid framework node.
    """

    case_uuid = fixture_framework.case_identifier_uuid
    return StandardsFramework(
        academic_subject=fixture_framework.academic_subject,
        adoption_status="Adopted",
        attribution_statement="Synthetic test attribution",
        author="Synthetic curriculum authority",
        case_identifier_uri=f"urn:uuid:{case_uuid}",
        case_identifier_uuid=case_uuid,
        identifier=uuid5(NAMESPACE_URL, f"framework-identifier:{case_uuid}"),
        in_language="en",
        jurisdiction="Synthetic jurisdiction",
        license="Synthetic test license",
        metadata=deepcopy(fixture_framework.metadata),
        name=fixture_framework.name,
        provider="Synthetic test provider",
    )


def _make_item(fixture_item: FixtureItem) -> StandardsFrameworkItem:
    """Create one complete SFI from a reduced fixture record.

    Parameters
    ----------
    fixture_item
        Reduced item content and metadata.

    Returns
    -------
    StandardsFrameworkItem
        Schema-valid SFI node.
    """

    case_uuid = fixture_item.case_identifier_uuid
    return StandardsFrameworkItem(
        academic_subject="Synthetic subject",
        attribution_statement="Synthetic test attribution",
        author="Synthetic curriculum authority",
        case_identifier_uri=f"urn:uuid:{case_uuid}",
        case_identifier_uuid=case_uuid,
        description=fixture_item.description,
        identifier=uuid5(NAMESPACE_URL, f"sfi-identifier:{case_uuid}"),
        in_language="en",
        jurisdiction="Synthetic jurisdiction",
        license="Synthetic test license",
        metadata=deepcopy(fixture_item.metadata),
        normalized_statement_type=fixture_item.normalized_statement_type,
        provider="Synthetic test provider",
        statement_code=fixture_item.statement_code,
        statement_type=fixture_item.statement_type,
    )


def _make_learning_component(
    fixture_component: FixtureLearningComponent,
) -> LearningComponent:
    """Create one complete Learning Component from a reduced fixture record.

    Parameters
    ----------
    fixture_component
        Reduced Learning Component content and metadata.

    Returns
    -------
    LearningComponent
        Schema-valid Learning Component node.
    """

    return LearningComponent(
        academic_subject="Synthetic subject",
        attribution_statement="Synthetic test attribution",
        author="Synthetic curriculum authority",
        description=fixture_component.description,
        identifier=fixture_component.identifier,
        in_language="en",
        license="Synthetic test license",
        metadata=deepcopy(fixture_component.metadata),
        provider="Synthetic test provider",
    )


def _make_relationship(
    fixture_relationship: FixtureRelationship | None = None,
    *,
    identifier: UUID | None = None,
    metadata: dict[str, Any] | None = None,
    relationship_type: str | None = None,
    source_entity: str | None = None,
    source_entity_key: str | None = None,
    source_entity_value: UUID | None = None,
    target_entity: str | None = None,
    target_entity_key: str | None = None,
    target_entity_value: UUID | None = None,
) -> Relationship:
    """Create one complete relationship from a fixture or explicit endpoints.

    Parameters
    ----------
    fixture_relationship
        Optional reduced relationship supplying all graph-specific values.
    identifier
        Explicit relationship UUID when no fixture relationship is supplied.
    metadata
        Explicit relationship metadata.
    relationship_type
        Explicit relationship type.
    source_entity
        Explicit source entity type.
    source_entity_key
        Explicit source endpoint key.
    source_entity_value
        Explicit source endpoint UUID.
    target_entity
        Explicit target entity type.
    target_entity_key
        Explicit target endpoint key.
    target_entity_value
        Explicit target endpoint UUID.

    Returns
    -------
    Relationship
        Schema-valid relationship record.

    Raises
    ------
    ValueError
        If neither a fixture relationship nor complete explicit values are supplied.
    """

    if fixture_relationship is not None:
        identifier = fixture_relationship.identifier
        metadata = deepcopy(fixture_relationship.metadata)
        relationship_type = fixture_relationship.relationship_type
        source_entity = fixture_relationship.source_entity
        source_entity_key = fixture_relationship.source_entity_key
        source_entity_value = fixture_relationship.source_entity_value
        target_entity = fixture_relationship.target_entity
        target_entity_key = fixture_relationship.target_entity_key
        target_entity_value = fixture_relationship.target_entity_value

    values = (
        identifier,
        relationship_type,
        source_entity,
        source_entity_key,
        source_entity_value,
        target_entity,
        target_entity_key,
        target_entity_value,
    )
    if any(value is None for value in values):
        raise ValueError("Complete explicit relationship values are required.")

    return Relationship(
        attribution_statement="Synthetic test attribution",
        author="Synthetic curriculum authority",
        identifier=identifier,
        license="Synthetic test license",
        metadata=metadata or {},
        provider="Synthetic test provider",
        relationship_type=relationship_type,
        source_entity=source_entity,
        source_entity_key=source_entity_key,
        source_entity_value=str(source_entity_value),
        target_entity=target_entity,
        target_entity_key=target_entity_key,
        target_entity_value=str(target_entity_value),
    )


def _permuted_bundle(
    bundle: AcademicStandardsLCKGBundle,
) -> AcademicStandardsLCKGBundle:
    """Reverse every order-bearing input collection and provenance mapping.

    Parameters
    ----------
    bundle
        Bundle whose encounter order should be permuted.

    Returns
    -------
    AcademicStandardsLCKGBundle
        Copy with logically identical content in reverse input order.
    """

    provenance = deepcopy(bundle.entity_provenance)
    provenance["items"] = dict(reversed(list(provenance["items"].items())))
    return bundle.model_copy(
        update={
            "entity_provenance": provenance,
            "items": list(reversed(bundle.items)),
            "learning_components": list(reversed(bundle.learning_components)),
            "relationships_has_child": list(reversed(bundle.relationships_has_child)),
            "relationships_supports": list(reversed(bundle.relationships_supports)),
        }
    )


@PARAM(
    argnames=("max_depth", "max_paths"),
    argvalues=((0, 1), (1, 0), (True, 1), (1, False), (1.5, 1), (1, "2")),
)
def test_ancestor_paths_reject_invalid_bounds(
    *, max_depth: Any, max_paths: Any
) -> None:
    """Traversal bounds must be positive integers rather than coercible values.

    Parameters
    ----------
    max_depth
        Invalid ancestor-depth bound.
    max_paths
        Invalid ancestor-path-count bound.
    """

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "nigeria_math.json")
    index = build_lp_graph_index(_build_bundle(fixture=fixture))
    sfi_uuid = fixture.items[-1].case_identifier_uuid

    with pytest.raises(expected_exception=ValueError, match="positive integer"):
        index.ancestor_paths(
            max_depth=max_depth,
            max_paths=max_paths,
            sfi_uuid=sfi_uuid,
        )


def test_ancestor_paths_reject_unknown_sfi() -> None:
    """Traversal fails closed instead of returning empty context for an unknown SFI."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "nigeria_math.json")
    index = build_lp_graph_index(_build_bundle(fixture=fixture))

    with pytest.raises(expected_exception=ValueError, match="Unknown SFI CASE UUID"):
        index.ancestor_paths(
            max_depth=1,
            max_paths=1,
            sfi_uuid=_DANGLING_UUID,
        )


def test_ancestor_paths_report_depth_and_path_truncation() -> None:
    """Depth and path-count bounds expose independent truncation states."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "pratham_science.json")
    item_by_type = {item.statement_type: item for item in fixture.items}
    index = build_lp_graph_index(_build_bundle(fixture=fixture))
    child_uuid = item_by_type["Indicator"].case_identifier_uuid
    direct_parent_uuid = item_by_type[
        "Content Domain Specific Learning Outcome"
    ].case_identifier_uuid

    depth_bounded = index.ancestor_paths(max_depth=1, max_paths=10, sfi_uuid=child_uuid)
    path_bounded = index.ancestor_paths(max_depth=2, max_paths=1, sfi_uuid=child_uuid)
    complete = index.ancestor_paths(max_depth=2, max_paths=2, sfi_uuid=child_uuid)

    assert [
        (path.ancestor_sfi_uuids, path.depth_truncated) for path in depth_bounded.paths
    ] == [((direct_parent_uuid,), True)]
    assert depth_bounded.paths_truncated is False
    assert len(path_bounded.paths) == 1
    assert path_bounded.paths[0].depth_truncated is False
    assert path_bounded.paths_truncated is True
    assert len(complete.paths) == 2
    assert all(path.depth_truncated is False for path in complete.paths)
    assert complete.paths_truncated is False


def test_ancestor_paths_retain_pratham_multi_parent_branches() -> None:
    """Pratham ancestry retains both parent paths instead of choosing one."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "pratham_science.json")
    item_by_type = {item.statement_type: item for item in fixture.items}
    index = build_lp_graph_index(_build_bundle(fixture=fixture))
    chapter_uuid = item_by_type["Chapter"].case_identifier_uuid
    child_uuid = item_by_type["Indicator"].case_identifier_uuid
    direct_parent_uuid = item_by_type[
        "Content Domain Specific Learning Outcome"
    ].case_identifier_uuid
    ncert_uuid = item_by_type["NCERT Learning Outcome"].case_identifier_uuid

    assert index.parent_sfi_uuids_by_sfi_uuid[direct_parent_uuid] == tuple(
        sorted((chapter_uuid, ncert_uuid), key=str)
    )
    assert index.child_sfi_uuids_by_parent_sfi_uuid[chapter_uuid] == (
        direct_parent_uuid,
    )
    assert index.child_sfi_uuids_by_parent_sfi_uuid[ncert_uuid] == (direct_parent_uuid,)
    assert index.child_sfi_uuids_by_parent_sfi_uuid[direct_parent_uuid] == (child_uuid,)
    paths = index.ancestor_paths(max_depth=4, max_paths=4, sfi_uuid=child_uuid)
    assert [path.ancestor_sfi_uuids for path in paths.paths] == [
        (direct_parent_uuid, chapter_uuid),
        (direct_parent_uuid, ncert_uuid),
    ]


def test_ancestor_paths_retain_shared_ancestors_on_each_dag_path() -> None:
    """A shared ancestor remains present in every distinct converging DAG path."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "pratham_science.json")
    item_by_type = {item.statement_type: item for item in fixture.items}
    chapter_uuid = item_by_type["Chapter"].case_identifier_uuid
    child_uuid = item_by_type[
        "Content Domain Specific Learning Outcome"
    ].case_identifier_uuid
    ncert_uuid = item_by_type["NCERT Learning Outcome"].case_identifier_uuid
    shared_uuid = UUID("00000000-0000-4000-8000-000000000001")
    shared_ancestor = _make_item(item_by_type["Chapter"]).model_copy(
        update={
            "case_identifier_uri": f"urn:uuid:{shared_uuid}",
            "case_identifier_uuid": shared_uuid,
            "description": "Synthetic shared ancestor",
            "identifier": uuid5(NAMESPACE_URL, f"sfi-identifier:{shared_uuid}"),
            "statement_type": "Shared Scope",
        }
    )
    shared_edges = (
        _make_relationship(
            identifier=UUID("00000000-0000-4000-8000-000000000002"),
            metadata={"unresolved_root_fallback": False},
            relationship_type="hasChild",
            source_entity="StandardsFrameworkItem",
            source_entity_key="case_identifier_uuid",
            source_entity_value=shared_uuid,
            target_entity="StandardsFrameworkItem",
            target_entity_key="case_identifier_uuid",
            target_entity_value=chapter_uuid,
        ),
        _make_relationship(
            identifier=UUID("00000000-0000-4000-8000-000000000003"),
            metadata={"unresolved_root_fallback": False},
            relationship_type="hasChild",
            source_entity="StandardsFrameworkItem",
            source_entity_key="case_identifier_uuid",
            source_entity_value=shared_uuid,
            target_entity="StandardsFrameworkItem",
            target_entity_key="case_identifier_uuid",
            target_entity_value=ncert_uuid,
        ),
    )
    bundle = _build_bundle(
        extra_has_child_relationships=shared_edges,
        extra_items=(shared_ancestor,),
        fixture=fixture,
    )
    index = build_lp_graph_index(bundle)

    paths = index.ancestor_paths(max_depth=3, max_paths=3, sfi_uuid=child_uuid)

    assert [path.ancestor_sfi_uuids for path in paths.paths] == [
        (chapter_uuid, shared_uuid),
        (ncert_uuid, shared_uuid),
    ]
    assert paths.paths_truncated is False


@PARAM(
    argnames="fixture_filename",
    argvalues=("ghana_math.json", "pratham_science.json", "rwanda_math.json"),
)
def test_index_is_deterministic_under_input_permutations(
    fixture_filename: str,
) -> None:
    """Every exposed index is stable under node, edge, and mapping reordering.

    Parameters
    ----------
    fixture_filename
        Reduced fixture covering fallback, DAG, or reused-LC index behavior.
    """

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / fixture_filename)
    bundle = _build_bundle(fixture=fixture)

    forward = build_lp_graph_index(bundle)
    reversed_index = build_lp_graph_index(_permuted_bundle(bundle))

    assert _index_snapshot(forward) == _index_snapshot(reversed_index)
    assert list(forward.sfi_by_uuid) == sorted(forward.sfi_by_uuid, key=str)
    assert list(forward.learning_component_by_uuid) == sorted(
        forward.learning_component_by_uuid, key=str
    )


@PARAM(
    argnames="fixture_filename",
    argvalues=(
        "ghana_english.json",
        "ghana_math.json",
        "madhi_math.json",
        "nigeria_math.json",
        "pratham_science.json",
        "rwanda_math.json",
    ),
)
def test_index_preserves_existing_as_and_as_lc_contracts(
    fixture_filename: str,
) -> None:
    """Index construction preserves valid upstream bundles for every curriculum.

    Parameters
    ----------
    fixture_filename
        Reduced fixture used to verify the upstream compatibility contract.
    """

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / fixture_filename)
    as_lc_bundle = _build_bundle(fixture=fixture)
    as_bundle = AcademicStandardsKGBundle(
        entity_provenance=deepcopy(as_lc_bundle.entity_provenance),
        framework=as_lc_bundle.framework,
        items=list(as_lc_bundle.items),
        relationships_has_child=list(as_lc_bundle.relationships_has_child),
        summary=as_lc_bundle.summary.academic_standards,
        unresolved_items=as_lc_bundle.unresolved_items.academic_standards,
        validation_report=as_lc_bundle.validation_report,
    )
    as_before = as_bundle.model_dump(mode="json")
    as_lc_before = as_lc_bundle.model_dump(mode="json")
    assert not _validate_merged_graph(
        academic_standards_bundle=as_bundle,
        lc_generation_summary=as_lc_bundle.summary.learning_components,
        learning_components=as_lc_bundle.learning_components,
        merged_entity_provenance=as_lc_bundle.entity_provenance,
        supports_edges=as_lc_bundle.relationships_supports,
    )

    build_lp_graph_index(as_lc_bundle)

    assert as_bundle.model_dump(mode="json") == as_before
    assert as_lc_bundle.model_dump(mode="json") == as_lc_before
    assert (
        AcademicStandardsKGBundle.model_validate(as_before).model_dump(mode="json")
        == as_before
    )
    assert (
        AcademicStandardsLCKGBundle.model_validate(as_lc_before).model_dump(mode="json")
        == as_lc_before
    )


def test_index_preserves_tree_ancestry_and_framework_attachment() -> None:
    """Nigeria's explicit-grade tree retains its full root-to-leaf structure."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "nigeria_math.json")
    item_by_type = {item.statement_type: item for item in fixture.items}
    index = build_lp_graph_index(_build_bundle(fixture=fixture))
    performance_uuid = item_by_type["Performance Objective"].case_identifier_uuid

    assert index.framework_child_sfi_uuids == (
        item_by_type["Grade"].case_identifier_uuid,
    )
    assert [
        path.ancestor_sfi_uuids
        for path in index.ancestor_paths(
            max_depth=10, max_paths=10, sfi_uuid=performance_uuid
        ).paths
    ] == [
        (
            item_by_type["Topic"].case_identifier_uuid,
            item_by_type["Sub-Theme"].case_identifier_uuid,
            item_by_type["Theme"].case_identifier_uuid,
            item_by_type["Grade"].case_identifier_uuid,
        )
    ]


def test_indexes_are_immutable_at_their_collection_boundaries() -> None:
    """Callers cannot mutate adjacency, provenance records, or frozen index fields."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "nigeria_math.json")
    index = build_lp_graph_index(_build_bundle(fixture=fixture))
    sfi_uuid = fixture.items[0].case_identifier_uuid

    with pytest.raises(expected_exception=TypeError):
        index.parent_sfi_uuids_by_sfi_uuid[sfi_uuid] = ()  # type: ignore[index]

    with pytest.raises(expected_exception=TypeError):
        index.sfi_provenance_by_uuid[sfi_uuid]["audit_flags"] = []  # type: ignore[index]

    with pytest.raises(expected_exception=FrozenInstanceError):
        index.framework_child_sfi_uuids = ()  # type: ignore[misc]


def test_nearest_ancestors_are_branch_specific() -> None:
    """A direct match on one DAG branch does not hide a deeper match on another."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "pratham_science.json")
    item_by_type = {item.statement_type: item for item in fixture.items}
    modified_items = [
        (
            item.model_copy(update={"statement_type": "Intermediate grouping"})
            if item.statement_type == "Chapter"
            else (
                item.model_copy(update={"statement_type": "Coordinate"})
                if item.statement_type == "NCERT Learning Outcome"
                else item
            )
        )
        for item in fixture.items
    ]
    modified_fixture = fixture.model_copy(update={"items": modified_items})
    higher_uuid = UUID("00000000-0000-4000-8000-000000000010")
    higher = _make_item(item_by_type["Chapter"]).model_copy(
        update={
            "case_identifier_uri": f"urn:uuid:{higher_uuid}",
            "case_identifier_uuid": higher_uuid,
            "description": "Synthetic higher coordinate",
            "identifier": uuid5(NAMESPACE_URL, f"sfi-identifier:{higher_uuid}"),
            "statement_type": "Coordinate",
        }
    )
    higher_to_chapter = _make_relationship(
        identifier=UUID("00000000-0000-4000-8000-000000000012"),
        metadata={"unresolved_root_fallback": False},
        relationship_type="hasChild",
        source_entity="StandardsFrameworkItem",
        source_entity_key="case_identifier_uuid",
        source_entity_value=higher_uuid,
        target_entity="StandardsFrameworkItem",
        target_entity_key="case_identifier_uuid",
        target_entity_value=item_by_type["Chapter"].case_identifier_uuid,
    )
    graph_bundle = _build_bundle(
        extra_has_child_relationships=(higher_to_chapter,),
        extra_items=(higher,),
        fixture=modified_fixture,
    )
    index = build_lp_graph_index(graph_bundle)
    child_uuid = item_by_type[
        "Content Domain Specific Learning Outcome"
    ].case_identifier_uuid

    assert index.nearest_ancestor_uuids(
        sfi_uuid=child_uuid, statement_type="Coordinate"
    ) == tuple(
        sorted(
            (
                higher_uuid,
                item_by_type["NCERT Learning Outcome"].case_identifier_uuid,
            ),
            key=str,
        )
    )


@PARAM(argnames="statement_type", argvalues=("", "   ", None, 7))
def test_nearest_ancestors_reject_invalid_statement_type(
    statement_type: Any,
) -> None:
    """A missing or non-string local statement type cannot produce context.

    Parameters
    ----------
    statement_type
        Invalid caller-supplied local statement type.
    """

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "nigeria_math.json")
    index = build_lp_graph_index(_build_bundle(fixture=fixture))

    with pytest.raises(expected_exception=ValueError, match="non-empty string"):
        index.nearest_ancestor_uuids(
            sfi_uuid=fixture.items[-1].case_identifier_uuid,
            statement_type=statement_type,
        )


def test_provenance_covers_every_sfi_and_preserves_source_audit_fields() -> None:
    """Every SFI retains its bounded source and audit provenance by CASE UUID."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "ghana_math.json")
    bundle = _build_bundle(fixture=fixture)
    index = build_lp_graph_index(bundle)
    anomalous_sfi = next(item for item in fixture.items if item.metadata["audit_flags"])
    provenance = index.sfi_provenance_by_uuid[anomalous_sfi.case_identifier_uuid]

    assert set(index.sfi_provenance_by_uuid) == {
        item.case_identifier_uuid for item in bundle.items
    }
    assert provenance["audit_flags"] == ["same_code_different_content"]
    assert provenance["audit_notes"] == anomalous_sfi.metadata["audit_notes"]
    assert provenance["source_page_indexes"] == [97]


@PARAM(argnames="malformation", argvalues=("malformed", "missing", "unknown"))
def test_provenance_rejects_malformed_missing_or_unknown_entries(
    malformation: str,
) -> None:
    """SFI provenance must be a complete one-to-one UUID-keyed mapping.

    Parameters
    ----------
    malformation
        Provenance defect to inject.
    """

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "nigeria_math.json")
    bundle = _build_bundle(fixture=fixture)
    provenance = deepcopy(bundle.entity_provenance)
    first_uuid = str(bundle.items[0].case_identifier_uuid)
    if malformation == "malformed":
        provenance["items"][first_uuid] = ["not", "a", "mapping"]
    elif malformation == "missing":
        del provenance["items"][first_uuid]
    else:
        provenance["items"][str(_DANGLING_UUID)] = {"audit_flags": []}

    with pytest.raises(expected_exception=ValueError):
        build_lp_graph_index(
            bundle.model_copy(update={"entity_provenance": provenance})
        )


def test_root_fallback_is_isolated_and_propagates_unresolved_ancestry() -> None:
    """A fallback-affected DAG branch taints descendants but not resolved parents."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "ghana_math.json")
    item_by_type = {item.statement_type: item for item in fixture.items}
    fallback_uuid = fixture.expectations.unresolved_item_ids[0]
    content_uuid = item_by_type["Content Standard"].case_identifier_uuid
    indicator_uuid = item_by_type["Indicator"].case_identifier_uuid
    resolved_parent_uuid = UUID("00000000-0000-4000-8000-000000000021")
    resolved_parent = _make_item(item_by_type["Content Standard"]).model_copy(
        update={
            "case_identifier_uri": f"urn:uuid:{resolved_parent_uuid}",
            "case_identifier_uuid": resolved_parent_uuid,
            "description": "Independent resolved hierarchy branch",
            "identifier": UUID("00000000-0000-4000-8000-000000000022"),
            "statement_code": "RESOLVED.BRANCH",
        }
    )
    fallback_to_content = _make_relationship(
        identifier=UUID("00000000-0000-4000-8000-000000000020"),
        metadata={"unresolved_root_fallback": False},
        relationship_type="hasChild",
        source_entity="StandardsFrameworkItem",
        source_entity_key="case_identifier_uuid",
        source_entity_value=fallback_uuid,
        target_entity="StandardsFrameworkItem",
        target_entity_key="case_identifier_uuid",
        target_entity_value=content_uuid,
    )
    resolved_parent_to_indicator = _make_relationship(
        identifier=UUID("00000000-0000-4000-8000-000000000023"),
        metadata={"unresolved_root_fallback": False},
        relationship_type="hasChild",
        source_entity="StandardsFrameworkItem",
        source_entity_key="case_identifier_uuid",
        source_entity_value=resolved_parent_uuid,
        target_entity="StandardsFrameworkItem",
        target_entity_key="case_identifier_uuid",
        target_entity_value=indicator_uuid,
    )
    index = build_lp_graph_index(
        _build_bundle(
            extra_has_child_relationships=(
                fallback_to_content,
                resolved_parent_to_indicator,
            ),
            extra_items=(resolved_parent,),
            fixture=fixture,
        )
    )

    assert index.framework_child_sfi_uuids == (resolved_parent_uuid,)
    assert index.parent_sfi_uuids_by_sfi_uuid[fallback_uuid] == ()
    assert index.parent_sfi_uuids_by_sfi_uuid[indicator_uuid] == tuple(
        sorted((content_uuid, resolved_parent_uuid), key=str)
    )
    assert index.root_fallback_sfi_uuids == (fallback_uuid,)
    assert len(index.root_fallback_relationships_by_sfi_uuid[fallback_uuid]) == 1
    assert index.unresolved_ancestry_sfi_uuids == frozenset(
        (content_uuid, fallback_uuid, indicator_uuid)
    )
    assert resolved_parent_uuid not in index.unresolved_ancestry_sfi_uuids
    assert {
        path.ancestor_sfi_uuids
        for path in index.ancestor_paths(
            max_depth=5, max_paths=5, sfi_uuid=indicator_uuid
        ).paths
    } == {(content_uuid, fallback_uuid), (resolved_parent_uuid,)}


def test_support_indexes_are_bidirectional_complete_and_uuid_ordered() -> None:
    """Rwanda's reused LC maps to every SFI in both deterministic directions."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "rwanda_math.json")
    bundle = _build_bundle(fixture=fixture)
    index = build_lp_graph_index(bundle)
    component_uuid = fixture.learning_components[0].identifier
    expected_sfi_uuids = tuple(
        sorted(fixture.expectations.lc_alignments[component_uuid], key=str)
    )
    indexed_sfi_uuids = tuple(
        sfi.case_identifier_uuid
        for sfi in index.sfis_by_learning_component_uuid[component_uuid]
    )

    assert indexed_sfi_uuids == expected_sfi_uuids
    for sfi_uuid in expected_sfi_uuids:
        assert tuple(
            component.identifier
            for component in index.learning_components_by_sfi_uuid[sfi_uuid]
        ) == (component_uuid,)

    unsupported_sfi_uuids = set(index.sfi_by_uuid) - set(expected_sfi_uuids)
    assert unsupported_sfi_uuids
    assert all(
        index.learning_components_by_sfi_uuid[sfi_uuid] == ()
        for sfi_uuid in unsupported_sfi_uuids
    )


def test_support_indexes_preserve_multiple_components_per_sfi() -> None:
    """Many-to-many support indexes retain every LC and SFI without overwrite."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "ghana_english.json")
    first_component_uuid = fixture.learning_components[0].identifier
    second_component_uuid = fixture.learning_components[1].identifier
    shared_sfi_uuid = fixture.expectations.lc_alignments[first_component_uuid][0]
    additional_support = _make_relationship(
        identifier=UUID("00000000-0000-4000-8000-000000000024"),
        metadata={"support_role": "primary"},
        relationship_type="supports",
        source_entity="LearningComponent",
        source_entity_key="identifier",
        source_entity_value=second_component_uuid,
        target_entity="StandardsFrameworkItem",
        target_entity_key="case_identifier_uuid",
        target_entity_value=shared_sfi_uuid,
    )
    bundle = _build_bundle(
        extra_support_relationships=(additional_support,), fixture=fixture
    )
    index = build_lp_graph_index(_permuted_bundle(bundle))

    assert tuple(
        component.identifier
        for component in index.learning_components_by_sfi_uuid[shared_sfi_uuid]
    ) == tuple(sorted((first_component_uuid, second_component_uuid), key=str))
    assert tuple(
        sfi.case_identifier_uuid
        for sfi in index.sfis_by_learning_component_uuid[second_component_uuid]
    ) == tuple(
        sorted(
            (
                *fixture.expectations.lc_alignments[second_component_uuid],
                shared_sfi_uuid,
            ),
            key=str,
        )
    )


@PARAM(
    argnames="duplicate_kind",
    argvalues=("has_child", "learning_component", "sfi", "supports"),
)
def test_validation_rejects_ambiguous_duplicate_nodes_and_logical_relationships(
    duplicate_kind: str,
) -> None:
    """Duplicate indexed identities or logical graph edges fail closed.

    Parameters
    ----------
    duplicate_kind
        Node identity or relationship pair to duplicate.
    """

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "nigeria_math.json")
    bundle = _build_bundle(fixture=fixture)
    updates: dict[str, Any] = {}
    if duplicate_kind == "has_child":
        duplicate = bundle.relationships_has_child[0].model_copy(
            update={"identifier": UUID("00000000-0000-4000-8000-000000000030")}
        )
        updates["relationships_has_child"] = [
            *bundle.relationships_has_child,
            duplicate,
        ]
    elif duplicate_kind == "learning_component":
        duplicate = bundle.learning_components[0].model_copy(
            update={"description": "Duplicate component identity"}
        )
        updates["learning_components"] = [*bundle.learning_components, duplicate]
    elif duplicate_kind == "sfi":
        duplicate = bundle.items[0].model_copy(
            update={
                "description": "Duplicate SFI CASE identity",
                "identifier": UUID("00000000-0000-4000-8000-000000000031"),
            }
        )
        updates["items"] = [*bundle.items, duplicate]
    else:
        duplicate = bundle.relationships_supports[0].model_copy(
            update={"identifier": UUID("00000000-0000-4000-8000-000000000032")}
        )
        updates["relationships_supports"] = [
            *bundle.relationships_supports,
            duplicate,
        ]

    with pytest.raises(expected_exception=ValueError, match="duplicate"):
        build_lp_graph_index(bundle.model_copy(update=updates))


@PARAM(
    argnames="dangling_kind",
    argvalues=(
        "has_child_source",
        "has_child_target",
        "supports_source",
        "supports_target",
    ),
)
def test_validation_rejects_dangling_relationship_endpoints(
    dangling_kind: str,
) -> None:
    """Every hierarchy and support endpoint must resolve to an indexed node.

    Parameters
    ----------
    dangling_kind
        Relationship endpoint to replace with an unknown UUID.
    """

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "nigeria_math.json")
    bundle = _build_bundle(fixture=fixture)
    if dangling_kind.startswith("has_child"):
        relationships = list(bundle.relationships_has_child)
        relationship_index = next(
            index
            for index, relationship in enumerate(relationships)
            if relationship.source_entity == "StandardsFrameworkItem"
        )
        field_name = (
            "source_entity_value"
            if dangling_kind.endswith("source")
            else "target_entity_value"
        )
        relationships[relationship_index] = relationships[
            relationship_index
        ].model_copy(update={field_name: str(_DANGLING_UUID)})
        malformed_bundle = bundle.model_copy(
            update={"relationships_has_child": relationships}
        )
    else:
        relationships = list(bundle.relationships_supports)
        field_name = (
            "source_entity_value"
            if dangling_kind.endswith("source")
            else "target_entity_value"
        )
        relationships[0] = relationships[0].model_copy(
            update={field_name: str(_DANGLING_UUID)}
        )
        malformed_bundle = bundle.model_copy(
            update={"relationships_supports": relationships}
        )

    with pytest.raises(expected_exception=ValueError, match="unknown"):
        build_lp_graph_index(malformed_bundle)


def test_validation_rejects_hierarchy_cycles() -> None:
    """A cycle hidden inside otherwise attached hierarchy input fails closed."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "nigeria_math.json")
    bundle = _build_bundle(fixture=fixture)
    grade_uuid = fixture.items[0].case_identifier_uuid
    leaf_uuid = fixture.items[-1].case_identifier_uuid
    cycle_edge = _make_relationship(
        identifier=UUID("00000000-0000-4000-8000-000000000040"),
        metadata={"unresolved_root_fallback": False},
        relationship_type="hasChild",
        source_entity="StandardsFrameworkItem",
        source_entity_key="case_identifier_uuid",
        source_entity_value=leaf_uuid,
        target_entity="StandardsFrameworkItem",
        target_entity_key="case_identifier_uuid",
        target_entity_value=grade_uuid,
    )

    with pytest.raises(expected_exception=ValueError, match="cycle"):
        build_lp_graph_index(
            bundle.model_copy(
                update={
                    "relationships_has_child": [
                        *bundle.relationships_has_child,
                        cycle_edge,
                    ]
                }
            )
        )


@PARAM(
    argnames=("collection", "malformation"),
    argvalues=(
        ("has_child", "endpoint_shape"),
        ("has_child", "relationship_type"),
        ("supports", "endpoint_shape"),
        ("supports", "relationship_type"),
    ),
)
def test_validation_rejects_invalid_relationship_shapes(
    *, collection: str, malformation: str
) -> None:
    """Relationships in each index input must retain their exact schema role.

    Parameters
    ----------
    collection
        Hierarchy or support relationship collection to corrupt.
    malformation
        Relationship type or endpoint-key defect to inject.
    """

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "nigeria_math.json")
    bundle = _build_bundle(fixture=fixture)
    if collection == "has_child":
        relationships = list(bundle.relationships_has_child)
        updates = (
            {"target_entity_key": "identifier"}
            if malformation == "endpoint_shape"
            else {"relationship_type": "supports"}
        )
        relationships[0] = relationships[0].model_copy(update=updates)
        malformed_bundle = bundle.model_copy(
            update={"relationships_has_child": relationships}
        )
    else:
        relationships = list(bundle.relationships_supports)
        updates = (
            {"target_entity_key": "identifier"}
            if malformation == "endpoint_shape"
            else {"relationship_type": "hasChild"}
        )
        relationships[0] = relationships[0].model_copy(update=updates)
        malformed_bundle = bundle.model_copy(
            update={"relationships_supports": relationships}
        )

    with pytest.raises(expected_exception=ValueError):
        build_lp_graph_index(malformed_bundle)


@PARAM(argnames="malformation", argvalues=("non_boolean", "sfi_edge"))
def test_validation_rejects_malformed_root_fallback_state(
    malformation: str,
) -> None:
    """Fallback state must be boolean and may mark only framework-root edges.

    Parameters
    ----------
    malformation
        Invalid fallback representation to inject.
    """

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "ghana_math.json")
    bundle = _build_bundle(fixture=fixture)
    relationships = list(bundle.relationships_has_child)
    if malformation == "non_boolean":
        fallback_index = next(
            index
            for index, relationship in enumerate(relationships)
            if relationship.metadata.get("unresolved_root_fallback") is True
        )
        relationships[fallback_index] = relationships[fallback_index].model_copy(
            update={"metadata": {"unresolved_root_fallback": "true"}}
        )
    else:
        hierarchy_index = next(
            index
            for index, relationship in enumerate(relationships)
            if relationship.source_entity == "StandardsFrameworkItem"
        )
        relationships[hierarchy_index] = relationships[hierarchy_index].model_copy(
            update={"metadata": {"unresolved_root_fallback": True}}
        )

    with pytest.raises(expected_exception=ValueError, match="fallback"):
        build_lp_graph_index(
            bundle.model_copy(update={"relationships_has_child": relationships})
        )


def test_validation_rejects_sfis_without_incoming_hierarchy_edges() -> None:
    """Every indexed SFI must remain attached through resolved or fallback input."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "nigeria_math.json")
    bundle = _build_bundle(fixture=fixture)
    target_uuid = fixture.items[-1].case_identifier_uuid
    relationships = [
        relationship
        for relationship in bundle.relationships_has_child
        if UUID(relationship.target_entity_value) != target_uuid
    ]

    with pytest.raises(expected_exception=ValueError, match="without an incoming"):
        build_lp_graph_index(
            bundle.model_copy(update={"relationships_has_child": relationships})
        )


@PARAM(
    argnames=("validation_errors", "validation_passed"),
    argvalues=(((), False), (("upstream count mismatch",), True)),
)
def test_validation_rejects_upstream_failure_or_errors(
    *, validation_errors: tuple[str, ...], validation_passed: bool
) -> None:
    """A failed or internally erroneous AS+LC report blocks LP indexing.

    Parameters
    ----------
    validation_errors
        Upstream validation errors to inject.
    validation_passed
        Upstream pass flag to inject.
    """

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "nigeria_math.json")
    bundle = _build_bundle(
        fixture=fixture,
        validation_errors=validation_errors,
        validation_passed=validation_passed,
    )

    with pytest.raises(expected_exception=ValueError, match="passed, error-free"):
        build_lp_graph_index(bundle)


def test_validation_rejects_zero_support_learning_components() -> None:
    """Every final Learning Component must retain at least one supports edge."""

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / "nigeria_math.json")
    bundle = _build_bundle(fixture=fixture)

    with pytest.raises(
        expected_exception=ValueError,
        match="Learning Components without supports edges",
    ):
        build_lp_graph_index(bundle.model_copy(update={"relationships_supports": []}))
