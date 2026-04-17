"""This module contains functionalities related to exporting the Academic Standards
knowledge graph. It exports a shape-preserving Learning Commons Academic Standards
knowledge graph from the CanonicalIR using ExportContext indexes and CreateKGConfig
policies.
"""

# Future Library
from __future__ import annotations

# Standard Library
import re

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, DefaultDict, Optional
from uuid import UUID, uuid5

# Third Party Library
from loguru import logger

# Package Library
from skg.kgs.schemas import (
    HierarchyOrderExport,
    Relationship,
    StandardsFramework,
    StandardsFrameworkItem,
)
from skg.kgs.utils import ExportContext, KGDirs, node_display_text, normalize_key_token
from skg.regexes import ROMAN_RE
from skg.schemas import CreateKGConfig
from skg.utils.constants import NodeRole, StatementRole
from skg.utils.general import open_json_type, write_to_json

AUX_ROLES: set[str] = {StatementRole.DESCRIPTOR.value, StatementRole.GUIDANCE.value}
ROMAN_MAP = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
    "XIII": 13,
    "XIV": 14,
    "XV": 15,
}
STATEMENT_ROLE_VALUES: set[str] = {item.value for item in StatementRole}


@dataclass
class AcademicStandardsExport:
    """The output of exporting Academic Standards KG artifacts."""

    drop_reasons: dict[str, str]  # canonical_node_id -> drop reason string
    framework: StandardsFramework
    items: list[StandardsFrameworkItem]
    order: HierarchyOrderExport
    pruned_node_ids: set[str]  # Node IDs pruned as empty groupings
    relationships: list[Relationship]
    reparent_stats: dict[str, Any]  # aux_reparented_count, orphan_aux_count, etc.


def _append_unique_child(
    *, child_id: str, export_children: dict[str, list[str]], parent_id: str
) -> bool:
    """Append a child to an export parent only if not already present.

    Parameters
    ----------
    child_id
        The canonical node ID of the child to append.
    export_children
        The parent-to-children mapping being built for export.
    parent_id
        The canonical node ID of the parent.

    Returns
    -------
    bool
        True if the child was newly appended, False if it was already present.
    """

    children = export_children.setdefault(parent_id, [])

    if child_id in children:
        return False

    children.append(child_id)
    return True


def _attach_aux_statements_in_export_tree(
    *,
    attached_aux_node_ids: set[str],
    aux_nodes_attached_to_expectation: DefaultDict[str, list[dict[str, Any]]],
    config: CreateKGConfig,
    ctx: ExportContext,
    emit_flag: dict[str, bool],
    export_children: dict[str, list[str]],
    orphan_aux_node_ids: set[str],
) -> dict[str, Any]:
    """Attach aux statements to expectation metadata without reparenting.

    This is an "attach-only" discovery pass used when the canonical IR keeps
    descriptors/guidance as siblings (or children) of expectations, but the export
    config requests attaching those aux statements into the owning expectation's
    metadata via `attach_to_expectation_metadata`.

    The pass:

    1. **Child layout**: For every emitted expectation node, attach any emitted
        guidance/descriptor children found under that expectation in `export_children`.
    2. **Sibling layout**: For every non-statement parent node, walk its ordered
        children and attach any aux siblings to the most recent preceding expectation
        sibling.

    The hierarchy (`export_children`) is not modified. Which aux nodes are ultimately
    emitted as SFIs is controlled by `_suppress_attached_to_expectation` (based on
    which aux nodes were successfully attached).

    This function does the following: “Given the current export tree, find every
    guidance/descriptor node that should be attached into an expectation’s metadata,
    either because it is already a child of that expectation or because it appears
    after that expectation in sibling order.”

    Parameters
    ----------
    attached_aux_node_ids
        Mutable set collecting canonical node IDs successfully attached.
    aux_nodes_attached_to_expectation
        Mutable mapping collecting metadata attachments for expectation nodes.
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.
    emit_flag
        Node-level emit flags (used to ignore already-dropped nodes).
    export_children
        Parent-to-children mapping for export.
    orphan_aux_node_ids
        Mutable set collecting aux nodes that had no owning expectation in sibling
        order.

    Returns
    -------
    dict[str, Any]
        Stats about attachments performed in this pass.
    """

    child_attached = _attach_child_layout_aux_nodes(
        attached_aux_node_ids=attached_aux_node_ids,
        aux_nodes_attached_to_expectation=aux_nodes_attached_to_expectation,
        config=config,
        ctx=ctx,
        emit_flag=emit_flag,
        export_children=export_children,
    )

    sibling_attached, sibling_orphans = _attach_sibling_layout_aux_nodes(
        attached_aux_node_ids=attached_aux_node_ids,
        aux_nodes_attached_to_expectation=aux_nodes_attached_to_expectation,
        config=config,
        ctx=ctx,
        emit_flag=emit_flag,
        export_children=export_children,
        orphan_aux_node_ids=orphan_aux_node_ids,
    )
    return {
        "attach_only_attached_count": child_attached + sibling_attached,
        "attach_only_orphan_aux_count": sibling_orphans,
    }


def _attach_child_layout_aux_nodes(
    *,
    attached_aux_node_ids: set[str],
    aux_nodes_attached_to_expectation: DefaultDict[str, list[dict[str, Any]]],
    config: CreateKGConfig,
    ctx: ExportContext,
    emit_flag: dict[str, bool],
    export_children: dict[str, list[str]],
) -> int:
    """Processes child layout, attaching emitted guidance/descriptors to their parent
    expectation. This layout is basically "expectation already directly owns aux
    children.".

    Examples
    --------
    1. Expectation already owns guidance/descriptor as children
        Suppose the export tree already contains:

            export_children[E1] == [G1, D1]

        where:

            E1.role == "expectation"
            G1.role == "guidance"
            D1.role == "descriptor"

        and both aux roles are configured with "attach_to_expectation_metadata".

        This function does not modify `export_children`. Instead, it records payloads
        for `G1` and `D1` under:

            aux_nodes_attached_to_expectation[E1]

        Result:
            - E1 is now associated with aux metadata for G1 and D1
            - G1 and D1 are added to `attached_aux_node_ids`
            - The hierarchy remains unchanged at this stage

    2. Senegal reading curriculum example
        A common Senegal reading pattern is:

            E1 = "Objectif spécifique"
            G1 = "Contenus"
            D1 = "Durée"

        If the export tree already stores `Contenus` and `Durée` as direct children of
        the `Objectif spécifique` expectation, this function attaches both as metadata
        to that expectation.

        Example:
            E1: "Joxe ay santaane / Donner des consignes"
            G1: guidance content
            D1: "Ayu bés 22 / Semaine 22"

    Parameters
    ----------
    attached_aux_node_ids
        Mutable set collecting canonical node IDs successfully attached.
    aux_nodes_attached_to_expectation
        Mutable mapping collecting metadata attachments for expectation nodes.
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.
    emit_flag
        Node-level emit flags (used to ignore already-dropped nodes).
    export_children
        Parent-to-children mapping for export.

    Returns
    -------
    int
        The number of auxiliary nodes successfully attached in this pass.
    """

    prefer_en = config.description_text_policy == "prefer_text_en"
    attached_count = 0

    for exp_id, node in ctx.nodes_by_id.items():
        if (
            not emit_flag.get(exp_id, False)
            or node["role"] != StatementRole.EXPECTATION.value
        ):
            continue

        for child_id in export_children.get(exp_id, []):
            if not emit_flag[child_id]:
                continue

            child_role = ctx.nodes_by_id[child_id]["role"]

            if child_role not in AUX_ROLES or not _is_attachable(
                config=config, role=child_role
            ):
                continue

            if child_id not in attached_aux_node_ids:
                attached_aux_node_ids.add(child_id)

            if _is_already_attached(
                aux_node_id=child_id,
                aux_nodes_attached_to_expectation=aux_nodes_attached_to_expectation,
                expectation_id=exp_id,
            ):
                continue

            aux_nodes_attached_to_expectation[exp_id].append(
                _build_aux_payload(aux_node_id=child_id, ctx=ctx, prefer_en=prefer_en)
            )
            attached_count += 1

    return attached_count


def _attach_sibling_layout_aux_nodes(
    *,
    attached_aux_node_ids: set[str],
    aux_nodes_attached_to_expectation: DefaultDict[str, list[dict[str, Any]]],
    config: CreateKGConfig,
    ctx: ExportContext,
    emit_flag: dict[str, bool],
    export_children: dict[str, list[str]],
    orphan_aux_node_ids: set[str],
) -> tuple[int, int]:
    """Processes sibling layout, attaching auxiliary nodes to the most recent preceding
    expectation. This layout is basically "expectation and aux are siblings, and aux
    belongs to the most recent preceding expectation.".

    Examples
    --------
    1. Ordered sibling row under a grouping parent
        Suppose a grouping parent has the following emitted children in order:

            [E1, G1, D1, E2]

        where:

            E1.role == "expectation"
            G1.role == "guidance"
            D1.role == "descriptor"
            E2.role == "expectation"

        This function scans left to right and attaches `G1` and `D1` to the most recent
        preceding expectation, which is `E1`.

        Result:
            aux_nodes_attached_to_expectation[E1] contains payloads for G1 and D1

        `E2` becomes the new `last_expectation` for any later aux siblings.

    2. Leading aux nodes become orphans
        Suppose a parent has:

            [G0, D0, E1]

        where no expectation appears before `G0` and `D0`.

        Because there is no preceding expectation to own them, the function does not
        attach `G0` or `D0` to `E1`. Instead, it records them in `orphan_aux_node_ids`.

        Result:
            - `G0` and `D0` are marked orphan aux
            - they are not attached to `E1`

    3. Senegal reading curriculum example
        A common Senegal planning row appears in sibling order as:

            [Objectif spécifique, Contenus, Durée]

        For example:

            E1: "Joxe ay santaane / Donner des consignes"
            G1: guidance content under "Contenus"
            D1: "Ayu bés 22 / Semaine 22"

        The function attaches `Contenus` and `Durée` to the expectation
        "Joxe ay santaane / Donner des consignes" because it is the most recent
        preceding expectation in sibling order.

    Parameters
    ----------
    attached_aux_node_ids
        Mutable set collecting canonical node IDs successfully attached.
    aux_nodes_attached_to_expectation
        Mutable mapping collecting metadata attachments for expectation nodes.
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.
    emit_flag
        Node-level emit flags (used to ignore already-dropped nodes).
    export_children
        Parent-to-children mapping for export.
    orphan_aux_node_ids
        Mutable set collecting aux nodes that had no owning expectation in sibling
        order.

    Returns
    -------
    tuple[int, int]
        A tuple of (attached_count, sibling_orphans_count) recorded during this pass.
    """

    prefer_en = config.description_text_policy == "prefer_text_en"
    total_attached = 0
    total_orphans = 0

    for parent_id, kids in export_children.items():
        parent_role = (
            NodeRole.FRAMEWORK.value
            if parent_id == ctx.root_id
            else ctx.nodes_by_id[parent_id]["role"]
        )

        if parent_role in STATEMENT_ROLE_VALUES:
            continue

        attached, orphans = _process_sibling_group(
            attached_aux_node_ids=attached_aux_node_ids,
            aux_nodes_attached_to_expectation=aux_nodes_attached_to_expectation,
            config=config,
            ctx=ctx,
            emit_flag=emit_flag,
            kids=kids,
            orphan_aux_node_ids=orphan_aux_node_ids,
            prefer_en=prefer_en,
        )
        total_attached += attached
        total_orphans += orphans

    return total_attached, total_orphans


def _build_academic_standards_graph_bundle(
    *,
    academic_standards: AcademicStandardsExport,
    config: CreateKGConfig,
    ctx: ExportContext,
) -> dict[str, Any]:
    """Build a single graph-shaped JSON bundle from the already-exported academic
    standards artifacts.

    This function does not re-run standards export logic. Instead, it converts the
    typed `AcademicStandardsExport` outputs into a graph bundle with top-level metadata
    plus `nodes` and `relationships` lists.

    Behavior:

    1. Builds an edge-order lookup from `HierarchyOrderExport.order`, where each
        `(parent_uuid, child_uuid)` pair maps to its child position under that parent.
    2. Emits one graph node for the StandardsFramework and one graph node for each
        StandardsFrameworkItem, using `case_identifier_uuid` as the graph node ID.
    3. Converts each exported `hasChild` Relationship into a graph edge using:
        - `source_entity_value` as `start`
        - `target_entity_value` as `end`
        - the serialized Relationship record as `properties`
    4. Adds `order_index` to each graph edge's properties based on the hierarchy-order
        artifact so downstream graph consumers can recover sibling order directly from
        the edge list.
    5. Returns a single bundle dictionary with: `doc_key`, `export_dialect`,
        `generated_at`, `graph_type`, `nodes`, and `relationships`.

    Examples
    --------
    1. Framework and two first-level items
        Suppose the exported order artifact contains:

            framework_uuid -> [A, B]

        Then the bundle contains:
            - one StandardsFramework node with id = framework CASE UUID
            - two StandardsFrameworkItem nodes with ids A and B
            - two hasChild edges:
                framework -> A  (order_index = 0)
                framework -> B  (order_index = 1)

    2. Senegal expectation with attached aux metadata
        Suppose an expectation SFI "Joxe ay santaane / Donner des consignes" has
        attached aux metadata for:

            - guidance: "Contenus"
            - descriptor: "Ayu bés 22 / Semaine 22"

        and appears as a child of a Palier 3 grouping SFI.

        Then the bundle contains:
            - a node for the Palier 3 grouping
            - a node for the expectation SFI whose properties include the attached aux
                metadata
            - a single hasChild edge from Palier 3 -> expectation
            - no separate graph nodes or edges for the attached guidance/descriptor

    3. Relationship ordering
        Suppose parent P has ordered children [C1, C2, C3] in the hierarchy-order
        export.

        Then the bundle contains hasChild edges:
            P -> C1 with properties.order_index = 0
            P -> C2 with properties.order_index = 1
            P -> C3 with properties.order_index = 2

        This preserves sibling order directly on the edge records.

    Parameters
    ----------
    academic_standards
        The exported Academic Standards artifacts.
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.

    Returns
    -------
    dict[str, Any]
        The graph bundle dictionary.
    """

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build (parent, child) -> order_index map from the ordering artifact.
    order_index_by_edge: dict[tuple[str, str], int] = {}

    for parent_id, child_ids in academic_standards.order.order.items():
        for idx, child_id in enumerate(child_ids):
            order_index_by_edge[(parent_id, child_id)] = idx

    # Nodes: use `case_identifier_uuid` as the node key since relationships already key
    # off `case_identifier_uuid`.
    fw = academic_standards.framework
    nodes: list[dict[str, Any]] = [
        {
            "id": str(fw.case_identifier_uuid),
            "labels": ["StandardsFramework"],
            "properties": fw.model_dump(mode="json"),
        }
    ]

    for sfi in academic_standards.items:
        nodes.append(
            {
                "id": str(sfi.case_identifier_uuid),
                "labels": ["StandardsFrameworkItem"],
                "properties": sfi.model_dump(mode="json"),
            }
        )

    # Relationships: reuse Relationship export, but convert to edge shape and add
    # order_index.
    relationships: list[dict[str, Any]] = []

    for r in academic_standards.relationships:
        start_id = r.source_entity_value  # Already `case_identifier_uuid` as string
        end_id = r.target_entity_value  # Already `case_identifier_uuid` as string
        props = r.model_dump(mode="json")
        props["order_index"] = order_index_by_edge.get((start_id, end_id))
        assert r.relationship_type == "hasChild", (
            f"Unexpected relationship type '{r.relationship_type}' "
            f"in Academic Standards export bundle."
        )
        relationships.append(
            {
                "id": str(r.identifier),
                "type": r.relationship_type,
                "start": start_id,
                "end": end_id,
                "properties": props,
            }
        )

    return {
        "doc_key": ctx.doc_key,
        "export_dialect": config.export_dialect,
        "generated_at": generated_at,
        "graph_type": "academic_standards",
        "nodes": nodes,
        "relationships": relationships,
    }


def _build_aux_payload(
    *, aux_node_id: str, ctx: ExportContext, prefer_en: bool
) -> dict[str, Any]:
    """Build the metadata payload dictionary for an auxiliary node.

    Parameters
    ----------
    aux_node_id
        The ID of the auxiliary node.
    ctx
        The ExportContext containing the nodes.
    prefer_en
        Whether to prefer English text.

    Returns
    -------
    dict[str, Any]
        The payload representing the auxiliary node metadata.
    """

    node = ctx.nodes_by_id[aux_node_id]
    bbox = node["bbox"]
    payload: dict[str, Any] = {
        "bbox": bbox,
        "canonical_node_id": aux_node_id,
        "page_indices": node.get("page_indices", []),
        "role": node["role"],
        "source_decision_ids": node.get("source_decision_ids", []),
        "source_segment_ids": node.get("source_segment_ids", []),
        "text": node_display_text(node=node, prefer_text_en=prefer_en),
    }

    if bbox is not None:
        payload["bbox_ref"] = "framework.metadata.provenance_context.bbox"

    return payload


def _build_export_parent_by_child(
    *, root_id: str, export_children: dict[str, list[str]]
) -> dict[str, str]:
    """Build an export-time parent lookup from the finalized export tree.

    Parameters
    ----------
    root_id
        The canonical node ID of the root (framework) node.
    export_children
        The finalized parent-to-children mapping for export.

    Returns
    -------
    dict[str, str]
        A mapping of canonical child node ID to its assigned parent node ID in the
        export hierarchy. The root node ID is excluded from this mapping since it has
        no parent.

    Raises
    ------
    ValueError
        If any emitted child is assigned to more than one exported parent.
    """

    parent_by_child: dict[str, str] = {}

    for parent_id, child_ids in export_children.items():
        for child_id in child_ids:
            prior_parent = parent_by_child.get(child_id)

            if prior_parent is not None and prior_parent != parent_id:
                raise ValueError(
                    f"Export hierarchy integrity error: "
                    f"child node {child_id} appears under multiple parents "
                    f"({prior_parent}, {parent_id})."
                )

            parent_by_child[child_id] = parent_id

    # Remove the root from the `parent_by_child` mapping since it has no parent.
    parent_by_child.pop(root_id, None)
    return parent_by_child


def _build_initial_emit_flags(
    *, config: CreateKGConfig, ctx: ExportContext
) -> tuple[dict[str, bool], dict[str, str]]:
    """Precompute node-level emit flags and drop reasons before pruning.

    Parameters
    ----------
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.

    Returns
    -------
    tuple[dict[str, bool], dict[str, str]]
        A tuple of (emit_flag, drop_reasons) where:
            - emit_flag is a mapping of canonical node ID to a boolean indicating
                whether the node should be emitted based on export policies.
            - drop_reasons is a mapping of canonical node ID to a string describing
                the reason for dropping the node (only for nodes where emit_flag is
                False).
    """

    emit_flag: dict[str, bool] = {}
    drop_reasons: dict[str, str] = {}

    for node_id in ctx.nodes_by_id:
        # The root/framework node is always skipped and should not be subject to
        # dropping rules since it has no parent and serves as the anchor for the entire
        # export hierarchy.
        if node_id == ctx.root_id:
            continue

        ok, reason = _should_emit_node_with_reason(
            ctx=ctx, config=config, node_id=node_id
        )
        emit_flag[node_id] = ok

        if not ok:
            drop_reasons[node_id] = reason

    return emit_flag, drop_reasons


def _build_relationships_and_order(
    *,
    config: CreateKGConfig,
    ctx: ExportContext,
    export_children: dict[str, list[str]],
    export_order_index: dict[tuple[str, str], int],
    framework_uuid: UUID,
    sfi_by_node: dict[str, StandardsFrameworkItem],
) -> tuple[list[Relationship], dict[str, list[str]]]:
    """Build "hasChild" relationships and hierarchy order map.

    This function does three main things.
        1. `_edge_metadata()` pulls provenance for a canonical edge from
            `ctx.edge_metadata_by_pair` and packages it into relationship metadata:
            canonical parent/child IDs, canonical order index, source decision IDs,
            source segment IDs, and the export-time order index from
            `export_order_index`. This is how the relationship keeps both the original
            canonical edge trace and the final exported ordering trace.
        2. It emits framework -> first-level SFI relationships. It looks at
            `export_children[ctx.root_id]`, filters to children that actually exist in
            `sfi_by_node`, deduplicates them while preserving order, writes that
            ordered UUID list into `order_map[str(framework_uuid)]`, and emits one
            hasChild relationship per child with source_entity="StandardsFramework".
        3. It emits SFI -> SFI relationships for every surviving exported parent. For
            each non-root parent in `export_children`, it skips the parent unless that
            canonical parent itself became an SFI, filters/dedupes its emitted
            children, records their UUID order in `order_map[str(parent_sfi_uuid)]`,
            and emits one hasChild edge per child. Parents with no emitted children
            simply do not get an `order_map` entry.

    Examples
    --------
    1. Framework -> first-level items
        Suppose the finalized export tree contains:

            export_children[ROOT] = [A, B]

        and both `A` and `B` were emitted as SFIs with UUIDs `UA` and `UB`.

        Then the function produces:

            order_map[str(framework_uuid)] == [str(UA), str(UB)]

        and emits:

            StandardsFramework(framework_uuid) -[:hasChild]-> StandardsFrameworkItem(UA)
            StandardsFramework(framework_uuid) -[:hasChild]-> StandardsFrameworkItem(UB)

    2. SFI -> SFI edges under a surviving grouping
        Suppose a surviving exported parent `P` has finalized children:

            export_children[P] = [E1, E2]

        and all three nodes were emitted as SFIs with UUIDs `UP`, `UE1`, `UE2`.

        Then the function produces:

            order_map[str(UP)] == [str(UE1), str(UE2)]

        and emits:

            StandardsFrameworkItem(UP) -[:hasChild]-> StandardsFrameworkItem(UE1)
            StandardsFrameworkItem(UP) -[:hasChild]-> StandardsFrameworkItem(UE2)

    3. Attached aux nodes do not become hierarchy edges
        Suppose a Senegal row originally contained:

            E1(expectation), G1(guidance/"Contenus"), D1(descriptor/"Durée")

        and earlier export steps attached `G1` and `D1` into
        `E1.metadata["aux_statements"]` and suppressed them as standalone SFIs.

        Then `_build_relationships_and_order()` emits a hasChild edge only for `E1`
        within the hierarchy. It does not emit separate hierarchy edges for `G1` or
        `D1`.

    4. Duplicate child references are collapsed deterministically
        Suppose earlier hoisting produced:

            export_children[P] = [E1, E2, E1]

        Then `_dedupe_preserve_order()` normalizes this to:

            [E1, E2]

        so the export does not produce duplicate hasChild edges or duplicate ordered
        children.

    Parameters
    ----------
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.
    export_children
        The parent-to-children mapping.
    export_order_index
        Export-time (parent_id, child_id) -> order_index mapping derived from the
        finalized export tree after any hoisting/reparenting.
    framework_uuid
        The UUID of the framework entity.
    sfi_by_node
        Mapping of canonical node ID to emitted SFI.

    Returns
    -------
    tuple
        (relationships, order_map).
    """

    def _edge_metadata(*, parent_id: str, child_id: str) -> dict[str, Any]:
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
            "canonical_order_index": edge_md.get(
                "order_index", ctx.edge_order_index.get((parent_id, child_id))
            ),
            "canonical_parent_id": parent_id,
            "canonical_child_id": child_id,
            "canonical_edge_source_decision_ids": edge_md.get(
                "source_decision_ids", []
            ),
            "canonical_edge_source_segment_ids": edge_md.get("source_segment_ids", []),
            "export_order_index": export_order_index.get((parent_id, child_id)),
            "export_parent_id": parent_id,
        }

    relationships: list[Relationship] = []
    order_map: dict[str, list[str]] = {}

    # Root -> first-level children.
    root_children = _dedupe_preserve_order(
        [cid for cid in export_children.get(ctx.root_id, []) if cid in sfi_by_node]
    )
    order_map[str(framework_uuid)] = [
        str(sfi_by_node[cid].case_identifier_uuid) for cid in root_children
    ]

    for cid in root_children:
        relationships.append(
            _emit_has_child(
                child_uuid=sfi_by_node[cid].case_identifier_uuid,
                config=config,
                doc_key=ctx.doc_key,
                parent_uuid=framework_uuid,
                relationship_metadata=_edge_metadata(
                    parent_id=ctx.root_id, child_id=cid
                ),
                source_entity="StandardsFramework",
            )
        )

    # SFI -> SFI edges.
    for pid, kids in export_children.items():
        if pid == ctx.root_id or pid not in sfi_by_node:
            continue

        emitted_kids = _dedupe_preserve_order(
            [cid for cid in kids if cid in sfi_by_node]
        )

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
                    relationship_metadata=_edge_metadata(parent_id=pid, child_id=cid),
                )
            )

    return relationships, order_map


def _collect_grade_levels(
    *,
    ctx: ExportContext,
    node_id: str,
    parent_by_child: dict[str, str] | None = None,
    prefer_text_en: bool,
) -> list[str]:
    """Collect grade level tags by walking ancestors and capturing nodes whose role ==
    grade_level.

    Examples
    --------
    1. Single grade ancestor
        Suppose the ancestry is:

            framework -> grade_level("Grade 2") -> topic -> expectation

        Then:

            _collect_grade_levels(..., node_id=expectation_id, prefer_text_en=False)

        returns:

            ["Grade 2"]

    2. Multiple grade-level wrappers
        Suppose the ancestry contains two grade-level nodes:

            framework -> grade_level("Primary") -> grade_level("Grade 2") -> expectation

        Then the function returns:

            ["Primary", "Grade 2"]

    3. De-duplication
        Suppose the ancestry contains repeated grade labels due to wrapper duplication:

            framework -> grade_level("CE1") -> section -> grade_level("CE1") -> expectation

        Then the returned list is de-duplicated while preserving order:

            ["CE1"]

    4. No grade-level ancestors
        If no ancestor has role == "grade_level", the function returns:

            []

    Parameters
    ----------
    ctx
        The ExportContext for the CanonicalIR, providing access to node properties and
        hierarchy.
    node_id
        The ID of the canonical node for which to collect grade levels.
    parent_by_child
        Optional mapping of canonical child node ID to parent node ID to use for
        walking ancestors. If None, will use ctx.parent_by_child. This allows grade
        level collection to reflect the finalized export hierarchy after hoisting or
        reparenting.
    prefer_text_en
        If True, prefer "text_en" over "text" when extracting display text for grade
        level nodes.

    Returns
    -------
    list[str]
        A deduplicated list of grade level labels found in the node's ancestry, ordered
        from the highest level (farthest ancestor) down to the lowest level (closest
        ancestor). If no grade level nodes are encountered during the traversal, an
        empty list is returned.
    """

    ancestry = _walk_ancestors(
        ctx=ctx, node_id=node_id, parent_by_child=parent_by_child
    )
    output: list[str] = []

    for aid in ancestry:
        node = ctx.nodes_by_id.get(aid) or {}

        if node["role"] == NodeRole.GRADE_LEVEL.value:
            label = node.get("normalized_text") or node_display_text(
                node=node, prefer_text_en=prefer_text_en
            )
            label = " ".join(str(label or "").split())

            if label:
                output.append(label)

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
) -> tuple[
    dict[str, list[str]], DefaultDict[str, list[dict[str, Any]]], dict[str, Any]
]:
    """Build export-time parent-to-children mapping with aux reparenting.

    In `under_expectation` mode, expectation-anchored reparenting is applied for both
    grouping parents and the framework root so root-level sibling layouts behave the
    same as grouped sibling layouts.

    Examples
    --------
    1. Sibling-layout row in a Senegal table
        A common Senegal reading row has one expectation followed by guidance and
        descriptor leaves. For example:

            * expectation: Objectif spécifique
            * guidance: Contenus
            * descriptor: Durée

        When these appear as ordered siblings under the same exported parent,
        `_compute_export_children()` keeps the expectation in the parent’s child list,
        then attaches the following guidance/descriptor to that expectation instead of
        leaving them as sibling SFIs under the parent. For example:

        Suppose a parent grouping has ordered emitted children:

            [E1(expectation), G1(guidance), D1(descriptor), E2(expectation)]

        In `under_expectation` mode, the exporter treats `G1` and `D1` as belonging to
        the most recent preceding expectation (`E1`).

        Result:
            export_children[parent] == [E1, E2]
            aux_attach_to_expectation[E1] contains payloads for G1 and D1

        This matches table rows where "Objectif spécifique" is followed by
        "Contenus" and "Durée".

    2. Child-layout aux under an expectation
        This function also supports the case where the canonical IR already stores
        guidance/descriptor as direct children of an expectation. In that layout, when
        `_compute_export_children()` encounters the expectation in sibling order, it
         pulls its aux children from `ctx.children_by_parent[expectation]` and attaches
         them to that expectation, instead of requiring them to appear as siblings. For
         example:

         Suppose a parent grouping has ordered emitted children:

            [E1(expectation), E2(expectation)]

        and the canonical IR already stores:

            ctx.children_by_parent[E1] == [G1(guidance), D1(descriptor)]

        When the exporter visits `E1`, it pulls `G1` and `D1` from the canonical
        expectation subtree and attaches them to `E1` (either as metadata or as
        export-time children, depending on config).

        Result:
            export_children[parent] still includes E1 only once
            child-layout aux are consumed without needing sibling matching

    3. Non-aux children stay in order
        This function does NOT rewrite everything under a parent. It only intercepts
        attachable aux statements. Other emitted children stay in the parent’s exported
        order. For example:

        Suppose a parent has ordered emitted children:

            [W1(week), E1(expectation), G1(guidance), D1(descriptor), W2(week)]

        The exporter preserves non-aux children in order and only re-homes attachable
        aux statements.

        Result:
            export_children[parent] == [W1, E1, W2]
            aux_attach_to_expectation[E1] contains G1 and D1

    4. Aux before any expectation is not matched
        If a guidance/descriptor node appears before any expectation sibling has been
        seen, there is no `last_expectation` to attach it to. In that case the node is
        left in `new_kids` rather than being silently attached to the wrong
        expectation. For example:

        Suppose a parent has ordered emitted children:

            [G0(guidance), D0(descriptor), E1(expectation)]

        Because no preceding expectation exists yet, `G0` and `D0` are not attached to
        `E1`. They remain in the parent's exported child sequence at this stage.

        Result:
            export_children[parent] == [G0, D0, E1]

        This avoids incorrectly assigning leading aux statements to a later expectation.

    5. Senegal reading curriculum example
        Under a `subtopic` such as "Róofoo-gi-baat / Grammaire", a row may contain:

            - expectation: "Objectif spécifique"
            - guidance: "Contenus"
            - descriptor: "Durée"

        The exporter keeps the expectation as the exported child under the subtopic and
        attaches the "Contenus" and "Durée" leaves to that expectation, rather than
        keeping all three as sibling StandardsFrameworkItems under the subtopic.

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
    tuple[
        dict[str, list[str]], DefaultDict[str, list[dict[str, Any]]], dict[str, Any]
    ]
        A tuple containing:
            export_children: The parent -> children mapping for the export tree.
            aux_nodes_attached_to_expectation: Metadata payloads to attach to
                expectations.
            reparent_stats: Counts and orphan/attached aux node IDs.
    """

    attached_aux_node_ids: set[str] = set()
    aux_nodes_attached_to_expectation: DefaultDict[str, list[dict[str, Any]]] = (
        defaultdict(list)
    )
    export_children: dict[str, list[str]] = {}
    orphan_aux_count = 0
    orphan_aux_node_ids: set[str] = set()
    reparented_count = 0

    for parent_id, kids in ctx.children_by_parent.items():
        parent_role = (
            NodeRole.FRAMEWORK.value
            if parent_id == ctx.root_id
            else ctx.nodes_by_id[parent_id]["role"]
        )
        ordered_emitted_kids = [cid for cid in kids if emit_flag[cid]]

        if config.aux_statement_parenting == "under_expectation" and (
            parent_id == ctx.root_id
            or _is_grouping_role(config=config, role=parent_role)
        ):
            # Count aux nodes before re-parenting to detect orphans.
            aux_before = sum(
                1
                for cid in ordered_emitted_kids
                if ctx.nodes_by_id[cid]["role"] in AUX_ROLES
            )

            new_kids, child_aux_consumed = _reparent_aux_nodes_under_expectations(
                attached_aux_node_ids=attached_aux_node_ids,
                aux_nodes_attached_to_expectation=aux_nodes_attached_to_expectation,
                config=config,
                ctx=ctx,
                emit_flag=emit_flag,
                export_children=export_children,
                ordered_kids=ordered_emitted_kids,
            )

            # Aux nodes that ended up in `new_kids` had no preceding expectation
            # (orphans).
            orphan_ids_in_batch = {
                cid for cid in new_kids if ctx.nodes_by_id[cid]["role"] in AUX_ROLES
            }
            reparented_count += aux_before - len(orphan_ids_in_batch)
            reparented_count += child_aux_consumed
            orphan_aux_count += len(orphan_ids_in_batch)
            orphan_aux_node_ids.update(orphan_ids_in_batch)
        else:
            new_kids = ordered_emitted_kids

        existing = export_children.get(parent_id, [])
        merged = list(new_kids)
        merged_set = set(merged)

        for c in existing:
            if c not in merged_set:
                merged.append(c)
                merged_set.add(c)

        export_children[parent_id] = merged

    reparent_stats = {
        "aux_reparented_count": reparented_count,
        "orphan_aux_count": orphan_aux_count,
        "orphan_aux_node_ids": sorted(orphan_aux_node_ids),
        "attached_aux_node_ids": sorted(attached_aux_node_ids),
    }
    return export_children, aux_nodes_attached_to_expectation, reparent_stats


def _compute_topic_path_key(
    *,
    ctx: ExportContext,
    node_id: str,
    parent_by_child: dict[str, str] | None = None,
    prefer_text_en: bool,
    role_allowlist: set[str] | None = None,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Compute a deterministic `topic_path_key` for progression threading. If
    role_allowlist is provided, only those roles contribute. Always excludes
    grade/stage/week to allow matching across levels.

    Examples
    --------
    1. Senegal reading example with whitelisted grouping roles
        Suppose an expectation sits under the following export ancestry:

            stage:    "CE1"
            strand:   "Lecture"
            substage: "Palier 1"
            subtopic: "Róofoo-gi-baat / Grammaire"

        and `role_allowlist={"strand", "substage", "subtopic"}`.

        Then `_compute_topic_path_key()` may return:

            (
                "strand=lecture|substage=palier_1|subtopic=roofoo_gi_baat_grammaire",
                [
                    {"role": "strand", "label": "Lecture", ...},
                    {"role": "substage", "label": "Palier 1", ...},
                    {"role": "subtopic", "label": "Róofoo-gi-baat / Grammaire", ...},
                ],
            )

        Note that `stage` is excluded even though it exists in the ancestry.

    2. Excluding structural roles
        Suppose the ancestry contains:

            framework -> section -> stage -> subtopic -> expectation

        Even without a role allowlist, `framework`, `section`, and `stage` are excluded
        by default. If `subtopic` has a usable label, the returned key contains only
        the `subtopic=...` segment.

    3. No valid path parts
        Suppose all ancestors are excluded roles or have blank labels after
        normalization.

        Then the function returns:

            (None, [])

    Parameters
    ----------
    ctx
        The ExportContext for the CanonicalIR, providing access to node properties and
        hierarchy.
    node_id
        The ID of the canonical node for which to compute the topic path key.
    parent_by_child
        Optional mapping of canonical child node ID to parent node ID to use for
        walking ancestors. If None, will use ctx.parent_by_child.
    prefer_text_en
        If True, prefer "text_en" over "text" when extracting display text for nodes.
    role_allowlist
        Optional set of roles to allow in the topic path key. If None, all roles are
        allowed (except those always excluded).

    Returns
    -------
    tuple
        (topic_path_key, debug) where topic_path_key is the computed key string or None
        if no valid key could be constructed, and debug is a list of dicts with role
        and label info for each ancestor considered in the path construction (for
        testing and verification purposes).
    """

    ancestry = _walk_ancestors(
        ctx=ctx, node_id=node_id, parent_by_child=parent_by_child
    )
    debug: list[dict[str, Any]] = []
    parts: list[str] = []

    # Always excluded (structural/non-threading).
    always_exclude = {
        NodeRole.FRAMEWORK.value,
        NodeRole.UNRESOLVED.value,
        NodeRole.PROSE.value,
        NodeRole.SECTION.value,
        NodeRole.GRADE_LEVEL.value,
        NodeRole.STAGE.value,
    }

    for aid in ancestry:
        n = ctx.nodes_by_id.get(aid) or {}
        r = str(n.get("role") or "")

        if not r:
            continue

        if r in always_exclude:
            continue

        if role_allowlist is not None and r not in role_allowlist:
            continue

        label = n.get("normalized_text") or node_display_text(
            node=n, prefer_text_en=prefer_text_en
        )
        label = " ".join(str(label or "").split())

        if not label:
            continue

        parts.append(f"{r}={normalize_key_token(label=label, separator="_")}")
        debug.append({"role": r, "label": label, "canonical_node_id": aid})

    if not parts:
        return None, debug

    return "|".join(parts), debug


def _dedupe_preserve_order(ids: list[str]) -> list[str]:
    """Remove duplicate child IDs but keep the first occurrence, which is important
    because earlier hoisting/merge steps can accidentally leave repeated canonical
    child references in a parent’s child list. This function prevents duplicate
    hasChild edges and duplicate child UUIDs in `order_map` without scrambling sequence.

    Parameters
    ----------
    ids
        The list of strings to deduplicate.

    Returns
    -------
    list[str]
        A new list containing the unique strings from the input list, in the order of
        their first occurrence.
    """

    output: list[str] = []
    seen: set[str] = set()

    for item in ids:
        if item in seen:
            continue

        output.append(item)
        seen.add(item)

    return output


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
    inferred_name = (
        node_display_text(node=root_node, prefer_text_en=prefer_en) or ctx.pdf_name
    )
    name = config.framework_name or inferred_name
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
    source_entity: str = "StandardsFrameworkItem",
) -> Relationship:
    """Create a hasChild Relationship.

    NB: The Relationship schema should validate that hasChild is either
    StandardsFramework -> StandardsFrameworkItem or
    StandardsFrameworkItem -> StandardsFrameworkItem, and that both endpoints are CASE
    UUIDs.

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
    source_entity
        The entity type of the parent. Pass `"StandardsFramework"` for root-level edges
        and `"StandardsFrameworkItem"` (default) for SFI-to-SFI edges.

    Returns
    -------
    Relationship
        The constructed hasChild Relationship.
    """

    relationship_metadata = relationship_metadata or {}
    relationship_metadata.setdefault("source_kg", "academic_standards")
    return Relationship(
        attribution_statement=config.attribution_statement,
        author=config.author,
        identifier=uuid5(
            config.namespace_uuid,
            f"lc:curriculum:{doc_key}:rel:hasChild:{parent_uuid}:{child_uuid}",
        ),
        license=config.license,
        metadata=relationship_metadata,
        provider=config.provider,
        relationship_type="hasChild",
        source_entity=source_entity,
        source_entity_key="case_identifier_uuid",
        source_entity_value=str(parent_uuid),
        target_entity="StandardsFrameworkItem",
        target_entity_key="case_identifier_uuid",
        target_entity_value=str(child_uuid),
    )


def _emit_sfi(
    *,
    aux_attachments: Optional[list[dict[str, Any]]] = None,
    canonical_ir_created_at: Optional[str],
    config: CreateKGConfig,
    ctx: ExportContext,
    export_order_index: dict[tuple[str, str], int] | None = None,
    export_parent_by_child: dict[str, str] | None = None,
    fw_metadata: dict[str, Any],
    is_orphan_aux: bool = False,
    node_id: str,
) -> StandardsFrameworkItem:
    """Emit a StandardsFrameworkItem for a given canonical node.

    Examples
    --------
    1. Basic expectation emission with attached aux metadata
        Suppose steps 3 - 5 has already attached guidance and descriptor rows to an
        expectation from the Senegal reading curriculum:

            expectation: "Joxe ay ndigal / Donner des ordres"
            guidance:    "Toftalanteg kàddu yu gàtt ..."
            descriptor:  "Ayu bés 20 / Semaine 20"

        Calling `_emit_sfi()` on the expectation node produces one
        `StandardsFrameworkItem` whose:

            - `description` is the expectation text
            - `normalized_statement_type` is "Standard"
            - `metadata["aux_statements"]` contains payloads for the guidance and
                descriptor nodes
            - `metadata["progression_context"]` is populated

        The guidance and descriptor are not emitted here as separate SFIs if they
        were previously attached and suppressed.

    2. Grouping node emission
        Suppose the node is a grouping such as:

            subtopic: "Róofoo-gi-baat / Grammaire"

        Calling `_emit_sfi()` emits an SFI whose:

            - `description` is the grouping label
            - `normalized_statement_type` is "Standard Grouping"
            - `metadata["progression_context"]` is absent because the node is not an
                expectation

    3. Orphan aux emission
        Suppose a descriptor row could not be attached to any expectation earlier in
        the export pipeline.

        Calling `_emit_sfi(..., is_orphan_aux=True, ...)` on that node emits a
        standalone SFI whose metadata includes:

            metadata["orphan_aux"] = True

        This preserves the aux statement for debugging/review instead of silently
        dropping it.

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
    export_order_index
        Optional export-time (parent_id, child_id) -> order_index mapping derived from
        the finalized export tree after hoisting/reparenting. Used as the primary
        per-parent ordering signal for progression metadata.
    export_parent_by_child
        Optional mapping of canonical child node ID to parent node ID to use for
        walking ancestors when computing progression context. If None, will use
        ctx.parent_by_child.
    fw_metadata
        Pre-computed framework metadata dict (from ctx.get_framework_metadata()).
        Passed in to avoid redundant recomputation per node.
    is_orphan_aux
        Whether this node is an orphan auxiliary statement (guidance/descriptor that
        had no preceding expectation sibling during reparenting).
    node_id
        The ID of the canonical node to emit as an SFI.

    Returns
    -------
    StandardsFrameworkItem
        The constructed StandardsFrameworkItem for the given node.
    """

    node = ctx.nodes_by_id[node_id]
    prefer_en = config.description_text_policy == "prefer_text_en"

    # Language policy:
    # - default: Always use framework language
    # - source: Prefer per-node language if present, else fall back to framework
    sfi_in_language = str(fw_metadata.get("in_language") or "")

    if config.export_in_language_policy == "source":
        # Canonical IR stores language inside TextUnit dicts (title/body), not as a
        # top-level field. Check title first, then body, skipping "und" (undetermined).
        node_lang = next(
            (
                node[field]["language"].strip()
                for field in ("title", "body")
                if isinstance(node.get(field), dict)
                and node[field].get("language")
                and node[field]["language"].strip().lower() != "und"
            ),
            None,
        )

        if node_lang:
            sfi_in_language = node_lang

    path_key = ctx.compute_path_key(node_id)
    sfi_id = uuid5(config.namespace_uuid, f"lc:curriculum:{ctx.doc_key}:sfi:{path_key}")

    role = node["role"]
    desc = node_display_text(node=node, prefer_text_en=prefer_en) or (
        f"[{role or 'unknown'}:{node_id[:8]}]"
    )
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
    # context. `framework.metadata.provenance_context.bbox` should include coord_space,
    # dpi, page dims, etc.
    if bbox is not None:
        metadata["bbox_ref"] = "framework.metadata.provenance_context.bbox"

    if aux_attachments:
        metadata["aux_statements"] = aux_attachments

    if is_orphan_aux:
        metadata["orphan_aux"] = True

    # Generate progression context keys for expectation nodes (used for Learning
    # Progressions KG inference downstream).
    if role == StatementRole.EXPECTATION.value:
        grade_key = _first_ancestor_label_for_role(
            ctx=ctx,
            node_id=node_id,
            parent_by_child=export_parent_by_child,
            prefer_text_en=prefer_en,
            role=NodeRole.GRADE_LEVEL.value,
        )
        stage_key = _first_ancestor_label_for_role(
            ctx=ctx,
            node_id=node_id,
            parent_by_child=export_parent_by_child,
            prefer_text_en=prefer_en,
            role=NodeRole.STAGE.value,
        )

        # Use the grouping whitelist if present so `topic_path_key` is consistent
        # across countries/configs.
        role_allowlist = None

        if config.grouping_role_policy == "whitelist":
            role_allowlist = {r.value for r in config.grouping_roles_whitelist}

            # But never allow grade/stage/week into the path key. If we left them in
            # the topic path key, the same conceptual thread would split into separate
            # keys like stage=ce1|strand=lecture|subtopic=grammaire vs.
            # stage=ce2|strand=lecture|subtopic=grammaire, which defeats the point of a
            # reusable cross-level thread key. The level signal is already preserved
            # elsewhere, so excluding it from `topic_path_key` avoids double encoding
            # and fragmentation.
            role_allowlist -= {
                NodeRole.GRADE_LEVEL.value,
                NodeRole.STAGE.value,
                NodeRole.WEEK.value,
            }

        topic_path_key, topic_path_parts = _compute_topic_path_key(
            ctx=ctx,
            node_id=node_id,
            parent_by_child=export_parent_by_child,
            prefer_text_en=prefer_en,
            role_allowlist=role_allowlist,
        )

        parent_lookup = export_parent_by_child or ctx.parent_by_child
        order_lookup = export_order_index or ctx.edge_order_index
        parent_id = parent_lookup.get(node_id)
        canonical_order_index_within_parent = (
            ctx.edge_order_index.get((parent_id, node_id)) if parent_id else None
        )
        order_index_within_parent = (
            order_lookup.get((parent_id, node_id)) if parent_id else None
        )
        canon_order_path = _walk_ancestors(
            ctx=ctx, node_id=node_id, parent_by_child=parent_lookup
        )

        # Code retrieval (should work across countries/canonicalizers).
        grade_low, grade_high = _parse_ordinal(grade_key) if grade_key else (None, None)
        stage_low, stage_high = _parse_ordinal(stage_key) if stage_key else (None, None)
        code_raw = node.get("local_code") or ""
        code_features = _parse_code_features(
            code=str(code_raw), grade_ordinal_low=grade_low
        )

        metadata["progression_context"] = {
            "grade_key": grade_key,
            "grade_ordinal_low": grade_low,
            "grade_ordinal_high": grade_high,
            "stage_key": stage_key,
            "stage_ordinal_low": stage_low,
            "stage_ordinal_high": stage_high,
            "thread_key": _normalize_thread_key(topic_path_key=topic_path_key),
            "topic_path_key": topic_path_key,
            "topic_path_parts": topic_path_parts,  # For debugging
            "canon_order_path": canon_order_path,
            "canonical_order_index_within_parent": canonical_order_index_within_parent,
            "order_index_within_parent": order_index_within_parent,
            **code_features,
        }

    return StandardsFrameworkItem(
        academic_subject=fw_metadata["academic_subject_default"],
        attribution_statement=config.attribution_statement,
        author=config.author,
        case_identifier_uri=f"{config.case_uri_base}{sfi_id}",
        case_identifier_uuid=sfi_id,
        date_created=canonical_ir_created_at,
        date_modified=None,
        description=desc,
        grade_level=_collect_grade_levels(
            ctx=ctx,
            node_id=node_id,
            parent_by_child=export_parent_by_child,
            prefer_text_en=prefer_en,
        ),
        identifier=sfi_id,
        in_language=sfi_in_language,
        jurisdiction=fw_metadata["jurisdiction"],
        license=config.license,
        metadata=metadata,
        normalized_statement_type=_normalized_statement_type(config=config, role=role),
        notes=None,
        provider=config.provider,
        statement_code=node.get("local_code"),
        statement_type=(node.get("source_label") or role or None),
    )


def _emit_sfis(
    *,
    aux_nodes_attached_to_expectation: DefaultDict[str, list[dict[str, Any]]],
    canonical_created_at_iso: Optional[str],
    config: CreateKGConfig,
    ctx: ExportContext,
    emit_flag: dict[str, bool],
    export_order_index: dict[tuple[str, str], int] | None = None,
    export_parent_by_child: dict[str, str] | None = None,
    orphan_aux_node_ids: set[str] | None = None,
) -> dict[str, StandardsFrameworkItem]:
    """Emit StandardsFrameworkItems for all flagged nodes.

    Parameters
    ----------
    aux_nodes_attached_to_expectation
        Metadata attachments for expectation nodes.
    canonical_created_at_iso
        The ISO-8601 creation datetime.
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.
    emit_flag
        Node-level emit flags.
    export_order_index
        Optional export-time (parent_id, child_id) -> order_index mapping derived from
        the finalized export tree after hoisting/reparenting.
    export_parent_by_child
        Optional mapping of canonical child node ID to parent node ID to use for
        walking ancestors when computing progression context. If None, will use
        ctx.parent_by_child.
    orphan_aux_node_ids
        Set of canonical node IDs for aux statements that had no preceding expectation
        sibling (orphans). If provided, their emitted SFIs will carry
        `metadata.orphan_aux = True`.

    Returns
    -------
    dict[str, StandardsFrameworkItem]
        Mapping of canonical node ID to emitted SFI.
    """

    fw_metadata = ctx.get_framework_metadata()  # Compute once for all SFIs
    sfi_by_node: dict[str, StandardsFrameworkItem] = {}
    _orphans = orphan_aux_node_ids or set()

    for node_id, should_emit in emit_flag.items():
        if not should_emit:
            continue

        sfi_by_node[node_id] = _emit_sfi(
            aux_attachments=aux_nodes_attached_to_expectation.get(node_id),
            canonical_ir_created_at=canonical_created_at_iso,
            config=config,
            ctx=ctx,
            export_order_index=export_order_index,
            export_parent_by_child=export_parent_by_child,
            fw_metadata=fw_metadata,
            is_orphan_aux=node_id in _orphans,
            node_id=node_id,
        )

    return sfi_by_node


def _find_surviving_ancestor_and_anchor(
    ctx: ExportContext, dropped_pid: str, emit_flag: dict[str, bool]
) -> tuple[str, str | None]:
    """Find the nearest emitted ancestor for a dropped parent and the anchor child on
    the dropped path used for ordering.

    Parameters
    ----------
    ctx
        The ExportContext for the CanonicalIR, providing access to node properties and
        hierarchy.
    dropped_pid
        The dropped parent ID for which to find the surviving ancestor and anchor child.
    emit_flag
        Dictionary mapping node IDs to boolean emit flags indicating which nodes are
        emitted.

    Returns
    -------
    tuple[str, str | None]
        A tuple of (surviving_ancestor_id, anchor_child_id). The surviving ancestor is
        the closest emitted ancestor of the dropped parent. The anchor child is the
        child of the surviving ancestor that is on the path to the dropped parent and
        has canonical order metadata used for determining where to insert hoisted
        children.
    """

    cur: str | None = ctx.parent_by_child.get(dropped_pid)
    seen: set[str] = set()
    anchor_child: str | None = dropped_pid

    while cur and cur != ctx.root_id and not emit_flag.get(cur) and cur not in seen:
        seen.add(cur)
        anchor_child = cur
        cur = ctx.parent_by_child.get(cur)

    surviving = cur if cur and cur not in seen else ctx.root_id
    return surviving, anchor_child


def _first_ancestor_label_for_role(
    *,
    ctx: ExportContext,
    node_id: str,
    parent_by_child: dict[str, str] | None = None,
    prefer_text_en: bool,
    role: str,
) -> str | None:
    """Find the closest ancestor (including self) with a given role and return its
    label. This is how an expectation gets labeled with the nearest enclosing
    stage-like wrapper, such as a palier/stage node (if one exists in the export
    hierarchy).

    Examples
    --------
    1. Closest stage ancestor
        Suppose the export ancestry for an expectation is:

            framework
              -> stage("CE1")
              -> substage("Palier 3")
              -> subtopic("Róofoo-gi-baat / Grammaire")
              -> expectation("Joxe ay ndigal / Donner des ordres")

        Calling:

            _first_ancestor_label_for_role(..., role="stage")

        returns:

            "ce1"  # or the node display text if `normalized_text` is unavailable

    2. Includes self
        Suppose `node_id` itself points to a node whose role is `"stage"`.

        Calling:

            _first_ancestor_label_for_role(..., role="stage")

        returns that node's own label, because the search includes self before
        walking upward.

    3. No matching ancestor
        Suppose a Senegal expectation has no `grade_level` node anywhere above it.

        Calling:

            _first_ancestor_label_for_role(..., role="grade_level")

        returns:

            None

    Parameters
    ----------
    ctx
        The ExportContext for the CanonicalIR, providing access to node properties and
        hierarchy.
    node_id
        The ID of the canonical node for which to find the ancestor.
    parent_by_child
        Optional mapping of canonical child node ID to parent node ID to use for
        walking ancestors. If None, will use ctx.parent_by_child.
    prefer_text_en
        If True, prefer "text_en" over "text" when extracting display text for the
        ancestor node.
    role
        The role string to match in ancestors.

    Returns
    -------
    str | None
        The label of the closest ancestor with the given role, or None if no such
        ancestor is found. The label is normalized by collapsing whitespace. If the
        ancestor node has no displayable text, returns None.
    """

    parent_lookup = parent_by_child or ctx.parent_by_child
    cur: str | None = node_id
    seen: set[str] = set()

    while cur and cur != ctx.root_id and cur not in seen:
        seen.add(cur)
        node = ctx.nodes_by_id.get(cur) or {}

        if node["role"] == role:
            label = node.get("normalized_text") or node_display_text(
                node=node, prefer_text_en=prefer_text_en
            )
            label = " ".join(str(label or "").split())
            return label or None

        cur = parent_lookup.get(cur)

    return None


def _handle_empty_grouping_pruning(
    *,
    config: CreateKGConfig,
    ctx: ExportContext,
    drop_reasons: dict[str, str],
    emit_flag: dict[str, bool],
    export_children: dict[str, list[str]],
) -> set[str]:
    """Optionally prune empty grouping nodes after hoisting and record drop reasons.

    This step assumes that `_reattach_children_of_dropped_nodes()` has already been
    called, so children of dropped mid-hierarchy nodes have been hoisted to their
    nearest surviving ancestor. Pruning then removes only grouping nodes that are still
    genuinely empty in the **export** tree.

    Nodes with `preserve_if_empty=True` are exempt from pruning even if they have zero
    emitted children. This is useful for intentionally retained structural wrappers in
    the canonical IR (e.g., a section heading that should survive as a visible export
    node even when its descendants were filtered out).

    Examples
    --------
    1. Simple empty grouping after aux suppression
        Suppose earlier steps have already attached and suppressed aux nodes, leaving:

            export_children = {
                ROOT: [S1],
                S1: [T1],
                T1: [],
            }

        and:

            emit_flag[S1] = True   # grouping (e.g., substage)
            emit_flag[T1] = True   # grouping (e.g., subtopic)

        Because `T1` is a grouping with zero emitted children, pruning removes it.
        After `T1` is removed, `S1` may also become empty and will be pruned on the
        next iteration if it then has no emitted children.

        Result:
            - empty grouping shells do not survive as standalone SFIs
            - the final hierarchy contains only groupings that still organize emitted
              descendants

    2. Senegal reading curriculum example
        In Senegal reading, a whitelisted grouping such as:

            substage: "Palier 2 — Production d'écrits"

        may contain several `subtopic` children such as:

            subtopic: "Róofoo-gi-baat / Grammaire"

        If one such `subtopic` loses all emitted descendants after earlier filtering,
        attachment, and subtree suppression, this step removes that empty `subtopic`.
        If the `substage` still contains other emitted expectations or non-empty
        subtopics, it remains. If it becomes empty too, it is pruned on a later pass.

    3. preserve_if_empty keeps an intentional wrapper
        Suppose a canonical grouping node has:

            preserve_if_empty = True

        and it ends up with zero emitted children after earlier export-time cleanup.
        This wrapper is kept in the export tree and is not added to `drop_reasons`,
        because the canonical IR explicitly requested that it survive even when empty.

    4. Why this runs after step 7
        Suppose a dropped non-grouping parent was hoisted away in step 7 so that its
        emitted children now sit directly under a surviving grouping parent. Only after
        that hoisting is complete can pruning correctly decide whether the grouping is
        truly empty. Running pruning earlier could remove a grouping before its rescued
        children were reattached.

    Parameters
    ----------
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.
    drop_reasons
        Dictionary mapping node IDs to reasons they were dropped.
    emit_flag
        Dictionary mapping node IDs to boolean emit flags.
    export_children
        Dictionary mapping parent node IDs to lists of child node IDs.

    Returns
    -------
    set[str]
        A set of node IDs that were pruned.
    """

    pruned_node_ids: set[str] = set()

    if config.prune_empty_groupings:
        pruned_node_ids = _prune_empty_groupings(
            config=config, ctx=ctx, emit_flag=emit_flag, export_children=export_children
        )

        for nid in pruned_node_ids:
            drop_reasons[nid] = "dropped:pruned_empty_grouping"

    return pruned_node_ids


def _is_already_attached(
    *,
    aux_node_id: str,
    aux_nodes_attached_to_expectation: DefaultDict[str, list[dict[str, Any]]],
    expectation_id: str,
) -> bool:
    """Checks if an auxiliary node is already attached to an expectation.

    Parameters
    ----------
    aux_node_id
        The canonical node ID of the auxiliary node.
    aux_nodes_attached_to_expectation
        Mutable mapping collecting metadata attachments for expectation nodes.
    expectation_id
        The ID of the expectation node.

    Returns
    -------
    bool
        True if the auxiliary node is already attached, False otherwise.
    """

    return any(
        p["canonical_node_id"] == aux_node_id
        for p in aux_nodes_attached_to_expectation[expectation_id]
    )


def _is_attachable(*, config: CreateKGConfig, role: str) -> bool:
    """Determines whether a statement role is configured to be attached.

    Parameters
    ----------
    config
        The CreateKGConfig containing handling policies.
    role
        The statement role to check.

    Returns
    -------
    bool
        True if the role should be attached to metadata, False otherwise.
    """

    return (
        role == StatementRole.GUIDANCE.value
        and config.guidance_handling == "attach_to_expectation_metadata"
    ) or (
        role == StatementRole.DESCRIPTOR.value
        and config.descriptor_handling == "attach_to_expectation_metadata"
    )


def _is_grouping_role(*, config: CreateKGConfig, role: str) -> bool:
    """Determine if a role should be treated as a grouping node in standards export.

    Statement roles (expectation/descriptor/guidance) and the synthetic framework role
    are never groupings. When `grouping_role_policy="loose"`, every other role is
    treated as a grouping. When `grouping_role_policy="whitelist"`, only roles in
    `grouping_roles_whitelist` count as groupings.

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

    if role == NodeRole.FRAMEWORK.value or role in STATEMENT_ROLE_VALUES:
        return False

    if config.grouping_role_policy == "loose":
        return True

    allowed = {r.value for r in config.grouping_roles_whitelist}
    return role in allowed


def _normalized_statement_type(*, config: CreateKGConfig, role: str) -> str:
    """Normalize a node role to a statement type for
    StandardsFrameworkItem.normalized_statement_type.

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


def _normalize_thread_key(topic_path_key: str | None) -> str | None:
    """Normalize a topic_path_key into a cross-level "thread" key.

    Many curricula number topics/subtopics in their labels (e.g., "1.1 Exploring My
    World", "2.5 Weather"). topic_path_key intentionally *excludes* grade/stage/week
    roles so that it can be used for threading, but those numeric prefixes may still be
    embedded in the keyified label itself (e.g., `topic=1_1_exploring_my_world`).

    This normalization strips leading numeric-underscore prefixes from each segment's
    value (e.g., `1_1_`), producing a more stable thread key across levels.

    NB:

    1. This is *not* country-specific; it targets a common numbering pattern.
    2. If a segment value becomes empty after stripping (rare), it falls back to the
      original value.

    Examples
    --------
    1. Stripping numeric topic prefixes
        _normalize_thread_key(
            "topic=1_1_exploring_my_world|subtopic=2_5_weather"
        ) == "topic=exploring_my_world|subtopic=weather"

    2. Keeping non-numbered segments unchanged
        _normalize_thread_key(
            "strand=lecture|subtopic=roofoo_gi_baat_grammaire"
        ) == "strand=lecture|subtopic=roofoo_gi_baat_grammaire"

    3. Ignoring malformed segments
        _normalize_thread_key(
            "strand=lecture|badsegment|subtopic=1_2_grammar"
        ) == "strand=lecture|subtopic=grammar"

    4. Empty input
        _normalize_thread_key(None) is None

    Parameters
    ----------
    topic_path_key
        The original topic_path_key to normalize.

    Returns
    -------
    str | None
        The normalized thread key, or None if the input key is None or results in no
        valid segments after normalization.
    """

    if not topic_path_key:
        return None

    out_parts: list[str] = []

    for seg in str(topic_path_key).split("|"):
        if "=" not in seg:
            continue

        role, value = seg.split("=", 1)
        v = str(value)
        v_norm = re.sub(r"^(?:\d+_)+", "", v)
        v_norm = v_norm if v_norm else v
        out_parts.append(f"{role}={v_norm}")

    return "|".join(out_parts) if out_parts else None


def _parse_code_features(*, code: str, grade_ordinal_low: int | None) -> dict[str, Any]:
    """Parse a local code into deterministic features for progression inference.

    Supports:
      - numeric codes with dots: 3.9.4.1
      - mixed codes: M3-1a/ENG.P1.02
      - roman segments: VI.2.1

    Examples
    --------
    1. Numeric dotted code
        _parse_code_features(code="3.9.4.1", grade_ordinal_low=3) == {
            "code": "3.9.4.1",
            "code_segments": ["3", "9", "4", "1"],
            "code_tuple": [3, 9, 4, 1],
            "code_stem": "3.9.4",
            "code_ordinal": "1",
            "code_stem_without_grade": "9.4",
        }

    2. Mixed alphanumeric code
        _parse_code_features(code="ENG.P1.02", grade_ordinal_low=None) == {
            "code": "ENG.P1.02",
            "code_segments": ["ENG", "P1", "02"],
            "code_tuple": ["ENG", "P1", 2],
            "code_stem": "ENG.P1",
            "code_ordinal": "02",
        }

    3. Roman numeral prefix
        _parse_code_features(code="VI.2.1", grade_ordinal_low=6) == {
            "code": "VI.2.1",
            "code_segments": ["VI", "2", "1"],
            "code_tuple": [6, 2, 1],
            "code_stem": "VI.2",
            "code_ordinal": "1",
            "code_stem_without_grade": "2",
        }

    4. Empty code
        _parse_code_features(code="", grade_ordinal_low=None) == {}

    Parameters
    ----------
    code
        The local code string to parse.
    grade_ordinal_low
        Optional lower bound for grade ordinals to strip from the code stem (e.g., 1
        for "Grade 1", 0 for "Kindergarten"). If provided, and if the first code
        segment matches this grade ordinal (either as a digit or roman numeral), it
        will be stripped from the `code_stem_without_grade` feature.

    Returns
    -------
    dict[str, Any]
        A dictionary of parsed code features.
    """

    output: dict[str, Any] = {}
    code = " ".join(str(code or "").strip().split())

    if not code:
        return output

    # Split on common separators; keep alphanum segments.
    segs = [s for s in re.split(r"[.\-_/\\\s]+", code) if s]

    if not segs:
        return output

    tup: list[int | str] = [_to_int_or_roman(s) for s in segs]

    output["code"] = code
    output["code_segments"] = segs
    output["code_tuple"] = tup

    if len(segs) >= 2:
        output["code_stem"] = ".".join(segs[:-1])
        output["code_ordinal"] = segs[-1]

    # If first segment matches grade_ordinal_low (numeric or roman), store stem without
    # that prefix too.
    if grade_ordinal_low is not None and segs:
        first_val = _to_int_or_roman(segs[0])

        if (
            isinstance(first_val, int)
            and first_val == grade_ordinal_low
            and len(segs) >= 3
        ):
            output["code_stem_without_grade"] = ".".join(segs[1:-1])

    return output


def _parse_ordinal(label: str) -> tuple[int | None, int | None]:
    """Parse the primary/lower ordinal from a grade/stage label.

    Handles:
      - digits: "Grade 3" -> 3, "I–II" -> 1, "Std III-VI" -> 3
      - embedded roman numerals: "Std VI" -> 6, "Standard III–VI" -> 3

    Examples
    --------
    1. Single numeric level
        _parse_ordinal("Grade 3") == (3, 3)

    2. Numeric range with dash
        _parse_ordinal("Semaines 10-17") == (10, 17)

    3. Roman numeral range
        _parse_ordinal("Std III–VI") == (3, 6)

    4. Embedded digit inside a label
        _parse_ordinal("CE1") == (1, 1)

    5. No ordinal information
        _parse_ordinal("Communication écrite") == (None, None)

    Parameters
    ----------
    label
        The input label string to parse.

    Returns
    -------
    tuple[int | None, int | None]
        A tuple of (ordinal_low, ordinal_high). If only one ordinal is found, both
        values will be the same. If no ordinals are found, both values will be None.
    """

    if not label:
        return None, None

    s = " ".join(str(label).strip().split())

    # Normalize dash variants to hyphen so range parsing works.
    s_norm = s.replace("–", "-").replace("—", "-").replace("−", "-")

    # Prefer digits if present.
    nums = [int(x) for x in re.findall(r"(\d+)", s_norm)]

    if nums:
        return (
            (min(nums[0], nums[1]), max(nums[0], nums[1]))
            if len(nums) >= 2
            else (nums[0], nums[0])
        )

    # Otherwise try roman numerals anywhere in the string.
    romans = [ROMAN_MAP.get(m.group(1).upper()) for m in ROMAN_RE.finditer(s_norm)]
    romans_int: list[int] = [r for r in romans if r is not None]

    if romans_int:
        return (
            (min(romans_int), max(romans_int))
            if len(romans_int) >= 2
            else (romans_int[0], romans_int[0])
        )

    return None, None


def _preserve_if_empty(*, ctx: ExportContext, nid: str) -> bool:
    """Return whether a canonical node requests empty-wrapper preservation.

    Parameters
    ----------
    ctx
        The ExportContext for the CanonicalIR, providing access to node properties.
    nid
        The canonical node ID.

    Returns
    -------
    bool
        True if the node carries `preserve_if_empty=True`, else False.
    """

    return bool((ctx.nodes_by_id.get(nid) or {}).get("preserve_if_empty", False))


def _process_sibling_group(
    *,
    attached_aux_node_ids: set[str],
    aux_nodes_attached_to_expectation: DefaultDict[str, list[dict[str, Any]]],
    config: CreateKGConfig,
    ctx: ExportContext,
    emit_flag: dict[str, bool],
    kids: list[str],
    orphan_aux_node_ids: set[str],
    prefer_en: bool,
) -> tuple[int, int]:
    """Processes a single group of sibling nodes, attaching aux nodes to expectations.

    Parameters
    ----------
    attached_aux_node_ids
        Mutable set collecting canonical node IDs successfully attached.
    aux_nodes_attached_to_expectation
        Mutable mapping collecting metadata attachments for expectation nodes.
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.
    emit_flag
        Node-level emit flags.
    kids
        The list of child node IDs to process in sibling order.
    orphan_aux_node_ids
        Mutable set collecting aux nodes that had no owning expectation.
    prefer_en
        Whether to prefer English text for payloads.

    Returns
    -------
    tuple[int, int]
        A tuple of (attached_count, orphan_count) for this specific group.
    """

    attached_count = 0
    last_expectation: Optional[str] = None
    orphan_count = 0

    for cid in kids:
        if not emit_flag.get(cid, False):
            continue

        role = (ctx.nodes_by_id.get(cid) or {})["role"]

        if role == StatementRole.EXPECTATION.value:
            last_expectation = cid
            continue

        if role not in AUX_ROLES:
            continue

        # Guard: If no expectation precedes this aux node, it is an orphan.
        if not last_expectation:
            if cid not in orphan_aux_node_ids:
                orphan_aux_node_ids.add(cid)
                orphan_count += 1

            continue

        # Guard: Check if the role is configured to be attachable.
        if not _is_attachable(config=config, role=role):
            continue

        if cid not in attached_aux_node_ids:
            attached_aux_node_ids.add(cid)

        # Guard: Skip if it's already attached to prevent duplicates.
        if _is_already_attached(
            aux_node_id=cid,
            aux_nodes_attached_to_expectation=aux_nodes_attached_to_expectation,
            expectation_id=last_expectation,
        ):
            continue

        # If all guards pass, attach the payload.
        aux_nodes_attached_to_expectation[last_expectation].append(
            _build_aux_payload(aux_node_id=cid, ctx=ctx, prefer_en=prefer_en)
        )
        attached_count += 1

    return attached_count, orphan_count


def _prune_empty_groupings(
    *,
    config: CreateKGConfig,
    ctx: ExportContext,
    emit_flag: dict[str, bool],
    export_children: dict[str, list[str]],
) -> set[str]:
    """Iteratively prune empty grouping nodes to a fixpoint.

    Only grouping nodes are eligible. A grouping is pruned when all of the following
    are true:

    1. It is still emitted,
    2. It has zero **emitted** children in the current export tree, and
    3. Its canonical node does not request `preserve_if_empty=True`.

    This function mutates both `emit_flag` and `export_children` in place. Parent-child
    lists are cleaned immediately when a grouping is pruned, and then globally scrubbed
    against the final emitted set at the end of pruning so no stale child references
    survive in the final export tree.

    Examples
    --------
    1. Single empty grouping
        Suppose:

            export_children = {
                ROOT: [A],
                A: [B],
                B: [],
            }

        and:

            emit_flag[A] = True   # grouping
            emit_flag[B] = True   # grouping

        `B` has no emitted children, so it is pruned first. If that leaves `A` with no
        emitted children, `A` is pruned on the next iteration.

        Final result:
            export_children = {ROOT: []}
            emit_flag[A] = False
            emit_flag[B] = False

    2. Cascading prune to a fixpoint
        Suppose:

            export_children = {
                ROOT: [Stage1],
                Stage1: [Subtopic1],
                Subtopic1: [],
            }

        where both `Stage1` and `Subtopic1` are grouping roles and both are currently
        emitted.

        First iteration:
            - `Subtopic1` is pruned because it has zero emitted children.

        Second iteration:
            - `Stage1` is now empty, so it is pruned too.

        The loop continues until no new empty grouping nodes remain.

    3. Senegal reading curriculum: keeping non-empty wrappers
        Suppose a Senegal `subtopic` like:

            "Róofoo-gi-baat / Grammaire"

        still contains one emitted expectation after aux statements (`Contenus`,
        `Durée`) were attached and suppressed.

            export_children[subtopic_id] == [expectation_id]

        Because that grouping still has at least one emitted child, it is **not**
        pruned. This step only removes grouping wrappers that have become structurally
        empty.

    4. preserve_if_empty overrides empty pruning
        Suppose a canonical grouping node has:

            preserve_if_empty = True

        and currently:

            export_children[preserved_id] == []
            emit_flag[preserved_id] = True

        Even though it is empty, it is kept in the export tree because the canonical IR
        explicitly requested preservation.

    5. Stale references are scrubbed away
        If a grouping is pruned after earlier hoisting changed export-time parentage,
        this function removes that grouping from every remaining parent child-list
        immediately. It then performs one final global scrub:

            export_children[parent] = [c for c in kids if c in emitted]

        so no non-emitted child references survive in the finalized export tree.

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

    Returns
    -------
    set[str]
        The set of canonical node IDs that were pruned.
    """

    changed = True
    emitted: set[str] = {nid for nid, ok in emit_flag.items() if ok}
    all_pruned: set[str] = set()

    while changed:
        changed = False
        to_prune: list[str] = []

        for nid in list(emitted):
            role = ctx.nodes_by_id[nid]["role"]

            if not _is_grouping_role(config=config, role=role) or _preserve_if_empty(
                ctx=ctx, nid=nid
            ):
                continue

            live_children = [c for c in export_children.get(nid, []) if c in emitted]

            if len(live_children) == 0:
                to_prune.append(nid)

        if to_prune:
            changed = True
            all_pruned.update(to_prune)

            for nid in to_prune:
                emitted.discard(nid)
                export_children.pop(nid, None)
                _remove_child_everywhere(child_id=nid, export_children=export_children)

    # Drop children that are no longer emitted.
    for pid, kids in list(export_children.items()):
        export_children[pid] = [c for c in kids if c in emitted]

    # Reflect pruning back into `emit_flag`.
    for nid in list(emit_flag.keys()):
        emit_flag[nid] = nid in emitted

    return all_pruned


def _reattach_children_of_dropped_nodes(
    *,
    ctx: ExportContext,
    emit_flag: dict[str, bool],
    export_children: dict[str, list[str]],
) -> dict[str, int]:
    """Re-attach emitted children of dropped (non-emitted) parents to the nearest
    surviving emitted ancestor in the export hierarchy.

    When this function is called (step 7), `emit_flag` indicates whether each canonical
    node will still be emitted and `export_children` contains the current export-time
    parent -> child map. This function is not trying to rescue dropped children. It
    only hoists children that are still emitted but are currently hanging under a
    dropped parent.

    Why this is needed
    ------------------
    If a dropped mid-hierarchy parent remains in `export_children`, then
    `_build_relationships_and_order()` later skips that parent because it has no
    emitted SFI. Any emitted descendants still reachable only through that dropped
    parent would otherwise become disconnected from the exported framework tree.

    The function therefore collapses dropped structural parents out of the export tree
    while preserving connectivity. For example:

        Root
            - A (emitted)
                - B (dropped)
                    - X (emitted)
                    - Y (emitted)

    becomes:

        Root
            - A (emitted)
                - X (emitted)
                - Y (emitted)

    Importantly, this function now also removes stale references to the dropped parent
    from every remaining parent child-list immediately, rather than relying on later
    pruning/filtering to clean those up.

    Ordering policy
    ---------------
    1. Prefer the canonical order slot of the closest dropped node on the path between
        the surviving ancestor and the dropped parent.
    2. If that anchor edge has no canonical order metadata, append the hoisted
        children at the end of the surviving ancestor's child list.
    3. Process dropped parents deepest-first so nested dropped chains collapse safely.

    Parameters
    ----------
    ctx
        The ExportContext for the CanonicalIR, providing access to canonical
        parent-child mappings.
    emit_flag
        Node-level emit flags (True if the node will be emitted as an SFI).
    export_children
        Parent-to-children mapping (mutated in place).

    Returns
    -------
    dict[str, int]
        - `reattached_children_count`: number of children newly inserted under a
            surviving ancestor.
        - `dropped_parents_processed`: number of dropped parents examined that had at
            least one emitted child eligible for hoisting.
        - `dropped_parents_cleaned`: number of dropped parents removed from at least
            one surviving parent child-list.
    """

    dropped_parents = [
        pid
        for pid in list(export_children)
        if pid != ctx.root_id and not emit_flag.get(pid, False)
    ]

    if not dropped_parents:
        return {
            "reattached_children_count": 0,
            "dropped_parents_processed": 0,
            "dropped_parents_cleaned": 0,
        }

    def _depth(nid: str) -> int:
        """Calculate depth of a node in the original hierarchy for sorting.

        Parameters
        ----------
        nid
            The node ID for which to calculate depth.

        Returns
        -------
        int
            The depth of the node in the original hierarchy, where root-level nodes
            have depth 0, their children have depth 1, and so on.
        """

        cur: str | None = nid
        d = 0
        seen: set[str] = set()

        while cur and cur != ctx.root_id and cur not in seen:
            seen.add(cur)
            d += 1
            cur = ctx.parent_by_child.get(cur)

        return d

    dropped_parents.sort(key=_depth, reverse=True)
    reattached_count = 0
    processed_count = 0
    cleaned_count = 0

    for dropped_pid in dropped_parents:
        # Remove stale references to the dropped parent everywhere first.
        cleaned_count += _remove_child_everywhere(
            child_id=dropped_pid, export_children=export_children
        )

        # Detach the dropped parent from the export tree entirely.
        children_to_hoist = export_children.pop(dropped_pid, [])

        if not children_to_hoist:
            continue

        # Only hoist children that are still emitted.
        emitted_children_to_hoist = [
            cid for cid in children_to_hoist if emit_flag.get(cid, False)
        ]

        if not emitted_children_to_hoist:
            continue

        processed_count += 1
        surviving, anchor_child = _find_surviving_ancestor_and_anchor(
            ctx=ctx, dropped_pid=dropped_pid, emit_flag=emit_flag
        )
        target_kids = export_children.setdefault(surviving, [])
        target_set = set(target_kids)
        canonical_order = (
            ctx.edge_order_index.get((surviving, anchor_child))
            if anchor_child is not None
            else None
        )
        insert_at: int | None = None

        # If canonical order exists, insert the hoisted children before the first
        # existing target child whose canonical order is greater than the anchor order.
        # Otherwise, append at the end.
        if canonical_order is not None:
            insert_at = next(
                (
                    i
                    for i, cid in enumerate(target_kids)
                    if (so := ctx.edge_order_index.get((surviving, cid))) is not None
                    and so > canonical_order
                ),
                None,
            )

        new_children = [c for c in emitted_children_to_hoist if c not in target_set]

        if insert_at is not None:
            target_kids[insert_at:insert_at] = new_children
        else:
            target_kids.extend(new_children)

        reattached_count += len(new_children)

    return {
        "reattached_children_count": reattached_count,
        "dropped_parents_processed": processed_count,
        "dropped_parents_cleaned": cleaned_count,
    }


def _remove_child_everywhere(
    *, child_id: str, export_children: dict[str, list[str]]
) -> int:
    """Remove a child reference from every export parent list.

    Parameters
    ----------
    child_id
        The child node ID to remove from all parent child-lists.
    export_children
        Parent-to-children mapping (mutated in place).

    Returns
    -------
    int
        Number of parent child-lists that were modified.
    """

    cleaned = 0

    for pid, kids in list(export_children.items()):
        if child_id not in kids:
            continue

        new_kids = [c for c in kids if c != child_id]

        if len(new_kids) != len(kids):
            export_children[pid] = new_kids
            cleaned += 1

    return cleaned


def _reparent_aux_nodes_under_expectations(
    *,
    attached_aux_node_ids: set[str],
    aux_nodes_attached_to_expectation: DefaultDict[str, list[dict[str, Any]]],
    config: CreateKGConfig,
    ctx: ExportContext,
    emit_flag: dict[str, bool],
    export_children: dict[str, list[str]],
    ordered_kids: list[str],
) -> tuple[list[str], int]:
    """Re-parent aux nodes under their owning expectation.

    Walks the ordered children of a grouping node, attaching aux nodes either to
    expectation metadata or as export-time children of the owning expectation.

    Supports two canonical IR layouts:

    1. **Sibling layout**: guidance/descriptor nodes are siblings of expectation nodes
        under a shared grouping parent. Aux nodes are matched to the immediately
        preceding expectation in sibling order.
    2. **Child layout**: guidance/descriptor nodes are direct children of their owning
        expectation in the canonical IR tree. These are discovered by inspecting
        `ctx.children_by_parent` for each expectation encountered.

    Both layouts may coexist within the same IR; the function handles them in a single
    pass.

    Examples
    --------
    1. Sibling layout: attach aux nodes to the most recent preceding expectation
        Suppose `ordered_kids` under a grouping parent are:

            [E1(expectation), G1(guidance), D1(descriptor), E2(expectation)]

        and the config requests:

            guidance_handling = "attach_to_expectation_metadata"
            descriptor_handling = "attach_to_expectation_metadata"

        Then the function keeps only the expectations in `new_kids`:

            new_kids == [E1, E2]

        and records metadata attachments for E1:

            aux_nodes_attached_to_expectation[E1] == [payload(G1), payload(D1)]

        because `G1` and `D1` are matched to the most recent preceding expectation
        in sibling order.

    2. Child layout: get aux children already stored under an expectation
        Suppose `ordered_kids` are:

            [E1(expectation), E2(expectation)]

        and the canonical IR already stores:

            ctx.children_by_parent[E1] == [G1(guidance), D1(descriptor)]

        When this function visits E1, it get `G1` and `D1` from the canonical
        expectation subtree and attaches them to E1.

        Result:
            new_kids == [E1, E2]
            child_aux_consumed_count == 2

        This avoids requiring those aux nodes to also appear as siblings.

    3. Leading aux nodes remain in the parent output if no expectation has appeared yet
        Suppose `ordered_kids` are:

            [G0(guidance), D0(descriptor), E1(expectation)]

        Because no preceding expectation exists when `G0` and `D0` are encountered,
        they are not attached to E1. They remain in `new_kids`:

            new_kids == [G0, D0, E1]

        This prevents incorrectly assigning leading aux nodes to a later expectation.

    4. Mixed layout in one pass
        Suppose `ordered_kids` are:

            [E1(expectation), G1(guidance), E2(expectation)]

        and also:

            ctx.children_by_parent[E2] == [D2(descriptor)]

        Then the function handles both layouts together:

            new_kids == [E1, E2]

        with:
            G1 attached to E1 by sibling order
            D2 attached to E2 by child harvesting

        This is why the function returns both:
            - `new_kids` for the parent's filtered child list
            - `child_aux_consumed_count` for aux nodes harvested from child layout

    5. Senegal reading row example
        In the Senegal reading curriculum, a row often contains:

            - expectation: "Objectif spécifique"
            - guidance: "Contenus"
            - descriptor: "Durée"

        When these appear in sibling order, the function keeps the expectation in the
        exported child sequence and attaches the "Contenus" and "Durée" nodes to that
        expectation, rather than keeping all three as sibling SFIs under the same
        parent.

    Parameters
    ----------
    attached_aux_node_ids
        Mutable set collecting canonical node IDs successfully attached.
    aux_nodes_attached_to_expectation
        Mutable mapping collecting metadata attachments for expectation nodes.
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.
    emit_flag
        Node-level emit flags. Used to filter canonical-IR children of expectations so
        that only currently-emittable aux nodes are harvested.
    export_children
        Mutable mapping of parent -> children being built up during export.
    ordered_kids
        The ordered, emit-eligible children of the parent.

    Returns
    -------
    tuple[list[str], int]
        A 2-tuple of (new_kids, child_aux_consumed_count). *new_kids* is the filtered
        ordered children for the parent (non-aux and un-reparented nodes).
        *child_aux_consumed_count* is the number of aux nodes that were harvested from
        the canonical-IR children of expectations (child layout).
    """

    child_aux_consumed: int = 0
    last_expectation: Optional[str] = None
    new_kids: list[str] = []
    prefer_en = config.description_text_policy == "prefer_text_en"

    def _attach_aux_node(*, aux_node_id: str, target_expectation_id: str) -> bool:
        """Process a single aux node as either metadata or as an export child.

        Parameters
        ----------
        aux_node_id
            The canonical node ID of the aux statement to attach.
        target_expectation_id
            The canonical node ID of the expectation to which the aux statement should
            be attached.

        Returns
        -------
        bool
            True if the aux node was newly attached/reparented, False if it had already
            been attached under the same expectation.
        """

        if _is_attachable(config=config, role=ctx.nodes_by_id[aux_node_id]["role"]):
            if _is_already_attached(
                aux_node_id=aux_node_id,
                aux_nodes_attached_to_expectation=aux_nodes_attached_to_expectation,
                expectation_id=target_expectation_id,
            ):
                return False

            aux_nodes_attached_to_expectation[target_expectation_id].append(
                _build_aux_payload(
                    aux_node_id=aux_node_id, ctx=ctx, prefer_en=prefer_en
                )
            )
            attached_aux_node_ids.add(aux_node_id)
            return True

        return _append_unique_child(
            child_id=aux_node_id,
            export_children=export_children,
            parent_id=target_expectation_id,
        )

    for cid in ordered_kids:
        role = ctx.nodes_by_id[cid]["role"]

        if role == StatementRole.EXPECTATION.value:
            last_expectation = cid
            new_kids.append(cid)

            # Child layout: get aux nodes that are direct children of this expectation
            # in the canonical IR.
            for child_id in ctx.children_by_parent.get(cid, []):
                if not emit_flag[child_id]:
                    continue

                child_role = ctx.nodes_by_id[child_id]["role"]

                if child_role in AUX_ROLES and _attach_aux_node(
                    aux_node_id=child_id, target_expectation_id=cid
                ):
                    child_aux_consumed += 1

            continue

        # Sibling layout: aux node following an expectation in sibling order.
        if role in AUX_ROLES and last_expectation:
            _attach_aux_node(aux_node_id=cid, target_expectation_id=last_expectation)
            continue

        new_kids.append(cid)

    return new_kids, child_aux_consumed


def _should_emit_node_with_reason(
    *, config: CreateKGConfig, ctx: ExportContext, node_id: str
) -> tuple[bool, str]:
    """Determine whether a canonical node should be emitted. If it should not be
    emitted, then also include a drop reason.

    NB: This function implements a conservative-drop behavior: a node is dropped if
    **any** of its `source_decision_ids` maps to a droppable decision. This is a design
    choice---not something set in stone. It is stricter than “drop only if the node is
    entirely sourced from bad segments.” In mixed-provenance cases, this could overdrop
    nodes.

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
    tuple[bool, str]
        (True, "emitted") if the node should be emitted, or (False, reason) where
        reason is a human-readable string explaining why the node was dropped. In
        whitelist mode, non-grouping nodes are only eligible for `export_as_sfi_other`
        when they are leaf nodes.
    """

    node = ctx.nodes_by_id[node_id]
    role = str(node.get("role") or "")

    # Segment drop policy.
    for source_decision_id in node["source_decision_ids"]:
        decision = ctx.decisions_by_id[source_decision_id]

        if decision and ctx.should_drop_segment(decision):
            decision_type = decision["decision_type"]
            col_sig = decision["columns_signature"]
            return (
                (False, f"dropped:columns_signature:{col_sig}")
                if col_sig and col_sig in ctx.kg_config.non_standard_columns_signature
                else (False, f"dropped:segment_decision:{decision_type}")
            )

    # Role handling.
    if role == StatementRole.GUIDANCE.value and config.guidance_handling == "drop":
        return False, f"dropped:guidance_handling:{config.guidance_handling}"

    if role == StatementRole.DESCRIPTOR.value and config.descriptor_handling == "drop":
        return False, f"dropped:descriptor_handling:{config.descriptor_handling}"

    # Strict grouping policy: if it's not a statement role, it must be an allowed
    # grouping. Non-grouping nodes may only be emitted as `Other` when they are true
    # leaves. Structural non-grouping nodes are dropped so their children can be
    # hoisted to the nearest surviving ancestor by
    # `_reattach_children_of_dropped_nodes`, rather than letting a semantic `Other`
    # node function as a de facto grouping parent.
    if (
        config.grouping_role_policy == "whitelist"
        and role != NodeRole.FRAMEWORK.value
        and role not in STATEMENT_ROLE_VALUES
        and not _is_grouping_role(config=config, role=role)
    ):
        if config.non_grouping_role_handling == "drop":
            return (
                False,
                f"dropped:non_grouping_role:{config.non_grouping_role_handling}",
            )

        has_canonical_children = len(ctx.children_by_parent.get(node_id, [])) > 0
        return (
            (False, "dropped:non_grouping_role:structural_parent")
            if has_canonical_children
            else (True, "emitted")
        )

    return True, "emitted"


def _sort_order_map(
    *, framework_uuid: UUID | str, order_map: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Stabilize parent key ordering while preserving deterministic child order.

    By the time this function is called, `order_map` has already been built in step 10
    by `_build_relationships_and_order()`. At that point, each entry already means:

        * key = exported parent UUID string
        * value = ordered list of exported child UUID strings

    This function just sorts three top-level outputs for stable serialization. Because
    Python dicts preserve insertion order, the returned dict will always serialize in
    this stable order:

        * framework parent first
        * all remaining parent UUID keys alphabetically

    The child UUID lists are copied over unchanged. So if a parent had children
    [C3, C1, C2], this function leaves that as [C3, C1, C2]. It does not sort children.

    Examples
    --------
    1. Framework key is moved to the front
        Suppose:

            framework_uuid = "fw"
            order_map = {
                "b-parent": ["c2", "c1"],
                "fw": ["a-parent", "b-parent"],
                "a-parent": ["a1", "a2"],
            }

        Then the returned dictionary is:

            {
                "fw": ["a-parent", "b-parent"],
                "a-parent": ["a1", "a2"],
                "b-parent": ["c2", "c1"],
            }

        The framework entry is placed first, and all remaining parent keys are sorted
        lexicographically.

    2. Child order is preserved exactly
        Suppose:

            order_map = {
                "fw": ["p2", "p1"],
                "p2": ["x3", "x1", "x2"],
            }

        `_sort_order_map()` does not sort the child lists. The returned dictionary
        still contains:

            "fw": ["p2", "p1"]
            "p2": ["x3", "x1", "x2"]

        Only the ordering of parent keys in the outer dictionary is normalized.

    3. No-op when already stable
        Suppose:

            framework_uuid = "fw"
            order_map = {
                "fw": ["a", "b"],
                "a": ["a1"],
                "b": ["b1"],
            }

        Because the framework key is already first and the remaining keys are already
        in sorted order, the returned dictionary has the same visible ordering.

    4. Framework key absent
        Suppose:

            framework_uuid = "fw"
            order_map = {
                "b": ["y"],
                "a": ["x"],
            }

        Then the returned dictionary is:

            {
                "a": ["x"],
                "b": ["y"],
            }

        The function still sorts non-framework parent keys even when the framework key
        is absent.

    Parameters
    ----------
    framework_uuid
        The UUID of the framework.
    order_map
        Dictionary mapping parent IDs to already-ordered child IDs.

    Returns
    -------
    dict[str, list[str]]
        A new order map dictionary sorted by parent keys.
    """

    fw_id = str(framework_uuid)
    order_map_sorted: dict[str, list[str]] = {}

    if fw_id in order_map:
        order_map_sorted[fw_id] = order_map[fw_id]

    for k in sorted(k for k in order_map if k != fw_id):
        order_map_sorted[k] = order_map[k]

    return order_map_sorted


def _suppress_attached_to_expectation(
    *,
    config: CreateKGConfig,
    ctx: ExportContext,
    drop_reasons: dict[str, str],
    emit_flag: dict[str, bool],
    reparent_stats: dict[str, Any],
) -> None:
    """Modify emit flags and track stats for attach-to-expectation handling.

    NB: If aux statements are "attach_to_expectation_metadata", they should NOT be
    counted as emitted nodes for pruning. Only aux statements that were successfully
    attached to an owning expectation are suppressed; orphan aux statements remain
    emitted (and are tagged as `orphan_aux`). In other words, after this function, aux
    statements are no longer "real export nodes"---they become metadata only.

    This function basically looks at the set of aux node IDs that were successfully
    attached to an expectation during steps 3 and 4, and then decides whether those aux
    nodes should still be emitted as standalone SFIs. Thus, this function is **not**
    finding new attachments (that already happened in steps 3 and 4). Instead, this
    function enforces the export policy for those already-attached aux nodes. After
    step 4, the exporter knows "G1 and D1 belong to expectation E1.". This function
    then asks: "Since G1 and D1 are already attached to E1's metadata, should they
    still appear as separate SFIs?".

    NB: There are really three cases for aux nodes.
        1. Attached aux node -> suppress as standalone SFI. If an aux node was
            successfully attached to an expectation and the config says
            attach-to-metadata, then it is dropped from standalone emission in step 5.
        2. Orphan aux node -> keep as standalone SFI
            If an aux node was not successfully attached because there was no owning
            expectation, step 5 does not suppress it. It stays emitted. That is why
            this function only iterates `attached_aux_node_ids`, not all aux nodes.
        3. Aux configured as "export_as_sfi_other" -> keep as standalone SFI. If
            guidance or descriptor handling were configured differently, then
            `_is_attachable()` would not have attached them in the first place, so step
            5 would have nothing to suppress for those nodes.

    NB: This function needs to be called before later cleanup because later steps
    need to know the final emit/non-emit state. The main flow says:
        - Step 5: Modify emit flags for attach-to-expectation
        - Step 6: Suppress subtrees rooted under aux nodes converted into
            expectation metadata
        - Step 7: Reattach children of dropped nodes
        - Step 8: Prune empty groupings

    This ordering is correct. If step 5 did not happen first, later cleanup steps
    would still think those attached aux nodes were legitimate exported nodes and
    might keep them alive or preserve their subtrees.

    Parameters
    ----------
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.
    drop_reasons
        Dictionary mapping node IDs to reasons they were dropped. Mutated in-place.
    emit_flag
        Dictionary mapping node IDs to boolean emit flags. Mutated in-place.
    reparent_stats
        Dictionary containing reparenting statistics. Mutated in-place.
    """

    attach_to_exp_count = 0

    # Only suppress aux nodes that were actually attached to an expectation's metadata.
    # This avoids silently deleting "orphan" aux statements that had no owning
    # expectation (those remain as SFIs and are tagged via `orphan_aux` metadata).
    attached_aux_node_ids: set[str] = set(
        reparent_stats.get("attached_aux_node_ids", [])
    )

    if (
        config.guidance_handling == "attach_to_expectation_metadata"
        or config.descriptor_handling == "attach_to_expectation_metadata"
    ):
        for nid in attached_aux_node_ids:
            if not emit_flag.get(nid, False):
                continue

            role = ctx.nodes_by_id[nid]["role"]

            if (
                role == StatementRole.GUIDANCE.value
                and config.guidance_handling == "attach_to_expectation_metadata"
            ):
                attach_to_exp_count += 1
                drop_reasons[nid] = f"dropped_guidance:{config.guidance_handling}"
                emit_flag[nid] = False
            elif (
                role == StatementRole.DESCRIPTOR.value
                and config.descriptor_handling == "attach_to_expectation_metadata"
            ):
                attach_to_exp_count += 1
                drop_reasons[nid] = f"dropped_descriptor:{config.descriptor_handling}"
                emit_flag[nid] = False

    reparent_stats["suppressed_after_being_attached"] = attach_to_exp_count


def _suppress_subtrees_of_attached_aux_nodes(
    *,
    drop_reasons: dict[str, str],
    emit_flag: dict[str, bool],
    export_children: dict[str, list[str]],
    reparent_stats: dict[str, Any],
) -> dict[str, int]:
    """Suppress any exported descendants reachable from aux nodes that were attached to
    expectation metadata, and remove those aux-rooted subtrees from the export tree.

    When a guidance/descriptor node is converted into expectation metadata via
    `_reparent_aux_nodes_under_expectations`, its subtree should not later be hoisted
    back into the Academic Standards hierarchy. This function removes those descendants
    from the export tree and marks any still-emitted descendants as dropped.

    In other words, once an aux node has been converted into expectation metadata in
    steps 3 - 4, nothing under that aux node should be allowed to survive in the
    exported hierarchy.

    Otherwise, a later hoisting pass could leak that subtree back into the KG. By the
    time step 6 (i.e., this function is called), step 4 has discovered attachable aux
    nodes, step 5 has flipped `emit_flag=false` for attached guidance/descriptors, but
    `export_children` may still contain those aux nodes as parents with descendants
    underneath them.

    Without this function, step 7 (`_reattach_children_of_dropped_nodes()`) could see a
    dropped aux parent that still has children and hoist those children upward to a
    surviving ancestor. That would reintroduce content from an aux subtree that was
    supposed to disappear into metadata.

    So, one way to view step 5 is that step 5 suppresses the attached aux nodes
    themselves whereas step 6 (this function) suppresses everything below them, so
    those descendants cannot be hoisted back later.

    Examples
    --------
    1. Main case that this function is protecting against
        Suppose step 5 already attached and suppressed G1:

        P
        - E1 (expectation)
        - G1 (guidance, attached to E1 metadata, emit_flag=False)
            - X1
            - X2

        Without step 6:

        * G1 is dropped,
        * but G1 still has children X1 and X2,
        * then step 7 could hoist X1 and X2 up to P or another surviving ancestor.

        That would be wrong, because X1 and X2 only existed under a guidance node that
        has already been absorbed into metadata.

        With step 6:

        * X1 and X2 are recursively marked non-emitted,
        * G1, X1, and X2 are removed from export_children,
        * so step 7 never gets a chance to hoist them.

    2. Multiple attached aux roots
        Suppose two attached aux nodes still have subtrees:

        P
        - E1
        - G1 (attached aux root)
            - X1
        - D1 (attached aux root)
            - Y1
            - Y2

        Step 6 will:

        * treat G1 and D1 as subtree_roots,
        * recursively suppress X1, Y1, and Y2,
        * remove G1, D1, X1, Y1, Y2 from the export tree.

        Returned stats would be:

        {
            "attached_aux_subtree_root_count": 2,
            "suppressed_attached_aux_descendant_count": 3,
        }

    3. No-op case
        Suppose attached aux nodes are leaves:

        P
        - E1
        - G1 (attached aux, no children)
        - D1 (attached aux, no children)

        Then:

        * attached_aux_node_ids is non-empty,
        * but subtree_roots is empty because neither G1 nor D1 has children,
        * so step 6 does no recursive suppression and returns zeros. It only cleans
            child lists to keep non-emitted nodes out.

    Parameters
    ----------
    drop_reasons
        Mutable mapping of canonical node ID -> drop reason.
    emit_flag
        Mutable mapping of canonical node ID -> emit boolean.
    export_children
        Export parent -> children mapping. Mutated in place.
    reparent_stats
        Reparent/attach statistics. The function reads `attached_aux_node_ids` and
        updates subtree-suppression stats.

    Returns
    -------
    dict[str, int]
        Statistics describing how many metadata-attached aux roots had export subtrees
        and how many descendant nodes were suppressed.
    """

    # Get the aux nodes that were successfully attached to expectation metadata earlier.
    attached_aux_node_ids = set(reparent_stats.get("attached_aux_node_ids", []))

    # No need to suppress if no aux nodes were attached to expectations.
    if not attached_aux_node_ids:
        return {
            "attached_aux_subtree_root_count": 0,
            "suppressed_attached_aux_descendant_count": 0,
        }

    # Find which attached aux nodes are actually subtree roots in the current export
    # tree. We only care about attached aux nodes that still have exported children.
    subtree_roots: set[str] = {
        nid for nid in attached_aux_node_ids if export_children.get(nid)
    }

    # If none of the attached aux nodes have children, we do a cleanup pass to remove
    # any non-emitted children from parent lists, then return zero stats.
    if not subtree_roots:
        for pid, kids in list(export_children.items()):
            export_children[pid] = [c for c in kids if emit_flag.get(c, False)]

        return {
            "attached_aux_subtree_root_count": 0,
            "suppressed_attached_aux_descendant_count": 0,
        }

    # Otherwise, we traverse downward from those aux roots using a stack to collect all
    # descendants.
    stack: list[str] = [
        child for root in subtree_roots for child in export_children.get(root, [])
    ]
    suppressed_descendants: set[str] = set()

    while stack:
        nid = stack.pop()

        if nid in suppressed_descendants:
            continue

        # For every discovered descendant that is still emitted, we flip the emit flag
        # and add a drop reason (if it does not exist).
        suppressed_descendants.add(nid)
        stack.extend(export_children.get(nid, []))

        # Evaluate emit_flag and drop_reasons upon discovery.
        if emit_flag.get(nid, False):
            emit_flag[nid] = False
            drop_reasons.setdefault(
                nid, "dropped:ancestor_attached_to_expectation_metadata"
            )

    # Now, remove `blocked_nodes` from the export tree.
    blocked_nodes = attached_aux_node_ids | suppressed_descendants

    for pid in blocked_nodes:
        export_children.pop(pid, None)

    for pid, kids in export_children.items():
        export_children[pid] = [
            c for c in kids if emit_flag.get(c, False) and c not in blocked_nodes
        ]

    return {
        "attached_aux_subtree_root_count": len(subtree_roots),
        "suppressed_attached_aux_descendant_count": len(suppressed_descendants),
    }


def _to_int_or_roman(s: str) -> int | str:
    """Convert a string to an integer if it's purely digits, or to a Roman numeral
    value if it matches a known Roman numeral.

    Parameters
    ----------
    s
        The input string to convert.

    Returns
    -------
    int | str
        The integer value if the string is purely digits, the integer value of the
        Roman numeral if it matches a known Roman numeral, or the original string if
        neither conversion applies.
    """

    if re.fullmatch(r"\d+", s):
        try:
            return int(s)
        except ValueError:
            return s

    u = s.upper()

    if u in ROMAN_MAP:
        return ROMAN_MAP[u]
    return s


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
        return v.strip() or None

    if isinstance(v, datetime):
        return v.isoformat()

    iso = getattr(v, "isoformat", None)

    if callable(iso):
        try:
            return str(iso())
        except (TypeError, ValueError, AttributeError):
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

    Examples
    --------
    1. Referential integrity failure
        Suppose a relationship points to target UUID `UX`, but `UX` is not present in
        the emitted SFI set.

        Then verification fails with a "Relationship references missing entity"
        assertion.

    2. Ordering mismatch failure
        Suppose relationships imply:

            parent P -> children [A, B]

        but `parent_to_children[P] == [A]`.

        Then verification fails because the hierarchy-order artifact does not match the
        exported hasChild edges.

    3. Reachability failure
        Suppose an expectation SFI `E1` was emitted, but due to a hoisting bug it is
        not reachable from the framework via any chain of hasChild edges.

        Then verification fails with a reachability assertion, identifying emitted SFIs
        that are disconnected from the framework root.

    4. Groupings-only export failure
        Suppose the exporter emitted only grouping SFIs such as stage/strand/subtopic,
        but no expectation SFI with `normalized_statement_type="Standard"`.

        Then verification fails with:

            "No expectation SFIs emitted (normalized_statement_type='Standard')."

        This prevents a structurally non-empty but semantically useless standards
        export.

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
    """

    # Check referential integrity.
    sfi_ids = {str(sfi.case_identifier_uuid) for sfi in sfi_by_node.values()}
    fw_id = str(framework.case_identifier_uuid)

    for r in relationships:
        s_ok = (r.source_entity_value == fw_id) or (r.source_entity_value in sfi_ids)
        t_ok = r.target_entity_value in sfi_ids
        assert s_ok and t_ok, (
            f"Relationship references missing entity: "
            f"{r.relationship_type} {r.source_entity_value} -> {r.target_entity_value}"
        )

    # Check ordering integrity.
    rel_children_by_parent: DefaultDict[str, set[str]] = defaultdict(set)

    for r in relationships:
        if r.relationship_type == "hasChild":
            rel_children_by_parent[r.source_entity_value].add(r.target_entity_value)

    for parent, kids in rel_children_by_parent.items():
        ordered = parent_to_children.get(parent)
        assert ordered is not None, f"Missing hierarchy order for parent: {parent}"
        assert set(ordered) == set(
            kids
        ), f"Hierarchy order child set mismatch for parent: {parent}"

    # Check reachability: every emitted SFI must be reachable from the framework root
    # via hasChild edges. This catches orphans caused by pruning/filters.
    adj: DefaultDict[str, list[str]] = defaultdict(list)

    for r in relationships:
        if r.relationship_type == "hasChild":
            adj[r.source_entity_value].append(r.target_entity_value)

    stack: list[str] = [fw_id]
    visited: set[str] = set()

    while stack:
        cur = stack.pop()

        if cur not in visited:
            visited.add(cur)

            # Add all unvisited neighbors at once.
            stack.extend(n for n in adj.get(cur, []) if n not in visited)

    reachable_sfis = visited - {fw_id}
    missing = sfi_ids - reachable_sfis

    assert not missing, (
        f"Reachability: {len(missing)} emitted SFIs unreachable from framework root. "
        f"Examples: {sorted(missing)[:20]}"
    )

    # Ensure at least one expectation ("Standard") exists. This prevents "successful"
    # exports that only contain groupings.
    has_any_standard = any(
        sfi.normalized_statement_type == "Standard" for sfi in sfi_by_node.values()
    )
    assert sfi_by_node, "No StandardsFrameworkItems emitted; check drop policies."
    assert has_any_standard, (
        "No expectation SFIs emitted (normalized_statement_type='Standard'). "
        "Export produced only groupings/other items. "
        "Check canonical IR roles and drop/handling policies."
    )


def _walk_ancestors(
    *, ctx: ExportContext, node_id: str, parent_by_child: dict[str, str] | None = None
) -> list[str]:
    """Return canonical `node_id` ancestry from root -> ... -> node_id (excluding root).

    Examples
    --------
    1. Basic ancestry walk
        Suppose the canonical hierarchy is:

            ROOT -> A(stage) -> B(subtopic) -> C(expectation)

        Calling:

            _walk_ancestors(..., node_id="C")

        returns:

            ["A", "B", "C"]

        The root is excluded from the returned list.

    2. Export-time parent override
        Suppose step 7 hoisted an expectation so that its exported ancestry differs
        from the canonical one.

        If `parent_by_child={"C": "X", "X": "A"}` is passed in, the function walks the
        export-time ancestry instead of `ctx.parent_by_child`.

        This allows progression metadata to reflect the finalized export hierarchy.

    3. Cycle-safe partial ancestry
        If the supplied parent mapping is malformed and contains a cycle, the function
        returns the ancestry accumulated up to the point where the cycle is detected,
        rather than looping forever.

    Parameters
    ----------
    ctx
        The ExportContext for the CanonicalIR, providing access to parent-child
        mappings.
    node_id
        The ID of the canonical node for which to walk ancestors.
    parent_by_child
        Optional parent_by_child mapping to use instead of ctx.parent_by_child. This is
        used in contexts where the parent-child relationships are being modified (e.g.,
        during export) and the original canonical IR parent_by_child would not reflect
        the current hierarchy.

    Returns
    -------
    list[str]
        The list of ancestor node IDs from the root down to the given node (excluding
        the root itself). If the node is not reachable from the root, returns the
        ancestry up to the point where a cycle is detected or the root is reached.
    """

    chain: list[str] = []
    cur: str | None = node_id
    parent_lookup = parent_by_child or ctx.parent_by_child
    seen: set[str] = set()

    while cur and cur != ctx.root_id and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        cur = parent_lookup.get(cur)

    chain.reverse()
    return chain


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

    The process is as follows:

    1. Emit the framework node.
    2. Precompute node-level emit flags based on segment-drop, statement-role, and
        grouping-role policies.
    3. Compute export-time aux parenting based on preceding expectation siblings to
        include:
            - Building the export tree
            - Handling root and grouping parents
            - Supporting sibling and child layouts
            - Collecting attachment payloads
            - Tracking orphan aux nodes
    4. Attach-only discovery pass: when aux nodes remain as siblings (or children) but
        export config requests attaching them to expectation metadata, discover and
        attach without modifying hierarchy:
            - Starts from what step 3 already found
            - Calls `_attach_aux_statements_in_export_tree()`, which tries to discover
                more attachable aux nodes in the current `export_children` tree without
                changing the hierarchy
            - Merges the returned stats back into `reparent_stats`
            - Overwrites/recomputes the tracked attached/orphan ID lists
        This step answers the question: "Given the tree we have now, can any remaining
        aux nodes be attached to expectations as metadata?". This matters because after
        Step 3, we can still have cases like:
            - Aux nodes still sitting as children under an expectation
            - Aux siblings under a non-statement parent that Step 3 did not overwrite
            - Mixed layouts that were not fully consumed during export-tree construction
    5. Handle attach-to-expectation rules for guidance/descriptors, modifying emit
        flags accordingly.
    6. Suppress export subtrees rooted under aux nodes that were converted into
        expectation metadata, preventing subtree leakage back into the hierarchy.
    7. Re-attach children of dropped mid-hierarchy nodes to their nearest surviving
        ancestor, ensuring tree connectivity before pruning.
    8. Prune empty groupings iteratively, modifying emit flags accordingly.
    9. Emit StandardsFrameworkItems for all nodes still flagged for emission.
    10. Build hasChild relationships and hierarchy order mappings.
    11. Sort items and relationships for stable output.
    12. Package everything into an AcademicStandardsExport dataclass.
    13. Persist the exported academic standards artifacts to disk. This step writes
        both:
            - Decomposed typed artifacts (framework, items, hasChild relationships,
                hierarchy order, drop reasons, reparent stats), and
            - A single graph bundle (`academic_standards_kg.json`) built from those
                artifacts.
        The decomposed artifacts are used for debugging and for reloading prior exports
        via `load_academic_standards_export()`. The graph bundle is used as the
        academic standards sentinel artifact and as the input for later merged bundles
        with Learning Components and Learning Progressions.

    NB: Aux nodes means auxiliary statement nodes: specifically, canonical nodes whose
    role is either `descriptor` or `guidance` (as defined by `AUX_ROLES`).
    `expectation` is considered to be the main normative role. So aux nodes are not
    "all non-expectations"; they are two statement-role types that are treated as
    supporting material around an expectation. In practice, aux nodes are things that
    the exporter can either drop, attach to an expectation's metadata, or keep as SFIs
    depending on config parameters. For example, `_is_attachable()` determines whether
    an aux node is eligible for attachment based on its role and the export config.

    NB: In this exporter, an aux node can end up in one of 3 states:

    1. Attached to an expectation as metadata and not emitted as its own SFI. This
        happens when the config specifies "attach_to_expectation_metadata". In this
        scenario, the sibling layout approach attaches the aux node to the most
        preceding expectation sibling. The child layout attaches the aux node to an
        expectation that already owns it as a child in the export tree.
    2. Emitted as its own SFI. An aux node is still emitted as a separate SFI in
        two common cases. First, if the config specifies "export_as_sfi_other",
        then `_is_attachable()` returns `False`, so that the node is **not**
        attached into expectation metadata. If `emit_flag=True`, then
        `_emit_sfis()` will emit it as its own SFI with a normalized statement
        type of "Other" because that's how aux roles normalize. Second, if
        attachment was requested but no owning expectation was found, then this is
        the orphan aux case. If a guidance/descriptor appears before any
        expectation in sibling order, `_process_sibling_group()` marks it as orphan
        instead of attaching it to a later expectation.
        `_process_attach_to_expectation()` then suppresses only the aux nodes that
        were actually attached. Orphan aux nodes remain emitted. `_emit_sfi()`
        marks them with `metadata["orphan_aux"] = True`. So the rule is:
        attached aux -> no standalone SFI; orphaned aux -> yes, standalone SFI,
        tagged as `orphan_aux`.
    3. Dropped entirely. An aux node can get fully dropped if the config specifies
        "drop". In this case, the aux node never makes it to the
        attachment-or-emission decision.

    For example, suppose our config specifies the following:

        * guidance_handling = "attach_to_expectation_metadata"
        * descriptor_handling = "attach_to_expectation_metadata"
        * aux_statement_parenting = "under_expectation"

    that means:

        1. Guidance/descriptor nodes are intended to disappear as standalone SFIs if
            they can be matched to an expectation.
        2. Only unmatched/orphan guidance/descriptor nodes remain as standalone SFIs.
        3. Expectations stay emitted as normal SFIs, and they carry matched aux nodes
            in metadata["aux_statements"].

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

    # 1.
    framework = _emit_framework(
        canonical_ir_created_at=canonical_created_at_iso,
        config=config,
        ctx=ctx,
        decision_set_id=decision_set_id,
        provenance_context=provenance_context,
    )
    framework_uuid = framework.case_identifier_uuid

    # 2.
    emit_flag, drop_reasons = _build_initial_emit_flags(config=config, ctx=ctx)

    # 3.
    export_children, aux_nodes_attached_to_expectation, reparent_stats = (
        _compute_export_children(config=config, ctx=ctx, emit_flag=emit_flag)
    )

    # 4.
    if (
        config.guidance_handling == "attach_to_expectation_metadata"
        or config.descriptor_handling == "attach_to_expectation_metadata"
    ):
        attached_aux_node_ids = set(reparent_stats.get("attached_aux_node_ids", []))
        orphan_aux_node_ids = set(reparent_stats.get("orphan_aux_node_ids", []))
        attach_only_stats = _attach_aux_statements_in_export_tree(
            attached_aux_node_ids=attached_aux_node_ids,
            aux_nodes_attached_to_expectation=aux_nodes_attached_to_expectation,
            config=config,
            ctx=ctx,
            emit_flag=emit_flag,
            export_children=export_children,
            orphan_aux_node_ids=orphan_aux_node_ids,
        )
        reparent_stats.update(attach_only_stats)
        reparent_stats["attached_aux_node_ids"] = sorted(attached_aux_node_ids)
        reparent_stats["orphan_aux_node_ids"] = sorted(orphan_aux_node_ids)
        reparent_stats["orphan_aux_count"] = len(orphan_aux_node_ids)

    # 5.
    _suppress_attached_to_expectation(
        config=config,
        ctx=ctx,
        drop_reasons=drop_reasons,
        emit_flag=emit_flag,
        reparent_stats=reparent_stats,
    )

    # 6.
    attached_aux_subtree_stats = _suppress_subtrees_of_attached_aux_nodes(
        drop_reasons=drop_reasons,
        emit_flag=emit_flag,
        export_children=export_children,
        reparent_stats=reparent_stats,
    )
    reparent_stats.update(attached_aux_subtree_stats)

    # 7.
    reattach_stats = _reattach_children_of_dropped_nodes(
        ctx=ctx, emit_flag=emit_flag, export_children=export_children
    )
    reparent_stats.update(reattach_stats)

    # 8.
    pruned_node_ids = _handle_empty_grouping_pruning(
        config=config,
        ctx=ctx,
        drop_reasons=drop_reasons,
        emit_flag=emit_flag,
        export_children=export_children,
    )

    # 9.
    export_parent_by_child = _build_export_parent_by_child(
        root_id=ctx.root_id, export_children=export_children
    )
    export_order_index = {
        (parent_id, child_id): child_index
        for parent_id, child_ids in export_children.items()
        for child_index, child_id in enumerate(child_ids)
    }
    sfi_by_node = _emit_sfis(
        aux_nodes_attached_to_expectation=aux_nodes_attached_to_expectation,
        canonical_created_at_iso=canonical_created_at_iso,
        config=config,
        ctx=ctx,
        emit_flag=emit_flag,
        export_order_index=export_order_index,
        export_parent_by_child=export_parent_by_child,
        orphan_aux_node_ids=set(reparent_stats.get("orphan_aux_node_ids") or []),
    )

    # 10.
    relationships, order_map = _build_relationships_and_order(
        config=config,
        ctx=ctx,
        export_children=export_children,
        export_order_index=export_order_index,
        framework_uuid=framework_uuid,
        sfi_by_node=sfi_by_node,
    )
    _verify_standards_export(
        framework=framework,
        parent_to_children=order_map,
        relationships=relationships,
        sfi_by_node=sfi_by_node,
    )

    # 11.
    items_sorted = sorted(
        sfi_by_node.values(), key=lambda sfi: str(sfi.case_identifier_uuid)
    )
    relationships_sorted = sorted(
        relationships,
        key=lambda r: (
            r.relationship_type,
            r.source_entity_value,
            r.target_entity_value,
            str(r.identifier),
        ),
    )
    order_map_sorted = _sort_order_map(
        framework_uuid=framework_uuid, order_map=order_map
    )

    # 12.
    academic_standards = AcademicStandardsExport(
        drop_reasons=drop_reasons,
        framework=framework,
        items=items_sorted,
        order=HierarchyOrderExport(order=order_map_sorted),
        pruned_node_ids=pruned_node_ids,
        relationships=relationships_sorted,
        reparent_stats=reparent_stats,
    )

    # 13.
    write_to_json(
        fp=kg_dirs.academic_standards / "academic_standards_framework.json",
        json_info=academic_standards.framework.model_dump(mode="json"),
    )
    write_to_json(
        fp=kg_dirs.academic_standards / "academic_standards_framework_items.json",
        json_info=[sfi.model_dump(mode="json") for sfi in academic_standards.items],
    )
    write_to_json(
        fp=kg_dirs.academic_standards
        / "academic_standards_has_child_relationships.json",
        json_info=[r.model_dump(mode="json") for r in academic_standards.relationships],
    )
    write_to_json(
        fp=kg_dirs.academic_standards / "academic_standards_hierarchy_order.json",
        json_info=academic_standards.order.model_dump(mode="json"),
    )
    write_to_json(
        fp=kg_dirs.academic_standards / "academic_standards_kg.json",
        json_info=_build_academic_standards_graph_bundle(
            academic_standards=academic_standards, config=config, ctx=ctx
        ),
    )
    write_to_json(
        fp=kg_dirs.academic_standards / "academic_standards_drop_reasons.json",
        json_info=academic_standards.drop_reasons,
    )
    write_to_json(
        fp=kg_dirs.academic_standards / "academic_standards_reparent_stats.json",
        json_info=academic_standards.reparent_stats,
    )

    logger.success(
        f"Exported Academic Standards KG: "
        f"{len(academic_standards.items)} items, "
        f"{len(academic_standards.relationships)} `hasChild` relationships"
    )

    return academic_standards


def load_academic_standards_export(kg_dirs: KGDirs) -> AcademicStandardsExport:
    """Reconstruct an AcademicStandardsExport from previously written disk artifacts.

    This enables progressive re-use: if the academic standards KG already exists and
    `overwrite=False`, we load the prior export rather than re-running the LLM-driven
    export pipeline.

    Parameters
    ----------
    kg_dirs
        The KG output directories containing the prior run's artifacts.

    Returns
    -------
    AcademicStandardsExport
        The reconstructed export object, suitable for passing to downstream steps
        (Learning Components, Learning Progressions, reporting).
    """

    d = kg_dirs.academic_standards

    framework = StandardsFramework.model_validate(
        open_json_type(d / "academic_standards_framework.json")
    )
    items = [
        StandardsFrameworkItem.model_validate(raw)
        for raw in open_json_type(d / "academic_standards_framework_items.json")
    ]
    relationships = [
        Relationship.model_validate(raw)
        for raw in open_json_type(d / "academic_standards_has_child_relationships.json")
    ]
    order = HierarchyOrderExport.model_validate(
        open_json_type(d / "academic_standards_hierarchy_order.json")
    )

    # drop_reasons and reparent_stats are persisted alongside the core artifacts so the
    # policy coverage report can be fully regenerated even when the export is reused.
    drop_reasons_fp = d / "academic_standards_drop_reasons.json"
    reparent_stats_fp = d / "academic_standards_reparent_stats.json"
    drop_reasons: dict[str, str] = (
        open_json_type(drop_reasons_fp) if drop_reasons_fp.exists() else {}
    )
    reparent_stats: dict[str, Any] = (
        open_json_type(reparent_stats_fp) if reparent_stats_fp.exists() else {}
    )

    return AcademicStandardsExport(
        drop_reasons=drop_reasons,
        framework=framework,
        items=items,
        order=order,
        pruned_node_ids=set(),  # Only needed during export; safe to default
        relationships=relationships,
        reparent_stats=reparent_stats,
    )


def load_or_export_academic_standards(
    *,
    canonical_ir_created_at: Any = None,
    config: CreateKGConfig,
    ctx: ExportContext,
    decision_set_id: Optional[str] = None,
    kg_dirs: KGDirs,
    provenance_context: Optional[dict[str, Any]] = None,
) -> tuple[AcademicStandardsExport, bool]:
    """Load an existing Academic Standards KG from disk or export a new one.

    Checks whether the academic standards sentinel bundle file already exists on disk.
    If it exists and `config.overwrite` is False, the prior export is loaded from
    disk. Otherwise, a new export is generated.

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
        The KG output directories.
    provenance_context
        Optional provenance context to include in metadata.

    Returns
    -------
    tuple[AcademicStandardsExport, bool]
        A tuple containing the Academic Standards export artifacts and a boolean
        indicating whether the export was reused from disk (`True`) or newly generated
        (`False`).
    """

    as_sentinel = kg_dirs.academic_standards / "academic_standards_kg.json"
    as_reused = False

    if as_sentinel.exists() and not config.overwrite:
        logger.warning(
            "Academic Standards KG already exists and overwrite=False--loading from "
            "disk."
        )
        academic_standards = load_academic_standards_export(kg_dirs)
        as_reused = True
    else:
        if as_sentinel.exists():
            logger.warning(
                "config.overwrite=True: re-exporting Academic Standards KG (existing "
                "artifacts will be overwritten)."
            )

        academic_standards = export_academic_standards(
            canonical_ir_created_at=canonical_ir_created_at,
            config=config,
            ctx=ctx,
            decision_set_id=decision_set_id,
            kg_dirs=kg_dirs,
            provenance_context=provenance_context,
        )

    return academic_standards, as_reused
