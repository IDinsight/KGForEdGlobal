"""This module contains the spine corrector logic for applying spine policies to
SegmentDecisionSets.

Responsibilities:

1. Caption context injection: derive missing outer-context anchors from table captions.
2. Role allow/disallow filtering: enforce where specific roles may appear.
3. Table-local-only role relocation: move roles from context to row groupings.
4. Cross-chunk consistency check: detect conflicting context across table chunks.
5. Hard shape enforcement: clear outputs for IGNORE/UNRESOLVED; demote empty flagged.

Title normalization, deduplication, precedence reordering, regex-based splitting, and
wrapper/grade-band dropping are handled by the segment decision cleaning and LLM
grouping canonicalization steps.
"""

# Standard Library
import unicodedata

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
    SpineViolationPolicy,
)
from skg.utils.general import open_json_type, write_to_json


@dataclass
class SpineDecisionResult:
    """Result of applying spine correction to a single SegmentDecision."""

    corrected: SegmentDecision
    corrections: list[SpineCorrection]
    flagged_unresolved: bool


def _apply_spine_to_single_decision(
    *,
    canonical_outer_context: list[GroupingDecision] | None,
    caption_bindings: dict[str, CaptionBinding],
    decision: SegmentDecision,
    spine_policy: SpineConfig,
) -> SpineDecisionResult:
    """Apply spine correction policy to a single SegmentDecision.

    The process is as follows:

    1. Inject outer context from caption (if applicable).
    2. Filter outer context by role policy.
    3. Relocate table-local-only roles from context to rows.
    4. Filter local groupings (block-local or row-local) by role policy.
    5. Cross-chunk consistency check (for tables with canonical_outer_context).
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
    spine_policy
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
    d, c = _inject_caption_context(
        caption_bindings=caption_bindings, d=d, spine_policy=spine_policy
    )
    corrections.extend(c)

    # 2.
    d, c = _correct_outer_context(d=d, spine_policy=spine_policy)
    corrections.extend(c)

    # 3.
    d, c = _relocate_local_roles(d=d, spine_policy=spine_policy)
    corrections.extend(c)

    # 4.
    d, c = _correct_local_structure(d=d, spine_policy=spine_policy)
    corrections.extend(c)

    # 5.
    c = _check_canonical_consistency(
        d=d, canonical_ctx=canonical_outer_context, spine_policy=spine_policy
    )
    corrections.extend(c)

    # 6.
    if d.decision_type in (SegmentDecisionType.IGNORE, SegmentDecisionType.UNRESOLVED):
        if d.context_groupings or d.groupings or d.leaves or d.rows:
            corrections.append(
                SpineCorrection(
                    detail=f"Cleared context/outputs for decision_type={d.decision_type.value}.",
                    kind="clear_noop",
                )
            )

        d.context_groupings = []
        d.groupings = []
        d.leaves = []
        d.rows = []

    if d.decision_type == SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED:
        has_any_output = bool(d.context_groupings or d.groupings or d.leaves or d.rows)

        if not has_any_output:
            corrections.append(
                SpineCorrection(
                    detail=(
                        "Demoted to UNRESOLVED because flagged_unresolved emitted nothing "
                        "(context_groupings/groupings/leaves/rows all empty)."
                    ),
                    kind="demote_flagged_unresolved_empty",
                )
            )
            d.context_groupings = []
            d.decision_type = SegmentDecisionType.UNRESOLVED
            d.groupings = []
            d.leaves = []
            d.rows = []

    try:
        d = SegmentDecision.model_validate(d.model_dump())
    except Exception as e:  # pylint: disable=broad-exception-caught
        corrections.append(
            SpineCorrection(
                detail=f"Post-spine revalidation failed; demoted to UNRESOLVED. err={e!s}",
                kind="revalidate_failed",
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

    return SpineDecisionResult(
        corrected=d,
        corrections=corrections,
        flagged_unresolved=(
            d.decision_type == SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED
        ),
    )


def _check_canonical_consistency(
    *,
    d: SegmentDecision,
    canonical_ctx: list[GroupingDecision] | None,
    spine_policy: SpineConfig,
) -> list[SpineCorrection]:
    """Check consistency with canonical outer context (cross-chunk).

    Parameters
    ----------
    d
        The SegmentDecision to check.
    canonical_ctx
        The canonical outer context for the segment (if any).
    spine_policy
        The SpineConfig defining the correction policy.

    Returns
    -------
    list[SpineCorrection]
        List containing any corrections made.
    """

    corrections: list[SpineCorrection] = []

    if canonical_ctx is not None and d.segment_kind == "table":
        has_conflict, details = _outer_context_conflicts(
            canonical_ctx=canonical_ctx,
            candidate_ctx=list(d.context_groupings or []),
            casefold_titles_for_matching=spine_policy.casefold_titles_for_matching,
            compare_roles=(
                set(spine_policy.outer_context_roles)
                if spine_policy.outer_context_roles
                else None
            ),
        )

        if has_conflict:
            for msg in details:
                corrections.append(
                    SpineCorrection(detail=msg, kind="chunk_context_conflict")
                )

            if spine_policy.violation_policy == SpineViolationPolicy.FLAG_UNRESOLVED:
                d.decision_type = SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED

    return corrections


def _correct_local_structure(
    *, d: SegmentDecision, spine_policy: SpineConfig
) -> tuple[SegmentDecision, list[SpineCorrection]]:
    """Filter block-local or table-row-local groupings by role policy.

    Parameters
    ----------
    d
        The SegmentDecision to correct.
    spine_policy
        The SpineConfig defining the correction policy.

    Returns
    -------
    tuple[SegmentDecision, list[SpineCorrection]]
        - The corrected SegmentDecision.
        - Any corrections made.
    """

    corrections: list[SpineCorrection] = []

    if d.segment_kind == "block":
        d.groupings, c = _filter_roles(
            allow=spine_policy.block_local_roles,
            disallow=set(spine_policy.disallowed_block_local_roles),
            groupings=list(d.groupings or []),
        )
        corrections.extend(c)
    elif d.segment_kind == "table" and d.rows:
        new_rows: list[RowDecision] = []

        for row in d.rows:
            row2 = row.model_copy(deep=True)
            row2.groupings, c = _filter_roles(
                allow=spine_policy.row_roles,
                disallow=set(spine_policy.disallowed_row_roles),
                groupings=list(row2.groupings or []),
            )
            corrections.extend(c)

            # If spine removed all row-local structure and the row has no leaves, drop
            # it.
            if not row2.groupings and not row2.leaves:
                corrections.append(
                    SpineCorrection(
                        detail=(
                            f"Dropped RowDecision row_index={row2.row_index} "
                            f"(no groupings/leaves after role filtering)."
                        ),
                        kind="drop_empty_row",
                    )
                )
                continue

            new_rows.append(row2)

        d.rows = new_rows

    return d, corrections


def _correct_outer_context(
    *, d: SegmentDecision, spine_policy: SpineConfig
) -> tuple[SegmentDecision, list[SpineCorrection]]:
    """Filter outer context by role policy.

    Parameters
    ----------
    d
        The SegmentDecision to correct.
    spine_policy
        The SpineConfig defining the correction policy.

    Returns
    -------
    tuple[SegmentDecision, list[SpineCorrection]]
        - The corrected SegmentDecision.
        - Any corrections made.
    """

    corrections: list[SpineCorrection] = []

    # Build the effective allow-list: include table-local-only roles long enough to
    # relocate them in the next step.
    outer_allow = list(spine_policy.outer_context_roles or [])

    if (
        d.segment_kind == "table"
        and spine_policy.relocate_table_local_only_roles_to_rows
    ):
        outer_allow = list(
            dict.fromkeys(outer_allow + list(spine_policy.table_local_only_roles or []))
        )

    original_groupings = list(d.context_groupings or [])

    # Filter by role policy.
    d.context_groupings, c = _filter_roles(
        allow=outer_allow,
        disallow=set(spine_policy.disallowed_outer_roles),
        groupings=original_groupings,
    )
    corrections.extend(c)

    # Check if outer context became empty after filtering (partial context check).
    if (
        not d.context_groupings
        and original_groupings
        and not spine_policy.allow_partial_context
        and d.decision_type
        not in (SegmentDecisionType.IGNORE, SegmentDecisionType.UNRESOLVED)
        and spine_policy.violation_policy == SpineViolationPolicy.FLAG_UNRESOLVED
    ):
        corrections.append(
            SpineCorrection(
                detail="Outer context became empty after role filtering.",
                kind="outer_context_would_be_empty",
            )
        )
        d.decision_type = SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED

    return d, corrections


def _derive_groupings_from_caption(
    *, caption_text: str, spine_policy: SpineConfig
) -> tuple[list[GroupingDecision], list[SpineCorrection]]:
    """Extract groupings from caption text using `spine.caption_context_patterns`.

    Each pattern is searched (not full-match) against the caption text. The first match
    per role wins.

    Parameters
    ----------
    caption_text
        The caption text to extract groupings from.
    spine_policy
        The SpineConfig containing ``caption_context_patterns``.

    Returns
    -------
    tuple[list[GroupingDecision], list[SpineCorrection]]
        - The derived list of GroupingDecision.
        - Any corrections made.
    """

    corrections: list[SpineCorrection] = []
    derived: list[GroupingDecision] = []
    seen_roles: set[NodeRole] = set()

    for pattern in spine_policy.caption_context_patterns:
        # One grouping per role (first-match wins).
        if pattern.role in seen_roles:
            continue

        m = pattern._compiled_re.search(caption_text)

        if not m:
            continue

        title = m.expand(pattern.title_template).strip()

        if not title:
            continue

        seen_roles.add(pattern.role)
        derived.append(
            GroupingDecision(
                local_code=None,
                role=pattern.role,
                source_label="caption_binding",
                title=title,
            )
        )

    if derived:
        corrections.append(
            SpineCorrection(
                detail=(
                    f"Derived {len(derived)} grouping(s) from caption: "
                    f"{[f'{g.role.value}={g.title!r}' for g in derived]}"
                ),
                kind="caption_derive",
            )
        )

    return derived, corrections


def _filter_derived_roles(
    *,
    decision: SegmentDecision,
    derived: list[GroupingDecision],
    spine_policy: SpineConfig,
) -> list[GroupingDecision]:
    """Filter the derived groupings based on allowed outer context roles.

    Parameters
    ----------
    decision
        The SegmentDecision being processed.
    derived
        The list of derived GroupingDecision.
    spine_policy
        The SpineConfig defining the filtering policy.

    Returns
    -------
    list[GroupingDecision]
        The filtered list of GroupingDecision.
    """

    allowed_outer = set(spine_policy.outer_context_roles or [])

    if (
        decision.segment_kind == "table"
        and spine_policy.relocate_table_local_only_roles_to_rows
    ):
        allowed_outer |= set(spine_policy.table_local_only_roles or [])

    if allowed_outer:
        return [g for g in derived if g.role in allowed_outer]

    return derived


def _filter_roles(
    *, allow: list[NodeRole], disallow: set[NodeRole], groupings: list[GroupingDecision]
) -> tuple[list[GroupingDecision], list[SpineCorrection]]:
    """Filter a grouping list by role allow/disallow policy.

    Parameters
    ----------
    allow
        Allow-list of roles. If non-empty, only these roles are kept.
    disallow
        Disallow-list of roles. These roles are always dropped.
    groupings
        The list of GroupingDecision to filter.

    Returns
    -------
    tuple[list[GroupingDecision], list[SpineCorrection]]
        - The filtered list of GroupingDecision.
        - Any corrections made.
    """

    corrections: list[SpineCorrection] = []
    allowed_set = set(allow)
    filtered: list[GroupingDecision] = []

    for g in groupings:
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

    return filtered, corrections


def _handle_caption_conflicts(
    *, decision: SegmentDecision, msgs: list[str], spine_policy: SpineConfig
) -> tuple[SegmentDecision, list[SpineCorrection]]:
    """Generate corrections and flag the decision if necessary.

    Parameters
    ----------
    decision
        The SegmentDecision to handle.
    msgs
        The list of conflict messages detected.
    spine_policy
        The SpineConfig defining the violation policy.

    Returns
    -------
    tuple[SegmentDecision, list[SpineCorrection]]
        - The (possibly flagged) SegmentDecision.
        - Any corrections made.
    """

    corrections = [
        SpineCorrection(detail=msg, kind="caption_context_conflict") for msg in msgs
    ]

    if (
        spine_policy.violation_policy == SpineViolationPolicy.FLAG_UNRESOLVED
        and decision.decision_type
        not in (SegmentDecisionType.IGNORE, SegmentDecisionType.UNRESOLVED)
    ):
        decision.decision_type = SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED

    return decision, corrections


def _inject_caption_context(
    *,
    caption_bindings: dict[str, CaptionBinding],
    d: SegmentDecision,
    spine_policy: SpineConfig,
) -> tuple[SegmentDecision, list[SpineCorrection]]:
    """Inject outer context from caption if applicable.

    Parameters
    ----------
    caption_bindings
        Caption bindings for the decision.
    d
        The SegmentDecision to inject into.
    spine_policy
        The SpineConfig defining the injection policy.

    Returns
    -------
    tuple[SegmentDecision, list[SpineCorrection]]
        - The corrected SegmentDecision.
        - Any corrections made.
    """

    if d.segment_kind != "table":
        return d, []

    caption_binding = caption_bindings.get(d.segment_id)

    if caption_binding and caption_binding.caption_text:
        return _inject_outer_context_from_caption(
            caption_text=caption_binding.caption_text,
            decision=d,
            spine_policy=spine_policy,
        )

    return d, []


def _inject_outer_context_from_caption(
    *, caption_text: str, decision: SegmentDecision, spine_policy: SpineConfig
) -> tuple[SegmentDecision, list[SpineCorrection]]:
    """Use caption text as deterministic evidence to *inject missing* outer-context
    anchors into `decision.context_groupings`.

    Rules:

    1. Use `caption_context_patterns` to derive groupings from caption text.
    2. Never overwrite an existing anchor role with a different title.
    3. If a real conflict exists, flag unresolved (per `spine.violation_policy`) and do
       NOT inject.

    Parameters
    ----------
    caption_text
        The caption text to extract context from.
    decision
        The SegmentDecision to inject into.
    spine_policy
        The SpineConfig defining the injection policy.

    Returns
    -------
    tuple[SegmentDecision, list[SpineCorrection]]
        - The corrected SegmentDecision.
        - Any corrections made.
    """

    corrections: list[SpineCorrection] = []
    caption_text = (caption_text or "").strip()

    if not caption_text:
        return decision, corrections

    # Derive groupings from caption patterns.
    derived, derive_corr = _derive_groupings_from_caption(
        caption_text=caption_text, spine_policy=spine_policy
    )
    corrections.extend(derive_corr)

    if not derived:
        return decision, corrections

    # Filter by allowed outer roles.
    derived = _filter_derived_roles(
        decision=decision, derived=derived, spine_policy=spine_policy
    )

    if not derived:
        return decision, corrections

    # Build existing context map for conflict detection.
    casefold = spine_policy.casefold_titles_for_matching
    existing = list(decision.context_groupings or [])
    existing_by_role = {
        g.role: _norm_title(casefold=casefold, title=g.title)
        for g in reversed(existing)
    }

    # Detect conflicts (same role, different normalized title).
    conflict_msgs: list[str] = []

    for cg in derived:
        if cg.role not in existing_by_role:
            continue

        derived_norm = _norm_title(casefold=casefold, title=cg.title)

        if existing_by_role[cg.role] != derived_norm:
            conflict_msgs.append(
                f"caption_context_conflict: role={cg.role.value} "
                f"existing='{existing_by_role[cg.role]}' caption='{derived_norm}' "
                f"(raw_caption='{caption_text}')"
            )

    if conflict_msgs:
        return _handle_caption_conflicts(
            decision=decision, msgs=conflict_msgs, spine_policy=spine_policy
        )

    # Inject only missing roles (no conflict case).
    additions: list[GroupingDecision] = []

    for cg in derived:
        if cg.role not in existing_by_role:
            additions.append(cg)
            corrections.append(
                SpineCorrection(
                    detail=f"Injected {cg.role.value}='{cg.title}' from caption_binding.",
                    kind="caption_inject",
                )
            )

    decision.context_groupings = existing + additions

    return decision, corrections


def _norm_title(*, casefold: bool, title: str) -> str:
    """Normalize a title for matching/conflict detection.

    Parameters
    ----------
    casefold
        If True, casefold the title and strip combining marks (accents) so that (e.g.)
        "Mathématiques" and "MATHEMATIQUES" compare as equal.
    title
        The title to normalize.

    Returns
    -------
    str
        The normalized title.
    """

    t = " ".join((title or "").split()).strip()

    if casefold:
        t = t.casefold()

        # Strip combining marks (accents) for robust cross-transcription matching (e.g.
        # 'é' (U+00E9) → NFKD → 'e' + '\u0301' → strip Mn → 'e').
        t = "".join(
            c
            for c in unicodedata.normalize("NFKD", t)
            if unicodedata.category(c) != "Mn"
        )

    return t


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
        """Convert a list of GroupingDecision to a map of role → normalized title.

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


def _relocate_local_roles(
    *, d: SegmentDecision, spine_policy: SpineConfig
) -> tuple[SegmentDecision, list[SpineCorrection]]:
    """Relocate table-local roles if configured.

    Parameters
    ----------
    d
        The SegmentDecision to correct.
    spine_policy
        The SpineConfig defining the correction policy.

    Returns
    -------
    tuple[SegmentDecision, list[SpineCorrection]]
        - The corrected SegmentDecision.
        - Any corrections made.
    """

    if (
        d.segment_kind == "table"
        and spine_policy.relocate_table_local_only_roles_to_rows
    ):
        return _relocate_table_local_only_roles(decision=d, spine=spine_policy)

    return d, []


def _relocate_table_local_only_roles(
    *, decision: SegmentDecision, spine: SpineConfig
) -> tuple[SegmentDecision, list[SpineCorrection]]:
    """Move any `spine.table_local_only_roles` out of `decision.context_groupings` and
    into each `row.groupings`.

    Rules:

    1. Never introduces duplicate roles into a row.
    2. If a conflict exists between a table-local-only role in context and the same
       role in a row (same role, different title), do NOT relocate that role into that
       row, and record a correction. Do this for each row independently.

    Parameters
    ----------
    decision
        The SegmentDecision to correct.
    spine
        The SpineConfig defining the correction policy.

    Returns
    -------
    tuple[SegmentDecision, list[SpineCorrection]]
        - The corrected SegmentDecision.
        - Any corrections made.
    """

    corrections: list[SpineCorrection] = []
    local_only = set(spine.table_local_only_roles)

    if not local_only:
        return decision, corrections

    moved: list[GroupingDecision] = [
        g for g in (decision.context_groupings or []) if g.role in local_only
    ]

    if not moved:
        return decision, corrections

    # If there are no rows, we cannot relocate deterministically.
    if not decision.rows:
        if spine.violation_policy == SpineViolationPolicy.FLAG_UNRESOLVED:
            decision.decision_type = SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED
            corrections.append(
                SpineCorrection(
                    detail="No rows present to relocate table-local-only roles into; flagged unresolved.",
                    kind="flag_unresolved",
                )
            )
            return decision, corrections

        # Keep as is and do nothing.
        return decision, corrections

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

    casefold = spine.casefold_titles_for_matching

    # Inject moved roles into each row, and avoid duplicates/contradictions.
    new_rows: list[RowDecision] = []

    for r in decision.rows:
        r2 = r.model_copy(deep=True)
        additions: list[GroupingDecision] = []
        existing = list(r2.groupings or [])
        existing_by_role = {
            eg.role: _norm_title(casefold=casefold, title=eg.title) for eg in existing
        }

        for mg in moved:
            if mg.role in existing_by_role:
                # If same role exists but different title → contradiction.
                if existing_by_role[mg.role] != _norm_title(
                    casefold=casefold, title=mg.title
                ):
                    actual_title = next(e.title for e in existing if e.role == mg.role)
                    msg = (
                        f"Row has conflicting {mg.role.value}: "
                        f"outer='{mg.title}' row='{actual_title}'"
                    )
                    corrections.append(
                        SpineCorrection(detail=msg, kind="relocate_conflict")
                    )

                    # NB: Do NOT mark the whole decision as unresolved for a single
                    # row-level contradiction. Record the conflict and avoid injecting.
                    continue

                # Same title -> skip (avoid duplicate role).
                continue

            additions.append(mg)

        r2.groupings = additions + existing
        new_rows.append(r2)

    decision.rows = new_rows

    return decision, corrections


def apply_spine_policy_to_decision_set(
    *,
    caption_bindings: dict[str, CaptionBinding],
    creation_dirs: CanonicalIRDirs,
    decision_set: SegmentDecisionSet,
    document_ir: DocumentIR,
    overwrite: bool,
    spine_policy: SpineConfig,
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
    spine_policy
        The SpineConfig defining the correction policy.

    Returns
    -------
    SegmentDecisionSet
        The corrected SegmentDecisionSet.
    """

    spine_out_fp = (
        creation_dirs.segment_decisions / "segment_decisions_spine_corrected.json"
    )
    spine_report_fp = (
        creation_dirs.segment_decisions / "segment_decisions_spine_report.json"
    )

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
        spine_name=spine_policy.name,
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
                spine_policy=spine_policy,
            )
            corrected_decisions.append(result.corrected)
            spine_report.decision_results[result.corrected.decision_id] = (
                result.corrections
            )

            if result.flagged_unresolved:
                spine_report.flagged_decisions.append(result.corrected.decision_id)

            # Establish canonical context from first non-flagged corrected decision.
            if (
                canonical_outer_context is None
                and not result.flagged_unresolved
                and result.corrected.decision_type
                not in (
                    SegmentDecisionType.IGNORE,
                    SegmentDecisionType.UNRESOLVED,
                )
            ):
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
