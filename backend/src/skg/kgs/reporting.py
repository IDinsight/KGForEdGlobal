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


def _build_has_child_adjacency(all_rels: list[Any]) -> dict[str, list[str]]:
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
            adj.setdefault(r.source_entity_value, []).append(r.target_entity_value)

    return adj


def _check_has_child_single_parent(
    *, all_rels: list[Any], fw_id: str, report: GraphValidationReport, sfi_ids: set[str]
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


def _check_has_child_cycles(
    *,
    adj: dict[str, list[str]],
    fw_id: str,
    report: GraphValidationReport,
    sfi_ids: set[str],
) -> None:
    """Check for cycles in the hasChild relationship graph using DFS.

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

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {nid: WHITE for nid in (sfi_ids | {fw_id})}
    has_cycle = False

    def _dfs(nid: str) -> bool:
        """Return True if a cycle is detected starting from nid.

        Parameters
        ----------
        nid
            The node ID to start DFS from.

        Returns
        -------
        bool
            True if a cycle is detected, False otherwise.
        """

        nonlocal has_cycle
        color[nid] = GRAY

        for child in adj.get(nid, []):
            if child not in color:
                continue

            if color[child] == GRAY:
                has_cycle = True
                return True

            if color[child] == WHITE and _dfs(child):
                return True

        color[nid] = BLACK
        return False

    for start in [fw_id] + sorted(sfi_ids):
        if color.get(start) == WHITE:
            if _dfs(start):
                break  # Early exit if cycle is found

    if has_cycle:
        report.error(
            code="HAS_CHILD_CYCLE",
            message="Cycle detected in exported hasChild graph.",
        )
    else:
        report.info(
            code="HAS_CHILD_NO_CYCLES",
            message="No cycles in exported hasChild graph.",
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
            src = r.source_entity_value

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
    """

    all_prog_rels = list(learning_progressions.builds_towards_relationships) + list(
        learning_progressions.relates_to_relationships
    )

    # All progression endpoints must be SFIs.
    non_sfi_endpoints = 0

    for r in all_prog_rels:
        if r.source_entity_value not in sfi_ids or r.target_entity_value not in sfi_ids:
            non_sfi_endpoints += 1

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

    # No duplicate directed buildsTowards pairs (exact (source, target) repeats) and no
    # duplicate relationship identifiers within type.
    builds_pairs = [
        (r.source_entity_value, r.target_entity_value)
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
        canon_str_pair(r.source_entity_value, r.target_entity_value)
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


def _check_referential_integrity(
    *, all_entity_ids: set[str], all_rels: list[Any], report: GraphValidationReport
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
        source_ok = r.source_entity_value in all_entity_ids
        target_ok = r.target_entity_value in all_entity_ids

        if not source_ok or not target_ok:
            dangling_count += 1

            if dangling_count <= 10:
                report.error(
                    code="DANGLING_ENDPOINT",
                    context={
                        "relationship_type": r.relationship_type,
                        "source": r.source_entity_value,
                        "target": r.target_entity_value,
                        "source_ok": source_ok,
                        "target_ok": target_ok,
                    },
                    message=(
                        f"{r.relationship_type} references missing entity: "
                        f"{r.source_entity_value} -> {r.target_entity_value}"
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


def _check_self_loops(*, all_rels: list[Any], report: GraphValidationReport) -> None:
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
        and r.source_entity_value == r.target_entity_value
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
    *, academic_standards: Any, report: GraphValidationReport
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
    *, academic_standards: Any, learning_components: Any, report: GraphValidationReport
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
            if r.target_entity_value not in standard_sfi_ids:
                non_standard_targets += 1

                if len(examples) < 5:
                    examples.append(
                        f"{r.source_entity_value} -> {r.target_entity_value}"
                    )

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

    entities.append(
        EntityProvenance(
            bbox=None,
            canonical_node_id=str(fw_meta.get("canonical_node_id") or ctx.root_id),
            columns_signatures=[],
            entity_identifier=fw.case_identifier_uuid,
            entity_type="StandardsFramework",
            local_code=None,
            page_indices=[],
            role="framework",
            source_decision_ids=[],
            source_segment_ids=[],
        )
    )

    # SFIs.
    for sfi in academic_standards.items:
        meta = sfi.metadata or {}
        decision_ids = meta.get("source_decision_ids", [])

        # Collect columns_signatures from all source decisions for this node.
        col_sigs = _collect_columns_signatures(ctx=ctx, decision_ids=decision_ids)

        entities.append(
            EntityProvenance(
                bbox=meta.get("bbox"),
                canonical_node_id=str(meta.get("canonical_node_id") or ""),
                columns_signatures=col_sigs,
                entity_identifier=sfi.case_identifier_uuid,
                entity_type="StandardsFrameworkItem",
                local_code=meta.get("local_code"),
                page_indices=meta.get("page_indices", []),
                role=meta.get("role") or "unknown",
                source_decision_ids=decision_ids,
                source_segment_ids=meta.get("source_segment_ids", []),
            )
        )

    # LCs.
    for lc in learning_components.learning_components:
        meta = lc.metadata or {}
        prov = meta.get("provenance") or {}

        entities.append(
            EntityProvenance(
                bbox=prov.get("bbox"),
                canonical_node_id=str(meta.get("canonical_node_id") or ""),
                columns_signatures=[],
                entity_identifier=lc.identifier,
                entity_type="LearningComponent",
                local_code=None,
                page_indices=prov.get("page_indices", []),
                role="learning_component",
                source_decision_ids=prov.get("source_decision_ids", []),
                source_segment_ids=prov.get("source_segment_ids", []),
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

    drop_reasons = academic_standards.drop_reasons
    reparent_stats = academic_standards.reparent_stats
    reason_counter: Counter[str] = Counter(drop_reasons.values())

    dropped_guidance = reason_counter.get("dropped:guidance_handling:drop", 0)
    dropped_descriptor = reason_counter.get("dropped:descriptor_handling:drop", 0)
    dropped_non_grouping_role = reason_counter.get("dropped:non_grouping_role:drop", 0)

    dropped_by_decision_type = {
        r.split(":", 2)[2]: c
        for r, c in reason_counter.items()
        if r.startswith("dropped:segment_decision:")
    }
    dropped_by_columns_signature = {
        r.split(":", 2)[2]: c
        for r, c in reason_counter.items()
        if r.startswith("dropped:columns_signature:")
    }

    known_exacts = {
        "dropped:guidance_handling:drop",
        "dropped:descriptor_handling:drop",
        "dropped:non_grouping_role:drop",
        "dropped:pruned_empty_grouping",
        "dropped:attach_to_expectation_metadata",
    }
    known_prefixes = ("dropped:segment_decision:", "dropped:columns_signature:")

    for reason, count in reason_counter.items():
        if reason not in known_exacts and not reason.startswith(known_prefixes):
            logger.warning(
                f"Unrecognized drop reason in policy report: {reason!r} "
                f"({count} node(s)). This may indicate a new drop category was added "
                f"upstream without a corresponding reporting handler."
            )

    max_drop_details = 200
    total_drops = len(drop_reasons)
    drop_details = [
        {
            "canonical_node_id": node_id,
            "role": str(ctx.nodes_by_id.get(node_id, {}).get("role") or ""),
            "drop_reason": reason,
        }
        for node_id, reason in sorted(drop_reasons.items())[:max_drop_details]
    ]

    if total_drops > max_drop_details:
        logger.info(
            f"Drop details truncated: showing {max_drop_details} of {total_drops} "
            f"dropped nodes in policy_coverage_report.json."
        )

    lc_stats = learning_components.lc_stats
    p_stats = (
        (learning_progressions.report.get("counts") or {})
        if learning_progressions
        else {}
    )

    return PolicyCoverageReport(
        doc_key=ctx.doc_key,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        pdf_name=str((ctx.get_framework_metadata() or {}).get("pdf_name") or ""),
        # Node-level drop accounting
        dropped_attach_to_expectation=reason_counter.get(
            "dropped:attach_to_expectation_metadata", 0
        ),
        dropped_by_columns_signature=dropped_by_columns_signature,
        dropped_by_decision_type=dropped_by_decision_type,
        dropped_descriptor=dropped_descriptor,
        dropped_guidance=dropped_guidance,
        dropped_non_grouping_role=dropped_non_grouping_role,
        pruned_empty_groupings=reason_counter.get("dropped:pruned_empty_grouping", 0),
        total_canonical_nodes=len(ctx.nodes_by_id) - 1,
        total_emitted_sfis=len(academic_standards.items),
        # Aux reparenting
        aux_reparented_count=reparent_stats.get("aux_reparented_count", 0),
        orphan_aux_count=reparent_stats.get("orphan_aux_count", 0),
        # LC stats
        lc_max_splits_observed=int(lc_stats.get("max_splits_observed", 0)),
        lc_split_policy=str(lc_stats.get("split_policy", "")),
        lc_splits_distribution=lc_stats.get("splits_distribution", {}),
        total_expectations=int(lc_stats.get("total_expectations", 0)),
        total_lcs=int(lc_stats.get("total_lcs", 0)),
        # Progression stats
        progression_candidate_edges=int(
            p_stats.get("candidate_edges_total_after_dedupe", 0)
        ),
        progression_dropped_cap_relates=int(p_stats.get("relates_dropped_cap", 0)),
        progression_dropped_low_conf_builds=int(
            p_stats.get("builds_dropped_low_conf", 0)
        ),
        progression_dropped_low_conf_relates=int(
            p_stats.get("relates_dropped_low_conf", 0)
        ),
        progression_kept_builds_towards=int(p_stats.get("builds_kept", 0)),
        progression_kept_relates_to=int(p_stats.get("relates_kept_after_cap", 0)),
        # Drop details
        drop_details=drop_details,
    )


def log_console_summary(
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

    # NB: total_dropped is approximate — it includes the framework root node (which
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
                "attach to expectation metadata",
                policy_report.dropped_attach_to_expectation,
            ),
            ("pruned empty groupings", policy_report.pruned_empty_groupings),
        ]
        for label, count in scalar_stats:
            if count > 0:
                logger.info(f"  - {label}: {count}")

    # LC stats.
    logger.info(
        f"LCs: {policy_report.total_lcs} from {policy_report.total_expectations} "
        f"expectations (policy: {policy_report.lc_split_policy})"
    )
    if policy_report.lc_max_splits_observed > 1:
        logger.info(
            f"  Max splits observed: {policy_report.lc_max_splits_observed} | "
            f"Distribution: {policy_report.lc_splits_distribution}"
        )

    # Progression stats.
    if policy_report.progression_candidate_edges > 0:
        logger.info(
            f"Progressions: {policy_report.progression_candidate_edges} candidates → "
            f"{policy_report.progression_kept_builds_towards} buildsTowards + "
            f"{policy_report.progression_kept_relates_to} relatesTo kept"
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

    1. Referential integrity: all relationship endpoints reference existing entities.
    2. Every LC has exactly one supports relationship.
    3. hasChild: no cycles in the exported graph; framework is root; all SFIs reachable.
    4. Self-loop check on all relationships.

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

    # Build unified entity ID sets.
    fw_id = str(academic_standards.framework.case_identifier_uuid)
    sfi_ids = {str(sfi.case_identifier_uuid) for sfi in academic_standards.items}
    lc_ids = {str(lc.identifier) for lc in learning_components.learning_components}

    all_entity_ids = {fw_id} | sfi_ids | lc_ids

    # Verify no UUID collisions across entity types. UUIDv5 generation uses
    # type-specific prefixes so collisions should be impossible, but a namespace bug
    # could silently break referential integrity checks downstream.
    expected_count = 1 + len(sfi_ids) + len(lc_ids)

    if len(all_entity_ids) != expected_count:
        overlap_count = expected_count - len(all_entity_ids)
        report.error(
            code="ENTITY_ID_COLLISION",
            message=(
                f"{overlap_count} UUID collision(s) detected across entity types "
                f"(framework/SFI/LC). This indicates a namespace or ID generation bug."
            ),
        )
    else:
        report.info(
            code="ENTITY_IDS_DISJOINT",
            message=(
                f"All {expected_count} entity IDs are unique across types "
                f"(1 framework, {len(sfi_ids)} SFIs, {len(lc_ids)} LCs)."
            ),
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
            learning_progressions=learning_progressions, report=report, sfi_ids=sfi_ids
        )

    # Finalize summary statistics.
    has_child_rels_count = sum(1 for r in all_rels if r.relationship_type == "hasChild")
    report.stats = {
        "total_entities": len(all_entity_ids),
        "total_sfis": len(sfi_ids),
        "total_lcs": len(lc_ids),
        "total_relationships": len(all_rels),
        "has_child_count": has_child_rels_count,
        "supports_count": len(learning_components.supports_relationships),
        "builds_towards_count": (
            len(learning_progressions.builds_towards_relationships)
            if learning_progressions
            else 0
        ),
        "relates_to_count": (
            len(learning_progressions.relates_to_relationships)
            if learning_progressions
            else 0
        ),
        "errors": len(report.errors()),
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
