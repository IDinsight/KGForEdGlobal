"""Load and validate reduced six-curriculum AS+LC regression fixtures."""

# Future Library
from __future__ import annotations

# Standard Library
from collections import defaultdict
from collections.abc import Callable
from hashlib import sha256
from json import dumps
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field

# Package Library
from kgfeg.kgs.schemas import (
    AcademicStandardsLCExportSummary,
    AcademicStandardsLCKGBundle,
    AcademicStandardsLCUnresolvedItems,
    AcademicStandardsValidationReport,
    LearningComponent,
    Relationship,
    StandardsFramework,
    StandardsFrameworkItem,
)
from tests.constants import FIXTURES_DIR

AS_LC_BUNDLE_TOP_LEVEL_KEYS = frozenset(
    {
        "entity_provenance",
        "framework",
        "items",
        "learning_components",
        "relationships_has_child",
        "relationships_supports",
        "summary",
        "unresolved_items",
        "validation_report",
    }
)
APPROVED_REDUCED_PROJECTION_SHA256 = {
    "ghana_english": "97d7f69034ea2e5ed453bc2c1b01a18fe96f817e795d27b16735fd72cf267bf9",
    "ghana_math": "88fa718be3867fcf99df891393a5c9e18a0339bf4a2144d3bc9c6046bf471e62",
    "madhi_math": "276ecb35f5f7993f1b005f11aaee7732cbf6ee5845a27e3a557f4525cb8c5ee0",
    "nigeria_math": "d0abfdf8f45f71029eb3faa860bb46b6c0d31d136d88afc1bdac89387cc797b6",
    "pratham_science": "76dc1c3f84cf5ef35d198bc1b274b5673aad078cd6b1bc97bd58b63d271c6646",
    "rwanda_math": "4ce2b5f9509a08598f4067e3c77339c18d208608801ab6b1b8df9f10b3bcc835",
}
ENTITY_PROVENANCE_SECTIONS = frozenset(
    {
        "framework",
        "items",
        "kg_run_manifest",
        "learning_components",
        "relationships_has_child",
    }
)
FRAMEWORK_RECORD_KEYS = frozenset(
    {
        "academic_subject",
        "adoption_status",
        "attribution_statement",
        "author",
        "case_identifier_uri",
        "case_identifier_uuid",
        "date_created",
        "date_modified",
        "description",
        "identifier",
        "in_language",
        "is_current",
        "jurisdiction",
        "license",
        "metadata",
        "name",
        "notes",
        "provider",
    }
)
FIXTURE_FILENAMES = (
    "ghana_english.json",
    "ghana_math.json",
    "madhi_math.json",
    "nigeria_math.json",
    "pratham_science.json",
    "rwanda_math.json",
)
LP_FIXTURES_DIR = FIXTURES_DIR / "lp"
ITEM_RECORD_KEYS = frozenset(
    {
        "academic_subject",
        "alternate_statement_code",
        "attribution_statement",
        "author",
        "case_identifier_uri",
        "case_identifier_uuid",
        "date_created",
        "date_modified",
        "description",
        "grade_level",
        "identifier",
        "in_language",
        "is_current",
        "jurisdiction",
        "license",
        "metadata",
        "normalized_statement_type",
        "notes",
        "provider",
        "statement_code",
        "statement_type",
    }
)
LEARNING_COMPONENT_RECORD_KEYS = frozenset(
    {
        "academic_subject",
        "attribution_statement",
        "author",
        "date_created",
        "date_modified",
        "description",
        "identifier",
        "in_language",
        "license",
        "metadata",
        "provider",
    }
)
RELATIONSHIP_RECORD_KEYS = frozenset(
    {
        "attribution_statement",
        "author",
        "date_created",
        "date_modified",
        "description",
        "identifier",
        "license",
        "metadata",
        "provider",
        "relationship_type",
        "source_entity",
        "source_entity_key",
        "source_entity_value",
        "target_entity",
        "target_entity_key",
        "target_entity_value",
    }
)
SOURCE_OBJECT_COUNT_KEYS = frozenset(
    {
        "frameworks",
        "learning_components",
        "relationships_has_child",
        "relationships_supports",
        "standards_framework_items",
    }
)
SUMMARY_RECORD_KEYS = frozenset(
    {
        "academic_standards",
        "learning_components",
        "total_node_count",
        "total_relationship_count",
    }
)
UNRESOLVED_ITEMS_RECORD_KEYS = frozenset({"academic_standards", "learning_components"})
VALIDATION_REPORT_RECORD_KEYS = frozenset(
    {
        "errors",
        "input_fingerprints",
        "learning_commons_export_schema_version",
        "object_counts",
        "passed",
        "validation_checks",
    }
)
NonNegativeStrictInt = Annotated[int, Field(ge=0, strict=True)]


class ExpectedCounts(BaseModel):
    """Expected counts for one reduced fixture."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: NonNegativeStrictInt
    learning_components: NonNegativeStrictInt
    relationships_has_child: NonNegativeStrictInt
    relationships_supports: NonNegativeStrictInt


class FixtureExpectations(BaseModel):
    """Pinned structural expectations for one reduced fixture."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    counts: ExpectedCounts
    cross_grade_item_sets: list[list[UUID]] = Field(default_factory=list)
    direct_parent_sets: dict[UUID, list[UUID]] = Field(default_factory=dict)
    lc_alignments: dict[UUID, list[UUID]] = Field(default_factory=dict)
    required_identity_scope_values: dict[str, list[str]] = Field(default_factory=dict)
    required_statement_types: list[str]
    unresolved_item_ids: list[UUID] = Field(default_factory=list)


class FixtureFramework(BaseModel):
    """Reduced StandardsFramework record copied from an approved AS+LC bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    academic_subject: str = Field(min_length=1)
    case_identifier_uuid: UUID
    metadata: dict[str, Any]
    name: str = Field(min_length=1)


class FixtureItem(BaseModel):
    """Reduced StandardsFrameworkItem record copied from an approved bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_identifier_uuid: UUID
    description: str = Field(min_length=1)
    metadata: dict[str, Any]
    normalized_statement_type: str = Field(min_length=1)
    statement_code: str | None = None
    statement_type: str = Field(min_length=1)


class FixtureLearningComponent(BaseModel):
    """Reduced LearningComponent record copied from an approved bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    description: str = Field(min_length=1)
    identifier: UUID
    metadata: dict[str, Any]


class FixtureRelationship(BaseModel):
    """Reduced current AS+LC Relationship record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    identifier: UUID
    metadata: dict[str, Any]
    relationship_type: Literal["hasChild", "supports"]
    source_entity: Literal[
        "LearningComponent", "StandardsFramework", "StandardsFrameworkItem"
    ]
    source_entity_key: Literal["case_identifier_uuid", "identifier"]
    source_entity_value: UUID
    target_entity: Literal["StandardsFrameworkItem"]
    target_entity_key: Literal["case_identifier_uuid"]
    target_entity_value: UUID


class LPRegressionFixture(BaseModel):
    """One self-contained reduced regression fixture and its source snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    curriculum: Literal[
        "ghana_english",
        "ghana_math",
        "madhi_math",
        "nigeria_math",
        "pratham_science",
        "rwanda_math",
    ]
    distinctive_property: Literal[
        "cross_grade_recurrence",
        "explicit_grade_tree",
        "multi_parent_dag",
        "multiple_grains_noisy_lc",
        "scope_only_class",
        "unresolved_fallback_and_code_anomaly",
    ]
    expectations: FixtureExpectations
    framework: FixtureFramework
    items: list[FixtureItem]
    learning_components: list[FixtureLearningComponent]
    relationships_has_child: list[FixtureRelationship]
    relationships_supports: list[FixtureRelationship]
    source_bundle: SourceBundleSnapshot


class SourceBundleSnapshot(BaseModel):
    """Immutable identity and count snapshot of one approved full AS+LC bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    doc_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    entity_provenance_sections: list[str]
    framework_case_identifier_uuid: UUID
    object_counts: dict[str, NonNegativeStrictInt]
    statement_type_counts: dict[str, NonNegativeStrictInt]
    top_level_keys: list[str]
    unresolved_root_fallback_count: NonNegativeStrictInt


def _derive_direct_parent_sets(
    relationships: list[FixtureRelationship],
) -> dict[UUID, list[UUID]]:
    """Derive sorted direct-parent sets from reduced `hasChild` records.

    Parameters
    ----------
    relationships
        Reduced `hasChild` relationships.

    Returns
    -------
    dict[UUID, list[UUID]]
        Child UUIDs mapped to their sorted direct-parent UUIDs.
    """

    parents_by_child: defaultdict[UUID, list[UUID]] = defaultdict(list)
    for relationship in relationships:
        parents_by_child[relationship.target_entity_value].append(
            relationship.source_entity_value
        )

    return {
        child_id: sorted(parent_ids, key=str)
        for child_id, parent_ids in parents_by_child.items()
    }


def _derive_lc_alignments(
    relationships: list[FixtureRelationship],
) -> dict[UUID, list[UUID]]:
    """Derive sorted LC-to-SFI alignments from reduced `supports` records.

    Parameters
    ----------
    relationships
        Reduced `supports` relationships.

    Returns
    -------
    dict[UUID, list[UUID]]
        Learning Component UUIDs mapped to sorted supported SFI UUIDs.
    """

    sfis_by_lc: defaultdict[UUID, list[UUID]] = defaultdict(list)
    for relationship in relationships:
        sfis_by_lc[relationship.source_entity_value].append(
            relationship.target_entity_value
        )

    return {lc_id: sorted(sfi_ids, key=str) for lc_id, sfi_ids in sfis_by_lc.items()}


def _is_non_empty_string(value: Any) -> bool:
    """Return whether a metadata value is a non-empty string.

    Parameters
    ----------
    value
        Metadata value to inspect.

    Returns
    -------
    bool
        True only for non-empty strings.
    """

    return isinstance(value, str) and bool(value)


def _is_non_negative_int(value: Any) -> bool:
    """Return whether a metadata value is a strict non-negative integer.

    Parameters
    ----------
    value
        Metadata value to inspect.

    Returns
    -------
    bool
        True for non-negative integers, excluding booleans.
    """

    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _reduced_projection_sha256(fixture: LPRegressionFixture) -> str:
    """Hash the canonical reduced source projection for one fixture.

    Parameters
    ----------
    fixture
        Parsed reduced fixture.

    Returns
    -------
    str
        SHA-256 of canonical framework, SFI, LC, and relationship records.
    """

    projection = {
        "framework": fixture.framework.model_dump(mode="json"),
        "items": [
            item.model_dump(mode="json")
            for item in sorted(
                fixture.items, key=lambda item: str(item.case_identifier_uuid)
            )
        ],
        "learning_components": [
            component.model_dump(mode="json")
            for component in sorted(
                fixture.learning_components,
                key=lambda component: str(component.identifier),
            )
        ],
        "relationships_has_child": [
            relationship.model_dump(mode="json")
            for relationship in sorted(
                fixture.relationships_has_child,
                key=lambda relationship: str(relationship.identifier),
            )
        ],
        "relationships_supports": [
            relationship.model_dump(mode="json")
            for relationship in sorted(
                fixture.relationships_supports,
                key=lambda relationship: str(relationship.identifier),
            )
        ],
    }
    canonical = dumps(
        projection,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _require_exact_keys(*, actual: set[str], expected: set[str], label: str) -> None:
    """Require an exact key set for a reduced current-artifact record.

    Parameters
    ----------
    actual
        Keys present in the record.
    expected
        Required exact key set.
    label
        Human-readable record label for diagnostics.

    Raises
    ------
    ValueError
        If the key sets differ.
    """

    if actual != expected:
        raise ValueError(
            f"{label} keys drifted: expected {sorted(expected)}, got {sorted(actual)}"
        )


def _require_unique(*, label: str, values: list[Any]) -> None:
    """Require a list to contain no duplicate values.

    Parameters
    ----------
    label
        Human-readable list label for diagnostics.
    values
        Values that must be unique.

    Raises
    ------
    ValueError
        If a duplicate value exists.
    """

    if len(values) != len(set(values)):
        raise ValueError(f"{label} contains duplicate values.")


def _validate_distinctive_property(fixture: LPRegressionFixture) -> None:
    """Verify the reduced fixture retains its curriculum-specific property.

    Parameters
    ----------
    fixture
        Parsed fixture to validate.

    Raises
    ------
    ValueError
        If the curriculum-specific regression property is absent.
    """

    expected_properties = {
        "ghana_english": "cross_grade_recurrence",
        "ghana_math": "unresolved_fallback_and_code_anomaly",
        "madhi_math": "scope_only_class",
        "nigeria_math": "explicit_grade_tree",
        "pratham_science": "multi_parent_dag",
        "rwanda_math": "multiple_grains_noisy_lc",
    }
    if fixture.distinctive_property != expected_properties[fixture.curriculum]:
        raise ValueError("Curriculum and distinctive-property label disagree.")

    property_validators: dict[str, Callable[[LPRegressionFixture], None]] = {
        "ghana_english": _validate_ghana_english_property,
        "ghana_math": _validate_ghana_math_property,
        "madhi_math": _validate_madhi_math_property,
        "nigeria_math": _validate_nigeria_math_property,
        "pratham_science": _validate_pratham_science_property,
        "rwanda_math": _validate_rwanda_math_property,
    }
    property_validators[fixture.curriculum](fixture)


def _validate_entity_records(fixture: LPRegressionFixture) -> None:
    """Validate reduced framework, SFI, and LC record shapes and identities.

    Parameters
    ----------
    fixture
        Parsed fixture to validate.

    Raises
    ------
    ValueError
        If a record shape, provenance field, or identifier is invalid.
    """

    _require_exact_keys(
        actual=set(fixture.framework.metadata),
        expected={"doc_key"},
        label="framework metadata",
    )
    if fixture.framework.metadata["doc_key"] != fixture.source_bundle.doc_key:
        raise ValueError("Framework doc_key does not match the source snapshot.")

    item_ids = [item.case_identifier_uuid for item in fixture.items]
    lc_ids = [component.identifier for component in fixture.learning_components]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("Fixture contains duplicate SFI UUIDs.")
    if len(lc_ids) != len(set(lc_ids)):
        raise ValueError("Fixture contains duplicate Learning Component UUIDs.")
    if set(item_ids).intersection(lc_ids):
        raise ValueError("SFI and Learning Component UUIDs collide.")

    for item in fixture.items:
        _validate_item_metadata(item)

    for component in fixture.learning_components:
        _validate_lc_metadata(component)


def _validate_expectations(fixture: LPRegressionFixture) -> None:
    """Validate the fixture's pinned expectation collections.

    Parameters
    ----------
    fixture
        Parsed fixture to validate.

    Raises
    ------
    ValueError
        If an expectation is empty, duplicated, or malformed.
    """

    expectations = fixture.expectations
    if not expectations.required_statement_types or not all(
        value.strip() for value in expectations.required_statement_types
    ):
        raise ValueError("Required statement types must be non-empty strings.")
    _require_unique(
        label="Required statement types",
        values=expectations.required_statement_types,
    )

    for scope_name, scope_values in expectations.required_identity_scope_values.items():
        if (
            not scope_name.strip()
            or not scope_values
            or not all(value.strip() for value in scope_values)
        ):
            raise ValueError("Required identity-scope values are malformed.")
        _require_unique(
            label=f"Required {scope_name} identity-scope values",
            values=scope_values,
        )

    for child_id, parent_ids in expectations.direct_parent_sets.items():
        if not parent_ids:
            raise ValueError(f"Expected parent set for {child_id} is empty.")
        _require_unique(label=f"Expected parent set for {child_id}", values=parent_ids)

    for lc_id, item_ids in expectations.lc_alignments.items():
        if not item_ids:
            raise ValueError(f"Expected LC alignment for {lc_id} is empty.")
        _require_unique(label=f"Expected LC alignment for {lc_id}", values=item_ids)

    for item_ids in expectations.cross_grade_item_sets:
        if len(item_ids) < 2:
            raise ValueError("Cross-grade item sets must contain at least two SFIs.")
        _require_unique(label="Cross-grade item set", values=item_ids)

    _require_unique(
        label="Expected unresolved item IDs",
        values=expectations.unresolved_item_ids,
    )


def _validate_ghana_english_property(fixture: LPRegressionFixture) -> None:
    """Validate Ghana English cross-grade source recurrence.

    Parameters
    ----------
    fixture
        Ghana English fixture to validate.

    Raises
    ------
    ValueError
        If either cross-grade source recurrence drifts.
    """

    item_by_id = {item.case_identifier_uuid: item for item in fixture.items}
    alignments = _derive_lc_alignments(fixture.relationships_supports)
    alignment_sets = {frozenset(item_ids) for item_ids in alignments.values()}
    if len(fixture.expectations.cross_grade_item_sets) != 2:
        raise ValueError("Ghana English must retain both cross-grade source item sets.")
    for item_ids in fixture.expectations.cross_grade_item_sets:
        grades = {
            item_by_id[item_id].metadata["identity_scope_values"].get("Grade")
            for item_id in item_ids
        }
        if (
            grades != {"BASIC 2", "BASIC 3"}
            or frozenset(item_ids) not in alignment_sets
        ):
            raise ValueError("Ghana English cross-grade source recurrence drifted.")


def _validate_ghana_math_property(fixture: LPRegressionFixture) -> None:
    """Validate Ghana mathematics unresolved-fallback anomaly coverage.

    Parameters
    ----------
    fixture
        Ghana mathematics fixture to validate.

    Raises
    ------
    ValueError
        If the fallback item or source-code anomaly flag is absent.
    """

    item_by_id = {item.case_identifier_uuid: item for item in fixture.items}
    unresolved_items = [
        item_by_id[item_id] for item_id in fixture.expectations.unresolved_item_ids
    ]
    if not unresolved_items or not any(
        "same_code_different_content" in item.metadata["audit_flags"]
        for item in unresolved_items
    ):
        raise ValueError(
            "Ghana math fixture must retain an unresolved root fallback with "
            "its source-code anomaly flag."
        )


def _validate_has_child_relationship(
    framework_id: UUID,
    item_ids: set[UUID],
    relationship: FixtureRelationship,
) -> None:
    """Validate one reduced `hasChild` relationship.

    Parameters
    ----------
    framework_id
        Fixture framework UUID.
    item_ids
        Fixture SFI UUIDs.
    relationship
        Reduced relationship to validate.

    Raises
    ------
    ValueError
        If metadata, endpoint types, keys, or values are malformed.
    """

    _require_exact_keys(
        actual=set(relationship.metadata),
        expected={"unresolved_root_fallback"},
        label=f"hasChild {relationship.identifier} metadata",
    )
    expected_source_entity = (
        "StandardsFramework"
        if relationship.source_entity_value == framework_id
        else "StandardsFrameworkItem"
    )
    unresolved = relationship.metadata["unresolved_root_fallback"]
    if (
        relationship.relationship_type != "hasChild"
        or relationship.source_entity != expected_source_entity
        or relationship.source_entity_key != "case_identifier_uuid"
        or relationship.target_entity_key != "case_identifier_uuid"
        or relationship.target_entity_value not in item_ids
        or relationship.source_entity_value not in item_ids | {framework_id}
        or relationship.source_entity_value == relationship.target_entity_value
        or not isinstance(unresolved, bool)
    ):
        raise ValueError(f"Malformed hasChild relationship {relationship.identifier}.")
    if unresolved and relationship.source_entity_value != framework_id:
        raise ValueError("Unresolved fallback must originate at the framework root.")


def _validate_item_metadata(item: FixtureItem) -> None:
    """Validate one reduced SFI metadata record.

    Parameters
    ----------
    item
        Reduced SFI to validate.

    Raises
    ------
    ValueError
        If the metadata shape or value types are malformed.
    """

    _require_exact_keys(
        actual=set(item.metadata),
        expected={
            "audit_flags",
            "audit_notes",
            "identity_scope_values",
            "source_page_indexes",
        },
        label=f"SFI {item.case_identifier_uuid} metadata",
    )
    audit_flags = item.metadata["audit_flags"]
    audit_notes = item.metadata["audit_notes"]
    identity_scope_values = item.metadata["identity_scope_values"]
    source_page_indexes = item.metadata["source_page_indexes"]
    if (
        not isinstance(audit_flags, list)
        or not all(_is_non_empty_string(value) for value in audit_flags)
        or not isinstance(audit_notes, list)
        or not all(_is_non_empty_string(value) for value in audit_notes)
        or not isinstance(identity_scope_values, dict)
        or not all(
            _is_non_empty_string(key) and _is_non_empty_string(value)
            for key, value in identity_scope_values.items()
        )
        or not isinstance(source_page_indexes, list)
        or not source_page_indexes
        or not all(_is_non_negative_int(value) for value in source_page_indexes)
    ):
        raise ValueError(f"SFI {item.case_identifier_uuid} metadata is malformed.")
    _require_unique(
        label=f"SFI {item.case_identifier_uuid} source pages",
        values=source_page_indexes,
    )
    if item.statement_code is not None and not item.statement_code.strip():
        raise ValueError(f"SFI {item.case_identifier_uuid} has a blank source code.")


def _validate_lc_metadata(component: FixtureLearningComponent) -> None:
    """Validate one reduced Learning Component metadata record.

    Parameters
    ----------
    component
        Reduced Learning Component to validate.

    Raises
    ------
    ValueError
        If source pages or source SFI UUIDs are malformed.
    """

    _require_exact_keys(
        actual=set(component.metadata),
        expected={"source_page_indexes", "source_sfi_uuids"},
        label=f"Learning Component {component.identifier} metadata",
    )
    source_page_indexes = component.metadata["source_page_indexes"]
    source_sfi_uuids = component.metadata["source_sfi_uuids"]
    if (
        not isinstance(source_page_indexes, list)
        or not source_page_indexes
        or not all(_is_non_negative_int(value) for value in source_page_indexes)
    ):
        raise ValueError(
            f"Learning Component {component.identifier} has malformed source pages."
        )
    _require_unique(
        label=f"Learning Component {component.identifier} source pages",
        values=source_page_indexes,
    )
    if not isinstance(source_sfi_uuids, list) or not source_sfi_uuids:
        raise ValueError(
            f"Learning Component {component.identifier} lacks source SFIs."
        )
    _require_unique(
        label=f"Learning Component {component.identifier} source SFIs",
        values=source_sfi_uuids,
    )
    for item_id in source_sfi_uuids:
        if not isinstance(item_id, str):
            raise ValueError(
                f"Learning Component {component.identifier} has a non-string "
                "source SFI UUID."
            )
        UUID(item_id)


def _validate_madhi_math_property(fixture: LPRegressionFixture) -> None:
    """Validate Madhi scope-only Class values without Class nodes.

    Parameters
    ----------
    fixture
        Madhi mathematics fixture to validate.

    Raises
    ------
    ValueError
        If either scope-only Class value or the absence of Class nodes drifts.
    """

    class_values = {
        item.metadata["identity_scope_values"].get("Class") for item in fixture.items
    }
    if any(item.statement_type == "Class" for item in fixture.items) or not {
        "Class-1",
        "Class-5",
    }.issubset(class_values):
        raise ValueError(
            "Madhi fixture must retain scope-only Class-1/Class-5 values "
            "without final Class items."
        )


def _validate_nigeria_math_property(fixture: LPRegressionFixture) -> None:
    """Validate Nigeria mathematics explicit Grade-rooted tree.

    Parameters
    ----------
    fixture
        Nigeria mathematics fixture to validate.

    Raises
    ------
    ValueError
        If a direct parent in the explicit tree drifts.
    """

    items_by_type: defaultdict[str, list[FixtureItem]] = defaultdict(list)
    for item in fixture.items:
        items_by_type[item.statement_type].append(item)
    parent_sets = _derive_direct_parent_sets(fixture.relationships_has_child)
    chain = (
        ("Theme", "Grade"),
        ("Sub-Theme", "Theme"),
        ("Topic", "Sub-Theme"),
        ("Performance Objective", "Topic"),
    )
    for child_type, parent_type in chain:
        child = items_by_type[child_type][0]
        parent = items_by_type[parent_type][0]
        if parent_sets.get(child.case_identifier_uuid) != [parent.case_identifier_uuid]:
            raise ValueError("Nigeria fixture no longer contains its explicit tree.")
    grade = items_by_type["Grade"][0]
    if parent_sets.get(grade.case_identifier_uuid) != [
        fixture.framework.case_identifier_uuid
    ]:
        raise ValueError("Nigeria Grade must remain attached to the framework root.")


def _validate_pratham_science_property(fixture: LPRegressionFixture) -> None:
    """Validate Pratham's multi-parent Standard grain and Indicator child.

    Parameters
    ----------
    fixture
        Pratham science fixture to validate.

    Raises
    ------
    ValueError
        If the pinned multi-parent DAG structure drifts.
    """

    item_by_id = {item.case_identifier_uuid: item for item in fixture.items}
    items_by_type = {item.statement_type: item for item in fixture.items}
    parent_sets = _derive_direct_parent_sets(fixture.relationships_has_child)
    child = items_by_type["Content Domain Specific Learning Outcome"]
    parent_types = {
        item_by_id[parent_id].statement_type
        for parent_id in parent_sets.get(child.case_identifier_uuid, [])
    }
    if parent_types != {"Chapter", "NCERT Learning Outcome"}:
        raise ValueError(
            "Pratham fixture must retain both direct parents of one DAG child."
        )
    indicator = items_by_type["Indicator"]
    if parent_sets.get(indicator.case_identifier_uuid) != [child.case_identifier_uuid]:
        raise ValueError(
            "Pratham Indicator must remain below the multi-parent Standard grain."
        )


def _validate_production_shapes() -> None:
    """Verify the approved current AS+LC Pydantic record shapes have not drifted.

    Raises
    ------
    ValueError
        If a current AS+LC schema field set differs from the approved snapshot.
    """

    shape_pairs = (
        (AS_LC_BUNDLE_TOP_LEVEL_KEYS, set(AcademicStandardsLCKGBundle.model_fields)),
        (FRAMEWORK_RECORD_KEYS, set(StandardsFramework.model_fields)),
        (ITEM_RECORD_KEYS, set(StandardsFrameworkItem.model_fields)),
        (LEARNING_COMPONENT_RECORD_KEYS, set(LearningComponent.model_fields)),
        (RELATIONSHIP_RECORD_KEYS, set(Relationship.model_fields)),
        (SUMMARY_RECORD_KEYS, set(AcademicStandardsLCExportSummary.model_fields)),
        (
            UNRESOLVED_ITEMS_RECORD_KEYS,
            set(AcademicStandardsLCUnresolvedItems.model_fields),
        ),
        (
            VALIDATION_REPORT_RECORD_KEYS,
            set(AcademicStandardsValidationReport.model_fields),
        ),
    )
    for expected, actual in shape_pairs:
        if actual != expected:
            raise ValueError(
                "Approved AS+LC Pydantic shape drifted: "
                f"expected {sorted(expected)}, got {sorted(actual)}"
            )


def _validate_reduced_projection(fixture: LPRegressionFixture) -> None:
    """Verify fixture records match the independently pinned source projection.

    Parameters
    ----------
    fixture
        Parsed fixture to validate.

    Raises
    ------
    ValueError
        If any reduced source record drifts from the approved projection.
    """

    actual_digest = _reduced_projection_sha256(fixture)
    expected_digest = APPROVED_REDUCED_PROJECTION_SHA256[fixture.curriculum]
    if actual_digest != expected_digest:
        raise ValueError(
            "Approved reduced fixture projection drifted: "
            f"expected {expected_digest}, got {actual_digest}."
        )


def _validate_relationship_graph_expectations(fixture: LPRegressionFixture) -> None:
    """Validate pinned parent sets, LC alignments, and LC metadata agreement.

    Parameters
    ----------
    fixture
        Parsed fixture to validate.

    Raises
    ------
    ValueError
        If graph expectations or LC provenance metadata drift.
    """

    actual_parent_sets = _derive_direct_parent_sets(fixture.relationships_has_child)
    expected_parent_sets = {
        child_id: sorted(parent_ids, key=str)
        for child_id, parent_ids in fixture.expectations.direct_parent_sets.items()
    }
    if actual_parent_sets != expected_parent_sets:
        raise ValueError("Direct parent sets do not match fixture expectations.")

    actual_alignments = _derive_lc_alignments(fixture.relationships_supports)
    expected_alignments = {
        lc_id: sorted(item_ids, key=str)
        for lc_id, item_ids in fixture.expectations.lc_alignments.items()
    }
    if actual_alignments != expected_alignments:
        raise ValueError("LC alignments do not match fixture expectations.")

    components_by_id = {
        component.identifier: component for component in fixture.learning_components
    }
    if set(actual_alignments) != set(components_by_id):
        raise ValueError("Every fixture Learning Component must have a supports edge.")
    for lc_id, item_ids in actual_alignments.items():
        metadata_ids = sorted(
            (
                UUID(item_id)
                for item_id in components_by_id[lc_id].metadata["source_sfi_uuids"]
            ),
            key=str,
        )
        if metadata_ids != item_ids:
            raise ValueError(
                f"Learning Component {lc_id} metadata and supports edges disagree."
            )


def _validate_relationship_identifiers(fixture: LPRegressionFixture) -> None:
    """Validate entity, relationship, and logical-edge identifier uniqueness.

    Parameters
    ----------
    fixture
        Parsed fixture to validate.

    Raises
    ------
    ValueError
        If an identifier collision or duplicate logical edge exists.
    """

    relationship_ids = [
        relationship.identifier
        for relationship in (
            fixture.relationships_has_child + fixture.relationships_supports
        )
    ]
    if len(relationship_ids) != len(set(relationship_ids)):
        raise ValueError("Fixture contains duplicate relationship UUIDs.")
    entity_ids = [
        fixture.framework.case_identifier_uuid,
        *(item.case_identifier_uuid for item in fixture.items),
        *(component.identifier for component in fixture.learning_components),
    ]
    if len(entity_ids) != len(set(entity_ids)):
        raise ValueError("Fixture contains colliding entity UUIDs.")
    if set(entity_ids).intersection(relationship_ids):
        raise ValueError("Fixture contains an entity/relationship UUID collision.")

    logical_relationships = [
        (
            relationship.relationship_type,
            relationship.source_entity_value,
            relationship.target_entity_value,
        )
        for relationship in (
            fixture.relationships_has_child + fixture.relationships_supports
        )
    ]
    if len(logical_relationships) != len(set(logical_relationships)):
        raise ValueError("Fixture contains duplicate logical relationships.")


def _validate_relationships(fixture: LPRegressionFixture) -> None:
    """Validate relationship shapes, endpoints, parent sets, flags, and alignments.

    Parameters
    ----------
    fixture
        Parsed fixture to validate.

    Raises
    ------
    ValueError
        If relationship integrity or a pinned expectation drifts.
    """

    framework_id = fixture.framework.case_identifier_uuid
    item_ids = {item.case_identifier_uuid for item in fixture.items}
    lc_ids = {component.identifier for component in fixture.learning_components}
    _validate_relationship_identifiers(fixture)

    for relationship in fixture.relationships_has_child:
        _validate_has_child_relationship(framework_id, item_ids, relationship)

    for relationship in fixture.relationships_supports:
        _validate_supports_relationship(item_ids, lc_ids, relationship)

    _validate_relationship_graph_expectations(fixture)
    _validate_unresolved_relationships(fixture)


def _validate_rwanda_math_property(fixture: LPRegressionFixture) -> None:
    """Validate Rwanda multiple grains and noisy cross-grade LC reuse.

    Parameters
    ----------
    fixture
        Rwanda mathematics fixture to validate.

    Raises
    ------
    ValueError
        If a Standard grain or the pinned noisy LC alignment drifts.
    """

    item_by_id = {item.case_identifier_uuid: item for item in fixture.items}
    item_types = {item.statement_type for item in fixture.items}
    required_grains = {
        "Attitudes and Values Objective",
        "Grade Key Competence",
        "Key Unit Competence",
        "Knowledge Objective",
        "Skills Objective",
    }
    alignments = _derive_lc_alignments(fixture.relationships_supports)
    shared_targets = max(alignments.values(), key=len, default=[])
    shared_grades = {
        item_by_id[item_id].metadata["identity_scope_values"].get("Grade")
        for item_id in shared_targets
    }
    if (
        not required_grains.issubset(item_types)
        or len(shared_targets) < 3
        or any(
            item_by_id[item_id].statement_type != "Attitudes and Values Objective"
            for item_id in shared_targets
        )
        or shared_grades != {"P1", "P2", "P3"}
    ):
        raise ValueError(
            "Rwanda fixture must retain multiple Standard grains and the noisy "
            "cross-grade Attitudes/Values LC reuse."
        )


def _validate_source_snapshot(fixture: LPRegressionFixture) -> None:
    """Validate the immutable source-bundle shape and count snapshot.

    Parameters
    ----------
    fixture
        Parsed fixture to validate.

    Raises
    ------
    ValueError
        If the source snapshot is internally inconsistent or has shape drift.
    """

    snapshot = fixture.source_bundle
    if snapshot.top_level_keys != sorted(AS_LC_BUNDLE_TOP_LEVEL_KEYS):
        raise ValueError("AS+LC bundle top-level shape drifted.")
    if snapshot.entity_provenance_sections != sorted(ENTITY_PROVENANCE_SECTIONS):
        raise ValueError("AS+LC entity-provenance sections drifted.")
    if set(snapshot.object_counts) != SOURCE_OBJECT_COUNT_KEYS:
        raise ValueError("AS+LC source object-count keys drifted.")
    if (
        snapshot.framework_case_identifier_uuid
        != fixture.framework.case_identifier_uuid
    ):
        raise ValueError("Framework UUID does not match the source snapshot.")
    if snapshot.object_counts["frameworks"] != 1:
        raise ValueError("Each source snapshot must contain one framework.")
    if not snapshot.statement_type_counts or not all(
        statement_type.strip() for statement_type in snapshot.statement_type_counts
    ):
        raise ValueError("Source statement-type count keys must be non-empty.")
    if snapshot.object_counts["standards_framework_items"] != sum(
        snapshot.statement_type_counts.values()
    ):
        raise ValueError(
            "Source statement-type counts do not reconcile with SFI count."
        )
    if (
        snapshot.unresolved_root_fallback_count
        > snapshot.object_counts["relationships_has_child"]
    ):
        raise ValueError("Source unresolved count exceeds source hasChild count.")


def _validate_supports_relationship(
    item_ids: set[UUID],
    lc_ids: set[UUID],
    relationship: FixtureRelationship,
) -> None:
    """Validate one reduced `supports` relationship.

    Parameters
    ----------
    item_ids
        Fixture SFI UUIDs.
    lc_ids
        Fixture Learning Component UUIDs.
    relationship
        Reduced relationship to validate.

    Raises
    ------
    ValueError
        If metadata, endpoint types, keys, or values are malformed.
    """

    _require_exact_keys(
        actual=set(relationship.metadata),
        expected={"support_role"},
        label=f"supports {relationship.identifier} metadata",
    )
    if (
        relationship.relationship_type != "supports"
        or relationship.source_entity != "LearningComponent"
        or relationship.source_entity_key != "identifier"
        or relationship.source_entity_value not in lc_ids
        or relationship.target_entity_key != "case_identifier_uuid"
        or relationship.target_entity_value not in item_ids
        or relationship.metadata["support_role"] != "primary"
    ):
        raise ValueError(f"Malformed supports relationship {relationship.identifier}.")


def _validate_unresolved_relationships(fixture: LPRegressionFixture) -> None:
    """Validate pinned unresolved-root-fallback relationship targets.

    Parameters
    ----------
    fixture
        Parsed fixture to validate.

    Raises
    ------
    ValueError
        If the unresolved relationship target set drifts.
    """

    actual_unresolved_ids = sorted(
        (
            relationship.target_entity_value
            for relationship in fixture.relationships_has_child
            if relationship.metadata["unresolved_root_fallback"]
        ),
        key=str,
    )
    if actual_unresolved_ids != sorted(
        fixture.expectations.unresolved_item_ids, key=str
    ):
        raise ValueError("Unresolved fallback flags do not match fixture expectations.")


def load_all_lp_regression_fixtures() -> list[LPRegressionFixture]:
    """Load and validate all six reduced LP regression fixtures.

    Returns
    -------
    list[LPRegressionFixture]
        Fixtures in stable curriculum filename order.
    """

    return [
        load_lp_regression_fixture(LP_FIXTURES_DIR / filename)
        for filename in FIXTURE_FILENAMES
    ]


def load_lp_regression_fixture(fixture_path: Path) -> LPRegressionFixture:
    """Load and validate one reduced LP regression fixture.

    Parameters
    ----------
    fixture_path
        JSON fixture path.

    Returns
    -------
    LPRegressionFixture
        Parsed and fully validated fixture.
    """

    fixture = LPRegressionFixture.model_validate_json(
        fixture_path.read_text(encoding="utf-8")
    )
    validate_lp_regression_fixture(fixture)
    return fixture


def validate_lp_regression_fixture(fixture: LPRegressionFixture) -> None:
    """Validate one reduced fixture against Step 1 structural contracts.

    Parameters
    ----------
    fixture
        Parsed fixture to validate.

    Raises
    ------
    ValueError
        If any source snapshot, count, graph, unresolved, or LC contract drifts.
    """

    actual_counts = ExpectedCounts(
        items=len(fixture.items),
        learning_components=len(fixture.learning_components),
        relationships_has_child=len(fixture.relationships_has_child),
        relationships_supports=len(fixture.relationships_supports),
    )
    if actual_counts != fixture.expectations.counts:
        raise ValueError("Reduced fixture counts do not match fixture expectations.")

    item_statement_types = {item.statement_type for item in fixture.items}
    if not set(fixture.expectations.required_statement_types).issubset(
        item_statement_types
    ):
        raise ValueError("Required statement types are missing from the fixture.")

    for (
        statement_type,
        required_values,
    ) in fixture.expectations.required_identity_scope_values.items():
        present_values = {
            item.metadata["identity_scope_values"].get(statement_type)
            for item in fixture.items
        }
        if not set(required_values).issubset(present_values):
            raise ValueError(
                f"Required {statement_type} identity-scope values are missing."
            )

    _validate_entity_records(fixture)
    _validate_expectations(fixture)
    _validate_production_shapes()
    _validate_relationships(fixture)
    _validate_source_snapshot(fixture)
    _validate_distinctive_property(fixture)
    _validate_reduced_projection(fixture)
