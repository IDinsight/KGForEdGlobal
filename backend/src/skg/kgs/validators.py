"""This module contains functionalities related to validating LLM-produced knowledge
graph artifacts.

NB: The Pydantic schemas validate structure and field-level invariants. The validators
in this module enforce quality checks that require access to other inputs.
"""

# Standard Library
import re

from typing import Any, Optional

# Package Library
from skg.kgs.schemas import (
    ExtractionWindow,
    SFICandidate,
    SFIDedupReviewRequest,
    SFIDedupReviewResponse,
    SFIExtractionResult,
    SFIExtractionValidationVerdict,
    SFIHasChildParentCandidate,
    SFIHasChildResolutionRequest,
    SFIHasChildResolutionResponse,
)
from skg.page_ir_extraction.validators import QualityError
from skg.schemas import CreateKGConfig

ACTIVE_OUTLINE_STACK_PARENT_REASON = "active_outline_stack_parent"
CANONICAL_SCOPE_PARENT_MATCH_REASON = "canonical_scope_parent_match"
CODE_PARENT_HINT_REASON = "code_parent_hint"
LOCAL_ACTIVE_OUTLINE_DIRECT_PARENT_REASON = "local_active_outline_direct_parent"
MATCHED_SECTION_PATH_LABEL_REASON = "matched_section_path_label"
NEARBY_SOURCE_CONTEXT_KEY_REASON = "nearby_source_context_key"
NEAREST_PRECEDING_GROUPING_REASON = "nearest_preceding_grouping"
ROOT_EVIDENCE_REASON = "root_fallback"
SAME_SOURCE_CONTEXT_KEY_REASON = "same_source_context_key"
SAME_SOURCE_SEGMENT_REASON = "same_source_segment"
SAME_SOURCE_WINDOW_REASON = "same_source_window"
SAME_TABLE_CONTEXT_REASON = "same_table_context"
SAME_TABLE_IMMEDIATE_PARENT_REASON = "same_table_immediate_parent"
SOURCE_LOCAL_CONTROLLED_PARENT_SCOPE_REASON = "source_local_controlled_parent_scope"
SOURCE_SCOPE_GROUPING_REASON = "source_scope_grouping"
SOURCE_VISIBLE_DIRECT_PARENT_REASON = "source_visible_direct_parent"
STATEMENT_TYPE_COMPATIBLE_REASON = "statement_type_compatible"

CARRY_FORWARD_PARENT_REASONS = frozenset(
    {
        ACTIVE_OUTLINE_STACK_PARENT_REASON,
        MATCHED_SECTION_PATH_LABEL_REASON,
        NEARBY_SOURCE_CONTEXT_KEY_REASON,
        NEAREST_PRECEDING_GROUPING_REASON,
        STATEMENT_TYPE_COMPATIBLE_REASON,
    }
)
HARD_LOCAL_DIRECT_PARENT_REASONS = frozenset(
    {
        CANONICAL_SCOPE_PARENT_MATCH_REASON,
        CODE_PARENT_HINT_REASON,
        LOCAL_ACTIVE_OUTLINE_DIRECT_PARENT_REASON,
        SOURCE_LOCAL_CONTROLLED_PARENT_SCOPE_REASON,
        SAME_TABLE_CONTEXT_REASON,
        SAME_TABLE_IMMEDIATE_PARENT_REASON,
        SOURCE_SCOPE_GROUPING_REASON,
    }
)


def _build_statement_type_alias_map(
    kg_config: CreateKGConfig,
) -> dict[str, str]:
    """Build canonical statement-type lookup from runtime configuration.

    Parameters
    ----------
    kg_config
        Runtime KG configuration.

    Returns
    -------
    dict[str, str]
        Normalized canonical labels and aliases mapped to canonical labels.
    """

    alias_to_canonical: dict[str, str] = {}

    for item in kg_config.academic_standards.statement_type_policy:
        for label in [item.statement_type, *item.aliases]:
            key = _normalize_statement_type_key(label)

            if key:
                alias_to_canonical[key] = item.statement_type

    return alias_to_canonical


def _candidate_direct_parent_evidence_tier(  # pylint: disable=R0911
    *, candidate: SFIHasChildParentCandidate, child_context: Any
) -> int:
    """Assign a dominance tier to one parent candidate.

    Lower tiers are stronger. The validator uses this to reject responses that choose a
    weak nearby or carry-forward parent while a stronger same-type local parent is
    present in the bounded candidate set.

    Parameters
    ----------
    candidate
        Parent candidate being evaluated.
    child_context
        Final child SFI context from the bounded hasChild request.

    Returns
    -------
    int
        Evidence tier, where 0 is source-visible/hard local and larger values are
        weaker retrieval or root fallback evidence.
    """

    evidence_reasons = set(candidate.evidence_reasons or [])

    if candidate.is_root or ROOT_EVIDENCE_REASON in evidence_reasons:
        return 90

    if SOURCE_VISIBLE_DIRECT_PARENT_REASON in evidence_reasons:
        return 0

    if _candidate_has_hard_local_direct_parent_evidence(
        candidate=candidate, child_context=child_context
    ):
        return 1

    if ACTIVE_OUTLINE_STACK_PARENT_REASON in evidence_reasons and (
        SAME_SOURCE_CONTEXT_KEY_REASON in evidence_reasons
        or SAME_SOURCE_SEGMENT_REASON in evidence_reasons
        or SAME_SOURCE_WINDOW_REASON in evidence_reasons
    ):
        return 2

    if _candidate_has_soft_carry_forward_evidence(candidate):
        return 3

    if (
        SAME_SOURCE_CONTEXT_KEY_REASON in evidence_reasons
        or SAME_SOURCE_SEGMENT_REASON in evidence_reasons
        or SAME_SOURCE_WINDOW_REASON in evidence_reasons
    ):
        return 4

    if evidence_reasons & CARRY_FORWARD_PARENT_REASONS:
        return 5

    return 6


def _candidate_has_direct_code_parent_match(
    *, candidate: SFIHasChildParentCandidate, child_context: Any
) -> bool:
    """Check whether a parent candidate is a direct hierarchical code prefix.

    Parameters
    ----------
    candidate
        Parent candidate being evaluated.
    child_context
        Final child SFI context from the bounded hasChild request.

    Returns
    -------
    bool
        True when both child and parent have normalized statement codes and the parent
        code is an exact dot-delimited prefix of the child code.
    """

    child_code = _normalize_code_for_parent_match(
        getattr(child_context, "normalized_statement_code", None)
        or getattr(child_context, "statement_code", None)
    )

    if not child_code:
        child_code = _extract_leading_code_for_parent_match(
            getattr(child_context, "description", None)
        )

    if not child_code:
        for source_text in getattr(child_context, "candidate_source_texts", []) or []:
            child_code = _extract_leading_code_for_parent_match(source_text)

            if child_code:
                break

    parent_code = _normalize_code_for_parent_match(
        candidate.normalized_statement_code or candidate.statement_code
    )
    return bool(child_code and parent_code and child_code.startswith(f"{parent_code}."))


def _candidate_has_hard_local_direct_parent_evidence(
    *, candidate: SFIHasChildParentCandidate, child_context: Any
) -> bool:
    """Check whether a parent candidate has hard local hierarchy evidence.

    Parameters
    ----------
    candidate
        Parent candidate being evaluated.
    child_context
        Final child SFI context from the bounded hasChild request.

    Returns
    -------
    bool
        True when the candidate has code-local, canonical-scope, local active-outline,
        source-local controlled parent scope, same-table, source-scope, or direct
        code-prefix evidence.
    """

    if candidate.is_root:
        return False

    evidence_reasons = set(candidate.evidence_reasons or [])

    return bool(
        evidence_reasons & HARD_LOCAL_DIRECT_PARENT_REASONS
        or _candidate_has_direct_code_parent_match(
            candidate=candidate, child_context=child_context
        )
    )


def _candidate_has_soft_carry_forward_evidence(
    candidate: SFIHasChildParentCandidate,
) -> bool:
    """Check whether a candidate has soft carry-forward hierarchy evidence.

    Parameters
    ----------
    candidate
        Parent candidate being evaluated.

    Returns
    -------
    bool
        True when the candidate is supported by outline, section-path, nearby, or
        preceding grouping evidence that should not outrank hard local evidence.
    """

    evidence_reasons = set(candidate.evidence_reasons or [])
    return (
        ACTIVE_OUTLINE_STACK_PARENT_REASON in evidence_reasons
        and MATCHED_SECTION_PATH_LABEL_REASON in evidence_reasons
    ) or (
        NEAREST_PRECEDING_GROUPING_REASON in evidence_reasons
        and NEARBY_SOURCE_CONTEXT_KEY_REASON in evidence_reasons
        and STATEMENT_TYPE_COMPATIBLE_REASON in evidence_reasons
    )


def _child_has_viable_source_visible_parent(
    *, child_id: str, resolution_request: SFIHasChildResolutionRequest
) -> bool:
    """Check whether an unresolved child has a visible direct parent candidate.

    The relationship resolver adds `source_visible_direct_parent` only to non-root
    candidates that already satisfy the configured direct parent statement-type policy
    and have strong source-visible hierarchy evidence. If such a candidate exists, an
    unresolved response is usually over-trusting inferred code hierarchy or root
    fallback, so the response should be rejected and retried by the LLM agent.

    Parameters
    ----------
    child_id
        Final SFI UUID string for the child being checked.
    resolution_request
        Bounded hasChild parent-selection request supplied to the LLM.

    Returns
    -------
    bool
        True when the child has at least one non-root source-visible direct parent
        candidate in its bounded candidate set.
    """

    for parent_set in resolution_request.child_parent_sets:
        if str(parent_set.child_context.final_sfi_uuid) != child_id:
            continue

        return any(
            _candidate_direct_parent_evidence_tier(
                candidate=candidate, child_context=parent_set.child_context
            )
            <= 1
            for candidate in parent_set.parent_candidates
        )

    return False


def _extract_leading_code_for_parent_match(value: Any) -> str:
    """Extract a leading dot-delimited statement code from visible text.

    Parameters
    ----------
    value
        Text that may begin with a source-visible statement code.

    Returns
    -------
    str
        Normalized leading code when present, otherwise an empty string.
    """

    if value is None:
        return ""

    match = re.match(r"^\s*([A-Za-z]+\d+(?:\s*\.\s*\d+)+)\.?", str(value))

    if match is None:
        return ""

    return _normalize_code_for_parent_match(match.group(1))


def _get_window_local_code(window: ExtractionWindow) -> Optional[str]:
    """Return the stripped local code exposed by an extraction window.

    Parameters
    ----------
    window
        Source extraction window.

    Returns
    -------
    Optional[str]
        Block or table local code, or ``None`` when unavailable.
    """

    if window.block is not None:
        local_code = window.block.get("local_code")
    elif window.table is not None:
        local_code = window.table.local_code
    else:
        local_code = None

    if local_code is None:
        return None

    local_code_clean = str(local_code).strip()
    return local_code_clean or None


def _normalize_code_for_parent_match(value: Any) -> str:
    """Normalize a source or finalization code for parent-prefix comparison.

    Parameters
    ----------
    value
        Raw or normalized statement code value.

    Returns
    -------
    str
        Lowercase code with whitespace removed and leading/trailing dot delimiters
        stripped. Empty input returns an empty string.
    """

    if value is None:
        return ""

    return re.sub(r"\s+", "", str(value).casefold()).strip(".")


def _normalize_statement_type_key(value: str) -> str:
    """Build a punctuation-insensitive statement-type comparison key.

    Parameters
    ----------
    value
        Statement-type label or alias.

    Returns
    -------
    str
        Casefolded alphanumeric key with runs of punctuation collapsed to spaces.
    """

    return re.sub(r"[^0-9a-z]+", " ", str(value or "").casefold()).strip()


def _normalize_text(value: str) -> str:
    """Normalize visible source text for conservative containment checks.

    Parameters
    ----------
    value
        Raw source text.

    Returns
    -------
    str
        Casefolded text with whitespace collapsed.
    """

    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _validate_candidate_code(
    *,
    candidate: SFICandidate,
    code_patterns_by_type: dict[str, str],
    statement_type_code_type_by_label: dict[str, Optional[str]],
    window: ExtractionWindow,
) -> None:
    """Validate that an optional candidate code is configured and source-exposed.

    This check deliberately does not decide whether the code is semantically attached
    to the correct statement. The validation LLM handles that judgment. Python only
    verifies that the exact code is allowed by configuration and appears in the
    window's deterministic code evidence or local-code metadata.

    Parameters
    ----------
    candidate
        Candidate containing an optional statement code.
    code_patterns_by_type
        Configured code regexes keyed by code type.
    statement_type_code_type_by_label
        Configured code type for each canonical statement type.
    window
        Source extraction window.

    Raises
    ------
    QualityError
        If the code is not allowed by configuration or is not exposed by the window.
    """

    if candidate.statement_code is None:
        return

    matching_code_types = sorted(
        code_type
        for code_type, pattern in code_patterns_by_type.items()
        if re.fullmatch(pattern, candidate.statement_code) is not None
    )
    configured_code_type = statement_type_code_type_by_label.get(
        candidate.statement_type
    )

    if configured_code_type is not None:
        if configured_code_type not in matching_code_types:
            raise QualityError(
                f"Candidate {candidate.candidate_id!r} has statement_code "
                f"{candidate.statement_code!r}, which does not match configured "
                f"code type {configured_code_type!r} for statement_type "
                f"{candidate.statement_type!r}."
            )

        resolved_code_type = configured_code_type
    else:
        if len(matching_code_types) != 1:
            raise QualityError(
                f"Candidate {candidate.candidate_id!r} has statement_code "
                f"{candidate.statement_code!r}, which must match exactly one "
                f"configured code pattern when its statement type has no code_type; "
                f"matched {matching_code_types}."
            )

        resolved_code_type = matching_code_types[0]

    local_code = _get_window_local_code(window)
    matches_local_code = (
        local_code is not None and candidate.statement_code == local_code
    )
    matches_code_evidence = any(
        code_match.code_type == resolved_code_type
        and code_match.value == candidate.statement_code
        for code_match in window.code_matches
    )

    if not matches_local_code and not matches_code_evidence:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has statement_code "
            f"{candidate.statement_code!r}, but that exact code is not exposed by "
            f"the extraction window as local_code or typed code evidence."
        )


def _validate_candidate_ids(result: SFIExtractionResult) -> None:
    """Validate sequential source-window candidate identifiers.

    Parameters
    ----------
    result
        Structured extraction result.

    Raises
    ------
    QualityError
        If candidate IDs do not exactly match their 1-based list positions.
    """

    expected_ids = [
        f"sfi_{candidate_number}"
        for candidate_number in range(1, len(result.sfi_candidates) + 1)
    ]
    actual_ids = [candidate.candidate_id for candidate in result.sfi_candidates]

    if actual_ids != expected_ids:
        raise QualityError(
            f"SFI candidate IDs must match their 1-based list positions exactly. "
            f"Expected {expected_ids!r}; got {actual_ids!r}."
        )


def _validate_candidate_source_exists(
    *, candidate: SFICandidate, window: ExtractionWindow
) -> None:
    """Validate that candidate evidence text exists in source-visible window text.

    This is a conservative anti-hallucination check, not a description-completeness,
    semantic, or citation-minimality check. The validation LLM decides whether the
    candidate description and selected source locations are appropriate.

    Parameters
    ----------
    candidate
        Candidate whose source wording should exist in the window.
    window
        Source extraction window.

    Raises
    ------
    QualityError
        If the candidate evidence quote is absent from source-visible window text.
    """

    window_text = _normalize_text(window.source_text)
    source_text_normalized = _normalize_text(candidate.source_text)

    if not source_text_normalized or source_text_normalized not in window_text:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} source_text is not present in "
            "the source-visible extraction-window text."
        )


def _validate_candidate_statement_type(
    *,
    candidate: SFICandidate,
    statement_type_alias_to_canonical: dict[str, str],
    statement_type_normalized_by_label: dict[str, str],
) -> None:
    """Validate canonical statement type and normalized type agreement.

    Parameters
    ----------
    candidate
        Candidate to validate.
    statement_type_alias_to_canonical
        Normalized canonical labels and aliases mapped to canonical labels.
    statement_type_normalized_by_label
        Canonical labels mapped to their configured normalized statement type.

    Raises
    ------
    QualityError
        If the statement type is unsupported, non-canonical, or normalized incorrectly.
    """

    statement_type_key = _normalize_statement_type_key(candidate.statement_type)
    canonical_statement_type = statement_type_alias_to_canonical.get(statement_type_key)

    if canonical_statement_type is None:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has unsupported statement_type "
            f"{candidate.statement_type!r}."
        )

    if candidate.statement_type != canonical_statement_type:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} must use canonical statement_type "
            f"{canonical_statement_type!r}, not {candidate.statement_type!r}."
        )

    expected_normalized_type = statement_type_normalized_by_label[
        canonical_statement_type
    ]

    if candidate.normalized_statement_type != expected_normalized_type:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} with statement_type "
            f"{canonical_statement_type!r} must use normalized_statement_type "
            f"{expected_normalized_type!r}; got "
            f"{candidate.normalized_statement_type!r}."
        )


def _validate_candidate_table_indexes(
    *, candidate: SFICandidate, window: ExtractionWindow
) -> None:
    """Validate candidate table references against the extraction window.

    Parameters
    ----------
    candidate
        Candidate containing table header/body references.
    window
        Source extraction window.

    Raises
    ------
    QualityError
        If block candidates cite table rows, table candidates omit all references, or
        any cited index is outside the supplied table window.
    """

    table = window.table

    if table is None:
        if candidate.table_header_indexes or candidate.table_row_indexes:
            raise QualityError(
                f"Block-window candidate {candidate.candidate_id!r} must not cite "
                "table header or body indexes."
            )

        return

    if not candidate.table_header_indexes and not candidate.table_row_indexes:
        raise QualityError(
            f"Table-window candidate {candidate.candidate_id!r} must cite at least "
            "one supplied header or body row index."
        )

    allowed_header_indexes = set(range(len(table.header_rows)))
    allowed_row_indexes = set(table.row_indexes)
    invalid_header_indexes = sorted(
        set(candidate.table_header_indexes) - allowed_header_indexes
    )
    invalid_row_indexes = sorted(set(candidate.table_row_indexes) - allowed_row_indexes)

    if invalid_header_indexes:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} cites unavailable table header "
            f"indexes: {invalid_header_indexes}."
        )

    if invalid_row_indexes:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} cites unavailable table body row "
            f"indexes: {invalid_row_indexes}."
        )


def _validate_dedup_merge_group_code_guardrails(
    *, review_request: SFIDedupReviewRequest, review_response: SFIDedupReviewResponse
) -> None:
    """Validate hard code-related guardrails for dedup merge groups.

    Parameters
    ----------
    review_request
        Bounded review request supplied to the LLM.
    review_response
        Structured LLM dedup response to validate.

    Raises
    ------
    QualityError
        If a merge group combines incompatible statement types or official codes.
    """

    candidates_by_id = {
        candidate.registry_candidate_id: candidate
        for candidate in review_request.candidates
    }

    for decision_group in review_response.decision_groups:
        if decision_group.decision != "merge" or len(decision_group.candidate_ids) < 2:
            continue

        group_candidates = [
            candidates_by_id[candidate_id]
            for candidate_id in decision_group.candidate_ids
        ]
        normalized_codes = {
            candidate.normalized_statement_code
            for candidate in group_candidates
            if candidate.normalized_statement_code is not None
        }
        statement_types = {candidate.statement_type for candidate in group_candidates}

        if len(statement_types) > 1:
            raise QualityError(
                f"Dedup merge groups must not merge different statement_type values: "
                f"{sorted(statement_types)}. Use conflict or needs_review instead."
            )

        if len(normalized_codes) > 1:
            raise QualityError(
                f"Dedup merge groups must not merge different official normalized "
                f"statement codes: {sorted(normalized_codes)}. Use keep_separate, "
                f"conflict, or needs_review instead."
            )


def _validate_dedup_response_candidate_coverage(
    *, review_request: SFIDedupReviewRequest, review_response: SFIDedupReviewResponse
) -> None:
    """Validate exact candidate coverage for one dedup response.

    Parameters
    ----------
    review_request
        Bounded review request supplied to the LLM.
    review_response
        Structured LLM dedup response to validate.

    Raises
    ------
    QualityError
        If candidates are invented, omitted, or assigned multiple times.
    """

    expected_candidate_ids = {
        candidate.registry_candidate_id for candidate in review_request.candidates
    }
    assigned_candidate_ids: list[str] = []

    for decision_group in review_response.decision_groups:
        assigned_candidate_ids.extend(decision_group.candidate_ids)

    assigned_candidate_id_set = set(assigned_candidate_ids)
    duplicate_candidate_ids = sorted(
        {
            candidate_id
            for candidate_id in assigned_candidate_ids
            if assigned_candidate_ids.count(candidate_id) > 1
        }
    )
    invented_candidate_ids = sorted(assigned_candidate_id_set - expected_candidate_ids)
    omitted_candidate_ids = sorted(expected_candidate_ids - assigned_candidate_id_set)

    if invented_candidate_ids:
        raise QualityError(
            f"Dedup response invented candidate IDs outside the review set: "
            f"{invented_candidate_ids}."
        )

    if omitted_candidate_ids:
        raise QualityError(
            f"Dedup response omitted review candidate IDs: {omitted_candidate_ids}."
        )

    if duplicate_candidate_ids:
        raise QualityError(
            f"Dedup response assigned candidate IDs to more than one group: "
            f"{duplicate_candidate_ids}."
        )


def _validate_dedup_response_reasons(review_response: SFIDedupReviewResponse) -> None:
    """Validate non-empty reasons for dedup decision groups.

    Parameters
    ----------
    review_response
        Structured LLM dedup response to validate.

    Raises
    ------
    QualityError
        If a decision group has an empty reason.
    """

    for group_index, decision_group in enumerate(
        review_response.decision_groups, start=1
    ):
        if not decision_group.reason.strip():
            raise QualityError(
                f"Dedup decision group {group_index} has an empty reason."
            )

        if (
            len(decision_group.candidate_ids) > 1
            and len(decision_group.reason.strip()) < 8
        ):
            raise QualityError(
                f"Dedup decision group {group_index} has a non-singleton decision "
                f"with an insufficiently specific reason."
            )


def _validate_has_child_parent_selection_policy(
    *,
    resolution_request: SFIHasChildResolutionRequest,
    resolution_response: SFIHasChildResolutionResponse,
) -> None:
    """Validate root, resolved, and unresolved hasChild parent-selection policy.

    Parameters
    ----------
    resolution_request
        Bounded hasChild parent-selection request supplied to the LLM.
    resolution_response
        Parsed LLM hasChild parent-selection response.

    Raises
    ------
    QualityError
        If an unresolved child selects parents, if a resolved child selects no parents,
        or if a resolved child selects both the StandardsFramework root and one or
        more SFI parents.
    """

    child_context_by_child_id = {
        str(parent_set.child_context.final_sfi_uuid): parent_set.child_context
        for parent_set in resolution_request.child_parent_sets
    }
    parent_candidates_by_child_id = {
        str(parent_set.child_context.final_sfi_uuid): {
            candidate.endpoint_id: candidate
            for candidate in parent_set.parent_candidates
        }
        for parent_set in resolution_request.child_parent_sets
    }
    root_parent_ids_by_child_id = {
        child_id: {
            candidate.endpoint_id
            for candidate in parent_candidates_by_id.values()
            if candidate.is_root
        }
        for child_id, parent_candidates_by_id in parent_candidates_by_child_id.items()
    }

    for child_resolution in resolution_response.child_resolutions:
        child_id = str(child_resolution.child_final_sfi_uuid)
        root_parent_ids = root_parent_ids_by_child_id.get(child_id, set())
        selected_parent_ids = set(child_resolution.selected_parent_endpoint_ids)
        selected_non_root_parent_ids = selected_parent_ids - root_parent_ids
        selected_root_parent_ids = selected_parent_ids & root_parent_ids

        if child_resolution.unresolved:
            if selected_parent_ids:
                raise QualityError(
                    f"hasChild response for unresolved child {child_id!r} must not "
                    f"select parent endpoints; got "
                    f"{sorted(selected_parent_ids)}."
                )

            if _child_has_viable_source_visible_parent(
                child_id=child_id, resolution_request=resolution_request
            ):
                raise QualityError(
                    f"hasChild response for child {child_id!r} marked unresolved, "
                    f"but the bounded candidate set includes a non-root candidate "
                    f"with source_visible_direct_parent evidence. Select the "
                    f"source-visible direct parent unless the candidate is not truly "
                    f"a direct parent, and explain any source/code conflict."
                )

            continue

        if not selected_parent_ids:
            raise QualityError(
                f"hasChild response for resolved child {child_id!r} must select at "
                f"least one parent endpoint. Set unresolved=true when no supplied "
                f"parent candidate is source-supported."
            )

        if selected_root_parent_ids and selected_non_root_parent_ids:
            raise QualityError(
                f"hasChild response for child {child_id!r} selected both the "
                f"StandardsFramework root {sorted(selected_root_parent_ids)} and "
                f"one or more SFI parents {sorted(selected_non_root_parent_ids)}. "
                f"Use the root only as the sole direct parent for top-level items, "
                f"or set unresolved=true with no selected parents for fallback."
            )

        _validate_resolved_child_prefers_source_visible_parent(
            child_context=child_context_by_child_id.get(child_id),
            child_id=child_id,
            parent_candidates_by_id=parent_candidates_by_child_id.get(child_id, {}),
            selected_parent_ids=selected_parent_ids,
        )
        _validate_resolved_child_uses_strongest_local_parent(
            child_context=child_context_by_child_id.get(child_id),
            child_id=child_id,
            parent_candidates_by_id=parent_candidates_by_child_id.get(child_id, {}),
            selected_parent_ids=selected_parent_ids,
        )


def _validate_resolved_child_prefers_source_visible_parent(
    *,
    child_context: Any,
    child_id: str,
    parent_candidates_by_id: dict[str, SFIHasChildParentCandidate],
    selected_parent_ids: set[str],
) -> None:
    """Validate that resolved children do not choose weak parents over visible parents.

    Source-visible direct-parent evidence is a strong signal against root fallback and
    weak semantic/topic fallback. It is not an absolute veto over a selected non-root
    parent that has stronger direct-parent evidence, such as a code-parent hint, an
    exact hierarchical code-prefix match, same-row table evidence, or active local
    outline plus matching table/source-context evidence.

    Parameters
    ----------
    child_context
        Final child SFI context from the bounded hasChild request.
    child_id
        Final SFI UUID string for the child being validated.
    parent_candidates_by_id
        Parent candidates for the child keyed by selectable endpoint ID.
    selected_parent_ids
        Endpoint IDs selected by the hasChild response for the child.

    Raises
    ------
    QualityError
        If the response selects only root, nearby, same-topic, or semantic parents
        while a source-visible direct-parent candidate is available.
    """

    source_visible_parent_ids = {
        endpoint_id
        for endpoint_id, candidate in parent_candidates_by_id.items()
        if (
            not candidate.is_root
            and SOURCE_VISIBLE_DIRECT_PARENT_REASON in candidate.evidence_reasons
        )
    }

    if not source_visible_parent_ids:
        return

    selected_non_root_parent_ids = {
        endpoint_id
        for endpoint_id in selected_parent_ids
        if endpoint_id in parent_candidates_by_id
        and not parent_candidates_by_id[endpoint_id].is_root
    }

    if (
        selected_non_root_parent_ids
        and selected_non_root_parent_ids <= source_visible_parent_ids
    ):
        return

    selected_weak_parent_ids = []

    for endpoint_id in selected_parent_ids:
        candidate = parent_candidates_by_id.get(endpoint_id)

        if candidate is None:
            continue

        if endpoint_id in source_visible_parent_ids:
            continue

        if (
            _candidate_direct_parent_evidence_tier(
                candidate=candidate, child_context=child_context
            )
            <= 1
        ):
            continue

        selected_weak_parent_ids.append(endpoint_id)

    if not selected_weak_parent_ids:
        return

    raise QualityError(
        f"hasChild response for child {child_id!r} selected weak parent "
        f"endpoint IDs {sorted(selected_weak_parent_ids)}, but the bounded "
        f"candidate set includes source-visible direct parent endpoint IDs "
        f"{sorted(source_visible_parent_ids)}. Select the source-visible direct "
        f"parent, or select a non-root candidate with strong direct-parent "
        f"evidence such as a code-parent hint, exact code-prefix match, "
        f"same-row table evidence, or active local outline plus matching "
        f"table/source-context evidence. Do not choose a root, nearby, "
        f"same-topic, or semantic parent over a source-visible direct parent."
    )


def _validate_resolved_child_uses_strongest_local_parent(
    *,
    child_context: Any,
    child_id: str,
    parent_candidates_by_id: dict[str, SFIHasChildParentCandidate],
    selected_parent_ids: set[str],
) -> None:
    """Validate that selected parents do not lose to stronger same-type local parents.

    This dominance guard catches semantically wrong but structurally valid hasChild
    selections: for example, selecting a nearby previous grouping when another
    candidate of the same allowed parent type has same-table, source-scope, canonical,
    or code-local evidence. The rule remains curriculum-agnostic because it compares
    only candidate evidence tiers and configured statement types.

    Parameters
    ----------
    child_context
        Final child SFI context from the bounded hasChild request.
    child_id
        Final SFI UUID string for the child being validated.
    parent_candidates_by_id
        Parent candidates for the child keyed by selectable endpoint ID.
    selected_parent_ids
        Endpoint IDs selected by the hasChild response for the child.

    Raises
    ------
    QualityError
        If a selected root or soft parent is dominated by a stronger non-root parent
        candidate of the same direct parent statement type.
    """

    non_root_candidates = {
        endpoint_id: candidate
        for endpoint_id, candidate in parent_candidates_by_id.items()
        if not candidate.is_root
    }

    if not non_root_candidates:
        return

    selected_candidates = [
        parent_candidates_by_id[endpoint_id]
        for endpoint_id in selected_parent_ids
        if endpoint_id in parent_candidates_by_id
    ]
    selected_non_root_candidates = [
        candidate for candidate in selected_candidates if not candidate.is_root
    ]

    if not selected_non_root_candidates:
        strongest_candidates = {
            endpoint_id: candidate
            for endpoint_id, candidate in non_root_candidates.items()
            if _candidate_direct_parent_evidence_tier(
                candidate=candidate, child_context=child_context
            )
            <= 1
        }

        if not strongest_candidates:
            return

        raise QualityError(
            f"hasChild response for child {child_id!r} selected the root or no "
            f"non-root parent while stronger local non-root parent candidates "
            f"exist: {sorted(strongest_candidates)}."
        )

    selected_best_tier_by_statement_type: dict[str | None, int] = {}

    for candidate in selected_non_root_candidates:
        candidate_tier = _candidate_direct_parent_evidence_tier(
            candidate=candidate, child_context=child_context
        )
        existing_tier = selected_best_tier_by_statement_type.get(
            candidate.statement_type
        )

        if existing_tier is None or candidate_tier < existing_tier:
            selected_best_tier_by_statement_type[candidate.statement_type] = (
                candidate_tier
            )

    dominated_parent_ids: list[str] = []
    stronger_parent_ids: list[str] = []

    for endpoint_id, candidate in non_root_candidates.items():
        candidate_tier = _candidate_direct_parent_evidence_tier(
            candidate=candidate, child_context=child_context
        )
        selected_tier = selected_best_tier_by_statement_type.get(
            candidate.statement_type
        )

        if selected_tier is None or candidate_tier >= selected_tier:
            continue

        if candidate_tier > 1:
            continue

        stronger_parent_ids.append(endpoint_id)
        dominated_parent_ids.extend(
            selected_candidate.endpoint_id
            for selected_candidate in selected_non_root_candidates
            if selected_candidate.statement_type == candidate.statement_type
            and _candidate_direct_parent_evidence_tier(
                candidate=selected_candidate, child_context=child_context
            )
            > candidate_tier
        )

    if not stronger_parent_ids:
        return

    raise QualityError(
        f"hasChild response for child {child_id!r} selected weaker parent "
        f"endpoint IDs {sorted(set(dominated_parent_ids))}, but the bounded "
        f"candidate set contains stronger local direct-parent candidates of the "
        f"same statement type: {sorted(set(stronger_parent_ids))}. Select the "
        f"hard-local parent, or explain a source conflict by choosing a parent with "
        f"equal or stronger local evidence."
    )


def _validate_result_identity(
    *, result: SFIExtractionResult, window: ExtractionWindow
) -> None:
    """Validate exact extraction-window identity copying.

    Parameters
    ----------
    result
        Structured extraction result.
    window
        Source extraction window.

    Raises
    ------
    QualityError
        If any window identity field differs from the source window.
    """

    if result.window_id != window.window_id:
        raise QualityError(
            f"Result window_id {result.window_id!r} does not match input window_id "
            f"{window.window_id!r}."
        )

    if result.window_index != window.window_index:
        raise QualityError(
            f"Result window_index {result.window_index!r} does not match input "
            f"window_index {window.window_index!r}."
        )

    if result.window_source_segment_ids != window.source_segment_ids:
        raise QualityError(
            f"Result window_source_segment_ids must exactly match input "
            f"source_segment_ids: {window.source_segment_ids!r}."
        )


def verify_sfi_dedup_review_quality(
    *, review_request: SFIDedupReviewRequest, review_response: SFIDedupReviewResponse
) -> None:
    """Run quality checks on one structured SFI dedup review response.

    Parameters
    ----------
    review_request
        Bounded review request supplied to the LLM.
    review_response
        Parsed LLM dedup review response.

    Raises
    ------
    QualityError
        If the response fails coverage or hard-guardrail checks.
    """

    if review_response.review_set_id != review_request.review_set_id:
        raise QualityError(
            f"Dedup response review_set_id {review_response.review_set_id!r} does "
            f"not match request review_set_id {review_request.review_set_id!r}."
        )

    _validate_dedup_response_candidate_coverage(
        review_request=review_request, review_response=review_response
    )
    _validate_dedup_response_reasons(review_response)
    _validate_dedup_merge_group_code_guardrails(
        review_request=review_request, review_response=review_response
    )


def verify_sfi_extraction_integrity(
    *,
    extraction_result: SFIExtractionResult,
    kg_config: CreateKGConfig,
    window: ExtractionWindow,
) -> None:
    """Validate universal integrity constraints for an SFI extraction result.

    This function intentionally avoids curriculum semantics, punctuation heuristics,
    statement-completeness judgments, citation minimality, and hierarchy inference.
    Those checks belong to the second-stage validation LLM.

    Parameters
    ----------
    extraction_result
        Draft or final structured extraction result.
    kg_config
        Runtime KG configuration.
    window
        Source extraction window.

    Raises
    ------
    QualityError
        If the result violates a stable identity, policy, source-existence, or
        reference-integrity constraint.
    """

    statement_type_alias_to_canonical = _build_statement_type_alias_map(kg_config)
    statement_type_code_type_by_label = {
        item.statement_type: item.code_type
        for item in kg_config.academic_standards.statement_type_policy
    }
    statement_type_normalized_by_label = {
        item.statement_type: item.normalized_statement_type
        for item in kg_config.academic_standards.statement_type_policy
    }

    _validate_result_identity(result=extraction_result, window=window)
    _validate_candidate_ids(extraction_result)

    for candidate in extraction_result.sfi_candidates:
        _validate_candidate_statement_type(
            candidate=candidate,
            statement_type_alias_to_canonical=statement_type_alias_to_canonical,
            statement_type_normalized_by_label=statement_type_normalized_by_label,
        )
        _validate_candidate_table_indexes(candidate=candidate, window=window)
        _validate_candidate_source_exists(candidate=candidate, window=window)
        _validate_candidate_code(
            candidate=candidate,
            code_patterns_by_type=dict(kg_config.academic_standards.code_patterns),
            statement_type_code_type_by_label=statement_type_code_type_by_label,
            window=window,
        )

    window_text = _normalize_text(window.source_text)

    for auxiliary_candidate in extraction_result.auxiliary_candidates:
        source_text_normalized = _normalize_text(auxiliary_candidate.source_text)

        if not source_text_normalized or source_text_normalized not in window_text:
            raise QualityError(
                f"Auxiliary candidate {auxiliary_candidate.auxiliary_id!r} "
                f"source_text is not present in the source-visible extraction-window "
                f"text."
            )


def verify_sfi_extraction_validation_integrity(
    *,
    draft_result: SFIExtractionResult,
    kg_config: CreateKGConfig,
    validation_verdict: SFIExtractionValidationVerdict,
    window: ExtractionWindow,
) -> None:
    """Validate the structured output of the SFI validation LLM.

    Parameters
    ----------
    draft_result
        First-stage extraction result reviewed by the validation LLM.
    kg_config
        Runtime KG configuration.
    validation_verdict
        Structured validation verdict containing an optional corrected result.
    window
        Source extraction window.

    Raises
    ------
    QualityError
        If the verdict contradicts its schema contract or its corrected result violates
        universal extraction integrity constraints.
    """

    if validation_verdict.passed:
        verify_sfi_extraction_integrity(
            extraction_result=draft_result, kg_config=kg_config, window=window
        )
        return

    corrected_result = validation_verdict.corrected_result

    if corrected_result is None:
        raise QualityError(
            "A failed SFI extraction validation verdict must include corrected_result."
        )

    verify_sfi_extraction_integrity(
        extraction_result=corrected_result, kg_config=kg_config, window=window
    )


def verify_sfi_has_child_resolution_quality(
    *,
    resolution_request: SFIHasChildResolutionRequest,
    resolution_response: SFIHasChildResolutionResponse,
) -> None:
    """Run quality checks on one structured hasChild resolution response.

    Parameters
    ----------
    resolution_request
        Bounded hasChild parent-selection request supplied to the LLM.
    resolution_response
        Parsed LLM hasChild parent-selection response.

    Raises
    ------
    QualityError
        If the response fails coverage, endpoint, root-selection, resolved-state, or
        self-loop checks.
    """

    if resolution_response.request_id != resolution_request.request_id:
        raise QualityError(
            f"hasChild response request_id {resolution_response.request_id!r} does "
            f"not match request_id {resolution_request.request_id!r}."
        )

    expected_child_ids = {
        str(parent_set.child_context.final_sfi_uuid)
        for parent_set in resolution_request.child_parent_sets
    }
    allowed_parent_ids_by_child_id = {
        str(parent_set.child_context.final_sfi_uuid): {
            candidate.endpoint_id for candidate in parent_set.parent_candidates
        }
        for parent_set in resolution_request.child_parent_sets
    }
    assigned_child_ids = [
        str(child_resolution.child_final_sfi_uuid)
        for child_resolution in resolution_response.child_resolutions
    ]
    assigned_child_id_set = set(assigned_child_ids)
    duplicate_child_ids = sorted(
        {
            child_id
            for child_id in assigned_child_ids
            if assigned_child_ids.count(child_id) > 1
        }
    )
    invented_child_ids = sorted(assigned_child_id_set - expected_child_ids)
    omitted_child_ids = sorted(expected_child_ids - assigned_child_id_set)

    if invented_child_ids:
        raise QualityError(
            f"hasChild response includes child IDs outside the request: "
            f"{invented_child_ids}."
        )

    if omitted_child_ids:
        raise QualityError(
            f"hasChild response omitted requested child IDs: {omitted_child_ids}."
        )

    if duplicate_child_ids:
        raise QualityError(
            f"hasChild response assigned child IDs more than once: "
            f"{duplicate_child_ids}."
        )

    _validate_has_child_parent_selection_policy(
        resolution_request=resolution_request, resolution_response=resolution_response
    )

    for child_resolution in resolution_response.child_resolutions:
        child_id = str(child_resolution.child_final_sfi_uuid)
        allowed_parent_ids = allowed_parent_ids_by_child_id[child_id]
        selected_parent_ids = child_resolution.selected_parent_endpoint_ids
        invented_parent_ids = sorted(set(selected_parent_ids) - allowed_parent_ids)

        if invented_parent_ids:
            raise QualityError(
                f"hasChild response for child {child_id!r} selected parent endpoint "
                f"IDs outside the bounded candidate set: {invented_parent_ids}."
            )

        if child_id in selected_parent_ids:
            raise QualityError(
                f"hasChild response for child {child_id!r} contains a self-loop."
            )

        if not child_resolution.reason.strip():
            raise QualityError(
                f"hasChild response for child {child_id!r} has an empty reason."
            )
