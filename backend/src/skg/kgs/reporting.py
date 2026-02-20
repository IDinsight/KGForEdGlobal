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
)
from skg.kgs.utils import ExportContext, KGDirs
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
    *, lc_ids: set[str], learning_components: Any, report: GraphValidationReport
) -> None:
    """Check that every Learning Component has at least one supports relationship.

    Parameters
    ----------
    lc_ids
        Set of all Learning Component IDs.
    learning_components
        The exported Learning Components KG artifacts.
    report
        The GraphValidationReport to append findings to.
    """

    lc_with_supports: set[str] = {
        r.source_entity_value
        for r in learning_components.supports_relationships
        if r.relationship_type == "supports"
    }
    lc_without_supports = lc_ids - lc_with_supports

    if lc_without_supports:
        report.error(
            code="LC_WITHOUT_SUPPORTS",
            message=(
                f"{len(lc_without_supports)} LearningComponent(s) have no supports "
                f"relationship. Examples: {sorted(lc_without_supports)[:5]}"
            ),
        )
    else:
        report.info(
            code="LC_SUPPORTS_OK",
            message=f"All {len(lc_ids)} LearningComponents have supports relationships.",
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

    # No duplicate relatesTo pairs (A, B) and (B, A) after canonicalization.
    relates_pairs: set[tuple[str, str]] = set()
    duplicate_relates = 0

    for r in learning_progressions.relates_to_relationships:
        a, b = sorted([r.source_entity_value, r.target_entity_value])
        pair = (a, b)

        if pair in relates_pairs:
            duplicate_relates += 1
        else:
            relates_pairs.add(pair)

    if duplicate_relates:
        report.error(
            code="RELATES_TO_DUPLICATE_PAIR",
            message=(
                f"{duplicate_relates} duplicate relatesTo pair(s) detected "
                f"(same endpoints in different directions)."
            ),
        )
    else:
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

    # Aggregate drop reasons by category.
    reason_counter: Counter[str] = Counter(drop_reasons.values())

    dropped_by_decision_type: dict[str, int] = {}
    dropped_by_columns_signature: dict[str, int] = {}
    dropped_guidance = 0
    dropped_descriptor = 0
    dropped_non_grouping_role = 0

    for reason, count in reason_counter.items():
        if reason.startswith("dropped:segment_decision:"):
            dt = reason.split(":", 2)[2]
            dropped_by_decision_type[dt] = dropped_by_decision_type.get(dt, 0) + count
        elif reason.startswith("dropped:columns_signature:"):
            sig = reason.split(":", 2)[2]
            dropped_by_columns_signature[sig] = (
                dropped_by_columns_signature.get(sig, 0) + count
            )
        elif reason == "dropped:guidance_handling:drop":
            dropped_guidance = count
        elif reason == "dropped:descriptor_handling:drop":
            dropped_descriptor = count
        elif reason == "dropped:non_grouping_role:drop":
            dropped_non_grouping_role = count

    pruned_count = len(academic_standards.pruned_node_ids)
    attach_count = reparent_stats.get("attach_to_expectation_count", 0)

    # Build per-node drop detail log (capped for file size).
    drop_details: list[dict[str, Any]] = []
    total_drops = len(drop_reasons)
    max_drop_details = 200

    for node_id, reason in sorted(drop_reasons.items()):
        if len(drop_details) >= max_drop_details:
            break

        node = ctx.nodes_by_id.get(node_id, {})
        drop_details.append(
            {
                "canonical_node_id": node_id,
                "role": str(node.get("role") or ""),
                "drop_reason": reason,
            }
        )

    if total_drops > max_drop_details:
        logger.info(
            f"Drop details truncated: showing {max_drop_details} of {total_drops} "
            f"dropped nodes in policy_coverage_report.json."
        )

    # LC stats.
    lc_stats = learning_components.lc_stats

    # Progression stats.
    p_stats = (
        (learning_progressions.report.get("counts") or {})
        if learning_progressions
        else {}
    )

    report = PolicyCoverageReport(
        doc_key=ctx.doc_key,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        pdf_name=str((ctx.get_framework_metadata() or {}).get("pdf_name") or ""),
        # Node-level drop accounting.
        dropped_attach_to_expectation=attach_count,
        dropped_by_columns_signature=dropped_by_columns_signature,
        dropped_by_decision_type=dropped_by_decision_type,
        dropped_descriptor=dropped_descriptor,
        dropped_guidance=dropped_guidance,
        dropped_non_grouping_role=dropped_non_grouping_role,
        pruned_empty_groupings=pruned_count,
        total_canonical_nodes=len(ctx.nodes_by_id) - 1,  # exclude root
        total_emitted_sfis=len(academic_standards.items),
        # Aux reparenting.
        aux_reparented_count=reparent_stats.get("aux_reparented_count", 0),
        orphan_aux_count=reparent_stats.get("orphan_aux_count", 0),
        # LC stats.
        lc_max_splits_observed=int(lc_stats.get("max_splits_observed", 0)),
        lc_split_policy=str(lc_stats.get("split_policy", "")),
        lc_splits_distribution=lc_stats.get("splits_distribution", {}),
        total_expectations=int(lc_stats.get("total_expectations", 0)),
        total_lcs=int(lc_stats.get("total_lcs", 0)),
        # Progression stats.
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
        # Drop details.
        drop_details=drop_details,
    )

    return report


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
    warnings = validation_report.warnings()

    if not errors:
        logger.info(f"Validation: PASSED ({len(warnings)} warning(s))")
    else:
        logger.error(
            f"Validation: FAILED ({len(errors)} error(s), {len(warnings)} warning(s))"
        )
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
        The validation report with errors, warnings, and check results.
    """

    report = GraphValidationReport(doc_key=ctx.doc_key)

    # Build unified entity ID sets.
    fw_id = str(academic_standards.framework.case_identifier_uuid)
    sfi_ids = {str(sfi.case_identifier_uuid) for sfi in academic_standards.items}
    lc_ids = {str(lc.identifier) for lc in learning_components.learning_components}

    all_entity_ids = {fw_id} | sfi_ids | lc_ids

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
        "warnings": len(report.warnings()),
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
