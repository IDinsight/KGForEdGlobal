"""Deterministic graph indexes for Learning Progressions construction.

The index treats the validated AS+LC bundle as authoritative. It preserves every
SFI-to-SFI ``hasChild`` branch, keeps unresolved framework-root fallbacks outside the
positive hierarchy, and exposes bidirectional ``supports`` lookups without assuming a
curriculum-specific graph shape or label vocabulary.
"""

# Future Library
from __future__ import annotations

# Standard Library
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from uuid import UUID

# Package Library
from kgfeg.kgs.schemas import (
    AcademicStandardsLCKGBundle,
    LearningComponent,
    Relationship,
    StandardsFrameworkItem,
)


@dataclass(frozen=True, slots=True)
class _HierarchyIndexes:
    """Immutable positive-hierarchy and framework-root index parts."""

    child_sfi_uuids_by_parent_sfi_uuid: Mapping[UUID, tuple[UUID, ...]]
    framework_child_sfi_uuids: tuple[UUID, ...]
    parent_sfi_uuids_by_sfi_uuid: Mapping[UUID, tuple[UUID, ...]]
    root_fallback_relationships_by_sfi_uuid: Mapping[UUID, tuple[Relationship, ...]]
    root_fallback_sfi_uuids: tuple[UUID, ...]
    unresolved_ancestry_sfi_uuids: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class _SupportIndexes:
    """Immutable bidirectional Learning Component support index parts."""

    learning_components_by_sfi_uuid: Mapping[UUID, tuple[LearningComponent, ...]]
    sfis_by_learning_component_uuid: Mapping[UUID, tuple[StandardsFrameworkItem, ...]]


@dataclass(frozen=True, slots=True)
class LPAncestorPath:
    """One bounded positive-hierarchy path above an SFI.

    Attributes
    ----------
    ancestor_sfi_uuids
        Ancestor CASE UUIDs ordered from the direct parent toward the framework. The
        framework itself is not an SFI and is therefore not included.
    depth_truncated
        Whether the path stopped at the requested depth while another SFI parent
        remained. Framework-root fallback edges are not traversed as hierarchy.
    """

    ancestor_sfi_uuids: tuple[UUID, ...]
    depth_truncated: bool


@dataclass(frozen=True, slots=True)
class LPAncestorPaths:
    """Deterministic bounded ancestor paths for one SFI.

    Attributes
    ----------
    paths
        Retained paths in deterministic UUID order.
    paths_truncated
        Whether additional positive-hierarchy paths were omitted by the path-count
        bound.
    """

    paths: tuple[LPAncestorPath, ...]
    paths_truncated: bool


@dataclass(frozen=True, slots=True)
class LPGraphIndex:
    """Immutable indexes over one validated AS+LC graph.

    All adjacency values are UUID-sorted tuples so traversal cannot depend on bundle or
    relationship encounter order. SFI parent/child adjacency contains only positive
    SFI-to-SFI hierarchy edges. Resolved framework attachments and unresolved
    framework-root fallbacks are represented separately.
    """

    child_sfi_uuids_by_parent_sfi_uuid: Mapping[UUID, tuple[UUID, ...]]
    framework_child_sfi_uuids: tuple[UUID, ...]
    learning_component_by_uuid: Mapping[UUID, LearningComponent]
    learning_components_by_sfi_uuid: Mapping[UUID, tuple[LearningComponent, ...]]
    parent_sfi_uuids_by_sfi_uuid: Mapping[UUID, tuple[UUID, ...]]
    root_fallback_relationships_by_sfi_uuid: Mapping[UUID, tuple[Relationship, ...]]
    root_fallback_sfi_uuids: tuple[UUID, ...]
    sfi_by_uuid: Mapping[UUID, StandardsFrameworkItem]
    sfi_provenance_by_uuid: Mapping[UUID, Mapping[str, Any]]
    sfis_by_learning_component_uuid: Mapping[UUID, tuple[StandardsFrameworkItem, ...]]
    unresolved_ancestry_sfi_uuids: frozenset[UUID]

    def ancestor_paths(
        self, *, max_depth: int, max_paths: int, sfi_uuid: UUID
    ) -> LPAncestorPaths:
        """Return bounded positive-hierarchy ancestor paths for one SFI.

        Each path starts with a direct SFI parent. A path ends at a framework-attached
        SFI, at an unresolved-root SFI whose fallback is kept separate, or at the
        requested depth. Branches are expanded by UUID, so retaining only the first
        ``max_paths`` is deterministic.

        Parameters
        ----------
        max_depth
            Maximum number of SFI ancestors retained in one path.
        max_paths
            Maximum number of distinct paths returned.
        sfi_uuid
            CASE UUID of the SFI whose ancestry is requested.

        Returns
        -------
        LPAncestorPaths
            Bounded paths plus explicit path-count and depth truncation state.

        Raises
        ------
        ValueError
            If either bound is not a positive integer or the SFI is unknown.
        """

        if isinstance(max_depth, bool) or not isinstance(max_depth, int):
            raise ValueError("max_depth must be a positive integer.")

        if max_depth < 1:
            raise ValueError("max_depth must be a positive integer.")

        if isinstance(max_paths, bool) or not isinstance(max_paths, int):
            raise ValueError("max_paths must be a positive integer.")

        if max_paths < 1:
            raise ValueError("max_paths must be a positive integer.")

        if sfi_uuid not in self.sfi_by_uuid:
            raise ValueError(f"Unknown SFI CASE UUID: {sfi_uuid}.")

        parent_uuids = self.parent_sfi_uuids_by_sfi_uuid[sfi_uuid]
        pending: list[tuple[UUID, tuple[UUID, ...]]] = [
            (parent_uuid, (parent_uuid,)) for parent_uuid in parent_uuids
        ]
        pending.reverse()
        paths: list[LPAncestorPath] = []

        while pending and len(paths) < max_paths:
            current_uuid, path = pending.pop()
            current_parent_uuids = self.parent_sfi_uuids_by_sfi_uuid[current_uuid]
            depth_truncated = len(path) >= max_depth and bool(current_parent_uuids)

            if depth_truncated or not current_parent_uuids:
                paths.append(
                    LPAncestorPath(
                        ancestor_sfi_uuids=path, depth_truncated=depth_truncated
                    )
                )
                continue

            for parent_uuid in reversed(current_parent_uuids):
                pending.append((parent_uuid, (*path, parent_uuid)))

        return LPAncestorPaths(paths=tuple(paths), paths_truncated=bool(pending))

    def nearest_ancestor_uuids(
        self, *, sfi_uuid: UUID, statement_type: str
    ) -> tuple[UUID, ...]:
        """Find the nearest matching ancestors independently across DAG branches.

        Traversal stops on a branch as soon as an ancestor with the requested local
        statement type is found. Other branches continue independently, so a nearer
        match on one path cannot hide a deeper relevant match on another path.
        Framework-root fallback edges are never traversed.

        Parameters
        ----------
        sfi_uuid
            CASE UUID of the SFI whose ancestors are searched.
        statement_type
            Exact caller-supplied local statement type to match.

        Returns
        -------
        tuple[UUID, ...]
            All branch-nearest matching ancestor UUIDs in deterministic order.

        Raises
        ------
        ValueError
            If the SFI is unknown or the requested statement type is blank.
        """

        if sfi_uuid not in self.sfi_by_uuid:
            raise ValueError(f"Unknown SFI CASE UUID: {sfi_uuid}.")

        if not isinstance(statement_type, str) or not statement_type.strip():
            raise ValueError("statement_type must be a non-empty string.")

        statement_type = statement_type.strip()
        matches: set[UUID] = set()
        pending = list(reversed(self.parent_sfi_uuids_by_sfi_uuid[sfi_uuid]))
        visited: set[UUID] = set()

        while pending:
            current_uuid = pending.pop()

            if current_uuid in visited:
                continue

            visited.add(current_uuid)
            current_sfi = self.sfi_by_uuid[current_uuid]

            if current_sfi.statement_type == statement_type:
                matches.add(current_uuid)
                continue

            pending.extend(reversed(self.parent_sfi_uuids_by_sfi_uuid[current_uuid]))

        return tuple(sorted(matches, key=str))


def _build_hierarchy_indexes(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    sfi_by_uuid: Mapping[UUID, StandardsFrameworkItem],
) -> _HierarchyIndexes:
    """Build positive hierarchy and separate framework-root indexes.

    Parameters
    ----------
    as_lc_bundle
        Validated bundle containing final ``hasChild`` relationships.
    sfi_by_uuid
        Final SFIs keyed by CASE UUID.

    Returns
    -------
    _HierarchyIndexes
        Deterministic immutable hierarchy and unresolved-root indexes.

    Raises
    ------
    ValueError
        If hierarchy edges are duplicated, leave SFIs unattached, or form a cycle.
    """

    child_uuids_by_parent_uuid: dict[UUID, set[UUID]] = {
        sfi_uuid: set() for sfi_uuid in sfi_by_uuid
    }
    parent_uuids_by_sfi_uuid: dict[UUID, set[UUID]] = {
        sfi_uuid: set() for sfi_uuid in sfi_by_uuid
    }
    root_fallback_relationships_by_sfi_uuid: dict[UUID, list[Relationship]] = {
        sfi_uuid: [] for sfi_uuid in sfi_by_uuid
    }
    attached_sfi_uuids: set[UUID] = set()
    framework_child_sfi_uuids: set[UUID] = set()
    seen_has_child_pairs: set[tuple[str, UUID, UUID]] = set()

    for relationship in sorted(
        as_lc_bundle.relationships_has_child,
        key=lambda relationship: (
            relationship.source_entity,
            relationship.source_entity_value,
            relationship.target_entity_value,
            str(relationship.identifier),
        ),
    ):
        source_uuid, target_uuid, unresolved_root_fallback = (
            _validate_has_child_relationship(
                framework_uuid=as_lc_bundle.framework.case_identifier_uuid,
                relationship=relationship,
                sfi_by_uuid=sfi_by_uuid,
            )
        )

        pair = (relationship.source_entity, source_uuid, target_uuid)

        if pair in seen_has_child_pairs:
            raise ValueError(
                f"AS+LC bundle contains duplicate hasChild endpoints: "
                f"{relationship.source_entity} {source_uuid} -> {target_uuid}."
            )

        seen_has_child_pairs.add(pair)
        attached_sfi_uuids.add(target_uuid)

        if relationship.source_entity == "StandardsFramework":
            if unresolved_root_fallback:
                root_fallback_relationships_by_sfi_uuid[target_uuid].append(
                    relationship
                )
            else:
                framework_child_sfi_uuids.add(target_uuid)

            continue

        child_uuids_by_parent_uuid[source_uuid].add(target_uuid)
        parent_uuids_by_sfi_uuid[target_uuid].add(source_uuid)

    missing_attachments = sorted(set(sfi_by_uuid) - attached_sfi_uuids, key=str)

    if missing_attachments:
        raise ValueError(
            f"AS+LC bundle contains SFIs without an incoming hasChild relationship: "
            f"{[str(sfi_uuid) for sfi_uuid in missing_attachments]}."
        )

    _validate_acyclic_hierarchy(
        child_uuids_by_parent_uuid=child_uuids_by_parent_uuid,
        parent_uuids_by_sfi_uuid=parent_uuids_by_sfi_uuid,
        sfi_by_uuid=sfi_by_uuid,
    )

    root_fallback_sfi_uuids = tuple(
        sorted(
            (
                sfi_uuid
                for sfi_uuid, relationships in (
                    root_fallback_relationships_by_sfi_uuid.items()
                )
                if relationships
            ),
            key=str,
        )
    )
    unresolved_ancestry_sfi_uuids = _propagate_unresolved_ancestry(
        child_uuids_by_parent_uuid=child_uuids_by_parent_uuid,
        root_fallback_sfi_uuids=root_fallback_sfi_uuids,
    )
    return _HierarchyIndexes(
        child_sfi_uuids_by_parent_sfi_uuid=MappingProxyType(
            {
                sfi_uuid: tuple(sorted(child_uuids, key=str))
                for sfi_uuid, child_uuids in sorted(
                    child_uuids_by_parent_uuid.items(),
                    key=lambda item: str(item[0]),
                )
            }
        ),
        framework_child_sfi_uuids=tuple(sorted(framework_child_sfi_uuids, key=str)),
        parent_sfi_uuids_by_sfi_uuid=MappingProxyType(
            {
                sfi_uuid: tuple(sorted(parent_uuids, key=str))
                for sfi_uuid, parent_uuids in sorted(
                    parent_uuids_by_sfi_uuid.items(),
                    key=lambda item: str(item[0]),
                )
            }
        ),
        root_fallback_relationships_by_sfi_uuid=MappingProxyType(
            {
                sfi_uuid: tuple(
                    sorted(relationships, key=lambda edge: str(edge.identifier))
                )
                for sfi_uuid, relationships in sorted(
                    root_fallback_relationships_by_sfi_uuid.items(),
                    key=lambda item: str(item[0]),
                )
            }
        ),
        root_fallback_sfi_uuids=root_fallback_sfi_uuids,
        unresolved_ancestry_sfi_uuids=unresolved_ancestry_sfi_uuids,
    )


def _build_support_indexes(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    learning_component_by_uuid: Mapping[UUID, LearningComponent],
    sfi_by_uuid: Mapping[UUID, StandardsFrameworkItem],
) -> _SupportIndexes:
    """Build deterministic bidirectional ``supports`` indexes.

    Parameters
    ----------
    as_lc_bundle
        Validated bundle containing final ``supports`` relationships.
    learning_component_by_uuid
        Final Learning Components keyed by identifier.
    sfi_by_uuid
        Final SFIs keyed by CASE UUID.

    Returns
    -------
    _SupportIndexes
        Immutable LC-by-SFI and SFI-by-LC mappings.

    Raises
    ------
    ValueError
        If support pairs are duplicated or a Learning Component is unsupported.
    """

    learning_component_uuids_by_sfi_uuid: dict[UUID, set[UUID]] = {
        sfi_uuid: set() for sfi_uuid in sfi_by_uuid
    }
    sfi_uuids_by_learning_component_uuid: dict[UUID, set[UUID]] = {
        component_uuid: set() for component_uuid in learning_component_by_uuid
    }
    seen_supports_pairs: set[tuple[UUID, UUID]] = set()

    for relationship in sorted(
        as_lc_bundle.relationships_supports,
        key=lambda relationship: (
            relationship.source_entity_value,
            relationship.target_entity_value,
            str(relationship.identifier),
        ),
    ):
        learning_component_uuid, sfi_uuid = _validate_supports_relationship(
            learning_component_by_uuid=learning_component_by_uuid,
            relationship=relationship,
            sfi_by_uuid=sfi_by_uuid,
        )
        support_pair = (learning_component_uuid, sfi_uuid)

        if support_pair in seen_supports_pairs:
            raise ValueError(
                f"AS+LC bundle contains duplicate supports endpoints: "
                f"{learning_component_uuid} -> {sfi_uuid}."
            )

        seen_supports_pairs.add(support_pair)
        learning_component_uuids_by_sfi_uuid[sfi_uuid].add(learning_component_uuid)
        sfi_uuids_by_learning_component_uuid[learning_component_uuid].add(sfi_uuid)

    unsupported_learning_component_uuids = sorted(
        (
            component_uuid
            for component_uuid, supported_sfi_uuids in (
                sfi_uuids_by_learning_component_uuid.items()
            )
            if not supported_sfi_uuids
        ),
        key=str,
    )

    if unsupported_learning_component_uuids:
        raise ValueError(
            f"AS+LC bundle contains Learning Components without supports edges: "
            f"{[str(component_uuid) for component_uuid in unsupported_learning_component_uuids]}."
        )

    return _SupportIndexes(
        learning_components_by_sfi_uuid=MappingProxyType(
            {
                sfi_uuid: tuple(
                    learning_component_by_uuid[component_uuid]
                    for component_uuid in sorted(component_uuids, key=str)
                )
                for sfi_uuid, component_uuids in sorted(
                    learning_component_uuids_by_sfi_uuid.items(),
                    key=lambda item: str(item[0]),
                )
            }
        ),
        sfis_by_learning_component_uuid=MappingProxyType(
            {
                component_uuid: tuple(
                    sfi_by_uuid[sfi_uuid]
                    for sfi_uuid in sorted(supported_sfi_uuids, key=str)
                )
                for component_uuid, supported_sfi_uuids in sorted(
                    sfi_uuids_by_learning_component_uuid.items(),
                    key=lambda item: str(item[0]),
                )
            }
        ),
    )


def _index_learning_components(
    as_lc_bundle: AcademicStandardsLCKGBundle,
) -> Mapping[UUID, LearningComponent]:
    """Index uniquely identified Learning Components in UUID order.

    Parameters
    ----------
    as_lc_bundle
        Validated bundle containing final Learning Components.

    Returns
    -------
    Mapping[UUID, LearningComponent]
        Immutable Learning Component identifier index.

    Raises
    ------
    ValueError
        If Learning Component identifiers are duplicated.
    """

    learning_component_uuids = [
        component.identifier for component in as_lc_bundle.learning_components
    ]

    if len(learning_component_uuids) != len(set(learning_component_uuids)):
        raise ValueError("AS+LC bundle contains duplicate Learning Component UUIDs.")

    return MappingProxyType(
        {
            component.identifier: component
            for component in sorted(
                as_lc_bundle.learning_components,
                key=lambda component: str(component.identifier),
            )
        }
    )


def _index_sfi_provenance(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    sfi_by_uuid: Mapping[UUID, StandardsFrameworkItem],
) -> Mapping[UUID, Mapping[str, Any]]:
    """Index complete SFI source and audit provenance.

    Parameters
    ----------
    as_lc_bundle
        Validated bundle carrying merged entity provenance.
    sfi_by_uuid
        Final SFIs keyed by CASE UUID.

    Returns
    -------
    Mapping[UUID, Mapping[str, Any]]
        Immutable outer and per-SFI provenance mappings.

    Raises
    ------
    ValueError
        If provenance is malformed, missing, or references an unknown SFI.
    """

    items_provenance = as_lc_bundle.entity_provenance.get("items")

    if not isinstance(items_provenance, Mapping):
        raise ValueError(
            "AS+LC entity provenance must contain an 'items' mapping for LP indexing."
        )

    sfi_provenance_by_uuid: dict[UUID, Mapping[str, Any]] = {}

    for raw_sfi_uuid, provenance in items_provenance.items():
        try:
            sfi_uuid = UUID(str(raw_sfi_uuid))
        except ValueError as exc:
            raise ValueError(
                "AS+LC item provenance contains a non-UUID key: " f"{raw_sfi_uuid!r}."
            ) from exc

        if sfi_uuid not in sfi_by_uuid:
            raise ValueError(
                f"AS+LC item provenance references unknown SFI {sfi_uuid}."
            )

        if not isinstance(provenance, Mapping):
            raise ValueError(
                f"AS+LC item provenance for SFI {sfi_uuid} must be a mapping."
            )

        sfi_provenance_by_uuid[sfi_uuid] = MappingProxyType(dict(provenance))

    missing_provenance = sorted(set(sfi_by_uuid) - set(sfi_provenance_by_uuid), key=str)

    if missing_provenance:
        raise ValueError(
            f"AS+LC entity provenance is missing SFI entries: "
            f"{[str(sfi_uuid) for sfi_uuid in missing_provenance]}."
        )

    return MappingProxyType(
        {
            sfi_uuid: sfi_provenance_by_uuid[sfi_uuid]
            for sfi_uuid in sorted(sfi_provenance_by_uuid, key=str)
        }
    )


def _index_sfis(
    as_lc_bundle: AcademicStandardsLCKGBundle,
) -> Mapping[UUID, StandardsFrameworkItem]:
    """Index uniquely identified SFIs in UUID order.

    Parameters
    ----------
    as_lc_bundle
        Validated bundle containing final SFIs.

    Returns
    -------
    Mapping[UUID, StandardsFrameworkItem]
        Immutable SFI CASE UUID index.

    Raises
    ------
    ValueError
        If SFI CASE UUIDs are duplicated.
    """

    sfi_uuids = [item.case_identifier_uuid for item in as_lc_bundle.items]

    if len(sfi_uuids) != len(set(sfi_uuids)):
        raise ValueError("AS+LC bundle contains duplicate SFI CASE UUIDs.")

    return MappingProxyType(
        {
            item.case_identifier_uuid: item
            for item in sorted(
                as_lc_bundle.items, key=lambda item: str(item.case_identifier_uuid)
            )
        }
    )


def _propagate_unresolved_ancestry(
    *,
    child_uuids_by_parent_uuid: Mapping[UUID, set[UUID]],
    root_fallback_sfi_uuids: tuple[UUID, ...],
) -> frozenset[UUID]:
    """Propagate root-fallback ancestry through every positive DAG branch.

    Parameters
    ----------
    child_uuids_by_parent_uuid
        Positive SFI child adjacency.
    root_fallback_sfi_uuids
        Direct targets of unresolved framework-root fallback relationships.

    Returns
    -------
    frozenset[UUID]
        Directly unresolved SFIs and every positive-hierarchy descendant.
    """

    unresolved_ancestry_sfi_uuids = set(root_fallback_sfi_uuids)
    pending_unresolved_sfi_uuids = list(reversed(root_fallback_sfi_uuids))

    while pending_unresolved_sfi_uuids:
        current_uuid = pending_unresolved_sfi_uuids.pop()

        for child_uuid in sorted(
            child_uuids_by_parent_uuid[current_uuid], key=str, reverse=True
        ):
            if child_uuid in unresolved_ancestry_sfi_uuids:
                continue

            unresolved_ancestry_sfi_uuids.add(child_uuid)
            pending_unresolved_sfi_uuids.append(child_uuid)

    return frozenset(unresolved_ancestry_sfi_uuids)


def _validate_acyclic_hierarchy(
    *,
    child_uuids_by_parent_uuid: Mapping[UUID, set[UUID]],
    parent_uuids_by_sfi_uuid: Mapping[UUID, set[UUID]],
    sfi_by_uuid: Mapping[UUID, StandardsFrameworkItem],
) -> None:
    """Reject cycles in the complete positive SFI hierarchy.

    Parameters
    ----------
    child_uuids_by_parent_uuid
        Positive SFI child adjacency.
    parent_uuids_by_sfi_uuid
        Positive SFI parent adjacency.
    sfi_by_uuid
        Complete final SFI index.

    Raises
    ------
    ValueError
        If any SFI remains in a directed cycle.
    """

    remaining_parent_counts = {
        sfi_uuid: len(parent_uuids)
        for sfi_uuid, parent_uuids in parent_uuids_by_sfi_uuid.items()
    }
    ready = sorted(
        (
            sfi_uuid
            for sfi_uuid, parent_count in remaining_parent_counts.items()
            if parent_count == 0
        ),
        key=str,
        reverse=True,
    )
    visited_sfi_uuids: set[UUID] = set()

    while ready:
        current_uuid = ready.pop()
        visited_sfi_uuids.add(current_uuid)

        for child_uuid in sorted(child_uuids_by_parent_uuid[current_uuid], key=str):
            remaining_parent_counts[child_uuid] -= 1

            if remaining_parent_counts[child_uuid] == 0:
                ready.append(child_uuid)
                ready.sort(key=str, reverse=True)

    cyclic_sfi_uuids = sorted(set(sfi_by_uuid) - visited_sfi_uuids, key=str)

    if cyclic_sfi_uuids:
        raise ValueError(
            f"AS+LC hasChild hierarchy contains an SFI cycle involving: "
            f"{[str(sfi_uuid) for sfi_uuid in cyclic_sfi_uuids]}."
        )


def _validate_has_child_relationship(
    *,
    framework_uuid: UUID,
    relationship: Relationship,
    sfi_by_uuid: Mapping[UUID, StandardsFrameworkItem],
) -> tuple[UUID, UUID, bool]:
    """Validate and resolve one final ``hasChild`` relationship.

    Parameters
    ----------
    framework_uuid
        CASE UUID of the bundle framework.
    relationship
        Final relationship to validate.
    sfi_by_uuid
        Complete final SFI index.

    Returns
    -------
    tuple[UUID, UUID, bool]
        Resolved source UUID, target SFI UUID, and fallback state.

    Raises
    ------
    ValueError
        If relationship type, endpoint shape, membership, or fallback state is invalid.
    """

    if relationship.relationship_type != "hasChild":
        raise ValueError(
            f"relationships_has_child contains a non-hasChild relationship: "
            f"{relationship.identifier}."
        )

    if (
        relationship.source_entity_key != "case_identifier_uuid"
        or relationship.target_entity != "StandardsFrameworkItem"
        or relationship.target_entity_key != "case_identifier_uuid"
    ):
        raise ValueError(
            f"hasChild relationship {relationship.identifier} has invalid endpoint shape."
        )

    source_uuid = UUID(relationship.source_entity_value)
    target_uuid = UUID(relationship.target_entity_value)

    if target_uuid not in sfi_by_uuid:
        raise ValueError(
            f"hasChild relationship {relationship.identifier} targets unknown "
            f"SFI {target_uuid}."
        )

    unresolved_root_fallback = relationship.metadata.get(
        "unresolved_root_fallback", False
    )

    if not isinstance(unresolved_root_fallback, bool):
        raise ValueError(
            f"hasChild relationship {relationship.identifier} has a non-boolean "
            f"unresolved_root_fallback value."
        )

    if relationship.source_entity == "StandardsFramework":
        if source_uuid != framework_uuid:
            raise ValueError(
                f"hasChild relationship {relationship.identifier} sources "
                f"framework {source_uuid}, not bundle framework {framework_uuid}."
            )

        return source_uuid, target_uuid, unresolved_root_fallback

    if relationship.source_entity != "StandardsFrameworkItem":
        raise ValueError(
            f"hasChild relationship {relationship.identifier} has unsupported "
            f"source entity {relationship.source_entity!r}."
        )

    if source_uuid not in sfi_by_uuid:
        raise ValueError(
            f"hasChild relationship {relationship.identifier} sources unknown "
            f"SFI {source_uuid}."
        )

    if unresolved_root_fallback:
        raise ValueError(
            f"hasChild relationship {relationship.identifier} marks an "
            f"SFI-to-SFI edge as a framework-root fallback."
        )

    return source_uuid, target_uuid, unresolved_root_fallback


def _validate_supports_relationship(
    *,
    learning_component_by_uuid: Mapping[UUID, LearningComponent],
    relationship: Relationship,
    sfi_by_uuid: Mapping[UUID, StandardsFrameworkItem],
) -> tuple[UUID, UUID]:
    """Validate and resolve one final ``supports`` relationship.

    Parameters
    ----------
    learning_component_by_uuid
        Complete final Learning Component index.
    relationship
        Final relationship to validate.
    sfi_by_uuid
        Complete final SFI index.

    Returns
    -------
    tuple[UUID, UUID]
        Resolved Learning Component and target SFI UUIDs.

    Raises
    ------
    ValueError
        If relationship type, endpoint shape, or membership is invalid.
    """

    if relationship.relationship_type != "supports":
        raise ValueError(
            f"relationships_supports contains a non-supports relationship: "
            f"{relationship.identifier}."
        )

    if (
        relationship.source_entity != "LearningComponent"
        or relationship.source_entity_key != "identifier"
        or relationship.target_entity != "StandardsFrameworkItem"
        or relationship.target_entity_key != "case_identifier_uuid"
    ):
        raise ValueError(
            f"supports relationship {relationship.identifier} has invalid endpoint shape."
        )

    learning_component_uuid = UUID(relationship.source_entity_value)
    sfi_uuid = UUID(relationship.target_entity_value)

    if learning_component_uuid not in learning_component_by_uuid:
        raise ValueError(
            f"supports relationship {relationship.identifier} sources unknown "
            f"Learning Component {learning_component_uuid}."
        )

    if sfi_uuid not in sfi_by_uuid:
        raise ValueError(
            f"supports relationship {relationship.identifier} targets unknown SFI {sfi_uuid}."
        )

    return learning_component_uuid, sfi_uuid


def build_lp_graph_index(as_lc_bundle: AcademicStandardsLCKGBundle) -> LPGraphIndex:
    """Build deterministic LP indexes from a validated AS+LC bundle.

    Parameters
    ----------
    as_lc_bundle
        Final AS+LC bundle whose nodes, relationships, provenance, and validation
        result are authoritative for downstream Learning Progressions work.

    Returns
    -------
    LPGraphIndex
        Immutable graph, provenance, and bidirectional LC-support indexes.

    Raises
    ------
    ValueError
        If upstream validation failed or the bundle cannot be indexed without
        ambiguous, missing, cyclic, or dangling graph material.
    """

    validation_report = as_lc_bundle.validation_report

    if not validation_report.passed or validation_report.errors:
        raise ValueError(
            f"Learning Progressions indexing requires a passed, error-free AS+LC "
            f"validation report (passed={validation_report.passed}, "
            f"errors={validation_report.errors[:3]})."
        )

    sfi_by_uuid = _index_sfis(as_lc_bundle)
    learning_component_by_uuid = _index_learning_components(as_lc_bundle)
    hierarchy_indexes = _build_hierarchy_indexes(
        as_lc_bundle=as_lc_bundle, sfi_by_uuid=sfi_by_uuid
    )
    support_indexes = _build_support_indexes(
        as_lc_bundle=as_lc_bundle,
        learning_component_by_uuid=learning_component_by_uuid,
        sfi_by_uuid=sfi_by_uuid,
    )
    return LPGraphIndex(
        child_sfi_uuids_by_parent_sfi_uuid=(
            hierarchy_indexes.child_sfi_uuids_by_parent_sfi_uuid
        ),
        framework_child_sfi_uuids=hierarchy_indexes.framework_child_sfi_uuids,
        learning_component_by_uuid=learning_component_by_uuid,
        learning_components_by_sfi_uuid=(
            support_indexes.learning_components_by_sfi_uuid
        ),
        parent_sfi_uuids_by_sfi_uuid=(hierarchy_indexes.parent_sfi_uuids_by_sfi_uuid),
        root_fallback_relationships_by_sfi_uuid=(
            hierarchy_indexes.root_fallback_relationships_by_sfi_uuid
        ),
        root_fallback_sfi_uuids=hierarchy_indexes.root_fallback_sfi_uuids,
        sfi_by_uuid=sfi_by_uuid,
        sfi_provenance_by_uuid=_index_sfi_provenance(
            as_lc_bundle=as_lc_bundle, sfi_by_uuid=sfi_by_uuid
        ),
        sfis_by_learning_component_uuid=(
            support_indexes.sfis_by_learning_component_uuid
        ),
        unresolved_ancestry_sfi_uuids=hierarchy_indexes.unresolved_ancestry_sfi_uuids,
    )
