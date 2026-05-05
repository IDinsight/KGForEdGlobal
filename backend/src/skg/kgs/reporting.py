"""This module consolidates graph validation, entity provenance export, and policy
coverage reporting for the KG export pipeline.

Phases:

1. PolicyCoverageReport: why each node was dropped/emitted + aggregate stats
2. EntityProvenanceExport: flat lookup of export_id -> source provenance
3. GraphValidationReport: structural integrity checks across all export phases
"""

# Standard Library
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

# Third Party Library
from loguru import logger

# Package Library
from skg.kgs.export_academic_standards import AcademicStandardsExport
from skg.kgs.export_learning_components import LearningComponentsExport
from skg.kgs.export_learning_progressions import LearningProgressionsExport
from skg.kgs.schemas import (
    EntityProvenance,
    EntityProvenanceExport,
    GraphValidationReport,
    PolicyCoverageReport,
    Relationship,
)
from skg.kgs.utils import ExportContext, KGDirs, canon_str_pair
from skg.utils.general import write_to_json


def _build_has_child_adjacency(all_rels: list[Relationship]) -> dict[str, list[str]]:
    """Build an adjacency list for hasChild relationships.

    Parameters
    ----------
    all_rels
        List of all relationships across exports.

    Returns
    -------
    dict[str, list[str]]
        Adjacency list mapping source IDs to target IDs for hasChild relations.
    """

    adj: dict[str, list[str]] = {}

    for r in all_rels:
        if r.relationship_type == "hasChild":
            src = _rel_src_id(r)
            tgt = _rel_tgt_id(r)
            adj.setdefault(src, []).append(tgt)

    return adj


def _check_duplicate_relationship_pairs(
    *,
    all_rels: list[Relationship],
    relationship_type: str,
    report: GraphValidationReport,
) -> None:
    """Detect exact duplicate directed source→target pairs for a single relationship
    type.

    Filters `all_rels` down to edges matching `relationship_type`, serializes each as a
    "source->target" string, and reports any pair that appears more than once.

    The error/info codes emitted are type-aware (e.g., `HAS_CHILD_DUPLICATE_PAIR` for
    hasChild, `SUPPORTS_DUPLICATE_PAIR` for supports) with a generic uppercase fallback
    for any other relationship type.

    Parameters
    ----------
    all_rels
        List of all relationships across exports.
    relationship_type
        The relationship type to filter on (e.g., "hasChild", "supports").
    report
        The GraphValidationReport to append errors or info findings to.
    """

    pairs = [
        (_rel_src_id(r), _rel_tgt_id(r))
        for r in all_rels
        if r.relationship_type == relationship_type
    ]
    duplicate_pairs = _duplicate_counts([f"{src}->{tgt}" for src, tgt in pairs])

    if duplicate_pairs:
        code_by_type = {
            "hasChild": "HAS_CHILD_DUPLICATE_PAIR",
            "supports": "SUPPORTS_DUPLICATE_PAIR",
        }
        report.error(
            code=code_by_type.get(
                relationship_type, f"{relationship_type.upper()}_DUPLICATE_PAIR"
            ),
            context={"examples": dict(list(duplicate_pairs.items())[:10])},
            message=(
                f"{len(duplicate_pairs)} duplicate {relationship_type} source-target "
                f"pair(s) detected."
            ),
        )
    else:
        code_by_type = {
            "hasChild": "HAS_CHILD_NO_DUPLICATE_PAIRS",
            "supports": "SUPPORTS_NO_DUPLICATE_PAIRS",
        }
        report.info(
            code=code_by_type.get(
                relationship_type, f"{relationship_type.upper()}_NO_DUPLICATE_PAIRS"
            ),
            message=f"No duplicate {relationship_type} source-target pairs detected.",
        )


def _check_entity_id_uniqueness(
    *,
    fw_id: str,
    lc_id_list: list[str],
    report: GraphValidationReport,
    sfi_id_list: list[str],
) -> set[str]:
    """Check entity UUID uniqueness within and across entity types.

    Validates two layers of uniqueness for exported entity identifiers:

    1. **Intra-type uniqueness**: no duplicate UUIDs within the SFI list or the LC list
        individually. Duplicates here indicate a bug in the upstream exporter that
        emitted the same entity row more than once.
    2. **Cross-type uniqueness**: no UUID collisions between the framework, SFI, and LC
        namespaces. Collisions indicate an ID-generation or namespace-routing bug.

    Raw ID lists are intentionally accepted instead of pre-deduplicated sets so the
    validator can detect duplicate IDs within the same entity type.

    Parameters
    ----------
    fw_id
        The framework entity's case identifier UUID (string).
    lc_id_list
        Raw list of LearningComponent identifier UUIDs, possibly containing duplicates.
    report
        The GraphValidationReport to append errors or info findings to.
    sfi_id_list
        Raw list of StandardsFrameworkItem case identifier UUIDs, possibly containing
        duplicates.

    Returns
    -------
    set[str]
        The unified, deduplicated set of all entity IDs (framework + SFIs + LCs), used
        by downstream referential-integrity checks.
    """

    duplicate_sfi_ids = _duplicate_counts(sfi_id_list)
    duplicate_lc_ids = _duplicate_counts(lc_id_list)

    if duplicate_sfi_ids:
        report.error(
            code="DUPLICATE_SFI_IDS",
            context={"examples": dict(list(duplicate_sfi_ids.items())[:10])},
            message=(
                f"{len(duplicate_sfi_ids)} duplicate StandardsFrameworkItem ID(s) "
                f"detected within the Academic Standards export."
            ),
        )

    if duplicate_lc_ids:
        report.error(
            code="DUPLICATE_LC_IDS",
            context={"examples": dict(list(duplicate_lc_ids.items())[:10])},
            message=(
                f"{len(duplicate_lc_ids)} duplicate LearningComponent ID(s) "
                f"detected within the Learning Components export."
            ),
        )

    sfi_ids = set(sfi_id_list)
    lc_ids = set(lc_id_list)
    cross_type_collisions: dict[str, list[str]] = {}

    if fw_id in sfi_ids:
        cross_type_collisions.setdefault(fw_id, []).extend(
            ["StandardsFramework", "StandardsFrameworkItem"]
        )

    if fw_id in lc_ids:
        cross_type_collisions.setdefault(fw_id, []).extend(
            ["StandardsFramework", "LearningComponent"]
        )

    for collision_id in sorted(sfi_ids & lc_ids):
        cross_type_collisions.setdefault(collision_id, []).extend(
            ["StandardsFrameworkItem", "LearningComponent"]
        )

    if cross_type_collisions:
        report.error(
            code="ENTITY_ID_CROSS_TYPE_COLLISION",
            context={"examples": dict(list(cross_type_collisions.items())[:10])},
            message=(
                f"{len(cross_type_collisions)} entity ID(s) are reused across "
                f"different entity types. This indicates a namespace or ID generation bug."
            ),
        )

    if not duplicate_sfi_ids and not duplicate_lc_ids and not cross_type_collisions:
        expected_count = 1 + len(sfi_id_list) + len(lc_id_list)
        report.info(
            code="ENTITY_IDS_UNIQUE",
            message=(
                f"All {expected_count} entity IDs are unique within and across types "
                f"(1 framework, {len(sfi_id_list)} SFIs, {len(lc_id_list)} LCs)."
            ),
        )

    return {fw_id} | sfi_ids | lc_ids


def _check_has_child_count(
    *, has_child_rels_count: int, report: GraphValidationReport, sfi_entity_count: int
) -> None:
    """Verify that the number of hasChild edges equals the number of exported SFI rows.

    In a well-formed rooted standards tree every StandardsFrameworkItem has exactly one
    incoming hasChild edge (from either the framework root or another SFI). Thus, the
    total hasChild count should equal the total SFI entity count. A mismatch indicates
    orphaned SFIs (too few edges) or duplicate parent assignments (too many).

    Parameters
    ----------
    has_child_rels_count
        Total number of hasChild relationships across all exports.
    report
        The GraphValidationReport to append errors or info findings to.
    sfi_entity_count
        Total number of exported SFI entity rows (before deduplication).
    """

    if has_child_rels_count != sfi_entity_count:
        report.error(
            code="HAS_CHILD_COUNT_MISMATCH",
            message=(
                f"Expected {sfi_entity_count} hasChild relationship(s) for "
                f"{sfi_entity_count} exported SFI entity row(s); found "
                f"{has_child_rels_count}."
            ),
        )
    else:
        report.info(
            code="HAS_CHILD_COUNT_OK",
            message=(
                f"hasChild relationship count matches exported SFI count "
                f"({has_child_rels_count})."
            ),
        )


def _check_has_child_cycles(
    *,
    adj: dict[str, list[str]],
    fw_id: str,
    report: GraphValidationReport,
    sfi_ids: set[str],
) -> None:
    """Check for cycles in the hasChild relationship graph using iterative DFS.

    Uses the standard three-colour (WHITE/GRAY/BLACK) algorithm but with an explicit
    stack instead of recursion, so it is safe for arbitrarily deep hierarchies that
    would otherwise exceed Python's default recursion limit.

    Parameters
    ----------
    adj
        Adjacency list mapping source IDs to target IDs.
    fw_id
        The framework case identifier UUID.
    report
        The GraphValidationReport to append findings to.
    sfi_ids
        Set of all SFI case identifier UUIDs.
    """

    white, gray, black = 0, 1, 2
    color: dict[str, int] = {nid: white for nid in (sfi_ids | {fw_id})}

    for start in [fw_id] + sorted(sfi_ids):
        if color.get(start) != white:
            continue

        stack: list[tuple[str, int]] = [(start, 0)]
        color[start] = gray

        while stack:
            nid, idx = stack[-1]
            children = adj.get(nid, [])

            # Flatten logic by handling the exhausted iterator first.
            if idx >= len(children):
                color[nid] = black
                stack.pop()
                continue

            # Advance the child pointer for the current frame.
            stack[-1] = (nid, idx + 1)
            child = children[idx]

            # Single lookup handles both 'not in' and value retrieval.
            child_color = color.get(child)

            if child_color is None:
                continue

            if child_color == gray:
                # Cycle detected: Every node currently in the stack is gray by
                # definition of DFS. Because child is gray, it is guaranteed to be in
                # the stack.
                path_nodes = [n for n, _ in stack]
                cycle_path = path_nodes[path_nodes.index(child) :] + [child]

                report.error(
                    code="HAS_CHILD_CYCLE",
                    message=f"Cycle detected in exported hasChild graph: {' -> '.join(cycle_path)}",
                )
                return  # Early exit entirely

            if child_color == white:
                color[child] = gray
                stack.append((child, 0))

    # If the loops finish naturally, no cycles exist.
    report.info(
        code="HAS_CHILD_NO_CYCLES", message="No cycles in exported hasChild graph."
    )


def _check_has_child_reachability(
    *,
    adj: dict[str, list[str]],
    fw_id: str,
    report: GraphValidationReport,
    sfi_ids: set[str],
) -> None:
    """Check that all SFIs are reachable from the framework root.

    Parameters
    ----------
    adj
        Adjacency list mapping source IDs to target IDs.
    fw_id
        The framework case identifier UUID.
    report
        The GraphValidationReport to append findings to.
    sfi_ids
        Set of all SFI case identifier UUIDs.
    """

    visited: set[str] = set()
    stack = [fw_id]

    while stack:
        cur = stack.pop()

        if cur not in visited:
            visited.add(cur)
            stack.extend(n for n in adj.get(cur, []) if n not in visited)

    reachable_sfis = visited - {fw_id}
    unreachable = sfi_ids - reachable_sfis

    if unreachable:
        report.error(
            code="SFI_UNREACHABLE",
            message=(
                f"{len(unreachable)} SFIs unreachable from framework root. "
                f"Examples: {sorted(unreachable)[:10]}"
            ),
        )
    else:
        report.info(
            code="SFI_REACHABILITY_OK",
            message=f"All {len(sfi_ids)} SFIs reachable from framework root.",
        )


def _check_has_child_single_parent(
    *,
    all_rels: list[Relationship],
    fw_id: str,
    report: GraphValidationReport,
    sfi_ids: set[str],
) -> None:
    """Validate that hasChild relationships form a single-parent hierarchy.

    In a shape-preserving LC KG export, the hasChild graph should be a rooted tree:

    1. The framework root has no parent.
    2. Every StandardsFrameworkItem has exactly one parent (either the framework or
        another SFI).

    Parameters
    ----------
    all_rels
        List of all relationships across exports.
    fw_id
        Framework case identifier UUID (string).
    report
        GraphValidationReport to append findings to.
    sfi_ids
        Set of all SFI case identifier UUIDs (strings).
    """

    indegree: Counter[str] = Counter()

    for r in all_rels:
        if r.relationship_type != "hasChild":
            continue

        tgt = str(r.target_entity_value)
        indegree[tgt] += 1

        # The framework should never be the target of hasChild.
        if tgt == fw_id:
            report.error(
                code="FRAMEWORK_HAS_PARENT",
                message="Framework node has an incoming hasChild edge, which is invalid.",
            )

    missing_parent = sorted([nid for nid in sfi_ids if indegree.get(nid, 0) == 0])
    multiple_parents = sorted([nid for nid in sfi_ids if indegree.get(nid, 0) > 1])

    if missing_parent:
        sample = ", ".join(missing_parent[:5])
        report.error(
            code="SFI_MISSING_PARENT",
            message=(
                f"{len(missing_parent)} SFI(s) have no incoming hasChild edge (missing parent). "
                f"Sample: {sample}"
            ),
        )

    if multiple_parents:
        sample = ", ".join(multiple_parents[:5])
        report.error(
            code="SFI_MULTIPLE_PARENTS",
            message=(
                f"{len(multiple_parents)} SFI(s) have more than one incoming hasChild edge "
                f"(multiple parents). Sample: {sample}"
            ),
        )

    if not missing_parent and not multiple_parents and indegree.get(fw_id, 0) == 0:
        report.info(
            code="HAS_CHILD_SINGLE_PARENT_OK",
            message="All SFIs have exactly one parent in hasChild; framework has no parent.",
        )


def _check_hierarchy_order_consistency(
    *,
    academic_standards: AcademicStandardsExport,
    all_rels: list[Relationship],
    fw_id: str,
    report: GraphValidationReport,
    sfi_ids: set[str],
) -> None:
    """Validate the hierarchy-order artifact against exported hasChild relationships.

    The Academic Standards exporter emits both hasChild relationship edges and a
    separate hierarchy-order artifact (`academic_standards.order`) that records the
    ordered child list for each parent. This check ensures the two representations are
    mutually consistent by verifying five invariants:

    1. **No duplicate children**: Each parent's ordered child list contains no repeated
        IDs.
    2. **Known parents**: Every parent ID in the order map is either the framework root
        or an exported SFI.
    3. **Known children**: Every child ID in the order map corresponds to an exported
        SFI.
    4. **hasChild ⊆ order**: Every exported hasChild `(source, target)` pair has a
        matching entry in the order map.
    5. **order ⊆ hasChild**: Every `(parent, child)` pair in the order map has a
        corresponding exported hasChild relationship.

    Parameters
    ----------
    academic_standards
        The exported Academic Standards KG artifacts, including the hierarchy-order
        artifact at `academic_standards.order`.
    all_rels
        List of all relationships across exports.
    fw_id
        The framework entity's case identifier UUID (string).
    report
        The GraphValidationReport to append errors or info findings to.
    sfi_ids
        Set of all exported SFI case identifier UUIDs (strings).
    """

    has_child_pairs = {
        (_rel_src_id(r), _rel_tgt_id(r))
        for r in all_rels
        if r.relationship_type == "hasChild"
    }

    (
        ordered_pairs,
        duplicate_order_children,
        unknown_order_parents,
        unknown_order_children,
    ) = _scan_hierarchy_order(
        order_map=academic_standards.order.order or {},
        sfi_ids=sfi_ids,
        valid_parent_ids={fw_id} | sfi_ids,
    )

    missing_from_order = sorted(has_child_pairs - ordered_pairs)
    missing_from_relationships = sorted(ordered_pairs - has_child_pairs)

    # Each error descriptor: (items, code, context_builder, message_builder). Only
    # non-empty items trigger an error.
    error_descriptors: list[tuple[Any, str, dict[str, Any], str]] = [
        (
            duplicate_order_children,
            "HIERARCHY_ORDER_DUPLICATE_CHILD",
            {"examples": dict(list(duplicate_order_children.items())[:10])},
            (
                f"{len(duplicate_order_children)} hierarchy-order parent(s) contain "
                f"duplicate ordered child IDs."
            ),
        ),
        (
            unknown_order_parents,
            "HIERARCHY_ORDER_UNKNOWN_PARENT",
            {"examples": sorted(set(unknown_order_parents))[:10]},
            (
                f"{len(set(unknown_order_parents))} hierarchy-order parent ID(s) "
                f"do not correspond to the framework or an exported SFI."
            ),
        ),
        (
            unknown_order_children,
            "HIERARCHY_ORDER_UNKNOWN_CHILD",
            {"examples": sorted(set(unknown_order_children))[:10]},
            (
                f"{len(set(unknown_order_children))} hierarchy-order child ID(s) "
                f"do not correspond to exported SFIs."
            ),
        ),
        (
            missing_from_order,
            "HAS_CHILD_MISSING_FROM_HIERARCHY_ORDER",
            {"examples": missing_from_order[:10]},
            (
                f"{len(missing_from_order)} hasChild relationship pair(s) are missing "
                f"from academic_standards.order."
            ),
        ),
        (
            missing_from_relationships,
            "HIERARCHY_ORDER_MISSING_HAS_CHILD",
            {"examples": missing_from_relationships[:10]},
            (
                f"{len(missing_from_relationships)} hierarchy-order pair(s) have no "
                f"corresponding exported hasChild relationship."
            ),
        ),
    ]
    has_errors = False

    for items, code, context, message in error_descriptors:
        if items:
            has_errors = True
            report.error(code=code, context=context, message=message)

    if not has_errors:
        report.info(
            code="HIERARCHY_ORDER_CONSISTENT",
            message="Hierarchy order artifact matches exported hasChild relationships.",
        )


def _check_lc_supports(
    *,
    lc_ids: set[str],
    learning_components: LearningComponentsExport,
    report: GraphValidationReport,
) -> None:
    """Check that every Learning Component has exactly one supports relationship.

    Parameters
    ----------
    lc_ids
        Set of all Learning Component IDs.
    learning_components
        The exported Learning Components KG artifacts.
    report
        The GraphValidationReport to append findings to.
    """

    supports_count_per_lc: dict[str, int] = {lc_id: 0 for lc_id in lc_ids}

    for r in learning_components.supports_relationships:
        if r.relationship_type == "supports":
            src = _rel_src_id(r)

            if src in supports_count_per_lc:
                supports_count_per_lc[src] += 1

    lc_without_supports = {
        lc_id for lc_id, count in supports_count_per_lc.items() if count == 0
    }
    lc_multi_supports = {
        lc_id: count for lc_id, count in supports_count_per_lc.items() if count > 1
    }

    if lc_without_supports:
        report.error(
            code="LC_WITHOUT_SUPPORTS",
            message=(
                f"{len(lc_without_supports)} LearningComponent(s) have no supports "
                f"relationship. Examples: {sorted(lc_without_supports)[:5]}"
            ),
        )

    if lc_multi_supports:
        report.error(
            code="LC_MULTIPLE_SUPPORTS",
            message=(
                f"{len(lc_multi_supports)} LearningComponent(s) have more than one "
                f"supports relationship. "
                f"Examples: {dict(sorted(lc_multi_supports.items())[:5])}"
            ),
        )

    if not lc_without_supports and not lc_multi_supports:
        report.info(
            code="LC_SUPPORTS_OK",
            message=(
                f"All {len(lc_ids)} LearningComponents have exactly one "
                f"supports relationship."
            ),
        )


def _check_progression_invariants(
    *,
    learning_progressions: LearningProgressionsExport,
    report: GraphValidationReport,
    sfi_ids: set[str],
    standard_sfi_ids: set[str],
) -> None:
    """Check progression-specific semantic invariants.

    Parameters
    ----------
    learning_progressions
        The exported Learning Progressions KG artifacts.
    report
        The GraphValidationReport to append findings to.
    sfi_ids
        Set of all SFI case identifier UUIDs.
    standard_sfi_ids
        Set of Standard-type SFI case identifier UUIDs. Progression edges are expected
        to connect normative standards, not grouping or auxiliary SFIs.
    """

    all_prog_rels = list(learning_progressions.builds_towards_relationships) + list(
        learning_progressions.relates_to_relationships
    )

    # All progression endpoints must be SFIs.
    non_sfi_endpoints = sum(
        map(
            lambda r: _rel_src_id(r) not in sfi_ids or _rel_tgt_id(r) not in sfi_ids,
            all_prog_rels,
        )
    )

    if non_sfi_endpoints:
        report.error(
            code="PROGRESSION_NON_SFI_ENDPOINT",
            message=(
                f"{non_sfi_endpoints} progression relationship(s) reference "
                f"non-SFI entities."
            ),
        )
    else:
        report.info(
            code="PROGRESSION_ENDPOINTS_OK",
            message="All progression endpoints are SFIs.",
        )

    # Progression endpoints should be normative Standard SFIs, not grouping or
    # auxiliary SFIs. This mirrors the LP exporter source-selection policy and catches
    # accidental progression edges against hierarchy/grouping nodes.
    non_standard_endpoints = sum(
        map(
            lambda r: (
                _rel_src_id(r) not in standard_sfi_ids
                or _rel_tgt_id(r) not in standard_sfi_ids
            ),
            all_prog_rels,
        )
    )

    if non_standard_endpoints:
        report.error(
            code="PROGRESSION_ENDPOINT_NOT_STANDARD",
            message=(
                f"{non_standard_endpoints} progression relationship(s) reference "
                f"non-Standard SFI endpoints."
            ),
        )
    else:
        report.info(
            code="PROGRESSION_STANDARD_ENDPOINTS_OK",
            message="All progression endpoints are Standard-type SFIs.",
        )

    # No duplicate directed buildsTowards pairs (exact (source, target) repeats) and no
    # duplicate relationship identifiers within type.
    builds_pairs = [
        (_rel_src_id(r), _rel_tgt_id(r))
        for r in learning_progressions.builds_towards_relationships
    ]
    builds_ids = [
        str(r.identifier) for r in learning_progressions.builds_towards_relationships
    ]
    duplicate_builds_pairs = len(builds_pairs) - len(set(builds_pairs))
    duplicate_builds_ids = len(builds_ids) - len(set(builds_ids))

    if duplicate_builds_pairs:
        report.error(
            code="BUILDS_TOWARDS_DUPLICATE_PAIR",
            message=(
                f"{duplicate_builds_pairs} duplicate buildsTowards pair(s) detected "
                f"(identical directed edges)."
            ),
        )
    if duplicate_builds_ids:
        report.error(
            code="BUILDS_TOWARDS_DUPLICATE_IDS",
            message=(
                f"{duplicate_builds_ids} duplicate buildsTowards identifier(s) "
                f"detected (different pairs sharing the same relationship UUID)."
            ),
        )
    if not duplicate_builds_pairs and not duplicate_builds_ids:
        report.info(
            code="BUILDS_TOWARDS_NO_DUPLICATES",
            message="No duplicate buildsTowards pairs.",
        )

    # No duplicate relatesTo pairs (A, B) and (B, A) after canonicalization.
    relates_pairs = [
        canon_str_pair(_rel_src_id(r), _rel_tgt_id(r))
        for r in learning_progressions.relates_to_relationships
    ]
    relates_ids = [
        str(r.identifier) for r in learning_progressions.relates_to_relationships
    ]
    duplicate_relates_pairs = len(relates_pairs) - len(set(relates_pairs))
    duplicate_relates_ids = len(relates_ids) - len(set(relates_ids))

    if duplicate_relates_pairs:
        report.error(
            code="RELATES_TO_DUPLICATE_PAIR",
            message=(
                f"{duplicate_relates_pairs} duplicate relatesTo pair(s) detected "
                f"(same endpoints in different directions)."
            ),
        )
    if duplicate_relates_ids:
        report.error(
            code="RELATES_TO_DUPLICATE_IDS",
            message=(
                f"{duplicate_relates_ids} duplicate relatesTo identifier(s) "
                f"detected (different pairs sharing the same relationship UUID)."
            ),
        )
    if not duplicate_relates_pairs and not duplicate_relates_ids:
        report.info(
            code="RELATES_TO_NO_DUPLICATES", message="No duplicate relatesTo pairs."
        )

    # No overlap between buildsTowards and relatesTo pairs. Phase 4 uses
    # forbidden_builds_pairs to prevent this, but an exclusion bug could silently
    # produce both relationship types for the same SFI pair. Canonicalize buildsTowards
    # pairs to undirected form for a fair comparison.
    builds_pairs_canonical = {canon_str_pair(src, tgt) for src, tgt in builds_pairs}
    relates_pairs_canonical = set(relates_pairs)
    overlap = builds_pairs_canonical & relates_pairs_canonical

    if overlap:
        sample = sorted(overlap)[:5]
        report.error(
            code="BUILDS_RELATES_OVERLAP",
            message=(
                f"{len(overlap)} SFI pair(s) have both a buildsTowards and a "
                f"relatesTo relationship. This should not happen — Phase 4 "
                f"excludes buildsTowards pairs from relatesTo inference. "
                f"Examples: {sample}"
            ),
        )
    else:
        report.info(
            code="BUILDS_RELATES_DISJOINT",
            message="buildsTowards and relatesTo pairs are fully disjoint.",
        )


def _check_referential_integrity(
    *,
    all_entity_ids: set[str],
    all_rels: list[Relationship],
    report: GraphValidationReport,
) -> None:
    """Check for referential integrity across all relationships.

    Parameters
    ----------
    all_entity_ids
        Set of all valid entity IDs in the graph.
    all_rels
        List of all relationships across exports.
    report
        The GraphValidationReport to append findings to.
    """

    dangling_count = 0

    for r in all_rels:
        src = _rel_src_id(r)
        tgt = _rel_tgt_id(r)
        source_ok = src in all_entity_ids
        target_ok = tgt in all_entity_ids

        if not source_ok or not target_ok:
            dangling_count += 1

            if dangling_count <= 10:
                report.error(
                    code="DANGLING_ENDPOINT",
                    context={
                        "relationship_type": r.relationship_type,
                        "source": src,
                        "target": tgt,
                        "source_ok": source_ok,
                        "target_ok": target_ok,
                    },
                    message=(
                        f"{r.relationship_type} references missing entity: "
                        f"{src} -> {tgt}"
                    ),
                )

    if dangling_count == 0:
        report.info(
            code="REFERENTIAL_INTEGRITY_OK",
            message=f"All {len(all_rels)} relationships have valid endpoints.",
        )
    elif dangling_count > 10:
        report.error(
            code="DANGLING_ENDPOINT_OVERFLOW",
            message=f"{dangling_count} total dangling endpoints (showing first 10).",
        )


def _check_relationship_endpoint_types(
    *,
    all_rels: list[Relationship],
    fw_id: str,
    lc_ids: set[str],
    report: GraphValidationReport,
    sfi_ids: set[str],
) -> None:
    """Validate relationship endpoint entity types for each relationship_type.

    This is stricter than referential integrity: it ensures the *kinds* of nodes
    connected by each relationship match the LC KG ontology shape.

    Parameters
    ----------
    all_rels
        List of all relationships across exports.
    fw_id
        Framework case identifier UUID (string).
    lc_ids
        Set of all LC identifiers (string UUIDs).
    report
        GraphValidationReport to append findings to.
    sfi_ids
        Set of all SFI case identifier UUIDs (strings).
    """

    overall_ok = True

    for r in all_rels:
        rt = r.relationship_type

        if rt == "hasChild":
            rel_ok = _validate_has_child(
                fw_id=fw_id, r=r, report=report, sfi_ids=sfi_ids
            )
        elif rt == "supports":
            rel_ok = _validate_supports(
                lc_ids=lc_ids, r=r, report=report, sfi_ids=sfi_ids
            )
        elif rt in {"buildsTowards", "relatesTo"}:
            rel_ok = _validate_sfi_to_sfi(r=r, report=report, sfi_ids=sfi_ids)
        else:
            rel_ok = False
            report.error(
                code="UNKNOWN_RELATIONSHIP_TYPE",
                message=f"Unknown relationship_type encountered in exports: {rt}",
            )

        if not rel_ok:
            overall_ok = False

    if overall_ok:
        report.info(
            code="REL_ENDPOINT_TYPES_OK",
            message="All relationship endpoint types match expected ontology shapes.",
        )


def _check_self_loops(
    *, all_rels: list[Relationship], report: GraphValidationReport
) -> None:
    """Check for self-loop relationships in the graph.

    Parameters
    ----------
    all_rels
        List of all relationships across exports.
    report
        The GraphValidationReport to append findings to.
    """

    self_loops = [
        r
        for r in all_rels
        if r.source_entity == r.target_entity
        and r.source_entity_key == r.target_entity_key
        and _rel_src_id(r) == _rel_tgt_id(r)
    ]

    if self_loops:
        report.error(
            code="SELF_LOOP",
            message=f"{len(self_loops)} self-loop relationship(s) detected.",
        )
    else:
        report.info(
            code="NO_SELF_LOOPS",
            message="No self-loop relationships detected.",
        )


def _check_standards_presence(
    *, academic_standards: AcademicStandardsExport, report: GraphValidationReport
) -> None:
    """Check that at least one expectation SFI exists in the standards.

    Parameters
    ----------
    academic_standards
        The exported Academic Standards KG artifacts.
    report
        The GraphValidationReport to append findings to.
    """

    has_standard = any(
        sfi.normalized_statement_type == "Standard" for sfi in academic_standards.items
    )

    if not has_standard:
        report.error(
            code="NO_STANDARDS_EMITTED",
            message="No expectation SFIs emitted (normalized_statement_type='Standard').",
        )
    else:
        report.info(
            code="STANDARDS_PRESENT", message="At least one Standard SFI emitted."
        )


def _check_supports_targets_are_standards(
    *,
    academic_standards: AcademicStandardsExport,
    learning_components: LearningComponentsExport,
    report: GraphValidationReport,
) -> None:
    """Check that every supports relationship targets a Standard-type SFI.

    The generic referential integrity check ensures the target exists; this
    additionally verifies the target is an expectation (normalized_statement_type ==
    "Standard") rather than a grouping or other node type.

    Parameters
    ----------
    academic_standards
        The exported Academic Standards KG artifacts.
    learning_components
        The exported Learning Components KG artifacts.
    report
        The GraphValidationReport to append findings to.
    """

    standard_sfi_ids: set[str] = {
        str(sfi.case_identifier_uuid)
        for sfi in academic_standards.items
        if sfi.normalized_statement_type == "Standard"
    }

    non_standard_targets = 0
    examples: list[str] = []

    for r in learning_components.supports_relationships:
        if r.relationship_type == "supports":
            tgt = _rel_tgt_id(r)
            if tgt not in standard_sfi_ids:
                non_standard_targets += 1

                if len(examples) < 5:
                    examples.append(f"{_rel_src_id(r)} -> {tgt}")

    if non_standard_targets:
        report.error(
            code="SUPPORTS_TARGET_NOT_STANDARD",
            message=(
                f"{non_standard_targets} supports relationship(s) target a "
                f"non-Standard SFI (e.g., a grouping). Examples: {examples}"
            ),
        )
    else:
        report.info(
            code="SUPPORTS_TARGETS_OK",
            message="All supports relationships target Standard-type SFIs.",
        )


def _collect_columns_signatures(
    *, ctx: ExportContext, decision_ids: list[str]
) -> list[str]:
    """Collect unique columns_signature values from the given decision IDs.

    Parameters
    ----------
    ctx
        The KG export context providing access to decisions_by_id.
    decision_ids
        Source decision IDs to look up.

    Returns
    -------
    list[str]
        Unique, sorted columns_signature values found on the decisions.
    """

    sigs: set[str] = set()

    for did in decision_ids:
        dec = ctx.decisions_by_id.get(did)

        if dec:
            sig = dec.get("columns_signature")

            if isinstance(sig, str) and sig.strip():
                sigs.add(sig.strip())

    return sorted(sigs)


def _duplicate_counts(values: list[str]) -> dict[str, int]:
    """Return only the values that appear more than once, with their occurrence counts.

    This function is used by validation checks that need to detect and report duplicate
    identifiers (e.g., entity IDs or serialized relationship pairs).

    Parameters
    ----------
    values
        A list of string values to scan for duplicates.

    Returns
    -------
    dict[str, int]
        A sorted mapping of each duplicated value to its total occurrence count. Values
        that appear exactly once are excluded.
    """

    counts = Counter(values)
    return {value: count for value, count in sorted(counts.items()) if count > 1}


def _ensure_int_list(value: Any) -> list[int]:
    """Coerce a possibly-missing/ill-typed field into a list[int].

    Parameters
    ----------
    value
        The value to coerce, which may be None, a scalar, or a list/tuple/set.

    Returns
    -------
    list[int]
        A list of integers parsed from the input, with non-coercible values ignored.
    """

    if value is None:
        return []

    if isinstance(value, list):
        values = value
    elif isinstance(value, (tuple, set)):
        values = list(value)
    else:
        values = [value]

    out: list[int] = []

    for x in values:
        if x is None:
            continue
        try:
            out.append(int(x))
        except Exception:  # pylint: disable=broad-except
            # Ignore non-coercible values rather than failing reporting.
            continue

    return out


def _ensure_str_list(value: Any) -> list[str]:
    """Coerce a possibly-missing/ill-typed field into a list[str].

    Parameters
    ----------
    value
        The value to coerce, which may be None, a scalar, or a list/tuple/set.

    Returns
    -------
    list[str]
        A list of strings parsed from the input, with non-string values coerced to
        strings and None values ignored.
    """

    if value is None:
        return []

    if isinstance(value, list):
        return [str(x) for x in value if x is not None]

    if isinstance(value, (tuple, set)):
        return [str(x) for x in value if x is not None]

    # A single scalar (including str).
    return [str(value)]


def _entity_text_unit(*, language: Any, text: Any) -> dict[str, str] | None:
    """Build the minimal TextUnit-shaped payload used by EntityProvenance.text.

    The pipeline's canonical text units use dictionaries shaped like
    {"language": "...", "text": "...", "text_en": "..."}. For provenance rows, the
    display text is enough; translations/originals remain on the exported entity
    metadata.

    Parameters
    ----------
    language
        The language code for the text unit, which will be coerced to a string and
        default to "und" (undetermined) if missing or falsy.
    text
        The display text for the entity, which will be coerced to a string and default
        to None if missing or falsy after stripping whitespace.

    Returns
    -------
    dict[str, str] | None
        A minimal TextUnit-shaped dictionary with "language" and "text" keys, or None
        if the text is missing/empty. The "language" value defaults to "und" if not
        provided.
    """

    text_s = " ".join(str(text or "").split())

    if not text_s:
        return None

    language_s = str(language or "und").strip() or "und"
    return {"language": language_s, "text": text_s}


def _extract_dialect_fallbacks(*dicts: dict[str, Any] | None) -> dict[str, str]:
    """Extract dialect fallback metadata from one or more metadata dictionaries.

    Exporters may store this under `dialect_fallbacks` directly; this function is
    intentionally conservative and only copies explicit fallback dictionaries. Later
    dictionaries override earlier dictionaries for the same key.

    Parameters
    ----------
    *dicts
        One or more dictionaries to search for `dialect_fallbacks` metadata.

    Returns
    -------
    dict[str, str]
        A consolidated mapping of dialect fallback keys to values, with later
        dictionaries taking precedence over earlier ones. Only includes entries where
        both key and value are non-empty strings after coercion.
    """

    out: dict[str, str] = {}

    for dict_ in dicts:
        if not isinstance(dict_, dict):
            continue

        fallbacks = dict_.get("dialect_fallbacks")

        # Only process the fallbacks if it's explicitly a dictionary.
        if isinstance(fallbacks, dict):
            for key, val in fallbacks.items():
                key_s = str(key or "").strip()
                val_s = str(val or "").strip()

                # Only keep truthy (non-empty) keys and values.
                if key_s and val_s:
                    out[key_s] = val_s

    return out


def _node_display_label(*, ctx: ExportContext, node_id: str) -> str:
    """Return a compact display label for a canonical node.

    Parameters
    ----------
    ctx
        The KG export context providing access to nodes_by_id and kg_config.
    node_id
        The node ID to generate a display label for.

    Returns
    -------
    str
        A human-readable label for the node, derived from available text fields or
        falling back to the node ID if no suitable text is found. The label is
        compacted to a single line with normalized whitespace.
    """

    node = ctx.nodes_by_id.get(node_id) or {}
    prefer_text_en = ctx.kg_config.as_description_text_policy == "prefer_text_en"

    for field_name in ("title", "body"):
        unit = node.get(field_name)

        if not isinstance(unit, dict):
            continue

        text_en = str(unit.get("text_en") or "").strip()
        text = str(unit.get("text") or "").strip()

        if prefer_text_en and text_en:
            return " ".join(text_en.split())

        if text:
            return " ".join(text.split())

        if text_en:
            return " ".join(text_en.split())

    for key in ("normalized_text", "source_label", "role"):
        value = " ".join(str(node.get(key) or "").split())

        if value:
            return value

    return node_id


def _rel_src_id(r: Relationship) -> str:
    """Return a normalized string ID for the relationship source endpoint.

    Parameters
    ----------
    r
        The relationship whose source ID to extract.

    Returns
    -------
    str
        The source endpoint ID as a string.
    """

    return str(r.source_entity_value)


def _rel_tgt_id(r: Relationship) -> str:
    """Return a normalized string ID for the relationship target endpoint.

    Parameters
    ----------
    r
        The relationship whose target ID to extract.

    Returns
    -------
    str
        The target endpoint ID as a string.
    """

    return str(r.target_entity_value)


def _scan_hierarchy_order(
    *, order_map: dict[str, list[str]], sfi_ids: set[str], valid_parent_ids: set[str]
) -> tuple[set[tuple[str, str]], dict[str, dict[str, int]], list[str], list[str]]:
    """Traverse the hierarchy-order map and collect anomalies.

    Walks every `(parent, children)` entry in the order map once, detecting duplicate
    child IDs per parent, unknown parent IDs, and unknown child IDs. Also builds the
    complete set of `(parent, child)` pairs for downstream set-difference checks.

    Parameters
    ----------
    order_map
        The hierarchy-order mapping of parent IDs to ordered child-ID lists,
        sourced from `academic_standards.order.order`.
    sfi_ids
        Set of all exported SFI case identifier UUIDs (strings).
    valid_parent_ids
        Set of IDs that are allowed as parents (framework root + all SFI IDs).

    Returns
    -------
    tuple[set[tuple[str, str]], dict[str, dict[str, int]], list[str], list[str]]
        A 4-tuple of:
            - **ordered_pairs**: All `(parent, child)` string pairs found in the order
                map.
            - **duplicate_order_children**: Mapping of parent IDs to their duplicate
                child counts (only parents with duplicates).
            - **unknown_order_parents**: Parent IDs not present in `valid_parent_ids`.
            - **unknown_order_children**: Child IDs not present in `sfi_ids`.
    """

    duplicate_order_children: dict[str, dict[str, int]] = {}
    ordered_pairs: set[tuple[str, str]] = set()
    unknown_order_children: list[str] = []
    unknown_order_parents: list[str] = []

    for parent_id, child_ids in order_map.items():
        parent_s = str(parent_id)

        if parent_s not in valid_parent_ids:
            unknown_order_parents.append(parent_s)

        child_id_strings = [str(child_id) for child_id in child_ids or []]
        dupes = _duplicate_counts(child_id_strings)

        if dupes:
            duplicate_order_children[parent_s] = dupes

        for child_s in child_id_strings:
            if child_s not in sfi_ids:
                unknown_order_children.append(child_s)

            ordered_pairs.add((parent_s, child_s))

    return (
        ordered_pairs,
        duplicate_order_children,
        unknown_order_parents,
        unknown_order_children,
    )


def _section_path_text(*, ctx: ExportContext, node_id: str | None) -> list[str]:
    """Build root-to-node human-readable section path text from canonical ancestry.

    The returned path includes the node itself. Missing or unknown node IDs simply
    return an empty list so provenance export remains best-effort.

    Parameters
    ----------
    ctx
        The KG export context providing access to `nodes_by_id`, `parent_by_child`, and
        `root_id`.
    node_id
        The node ID to build a section path for.

    Returns
    -------
    list[str]
        A list of human-readable labels for the node and its ancestors up to the root,
        derived from available text fields or falling back to node IDs when necessary.
    """

    if not node_id or node_id not in ctx.nodes_by_id:
        return []

    chain: list[str] = []
    cur: str | None = node_id
    seen: set[str] = set()

    while cur and cur not in seen:
        seen.add(cur)
        chain.append(cur)

        if cur == ctx.root_id:
            break

        cur = ctx.parent_by_child.get(cur)

    chain.reverse()
    labels: list[str] = []
    seen_labels: set[str] = set()

    for nid in chain:
        label = _node_display_label(ctx=ctx, node_id=nid)

        if label and label not in seen_labels:
            labels.append(label)
            seen_labels.add(label)

    return labels


def _validate_has_child(
    *, fw_id: str, r: Relationship, report: GraphValidationReport, sfi_ids: set[str]
) -> bool:
    """Validate endpoint types and values for a 'hasChild' relationship.

    Parameters
    ----------
    fw_id
        Framework case identifier UUID (string).
    r
        The relationship object to validate.
    report
        GraphValidationReport to append findings to.
    sfi_ids
        Set of all SFI case identifier UUIDs (strings).

    Returns
    -------
    bool
        True if the relationship is valid, False otherwise.
    """

    ok = True
    src_t = r.source_entity
    tgt_t = r.target_entity
    src = str(r.source_entity_value)
    tgt = str(r.target_entity_value)

    if src_t not in {"StandardsFramework", "StandardsFrameworkItem"}:
        ok = False
        report.error(
            code="REL_ENDPOINT_TYPE_MISMATCH",
            message=f"hasChild source_entity must be StandardsFramework or StandardsFrameworkItem (got {src_t}).",
        )

    if tgt_t != "StandardsFrameworkItem":
        ok = False
        report.error(
            code="REL_ENDPOINT_TYPE_MISMATCH",
            message=f"hasChild target_entity must be StandardsFrameworkItem (got {tgt_t}).",
        )

    if src_t == "StandardsFramework" and src != fw_id:
        ok = False
        report.error(
            code="REL_ENDPOINT_VALUE_MISMATCH",
            message="hasChild source_entity is StandardsFramework but source_entity_value is not the framework ID.",
        )

    if src_t == "StandardsFrameworkItem" and src not in sfi_ids:
        ok = False
        report.error(
            code="REL_ENDPOINT_VALUE_MISMATCH",
            message="hasChild source_entity is StandardsFrameworkItem but source_entity_value is not an exported SFI ID.",
        )

    if tgt not in sfi_ids:
        ok = False
        report.error(
            code="REL_ENDPOINT_VALUE_MISMATCH",
            message="hasChild target_entity_value is not an exported SFI ID.",
        )

    return ok


def _validate_sfi_to_sfi(
    *, r: Relationship, report: GraphValidationReport, sfi_ids: set[str]
) -> bool:
    """Validate endpoint types and values for SFI-to-SFI relationships.

    Parameters
    ----------
    r
        The relationship object to validate (buildsTowards or relatesTo).
    report
        GraphValidationReport to append findings to.
    sfi_ids
        Set of all SFI case identifier UUIDs (strings).

    Returns
    -------
    bool
        True if the relationship is valid, False otherwise.
    """

    ok = True
    rt = r.relationship_type
    src_t = r.source_entity
    tgt_t = r.target_entity
    src = str(r.source_entity_value)
    tgt = str(r.target_entity_value)

    if src_t != "StandardsFrameworkItem" or tgt_t != "StandardsFrameworkItem":
        ok = False
        report.error(
            code="REL_ENDPOINT_TYPE_MISMATCH",
            message=(
                f"{rt} must connect StandardsFrameworkItem -> StandardsFrameworkItem "
                f"(got {src_t} -> {tgt_t})."
            ),
        )

    if src not in sfi_ids or tgt not in sfi_ids:
        ok = False
        report.error(
            code="REL_ENDPOINT_VALUE_MISMATCH",
            message=f"{rt} endpoints must be exported SFI IDs.",
        )

    return ok


def _validate_supports(
    *,
    lc_ids: set[str],
    r: Relationship,
    report: GraphValidationReport,
    sfi_ids: set[str],
) -> bool:
    """Validate endpoint types and values for a 'supports' relationship.

    Parameters
    ----------
    lc_ids
        Set of all LC identifiers (string UUIDs).
    r
        The relationship object to validate.
    report
        GraphValidationReport to append findings to.
    sfi_ids
        Set of all SFI case identifier UUIDs (strings).

    Returns
    -------
    bool
        True if the relationship is valid, False otherwise.
    """

    ok = True
    src_t = r.source_entity
    tgt_t = r.target_entity
    src = str(r.source_entity_value)
    tgt = str(r.target_entity_value)

    if src_t != "LearningComponent" or tgt_t != "StandardsFrameworkItem":
        ok = False
        report.error(
            code="REL_ENDPOINT_TYPE_MISMATCH",
            message=(
                "supports must connect LearningComponent -> StandardsFrameworkItem "
                f"(got {src_t} -> {tgt_t})."
            ),
        )

    if src not in lc_ids:
        ok = False
        report.error(
            code="REL_ENDPOINT_VALUE_MISMATCH",
            message="supports source_entity_value is not an exported LearningComponent ID.",
        )

    if tgt not in sfi_ids:
        ok = False
        report.error(
            code="REL_ENDPOINT_VALUE_MISMATCH",
            message="supports target_entity_value is not an exported SFI ID.",
        )

    return ok


def build_entity_provenance_export(
    *,
    academic_standards: AcademicStandardsExport,
    ctx: ExportContext,
    learning_components: LearningComponentsExport,
) -> EntityProvenanceExport:
    """Build a flat entity provenance lookup from all exported entities.

    This export covers *entity* provenance only: StandardsFramework, SFI, and LC nodes.
    Relationship provenance (e.g., buildsTowards/relatesTo confidence, rationale, phase
    metadata) is intentionally excluded since it lives on each Relationship's
    `metadata` dict and in the per-phase provenance artifacts
    (`learning_progressions_candidate_edges_provenance.json`). Downstream consumers
    needing relationship-level provenance should consult those artifacts or the graph
    bundle directly.

    The export is deliberately self-contained for review:
        - SFIs and LCs include table `columns_signature` values when recoverable from
            source decision IDs.
        - All rows include display text when available.
        - SFIs and LCs include root-to-source section path labels derived from
            canonical ancestry.
        - Explicit `dialect_fallbacks` metadata is preserved when exporters provide it.
        - LC rows preserve the supporting SFI code and source role/split policy context.

    Parameters
    ----------
    academic_standards
        The exported Academic Standards KG artifacts.
    ctx
        The KG export context (for looking up columns_signature on decisions).
    learning_components
        The exported Learning Components KG artifacts.

    Returns
    -------
    EntityProvenanceExport
        A flat lookup: export_id → canonical provenance fields.
    """

    entities: list[EntityProvenance] = []

    # Framework.
    fw = academic_standards.framework
    fw_meta = fw.metadata or {}
    fw_canonical_node_id = str(fw_meta.get("canonical_node_id") or ctx.root_id)

    entities.append(
        EntityProvenance(
            bbox=None,
            canonical_node_id=fw_canonical_node_id,
            columns_signatures=[],
            dialect_fallbacks=_extract_dialect_fallbacks(fw_meta),
            entity_identifier=fw.case_identifier_uuid,
            entity_type="StandardsFramework",
            local_code=None,
            page_indices=[],
            role="framework",
            section_path_text=_section_path_text(ctx=ctx, node_id=fw_canonical_node_id)
            or [str(fw.name)],
            source_decision_ids=[],
            source_segment_ids=[],
            text=_entity_text_unit(language=fw.in_language, text=fw.name),
        )
    )

    # SFIs.
    for sfi in academic_standards.items:
        meta = sfi.metadata or {}
        canonical_node_id = str(meta.get("canonical_node_id") or "")
        decision_ids = _ensure_str_list(meta.get("source_decision_ids"))
        segment_ids = _ensure_str_list(meta.get("source_segment_ids"))

        # Collect columns_signatures from all source decisions for this node.
        col_sigs = _collect_columns_signatures(ctx=ctx, decision_ids=decision_ids)

        entities.append(
            EntityProvenance(
                bbox=meta.get("bbox"),
                canonical_node_id=canonical_node_id,
                columns_signatures=col_sigs,
                dialect_fallbacks=_extract_dialect_fallbacks(meta),
                entity_identifier=str(sfi.case_identifier_uuid),
                entity_type="StandardsFrameworkItem",
                local_code=meta.get("local_code"),
                page_indices=_ensure_int_list(meta.get("page_indices")),
                role=meta.get("role") or "unknown",
                section_path_text=_section_path_text(
                    ctx=ctx, node_id=canonical_node_id
                ),
                source_decision_ids=decision_ids,
                source_segment_ids=segment_ids,
                text=_entity_text_unit(language=sfi.in_language, text=sfi.description),
            )
        )

    # LCs.
    for lc in learning_components.learning_components:
        meta = lc.metadata or {}
        prov = meta.get("provenance") or {}
        canonical_node_id = str(meta.get("canonical_node_id") or "")
        decision_ids = _ensure_str_list(prov.get("source_decision_ids"))
        segment_ids = _ensure_str_list(prov.get("source_segment_ids"))
        split_policy = str(meta.get("split_policy") or "unknown").strip() or "unknown"
        supporting_role = (
            str(meta.get("supporting_sfi_role") or "unknown").strip() or "unknown"
        )

        entities.append(
            EntityProvenance(
                bbox=prov.get("bbox"),
                canonical_node_id=canonical_node_id,
                columns_signatures=_collect_columns_signatures(
                    ctx=ctx, decision_ids=decision_ids
                ),
                dialect_fallbacks=_extract_dialect_fallbacks(meta, prov),
                entity_identifier=str(lc.identifier),
                entity_type="LearningComponent",
                local_code=meta.get("supporting_sfi_statement_code"),
                page_indices=_ensure_int_list(prov.get("page_indices")),
                role=(f"learning_component:{split_policy}:supports:{supporting_role}"),
                section_path_text=_section_path_text(
                    ctx=ctx, node_id=canonical_node_id
                ),
                source_decision_ids=decision_ids,
                source_segment_ids=segment_ids,
                text=_entity_text_unit(language=lc.in_language, text=lc.description),
            )
        )

    fw_metadata = ctx.get_framework_metadata() or {}

    return EntityProvenanceExport(
        doc_key=ctx.doc_key,
        entities=entities,
        pdf_name=str(fw_metadata.get("pdf_name") or ""),
    )


def build_policy_coverage_report(
    *,
    academic_standards: AcademicStandardsExport,
    ctx: ExportContext,
    learning_components: LearningComponentsExport,
    learning_progressions: Optional[LearningProgressionsExport] = None,
) -> PolicyCoverageReport:
    """Build the unified policy coverage report from all export phases.

    Parameters
    ----------
    academic_standards
        The exported Academic Standards KG artifacts (with drop_reasons).
    ctx
        The KG export context.
    learning_components
        The exported Learning Components KG artifacts (with lc_stats).
    learning_progressions
        The exported Learning Progressions KG artifacts (optional).

    Returns
    -------
    PolicyCoverageReport
        The aggregate report explaining what was emitted, dropped, and why.
    """

    def _count_exact_reasons(*reasons: str) -> int:
        """Count one or more exact drop-reason strings.

        Parameters
        ----------
        *reasons
            One or more exact drop reason strings to count in the report.

        Returns
        -------
        int
            The total count of drop reasons matching any of the supplied strings.
        """

        return sum(reason_counter.get(reason, 0) for reason in reasons)

    def _count_prefixed_reasons(*prefixes: str) -> int:
        """Count all drop reasons matching any supplied current-taxonomy prefix.

        Parameters
        ----------
        *prefixes
            One or more drop reason prefixes to match against the report's drop reasons.

        Returns
        -------
        int
            The total count of drop reasons that start with any of the supplied prefixes.
        """

        return sum(
            count
            for reason, count in reason_counter.items()
            if any(reason.startswith(prefix) for prefix in prefixes)
        )

    def _prefixed_reason_counts(prefix: str) -> dict[str, int]:
        """Build a grouped count map by stripping one current-taxonomy prefix.

        Parameters
        ----------
        prefix
            The prefix to strip from drop reasons for grouping.

        Returns
        -------
        dict[str, int]
            A mapping from reason suffix (the part after the prefix) to count, for all
            reasons that start with the given prefix.
        """

        return {
            reason_[len(prefix) :]: count_
            for reason_, count_ in sorted(reason_counter.items())
            if reason_.startswith(prefix)
        }

    drop_reasons = academic_standards.drop_reasons
    reason_counter: Counter[str] = Counter(drop_reasons.values())
    reparent_stats = academic_standards.reparent_stats or {}

    # These strings intentionally match the current Academic Standards exporter, whose
    # `_drop_reason()` helper emits `drop:<category>:<detail>` values.
    dropped_aux_attached_to_expectation = _count_exact_reasons(
        "drop:guidance_attached_to_expectation:attach_to_expectation_metadata",
        "drop:descriptor_attached_to_expectation:attach_to_expectation_metadata",
    )
    dropped_aux_descendants_suppressed = _count_exact_reasons(
        "drop:ancestor_attached_to_expectation_metadata"
    )
    dropped_due_to_expectation_metadata_attachment = (
        dropped_aux_attached_to_expectation + dropped_aux_descendants_suppressed
    )

    dropped_descriptor = _count_exact_reasons("drop:descriptor_handling:drop")
    dropped_guidance = _count_exact_reasons("drop:guidance_handling:drop")
    dropped_non_grouping_role = _count_prefixed_reasons("drop:non_grouping_role:")
    pruned_empty_groupings = _count_exact_reasons("drop:pruned_empty_grouping")

    dropped_by_decision_type = _prefixed_reason_counts("drop:segment_decision:")
    dropped_by_columns_signature = _prefixed_reason_counts("drop:columns_signature:")
    drop_reason_counts = dict(sorted(reason_counter.items()))

    known_exacts = {
        "drop:ancestor_attached_to_expectation_metadata",
        "drop:descriptor_attached_to_expectation:attach_to_expectation_metadata",
        "drop:guidance_attached_to_expectation:attach_to_expectation_metadata",
        "drop:guidance_handling:drop",
        "drop:descriptor_handling:drop",
        "drop:pruned_empty_grouping",
    }
    known_prefixes = (
        "drop:columns_signature:",
        "drop:non_grouping_role:",
        "drop:segment_decision:",
    )

    for reason, count in reason_counter.items():
        if reason not in known_exacts and not reason.startswith(known_prefixes):
            logger.warning(
                f"Unrecognized drop reason in policy report: {reason!r} "
                f"({count} node(s)). This may indicate a new drop category was added "
                f"upstream without a corresponding reporting handler. The reason is "
                f"still included in `drop_reason_counts`."
            )

    max_drop_details = 200
    total_drops = len(drop_reasons)
    drop_details = [
        {
            "drop_reason": reason,
            "canonical_node_id": node_id,
            "role": str(ctx.nodes_by_id.get(node_id, {}).get("role") or ""),
        }
        for node_id, reason in sorted(drop_reasons.items())[:max_drop_details]
    ]
    drop_details_truncated = total_drops > max_drop_details

    if drop_details_truncated:
        logger.info(
            f"Drop details truncated: showing {max_drop_details} of {total_drops} "
            f"dropped nodes in policy_coverage_report.json."
        )

    lc_stats = learning_components.lc_stats or {}
    source_eligibility_summary = lc_stats.get("source_eligibility_summary") or {}
    _val = source_eligibility_summary.get("reason_counts")
    source_reason_counts = (
        dict(sorted((str(k), v) for k, v in _val.items() if str(k) != "eligible"))
        if isinstance(_val, dict)
        else {}
    )
    total_lc_source_sfis_eligible = lc_stats.get(
        "total_lc_source_sfis_eligible",
        source_eligibility_summary.get("eligible_sfis", 0),
    )

    # `learning_progressions.report` is already the detailed LP reporting artifact. The
    # policy coverage report keeps compact summary fields plus the highest-value
    # policy/config summaries so users do not need to open a second file for first-pass
    # diagnosis.
    lp_report = learning_progressions.report if learning_progressions else {}
    p_stats = (lp_report.get("counts") or {}) if learning_progressions else {}
    lp_drops = (lp_report.get("drops") or {}) if learning_progressions else {}
    lp_phase_toggles = (
        (lp_report.get("phase_toggles") or {}) if learning_progressions else {}
    )
    lp_thresholds = (lp_report.get("thresholds") or {}) if learning_progressions else {}
    lc_splits_distribution = {
        str(split_count): count
        for split_count, count in (lc_stats.get("splits_distribution") or {}).items()
    }

    return PolicyCoverageReport(
        doc_key=ctx.doc_key,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        pdf_name=str((ctx.get_framework_metadata() or {}).get("pdf_name") or ""),
        # Node-level drop accounting.
        drop_reason_counts=drop_reason_counts,
        dropped_aux_attached_to_expectation=dropped_aux_attached_to_expectation,
        dropped_aux_descendants_suppressed=dropped_aux_descendants_suppressed,
        dropped_due_to_expectation_metadata_attachment=(
            dropped_due_to_expectation_metadata_attachment
        ),
        dropped_by_columns_signature=dropped_by_columns_signature,
        dropped_by_decision_type=dropped_by_decision_type,
        dropped_descriptor=dropped_descriptor,
        dropped_guidance=dropped_guidance,
        dropped_non_grouping_role=dropped_non_grouping_role,
        pruned_empty_groupings=pruned_empty_groupings,
        # Subtract 1 for the canonical root node, which becomes a StandardsFramework
        # entity rather than an SFI. This assumes a single root per canonical IR
        # (framework_scope == "per_pdf").
        total_canonical_nodes=len(ctx.nodes_by_id) - 1,
        total_emitted_sfis=len(academic_standards.items),
        # Aux reparenting/attachment.
        attach_only_newly_attached_aux_node_count=(
            reparent_stats.get("attach_only_newly_attached_aux_node_count", 0)
        ),
        child_layout_aux_attached_count=(
            reparent_stats.get("child_layout_aux_attached_count", 0)
        ),
        orphan_aux_count=reparent_stats.get("orphan_aux_node_count", 0),
        sibling_aux_reparented_count=(
            reparent_stats.get("sibling_aux_reparented_count", 0)
        ),
        total_attached_aux_node_count=(
            reparent_stats.get("attached_aux_node_count", 0)
        ),
        attached_aux_subtree_root_count=(
            reparent_stats.get("attached_aux_subtree_root_count", 0)
        ),
        dropped_parents_processed=(reparent_stats.get("dropped_parents_processed", 0)),
        dropped_parents_removed_from_parent_lists_count=(
            reparent_stats.get("dropped_parents_removed_from_parent_lists_count", 0)
        ),
        reattach_appended_without_anchor_order_count=(
            reparent_stats.get("reattach_appended_without_anchor_order_count", 0)
        ),
        reattach_original_sibling_fallback_count=(
            reparent_stats.get("reattach_original_sibling_fallback_count", 0)
        ),
        reattached_children_count=(reparent_stats.get("reattached_children_count", 0)),
        removed_dropped_parent_reference_list_count=(
            reparent_stats.get("removed_dropped_parent_reference_list_count", 0)
        ),
        suppressed_attached_aux_descendant_count=(
            reparent_stats.get("suppressed_attached_aux_descendant_count", 0)
        ),
        suppressed_attached_aux_node_count=(
            reparent_stats.get("suppressed_attached_aux_node_count", 0)
        ),
        # LC stats.
        lc_fallback_sfis_count=lc_stats.get("fallback_sfis_count", 0),
        lc_max_splits_observed=lc_stats.get("max_splits_observed", 0),
        lc_source_exclusion_reason_counts=source_reason_counts,
        lc_split_policy=str(lc_stats.get("split_policy", "")),
        lc_splits_distribution=lc_splits_distribution,
        total_lc_source_sfis_considered=lc_stats.get(
            "total_lc_source_sfis_considered",
            source_eligibility_summary.get("total_sfis_considered", 0),
        ),
        total_lc_source_sfis_eligible=total_lc_source_sfis_eligible,
        total_lc_source_sfis_empty_text=(
            lc_stats.get("total_lc_source_sfis_empty_text", 0)
        ),
        total_lc_source_sfis_excluded=lc_stats.get(
            "total_lc_source_sfis_excluded",
            source_eligibility_summary.get("excluded_sfis", 0),
        ),
        total_lcs=lc_stats.get("total_lcs", 0),
        # Progression stats.
        lp_bucket_drop_counts=lp_drops,
        lp_candidate_builds_towards=p_stats.get("candidate_builds_towards", 0),
        lp_candidate_edges_after_dedupe=p_stats.get(
            "candidate_edges_total_after_dedupe", 0
        ),
        lp_candidate_edges_pre_dedupe=p_stats.get(
            "candidate_edges_total_pre_dedupe", 0
        ),
        lp_candidate_relates_to=p_stats.get("candidate_relates_to", 0),
        lp_dropped_cap_relates=p_stats.get("relates_dropped_cap", 0),
        lp_dropped_dedupe=p_stats.get("candidate_edges_dropped_dedupe", 0),
        lp_dropped_doc_order_builds=p_stats.get("builds_dropped_doc_order", 0),
        lp_dropped_low_conf_builds=p_stats.get("builds_dropped_low_conf", 0),
        lp_dropped_low_conf_relates=p_stats.get("relates_dropped_low_conf", 0),
        lp_kept_builds_towards=p_stats.get("builds_kept", 0),
        lp_kept_builds_towards_before_doc_order=p_stats.get(
            "builds_kept_before_doc_order", 0
        ),
        lp_kept_relates_to=p_stats.get("relates_kept_after_cap", 0),
        lp_kept_relates_to_after_threshold=p_stats.get(
            "relates_kept_after_threshold", 0
        ),
        lp_phase_toggles=lp_phase_toggles,
        lp_thresholds=lp_thresholds,
        # Drop details.
        drop_details=drop_details,
        drop_details_limit=max_drop_details,
        drop_details_total_count=total_drops,
        drop_details_truncated=drop_details_truncated,
    )


def log_console_summary(  # pylint: disable=R0912, R1260
    *, policy_report: PolicyCoverageReport, validation_report: GraphValidationReport
) -> None:
    """Log a concise console summary of the reports.

    Parameters
    ----------
    policy_report
        The policy coverage report.
    validation_report
        The graph validation report.
    """

    logger.info("=" * 60)
    logger.info("KG EXPORT SUMMARY")
    logger.info("=" * 60)

    # Policy coverage.
    logger.info(
        f"Canonical nodes: {policy_report.total_canonical_nodes} | "
        f"Emitted SFIs: {policy_report.total_emitted_sfis}"
    )

    # NB: total_dropped is approximate: it includes the framework root node (which
    # becomes a StandardsFramework, not an SFI), pruned empty groupings, and nodes
    # reclassified as expectation metadata. The per-category breakdown below is the
    # authoritative accounting.
    total_dropped = (
        policy_report.total_canonical_nodes - policy_report.total_emitted_sfis
    )

    if total_dropped > 0:
        logger.info(f"Total dropped (approx): {total_dropped}")

        # Consolidate dictionary-based dropped stats.
        dict_stats = [
            ("segment decision", policy_report.dropped_by_decision_type),
            ("columns_signature", policy_report.dropped_by_columns_signature),
        ]

        for label, drop_dict in dict_stats:
            for key, count in sorted(drop_dict.items()):
                logger.info(f"  - {label} ({key}): {count}")

        # Consolidate scalar dropped stats.
        scalar_stats = [
            ("guidance (drop)", policy_report.dropped_guidance),
            ("descriptor (drop)", policy_report.dropped_descriptor),
            ("non-grouping role (drop)", policy_report.dropped_non_grouping_role),
            (
                "attached aux dropped into expectation metadata",
                policy_report.dropped_aux_attached_to_expectation,
            ),
            (
                "descendants suppressed below attached aux",
                policy_report.dropped_aux_descendants_suppressed,
            ),
            ("pruned empty groupings", policy_report.pruned_empty_groupings),
        ]

        for label, count in filter(lambda stat: stat[1] > 0, scalar_stats):
            logger.info(f"  - {label}: {count}")

    # LC stats.
    logger.info(
        f"LCs: {policy_report.total_lcs} from "
        f"{policy_report.total_lc_source_sfis_eligible} eligible LC-source SFI(s) "
        f"(policy: {policy_report.lc_split_policy})"
    )

    if policy_report.total_lc_source_sfis_excluded > 0:
        logger.info(
            f"  LC source filtering: {policy_report.total_lc_source_sfis_eligible} "
            f"eligible/{policy_report.total_lc_source_sfis_considered} considered "
            f"({policy_report.total_lc_source_sfis_excluded} excluded)"
        )

    if policy_report.total_lc_source_sfis_empty_text > 0:
        logger.info(
            f"  Empty-text LC sources: {policy_report.total_lc_source_sfis_empty_text}"
        )

    if policy_report.lc_fallback_sfis_count > 0:
        logger.info(f"  LC fallback SFIs: {policy_report.lc_fallback_sfis_count}")

    if policy_report.lc_max_splits_observed > 1:
        logger.info(
            f"  Max splits observed: {policy_report.lc_max_splits_observed} | "
            f"Distribution: {policy_report.lc_splits_distribution}"
        )

    # LP stats.
    if policy_report.progression_candidate_edges_after_dedupe > 0:
        logger.info(
            f"Progressions: {policy_report.progression_candidate_edges_pre_dedupe} raw candidates "
            f"({policy_report.progression_candidate_edges_after_dedupe} after dedupe) → "
            f"{policy_report.progression_kept_builds_towards} buildsTowards + "
            f"{policy_report.progression_kept_relates_to} relatesTo kept"
        )

        total_dropped = (
            policy_report.progression_dropped_dedupe
            + policy_report.progression_dropped_doc_order_builds
            + policy_report.progression_dropped_low_conf_builds
            + policy_report.progression_dropped_low_conf_relates
            + policy_report.progression_dropped_cap_relates
        )

        if total_dropped > 0:
            logger.info(
                f"  Dropped: {policy_report.progression_dropped_dedupe} dedupe + "
                f"{policy_report.progression_dropped_doc_order_builds} buildsTowards "
                f"(doc order) + {policy_report.progression_dropped_low_conf_builds} "
                f"buildsTowards (low conf) + "
                f"{policy_report.progression_dropped_low_conf_relates} "
                f"relatesTo (low conf) + "
                f"{policy_report.progression_dropped_cap_relates} "
                f"relatesTo (per-SFI cap)"
            )

    # Validation.
    errors = validation_report.errors()

    if not errors:
        logger.info("Validation: PASSED")
    else:
        logger.error(f"Validation: FAILED ({len(errors)} error(s))")

        for issue in errors[:10]:
            logger.error(f"  [{issue.code}] {issue.message}")

    logger.info("=" * 60)


def validate_graph(
    *,
    academic_standards: AcademicStandardsExport,
    ctx: ExportContext,
    learning_components: LearningComponentsExport,
    learning_progressions: Optional[LearningProgressionsExport] = None,
) -> GraphValidationReport:
    """Run structural integrity checks across all export phases.

    Checks performed:

    1. Entity ID uniqueness within and across framework/SFI/LC entity types.
    2. Referential integrity: all relationship endpoints reference existing entities.
    3. Relationship endpoint ontology shape by relationship type.
    4. hasChild hierarchy constraints: single parent, no cycles, reachability, count,
        duplicate pair detection, and hierarchy-order consistency.
    5. LearningComponent constraints: every LC has exactly one supports edge, supports
        targets are Standard-type SFIs, and duplicate supports pairs are detected.
    6. Standards presence: at least one Standard SFI is emitted.
    7. Self-loop and duplicate relationship identifier checks.
    8. Learning Progression invariants when progressions are generated: endpoints are
        Standard SFIs, buildsTowards/relatesTo duplicate checks, and disjointness.

    Parameters
    ----------
    academic_standards
        The exported Academic Standards KG artifacts.
    ctx
        The KG export context.
    learning_components
        The exported Learning Components KG artifacts.
    learning_progressions
        The exported Learning Progressions KG artifacts (optional).

    Returns
    -------
    GraphValidationReport
        The validation report with errors and check results.
    """

    report = GraphValidationReport(doc_key=ctx.doc_key)

    # Build raw entity ID lists first so validation can detect duplicates within a
    # single entity type before constructing lookup sets for referential integrity.
    fw_id = str(academic_standards.framework.case_identifier_uuid)
    sfi_id_list = [str(sfi.case_identifier_uuid) for sfi in academic_standards.items]
    lc_id_list = [str(lc.identifier) for lc in learning_components.learning_components]
    sfi_ids = set(sfi_id_list)
    lc_ids = set(lc_id_list)
    standard_sfi_ids = {
        str(sfi.case_identifier_uuid)
        for sfi in academic_standards.items
        if sfi.normalized_statement_type == "Standard"
    }
    all_entity_ids = _check_entity_id_uniqueness(
        fw_id=fw_id, lc_id_list=lc_id_list, report=report, sfi_id_list=sfi_id_list
    )

    # Collect all relationships across exports.
    all_rels = list(academic_standards.relationships)
    all_rels.extend(learning_components.supports_relationships)

    if learning_progressions:
        all_rels.extend(learning_progressions.builds_towards_relationships)
        all_rels.extend(learning_progressions.relates_to_relationships)

    # Execute isolated validation checks.
    _check_referential_integrity(
        all_entity_ids=all_entity_ids, all_rels=all_rels, report=report
    )
    _check_relationship_endpoint_types(
        all_rels=all_rels, fw_id=fw_id, lc_ids=lc_ids, report=report, sfi_ids=sfi_ids
    )
    _check_duplicate_relationship_pairs(
        all_rels=all_rels, relationship_type="hasChild", report=report
    )
    _check_duplicate_relationship_pairs(
        all_rels=all_rels, relationship_type="supports", report=report
    )
    _check_has_child_single_parent(
        all_rels=all_rels, fw_id=fw_id, report=report, sfi_ids=sfi_ids
    )
    _check_lc_supports(
        lc_ids=lc_ids, learning_components=learning_components, report=report
    )
    _check_supports_targets_are_standards(
        academic_standards=academic_standards,
        learning_components=learning_components,
        report=report,
    )
    adj = _build_has_child_adjacency(all_rels=all_rels)
    _check_has_child_cycles(adj=adj, fw_id=fw_id, report=report, sfi_ids=sfi_ids)

    _check_has_child_reachability(adj=adj, fw_id=fw_id, report=report, sfi_ids=sfi_ids)

    _check_has_child_count(
        has_child_rels_count=sum(
            1 for r in all_rels if r.relationship_type == "hasChild"
        ),
        report=report,
        sfi_entity_count=len(sfi_id_list),
    )
    _check_hierarchy_order_consistency(
        academic_standards=academic_standards,
        all_rels=all_rels,
        fw_id=fw_id,
        report=report,
        sfi_ids=sfi_ids,
    )

    _check_standards_presence(academic_standards=academic_standards, report=report)

    _check_self_loops(all_rels=all_rels, report=report)

    # Duplicate relationship identifiers.
    rel_ids = [str(r.identifier) for r in all_rels]
    unique_rel_ids = set(rel_ids)

    if len(rel_ids) != len(unique_rel_ids):
        dup_count = len(rel_ids) - len(unique_rel_ids)
        report.error(
            code="DUPLICATE_RELATIONSHIP_IDS",
            message=(
                f"{dup_count} duplicate relationship identifier(s) detected "
                f"across all exports."
            ),
        )
    else:
        report.info(
            code="RELATIONSHIP_IDS_UNIQUE",
            message=f"All {len(rel_ids)} relationship identifiers are unique.",
        )

    if learning_progressions:
        _check_progression_invariants(
            learning_progressions=learning_progressions,
            report=report,
            sfi_ids=sfi_ids,
            standard_sfi_ids=standard_sfi_ids,
        )

    # Finalize summary statistics.
    has_child_rels_count = sum(1 for r in all_rels if r.relationship_type == "hasChild")
    report.stats = {
        "builds_towards_count": (
            len(learning_progressions.builds_towards_relationships)
            if learning_progressions
            else 0
        ),
        "errors": len(report.errors()),
        "has_child_count": has_child_rels_count,
        "relates_to_count": (
            len(learning_progressions.relates_to_relationships)
            if learning_progressions
            else 0
        ),
        "supports_count": sum(1 for r in all_rels if r.relationship_type == "supports"),
        "total_entities": len(all_entity_ids),
        "total_entity_rows": 1 + len(sfi_id_list) + len(lc_id_list),
        "total_lc_rows": len(lc_id_list),
        "total_lcs": len(lc_ids),
        "total_relationships": len(all_rels),
        "total_sfi_rows": len(sfi_id_list),
        "total_sfis": len(sfi_ids),
        "total_standard_sfis": len(standard_sfi_ids),
    }

    return report


def write_reports(
    *,
    entity_provenance: EntityProvenanceExport,
    kg_dirs: KGDirs,
    policy_report: PolicyCoverageReport,
    validation_report: GraphValidationReport,
) -> None:
    """Write all report artifacts to the KG output directories.

    Parameters
    ----------
    entity_provenance
        The entity provenance export.
    kg_dirs
        The KG run directories.
    policy_report
        The policy coverage report.
    validation_report
        The graph validation report.
    """

    write_to_json(
        fp=kg_dirs.root / "policy_coverage_report.json",
        json_info=policy_report.model_dump(mode="json"),
    )
    write_to_json(
        fp=kg_dirs.root / "entity_provenance.json",
        json_info=entity_provenance.model_dump(mode="json"),
    )
    write_to_json(
        fp=kg_dirs.root / "graph_validation_report.json",
        json_info=validation_report.model_dump(mode="json"),
    )
