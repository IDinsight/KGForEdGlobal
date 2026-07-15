"""This module contains LC-source selection for KG creation (steps 11-12).

- 11: go/no-go gates on the step-10 Academic Standards KG bundle.
- 12: deterministic selection of LC-source SFIs (profile allowlist or
  leaf-node default).

Sibling LC modules mirror the sfi_* per-step layout: lc_generation.py
(steps 13-14: requests + LLM decomposition), lc_finalization.py (steps 15-17:
mint nodes, supports edges, validate/summarize), lc_export.py (step 18:
AS+LC bundle merge).
"""

# Standard Library
from collections import Counter
from typing import Optional, Sequence
from uuid import UUID

# Third Party Library
from loguru import logger

# Package Library
from skg.kgs.schemas import (
    AcademicStandardsKGBundle,
    LCEligibilityReport,
    LCExcludedSFI,
    LCExclusionReason,
    LCSelectionMode,
    SFIFinalRecord,
    SFIHasChildEdge,
)
from skg.kgs.utils import KGDirs, make_dir
from skg.schemas import _CreateKGLearningComponentsConfig
from skg.utils.general import write_to_json

LC_ELIGIBILITY_REPORT_FN = "lc_eligibility_report.json"
LC_ELIGIBLE_SFIS_FN = "lc_eligible_sfis.json"


def _classify_sfi_for_lc_generation(
    *,
    allowlist: Optional[list[str]],
    parent_sfi_uuids: set[UUID],
    record: SFIFinalRecord,
    unresolved_ancestor_sfi_uuids: set[UUID],
) -> Optional[LCExclusionReason]:
    """Classify one SFI as an LC-generation seed or an exclusion.

    Parameters
    ----------
    allowlist
        Configured LC-source statement types, or None for the leaf default.
    parent_sfi_uuids
        Final SFI UUIDs that appear as a hasChild parent (non-leaves).
    record
        The final SFI record to classify.
    unresolved_ancestor_sfi_uuids
        SFIs excluded for unresolved ancestor paths (empty unless an
        unresolved-items override is active without ancestor allowance).

    Returns
    -------
    Optional[LCExclusionReason]
        The exclusion reason, or None when the SFI is an eligible seed.
    """

    if allowlist is not None:
        if record.statement_type not in allowlist:
            return "not_in_allowlist"
    else:
        if record.final_sfi_uuid in parent_sfi_uuids:
            return "not_a_leaf"
        if record.normalized_statement_type != "Standard":
            return "grouping_node"
    if not record.description.strip():
        return "empty_text"
    if record.final_sfi_uuid in unresolved_ancestor_sfi_uuids:
        return "unresolved_ancestor_path"
    return None


def _collect_unresolved_ancestor_sfi_uuids(
    has_child_edges: Sequence[SFIHasChildEdge],
) -> set[UUID]:
    """Collect SFIs whose hasChild ancestor path passes an unresolved edge.

    An unresolved root-fallback edge attaches its child SFI directly to the
    framework root because no real parent could be resolved. That child and
    every SFI below it therefore have an unresolved ancestor path.

    Parameters
    ----------
    has_child_edges
        Final resolved hasChild edges.

    Returns
    -------
    set[UUID]
        Final SFI UUIDs of unresolved-fallback children and their descendants.
    """

    children_by_parent: dict[UUID, list[UUID]] = {}
    for edge in has_child_edges:
        if edge.parent_final_sfi_uuid is not None:
            children_by_parent.setdefault(edge.parent_final_sfi_uuid, []).append(
                edge.child_final_sfi_uuid
            )

    tainted: set[UUID] = set()
    frontier = [
        edge.child_final_sfi_uuid
        for edge in has_child_edges
        if edge.unresolved_root_fallback
    ]
    while frontier:
        current = frontier.pop()
        if current in tainted:
            continue
        tainted.add(current)
        frontier.extend(children_by_parent.get(current, []))
    return tainted


def run_lc_generation_gates(
    *,
    academic_standards_bundle: AcademicStandardsKGBundle,
    lc_config: _CreateKGLearningComponentsConfig,
) -> list[str]:
    """Run the step-11 go/no-go gates on the step-10 AS KG bundle.

    Gate 1 requires a passed, error-free step-10 validation report and fails
    loudly otherwise. Gate 2 reports-and-restricts rather than failing:
    finalization exclusions and unresolved root-fallback edges never block the
    run (the step-10 bundle already passed validation with them recorded) —
    generation proceeds over the resolved subgraph by default, and the gap
    counts are surfaced as warnings. A configured manual-review override
    widens scope (``allow_unresolved_ancestor_context``) and is recorded.

    Parameters
    ----------
    academic_standards_bundle
        Compiled step-10 Academic Standards KG bundle.
    lc_config
        Learning Components runtime configuration.

    Returns
    -------
    list[str]
        Non-fatal warnings raised by the gates (unresolved-item gaps and any
        recorded manual-review override).

    Raises
    ------
    ValueError
        If step-10 validation failed.
    """

    validation_report = academic_standards_bundle.validation_report
    if not validation_report.passed or validation_report.errors:
        raise ValueError(
            "LC generation blocked (gate 11): step-10 validation_report has "
            f"passed={validation_report.passed} and "
            f"{len(validation_report.errors)} errors. Never generate LCs from "
            "an invalid Academic Standards KG."
        )

    unresolved_items = academic_standards_bundle.unresolved_items
    exclusions = {
        reason: count
        for reason, count in unresolved_items.finalization_exclusion_summary.items()
        if count
    }
    unresolved_edge_count = len(unresolved_items.relationship_unresolved_edges)

    warnings: list[str] = []
    if exclusions or unresolved_edge_count:
        gap_warning = (
            "step-10 bundle has finalization exclusions "
            f"{exclusions} and {unresolved_edge_count} unresolved "
            "root-fallback edges; LC generation proceeds over the resolved "
            "subgraph (seeds with unresolved ancestor paths are excluded)"
        )
        logger.warning(gap_warning)
        warnings.append(gap_warning)
    if lc_config.lc_manual_review_overrides is not None:
        override_warning = (
            "manual-review overrides recorded: "
            f"{lc_config.lc_manual_review_overrides}"
        )
        logger.warning(override_warning)
        warnings.append(override_warning)

    return warnings


def select_lc_source_sfis(
    *,
    academic_standards_bundle: AcademicStandardsKGBundle,
    has_child_edges: Sequence[SFIHasChildEdge],
    kg_dirs: KGDirs,
    lc_config: _CreateKGLearningComponentsConfig,
    sfi_final_records: Sequence[SFIFinalRecord],
) -> tuple[list[SFIFinalRecord], LCEligibilityReport]:
    """Run steps 11 (gates) and 12 (LC-source SFI selection).

    Selection is fully deterministic. With a configured allowlist, SFIs are
    selected by source-facing statement type. Without one, selection defaults
    to leaf SFIs (never a hasChild parent) whose normalized statement type is
    "Standard" — childless grouping nodes must not become LC sources. Seeds
    whose ancestor path passes through an unresolved root-fallback edge are
    excluded unless the manual-review override allows unresolved ancestor
    context. Every excluded SFI is recorded with a single deterministic
    reason.

    Parameters
    ----------
    academic_standards_bundle
        Compiled step-10 Academic Standards KG bundle (gates, 11).
    has_child_edges
        Final resolved hasChild edges (leaf and ancestor computation).
    kg_dirs
        KG artifact directories; artifacts are written under ``kg_dirs.root``.
    lc_config
        Learning Components runtime configuration.
    sfi_final_records
        Final SFI records to select from.

    Returns
    -------
    tuple[list[SFIFinalRecord], LCEligibilityReport]
        The eligible LC-source SFIs and the eligibility coverage report.

    Raises
    ------
    ValueError
        If step-10 validation failed (11), or selection produced zero
        eligible SFIs from a non-empty record set.
    """

    warnings = run_lc_generation_gates(
        academic_standards_bundle=academic_standards_bundle, lc_config=lc_config
    )

    allowlist = lc_config.lc_source_statement_types
    selection_mode: LCSelectionMode = (
        "explicit_allowlist" if allowlist is not None else "leaf_default"
    )
    if allowlist is None:
        leaf_default_warning = (
            "no lc_source_statement_types configured; used leaf default "
            "(leaf SFIs with normalized_statement_type == 'Standard')"
        )
        logger.warning(leaf_default_warning)
        warnings.append(leaf_default_warning)

    parent_sfi_uuids = {
        edge.parent_final_sfi_uuid
        for edge in has_child_edges
        if edge.parent_final_sfi_uuid is not None
    }

    overrides = lc_config.lc_manual_review_overrides
    allow_unresolved_ancestors = bool(
        overrides and overrides.get("allow_unresolved_ancestor_context")
    )
    unresolved_ancestor_sfi_uuids: set[UUID] = (
        set()
        if allow_unresolved_ancestors
        else _collect_unresolved_ancestor_sfi_uuids(has_child_edges)
    )

    eligible: list[SFIFinalRecord] = []
    excluded: list[LCExcludedSFI] = []
    for record in sfi_final_records:
        reason = _classify_sfi_for_lc_generation(
            allowlist=allowlist,
            parent_sfi_uuids=parent_sfi_uuids,
            record=record,
            unresolved_ancestor_sfi_uuids=unresolved_ancestor_sfi_uuids,
        )
        if reason is None:
            eligible.append(record)
        else:
            excluded.append(
                LCExcludedSFI(
                    final_sfi_uuid=record.final_sfi_uuid,
                    normalized_statement_type=record.normalized_statement_type,
                    reason=reason,
                    statement_type=record.statement_type,
                )
            )

    reason_counts: dict[str, int] = dict(
        Counter(excluded_sfi.reason for excluded_sfi in excluded)
    )

    report = LCEligibilityReport(
        excluded=excluded,
        lc_selection_mode=selection_mode,
        lc_source_exclusion_reason_counts=reason_counts,
        total_lc_source_sfis_considered=len(sfi_final_records),
        total_lc_source_sfis_eligible=len(eligible),
        total_lc_source_sfis_empty_text=reason_counts.get("empty_text", 0),
        total_lc_source_sfis_excluded=len(excluded),
        warnings=warnings,
    )

    make_dir(kg_dirs.root)
    write_to_json(
        fp=kg_dirs.root / LC_ELIGIBLE_SFIS_FN,
        json_info=[record.model_dump(mode="json") for record in eligible],
    )
    write_to_json(
        fp=kg_dirs.root / LC_ELIGIBILITY_REPORT_FN,
        json_info=report.model_dump(mode="json"),
    )

    if sfi_final_records and not eligible:
        raise ValueError(
            "LC-source selection produced zero eligible SFIs out of "
            f"{len(sfi_final_records)} considered "
            f"(mode={selection_mode}; exclusion reasons={reason_counts}). "
            "Check lc_source_statement_types and the hasChild hierarchy; see "
            f"{kg_dirs.root / LC_ELIGIBILITY_REPORT_FN} for per-SFI reasons."
        )

    logger.success(
        f"Selected LC-source SFIs: mode={selection_mode}; "
        f"considered={report.total_lc_source_sfis_considered}; "
        f"eligible={report.total_lc_source_sfis_eligible}; "
        f"excluded={report.total_lc_source_sfis_excluded}; "
        f"exclusion_reasons={reason_counts}"
    )
    return eligible, report
