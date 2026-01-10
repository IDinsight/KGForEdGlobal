"""This module contains utility functions for knowledge graphs."""

# Standard Library
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import UUID, uuid5

# Third Party Library
from loguru import logger

# Package Library
from skg.canonical_ir.schemas import CanonicalEdge, CanonicalIR, CanonicalNode
from skg.kgs.schemas import (
    GraphValidationReport,
    KnowledgeGraphConfig,
    KnowledgeGraphExport,
    LearningComponent,
    Relationship,
    StandardsFramework,
    StandardsFrameworkItem,
)
from skg.page_ir.schemas import TextUnit
from skg.schemas import RunCtx
from skg.utils.constants import RelationshipTypes, StatementRole
from skg.utils.general import make_dir, write_to_json

_ROLE_TO_NORMALIZED: dict[StatementRole, str] = {
    StatementRole.DESCRIPTOR: "Other",
    StatementRole.EXPECTATION: "Standard",
    StatementRole.GRADE_LEVEL: "Standard Grouping",
    StatementRole.GUIDANCE: "Other",
    StatementRole.SECTION: "Standard Grouping",
    StatementRole.STRAND: "Standard Grouping",
    StatementRole.SUBJECT: "Standard Grouping",
    StatementRole.TOPIC: "Standard Grouping",
}
_ROLE_TO_STATEMENT_TYPE: dict[StatementRole, str] = {
    StatementRole.DESCRIPTOR: "Descriptor",
    StatementRole.EXPECTATION: "Expectation",
    StatementRole.GRADE_LEVEL: "Grade Level",
    StatementRole.GUIDANCE: "Guidance",
    StatementRole.SECTION: "Section",
    StatementRole.STRAND: "Strand",
    StatementRole.SUBJECT: "Subject",
    StatementRole.TOPIC: "Topic",
}


@dataclass(frozen=True)
class CanonicalIRIndex:
    """Indexed view of a CanonicalIR for deterministic access."""

    canonical_ir: CanonicalIR
    children_by_parent: dict[str, list[str]]
    parents_by_child: dict[str, list[str]]
    node_by_id: dict[str, CanonicalNode]
    report: GraphValidationReport


@dataclass(frozen=True)
class CaseIdentifiers:
    """CASE Identifiers for an entity."""

    uri: str
    uuid: UUID


@dataclass(frozen=True)
class EntityMappingResult:
    """Result of Step 6: CanonicalNode -> LC entities (Framework + SFIs)."""

    # Canonical node_ids dropped at mapping time (e.g., empty text).
    dropped_node_ids: set[str]
    dropped_node_reasons: dict[str, str]
    framework: StandardsFramework
    report: GraphValidationReport

    # Node_ids reclassified by the normative safety filter (old_role --> new_role).
    role_overrides: dict[str, dict[str, str]]

    # Canonical node_id --> exported SFI identifier (entity identifier).
    sfi_uuid_by_canonical_id: dict[str, UUID]

    # Canonical node_id --> exported SFI CASE UUID (used for hasChild endpoints).
    sfi_case_uuid_by_canonical_id: dict[str, UUID]

    standards_framework_items: list[StandardsFrameworkItem]


@dataclass(frozen=True)
class FilteredCanonicalIRIndex:
    """Filtered view of CanonicalIR suitable for export mapping.

    NB: kept_nodes/edges are the ONLY content allowed to flow into mapping/export.
    dropped_* includes deterministic reasons for auditing.
    """

    canonical: Any  # CanonicalIR
    children_by_parent: dict[str, list[str]]
    dropped_edges: list[dict[str, Any]]  # edge and reason
    dropped_node_ids: set[str]
    dropped_node_reasons: dict[str, str]  # node_id --> reason code
    kept_edges: list[CanonicalEdge]
    kept_node_ids: set[str]
    kept_nodes: list[CanonicalNode]
    parents_by_child: dict[str, list[str]]
    report: GraphValidationReport


@dataclass(frozen=True)
class HasChildBuildResult:
    """hasChild relationships from CanonicalIR edges."""

    dropped_edges: list[dict[str, Any]]
    relationships: list[Relationship]
    report: GraphValidationReport


@dataclass(frozen=True)
class LearningComponentBuildResult:
    """LearningComponents and supports relationships."""

    learning_components: list[LearningComponent]
    supports_relationships: list[Relationship]
    report: GraphValidationReport


@dataclass(frozen=True)
class KGDirs:
    """Dataclass for KG directories."""

    root: Path
    cache: Path


@dataclass(frozen=True)
class ResolvedText:
    """Selected text for export with language defaults applied.

    No translation is performed here. This is simply a deterministic selection of
    the best available text unit and the best available language tag (prefer the text
    unit's language tag, else config default).
    """

    in_language: str
    source_language: str
    source_text: str
    text: str
    english_text: Optional[str] = None
    english_language: Optional[str] = None


class DefaultsResolver:
    """Resolve required metadata defaults for export objects."""

    def __init__(self, *, config: KnowledgeGraphConfig) -> None:
        """Initialize DefaultsResolver with KnowledgeGraphConfig.

        Parameters
        ----------
        config
            The KnowledgeGraphConfig to use for defaults.
        """

        self.config = config

    def academic_subject(self, *, override: Optional[str] = None) -> str:
        """Return the academic_subject for entities.

        Parameters
        ----------
        override
            An optional override academic subject.

        Returns
        -------
        str
            The resolved academic_subject.
        """

        if override is not None and override.strip():
            return override.strip()

        return (self.config.academic_subject_default or "General").strip()

    def adoption_status(self, *, override: Optional[str] = None) -> str:
        """Return the adoption_status for StandardsFramework entities.

        Parameters
        ----------
        override
            An optional override adoption status.

        Returns
        -------
        str
            The resolved adoption_status.

        Raises
        ------
        ValueError
            If adoption_status is required but not provided.
        """

        if override is not None:
            return override

        if self.config.adoption_status is None:
            raise ValueError(
                "adoption_status is required for StandardsFramework exports."
            )

        return self.config.adoption_status

    def common(self) -> dict[str, str]:
        """Return common required metadata fields shared by all entities/relationships.

        Returns
        -------
        dict[str, str]
            The common metadata fields.
        """

        return {
            "attribution_statement": self.config.attribution_statement,
            "author": self.config.author,
            "license": self.config.license,
            "provider": self.config.provider,
        }

    @staticmethod
    def framework_title(*, canonical_pdf_name: str | None) -> str:
        """Return the title for the StandardsFramework entity.

        Parameters
        ----------
        canonical_pdf_name
            The canonical PDF name from the CanonicalIR.

        Returns
        -------
        str
            The resolved framework title.
        """

        if canonical_pdf_name and canonical_pdf_name.strip():
            return canonical_pdf_name.strip()

        return "Curriculum Framework"

    def jurisdiction(self, *, override: Optional[str] = None) -> str:
        """Return the jurisdiction for entities.

        Parameters
        ----------
        override
            An optional override jurisdiction.

        Returns
        -------
        str
            The resolved jurisdiction.
        """

        return override or self.config.jurisdiction_default

    @staticmethod
    def relationship_description(*, rel_type: str) -> str:
        """Return a default description for a relationship type.

        Parameters
        ----------
        rel_type
            The relationship type.

        Returns
        -------
        str
            The relationship description.
        """

        if rel_type == "hasChild":
            return "Parent-child hierarchy relationship derived from CanonicalIR."

        if rel_type == "supports":
            return "LearningComponent supports a StandardsFrameworkItem."

        return f"Relationship of type '{rel_type}'."


class DeterministicIdRegistry:
    """Deterministic UUID registry for KG export. All IDs are derived via
    uuid5(namespace_uuid, seed_string). Seeds include doc_key so IDs are unique across
    PDFs even if canonical IDs collide.
    """

    def __init__(self, *, config: KnowledgeGraphConfig, doc_key: str) -> None:
        """Initialize DeterministicIdRegistry.

        Parameters
        ----------
        config
            The KnowledgeGraphConfig to use for namespace UUID.
        doc_key
            The CanonicalIR doc_key to scope the IDs.
        """

        self._cfg = config
        self._doc_key = doc_key.strip().lower()
        self._ns = config.namespace_uuid

    def case_identifiers_for_framework(self) -> CaseIdentifiers:
        """CASE identifier for the framework.

        Returns
        -------
        CaseIdentifiers
            The CASE identifiers for the framework.
        """

        case_uuid = uuid5(self._ns, _seed(self._doc_key, "case", "framework"))

        return CaseIdentifiers(
            uri=self._cfg.case_uri_base + str(case_uuid), uuid=case_uuid
        )

    def case_identifiers_for_sfi(self, *, canonical_node_id: str) -> CaseIdentifiers:
        """CASE identifier for an item (derived from CanonicalIR node id).

        Parameters
        ----------
        canonical_node_id
            The CanonicalIR node_id for the SFI.

        Returns
        -------
        CaseIdentifiers
            The CASE identifiers for the SFI.
        """

        case_uuid = uuid5(
            self._ns, _seed(self._doc_key, "case", "sfi", canonical_node_id)
        )

        return CaseIdentifiers(
            uri=self._cfg.case_uri_base + str(case_uuid), uuid=case_uuid
        )

    def framework_id(self) -> UUID:
        """UUID for the StandardsFramework (one per PDF by default).

        Returns
        -------
        UUID
            UUID for the StandardsFramework.
        """

        return uuid5(self._ns, _seed(self._doc_key, "entity", "framework"))

    def learning_component_id(
        self, *, split_key: str = "0", standard_sfi_id: UUID
    ) -> UUID:
        """UUID for a LearningComponent. Derived from the target standard SFI UUID (not
        text) to keep stability under translation/minor wording changes.

        Parameters
        ----------
        split_key
            The split key for the LearningComponent. "0" for 1-to-1 mapping. Otherwise,
            a deterministic split identifier (e.g. "bullet-2" or hash).
        standard_sfi_id
            The UUID of the StandardsFrameworkItem that this LearningComponent supports.

        Returns
        -------
        UUID
            UUID for the LearningComponent.
        """

        return uuid5(
            self._ns,
            _seed(self._doc_key, "entity", "lc", str(standard_sfi_id), split_key),
        )

    def relationship_id(
        self, *, from_id: UUID, rel_type: RelationshipTypes | str, to_id: UUID
    ) -> UUID:
        """UUID for a relationship record (type + endpoints), doc-scoped.

        Parameters
        ----------
        from_id
            The UUID of the source entity.
        rel_type
            The relationship type.
        to_id
            The UUID of the target entity.

        Returns
        -------
        UUID
            UUID for the relationship.
        """

        return uuid5(
            self._ns,
            _seed(self._doc_key, "rel", str(rel_type), str(from_id), str(to_id)),
        )

    def sfi_id(self, *, canonical_node_id: str) -> UUID:
        """UUID for a StandardsFrameworkItem derived from a CanonicalIR node_id.

        Parameters
        ----------
        canonical_node_id
            The CanonicalIR node_id for the SFI.

        Returns
        -------
        UUID
            UUID for the StandardsFrameworkItem.
        """

        return uuid5(self._ns, _seed(self._doc_key, "entity", "sfi", canonical_node_id))


class SubjectResolver:
    """Deterministically find nearest subject ancestor for a node."""

    def __init__(
        self,
        *,
        config: KnowledgeGraphConfig,
        node_by_id: dict[str, CanonicalNode],
        parents_by_child: dict[str, list[str]],
        root_id: str,
    ) -> None:
        """Initialize SubjectResolver.

        Parameters
        ----------
        config
            The KnowledgeGraphConfig to use for text resolution.
        node_by_id
            Mapping of node_id to CanonicalNode.
        parents_by_child
            Mapping of child_id to list of parent_ids.
        root_id
            The root node_id of the framework.
        """

        self._cache: dict[str, Optional[str]] = {}
        self.config = config
        self.node_by_id = node_by_id

        # Single-parent traversal: follow the FIRST parent in filtered.parents_by_child.
        self.parent_by_child = {
            child_id: parents[0]
            for child_id, parents in parents_by_child.items()
            if parents
        }

        self.root_id = root_id

    def get_subject(self, *, start_node_id: str) -> Optional[str]:
        """Walk up parents to find nearest SUBJECT ancestor, deterministically.

        Parameters
        ----------
        start_node_id
            The starting CanonicalNode node_id.

        Returns
        -------
        Optional[str]
            The resolved subject text, or None if not found.
        """

        if start_node_id in self._cache:
            return self._cache[start_node_id]

        cur = start_node_id
        seen: set[str] = set()
        visited: list[str] = []

        while True:
            # Cycle protection; shouldn't happen, but keep deterministic behavior.
            if cur in seen:
                for v in visited:
                    self._cache[v] = None
                return None

            seen.add(cur)
            visited.append(cur)

            n = self.node_by_id.get(cur)
            if n is not None and n.role == StatementRole.SUBJECT:
                r = _resolve_text(config=self.config, node=n)
                subj = r.text if r else None
                for v in visited:
                    self._cache[v] = subj
                return subj

            p = self.parent_by_child.get(cur)
            if not p or p == self.root_id:
                for v in visited:
                    self._cache[v] = None
                return None
            cur = p


def _build_adjacency_maps(
    *,
    canonical_ir: CanonicalIR,
    node_by_id: dict[str, CanonicalNode],
    report: GraphValidationReport,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Build parent/child adjacency lists and validate edge endpoints.

    Parameters
    ----------
    canonical_ir
        The CanonicalIR to process.
    node_by_id
        Mapping of node_id to CanonicalNode.
    report
        The GraphValidationReport to log warnings/errors to.

    Returns
    -------
    tuple[dict[str, list[str]], dict[str, list[str]]]
        Tuple containing (children_by_parent, parents_by_child).
    """

    _children_seen: dict[str, set[str]] = {}
    _parents_seen: dict[str, set[str]] = {}

    children_by_parent: dict[str, list[str]] = {}
    parents_by_child: dict[str, list[str]] = {}

    missing_endpoint_edges: list[dict[str, str]] = []
    non_haschild_edges: list[dict[str, str]] = []

    for e in canonical_ir.edges:
        if e.rel != "hasChild":
            non_haschild_edges.append(
                {"parent_id": e.parent_id, "child_id": e.child_id, "rel": e.rel}
            )
            continue

        if e.parent_id not in node_by_id or e.child_id not in node_by_id:
            missing_endpoint_edges.append(
                {"parent_id": e.parent_id, "child_id": e.child_id}
            )
            continue

        # Children list.
        if e.parent_id not in children_by_parent:
            _children_seen[e.parent_id] = set()
            children_by_parent[e.parent_id] = []
        if e.child_id not in _children_seen[e.parent_id]:
            _children_seen[e.parent_id].add(e.child_id)
            children_by_parent[e.parent_id].append(e.child_id)

        # Parents list.
        if e.child_id not in parents_by_child:
            _parents_seen[e.child_id] = set()
            parents_by_child[e.child_id] = []
        if e.parent_id not in _parents_seen[e.child_id]:
            _parents_seen[e.child_id].add(e.parent_id)
            parents_by_child[e.child_id].append(e.parent_id)

    if non_haschild_edges:
        report.warn(
            "unexpected_edge_rel",
            "Found edges whose rel is not 'hasChild'. These were ignored in adjacency build.",
            {"examples": non_haschild_edges[:25], "n": len(non_haschild_edges)},
        )

    if missing_endpoint_edges:
        report.error(
            "edge_missing_endpoint",
            "One or more edges reference missing parent/child node IDs.",
            {"examples": missing_endpoint_edges[:25], "n": len(missing_endpoint_edges)},
        )

    return children_by_parent, parents_by_child


def _build_filtered_adjacency_step(
    *, kept_edges: list[CanonicalEdge], report: GraphValidationReport
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Build adjacency maps for kept edges and check for tree violations.

    Parameters
    ----------
    kept_edges
        The list of kept CanonicalEdge objects.
    report
        The GraphValidationReport to log warnings/errors to.

    Returns
    -------
    tuple[dict[str, list[str]], dict[str, list[str]]]
        Tuple containing (children_by_parent, parents_by_child).
    """

    _seen_child: dict[str, set[str]] = {}
    _seen_parent: dict[str, set[str]] = {}

    children_by_parent: dict[str, list[str]] = {}
    parents_by_child: dict[str, list[str]] = {}

    for e in kept_edges:
        if e.parent_id not in children_by_parent:
            _seen_child[e.parent_id] = set()
            children_by_parent[e.parent_id] = []
        if e.child_id not in _seen_child[e.parent_id]:
            _seen_child[e.parent_id].add(e.child_id)
            children_by_parent[e.parent_id].append(e.child_id)
        if e.child_id not in parents_by_child:
            _seen_parent[e.child_id] = set()
            parents_by_child[e.child_id] = []
        if e.parent_id not in _seen_parent[e.child_id]:
            _seen_parent[e.child_id].add(e.parent_id)
            parents_by_child[e.child_id].append(e.parent_id)

    # Early warning: multi-parents remain after filtering.
    multi_parent_after: list[dict[str, Any]] = []
    for child_id, parents in parents_by_child.items():
        if len(parents) > 1:
            multi_parent_after.append({"child_id": child_id, "parents": parents})
    if multi_parent_after:
        report.warn(
            "multiple_parents_after_filtering",
            "After filtering, some nodes still have multiple parents (tree violation).",
            {"n": len(multi_parent_after), "examples": multi_parent_after[:25]},
        )

    return children_by_parent, parents_by_child


def _build_node_map(
    *, canonical_ir: CanonicalIR, report: GraphValidationReport
) -> dict[str, CanonicalNode]:
    """Build node_id to CanonicalNode map and check for duplicates.

    Parameters
    ----------
    canonical_ir
        The CanonicalIR to process.
    report
        The GraphValidationReport to log warnings/errors to.

    Returns
    -------
    dict[str, CanonicalNode]
        Mapping of node_id to CanonicalNode.
    """

    duplicate_node_ids: list[str] = []
    node_by_id: dict[str, CanonicalNode] = {}

    for n in canonical_ir.nodes:
        if n.node_id in node_by_id:
            duplicate_node_ids.append(n.node_id)
        else:
            node_by_id[n.node_id] = n

    if duplicate_node_ids:
        report.error(
            "duplicate_node_id",
            "CanonicalIR contains duplicate node_id values.",
            {"duplicate_node_ids": sorted(set(duplicate_node_ids))[:50]},
        )

    return node_by_id


def _check_connectivity(
    *,
    adjacency: dict[UUID, list[UUID]],
    exported_sfi_identifiers: set[UUID],
    root_identifier: UUID,
) -> list[str]:
    """Perform BFS to find unreachable SFIs.

    Parameters
    ----------
    adjacency
        Adjacency list mapping from parent SFI identifier to list of child SFI
    exported_sfi_identifiers
        Set of all exported SFI identifiers.
    root_identifier
        The root framework identifier.

    Returns
    -------
    list[str]
        Sorted list of unreachable SFI identifiers as strings.
    """

    queue: list[UUID] = [root_identifier]
    reachable: set[UUID] = set()

    while queue:
        cur = queue.pop(0)

        if cur in reachable:
            continue

        reachable.add(cur)

        for nxt in adjacency.get(cur, []):
            if nxt not in reachable:
                queue.append(nxt)

    return sorted(
        [str(sid) for sid in (exported_sfi_identifiers - reachable)], key=lambda x: x
    )


def _check_english_text_policy(
    *,
    canonical_doc_key: str,
    canonical_pdf_name: Optional[str],
    canonical_root_id: str,
    config: KnowledgeGraphConfig,
    kept_nodes: list[CanonicalNode],
    report: GraphValidationReport,
) -> None:
    """Validate and log stats regarding the English text preference policy.

    Parameters
    ----------
    canonical_doc_key
        The canonical document key.
    canonical_pdf_name
        The canonical PDF name.
    canonical_root_id
        The root node ID (skipped during scan).
    config
        The KnowledgeGraphConfig.
    kept_nodes
        The list of nodes kept after filtering.
    report
        The GraphValidationReport to update.
    """

    if config.description_text_policy != "prefer_text_en":
        return

    nodes_with_text_en = 0
    nodes_scanned = 0

    for n in kept_nodes:
        if n.node_id == canonical_root_id:
            continue

        nodes_scanned += 1

        # Check BOTH title/body units for usable text_en.
        for unit in (getattr(n, "title", None), getattr(n, "body", None)):
            if unit and (getattr(unit, "text_en", None) or "").strip():
                nodes_with_text_en += 1
                break

    report.stats.setdefault("text_resolution", {})
    report.stats["text_resolution"].update(
        {
            "description_text_policy": config.description_text_policy,
            "nodes_scanned": nodes_scanned,
            "nodes_with_text_en": nodes_with_text_en,
        }
    )

    if nodes_with_text_en == 0:
        report.warn(
            "description_text_policy_no_text_en",
            'description_text_policy="prefer_text_en" but CanonicalIR contains no '
            "usable text_en; exporter will fall back to source text.",
            {
                "pdf_name": canonical_pdf_name,
                "doc_key": canonical_doc_key,
                "description_text_policy": config.description_text_policy,
            },
        )


def _compute_reachability_from_edges(
    *, edges: list[CanonicalEdge], root_id: str
) -> set[str]:
    """Compute the set of reachable node IDs from the root via the provided edges.

    Parameters
    ----------
    edges
        The edges to traverse.
    root_id
        The starting root node ID.

    Returns
    -------
    set[str]
        Set of reachable node IDs.
    """

    adj: dict[str, list[str]] = {}
    for e in edges:
        adj.setdefault(e.parent_id, []).append(e.child_id)

    reachable: set[str] = {root_id}
    queue: list[str] = [root_id]

    while queue:
        nid = queue.pop()
        for child_id in adj.get(nid, []):
            if child_id not in reachable:
                reachable.add(child_id)
                queue.append(child_id)

    return reachable


def _create_framework_entity(
    *,
    canonical_doc_key: str,
    canonical_pdf_name: Optional[str],
    config: KnowledgeGraphConfig,
    defaults: DefaultsResolver,
    ids: DeterministicIdRegistry,
    root_node: CanonicalNode,
) -> tuple[StandardsFramework, Optional[str]]:
    """Create the StandardsFramework entity.

    Parameters
    ----------
    canonical_doc_key
        The canonical document key.
    canonical_pdf_name
        The canonical PDF name from the CanonicalIR.
    config
        The KnowledgeGraphConfig to use for text resolution.
    defaults
        DefaultsResolver instance.
    ids
        DeterministicIdRegistry instance.
    root_node
        The root CanonicalNode of the framework.

    Returns
    -------
    tuple[StandardsFramework, Optional[str]]
        The framework entity and an optional warning reason if fallback title used.
    """

    fw_resolved = _resolve_text(config=config, node=root_node)
    warning_reason = None

    if fw_resolved is None:
        fallback = defaults.framework_title(canonical_pdf_name=canonical_pdf_name)

        # Keep fallback language deterministic and consistent with the export policy.
        fallback_in_lang = (
            (config.language_default or "und").strip() or "und"
            if config.export_in_language_policy == "default"
            else "und"
        )

        fw_resolved = ResolvedText(
            in_language=fallback_in_lang,
            source_language="und",
            source_text=fallback,
            text=fallback,
            english_text=None,
            english_language=None,
        )
        warning_reason = "framework_title_missing"

    fw_identifier = ids.framework_id()
    fw_case = ids.case_identifiers_for_framework()
    common = defaults.common()

    metadata = {
        "docKey": canonical_doc_key,
        "canonicalNodeId": root_node.node_id,
        "canonicalRole": _role_str(root_node.role),
        "pageIndices": root_node.page_indices,
        "bbox": root_node.bbox,
        "sourceIds": root_node.source_ids,
        "sourceText": fw_resolved.source_text,
        "sourceLanguage": fw_resolved.source_language,
    }

    # Use the resolved english fields.
    if fw_resolved.english_text:
        metadata["englishText"] = fw_resolved.english_text
        metadata["englishLanguage"] = fw_resolved.english_language or "en"

    framework = StandardsFramework(
        academic_subject=defaults.academic_subject(),
        adoption_status=defaults.adoption_status(),
        attribution_statement=common["attribution_statement"],
        author=common["author"],
        case_identifier_uri=fw_case.uri,
        case_identifier_uuid=fw_case.uuid,
        description=None,
        identifier=fw_identifier,
        in_language=fw_resolved.in_language,
        license=common["license"],
        metadata=metadata,
        name=fw_resolved.text,
        jurisdiction=defaults.jurisdiction(),
        provider=common["provider"],
    )
    return framework, warning_reason


def _create_relationship(
    *,
    canonical_doc_key: str,
    common_metadata: dict[str, str],
    defaults: DefaultsResolver,
    edge: CanonicalEdge,
    from_case_uuid: UUID,
    from_identifier: UUID,
    ids: DeterministicIdRegistry,
    source_entity: str,
    to_case_uuid: UUID,
    to_identifier: UUID,
) -> Relationship:
    """Create a generic hasChild Relationship object.

    Parameters
    ----------
    canonical_doc_key
        The canonical document key.
    common_metadata
        Common metadata dictionary.
    defaults
        DefaultsResolver instance.
    edge
        The CanonicalEdge being processed.
    from_case_uuid
        The source CASE UUID.
    from_identifier
        The source entity identifier.
    ids
        DeterministicIdRegistry instance.
    source_entity
        The source entity type string.
    to_case_uuid
        The target CASE UUID.
    to_identifier
        The target entity identifier.

    Returns
    -------
    Relationship
        The created Relationship object.
    """

    rel_identifier = ids.relationship_id(
        from_id=from_case_uuid, rel_type="hasChild", to_id=to_case_uuid
    )

    return Relationship(
        attribution_statement=common_metadata["attribution_statement"],
        author=common_metadata["author"],
        description=defaults.relationship_description(rel_type="hasChild"),
        identifier=rel_identifier,
        license=common_metadata["license"],
        metadata={
            "docKey": canonical_doc_key,
            "canonicalParentId": edge.parent_id,
            "canonicalChildId": edge.child_id,
            "sourceIdentifier": str(from_identifier),
            "targetIdentifier": str(to_identifier),
            "sourceCaseUUID": str(from_case_uuid),
            "targetCaseUUID": str(to_case_uuid),
            "policy": "tree_keep_first_parent_in_edge_order",
        },
        provider=common_metadata["provider"],
        relationship_type="hasChild",
        source_entity=source_entity,
        source_entity_key="caseIdentifierUUID",
        source_entity_value=str(from_case_uuid),
        target_entity="StandardsFrameworkItem",
        target_entity_key="caseIdentifierUUID",
        target_entity_value=str(to_case_uuid),
    )


def _create_sfi_entity(
    *,
    defaults: DefaultsResolver,
    effective_role: StatementRole,
    ids: DeterministicIdRegistry,
    node: CanonicalNode,
    resolved: ResolvedText,
    subject_text: Optional[str],
) -> tuple[StandardsFrameworkItem, UUID, UUID]:
    """Create a StandardsFrameworkItem entity.

    Parameters
    ----------
    defaults
        DefaultsResolver instance.
    effective_role
        The effective StatementRole for the node.
    ids
        DeterministicIdRegistry instance.
    node
        The CanonicalNode being processed.
    resolved
        The ResolvedText for the node.
    subject_text
        The resolved subject text for the node.

    Returns
    -------
    tuple[StandardsFrameworkItem, UUID, UUID]
        The SFI entity, its identifier UUID, and its CASE UUID.
    """

    normalized = _ROLE_TO_NORMALIZED[effective_role]
    statement_type = _ROLE_TO_STATEMENT_TYPE.get(
        effective_role, _role_str(effective_role)
    )

    sfi_identifier = ids.sfi_id(canonical_node_id=node.node_id)
    sfi_case = ids.case_identifiers_for_sfi(canonical_node_id=node.node_id)

    academic_subject = defaults.academic_subject(override=subject_text)
    grade_level = [resolved.text] if effective_role == StatementRole.GRADE_LEVEL else []
    common = defaults.common()

    metadata = {
        "docKey": node.doc_key,
        "canonicalNodeId": node.node_id,
        "canonicalRole": _role_str(node.role),
        "effectiveRole": _role_str(effective_role),
        "pageIndices": node.page_indices,
        "bbox": node.bbox,
        "sourceIds": node.source_ids,
        "sourceText": resolved.source_text,
        "sourceLanguage": resolved.source_language,
    }

    if resolved.english_text:
        metadata["englishText"] = resolved.english_text
        metadata["englishLanguage"] = resolved.english_language or "en"

    sfi = StandardsFrameworkItem(
        academic_subject=academic_subject,
        attribution_statement=common["attribution_statement"],
        author=common["author"],
        case_identifier_uri=sfi_case.uri,
        case_identifier_uuid=sfi_case.uuid,
        description=resolved.text,
        grade_level=grade_level,
        identifier=sfi_identifier,
        in_language=resolved.in_language,
        jurisdiction=defaults.jurisdiction(),
        license=common["license"],
        metadata=metadata,
        normalized_statement_type=normalized,
        provider=common["provider"],
        statement_code=node.list_id,
        statement_type=statement_type,
    )

    return sfi, sfi_identifier, sfi_case.uuid


def _deep_merge_dict(*, dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge src into dst (mutates dst). src wins on conflicts.

    Parameters
    ----------
    dst
        The destination dictionary to merge into.
    src
        The source dictionary to merge from.

    Returns
    -------
    dict[str, Any]
        The merged dictionary (same as dst).
    """

    for k, v in src.items():
        if k in dst and isinstance(dst[k], dict) and isinstance(v, dict):
            _deep_merge_dict(dst=dst[k], src=v)
        else:
            dst[k] = v

    return dst


def _extract_english_text(*, node: CanonicalNode) -> Optional[str]:
    """Return node.text_en if present and non-empty, else None.

    Parameters
    ----------
    node
        The CanonicalNode to extract text from.

    Returns
    -------
    Optional[str]
        The extracted English text, or None if not present.
    """

    unit = _select_text_unit(node=node)
    if unit is None:
        return None

    text_en = getattr(unit, "text_en", None)
    if not text_en:
        return None

    return str(text_en).strip() or None


def _filter_edges(
    *,
    canonical_edges: list[CanonicalEdge],
    dropped_node_reasons: dict[str, str],
    kept_node_ids: set[str],
    report: GraphValidationReport,
) -> tuple[list[CanonicalEdge], list[dict[str, Any]]]:
    """Filter edges based on kept nodes and populate report stats.

    Parameters
    ----------
    canonical_edges
        The list of CanonicalEdge objects to filter.
    dropped_node_reasons
        Mapping of dropped node_id to drop reason code.
    kept_node_ids
        Set of kept node IDs.
    report
        The GraphValidationReport to log warnings/errors to.

    Returns
    -------
    tuple[list[CanonicalEdge], list[dict[str, Any]]]
        Tuple containing the list of kept edges and list of dropped edge info dicts.
    """

    kept_edges: list[CanonicalEdge] = []
    dropped_edges: list[dict[str, Any]] = []

    for e in canonical_edges:
        if e.rel != "hasChild":
            dropped_edges.append(
                {
                    "parent_id": e.parent_id,
                    "child_id": e.child_id,
                    "rel": e.rel,
                    "reason": "drop_non_haschild_edge",
                }
            )
            continue

        parent_kept = e.parent_id in kept_node_ids
        child_kept = e.child_id in kept_node_ids

        if parent_kept and child_kept:
            kept_edges.append(e)
        else:
            reason = "drop_edge_missing_endpoint_after_filter"
            if not parent_kept and not child_kept:
                reason = "drop_edge_parent_and_child_dropped"
            elif not parent_kept:
                reason = "drop_edge_parent_dropped"
            elif not child_kept:
                reason = "drop_edge_child_dropped"

            dropped_edges.append(
                {
                    "parent_id": e.parent_id,
                    "child_id": e.child_id,
                    "rel": e.rel,
                    "reason": reason,
                    "parent_drop_reason": dropped_node_reasons.get(e.parent_id),
                    "child_drop_reason": dropped_node_reasons.get(e.child_id),
                }
            )

    if dropped_edges:
        # Log examples; keep it bounded for report size.
        report.info(
            "edges_dropped_by_filtering",
            "Some hasChild edges were dropped because one or both endpoints were filtered out.",
            {"n_dropped_edges": len(dropped_edges), "examples": dropped_edges[:25]},
        )

    report.stats.setdefault("filtering", {})
    report.stats["filtering"].update(
        {"kept_edges": len(kept_edges), "dropped_edges": len(dropped_edges)}
    )

    return kept_edges, dropped_edges


def _filter_edges_by_kept_nodes(
    *, drop_reason: str, edges: list[CanonicalEdge], kept_node_ids: set[str]
) -> tuple[list[CanonicalEdge], list[dict[str, Any]]]:
    """Filter edges, keeping only those where both parent and child are in
    kept_node_ids.

    Parameters
    ----------
    drop_reason
        The reason string to assign to dropped edges.
    edges
        The list of edges to filter.
    kept_node_ids
        The set of valid node IDs.

    Returns
    -------
    tuple[list[CanonicalEdge], list[dict[str, Any]]]
        (kept_edges, dropped_edges_dicts)
    """

    kept: list[CanonicalEdge] = []
    dropped: list[dict[str, Any]] = []

    for e in edges:
        if (e.parent_id in kept_node_ids) and (e.child_id in kept_node_ids):
            kept.append(e)
        else:
            dropped.append(
                {
                    "parent_id": e.parent_id,
                    "child_id": e.child_id,
                    "rel": e.rel,
                    "reason": drop_reason,
                }
            )

    return kept, dropped


def _filter_nodes(
    *,
    canonical: CanonicalIR,
    config: KnowledgeGraphConfig,
    index: CanonicalIRIndex,
    report: GraphValidationReport,
) -> tuple[list[CanonicalNode], set[str], set[str], dict[str, str]]:
    """Filter nodes based on config and role policies, and populate report stats.

    Parameters
    ----------
    canonical
        The CanonicalIR to filter.
    config
        The KnowledgeGraphConfig to use for filtering rules.
    index
        The CanonicalIRIndex for node lookup.
    report
        The GraphValidationReport to log warnings/errors to.

    Returns
    -------
    tuple[list[CanonicalNode], set[str], set[str], dict[str, str]]
        Tuple containing (kept_nodes, kept_node_ids, dropped_node_ids, dropped_node_re
    """

    dropped_node_ids: set[str] = set()
    dropped_node_reasons: dict[str, str] = {}
    kept_node_ids: set[str] = set()
    kept_nodes: list[CanonicalNode] = []

    for node in canonical.nodes:
        reason = _node_drop_reason(config=config, node=node)
        if reason is None:
            kept_nodes.append(node)
            kept_node_ids.add(node.node_id)
        else:
            dropped_node_ids.add(node.node_id)
            dropped_node_reasons[node.node_id] = reason

    # Ensure root is kept (hard requirement for export).
    if canonical.root_id not in kept_node_ids:
        root_node = index.node_by_id.get(canonical.root_id)
        root_role = _role_str(root_node.role) if root_node else None
        report.error(
            "root_dropped",
            "CanonicalIR.root_id was dropped by filtering; cannot export deterministically.",
            {
                "root_id": canonical.root_id,
                "root_role": root_role,
                "root_drop_reason": dropped_node_reasons.get(canonical.root_id),
            },
        )
        report.raise_if_errors()

    # Log unresolved[] exclusion (separate from UNRESOLVED role nodes).
    n_unresolved_blocks = len(getattr(canonical, "unresolved", []) or [])
    if n_unresolved_blocks:
        report.info(
            "unresolved_blocks_excluded",
            "CanonicalIR.unresolved[] blocks are excluded from export by default.",
            {"n_unresolved_blocks": n_unresolved_blocks},
        )

    # Role-based stats (dropped/kept).
    dropped_role_counts: dict[str, int] = {}
    kept_role_counts: dict[str, int] = {}

    for n in kept_nodes:
        rs = _role_str(n.role)
        kept_role_counts[rs] = kept_role_counts.get(rs, 0) + 1

    for node_id in dropped_node_ids:
        rs = _role_str(index.node_by_id[node_id].role)
        dropped_role_counts[rs] = dropped_role_counts.get(rs, 0) + 1

    report.stats.update(
        {
            "filtering": {
                "include_descriptors": config.include_descriptors,
                "include_guidance": config.include_guidance,
                "kept_nodes": len(kept_nodes),
                "dropped_nodes": len(dropped_node_ids),
                "kept_role_counts": kept_role_counts,
                "dropped_role_counts": dropped_role_counts,
            }
        }
    )

    return kept_nodes, kept_node_ids, dropped_node_ids, dropped_node_reasons


def _finalize_has_child_report(
    *,
    drop_counters: dict[str, int],
    dropped_edges: list[dict[str, Any]],
    exported_sfi_identifiers: set[UUID],
    relationships: list[Relationship],
    report: GraphValidationReport,
    sfis: list[StandardsFrameworkItem],
    unreachable_sfis: list[str],
) -> None:
    """Log warnings and update stats for hasChild build results.

    Parameters
    ----------
    drop_counters
        Mapping of drop reason codes to counts.
    dropped_edges
        List of dropped edge info dicts.
    exported_sfi_identifiers
        Set of all exported SFI identifiers.
    relationships
        List of created hasChild Relationship objects.
    report
        The GraphValidationReport to log warnings/errors to.
    sfis
        List of created StandardsFrameworkItem entities.
    unreachable_sfis
        List of unreachable SFI identifier strings.
    """

    if not relationships:
        report.warn(
            "no_haschild_relationships",
            "No hasChild relationships were produced. The exported graph will be disconnected.",
            {"n_sfis": len(sfis)},
        )

    if unreachable_sfis:
        report.warn(
            "unreachable_sfis",
            "Some exported StandardsFrameworkItems are not reachable from the framework via hasChild.",
            {"n_unreachable": len(unreachable_sfis), "examples": unreachable_sfis[:25]},
        )

    if dropped_edges:
        report.info(
            "haschild_edges_dropped",
            "Some hasChild edges were dropped due to missing endpoints or tree constraints.",
            {"n_dropped": len(dropped_edges), "examples": dropped_edges[:25]},
        )

    report.stats.setdefault("hierarchy", {})
    report.stats["hierarchy"].update(
        {
            "hasChild_relationships": len(relationships),
            "dropped_edges_total": len(dropped_edges),
            "dropped_missing_endpoint": drop_counters["missing_endpoint"],
            "dropped_child_is_root": drop_counters["child_is_root"],
            "dropped_self_loop": drop_counters["self_loop"],
            "dropped_multi_parent": drop_counters["multi_parent"],
            "dropped_duplicates": drop_counters["duplicates"],
            "reachable_sfi_count": len(exported_sfi_identifiers)
            - len(unreachable_sfis),
            "unreachable_sfi_count": len(unreachable_sfis),
        }
    )


def _has_alpha(text: str) -> bool:
    """Return True if text has any alphabetic characters.

    Parameters
    ----------
    text
        The text to evaluate.

    Returns
    -------
    bool
        True if text has any alphabetic characters, else False.
    """

    return any(ch.isalpha() for ch in (text or ""))


def _has_heading_provenance(node: CanonicalNode) -> bool:
    """Return True if node has heading provenance cues.

    Parameters
    ----------
    node
        The CanonicalNode to evaluate.

    Returns
    -------
    bool
        True if node has heading provenance cues, else False.
    """

    return any(sid.startswith("block:heading") for sid in node.source_ids)


def _has_paragraph_provenance(node: CanonicalNode) -> bool:
    """Return True if node has paragraph provenance cues.

    Parameters
    ----------
    node
        The CanonicalNode to evaluate.

    Returns
    -------
    bool
        True if node has paragraph provenance cues, else False.
    """

    return any(sid.startswith("block:paragraph") for sid in node.source_ids)


def _identify_dead_group_ids(
    *,
    children_by_parent: dict[str, list[str]],
    kept_nodes: list[CanonicalNode],
    root_id: str,
) -> set[str]:
    """Identify grouping nodes that have no EXPECTATION descendants.

    Parameters
    ----------
    children_by_parent
        Adjacency list.
    kept_nodes
        List of currently kept nodes.
    root_id
        The framework root ID.

    Returns
    -------
    set[str]
        Set of node IDs identified as 'dead' groupings.
    """

    group_roles: set[StatementRole] = {
        StatementRole.GRADE_LEVEL,
        StatementRole.SECTION,
        StatementRole.STRAND,
        StatementRole.SUBJECT,
        StatementRole.TOPIC,
    }

    expectation_ids = {
        n.node_id for n in kept_nodes if n.role == StatementRole.EXPECTATION
    }

    # Postorder traversal from root to ensure children processed before parents.
    postorder: list[str] = []
    stack: list[str] = [root_id]
    seen: set[str] = set()

    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        postorder.append(nid)
        for child_id in children_by_parent.get(nid, []):
            stack.append(child_id)

    has_expect_desc: dict[str, bool] = {}

    # Iterate in reverse postorder (leaves first).
    for nid in reversed(postorder):
        # Is this node an expectation?
        is_expect = nid in expectation_ids

        # Does it have any children that are/have expectations?
        child_has_expect = False
        for child_id in children_by_parent.get(nid, []):
            if has_expect_desc.get(child_id, False):
                child_has_expect = True
                break

        has_expect_desc[nid] = is_expect or child_has_expect

    dead_group_ids = {
        n.node_id
        for n in kept_nodes
        if (n.role in group_roles) and (not has_expect_desc.get(n.node_id, False))
    }

    return dead_group_ids


def _is_table_derived(node: CanonicalNode) -> bool:
    """Return True if node has table-derived provenance cues.

    Parameters
    ----------
    node
        The CanonicalNode to evaluate.

    Returns
    -------
    bool
        True if node has table-derived provenance cues, else False.
    """

    for sid in node.source_ids:
        if sid.startswith("prov:table_spec=") or sid.startswith("table:"):
            return True

    return False


def _log_dropped_nodes(
    *,
    dropped_node_ids: set[str],
    dropped_node_reasons: dict[str, str],
    node_by_id: dict[str, CanonicalNode],
    report: GraphValidationReport,
) -> None:
    """Log information about nodes dropped during the mapping phase.

    Parameters
    ----------
    dropped_node_ids
        Set of dropped node IDs.
    dropped_node_reasons
        Mapping of node ID to drop reason.
    node_by_id
        Mapping of node ID to CanonicalNode.
    report
        The GraphValidationReport to update.
    """

    if not dropped_node_ids:
        return

    examples: list[dict[str, Any]] = []

    # Sort for deterministic logging.
    for nid in sorted(list(dropped_node_ids))[:25]:
        examples.append(
            {
                "node_id": nid,
                "reason": dropped_node_reasons.get(nid),
                "role": (
                    _role_str(node_by_id[nid].role) if nid in node_by_id else None
                ),
            }
        )
    report.info(
        "nodes_dropped_at_mapping",
        "Some nodes were dropped during mapping (usually empty/invalid text).",
        {"n": len(dropped_node_ids), "examples": examples},
    )


def _node_drop_reason(
    *, config: KnowledgeGraphConfig, node: CanonicalNode
) -> Optional[str]:
    """Return a deterministic drop reason code, or None if node should be kept.

    Parameters
    ----------
    config
        The KnowledgeGraphConfig to use for filtering rules.
    node
        The CanonicalNode to evaluate.

    Returns
    -------
    Optional[str]
        The drop reason code, or None if the node should be kept.
    """

    role = node.role

    if role == StatementRole.UNRESOLVED:
        return "drop_unresolved_role"

    if role == StatementRole.DESCRIPTOR and not config.include_descriptors:
        return "drop_descriptors_disabled"

    if role == StatementRole.GUIDANCE and not config.include_guidance:
        return "drop_guidance_disabled"

    # Keep everything else (FRAMEWORK, SUBJECT, GRADE_LEVEL, STRAND, SECTION, TOPIC,
    # EXPECTATION, etc.)
    return None


def _node_text_debug(node: CanonicalNode) -> dict[str, Any]:
    """Return a debug dict summarizing a CanonicalNode's text provenance.

    Parameters
    ----------
    node
        The CanonicalNode to summarize.

    Returns
    -------
    dict[str, Any]
        The debug summary dictionary.
    """

    return {
        "node_id": node.node_id,
        "role": _role_str(node.role),
        "list_id": node.list_id,
        "page_indices": node.page_indices,
        "source_ids": node.source_ids[:10],
    }


def _normative_safety_override(
    *, node: CanonicalNode, resolved: ResolvedText
) -> tuple[Optional[StatementRole], Optional[str]]:
    """Return (new_role, reason) or (None, None) to keep role unchanged.

    Deterministic, layout/provenance-biased (NOT country-specific):

    1. If EXPECTATION is actually a heading --> reclassify to SECTION.
    2. If EXPECTATION comes from long paragraph text and not table-derived -->
        reclassify to GUIDANCE.
    3. If EXPECTATION has no alphabetic characters or is too short --> signal drop at
        mapping time (handled elsewhere; this returns None here).

    Parameters
    ----------
    node
        The CanonicalNode to evaluate.
    resolved
        The ResolvedText for the node.

    Returns
    -------
    tuple[Optional[StatementRole], Optional[str]]
        The (new_role, reason) or (None, None) to keep role unchanged.
    """

    if node.role != StatementRole.EXPECTATION:
        return None, None

    text = (resolved.source_text or "").strip()

    if not text or len(text) < 3 or not _has_alpha(text):
        return None, None

    if _has_heading_provenance(node):
        return StatementRole.SECTION, "reclassify_expectation_heading_to_section"

    if (
        (not _is_table_derived(node))
        and _has_paragraph_provenance(node)
        and len(text) >= 160
    ):
        return (
            StatementRole.GUIDANCE,
            "reclassify_expectation_long_paragraph_to_guidance",
        )

    return None, None


def _populate_index_stats(
    *,
    canonical_ir: CanonicalIR,
    children_by_parent: dict[str, list[str]],
    parents_by_child: dict[str, list[str]],
    report: GraphValidationReport,
) -> None:
    """Compute and populate validation statistics.

    Parameters
    ----------
    canonical_ir
        The CanonicalIR to analyze.
    children_by_parent
        Mapping of parent_id to list of child_ids.
    parents_by_child
        Mapping of child_id to list of parent_ids.
    report
        The GraphValidationReport to populate.
    """

    role_counts: dict[str, int] = {}

    for n in canonical_ir.nodes:
        role = getattr(n, "role", None)
        role_str = getattr(role, "value", role)
        role_counts[str(role_str)] = role_counts.get(str(role_str), 0) + 1

    report.stats.update(
        {
            "n_nodes": len(canonical_ir.nodes),
            "n_edges": len(canonical_ir.edges),
            "n_unresolved": len(getattr(canonical_ir, "unresolved", []) or []),
            "role_counts": role_counts,
            "n_parents_indexed": len(parents_by_child),
            "n_children_indexed": len(children_by_parent),
        }
    )


def _process_node_for_mapping(
    *,
    config: KnowledgeGraphConfig,
    defaults: DefaultsResolver,
    ids: DeterministicIdRegistry,
    node: CanonicalNode,
    root_node_id: str,
    subject_resolver: SubjectResolver,
) -> tuple[
    Optional[StandardsFrameworkItem],
    Optional[UUID],
    Optional[UUID],
    Optional[str],
    Optional[dict[str, Any]],
]:
    """Process a single node for mapping to SFI, handling validation and overrides.

    Parameters
    ----------
    config
        The KnowledgeGraphConfig to use for filtering rules.
    defaults
        DefaultsResolver instance.
    ids
        DeterministicIdRegistry instance.
    node
        The CanonicalNode to process.
    root_node_id
        The root node_id of the framework.
    subject_resolver
        SubjectResolver instance.

    Returns
    -------
    tuple
        (SFI, SFI identifier, SFI CASE UUID, drop_reason, override_data)
    """

    if node.node_id == root_node_id:
        return None, None, None, None, None

    resolved = _resolve_text(config=config, node=node)

    if resolved is None:
        return None, None, None, "drop_empty_text", None

    # Normative safety overrides (only for EXPECTATION).
    override_role, override_reason = _normative_safety_override(
        node=node, resolved=resolved
    )

    effective_role = override_role if override_role is not None else node.role

    # Validate node for inclusion.
    drop_reason = _validate_sfi_node(
        config=config, effective_role=effective_role, resolved=resolved
    )

    if drop_reason:
        return None, None, None, drop_reason, None

    # Determine subject context.
    subject_text = (
        resolved.text
        if effective_role == StatementRole.SUBJECT
        else subject_resolver.get_subject(start_node_id=node.node_id)
    )

    # Create SFI.
    sfi, sfi_identifier, sfi_case_uuid = _create_sfi_entity(
        defaults=defaults,
        effective_role=effective_role,
        ids=ids,
        node=node,
        resolved=resolved,
        subject_text=subject_text,
    )

    override_data = None

    if override_role is not None:
        override_data = {
            "to_role": override_role,
            "reason": override_reason,
            "resolved": resolved,
        }

    return sfi, sfi_identifier, sfi_case_uuid, None, override_data


def _prune_dead_groupings(
    *,
    canonical: CanonicalIR,
    children_by_parent: dict[str, list[str]],
    kept_edges: list[CanonicalEdge],
    kept_node_ids: set[str],
    kept_nodes: list[CanonicalNode],
    report: GraphValidationReport,
) -> tuple[
    list[CanonicalNode],
    set[str],
    list[CanonicalEdge],
    list[dict[str, Any]],
    set[str],
    dict[str, str],
]:
    """Prune "dead" grouping branches (group nodes with no EXPECTATION descendants).

    NB: This step is intentionally Step-5 only so that CanonicalIR remains a faithful
    stitch of extracted structure; export decides what to drop.

    The process is as follows:

    1. Identify Dead Groupings: Find all grouping nodes (e.g., SECTION, STRAND) that do
        not have any EXPECTATION descendants.
    2. Update Node Sets: Remove identified dead grouping nodes from the kept node sets.
    3. Filter Edges (Pass 1): Remove edges connected to the dropped dead grouping nodes.
    4. Check Reachability: Compute reachability from the root node to identify any
        nodes that have become unreachable due to step 3.
    5. Update Node Sets: Remove unreachable nodes from the kept node sets.
    6. Filter Edges (Pass 2): Remove edges connected to the newly unreachable nodes.
    7. Reporting: Update the report with statistics about the pruning process.

    Parameters
    ----------
    canonical
        The CanonicalIR being processed.
    children_by_parent
        Adjacency list mapping from parent node_id to list of child node_ids.
    kept_edges
        The list of currently kept CanonicalEdges.
    kept_node_ids
        The set of currently kept node IDs.
    kept_nodes
        The list of currently kept CanonicalNodes.
    report
        The GraphValidationReport to update.

    Returns
    -------
    tuple
        (kept_nodes, kept_node_ids, kept_edges, dropped_edges_extra,
         dropped_node_ids_extra, dropped_node_reasons_extra)
    """

    # 1.
    dead_group_ids = _identify_dead_group_ids(
        children_by_parent=children_by_parent,
        kept_nodes=kept_nodes,
        root_id=canonical.root_id,
    )

    if not dead_group_ids:
        report.stats.setdefault("pruning", {})
        report.stats["pruning"]["dead_grouping_prune"] = {
            "enabled": True,
            "dead_group_nodes_pruned": 0,
            "unreachable_nodes_pruned": 0,
        }
        return kept_nodes, kept_node_ids, kept_edges, [], set(), {}

    # 2.
    dropped_node_ids_extra = set(dead_group_ids)
    dropped_node_reasons_extra = {
        nid: "drop_dead_group_no_expectation_descendant" for nid in dead_group_ids
    }

    current_kept_ids = kept_node_ids - dead_group_ids

    # 3.
    kept_edges_pass1, dropped_edges_pass1 = _filter_edges_by_kept_nodes(
        edges=kept_edges,
        kept_node_ids=current_kept_ids,
        drop_reason="drop_edge_after_dead_group_prune",
    )

    # 4.
    reachable_ids = _compute_reachability_from_edges(
        edges=kept_edges_pass1, root_id=canonical.root_id
    )

    unreachable_ids = current_kept_ids - reachable_ids

    # 5.
    if unreachable_ids:
        for nid in unreachable_ids:
            dropped_node_ids_extra.add(nid)
            dropped_node_reasons_extra[nid] = "drop_unreachable_after_dead_group_prune"
        current_kept_ids = current_kept_ids - unreachable_ids

    # 6. NB: We filter based on the result of Pass 1 to avoid double-processing.
    kept_edges_final, dropped_edges_pass2 = _filter_edges_by_kept_nodes(
        edges=kept_edges_pass1,
        kept_node_ids=current_kept_ids,
        drop_reason="drop_edge_after_reachability_prune",
    )

    all_dropped_edges = dropped_edges_pass1 + dropped_edges_pass2
    kept_nodes_final = [n for n in kept_nodes if n.node_id in current_kept_ids]

    # 7.
    report.stats.setdefault("pruning", {})
    report.stats["pruning"]["dead_grouping_prune"] = {
        "enabled": True,
        "dead_group_nodes_pruned": len(dead_group_ids),
        "unreachable_nodes_pruned": len(unreachable_ids),
        "examples": sorted(list(dead_group_ids))[:25],
    }
    report.warn(
        "pruned_dead_groupings",
        "Pruned grouping nodes with no EXPECTATION descendants (export-only).",
        {"n": len(dead_group_ids), "examples": sorted(list(dead_group_ids))[:25]},
    )

    return (
        kept_nodes_final,
        current_kept_ids,
        kept_edges_final,
        all_dropped_edges,
        dropped_node_ids_extra,
        dropped_node_reasons_extra,
    )


def _resolve_edge_endpoints(
    *,
    edge: CanonicalEdge,
    framework: StandardsFramework,
    root_id: str,
    sfi_case_uuid_by_canonical_id: Optional[dict[str, UUID]],
    sfi_uuid_by_canonical_id: dict[str, UUID],
) -> tuple[Optional[UUID], Optional[UUID], str, Optional[UUID], Optional[UUID]]:
    """Resolve identifiers for parent and child nodes.

    Parameters
    ----------
    edge
        The CanonicalEdge to resolve.
    framework
        The StandardsFramework entity.
    root_id
        The root node_id of the framework.
    sfi_case_uuid_by_canonical_id
        Mapping of canonical_id to SFI CASE UUID.
    sfi_uuid_by_canonical_id
        Mapping of canonical_id to SFI identifier UUID.

    Returns
    -------
    tuple[Optional[UUID], Optional[UUID], str, Optional[UUID], Optional[UUID]]
        The (from_identifier, from_case_uuid, source_entity, to_identifier,
    """

    if edge.parent_id == root_id:
        from_identifier = framework.identifier
        from_case_uuid = framework.case_identifier_uuid
        source_entity = "StandardsFramework"
    else:
        from_identifier = sfi_uuid_by_canonical_id.get(edge.parent_id)
        from_case_uuid = (
            sfi_case_uuid_by_canonical_id.get(edge.parent_id)
            if sfi_case_uuid_by_canonical_id is not None
            else None
        )
        source_entity = "StandardsFrameworkItem"

    to_identifier = sfi_uuid_by_canonical_id.get(edge.child_id)
    to_case_uuid = (
        sfi_case_uuid_by_canonical_id.get(edge.child_id)
        if sfi_case_uuid_by_canonical_id is not None
        else None
    )

    return from_identifier, from_case_uuid, source_entity, to_identifier, to_case_uuid


def _resolve_text(
    *, config: KnowledgeGraphConfig, node: CanonicalNode
) -> Optional[ResolvedText]:
    """Resolve text and language for a node.

    Parameters
    ----------
    config
        The KnowledgeGraphConfig.
    node
        The CanonicalNode to resolve.

    Returns
    -------
    Optional[ResolvedText]
        The resolved text, or None if no text found.
    """

    unit = node.title or node.body

    if not unit:
        return None

    source_lang = (getattr(unit, "language", None) or "und").strip() or "und"
    source_text = (getattr(unit, "text", None) or "").strip()

    # CanonicalIR TextUnit often has text_en; keep it optional + safe.
    text_en = (getattr(unit, "text_en", None) or "").strip()

    # If there's literally no usable text, drop the node deterministically.
    if not source_text and not text_en:
        return None

    # Choose description text deterministically.
    if config.description_text_policy == "prefer_text_en" and text_en:
        text = text_en
    else:
        # Prefer source text, but don't emit empty descriptions if only English exists.
        text = source_text or text_en

    if not text:
        return None

    # Choose inLanguage deterministically.
    if config.export_in_language_policy == "default":
        in_lang = (config.language_default or "und").strip() or "und"
    else:
        in_lang = source_lang

    return ResolvedText(
        english_text=text_en or None,
        english_language="en" if text_en else None,
        in_language=in_lang,
        source_language=source_lang,
        source_text=source_text,
        text=text,
    )


def _role_str(role: StatementRole | str) -> str:
    """Return the string representation of a StatementRole.

    Parameters
    ----------
    role
        The StatementRole or string.

    Returns
    -------
    str
        The string representation of the role.
    """

    return getattr(role, "value", role)


def _seed(*parts: str) -> str:
    """Create a stable seed string from normalized parts.

    Parameters
    ----------
    parts
        The parts to join into a seed string.

    Returns
    -------
    str
        The normalized seed string.
    """

    return ":".join(p.strip().lower() for p in parts)


def _select_text_unit(*, node: CanonicalNode) -> Optional[TextUnit]:
    """Select a text unit for a node.

    Parameters
    ----------
    node
        The CanonicalNode to select text from.

    Returns
    -------
    Optional[TextUnit]
        The selected text unit, or None if neither has text.
    """

    if node.title is not None and (node.title.text or "").strip():
        return node.title

    if node.body is not None and (node.body.text or "").strip():
        return node.body

    return None


def _update_drop_counters(*, counters: dict[str, int], reason_code: str) -> None:
    """Update drop counters based on reason code.

    Parameters
    ----------
    counters
        The drop counters dictionary to update.
    reason_code
        The reason code for the drop.
    """

    if "child_is_framework_root" in reason_code:
        counters["child_is_root"] += 1
    elif "missing_endpoint" in reason_code or "child_not_exported" in reason_code:
        counters["missing_endpoint"] += 1
    elif "self_loop" in reason_code:
        counters["self_loop"] += 1


def _validate_node_doc_keys(
    *, canonical_ir: CanonicalIR, report: GraphValidationReport
) -> None:
    """Validate that all nodes match the CanonicalIR doc_key.

    Parameters
    ----------
    canonical_ir
        The CanonicalIR to validate.
    report
        The GraphValidationReport to log errors to.
    """

    doc_key = canonical_ir.doc_key
    mismatched_doc_keys: list[dict[str, Any]] = []

    for n in canonical_ir.nodes:
        if n.doc_key != doc_key:
            mismatched_doc_keys.append(
                {
                    "node_id": n.node_id,
                    "node_doc_key": n.doc_key,
                    "canonical_doc_key": doc_key,
                }
            )

    if mismatched_doc_keys:
        report.error(
            "doc_key_mismatch",
            "CanonicalIR.doc_key does not match one or more CanonicalNode.doc_key values.",
            {
                "mismatches": mismatched_doc_keys[:25],
                "n_mismatches": len(mismatched_doc_keys),
            },
        )


def _validate_relationship_endpoints(
    *,
    child_id: str,
    edge: CanonicalEdge,
    exported_sfi_identifiers: set[UUID],
    from_case_uuid: Optional[UUID],
    from_identifier: Optional[UUID],
    root_id: str,
    to_case_uuid: Optional[UUID],
    to_identifier: Optional[UUID],
) -> Optional[dict[str, Any]]:
    """Validate relationship endpoints and return a drop reason if invalid.

    Parameters
    ----------
    child_id
        The child node ID.
    edge
        The CanonicalEdge being processed.
    exported_sfi_identifiers
        Set of exported SFI identifiers.
    from_case_uuid
        The source CASE UUID.
    from_identifier
        The source entity identifier.
    root_id
        The root node ID.
    to_case_uuid
        The target CASE UUID.
    to_identifier
        The target entity identifier.

    Returns
    -------
    Optional[dict[str, Any]]
        A dictionary describing the drop reason if invalid, or None if valid.
    """

    if child_id == root_id:
        return {
            "parent_id": edge.parent_id,
            "child_id": edge.child_id,
            "reason": "drop_edge_child_is_framework_root",
        }

    # Missing endpoints after mapping-time drops.
    if (
        from_identifier is None
        or to_identifier is None
        or from_case_uuid is None
        or to_case_uuid is None
    ):
        return {
            "parent_id": edge.parent_id,
            "child_id": edge.child_id,
            "reason": "drop_edge_missing_endpoint_after_mapping",
            "missing_parent": (from_identifier is None) or (from_case_uuid is None),
            "missing_child": (to_identifier is None) or (to_case_uuid is None),
        }

    # Child must be an exported SFI.
    if to_identifier not in exported_sfi_identifiers:
        return {
            "parent_id": edge.parent_id,
            "child_id": edge.child_id,
            "reason": "drop_edge_child_not_exported_sfi",
        }

    # Avoid self loops (by entity identifiers).
    if from_identifier == to_identifier:
        return {
            "parent_id": edge.parent_id,
            "child_id": edge.child_id,
            "reason": "drop_self_loop",
        }

    return None


def _validate_root_node(
    *, node_by_id: dict[str, CanonicalNode], report: GraphValidationReport, root_id: str
) -> None:
    """Validate existence and role of the root node.

    Parameters
    ----------
    node_by_id
        Mapping of node_id to CanonicalNode.
    report
        The GraphValidationReport to log warnings/errors to.
    root_id
        The root node ID.
    """

    if root_id not in node_by_id:
        report.error(
            "missing_root_id",
            "CanonicalIR.root_id does not exist in nodes[].",
            {"root_id": root_id},
        )
    else:
        # Check that root role should usually be framework.
        root_role = getattr(node_by_id[root_id], "role", None)
        root_role_str = getattr(root_role, "value", root_role)
        if root_role_str != StatementRole.FRAMEWORK.value:
            report.warn(
                "root_role_not_framework",
                "CanonicalIR.root_id exists but root node role is not 'framework'.",
                {"root_id": root_id, "root_role": root_role_str},
            )


def _validate_sfi_node(
    *,
    config: KnowledgeGraphConfig,
    effective_role: StatementRole,
    resolved: ResolvedText,
) -> Optional[str]:
    """Validate if a node should be exported as an SFI.

    Parameters
    ----------
    config
        The KnowledgeGraphConfig to use for filtering rules.
    effective_role
        The effective StatementRole for the node.
    resolved
        The ResolvedText for the node.

    Returns
    -------
    Optional[str]
        Drop reason if invalid, None if valid.
    """

    # Junk filter for EXPECTATION.
    if effective_role == StatementRole.EXPECTATION:
        txt = (resolved.source_text or "").strip()
        if len(txt) < 3 or not _has_alpha(txt):
            return "drop_expectation_non_linguistic_or_too_short"

    if effective_role not in _ROLE_TO_NORMALIZED:
        return f"drop_unmapped_role:{_role_str(effective_role)}"

    if effective_role == StatementRole.GUIDANCE and not config.include_guidance:
        return "drop_guidance_disabled_after_reclassify"

    if effective_role == StatementRole.DESCRIPTOR and not config.include_descriptors:
        return "drop_descriptors_disabled_after_reclassify"

    return None


def _validate_tree_topology(
    *, parents_by_child: dict[str, list[str]], report: GraphValidationReport
) -> None:
    """Check for multiple parents (tree constraint violations).

    Parameters
    ----------
    parents_by_child
        Mapping of child_id to list of parent_ids.
    report
        The GraphValidationReport to log warnings to.
    """

    multi_parent_nodes: list[dict[str, Any]] = []

    for child_id, parents in parents_by_child.items():
        if len(parents) > 1:
            multi_parent_nodes.append({"child_id": child_id, "parents": parents})

    if multi_parent_nodes:
        report.warn(
            "multiple_parents_detected",
            "One or more nodes have multiple parents (tree constraint violation).",
            {"examples": multi_parent_nodes[:25], "n": len(multi_parent_nodes)},
        )


def build_has_child_relationships(
    *,
    defaults: DefaultsResolver,
    filtered: FilteredCanonicalIRIndex,
    framework: StandardsFramework,
    ids: DeterministicIdRegistry,
    sfi_case_uuid_by_canonical_id: Optional[dict[str, UUID]] = None,
    sfi_uuid_by_canonical_id: dict[str, UUID],
    sfis: list[StandardsFrameworkItem],
) -> HasChildBuildResult:
    """Build hasChild relationships from CanonicalIR edges (post-filter).

    Deterministic policies:

    1. If an edge endpoint is missing (dropped at mapping), drop the edge and log it.
    2. Tree constraint: each child can have only one parent. If multiple parents exist,
        keep the FIRST one encountered in filtered.kept_edges order (which is
        deterministic), drop the rest and log them.
    3. Deduplicate identical relationships deterministically (keep first by edge order).

    Root connectivity:

    1. BFS from framework.identifier over hasChild edges (entity identifiers).
    2. Warn if any exported SFI is unreachable.

    Parameters
    ----------
    defaults
        The DefaultsResolver to use for metadata defaults.
    filtered
        The FilteredCanonicalIRIndex to build from.
    framework
        The StandardsFramework entity.
    ids
        The DeterministicIdRegistry to use for UUID generation.
    sfi_case_uuid_by_canonical_id
        Optional mapping of CanonicalIR node_id -> exported SFI CASE UUID.
        If not provided, it will be derived from the sfis list.
    sfi_uuid_by_canonical_id
        Mapping of CanonicalIR node_id -> exported SFI identifier (entity UUID).
    sfis
        The list of exported StandardsFrameworkItems.

    Returns
    -------
    HasChildBuildResult
        The result of building hasChild relationships.
    """

    report = filtered.report.model_copy(deep=True)
    canonical = filtered.canonical

    # If caller didn't pass CASE UUID mapping, derive it from SFI metadata.
    if sfi_case_uuid_by_canonical_id is None:
        sfi_case_uuid_by_canonical_id = {}
        for sfi in sfis:
            md = sfi.metadata or {}
            canonical_id = md.get("canonicalNodeId") or md.get("canonical_node_id")
            if canonical_id:
                sfi_case_uuid_by_canonical_id[str(canonical_id)] = (
                    sfi.case_identifier_uuid
                )

    exported_sfi_identifiers = {s.identifier for s in sfis}

    relationships: list[Relationship] = []
    dropped_edges: list[dict[str, Any]] = []

    # child_identifier -> kept parent_identifier (tree constraint).
    parent_of_child: dict[UUID, UUID] = {}

    # Dedupe by relationship identifier.
    seen_rel_ids: set[str] = set()

    # For BFS connectivity check.
    adjacency: dict[UUID, list[UUID]] = {}

    # Counters for validation report.
    drop_counters = {
        "missing_endpoint": 0,
        "child_is_root": 0,
        "self_loop": 0,
        "multi_parent": 0,
        "duplicates": 0,
    }

    common = defaults.common()

    for e in filtered.kept_edges:
        (
            from_identifier,
            from_case_uuid,
            source_entity,
            to_identifier,
            to_case_uuid,
        ) = _resolve_edge_endpoints(
            edge=e,
            framework=framework,
            root_id=canonical.root_id,
            sfi_case_uuid_by_canonical_id=sfi_case_uuid_by_canonical_id,
            sfi_uuid_by_canonical_id=sfi_uuid_by_canonical_id,
        )

        drop_reason = _validate_relationship_endpoints(
            child_id=e.child_id,
            edge=e,
            exported_sfi_identifiers=exported_sfi_identifiers,
            from_case_uuid=from_case_uuid,
            from_identifier=from_identifier,
            root_id=canonical.root_id,
            to_case_uuid=to_case_uuid,
            to_identifier=to_identifier,
        )

        if drop_reason:
            reason_code = drop_reason.get("reason", "")
            _update_drop_counters(counters=drop_counters, reason_code=reason_code)
            dropped_edges.append(drop_reason)
            continue

        assert from_identifier is not None
        assert to_identifier is not None
        assert from_case_uuid is not None
        assert to_case_uuid is not None

        if to_identifier in parent_of_child:
            drop_counters["multi_parent"] += 1
            dropped_edges.append(
                {
                    "parent_id": e.parent_id,
                    "child_id": e.child_id,
                    "reason": "drop_multi_parent_keep_first",
                    "kept_parent_identifier": str(parent_of_child[to_identifier]),
                    "dropped_parent_identifier": str(from_identifier),
                }
            )
            continue

        rel = _create_relationship(
            canonical_doc_key=canonical.doc_key,
            common_metadata=common,
            defaults=defaults,
            edge=e,
            from_case_uuid=from_case_uuid,
            from_identifier=from_identifier,
            ids=ids,
            source_entity=source_entity,
            to_case_uuid=to_case_uuid,
            to_identifier=to_identifier,
        )
        rel_identifier_str = str(rel.identifier)

        if rel_identifier_str in seen_rel_ids:
            drop_counters["duplicates"] += 1
            dropped_edges.append(
                {
                    "parent_id": e.parent_id,
                    "child_id": e.child_id,
                    "reason": "drop_duplicate_relationship",
                    "relationship_identifier": rel_identifier_str,
                }
            )
            continue

        seen_rel_ids.add(rel_identifier_str)
        parent_of_child[to_identifier] = from_identifier
        adjacency.setdefault(from_identifier, []).append(to_identifier)
        relationships.append(rel)

    # Deterministic ordering: by relationship identifier string.
    relationships.sort(key=lambda r: str(r.identifier))

    # Connectivity check: BFS from framework.identifier over hasChild edges.
    unreachable_sfis = _check_connectivity(
        adjacency=adjacency,
        exported_sfi_identifiers=exported_sfi_identifiers,
        root_identifier=framework.identifier,
    )

    _finalize_has_child_report(
        drop_counters=drop_counters,
        dropped_edges=dropped_edges,
        exported_sfi_identifiers=exported_sfi_identifiers,
        relationships=relationships,
        report=report,
        sfis=sfis,
        unreachable_sfis=unreachable_sfis,
    )

    return HasChildBuildResult(
        dropped_edges=dropped_edges, relationships=relationships, report=report
    )


def build_index(*, canonical_ir: CanonicalIR) -> CanonicalIRIndex:
    """Build lookup maps and adjacency, and run basic invariant checks.

    Parameters
    ----------
    canonical_ir
        The CanonicalIR to index.

    Returns
    -------
    CanonicalIRIndex
        The indexed CanonicalIR.
    """

    report = GraphValidationReport(
        doc_key=canonical_ir.doc_key, pdf_name=canonical_ir.pdf_name
    )

    node_by_id = _build_node_map(canonical_ir=canonical_ir, report=report)

    _validate_root_node(
        node_by_id=node_by_id, report=report, root_id=canonical_ir.root_id
    )
    _validate_node_doc_keys(canonical_ir=canonical_ir, report=report)

    children_by_parent, parents_by_child = _build_adjacency_maps(
        canonical_ir=canonical_ir, node_by_id=node_by_id, report=report
    )

    _validate_tree_topology(parents_by_child=parents_by_child, report=report)

    _populate_index_stats(
        canonical_ir=canonical_ir,
        children_by_parent=children_by_parent,
        parents_by_child=parents_by_child,
        report=report,
    )

    report.raise_if_errors()

    return CanonicalIRIndex(
        canonical_ir=canonical_ir,
        children_by_parent=children_by_parent,
        node_by_id=node_by_id,
        parents_by_child=parents_by_child,
        report=report,
    )


def build_learning_components(
    *,
    config: KnowledgeGraphConfig,
    defaults: DefaultsResolver,
    ids: DeterministicIdRegistry,
    report: GraphValidationReport,
    sfis: list[StandardsFrameworkItem],
) -> LearningComponentBuildResult:
    """LearningComponent factory.

    Policy:

    1. If generate_learning_components=False: produce none.
    2. Else: create 1 LearningComponent per SFI where
        normalized_statement_type == "Standard".
    3. Emit supports relationships LC -> SFI.
    4. All entity + relationship identifiers are deterministic. supports targets SFI
        CASE UUIDs.
    5. Deterministic ordering: sort both lists by UUID string.

    Parameters
    ----------
    config
        The KnowledgeGraphConfig to use for generation policies.
    defaults
        The DefaultsResolver to use for default metadata values.
    ids
        The DeterministicIdRegistry to use for UUID generation.
    report
        The GraphValidationReport to log generation stats/warnings.
    sfis
        The list of StandardsFrameworkItems to generate LearningComponents from.

    Returns
    -------
    LearningComponentBuildResult
        The generated LearningComponents, supports relationships, and updated report.
    """

    report = report.model_copy(deep=True)

    if not config.generate_learning_components:
        report.stats.setdefault("learning_components", {})
        report.stats["learning_components"].update(
            {
                "enabled": False,
                "n_learning_components": 0,
                "n_supports_relationships": 0,
            }
        )
        return LearningComponentBuildResult(
            learning_components=[], supports_relationships=[], report=report
        )

    # Only standards (not groupings/other).
    standard_sfis = [s for s in sfis if s.normalized_statement_type == "Standard"]

    lcs: list[LearningComponent] = []
    supports_rels: list[Relationship] = []

    duplicates = 0
    seen_lc_ids: set[str] = set()
    seen_rel_ids: set[str] = set()

    common = defaults.common()

    for sfi in standard_sfis:
        lc_identifier = ids.learning_component_id(
            standard_sfi_id=sfi.identifier, split_key="0"
        )
        lc_identifier_str = str(lc_identifier)

        if lc_identifier_str in seen_lc_ids:
            duplicates += 1
            continue

        # supports relationship ID is deterministic --> check it BEFORE creating/adding
        # the LC so we never end up with an LC that has no supports edge.
        rel_identifier = ids.relationship_id(
            from_id=lc_identifier, rel_type="supports", to_id=sfi.case_identifier_uuid
        )
        rel_identifier_str = str(rel_identifier)

        if rel_identifier_str in seen_rel_ids:
            duplicates += 1
            continue

        # Commit both ids only after both uniqueness checks pass.
        seen_lc_ids.add(lc_identifier_str)
        seen_rel_ids.add(rel_identifier_str)

        academic_subject = defaults.academic_subject(override=sfi.academic_subject)

        sfi_meta = sfi.metadata or {}

        # Build LC metadata by inheriting the same provenance/text fields from the SFI.
        lc_meta: dict[str, Any] = {
            "policy": "lc_1_to_1",
            "supportsStandardIdentifier": str(sfi.identifier),
            "supportsStandardCaseIdentifierUUID": str(sfi.case_identifier_uuid),
            "docKey": sfi_meta.get("docKey"),
            "canonicalNodeId": sfi_meta.get("canonicalNodeId"),
            "pageIndices": sfi_meta.get("pageIndices"),
            "sourceText": sfi_meta.get("sourceText"),
            "sourceLanguage": sfi_meta.get("sourceLanguage"),
        }

        # Only add English fields if present (keeps metadata clean + deterministic)
        if sfi_meta.get("englishText"):
            lc_meta["englishText"] = sfi_meta.get("englishText")
            lc_meta["englishLanguage"] = sfi_meta.get("englishLanguage") or "en"

        # Keep your existing "sourceStandard" subobject, but enrich it with text/lang too.
        source_standard: dict[str, Any] = {
            "statementCode": sfi.statement_code,
            "statementType": sfi.statement_type,
            "canonicalNodeId": sfi_meta.get("canonicalNodeId"),
            "pageIndices": sfi_meta.get("pageIndices"),
            "sourceText": sfi_meta.get("sourceText"),
            "sourceLanguage": sfi_meta.get("sourceLanguage"),
        }
        if sfi_meta.get("englishText"):
            source_standard["englishText"] = sfi_meta.get("englishText")
            source_standard["englishLanguage"] = sfi_meta.get("englishLanguage") or "en"

        lc_meta["sourceStandard"] = source_standard

        lc = LearningComponent(
            academic_subject=academic_subject,
            attribution_statement=common["attribution_statement"],
            author=common["author"],
            description=sfi.description,
            identifier=lc_identifier,
            in_language=sfi.in_language,
            license=common["license"],
            metadata=lc_meta,
            provider=common["provider"],
        )
        lcs.append(lc)

        rel_meta: dict[str, Any] = {
            "policy": "lc_1_to_1",
            "supportsStandardIdentifier": str(sfi.identifier),
            "supportsStandardCaseIdentifierUUID": str(sfi.case_identifier_uuid),
            "docKey": sfi_meta.get("docKey"),
            "canonicalNodeId": sfi_meta.get("canonicalNodeId"),
            "pageIndices": sfi_meta.get("pageIndices"),
            "sourceText": sfi_meta.get("sourceText"),
            "sourceLanguage": sfi_meta.get("sourceLanguage"),
        }
        if sfi_meta.get("englishText"):
            rel_meta["englishText"] = sfi_meta.get("englishText")
            rel_meta["englishLanguage"] = sfi_meta.get("englishLanguage") or "en"

        supports_rels.append(
            Relationship(
                attribution_statement=common["attribution_statement"],
                author=common["author"],
                description=defaults.relationship_description(rel_type="supports"),
                identifier=rel_identifier,
                license=common["license"],
                metadata=rel_meta,
                provider=common["provider"],
                relationship_type="supports",
                source_entity="LearningComponent",
                source_entity_key="identifier",
                source_entity_value=str(lc.identifier),
                target_entity="StandardsFrameworkItem",
                target_entity_key="caseIdentifierUUID",
                target_entity_value=str(sfi.case_identifier_uuid),
            )
        )

    lcs.sort(key=lambda x: str(x.identifier))
    supports_rels.sort(key=lambda x: str(x.identifier))

    report.stats.setdefault("learning_components", {})
    report.stats["learning_components"].update(
        {
            "enabled": True,
            "n_sfis_total": len(sfis),
            "n_standard_sfis": len(standard_sfis),
            "n_learning_components": len(lcs),
            "n_supports_relationships": len(supports_rels),
            "duplicates_skipped": duplicates,
        }
    )

    if duplicates:
        report.warn(
            "lc_duplicates_skipped",
            "Some LearningComponents/supports relationships were skipped due to duplicate deterministic identifiers.",
            {"duplicates_skipped": duplicates},
        )

    return LearningComponentBuildResult(
        learning_components=lcs, report=report, supports_relationships=supports_rels
    )


def build_graph_stats(
    *,
    canonical_doc_key: str,
    export: KnowledgeGraphExport,
    report: GraphValidationReport,
) -> dict[str, Any]:
    """Build export statistics summary.

    Parameters
    ----------
    canonical_doc_key
        The canonical document key.
    export
        The KnowledgeGraphExport to summarize.
    report
        The GraphValidationReport with validation stats.

    Returns
    -------
    dict[str, Any]
        The export statistics summary.
    """

    rel_type_counts: dict[str, int] = {}
    for r in export.relationships:
        rel_type_counts[r.relationship_type] = (
            rel_type_counts.get(r.relationship_type, 0) + 1
        )

    exported_role_counts: dict[str, int] = {}
    for sfi in export.standards_framework_items:
        role = None
        if sfi.metadata:
            role = sfi.metadata.get("effectiveRole") or sfi.metadata.get(
                "canonicalRole"
            )
        if role:
            exported_role_counts[str(role)] = exported_role_counts.get(str(role), 0) + 1

    filtering_stats = (report.stats or {}).get("filtering", {})
    mapping_stats = (report.stats or {}).get("mapping", {})

    stats: dict[str, Any] = {
        "doc_key": canonical_doc_key,
        "entities": {
            "frameworks": len(export.frameworks),
            "standardsFrameworkItems": len(export.standards_framework_items),
            "learningComponents": len(export.learning_components),
        },
        "relationships": {
            "total": len(export.relationships),
            "by_type": rel_type_counts,
        },
        "canonical_roles": {
            "exported": exported_role_counts,
            "kept_by_filter": filtering_stats.get("kept_role_counts", {}),
            "dropped_by_filter": filtering_stats.get("dropped_role_counts", {}),
        },
        "mapping": {
            "framework_identifier": mapping_stats.get("framework_identifier"),
            "sfi_count": mapping_stats.get("sfi_count"),
            "dropped_nodes_at_mapping": mapping_stats.get("dropped_nodes_at_mapping"),
            "role_overrides": mapping_stats.get("role_overrides"),
        },
        "validation": {
            "n_issues": len(report.issues),
            "n_errors": len([i for i in report.issues if i.level == "error"]),
            "n_warnings": len([i for i in report.issues if i.level == "warning"]),
        },
    }

    return stats


def create_kg_dirs(*, output_dir: Path) -> KGDirs:
    """Create KG directories for a given KG run.

    Parameters
    ----------
    output_dir
        The output directory root.

    Returns
    -------
    KGDirs
        The created KG directories.
    """

    root = output_dir
    cache = root / "cache"

    for p in [root, cache]:
        make_dir(p)

    return KGDirs(root=root, cache=cache)


def filter_canonical_ir(
    *, config: KnowledgeGraphConfig, index: CanonicalIRIndex
) -> FilteredCanonicalIRIndex:
    """Deterministic filtering and role policy.

    Rules implemented:

    1. Drop role=UNRESOLVED always.
    2. Drop DESCRIPTOR unless include_descriptors=True.
    3. Drop GUIDANCE unless include_guidance=True.
    4. CanonicalIR.unresolved[] is never exported.
    5. Drop any edge whose parent/child is not kept (do NOT reattach).

    Parameters
    ----------
    config
        The KnowledgeGraphConfig to use for filtering rules.
    index
        The CanonicalIRIndex to filter.

    Returns
    -------
    FilteredCanonicalIRIndex
        The filtered CanonicalIRIndex.
    """

    canonical = index.canonical_ir

    # Copy report so filtering can be used independently without mutating.
    report = index.report.model_copy(deep=True)

    # Node filtering.
    kept_nodes, kept_node_ids, dropped_node_ids, dropped_node_reasons = _filter_nodes(
        canonical=canonical, config=config, index=index, report=report
    )

    # Edge filtering.
    kept_edges, dropped_edges = _filter_edges(
        canonical_edges=canonical.edges,
        dropped_node_reasons=dropped_node_reasons,
        kept_node_ids=kept_node_ids,
        report=report,
    )

    # Build adjacency for kept graph.
    children_by_parent, parents_by_child = _build_filtered_adjacency_step(
        kept_edges=kept_edges, report=report
    )

    # Optional pruning to remove "dead" grouping branches at export-time only.
    if config.prune_dead_groupings:
        (
            kept_nodes,
            kept_node_ids,
            kept_edges,
            dropped_edges_extra,
            dropped_node_ids_extra,
            dropped_node_reasons_extra,
        ) = _prune_dead_groupings(
            canonical=canonical,
            children_by_parent=children_by_parent,
            kept_edges=kept_edges,
            kept_nodes=kept_nodes,
            kept_node_ids=kept_node_ids,
            report=report,
        )

        dropped_edges.extend(dropped_edges_extra)
        dropped_node_ids |= dropped_node_ids_extra
        dropped_node_reasons.update(dropped_node_reasons_extra)

        # Rebuild adjacency after pruning.
        children_by_parent, parents_by_child = _build_filtered_adjacency_step(
            kept_edges=kept_edges, report=report
        )

    return FilteredCanonicalIRIndex(
        canonical=canonical,
        children_by_parent=children_by_parent,
        dropped_edges=dropped_edges,
        dropped_node_ids=dropped_node_ids,
        dropped_node_reasons=dropped_node_reasons,
        kept_edges=kept_edges,
        kept_node_ids=kept_node_ids,
        kept_nodes=kept_nodes,
        parents_by_child=parents_by_child,
        report=report,
    )


def map_canonical_to_entities(
    *,
    config: KnowledgeGraphConfig,
    defaults: DefaultsResolver,
    filtered: FilteredCanonicalIRIndex,
    ids: DeterministicIdRegistry,
) -> EntityMappingResult:
    """CanonicalNode to LC entity mapping (shape-only).

    Parameters
    ----------
    config
        KnowledgeGraphConfig with export settings.
    defaults
        DefaultsResolver for default values.
    filtered
        The FilteredCanonicalIRIndex after filtering.
    ids
        DeterministicIdRegistry for ID generation.

    Returns
    -------
    EntityMappingResult
        The mapped StandardsFramework and StandardsFrameworkItems.
    """

    canonical = filtered.canonical
    report = filtered.report.model_copy(deep=True)

    node_by_id = {n.node_id: n for n in canonical.nodes}
    root_node = node_by_id.get(canonical.root_id)

    if root_node is None:
        report.error(
            "root_missing_at_mapping",
            "CanonicalIR.root_id is missing during mapping; cannot build framework.",
            {"root_id": canonical.root_id},
        )
        report.raise_if_errors()

    assert root_node is not None

    # Build framework (exactly one).
    framework, warning_reason = _create_framework_entity(
        canonical_doc_key=canonical.doc_key,
        canonical_pdf_name=canonical.pdf_name,
        config=config,
        defaults=defaults,
        ids=ids,
        root_node=root_node,
    )

    if warning_reason:
        report.warn(
            warning_reason,
            "Framework node had no title/body; used fallback title.",
            {"fallback_title": framework.name, **_node_text_debug(root_node)},
        )

    # Subject propagation (deterministic, general).
    subject_resolver = SubjectResolver(
        config=config,
        node_by_id=node_by_id,
        parents_by_child=filtered.parents_by_child,
        root_id=canonical.root_id,
    )

    # Validate english text policy.
    _check_english_text_policy(
        canonical_doc_key=canonical.doc_key,
        canonical_pdf_name=canonical.pdf_name,
        canonical_root_id=canonical.root_id,
        config=config,
        kept_nodes=filtered.kept_nodes,
        report=report,
    )

    # Build SFIs.
    sfi_case_uuid_by_canonical_id: dict[str, UUID] = {}
    sfi_uuid_by_canonical_id: dict[str, UUID] = {}
    sfis: list[StandardsFrameworkItem] = []

    dropped_node_ids: set[str] = set()
    dropped_node_reasons: dict[str, str] = {}
    role_overrides: dict[str, dict[str, str]] = {}

    for node in filtered.kept_nodes:
        (
            sfi,
            sfi_identifier,
            sfi_case_uuid,
            drop_reason,
            override_data,
        ) = _process_node_for_mapping(
            config=config,
            defaults=defaults,
            ids=ids,
            node=node,
            root_node_id=root_node.node_id,
            subject_resolver=subject_resolver,
        )

        if drop_reason:
            dropped_node_ids.add(node.node_id)
            dropped_node_reasons[node.node_id] = drop_reason
            continue

        if sfi is None or sfi_identifier is None or sfi_case_uuid is None:
            continue

        sfis.append(sfi)
        sfi_uuid_by_canonical_id[node.node_id] = sfi_identifier
        sfi_case_uuid_by_canonical_id[node.node_id] = sfi_case_uuid

        if override_data:
            to_role = override_data["to_role"]
            reason = override_data["reason"]
            resolved = override_data["resolved"]

            role_overrides[node.node_id] = {
                "from_role": _role_str(node.role),
                "to_role": _role_str(to_role),
                "reason": reason or "reclassified",
            }
            report.warn(
                "normative_safety_reclassification",
                "An EXPECTATION node was reclassified by the deterministic normative safety filter.",
                {
                    "node_id": node.node_id,
                    "from_role": _role_str(node.role),
                    "to_role": _role_str(to_role),
                    "reason": reason,
                    "text_preview": resolved.source_text[:200],
                    "provenance": _node_text_debug(node),
                },
            )

    _log_dropped_nodes(
        dropped_node_ids=dropped_node_ids,
        dropped_node_reasons=dropped_node_reasons,
        node_by_id=node_by_id,
        report=report,
    )

    report.stats.setdefault("mapping", {})
    report.stats["mapping"].update(
        {
            "framework_identifier": str(framework.identifier),
            "sfi_count": len(sfis),
            "dropped_nodes_at_mapping": len(dropped_node_ids),
            "role_overrides": len(role_overrides),
        }
    )

    return EntityMappingResult(
        dropped_node_ids=dropped_node_ids,
        dropped_node_reasons=dropped_node_reasons,
        framework=framework,
        report=report,
        role_overrides=role_overrides,
        sfi_case_uuid_by_canonical_id=sfi_case_uuid_by_canonical_id,
        sfi_uuid_by_canonical_id=sfi_uuid_by_canonical_id,
        standards_framework_items=sorted(sfis, key=lambda x: str(x.identifier)),
    )


def merge_validation_reports(
    *, base: GraphValidationReport, other: GraphValidationReport
) -> GraphValidationReport:
    """Combine issues and stats from two reports deterministically.

    NB:

    1. Issues are appended in order (base then other).
    2. Stats are deep-merged (other wins on conflicts).

    Parameters
    ----------
    base
        The base GraphValidationReport.
    other
        The other GraphValidationReport to merge into base.

    Returns
    -------
    GraphValidationReport
        The merged GraphValidationReport.
    """

    merged = base.model_copy(deep=True)

    # Append issues (preserve order).
    merged.issues.extend(other.issues)

    # Merge stats.
    merged.stats = _deep_merge_dict(dst=merged.stats or {}, src=other.stats or {})

    # Prefer non-null doc_key/pdf_name.
    if not merged.doc_key and other.doc_key:
        merged.doc_key = other.doc_key
    if not merged.pdf_name and other.pdf_name:
        merged.pdf_name = other.pdf_name

    return merged


def persist_kg_run(*, output_dir: Path, **kwargs: Any) -> tuple[KGDirs, RunCtx]:
    """Persist KG run metadata.

    Parameters
    ----------
    output_dir
        The output directory for the KG run results.
    kwargs
        Additional KG run configuration parameters.

    Returns
    -------
    tuple[KGDirs, RunCtx]
        The created KG directories and persisted KG run metadata.
    """

    extra = kwargs.get("extra", {})
    extra.pop("status", None)
    kg_dirs = create_kg_dirs(output_dir=output_dir)
    kg_run = RunCtx(
        extra=extra, run_id=str(uuid.uuid4()), started_at=datetime.now(timezone.utc)
    )
    write_to_json(fp=output_dir / "kg_run.json", json_info=kg_run)
    logger.info(f"KG directory: {output_dir}")

    return kg_dirs, kg_run


def sort_export_lists_in_place(export_obj: KnowledgeGraphExport) -> None:
    """Sort KnowledgeGraphExport lists in-place for deterministic JSON output.

    Parameters
    ----------
    export_obj
        The KnowledgeGraphExport object to sort.
    """

    export_obj.frameworks.sort(key=lambda x: str(x.identifier))
    export_obj.learning_components.sort(key=lambda x: str(x.identifier))
    export_obj.relationships.sort(key=lambda x: str(x.identifier))
    export_obj.standards_framework_items.sort(key=lambda x: str(x.identifier))
