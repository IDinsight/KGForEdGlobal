"""This module contains functionalities related to exporting the Academic Standards
knowledge graph. It exports a shape-preserving Learning Commons Academic Standards
knowledge graph from the CanonicalIR using ExportContext indexes and CreateKGConfig
policies.

Outputs:

1. StandardsFramework
2. StandardsFrameworkItem[]
3. Relationship[] (hasChild edges)
4. HierarchyOrderExport (parent -> ordered children)

Notes:

1. Deterministic IDs: UUIDv5 using config.namespace_uuid.
2. Export-time transformations only (aux parenting and pruning do NOT mutate
    CanonicalIR).
"""

# Future Library
from __future__ import annotations

# Standard Library
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, DefaultDict, Optional
from uuid import UUID, uuid5

# Package Library
from skg.kgs.schemas import (
    HierarchyOrderExport,
    Relationship,
    StandardsFramework,
    StandardsFrameworkItem,
)
from skg.kgs.utils import ExportContext, KGDirs
from skg.schemas import CreateKGConfig
from skg.utils.constants import NodeRole, StatementRole
from skg.utils.general import write_to_json

AUX_ROLES: set[str] = {StatementRole.DESCRIPTOR.value, StatementRole.GUIDANCE.value}


@dataclass
class AcademicStandardsExport:
    """The output of exporting Academic Standards KG artifacts."""

    framework: StandardsFramework
    items: list[StandardsFrameworkItem]
    order: HierarchyOrderExport
    relationships: list[Relationship]


def _build_relationships_and_order(
    *,
    config: CreateKGConfig,
    ctx: ExportContext,
    export_children: dict[str, list[str]],
    framework_uuid: UUID,
    sfi_by_node: dict[str, StandardsFrameworkItem],
) -> tuple[list[Relationship], dict[str, list[str]]]:
    """Build hasChild relationships and hierarchy order map.

    Parameters
    ----------
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.
    export_children
        The parent-to-children mapping.
    framework_uuid
        The UUID of the framework entity.
    sfi_by_node
        Mapping of canonical node ID to emitted SFI.

    Returns
    -------
    tuple
        (relationships, order_map).
    """

    def _edge_metadata(parent_id: str, child_id: str) -> dict[str, Any]:
        """Retrieve edge metadata for a given parent-child pair.

        Parameters
        ----------
        parent_id
            The canonical node ID of the parent.
        child_id
            The canonical node ID of the child.

        Returns
        -------
        dict[str, Any]
            The edge metadata dictionary.
        """

        edge_md = ctx.edge_metadata_by_pair.get((parent_id, child_id), {})

        return {
            "canonical_parent_id": parent_id,
            "canonical_child_id": child_id,
            "canonical_order_index": edge_md.get(
                "order_index", ctx.edge_order_index.get((parent_id, child_id))
            ),
            "canonical_edge_source_decision_ids": edge_md.get(
                "source_decision_ids", []
            ),
            "canonical_edge_source_segment_ids": edge_md.get("source_segment_ids", []),
        }

    relationships: list[Relationship] = []
    order_map: dict[str, list[str]] = {}

    # Root -> first-level children.
    root_children = [
        cid for cid in export_children.get(ctx.root_id, []) if cid in sfi_by_node
    ]
    order_map[str(framework_uuid)] = [
        str(sfi_by_node[cid].case_identifier_uuid) for cid in root_children
    ]

    for cid in root_children:
        rel = _emit_has_child(
            child_uuid=sfi_by_node[cid].case_identifier_uuid,
            config=config,
            doc_key=ctx.doc_key,
            parent_uuid=framework_uuid,
            relationship_metadata=_edge_metadata(ctx.root_id, cid),
        )
        rel.source_entity = "StandardsFramework"
        rel.target_entity = "StandardsFrameworkItem"
        relationships.append(rel)

    # SFI -> SFI edges.
    for pid, kids in export_children.items():
        if pid == ctx.root_id or pid not in sfi_by_node:
            continue

        emitted_kids = [cid for cid in kids if cid in sfi_by_node]

        if not emitted_kids:
            continue

        p_uuid = sfi_by_node[pid].case_identifier_uuid
        order_map[str(p_uuid)] = [
            str(sfi_by_node[cid].case_identifier_uuid) for cid in emitted_kids
        ]

        for cid in emitted_kids:
            relationships.append(
                _emit_has_child(
                    child_uuid=sfi_by_node[cid].case_identifier_uuid,
                    config=config,
                    doc_key=ctx.doc_key,
                    parent_uuid=p_uuid,
                    relationship_metadata=_edge_metadata(pid, cid),
                )
            )

    return relationships, order_map


def _collect_grade_levels(
    *, ctx: ExportContext, node_id: str, prefer_text_en: bool
) -> list[str]:
    """Collect grade level tags by walking ancestors and capturing nodes whose role ==
    grade_level.

    Parameters
    ----------
    ctx
        The ExportContext for the CanonicalIR, providing access to node properties and
        hierarchy.
    node_id
        The ID of the canonical node for which to collect grade levels.
    prefer_text_en
        If True, prefer "text_en" over "text" when extracting display text for grade
        level nodes.

    Returns
    -------
    list[str]
        The list of grade level tags collected from the ancestors of the given node, in
        order from closest ancestor to farthest (but de-duped if the same grade level
        appears multiple times in the ancestry). Grade level tags are determined by the
        display text of ancestor nodes with role "grade_level". If no grade level
        ancestors are found, returns an empty list.
    """

    cur: Optional[str] = node_id
    output: list[str] = []
    seen: set[str] = set()

    while cur and cur != ctx.root_id and cur not in seen:
        seen.add(cur)
        node = ctx.nodes_by_id.get(cur) or {}

        if node.get("role") == "grade_level":
            label = _node_display_text(node=node, prefer_text_en=prefer_text_en)

            if label:
                output.append(label)

        cur = ctx.parent_by_child.get(cur)

    output.reverse()

    # De-dupe while preserving order.
    deduped: list[str] = []
    dset: set[str] = set()

    for g in output:
        if g not in dset:
            deduped.append(g)
            dset.add(g)

    return deduped


def _compute_export_children(
    *, config: CreateKGConfig, ctx: ExportContext, emit_flag: dict[str, bool]
) -> tuple[dict[str, list[str]], DefaultDict[str, list[dict[str, Any]]]]:
    """Build export-time parent-to-children mapping with aux reparenting.

    Parameters
    ----------
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.
    emit_flag
        Node-level emit flags (True if the node should be emitted).

    Returns
    -------
    tuple
        (export_children, aux_attach_to_expectation) — the parent-to-children
        mapping and metadata attachments for expectation nodes.
    """

    aux_attach_to_expectation: DefaultDict[str, list[dict[str, Any]]] = defaultdict(
        list
    )
    export_children: dict[str, list[str]] = {}

    for parent_id, kids in ctx.children_by_parent.items():
        if parent_id == ctx.root_id:
            parent_role = NodeRole.FRAMEWORK.value
        else:
            parent_role = str(ctx.nodes_by_id[parent_id].get("role") or "")

        ordered_emitted_kids = [cid for cid in kids if emit_flag.get(cid, False)]

        if config.aux_statement_parenting == "under_expectation" and _is_grouping_role(
            config=config, role=parent_role
        ):
            new_kids = _reparent_aux_under_expectations(
                aux_attach_to_expectation=aux_attach_to_expectation,
                config=config,
                ctx=ctx,
                export_children=export_children,
                ordered_kids=ordered_emitted_kids,
            )
        else:
            new_kids = ordered_emitted_kids

        existing = export_children.get(parent_id, [])
        merged = list(new_kids)

        for c in existing:
            if c not in merged:
                merged.append(c)

        export_children[parent_id] = merged

    return export_children, aux_attach_to_expectation


def _emit_framework(
    *,
    canonical_ir_created_at: Optional[str],
    config: CreateKGConfig,
    ctx: ExportContext,
    decision_set_id: Optional[str],
    provenance_context: Optional[dict[str, Any]] = None,
) -> StandardsFramework:
    """Emit the StandardsFramework entity.

    Parameters
    ----------
    canonical_ir_created_at
        The creation datetime for the framework.
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.
    decision_set_id
        Optional decision set ID to include in metadata.
    provenance_context
        Optional provenance context to include in metadata.

    Returns
    -------
    StandardsFramework
        The constructed StandardsFramework entity.
    """

    framework_id = uuid5(
        config.namespace_uuid, f"lc:curriculum:{ctx.doc_key}:framework"
    )
    metadata = ctx.get_framework_metadata()
    prefer_en = config.description_text_policy == "prefer_text_en"
    root_node = ctx.nodes_by_id.get(ctx.root_id, {})
    name = _node_display_text(node=root_node, prefer_text_en=prefer_en) or ctx.pdf_name

    return StandardsFramework(
        academic_subject=metadata["academic_subject_default"],
        adoption_status=metadata["adoption_status"],
        attribution_statement=metadata["attribution_statement"],
        author=metadata["author"],
        case_identifier_uri=f"{config.case_uri_base}{framework_id}",
        case_identifier_uuid=framework_id,
        date_created=canonical_ir_created_at,
        date_modified=None,
        identifier=framework_id,
        in_language=metadata["in_language"],
        jurisdiction=metadata["jurisdiction"],
        license=metadata["license"],
        metadata={
            "decision_set_id": decision_set_id,
            "doc_key": ctx.doc_key,
            "export_dialect": metadata.get("export_dialect"),
            "pdf_name": ctx.pdf_name,
            "provenance_context": provenance_context or {},
        },
        name=name,
        provider=config.provider,
    )


def _emit_has_child(
    *,
    child_uuid: UUID,
    config: CreateKGConfig,
    doc_key: str,
    parent_uuid: UUID,
    relationship_metadata: Optional[dict[str, Any]] = None,
) -> Relationship:
    """Create a hasChild Relationship between two StandardsFrameworkItems.

    Parameters
    ----------
    child_uuid
        The UUID of the child StandardsFrameworkItem.
    config
        The CreateKGConfig for export.
    doc_key
        The document key for namespacing.
    parent_uuid
        The UUID of the parent StandardsFrameworkItem.
    relationship_metadata
        Optional metadata to attach to the relationship.

    Returns
    -------
    Relationship
        The constructed hasChild Relationship.
    """

    return Relationship(
        attribution_statement=config.attribution_statement,
        author=config.author,
        identifier=uuid5(
            config.namespace_uuid,
            f"lc:curriculum:{doc_key}:rel:hasChild:{parent_uuid}:{child_uuid}",
        ),
        license=config.license,
        metadata=relationship_metadata or {},
        provider=config.provider,
        relationship_type="hasChild",
        source_entity="StandardsFrameworkItem",  # May be overwritten for framework edges
        source_entity_key="caseIdentifierUUID",
        source_entity_value=str(parent_uuid),
        target_entity="StandardsFrameworkItem",
        target_entity_key="caseIdentifierUUID",
        target_entity_value=str(child_uuid),
    )


def _emit_sfi(
    *,
    aux_attachments: Optional[list[dict[str, Any]]] = None,
    canonical_ir_created_at: Any,
    config: CreateKGConfig,
    ctx: ExportContext,
    node_id: str,
) -> StandardsFrameworkItem:
    """Emit a StandardsFrameworkItem for a given canonical node.

    Parameters
    ----------
    aux_attachments
        Optional list of auxiliary statements (e.g., guidance, descriptors) to attach
        to this SFI's metadata.
    canonical_ir_created_at
        The creation datetime for the CanonicalIR, to use as the SFI's date_created
        field.
    config
        The CreateKGConfig for export, which may influence text selection and statement
        type normalization.
    ctx
        The ExportContext for the CanonicalIR, providing access to node properties and
        framework metadata.
    node_id
        The ID of the canonical node to emit as an SFI.

    Returns
    -------
    StandardsFrameworkItem
        The constructed StandardsFrameworkItem for the given node.
    """

    node = ctx.nodes_by_id[node_id]
    prefer_en = config.description_text_policy == "prefer_text_en"

    path_key = ctx.compute_path_key(node_id)
    sfi_id = uuid5(config.namespace_uuid, f"lc:curriculum:{ctx.doc_key}:sfi:{path_key}")

    role = str(node.get("role") or "")
    desc = _node_display_text(node=node, prefer_text_en=prefer_en)
    bbox = node.get("bbox")

    metadata: dict[str, Any] = {
        "bbox": bbox,
        "canonical_node_id": node_id,
        "local_code": node.get("local_code"),
        "normalized_text": node.get("normalized_text"),
        "page_indices": node.get("page_indices", []),
        "role": role,
        "source_decision_ids": node.get("source_decision_ids", []),
        "source_label": node.get("source_label"),
        "source_segment_ids": node.get("source_segment_ids", []),
    }

    # Make bbox interpretation self-describing by pointing to the framework-level bbox
    # context. (framework.metadata.provenance_context.bbox) should include coord_space,
    # dpi, page dims, etc.
    if bbox is not None:
        metadata["bbox_ref"] = "framework.metadata.provenance_context.bbox"

    if aux_attachments:
        metadata["aux_statements"] = aux_attachments

    return StandardsFrameworkItem(
        academic_subject=ctx.get_framework_metadata()["academic_subject_default"],
        attribution_statement=config.attribution_statement,
        author=config.author,
        case_identifier_uri=f"{config.case_uri_base}{sfi_id}",
        case_identifier_uuid=sfi_id,
        date_created=canonical_ir_created_at,
        date_modified=None,
        description=desc,
        grade_level=_collect_grade_levels(
            ctx=ctx, node_id=node_id, prefer_text_en=prefer_en
        ),
        identifier=sfi_id,
        in_language=ctx.get_framework_metadata()["in_language"],
        jurisdiction=ctx.get_framework_metadata()["jurisdiction"],
        license=config.license,
        metadata=metadata,
        normalized_statement_type=_normalized_statement_type(config=config, role=role),
        notes=None,
        provider=config.provider,
        statement_code=(node.get("local_code") or None),
        statement_type=(node.get("source_label") or role or None),
    )


def _emit_sfis(
    *,
    aux_attach_to_expectation: DefaultDict[str, list[dict[str, Any]]],
    canonical_created_at_iso: Optional[str],
    config: CreateKGConfig,
    ctx: ExportContext,
    emit_flag: dict[str, bool],
) -> dict[str, StandardsFrameworkItem]:
    """Emit StandardsFrameworkItems for all flagged nodes.

    Parameters
    ----------
    aux_attach_to_expectation
        Metadata attachments for expectation nodes.
    canonical_created_at_iso
        The ISO-8601 creation datetime.
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.
    emit_flag
        Node-level emit flags.

    Returns
    -------
    dict[str, StandardsFrameworkItem]
        Mapping of canonical node ID to emitted SFI.
    """

    sfi_by_node: dict[str, StandardsFrameworkItem] = {}

    for node_id, ok in emit_flag.items():
        if not ok:
            continue

        sfi_by_node[node_id] = _emit_sfi(
            aux_attachments=aux_attach_to_expectation.get(node_id),
            canonical_ir_created_at=canonical_created_at_iso,
            config=config,
            ctx=ctx,
            node_id=node_id,
        )

    return sfi_by_node


def _is_grouping_role(*, config: CreateKGConfig, role: str) -> bool:
    """Determine if a role is a grouping role (not expectation/aux).

    Parameters
    ----------
    config
        The CreateKGConfig for export (may influence role interpretation).
    role
        The role string to check.

    Returns
    -------
    bool
        True if the role is a grouping role, False otherwise.
    """

    if role == NodeRole.FRAMEWORK.value:
        return False

    if role in {item.value for item in StatementRole}:
        return False

    if config.grouping_role_policy == "loose":
        return True

    allowed = {r.value for r in config.grouping_roles_whitelist}

    return role in allowed


def _node_display_text(*, node: dict[str, Any], prefer_text_en: bool) -> str:
    """Determine display text for a node, preferring title over body, and falling back
    to local_code or role if no text found.

    Parameters
    ----------
    node
        The node dictionary to extract text from.
    prefer_text_en
        If True, prefer "text_en" over "text" when extracting from title/body.

    Returns
    -------
    str
        The display text for the node.
    """

    title = _pick_text(unit=node.get("title"), prefer_text_en=prefer_text_en)

    if title:
        return title

    body = _pick_text(unit=node.get("body"), prefer_text_en=prefer_text_en)

    if body:
        return body

    # Last resort fallback: code or role.
    return (node.get("local_code") or node.get("role") or "").strip()


def _normalized_statement_type(*, config: CreateKGConfig, role: str) -> str:
    """Normalize a node role to a statement type for
    StandardsFrameworkItem.statement_type.

    Parameters
    ----------
    config
        The CreateKGConfig for export (may influence normalization).
    role
        The node role to normalize.

    Returns
    -------
    str
        The normalized statement type (e.g., "Standard", "Other", "Standard Grouping").
    """

    if role == StatementRole.EXPECTATION.value:
        return "Standard"

    if role in AUX_ROLES:
        return "Other"

    if _is_grouping_role(config=config, role=role):
        return "Standard Grouping"

    return "Other"


def _pick_text(*, prefer_text_en: bool, unit: Any) -> str:
    """Retrieve text from a title/body unit.

    NB: Canonical nodes store title/body as a dict like:
        {"language": "...", "text": "...", "text_en": "..."}.

    Parameters
    ----------
    prefer_text_en
        If True, prefer "text_en" over "text" if both are present.
    unit
        The title/body unit to extract text from.

    Returns
    -------
    str
        The extracted text, or empty string if none found.
    """

    if not isinstance(unit, dict):
        return ""

    if prefer_text_en:
        t = (unit.get("text_en") or "").strip()

        if t:
            return t

    return ((unit.get("text") or "") or (unit.get("text_en") or "")).strip()


def _prune_empty_groupings(
    *,
    config: CreateKGConfig,
    ctx: ExportContext,
    emit_flag: dict[str, bool],
    export_children: dict[str, list[str]],
) -> None:
    """Iteratively prune grouping nodes that have no emitted children. Mutates
    emit_flag and export_children in place.

    Parameters
    ----------
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.
    emit_flag
        Node-level emit flags (mutated in place).
    export_children
        Parent-to-children mapping (mutated in place).
    """

    changed = True
    emitted: set[str] = {nid for nid, ok in emit_flag.items() if ok}

    while changed:
        changed = False
        to_prune: list[str] = []

        for nid in list(emitted):
            role = str(ctx.nodes_by_id[nid].get("role") or "")
            is_expectation = role == StatementRole.EXPECTATION.value

            if _is_grouping_role(config=config, role=role) and not is_expectation:
                live_children = [
                    c for c in export_children.get(nid, []) if c in emitted
                ]

                if len(live_children) == 0:
                    to_prune.append(nid)

        if to_prune:
            changed = True

            for nid in to_prune:
                emitted.discard(nid)
                export_children.pop(nid, None)
                pid = ctx.parent_by_child.get(nid)

                if pid is not None and pid in export_children:
                    export_children[pid] = [c for c in export_children[pid] if c != nid]

    # Drop children that are no longer emitted.
    for pid, kids in list(export_children.items()):
        export_children[pid] = [c for c in kids if c == ctx.root_id or c in emitted]

    # Reflect pruning back into emit_flag.
    for nid in list(emit_flag.keys()):
        emit_flag[nid] = nid in emitted


def _reparent_aux_under_expectations(
    *,
    aux_attach_to_expectation: DefaultDict[str, list[dict[str, Any]]],
    config: CreateKGConfig,
    ctx: ExportContext,
    export_children: dict[str, list[str]],
    ordered_kids: list[str],
) -> list[str]:
    """Re-parent aux statements under their preceding expectation sibling.

    Walks the ordered children of a grouping node, attaching aux nodes either
    to expectation metadata or as export-time children of the last expectation.

    Parameters
    ----------
    aux_attach_to_expectation
        Mutable mapping collecting metadata attachments for expectation nodes.
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.
    export_children
        Mutable mapping of parent -> children being built up during export.
    ordered_kids
        The ordered, emit-eligible children of the parent.

    Returns
    -------
    list[str]
        The new ordered children for the parent (non-aux and un-reparented nodes).
    """

    last_expectation: Optional[str] = None
    new_kids: list[str] = []
    prefer_en = config.description_text_policy == "prefer_text_en"

    for cid in ordered_kids:
        node = ctx.nodes_by_id[cid]
        role = str(node.get("role") or "")

        if role == StatementRole.EXPECTATION.value:
            last_expectation = cid
            new_kids.append(cid)
            continue

        if role in AUX_ROLES and last_expectation:
            attach_to_metadata = (
                role == StatementRole.GUIDANCE.value
                and config.guidance_handling == "attach_to_expectation_metadata"
            ) or (
                role == StatementRole.DESCRIPTOR.value
                and config.descriptor_handling == "attach_to_expectation_metadata"
            )

            if attach_to_metadata:
                aux_attach_to_expectation[last_expectation].append(
                    {
                        "role": role,
                        "text": _node_display_text(node=node, prefer_text_en=prefer_en),
                        "canonical_node_id": cid,
                        "page_indices": node.get("page_indices", []),
                        "source_decision_ids": node.get("source_decision_ids", []),
                        "source_segment_ids": node.get("source_segment_ids", []),
                    }
                )
                continue

            export_children.setdefault(last_expectation, [])
            export_children[last_expectation].append(cid)
            continue

        new_kids.append(cid)

    return new_kids


def _to_iso8601_or_none(v: Any) -> Optional[str]:
    """Normalize a value to an ISO-8601 string if possible. LC KG export schemas
    require date fields to be strings (or None). CanonicalIR created_at is often parsed
    as a datetime by Pydantic.

    Parameters
    ----------
    v
        The value to normalize.

    Returns
    -------
    Optional[str]
        The ISO-8601 string representation, or None if not possible.
    """

    if v is None:
        return None

    if isinstance(v, str):
        v2 = v.strip()
        return v2 or None

    if isinstance(v, datetime):
        return v.isoformat()

    iso = getattr(v, "isoformat", None)

    if callable(iso):
        try:
            return str(iso())
        except Exception:  # pylint: disable=broad-except
            return None

    return None


def _verify_standards_export(
    *,
    framework: StandardsFramework,
    parent_to_children: dict[str, list[str]],
    relationships: list[Relationship],
    sfi_by_node: dict[str, StandardsFrameworkItem],
) -> None:
    """Verify the integrity of the exported standards artifacts.

    Parameters
    ----------
    framework
        The exported StandardsFramework.
    parent_to_children
        The mapping of parent IDs to ordered child IDs.
    relationships
        The list of exported Relationships.
    sfi_by_node
        The mapping of canonical node IDs to exported StandardsFrameworkItems.

    Raises
    ------
    ValueError
        If any integrity check fails.
    """

    # Check referential integrity.
    sfi_ids = {str(sfi.case_identifier_uuid) for sfi in sfi_by_node.values()}
    fw_id = str(framework.case_identifier_uuid)

    for r in relationships:
        s_ok = (r.source_entity_value == fw_id) or (r.source_entity_value in sfi_ids)
        t_ok = r.target_entity_value in sfi_ids

        if not s_ok or not t_ok:
            raise ValueError(
                f"Relationship references missing entity: {r.relationship_type} "
                f"{r.source_entity_value} -> {r.target_entity_value}"
            )

    # Check ordering integrity.
    rel_children_by_parent: DefaultDict[str, set[str]] = defaultdict(set)
    for r in relationships:
        if r.relationship_type == "hasChild":
            rel_children_by_parent[r.source_entity_value].add(r.target_entity_value)

    for parent, kids in rel_children_by_parent.items():
        ordered = parent_to_children.get(parent)

        if ordered is None:
            raise ValueError(f"Missing hierarchy order for parent: {parent}")

        if set(ordered) != set(kids):
            raise ValueError(f"Hierarchy order child set mismatch for parent: {parent}")

    # Ensure at least one expectation ("Standard") exists. This prevents "successful"
    # exports that only contain groupings.
    if not sfi_by_node:
        raise ValueError("No StandardsFrameworkItems emitted; check drop policies.")

    has_any_standard = any(
        sfi.normalized_statement_type == "Standard" for sfi in sfi_by_node.values()
    )

    if not has_any_standard:
        raise ValueError(
            "No expectation SFIs emitted (normalized_statement_type='Standard'). "
            "Export produced only groupings/other items; check canonical IR roles and "
            "drop/handling policies."
        )


def export_academic_standards(
    *,
    canonical_ir_created_at: Any = None,
    config: CreateKGConfig,
    ctx: ExportContext,
    decision_set_id: Optional[str] = None,
    kg_dirs: KGDirs,
    provenance_context: Optional[dict[str, Any]] = None,
) -> AcademicStandardsExport:
    """Export Academic Standards KG artifacts from Canonical IR context.

    Parameters
    ----------
    canonical_ir_created_at
        The creation datetime for the CanonicalIR.
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.
    decision_set_id
        Optional decision set ID to include in metadata.
    kg_dirs
        The KGDirs for output.
    provenance_context
        Optional provenance context to include in metadata.

    Returns
    -------
    AcademicStandardsExport
        The exported Academic Standards KG artifacts.
    """

    canonical_created_at_iso = _to_iso8601_or_none(canonical_ir_created_at)

    framework = _emit_framework(
        canonical_ir_created_at=canonical_created_at_iso,
        config=config,
        ctx=ctx,
        decision_set_id=decision_set_id,
        provenance_context=provenance_context,
    )
    framework_uuid = framework.case_identifier_uuid

    # Precompute node-level emit flags before pruning.
    emit_flag: dict[str, bool] = {
        node_id: should_emit_node(ctx=ctx, config=config, node_id=node_id)
        for node_id in ctx.nodes_by_id
        if node_id != ctx.root_id
    }

    # Export-time aux parenting: operate on canonical IDs.
    export_children, aux_attach_to_expectation = _compute_export_children(
        config=config,
        ctx=ctx,
        emit_flag=emit_flag,
    )

    # NB: If aux statements are "attach_to_expectation_metadata", they should NOT be
    # counted as emitted nodes for pruning. Aux-parenting needed to see them to build
    # aux_attach_to_expectation, but they will not be emitted as SFIs later.
    if (
        config.guidance_handling == "attach_to_expectation_metadata"
        or config.descriptor_handling == "attach_to_expectation_metadata"
    ):
        for nid, ok in list(emit_flag.items()):
            if not ok:
                continue

            role = str(ctx.nodes_by_id[nid].get("role") or "")

            if (
                role == StatementRole.GUIDANCE.value
                and config.guidance_handling == "attach_to_expectation_metadata"
            ):
                emit_flag[nid] = False
            elif (
                role == StatementRole.DESCRIPTOR.value
                and config.descriptor_handling == "attach_to_expectation_metadata"
            ):
                emit_flag[nid] = False

    # Prune empty groupings (strict; no reattachment).
    if config.prune_empty_groupings:
        _prune_empty_groupings(
            config=config,
            ctx=ctx,
            emit_flag=emit_flag,
            export_children=export_children,
        )

    # Emit SFIs.
    sfi_by_node = _emit_sfis(
        aux_attach_to_expectation=aux_attach_to_expectation,
        canonical_created_at_iso=canonical_created_at_iso,
        config=config,
        ctx=ctx,
        emit_flag=emit_flag,
    )

    # Build relationships + order mapping.
    relationships, order_map = _build_relationships_and_order(
        config=config,
        ctx=ctx,
        export_children=export_children,
        framework_uuid=framework_uuid,
        sfi_by_node=sfi_by_node,
    )

    _verify_standards_export(
        framework=framework,
        parent_to_children=order_map,
        relationships=relationships,
        sfi_by_node=sfi_by_node,
    )

    standards = AcademicStandardsExport(
        framework=framework,
        items=list(sfi_by_node.values()),
        order=HierarchyOrderExport(order=order_map),
        relationships=relationships,
    )

    write_to_json(
        fp=kg_dirs.academic_standards / "standards_framework.json",
        json_info=standards.framework,
    )
    write_to_json(
        fp=kg_dirs.academic_standards / "standards_framework_items.json",
        json_info=standards.items,
    )
    write_to_json(
        fp=kg_dirs.academic_standards / "standards_has_child_relationships.json",
        json_info=standards.relationships,
    )
    write_to_json(
        fp=kg_dirs.academic_standards / "standards_hierarchy_order.json",
        json_info=standards.order,
    )

    return standards


def should_emit_node(
    *, config: CreateKGConfig, ctx: ExportContext, node_id: str
) -> bool:
    """Determine whether a canonical node should be emitted as a StandardsFrameworkItem
    based on its properties and the export configuration.

    Parameters
    ----------
    config
        The CreateKGConfig for export, which may influence drop policies.
    ctx
        The ExportContext for the CanonicalIR, providing access to node properties and
        decision/segment information for drop policies.
    node_id
        The ID of the canonical node to evaluate.

    Returns
    -------
    bool
        True if the node should be emitted as a StandardsFrameworkItem, False if it
        should be dropped.
    """

    node = ctx.nodes_by_id[node_id]
    role = str(node.get("role") or "")

    # Segment drop policy.
    for did in node.get("source_decision_ids", []):
        dec = ctx.decisions_by_id.get(did)

        if dec and ctx.should_drop_segment(decision=dec):
            return False

    # Role handling.
    if role == StatementRole.GUIDANCE.value and config.guidance_handling == "drop":
        return False

    if role == StatementRole.DESCRIPTOR.value and config.descriptor_handling == "drop":
        return False

    # Strict grouping policy: if it's not a statement role, it must be an allowed
    # grouping (otherwise drop or export-as-Other depending on config).
    if (
        config.grouping_role_policy == "whitelist"
        and role != NodeRole.FRAMEWORK.value
        and role not in {item.value for item in StatementRole}
        and not _is_grouping_role(config=config, role=role)
    ):
        return config.non_grouping_role_handling != "drop"

    return True
