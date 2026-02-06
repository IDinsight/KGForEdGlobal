"""This module contains functionalities related to exporting the Academic Standards
knowledge graph. It exports a shape-preserving Learning Commons Academic Standards
knowledge graph from the CanonicalIR using ExportContext indexes and CreateKGConfig
policies.

Outputs
-------

## 1. academic_standards_framework.json

### What it contains

A single **`StandardsFramework`** object: the *root “document/framework” node* for the
PDF. Typical contents:

* Deterministic IDs (framework UUID + CASE UUID/URI fields)
* Human metadata about the document:
  * title, jurisdiction/country, publisher/ministry, year, grade range,
    subjects/learning areas, etc.
  * language info (and sometimes translation-related fields)
* Provenance/source info (doc_key, file name, pipeline run metadata, etc.)

### What questions it answers

“About the framework/document as a whole”:

* What curriculum document did we ingest?
* Which country/ministry/year is this?
* What grade range and subjects does it cover (if present)?
* What is the framework’s stable ID to join all other files to?
* What is the canonical reference for this export run?

## 2. academic_standards_framework_items.json

### What it contains

An array of **`StandardsFrameworkItem` (SFI)** records, i.e. all the nodes under the
framework that we chose to emit.

This is where the bulk of the standards lives:

* **Grouping SFIs** (normalized type: *“Standard Grouping”*)
  * grade/stage/subject/theme/topic/etc.
* **Expectation SFIs** (normalized type: *“Standard”*)
  * the normative outcomes/competencies/objectives
* **Aux SFIs** (normalized type: *“Other”*), only if we exported descriptors/guidance
    as SFIs
  * descriptors/benchmarks/indicators, guidance, etc.

Also usually included:

* Titles/statements (original + English if available)
* Local codes/identifiers (when present)
* Provenance pointers:
  * `source_decision_ids`, `source_segment_ids`, page indices, bbox, section path

### What questions it answers

“About *what* the standards are”:

* What are all the standards statements in this curriculum?
* What are the groupings (grades/subjects/themes/topics) that structure the standards?
* Show me every expectation in Grade 2 Mathematics (we’ll need the hierarchy links from
    `academic_standards_has_child_relationships.json`/`academic_standards_hierarchy_order`
    to filter effectively).
* Where did this standard come from in the PDF (page/bbox/decision IDs)?
* Which items were exported vs dropped (indirectly: only exported ones are present).

## 3. `academic_standards_has_child_relationships.json`

### What it contains

An array of **relationship records** representing the *hierarchy edges*:

* `(framework) -[:hasChild]-> (SFI)`
* `(SFI) -[:hasChild]-> (SFI)`

Each relationship typically includes:

* Deterministic relationship ID (UUIDv5)
* `rel="hasChild"`
* `from_id` and `to_id` (parent/child export IDs)

NB: This file encodes **structure**, but not reliable ordering by itself (even if we
output edges in order, consumers shouldn’t assume it).

### What questions it answers

“About the tree/containment”:

* What are the children of this grade/subject/theme node?
* What is the parent of a given standards statement?
* What is the path from the framework root to this expectation?
* What are all descendants under a given subtree?
* How many standards are under a specific grouping?

## 4. `academic_standards_hierarchy_order.json`

### What it contains

An “ordering artifact” that explicitly captures **sibling order** for each parent node.

Conceptually it is a list/map of:

* `parent_id -> [child_id_1, child_id_2, ...]` in the intended order

This order is sourced from `order_index` on CanonicalIR edges, and then filtered
through:

* `should_emit_node`
* aux re-parenting (if enabled)
* pruning empty groupings (if enabled)

### What questions it answers

“About sequence/reading order/scope-and-sequence hints”

* In what order should I present the standards under this topic?
* What is the “next” standard after this one within a grouping?
* Does this curriculum imply sequencing by topic order/grade order/theme-week order?
* Can I reconstruct a consistent traversal that matches the PDF’s intended flow?

This is also crucial for:

* **Learning progression inference modules** (grade/week ordering)
* Generating UI displays that match the original syllabus structure

---

## How these files all work together

* **Framework** = “What document is this?”
* **Items** = “What nodes exist and what do they say?”
* **hasChild edges** = “How are those nodes connected hierarchically?”
* **hierarchy_order** = “In what order should siblings be traversed/presented?”

If we only had one file:

* Framework alone can’t answer anything about standards content.
* Items alone can list standards text but can’t reliably say “which Grade/Subject they
    belong to” without hierarchy links.
* hasChild edges can build the tree, but without items we don’t know what the nodes
    mean.
* hierarchy_order alone can’t build the tree (it assumes the child set per parent), and
    it doesn’t contain text.

---

## Example “question → which file(s) you need?”

* “What’s the stable ID for the Zambia Grade 1–3 framework?”
  → **framework.json**

* “List all standards statements (expectations) in the whole document.”
  → **framework_items.json**

* “Which topic does this standard belong to?”
  → **has_child_relationships.json + framework_items.json**

* “Show me the Grade 2 → Subject → Topic path for this item.”
  → **has_child_relationships.json + framework_items.json (+ framework.json for root)**

* “What comes after this standard in the syllabus order?”
  → **hierarchy_order.json (+ items for labels/text)**
"""

# Future Library
from __future__ import annotations

# Standard Library
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, DefaultDict, Optional
from uuid import UUID, uuid5

# Package Library
from skg.kgs.schemas import (
    HierarchyOrderExport,
    Relationship,
    StandardsFramework,
    StandardsFrameworkItem,
)
from skg.kgs.utils import ExportContext, KGDirs, node_display_text
from skg.schemas import CreateKGConfig
from skg.utils.constants import NodeRole, StatementRole
from skg.utils.general import write_to_json

AUX_ROLES: set[str] = {StatementRole.DESCRIPTOR.value, StatementRole.GUIDANCE.value}
STATEMENT_ROLE_VALUES: set[str] = {item.value for item in StatementRole}


@dataclass
class AcademicStandardsExport:
    """The output of exporting Academic Standards KG artifacts."""

    framework: StandardsFramework
    items: list[StandardsFrameworkItem]
    order: HierarchyOrderExport
    relationships: list[Relationship]


def _build_academic_standards_graph_bundle(
    *,
    academic_standards: AcademicStandardsExport,
    config: CreateKGConfig,
    ctx: ExportContext,
) -> dict[str, Any]:
    """Build a single Neo4j-friendly graph bundle JSON.

    - Nodes: StandardsFramework + StandardsFrameworkItem
    - Relationships: HAS_CHILD (from Relationships export)
    - Ordering: add `order_index` on HAS_CHILD relationships based on
        HierarchyOrderExport.order (parent -> ordered child ids).

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
        The Neo4j-friendly graph bundle dictionary.
    """

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build (parent, child) -> order_index map from the ordering artifact.
    order_index_by_edge: dict[tuple[str, str], int] = {}

    for parent_id, child_ids in academic_standards.order.order.items():
        for idx, child_id in enumerate(child_ids):
            order_index_by_edge[(parent_id, child_id)] = idx

    # Nodes: use case_identifier_uuid as the Neo4j node key since relationships already
    # key off case_identifier_uuid.
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

    # Relationships: reuse Relationship export, but convert to Neo4j edge shape and add
    # order_index.
    relationships: list[dict[str, Any]] = []

    for r in academic_standards.relationships:
        start_id = r.source_entity_value  # Already case_identifier_uuid as string
        end_id = r.target_entity_value  # Already case_identifier_uuid as string

        props = r.model_dump(mode="json")
        props["order_index"] = order_index_by_edge.get((start_id, end_id))

        relationships.append(
            {
                "id": str(r.identifier),
                "type": "HAS_CHILD",  # Neo4j relationship type
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
        relationships.append(
            _emit_has_child(
                child_uuid=sfi_by_node[cid].case_identifier_uuid,
                config=config,
                doc_key=ctx.doc_key,
                parent_uuid=framework_uuid,
                relationship_metadata=_edge_metadata(ctx.root_id, cid),
                source_entity="StandardsFramework",
            )
        )

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
            label = node_display_text(node=node, prefer_text_en=prefer_text_en)

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
    name = node_display_text(node=root_node, prefer_text_en=prefer_en) or ctx.pdf_name

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
        The entity type of the parent. Pass ``"StandardsFramework"`` for root-level
        edges and ``"StandardsFrameworkItem"`` (default) for SFI-to-SFI edges.

    Returns
    -------
    Relationship
        The constructed hasChild Relationship.
    """

    relationship_metadata = dict(relationship_metadata or {})
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
    canonical_ir_created_at: Any,
    config: CreateKGConfig,
    ctx: ExportContext,
    fw_metadata: dict[str, Any],
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
    fw_metadata
        Pre-computed framework metadata dict (from ctx.get_framework_metadata()).
        Passed in to avoid redundant recomputation per node.
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
    # context. (framework.metadata.provenance_context.bbox) should include coord_space,
    # dpi, page dims, etc.
    if bbox is not None:
        metadata["bbox_ref"] = "framework.metadata.provenance_context.bbox"

    if aux_attachments:
        metadata["aux_statements"] = aux_attachments

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
            ctx=ctx, node_id=node_id, prefer_text_en=prefer_en
        ),
        identifier=sfi_id,
        in_language=fw_metadata["in_language"],
        jurisdiction=fw_metadata["jurisdiction"],
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

    fw_metadata = ctx.get_framework_metadata()  # Compute once for all SFIs
    sfi_by_node: dict[str, StandardsFrameworkItem] = {}

    for node_id, ok in emit_flag.items():
        if not ok:
            continue

        sfi_by_node[node_id] = _emit_sfi(
            aux_attachments=aux_attach_to_expectation.get(node_id),
            canonical_ir_created_at=canonical_created_at_iso,
            config=config,
            ctx=ctx,
            fw_metadata=fw_metadata,
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

    if role in STATEMENT_ROLE_VALUES:
        return False

    if config.grouping_role_policy == "loose":
        return True

    allowed = {r.value for r in config.grouping_roles_whitelist}

    return role in allowed


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
        export_children[pid] = [c for c in kids if c in emitted]

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
                        "text": node_display_text(node=node, prefer_text_en=prefer_en),
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

    # Stable ordering: make file outputs deterministic across runs.
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

    # Preserve deterministic child ordering, but stabilize parent key ordering too.
    fw_id = str(framework_uuid)
    order_map_sorted: dict[str, list[str]] = {}

    if fw_id in order_map:
        order_map_sorted[fw_id] = order_map[fw_id]

    for k in sorted(k for k in order_map if k != fw_id):
        order_map_sorted[k] = order_map[k]

    academic_standards = AcademicStandardsExport(
        framework=framework,
        items=items_sorted,
        order=HierarchyOrderExport(order=order_map_sorted),
        relationships=relationships_sorted,
    )

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

    return academic_standards


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
        and role not in STATEMENT_ROLE_VALUES
        and not _is_grouping_role(config=config, role=role)
    ):
        return config.non_grouping_role_handling != "drop"

    return True
