"""This module contains the spine corrector logic for applying spine policies to
SegmentDecisionSets.
"""

# Standard Library
import re

from dataclasses import dataclass

# Third Party Library
from loguru import logger

# Package Library
from skg.canonical_ir.schemas import (
    GroupingDecision,
    RowDecision,
    SegmentDecision,
    SegmentDecisionSet,
    compute_decision_set_id,
)
from skg.canonical_ir.segment_decisions import CaptionBinding
from skg.canonical_ir.utils import CanonicalIRDirs
from skg.document_ir.schemas import DocumentIR
from skg.schemas import SpineConfig, SpineCorrection, SpineCorrectionReport
from skg.utils.constants import (
    NodeRole,
    SegmentDecisionType,
    SpineSplitApplyTo,
    SpineViolationPolicy,
)
from skg.utils.general import open_json_type, write_to_json


@dataclass
class SpineDecisionResult:
    """Result of applying spine correction to a single SegmentDecision."""

    corrected: SegmentDecision
    corrections: list[SpineCorrection]
    flagged_unresolved: bool


def _apply_outer_normalizations(
    *, groupings: list[GroupingDecision], spine: SpineConfig
) -> tuple[list[GroupingDecision], list[SpineCorrection]]:
    """Apply outer context normalizations (wrapper minimization, grade band dropping).

    Parameters
    ----------
    groupings
        The list of GroupingDecision to normalize.
    spine
        The SpineConfig defining the normalization policy.

    Returns
    -------
    tuple[list[GroupingDecision], list[SpineCorrection]]
        The normalized list of GroupingDecision and any corrections made.
    """

    corrections: list[SpineCorrection] = []
    gs = list(groupings)
    has_grade = any(g.role == NodeRole.GRADE_LEVEL for g in gs)

    # Drop stage if grade present.
    if spine.normalize.drop_stage_if_grade_present and has_grade:
        before = len(gs)
        gs = [g for g in gs if g.role != NodeRole.STAGE]
        if len(gs) != before:
            corrections.append(
                SpineCorrection(
                    kind="drop_stage",
                    detail="Dropped STAGE because GRADE_LEVEL present.",
                )
            )

    # Drop stage if wrapper patterns match.
    if (
        spine.normalize.drop_stage_if_matches_wrapper_patterns
        and spine.normalize.stage_wrapper_title_patterns
    ):
        pats = [
            re.compile(p, re.IGNORECASE)
            for p in spine.normalize.stage_wrapper_title_patterns
        ]
        new_gs: list[GroupingDecision] = []

        for g in gs:
            if g.role == NodeRole.STAGE and any(p.search(g.title) for p in pats):
                corrections.append(
                    SpineCorrection(
                        kind="drop_stage_wrapper",
                        detail=f"Dropped STAGE wrapper '{g.title}'",
                    )
                )
                continue

            new_gs.append(g)

        gs = new_gs

    # Drop grade bands.
    if spine.normalize.drop_grade_bands and spine.normalize.grade_band_title_patterns:
        pats = [
            re.compile(p, re.IGNORECASE)
            for p in spine.normalize.grade_band_title_patterns
        ]
        new_gs = []

        for g in gs:
            if g.role == NodeRole.GRADE_LEVEL and any(p.search(g.title) for p in pats):
                corrections.append(
                    SpineCorrection(
                        kind="drop_grade_band", detail=f"Dropped grade band '{g.title}'"
                    )
                )
                continue

            new_gs.append(g)

        gs = new_gs

    return gs, corrections


def _apply_spine_to_single_decision(
    *,
    canonical_outer_context: list[GroupingDecision] | None,
    caption_bindings: dict[str, CaptionBinding],
    decision: SegmentDecision,
    spine: SpineConfig,
) -> SpineDecisionResult:
    """Apply spine correction policy to a single SegmentDecision.

    The process is as follows:

    1. Inject outer context from caption (if applicable).
    2. Normalize/split/filter/reorder outer context.
    3. Table-only relocation of local-only roles.
    4. Correct local groupings (block-local or row-local).
    5. Chunk consistency check (for tables with canonical_outer_context).
    6. Hard-shape enforcement (no outputs for ignore/unresolved; flagged_unresolved
       must emit something).

    Parameters
    ----------
    canonical_outer_context
        The canonical outer context for the segment (if any).
    caption_bindings
        Caption bindings for the decision.
    decision
        The SegmentDecision to correct.
    spine
        The SpineConfig defining the correction policy.

    Returns
    -------
    SpineDecisionResult
        The result of applying the spine correction, including the corrected decision,
        any corrections made, and whether it was flagged as unresolved.
    """

    # Deep copy to preserve auditability/provenance.
    d = decision.model_copy(deep=True)
    corrections: list[SpineCorrection] = []

    # 1.
    d, c, flagged = _inject_caption_context(
        caption_bindings=caption_bindings, d=d, spine=spine
    )
    corrections.extend(c)

    # 2.
    d, c, flagged = _correct_outer_context(d=d, spine=spine)
    corrections.extend(c)

    # 3.
    d, c, flagged = _relocate_local_roles(d=d, spine=spine)
    corrections.extend(c)

    # 4.
    d, c = _correct_local_structure(d=d, spine=spine)
    corrections.extend(c)

    # 5.
    c, flagged = _check_canonical_consistency(
        d=d, canonical_ctx=canonical_outer_context, spine=spine
    )
    corrections.extend(c)

    # 6.
    if d.decision_type in (SegmentDecisionType.IGNORE, SegmentDecisionType.UNRESOLVED):
        # These must be pure no-ops: empty context + empty outputs.
        if d.context_groupings or d.groupings or d.leaves or d.rows:
            corrections.append(
                SpineCorrection(
                    kind="clear_noop",
                    detail=f"Cleared context/outputs for decision_type={d.decision_type.value}.",
                )
            )

        d.context_groupings = []
        d.groupings = []
        d.leaves = []
        d.rows = []
        flagged = False

    if d.decision_type == SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED:
        # flagged_unresolved is allowed to be "context-only" (no
        # groupings/leaves/rows), as long as *something* is emitted overall.
        has_any_output = bool(d.context_groupings or d.groupings or d.leaves or d.rows)
        if not has_any_output:
            corrections.append(
                SpineCorrection(
                    kind="demote_flagged_unresolved_empty",
                    detail=(
                        "Demoted to UNRESOLVED because flagged_unresolved emitted nothing "
                        "(context_groupings/groupings/leaves/rows all empty)."
                    ),
                )
            )
            d.context_groupings = []
            d.decision_type = SegmentDecisionType.UNRESOLVED  # pylint: disable=R0204
            d.groupings = []
            d.leaves = []
            d.rows = []
            flagged = False

    # Re-run pydantic validators after mutation; if it fails, demote deterministically.
    try:
        d = SegmentDecision.model_validate(d.model_dump())
    except Exception as e:  # pylint: disable=broad-except
        corrections.append(
            SpineCorrection(
                kind="revalidate_failed",
                detail=f"Post-spine revalidation failed; demoted to UNRESOLVED. err={e!s}",
            )
        )

        d = d.model_copy(
            update={
                "context_groupings": [],
                "decision_type": SegmentDecisionType.UNRESOLVED,
                "groupings": [],
                "leaves": [],
                "rows": [],
            }
        )
        flagged = False

    return SpineDecisionResult(
        corrected=d, corrections=corrections, flagged_unresolved=flagged
    )


def _apply_split_rules_to_grouping(
    *, apply_to: SpineSplitApplyTo, g: GroupingDecision, spine: SpineConfig
) -> tuple[list[GroupingDecision], list[SpineCorrection]]:
    """Apply first-matching split rule (deterministic order = config order).

    Parameters
    ----------
    apply_to
        Where these groupings are applied (for split rules).
    g
        The GroupingDecision to potentially split.
    spine
        The SpineConfig defining the split rules.

    Returns
    -------
    tuple[list[GroupingDecision], list[SpineCorrection]]
        The resulting list of GroupingDecision (possibly split) and any corrections
        made.
    """

    corrections: list[SpineCorrection] = []

    for rule in spine.split_rules:
        if rule.apply_to not in (SpineSplitApplyTo.ANY, apply_to):
            continue

        rx = re.compile(rule.match, rule.flags)
        m = rx.match(g.title)

        if not m:
            continue

        outputs: list[GroupingDecision] = []

        for tmpl in rule.emit:
            title = m.expand(tmpl.title_template).strip()

            if not title:
                continue

            outputs.append(
                GroupingDecision(
                    local_code=None,  # Do not inherit local_code on splits
                    role=tmpl.role,
                    source_label=(  # Preserve provenance label
                        g.source_label
                        if tmpl.inherit_source_label
                        else (tmpl.source_label or g.source_label)
                    ),
                    title=title,
                )
            )

        if outputs:
            corrections.append(
                SpineCorrection(
                    kind="split",
                    detail=f"Split '{g.title}' -> {[o.title for o in outputs]}",
                )
            )
            return outputs, corrections

        # If matched but emitted nothing, treat as no-op.
        return [g], corrections

    return [g], corrections


def _check_canonical_consistency(
    *,
    d: SegmentDecision,
    canonical_ctx: list[GroupingDecision] | None,
    spine: SpineConfig,
) -> tuple[list[SpineCorrection], bool]:
    """Check consistency with canonical outer context.

    Parameters
    ----------
    d
        The SegmentDecision to check.
    canonical_ctx
        The canonical outer context for the segment (if any).
    spine
        The SpineConfig defining the correction policy.

    Returns
    -------
    tuple[list[SpineCorrection], bool]
        - Any corrections made.
        - True if the decision was flagged as unresolved due to conflicts.
    """

    corrections = []
    should_flag = False

    if canonical_ctx is not None and d.segment_kind == "table":
        has_conflict, details = _outer_context_conflicts(
            canonical_ctx=canonical_ctx,
            candidate_ctx=list(d.context_groupings or []),
            casefold_titles_for_matching=spine.normalize.casefold_titles_for_matching,
            compare_roles=(
                set(spine.outer_context_roles) if spine.outer_context_roles else None
            ),
        )

        if has_conflict:
            for msg in details:
                corrections.append(
                    SpineCorrection(kind="chunk_context_conflict", detail=msg)
                )

            if spine.violation_policy == SpineViolationPolicy.FLAG_UNRESOLVED:
                d.decision_type = SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED
                should_flag = True

    return corrections, should_flag


def _correct_grouping_list(
    *,
    allow: list[NodeRole],
    apply_to: SpineSplitApplyTo,
    disallow: set[NodeRole],
    groupings: list[GroupingDecision],
    spine: SpineConfig,
) -> tuple[list[GroupingDecision], list[SpineCorrection], bool]:
    """Correct a list of GroupingDecision according to spine policy.

    Parameters
    ----------
    allow
        Allow-list of roles.
    apply_to
        Where these groupings are applied (for split rules).
    disallow
        Disallow-list of roles.
    groupings
        The list of GroupingDecision to correct.
    spine
        The SpineConfig defining the correction policy.

    Returns
    -------
    tuple[list[GroupingDecision], list[SpineCorrection], bool]
        - The corrected list of GroupingDecision.
        - Any corrections made.
        - True if the resulting list would be empty but allow_partial_context is False.
    """

    corrections: list[SpineCorrection] = []
    needs_flag = False
    output: list[GroupingDecision] = []

    # Normalize titles.
    for g in groupings:
        g2 = g.model_copy(deep=True)

        if spine.normalize.normalize_whitespace:
            g2.title = " ".join(g2.title.split()).strip()

        output.append(g2)

    # Split fused groupings (first matching rule wins).
    output2: list[GroupingDecision] = []

    for g in output:
        split_outs, split_corr = _apply_split_rules_to_grouping(
            apply_to=apply_to, g=g, spine=spine
        )
        output2.extend(split_outs)
        corrections.extend(split_corr)

    # Allow/disallow list.
    allowed_set = set(allow)
    filtered: list[GroupingDecision] = []

    for g in output2:
        if g.role in disallow:
            corrections.append(
                SpineCorrection(
                    detail=f"Dropped disallowed role {g.role.value} ('{g.title}').",
                    kind="drop_role",
                )
            )
            continue

        if allowed_set and g.role not in allowed_set:
            corrections.append(
                SpineCorrection(
                    kind="drop_role",
                    detail=f"Dropped role not in allow-list {g.role.value} ('{g.title}').",
                )
            )
            continue

        filtered.append(g)

    # Dedupe deterministically (merge metadata into keep-first). Outer context:
    # singleton per role always. Block/row local: singleton per role except SECTION can
    # repeat.
    enforce_singleton = True

    # Determine which roles must be singletons.
    singleton_roles = _get_singleton_roles(apply_to=apply_to, spine=spine)

    if (
        apply_to == SpineSplitApplyTo.OUTER_CONTEXT
        and not filtered
        and groupings
        and not spine.allow_partial_context
    ):
        needs_flag = True
        corrections.append(
            SpineCorrection(
                kind="outer_context_would_be_empty",
                detail="Outer context became empty after filtering...",
            )
        )

    deduped, notes = _dedupe_groupings(
        casefold_titles_for_matching=spine.normalize.casefold_titles_for_matching,
        enforce_singleton_per_role=enforce_singleton,
        groupings=filtered,
        singleton_roles=singleton_roles,
    )

    for n in notes:
        corrections.append(SpineCorrection(kind="dedupe", detail=n))

    # Reorder by role order (stable by original order as secondary).
    role_rank = {r: i for i, r in enumerate(spine.normalize.role_order)}
    final = [
        g
        for _, g in sorted(
            enumerate(deduped),
            key=lambda t: (role_rank.get(t[1].role, 10**9), t[0]),
        )
    ]

    return final, corrections, needs_flag


def _correct_local_structure(
    *, d: SegmentDecision, spine: SpineConfig
) -> tuple[SegmentDecision, list[SpineCorrection]]:
    """Correct block-local or table-row-local groupings.

    Parameters
    ----------
    d
        The SegmentDecision to correct.
    spine
        The SpineConfig defining the correction policy.

    Returns
    -------
    tuple[SegmentDecision, list[SpineCorrection]]
        - The corrected SegmentDecision.
        - Any corrections made.
    """

    corrections = []

    if d.segment_kind == "block":
        d.groupings, c, _ = _correct_grouping_list(
            allow=spine.block_local_roles,
            apply_to=SpineSplitApplyTo.BLOCK_LOCAL,
            disallow=set(spine.disallowed_block_local_roles),
            groupings=list(d.groupings or []),
            spine=spine,
        )
        corrections.extend(c)

    elif d.segment_kind == "table" and d.rows:
        new_rows: list[RowDecision] = []

        for row in d.rows:
            row2 = row.model_copy(deep=True)

            # Correct the row's internal groupings.
            row2.groupings, c, _ = _correct_grouping_list(
                allow=spine.row_roles,
                apply_to=SpineSplitApplyTo.TABLE_ROW_LOCAL,
                disallow=set(spine.disallowed_row_roles),
                groupings=list(row2.groupings or []),
                spine=spine,
            )
            corrections.extend(c)

            # If spine removed all row-local structure and the row has no leaves, drop
            # it.
            if not row2.groupings and not row2.leaves:
                corrections.append(
                    SpineCorrection(
                        detail=f"Dropped RowDecision row_index={row2.row_index} (no groupings/leaves after spine correction).",
                        kind="drop_empty_row",
                    )
                )
                continue

            new_rows.append(row2)

        d.rows = new_rows

    return d, corrections


def _correct_outer_context(
    *, d: SegmentDecision, spine: SpineConfig
) -> tuple[SegmentDecision, list[SpineCorrection], bool]:
    """Filter, normalize, and split outer context.

    Parameters
    ----------
    d
        The SegmentDecision to correct.
    spine
        The SpineConfig defining the correction policy.

    Returns
    -------
    tuple[SegmentDecision, list[SpineCorrection], bool]
        - The corrected SegmentDecision.
        - Any corrections made.
        - True if the decision was flagged as unresolved due to empty outer context.
    """

    corrections = []
    should_flag = False

    # Keep table-local-only roles long enough to relocate them deterministically.
    outer_allow = list(spine.outer_context_roles or [])

    if d.segment_kind == "table" and spine.relocate_table_local_only_roles_to_rows:
        outer_allow = list(
            dict.fromkeys(outer_allow + list(spine.table_local_only_roles or []))
        )

    # Correct grouping list.
    d.context_groupings, c, needs_flag = _correct_grouping_list(
        allow=outer_allow,
        apply_to=SpineSplitApplyTo.OUTER_CONTEXT,
        disallow=set(spine.disallowed_outer_roles),
        groupings=list(d.context_groupings or []),
        spine=spine,
    )
    corrections.extend(c)

    # Check violation policy.
    if (
        needs_flag
        and d.decision_type
        not in (
            SegmentDecisionType.IGNORE,
            SegmentDecisionType.UNRESOLVED,
        )
        and spine.violation_policy == SpineViolationPolicy.FLAG_UNRESOLVED
    ):
        d.decision_type = SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED
        should_flag = True

    # Apply outer normalizations.
    d.context_groupings, c = _apply_outer_normalizations(
        groupings=list(d.context_groupings or []), spine=spine
    )
    corrections.extend(c)

    return d, corrections, should_flag


def _dedupe_groupings(
    *,
    casefold_titles_for_matching: bool,
    enforce_singleton_per_role: bool,
    groupings: list[GroupingDecision],
    singleton_roles: set[NodeRole] | None = None,
) -> tuple[list[GroupingDecision], list[str]]:
    """Deterministic dedupe that matches GroupingDecision schema constraints.

    Two passes:

    1. Exact dedupe by (role, normalized_title).
    2. Optionally enforce singleton per role (keep-first), merging metadata.

    NB: For outer context you almost always want 1 per role; for row-local you might
    not.

    Parameters
    ----------
    groupings
        List of GroupingDecision to dedupe.
    casefold_titles_for_matching
        If True, casefold titles when normalizing for matching.
    enforce_singleton_per_role
        If True, enforce singleton per role in second pass.
    singleton_roles
        If provided, only enforce singleton for these roles in second pass.

    Returns
    -------
    tuple[list[GroupingDecision], list[str]]
      - Deduped list in stable encounter order
      - Notes describing merges/drops/conflicts
    """

    notes: list[str] = []

    if not groupings:
        return [], notes

    # Deep copy so we never mutate caller models.
    gs = [g.model_copy(deep=True) for g in groupings]

    # Pass 1: exact duplicates (role and normalized title).
    idx_by_key: dict[tuple[NodeRole, str], int] = {}
    kept: list[GroupingDecision] = []

    for g in gs:
        k = (g.role, _norm_title(casefold=casefold_titles_for_matching, title=g.title))

        if k not in idx_by_key:
            idx_by_key[k] = len(kept)
            kept.append(g)
            continue

        i = idx_by_key[k]
        merged, merge_notes = _merge_grouping_metadata_keep_first(
            incoming=g, keep=kept[i]
        )
        kept[i] = merged
        notes.extend(merge_notes)
        notes.append(f"dedupe_exact: dropped duplicate {g.role.value} '{g.title}'")

    # Pass 2: enforce singleton per role (optional).
    if not enforce_singleton_per_role:
        return kept, notes

    roles_to_singleton = (
        singleton_roles if singleton_roles is not None else set(NodeRole)
    )

    final: list[GroupingDecision] = []
    idx_by_role: dict[NodeRole, int] = {}

    for g in kept:
        if g.role not in roles_to_singleton:
            final.append(g)
            continue

        if g.role not in idx_by_role:
            idx_by_role[g.role] = len(final)
            final.append(g)
            continue

        i = idx_by_role[g.role]
        merged, merge_notes = _merge_grouping_metadata_keep_first(
            incoming=g, keep=final[i]
        )
        final[i] = merged
        notes.extend(merge_notes)
        notes.append(
            f"dedupe_role: dropped extra {g.role.value} '{g.title}' (kept '{final[i].title}')"
        )

    return final, notes


def _derive_groupings_from_caption(
    *, caption_text: str, spine: SpineConfig
) -> tuple[list[GroupingDecision], list[SpineCorrection]]:
    """Apply split rules and ensure source labels exist.

    Parameters
    ----------
    caption_text
        The caption text to extract groupings from.
    spine
        The SpineConfig defining the split rules.

    Returns
    -------
    tuple[list[GroupingDecision], list[SpineCorrection]]
        - The derived list of GroupingDecision.
        - Any corrections made.
    """

    # Build a synthetic grouping so split_rules can operate deterministically. Use
    # PROSE so the raw caption never "sticks" as a hierarchy node.
    cap_g = GroupingDecision(
        local_code=None,
        role=NodeRole.PROSE,
        source_label="caption_binding",
        title=caption_text,
    )

    # Apply split rules.
    split_outs, split_corr = _apply_split_rules_to_grouping(
        apply_to=SpineSplitApplyTo.OUTER_CONTEXT, g=cap_g, spine=spine
    )

    # Filter PROSE and ensure source_label.
    derived = []
    for g in split_outs:
        # Keep only non-PROSE outputs (we don't want the caption node itself).
        if g.role == NodeRole.PROSE:
            continue

        # Ensure provenance label survives.
        if g.source_label is None:
            g = g.model_copy(update={"source_label": cap_g.source_label})
        derived.append(g)

    return derived, split_corr


def _detect_caption_conflicts(
    *,
    derived: list[GroupingDecision],
    existing_map: dict[NodeRole, str],
    raw_caption: str,
    spine: SpineConfig,
) -> list[str]:
    """Compare derived candidates against the effective existing map. If any conflicts
    exist, do NOT inject.

    Parameters
    ----------
    derived
        The list of derived GroupingDecision.
    existing_map
        The map of existing effective anchors by role to normalized title.
    raw_caption
        The raw caption text for logging.
    spine
        The SpineConfig defining the normalization policy.

    Returns
    -------
    list[str]
        The list of conflict messages detected.
    """

    casefold = spine.normalize.casefold_titles_for_matching
    msgs = []

    for cg in derived:
        if cg.role not in existing_map:
            continue

        existing_norm = existing_map[cg.role]
        derived_norm = _norm_title(casefold=casefold, title=cg.title)

        if existing_norm != derived_norm:
            msgs.append(
                f"caption_context_conflict: role={cg.role.value} "
                f"existing='{existing_norm}' caption='{derived_norm}' (raw_caption='{raw_caption}')"
            )

    return msgs


def _filter_derived_roles(
    *, decision: SegmentDecision, derived: list[GroupingDecision], spine: SpineConfig
) -> list[GroupingDecision]:
    """Filter the derived groupings based on allowed outer context roles.

    Parameters
    ----------
    decision
        The SegmentDecision being processed.
    derived
        The list of derived GroupingDecision.
    spine
        The SpineConfig defining the filtering policy.

    Returns
    -------
    list[GroupingDecision]
        The filtered list of GroupingDecision.
    """

    allowed_outer = set(spine.outer_context_roles or [])

    if (
        decision.segment_kind == "table"
        and spine.relocate_table_local_only_roles_to_rows
    ):
        allowed_outer |= set(spine.table_local_only_roles or [])

    if allowed_outer:
        return [g for g in derived if g.role in allowed_outer]

    return derived


def _get_effective_existing_anchors(
    *,
    existing_all: list[GroupingDecision],
    grade_present_after: bool,
    spine: SpineConfig,
) -> dict[NodeRole, str]:
    """Return a map of {Role: NormalizedTitle} for existing anchors that are considered
    'non-droppable' (effective).

    Parameters
    ----------
    existing_all
        The list of existing GroupingDecision.
    grade_present_after
        True if a GRADE_LEVEL grouping is present either in existing or derived
        groupings.
    spine
        The SpineConfig defining the normalization policy.

    Returns
    -------
    dict[NodeRole, str]
        Map of effective existing anchors by role to normalized title.
    """

    casefold = spine.normalize.casefold_titles_for_matching

    stage_pats = []
    if (
        spine.normalize.drop_stage_if_matches_wrapper_patterns
        and spine.normalize.stage_wrapper_title_patterns
    ):
        stage_pats = [
            re.compile(p, re.IGNORECASE)
            for p in spine.normalize.stage_wrapper_title_patterns
        ]

    grade_band_pats = []
    if spine.normalize.drop_grade_bands and spine.normalize.grade_band_title_patterns:
        grade_band_pats = [
            re.compile(p, re.IGNORECASE)
            for p in spine.normalize.grade_band_title_patterns
        ]

    effective_map = {}

    for g in existing_all:
        if _is_droppable_existing(
            g=g,
            grade_band_pats=grade_band_pats,
            grade_present_after=grade_present_after,
            spine=spine,
            stage_pats=stage_pats,
        ):
            continue

        if g.role not in effective_map:
            effective_map[g.role] = _norm_title(casefold=casefold, title=g.title)

    return effective_map


def _get_singleton_roles(
    apply_to: SpineSplitApplyTo, spine: SpineConfig
) -> set[NodeRole]:
    """Determine which roles must be singletons.

    Parameters
    ----------
    apply_to
        Where these groupings are applied (for split rules).
    spine
        The SpineConfig defining the correction policy.

    Returns
    -------
    set[NodeRole]
        The set of roles that must be singletons.
    """

    if apply_to == SpineSplitApplyTo.OUTER_CONTEXT:
        return set(NodeRole)

    if apply_to == SpineSplitApplyTo.TABLE_ROW_LOCAL:
        base = set(spine.row_roles) if spine.row_roles else set(NodeRole)
    else:  # block_local
        base = (
            set(spine.block_local_roles) if spine.block_local_roles else set(NodeRole)
        )

    return base - {NodeRole.SECTION}


def _handle_caption_conflicts(
    *, decision: SegmentDecision, msgs: list[str], spine: SpineConfig
) -> tuple[SegmentDecision, list[SpineCorrection], bool]:
    """Generate corrections and flags the decision if necessary.

    Parameters
    ----------
    decision
        The SegmentDecision to handle.
    msgs
        The list of conflict messages detected.
    spine
        The SpineConfig defining the violation policy.

    Returns
    -------
    tuple[SegmentDecision, list[SpineCorrection], bool]
        - The corrected SegmentDecision.
        - Any corrections made.
        - True if the decision was flagged as unresolved due to conflicts.
    """

    corrections = [
        SpineCorrection(detail=msg, kind="caption_context_conflict") for msg in msgs
    ]
    flagged = False

    if (
        spine.violation_policy == SpineViolationPolicy.FLAG_UNRESOLVED
        and decision.decision_type
        not in (SegmentDecisionType.IGNORE, SegmentDecisionType.UNRESOLVED)
    ):
        decision.decision_type = SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED
        flagged = True

    return decision, corrections, flagged


def _inject_caption_context(
    *,
    caption_bindings: dict[str, CaptionBinding],
    d: SegmentDecision,
    spine: SpineConfig,
) -> tuple[SegmentDecision, list[SpineCorrection], bool]:
    """Inject outer context from caption if applicable.

    Parameters
    ----------
    caption_bindings
        Caption bindings for the decision.
    d
        The SegmentDecision to inject into.
    spine
        The SpineConfig defining the injection policy.

    Returns
    -------
    tuple[SegmentDecision, list[SpineCorrection], bool]
        - The corrected SegmentDecision.
        - Any corrections made.
        - True if the decision was flagged as unresolved due to injection conflicts.
    """

    if d.segment_kind != "table":
        return d, [], False

    caption_binding = caption_bindings.get(d.segment_id)

    if caption_binding and caption_binding.caption_text:
        return _inject_outer_context_from_caption(
            caption_text=caption_binding.caption_text, decision=d, spine=spine
        )

    return d, [], False


def _inject_outer_context_from_caption(
    *, caption_text: str, decision: SegmentDecision, spine: SpineConfig
) -> tuple[SegmentDecision, list[SpineCorrection], bool]:
    """Use caption text as deterministic evidence to *inject missing* outer-context
    anchors into decision.context_groupings.

    NB:

    1. Only use config-driven split_rules to derive groupings from caption text.
    2. Never overwrite an existing non-droppable anchor role with a different title.
    3. If a real conflict exists, flag unresolved (per spine.violation_policy) and do
        NOT inject.
    4. Ignore existing wrapper-ish STAGE and grade-band GRADE_LEVEL when checking
        conflicts, because those will be dropped by _apply_outer_normalizations anyway.

    Parameters
    ----------
    caption_text
        The caption text to extract context from.
    decision
        The SegmentDecision to inject into.
    spine
        The SpineConfig defining the injection policy.

    Returns
    -------
    tuple[SegmentDecision, list[SpineCorrection], bool]
        - The corrected SegmentDecision.
        - Any corrections made.
        - True if the decision was flagged as unresolved due to injection conflicts.
    """

    corrections: list[SpineCorrection] = []
    caption_text = (caption_text or "").strip()

    if not caption_text:
        return decision, corrections, False

    # Normalize caption whitespace in the same way as grouping normalization.
    if spine.normalize.normalize_whitespace:
        caption_text = " ".join(caption_text.split()).strip()

    # Derive groupings and apply split rules.
    derived, split_corr = _derive_groupings_from_caption(
        caption_text=caption_text, spine=spine
    )
    corrections.extend(split_corr)

    if not derived:
        return decision, corrections, False

    # Filter roles.
    derived = _filter_derived_roles(decision=decision, derived=derived, spine=spine)

    if not derived:
        return decision, corrections, False

    # Analyze existing context (identify conflicts vs. merges).
    existing_all = list(decision.context_groupings or [])

    # Determine if Grade Level exists in current OR derived to determine whether or not
    # to drop.
    grade_present_after = any(
        g.role == NodeRole.GRADE_LEVEL for g in existing_all
    ) or any(g.role == NodeRole.GRADE_LEVEL for g in derived)

    existing_effective_map = _get_effective_existing_anchors(
        existing_all=existing_all, grade_present_after=grade_present_after, spine=spine
    )

    # Check conflicts.
    conflict_msgs = _detect_caption_conflicts(
        derived=derived,
        existing_map=existing_effective_map,
        raw_caption=caption_text,
        spine=spine,
    )

    if conflict_msgs:
        return _handle_caption_conflicts(
            decision=decision, msgs=conflict_msgs, spine=spine
        )

    # Merge provenances and inject new items.
    new_context, merge_corrections = _merge_and_inject(
        derived=derived,
        existing_all=existing_all,
        existing_map=existing_effective_map,
        spine=spine,
    )

    decision.context_groupings = new_context
    corrections.extend(merge_corrections)

    return decision, corrections, False


def _is_droppable_existing(
    *,
    g: GroupingDecision,
    grade_band_pats: list[re.Pattern],
    grade_present_after: bool,
    spine: SpineConfig,
    stage_pats: list[re.Pattern],
) -> bool:
    """Determine if an anchor is weak/droppable.

    Parameters
    ----------
    g
        The GroupingDecision to check.
    grade_band_pats
        Compiled patterns for grade band titles.
    grade_present_after
        True if a GRADE_LEVEL grouping is present either in existing or derived
        groupings.
    spine
        The SpineConfig defining the normalization policy.
    stage_pats
        Compiled patterns for stage wrapper titles.

    Returns
    -------
    bool
        True if the anchor is droppable.
    """

    if g.role in {NodeRole.PROSE}:
        return True

    if g.role == NodeRole.STAGE:
        if spine.normalize.drop_stage_if_grade_present and grade_present_after:
            return True
        if stage_pats and any(p.search(g.title or "") for p in stage_pats):
            return True

    if (
        g.role == NodeRole.GRADE_LEVEL
        and grade_band_pats
        and any(p.search(g.title or "") for p in grade_band_pats)
    ):
        return True

    return False


def _is_grade_level_present(
    *, derived: list[GroupingDecision], existing: list[GroupingDecision]
) -> bool:
    """Determine if a GRADE_LEVEL grouping is present in either existing or derived.

    Parameters
    ----------
    derived
        The list of derived GroupingDecision.
    existing
        The list of existing GroupingDecision.

    Returns
    -------
    bool
        True if a GRADE_LEVEL grouping is present in either list.
    """

    return any(g.role == NodeRole.GRADE_LEVEL for g in existing) or any(
        g.role == NodeRole.GRADE_LEVEL for g in derived
    )


def _merge_and_inject(
    *,
    derived: list[GroupingDecision],
    existing_all: list[GroupingDecision],
    existing_map: dict[NodeRole, str],
    spine: SpineConfig,
) -> tuple[list[GroupingDecision], list[SpineCorrection]]:
    """Merge provenance for existing matches or inject new items.

    Parameters
    ----------
    derived
        The list of derived GroupingDecision.
    existing_all
        The list of existing GroupingDecision.
    existing_map
        The map of existing effective anchors by role to normalized title.
    spine
        The SpineConfig defining the normalization policy.

    Returns
    -------
    tuple[list[GroupingDecision], list[SpineCorrection]]
        - The merged list of GroupingDecision.
        - Any corrections made.
    """

    # No conflicts: inject only missing roles and optionally merge provenance.
    additions: list[GroupingDecision] = []
    casefold = spine.normalize.casefold_titles_for_matching
    corrections: list[SpineCorrection] = []

    # Start with the existing context; we will rebuild this list if updates occur.
    current_context = list(existing_all)

    for cg in derived:
        # If the role is not known in our 'effective' map, it's a new addition.
        if cg.role not in existing_map:
            additions.append(cg)
            corrections.append(
                SpineCorrection(
                    detail=f"Injected {cg.role.value}='{cg.title}' from caption_binding.",
                    kind="caption_inject",
                )
            )
            continue

        # If we are here, the role exists. We know from the previous conflict check
        # that the titles match (normalized). We try to merge provenance if missing.
        candidate_norm = _norm_title(casefold=casefold, title=cg.title)
        next_context_state = []
        merged_in_this_pass = False

        for eg in current_context:
            # We want to find the specific existing item that matches this role/title
            # and needs a source_label.
            if (
                not merged_in_this_pass
                and eg.role == cg.role
                and eg.source_label is None
                and cg.source_label
                and _norm_title(casefold=casefold, title=eg.title) == candidate_norm
            ):
                # Update the item with the new source_label.
                next_context_state.append(
                    eg.model_copy(update={"source_label": cg.source_label})
                )
                corrections.append(
                    SpineCorrection(
                        detail=(
                            f"Filled missing source_label for {cg.role.value} '{eg.title}' "
                            f"from caption_binding."
                        ),
                        kind="caption_merge_provenance",
                    )
                )
                merged_in_this_pass = True
            else:
                next_context_state.append(eg)

        # Update the context state for the next iteration of 'derived'.
        current_context = next_context_state

    return current_context + additions, corrections


def _merge_grouping_metadata_keep_first(
    *, incoming: GroupingDecision, keep: GroupingDecision
) -> tuple[GroupingDecision, list[str]]:
    """Deterministically merge metadata into `keep` WITHOUT changing its role/title.
    Rule: keep-first for conflicting values; only fill missing fields.

    Parameters
    ----------
    incoming
        The incoming GroupingDecision to merge from.
    keep
        The GroupingDecision to keep and merge into.

    Returns
    -------
    tuple[GroupingDecision, list[str]]
        - The merged GroupingDecision (based on `keep`).
        - Notes describing any merges or conflicts observed.
    """

    notes: list[str] = []
    updates: dict = {}

    if keep.local_code is None and incoming.local_code:
        updates["local_code"] = incoming.local_code
        notes.append(
            f"merged local_code='{incoming.local_code}' into kept {keep.role.value} '{keep.title}'"
        )
    elif (
        keep.local_code
        and incoming.local_code
        and keep.local_code != incoming.local_code
    ):
        # Keep-first; record that we observed a conflict.
        notes.append(
            f"local_code conflict for {keep.role.value} '{keep.title}': "
            f"kept='{keep.local_code}' dropped='{incoming.local_code}'"
        )

    if keep.source_label is None and incoming.source_label:
        updates["source_label"] = incoming.source_label
        notes.append(
            f"merged source_label='{incoming.source_label}' into kept {keep.role.value} '{keep.title}'"
        )
    elif (
        keep.source_label
        and incoming.source_label
        and keep.source_label != incoming.source_label
    ):
        notes.append(
            f"source_label conflict for {keep.role.value} '{keep.title}': "
            f"kept='{keep.source_label}' dropped='{incoming.source_label}'"
        )

    if updates:
        keep = keep.model_copy(update=updates)

    return keep, notes


def _norm_title(*, casefold: bool, title: str) -> str:
    """Normalize a title for matching/deduping.

    Parameters
    ----------
    casefold
        If True, casefold the title.
    title
        The title to normalize.

    Returns
    -------
    str
        The normalized title.
    """

    t = " ".join((title or "").split()).strip()

    return t.casefold() if casefold else t


def _outer_context_conflicts(
    *,
    candidate_ctx: list[GroupingDecision],
    canonical_ctx: list[GroupingDecision],
    casefold_titles_for_matching: bool,
    compare_roles: set[NodeRole] | None = None,
) -> tuple[bool, list[str]]:
    """Conservative chunk conflict check for table chunk decisions.

    Defaults:

    1. Compare only "outer anchor" roles (exclude TOPIC/SUBTOPIC/SECTION/PROSE)
    2. Missing roles are NOT conflicts
    3. Differing titles for the same role ARE conflicts

    Parameters
    ----------
    candidate_ctx
        Candidate outer context to check.
    canonical_ctx
        Canonical outer context to check against.
    casefold_titles_for_matching
        If True, casefold titles when normalizing for matching.
    compare_roles
        If provided, the set of roles to compare. If None, defaults to outer anchor
        roles (excluding TOPIC/SUBTOPIC/SECTION/PROSE).

    Returns
    -------
    tuple[bool, list[str]]
        - True if conflicts were found, False otherwise.
        - List of conflict detail messages.
    """

    details: list[str] = []

    # Default anchor set: exclude roles that commonly drift locally or are
    # non-curricular wrappers.
    if compare_roles is None:
        compare_roles = (
            {g.role for g in canonical_ctx} | {g.role for g in candidate_ctx}
        ) - {NodeRole.TOPIC, NodeRole.SUBTOPIC, NodeRole.SECTION, NodeRole.PROSE}

    def to_map(ctx: list[GroupingDecision]) -> dict[NodeRole, str]:
        """Convert a list of GroupingDecision to a map of role -> normalized title.

        Parameters
        ----------
        ctx
            The list of GroupingDecision to convert.

        Returns
        -------
        dict[NodeRole, str]
            The resulting map of role to normalized title.
        """

        # If there are multiple per role, keep FIRST in encounter order (deterministic).
        m: dict[NodeRole, str] = {}

        for g in ctx:
            if g.role in compare_roles and g.role not in m:
                m[g.role] = _norm_title(
                    casefold=casefold_titles_for_matching, title=g.title
                )

        return m

    a = to_map(canonical_ctx)
    b = to_map(candidate_ctx)

    for role in sorted(compare_roles, key=lambda r: r.value):
        # Absence is not a conflict.
        if role not in a or role not in b:
            continue

        if a[role] != b[role]:
            details.append(
                f"outer_context_conflict: role={role.value} canonical='{a[role]}' candidate='{b[role]}'"
            )

    return (len(details) > 0), details


def _relocate_table_local_only_roles(
    *, decision: SegmentDecision, spine: SpineConfig
) -> tuple[SegmentDecision, list[SpineCorrection], bool]:
    """Move any spine.table_local_only_roles out of decision.context_groupings and into
    each row.groupings.

    Rules:

    1. Never introduces duplicate roles into a row.
    2. If a row already has the same role but a *different* title, we cannot safely
        resolve -> flag unresolved.

    Parameters
    ----------
    decision
        The SegmentDecision to correct.
    spine
        The SpineConfig defining the correction policy.

    Returns
    -------
    tuple[SegmentDecision, list[SpineCorrection], bool]
        - The corrected SegmentDecision.
        - Any corrections made.
        - Whether the decision was flagged as unresolved.
    """

    corrections: list[SpineCorrection] = []
    local_only = set(spine.table_local_only_roles)

    if not local_only:
        return decision, corrections, False

    moved: list[GroupingDecision] = [
        g for g in (decision.context_groupings or []) if g.role in local_only
    ]

    if not moved:
        return decision, corrections, False

    # If there are no rows, we cannot relocate deterministically.
    if not decision.rows:
        if spine.violation_policy == SpineViolationPolicy.FLAG_UNRESOLVED:
            decision.decision_type = SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED
            corrections.append(
                SpineCorrection(
                    kind="flag_unresolved",
                    detail="No rows present to relocate table-local-only roles into; flagged unresolved.",
                )
            )
            return decision, corrections, True

        # Keep as is and do nothing.
        return decision, corrections, False

    # Remove moved roles from outer context.
    decision.context_groupings = [
        g for g in (decision.context_groupings or []) if g.role not in local_only
    ]
    corrections.append(
        SpineCorrection(
            detail=f"Moved {len(moved)} table-local-only grouping(s) from context to rows.",
            kind="relocate",
        )
    )

    # Inject moved roles into each row, and avoid duplicates/contradictions.
    flagged = False
    new_rows: list[RowDecision] = []

    for r in decision.rows:
        r2 = r.model_copy(deep=True)
        additions: list[GroupingDecision] = []
        existing = list(r2.groupings or [])
        existing_by_role = {
            eg.role: _norm_title(
                casefold=spine.normalize.casefold_titles_for_matching, title=eg.title
            )
            for eg in existing
        }

        for mg in moved:
            if mg.role in existing_by_role:
                # If same role exists but different title -> contradiction.
                if existing_by_role[mg.role] != _norm_title(
                    casefold=spine.normalize.casefold_titles_for_matching,
                    title=mg.title,
                ):
                    actual_title = next(e.title for e in existing if e.role == mg.role)
                    msg = (
                        f"Row has conflicting {mg.role.value}: "
                        f"outer='{mg.title}' row='{actual_title}'"
                    )
                    corrections.append(
                        SpineCorrection(kind="relocate_conflict", detail=msg)
                    )
                    if spine.violation_policy == SpineViolationPolicy.FLAG_UNRESOLVED:
                        # NB: Do NOT flip the entire decision to
                        # EMIT_FLAGGED_UNRESOLVED for a single row-level contradiction.
                        # We can still materialize the table by simply not injecting
                        # the conflicting moved role into this row.
                        flagged = True

                    # Do not add conflicting mg.
                    continue

                # Same title -> skip (avoid duplicate role).
                continue

            additions.append(mg)

        r2.groupings = additions + existing
        new_rows.append(r2)

    decision.rows = new_rows

    return decision, corrections, flagged


def _relocate_local_roles(
    *, d: SegmentDecision, spine: SpineConfig
) -> tuple[SegmentDecision, list[SpineCorrection], bool]:
    """Relocate table-local roles if configured.

    Parameters
    ----------
    d
        The SegmentDecision to correct.
    spine
        The SpineConfig defining the correction policy.

    Returns
    -------
    tuple[SegmentDecision, list[SpineCorrection], bool]
        - The corrected SegmentDecision.
        - Any corrections made.
        - Whether the decision was flagged as unresolved.
    """

    if d.segment_kind == "table" and spine.relocate_table_local_only_roles_to_rows:
        return _relocate_table_local_only_roles(decision=d, spine=spine)

    return d, [], False


def apply_spine_policy_to_decision_set(
    *,
    caption_bindings: dict[str, CaptionBinding],
    creation_dirs: CanonicalIRDirs,
    decision_set: SegmentDecisionSet,
    document_ir: DocumentIR,
    overwrite: bool,
    spine: SpineConfig,
) -> SegmentDecisionSet:
    """Apply spine correction policy to a SegmentDecisionSet.

    Parameters
    ----------
    caption_bindings
        Caption bindings for the decision set.
    creation_dirs
        The canonical IR creation directories.
    decision_set
        The SegmentDecisionSet to correct.
    document_ir
        The DocumentIR corresponding to the decision set.
    overwrite
        If True, overwrite existing spine corrected decision set files.
    spine
        The SpineConfig defining the correction policy.

    Returns
    -------
    SegmentDecisionSet
        The corrected SegmentDecisionSet.
    """

    spine_out_fp = creation_dirs.root / "segment_decisions_spine_corrected.json"
    spine_report_fp = creation_dirs.root / "spine_report.json"

    if not overwrite and spine_out_fp.exists() and spine_report_fp.exists():
        logger.warning(
            f"Spine corrected JSON already exists at {spine_out_fp}. Skipping correction. "
            f"If you wish to overwrite, pass the --overwrite flag."
        )
        return SegmentDecisionSet.model_validate(open_json_type(spine_out_fp))

    # Group decisions by segment_id.
    decisions_by_segment: dict[str, list[SegmentDecision]] = {}
    for d in decision_set.decisions:
        decisions_by_segment.setdefault(d.segment_id, []).append(d)

    corrected_decisions: list[SegmentDecision] = []
    spine_report = SpineCorrectionReport(
        decision_results={},
        decision_set_id="",  # Will be set to the corrected decision set ID below
        flagged_decisions=[],
        spine_name=spine.name,
        warnings=[],
    )

    # Walk segments in DocumentIR order.
    for segment in document_ir.segments:
        segment_decisions = decisions_by_segment.get(segment.segment_id, [])

        if not segment_decisions:
            continue

        segment_decisions_sorted = sorted(
            segment_decisions,
            key=lambda d: (
                d.row_range_start if d.row_range_start is not None else -1,
                d.row_range_end if d.row_range_end is not None else 10**18,
                d.decision_id,
            ),
        )

        # For chunk consistency within the same segment.
        canonical_outer_context: list[GroupingDecision] | None = None

        for d in segment_decisions_sorted:
            result = _apply_spine_to_single_decision(
                canonical_outer_context=canonical_outer_context,
                caption_bindings=caption_bindings,
                decision=d,
                spine=spine,
            )
            corrected_decisions.append(result.corrected)
            spine_report.decision_results[result.corrected.decision_id] = (
                result.corrections
            )

            if result.flagged_unresolved:
                spine_report.flagged_decisions.append(result.corrected.decision_id)

            # Establish canonical context from first non-flagged corrected decision.
            if canonical_outer_context is None and not result.flagged_unresolved:
                canonical_outer_context = list(result.corrected.context_groupings or [])

    # NB: Corrected decisions means corrected decision_set_id must be recomputed.
    new_decision_set_id = compute_decision_set_id(decisions=corrected_decisions)
    spine_report.decision_set_id = new_decision_set_id

    if new_decision_set_id != decision_set.decision_set_id:
        spine_report.warnings.append(
            f"decision_set_id_changed: "
            f"input={decision_set.decision_set_id} "
            f"output={new_decision_set_id}"
        )

    segment_decisions = SegmentDecisionSet(
        decision_set_id=new_decision_set_id,
        decisions=corrected_decisions,
        doc_key=decision_set.doc_key,
        generator="spine_corrector",
        pdf_name=decision_set.pdf_name,
    )

    write_to_json(fp=spine_out_fp, json_info=segment_decisions)
    write_to_json(fp=spine_report_fp, json_info=spine_report)

    logger.info(f"Saved spine-corrected decisions to: {spine_out_fp}")
    logger.info(f"Saved spine correction report to: {spine_report_fp}")

    return segment_decisions
