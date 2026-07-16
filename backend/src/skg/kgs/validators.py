"""This module contains functionalities related to validating LLM-produced knowledge
graph artifacts.

NB: The Pydantic schemas validate structure and field-level invariants. The validators
in this module enforce quality checks that require access to other inputs.
"""

# Standard Library
import re

from typing import Any, Optional, Sequence

# Third Party Library
from loguru import logger

# Package Library
from skg.kgs.schemas import (
    ExtractionWindow,
    SFICandidate,
    SFIDedupReviewRequest,
    SFIDedupReviewResponse,
    SFIDedupValidationVerdict,
    SFIExtractionResult,
    SFIExtractionValidationVerdict,
    SFIHasChildParentCandidate,
    SFIHasChildResolutionRequest,
    SFIHasChildResolutionResponse,
    SFISourceAnchor,
)
from skg.kgs.sfi_source_anchors import (
    SFISourceUnit,
    build_sfi_source_unit_map,
    find_source_anchor_span,
    source_anchor_set_signature,
)
from skg.kgs.utils import resolve_candidate_code
from skg.page_ir_extraction.validators import QualityError
from skg.schemas import CreateKGConfig, normalize_controlled_value_key

ACTIVE_OUTLINE_STACK_PARENT_REASON = "active_outline_stack_parent"
CODE_PARENT_HINT_REASON = "code_parent_hint"
LOCAL_ACTIVE_OUTLINE_DIRECT_PARENT_REASON = "local_active_outline_direct_parent"
MATCHED_SECTION_PATH_LABEL_REASON = "matched_section_path_label"
NEARBY_SOURCE_CONTEXT_KEY_REASON = "nearby_source_context_key"
NEAREST_PRECEDING_GROUPING_REASON = "nearest_preceding_grouping"
ROOT_EVIDENCE_REASON = "root_fallback"
SAME_SOURCE_CONTEXT_KEY_REASON = "same_source_context_key"
SAME_SOURCE_OCCURRENCE_CROSS_TYPE_SIGNAL = "same_source_occurrence_cross_type"
SAME_SOURCE_SEGMENT_REASON = "same_source_segment"
SAME_SOURCE_WINDOW_REASON = "same_source_window"
SAME_TABLE_CONTEXT_REASON = "same_table_context"
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
DECISIVE_DIRECT_PARENT_REASONS = frozenset(
    {
        CODE_PARENT_HINT_REASON,
        SOURCE_LOCAL_CONTROLLED_PARENT_SCOPE_REASON,
        SOURCE_SCOPE_GROUPING_REASON,
    }
)
STRONG_LOCAL_RANKING_PARENT_REASONS = frozenset(
    {
        LOCAL_ACTIVE_OUTLINE_DIRECT_PARENT_REASON,
        SAME_TABLE_CONTEXT_REASON,
    }
)


def _build_scope_value_alias_maps(
    kg_config: CreateKGConfig,
) -> dict[str, dict[str, str]]:
    """Build configured controlled-value alias maps for semantic scope validation.

    Parameters
    ----------
    kg_config
        Runtime KG configuration.

    Returns
    -------
    dict[str, dict[str, str]]
        Canonical scope statement types mapped to normalized aliases and canonical
        controlled values.
    """

    scope_value_alias_maps: dict[str, dict[str, str]] = {}

    for item in kg_config.academic_standards.statement_type_policy:
        alias_to_canonical: dict[str, str] = {}

        for controlled_value in item.controlled_values:
            for alias in [
                controlled_value.canonical_value,
                *controlled_value.aliases,
            ]:
                alias_key = normalize_controlled_value_key(alias)

                if alias_key:
                    alias_to_canonical[alias_key] = controlled_value.canonical_value

        if alias_to_canonical:
            scope_value_alias_maps[item.statement_type] = alias_to_canonical

    return scope_value_alias_maps


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
    candidate: SFIHasChildParentCandidate,
) -> int:
    """Assign a dominance tier to one parent candidate.

    Lower tiers are stronger. Only corroborated direct-parent evidence occupies tiers
    zero and one, which are eligible to dominate unresolved or weaker resolved choices.
    Same-table and local-outline evidence remain strong retrieval/ranking signals but
    cannot independently make a parent selection mandatory.

    Parameters
    ----------
    candidate
        Parent candidate being evaluated.

    Returns
    -------
    int
        Evidence tier, where zero and one are corroborated direct-parent evidence,
        larger values are retrieval/ranking evidence, and root fallback is weakest.
    """

    evidence_reasons = set(candidate.evidence_reasons or [])

    if candidate.is_root or ROOT_EVIDENCE_REASON in evidence_reasons:
        return 90

    if SOURCE_VISIBLE_DIRECT_PARENT_REASON in evidence_reasons:
        return 0

    if _candidate_has_decisive_direct_parent_evidence(candidate):
        return 1

    if evidence_reasons & STRONG_LOCAL_RANKING_PARENT_REASONS:
        return 2

    if ACTIVE_OUTLINE_STACK_PARENT_REASON in evidence_reasons and (
        SAME_SOURCE_CONTEXT_KEY_REASON in evidence_reasons
        or SAME_SOURCE_SEGMENT_REASON in evidence_reasons
        or SAME_SOURCE_WINDOW_REASON in evidence_reasons
    ):
        return 3

    if _candidate_has_soft_carry_forward_evidence(candidate):
        return 4

    if (
        SAME_SOURCE_CONTEXT_KEY_REASON in evidence_reasons
        or SAME_SOURCE_SEGMENT_REASON in evidence_reasons
        or SAME_SOURCE_WINDOW_REASON in evidence_reasons
    ):
        return 5

    if evidence_reasons & CARRY_FORWARD_PARENT_REASONS:
        return 6

    return 7


def _candidate_has_decisive_direct_parent_evidence(
    candidate: SFIHasChildParentCandidate,
) -> bool:
    """Check whether a parent candidate has corroborated direct-parent evidence.

    Decisive evidence is emitted only after curriculum-neutral structural checks have
    established a locally compatible code-parent hint, a typed source-local controlled
    parent match, or a source-scope grouping/header relationship. Generic code-prefix,
    same-row, same-window, and active-outline proximity are intentionally excluded.

    Parameters
    ----------
    candidate
        Parent candidate being evaluated.

    Returns
    -------
    bool
        True when the candidate carries at least one decisive evidence reason.
    """

    if candidate.is_root:
        return False

    return bool(set(candidate.evidence_reasons or []) & DECISIVE_DIRECT_PARENT_REASONS)


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
        preceding grouping evidence that should not outrank corroborated evidence.
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


def _decision_group_shares_exact_description_source_anchors(
    *, candidate_ids: Sequence[str], review_request: SFIDedupReviewRequest
) -> bool:
    """Check whether one mixed-type group shares exact description anchors.

    Parameters
    ----------
    candidate_ids
        Candidate IDs proposed for one dedup decision group.
    review_request
        Bounded review request containing exact candidate source anchors.

    Returns
    -------
    bool
        True when every candidate preserves one identical non-empty exact
        description-anchor set and the group contains multiple statement-type pairs.
    """

    candidates_by_id = {
        candidate.registry_candidate_id: candidate
        for candidate in review_request.candidates
    }
    group_candidates = [
        candidates_by_id[candidate_id]
        for candidate_id in candidate_ids
        if candidate_id in candidates_by_id
    ]

    if len(group_candidates) != len(candidate_ids):
        return False

    type_pairs = {
        (candidate.statement_type, candidate.normalized_statement_type)
        for candidate in group_candidates
    }

    if len(type_pairs) < 2:
        return False

    anchor_signatures = {
        source_anchor_set_signature(candidate.description_source_anchors)
        for candidate in group_candidates
    }
    occurrence_keys = {
        candidate.source_occurrence_location_key for candidate in group_candidates
    }
    return (
        len(anchor_signatures) == 1
        and bool(next(iter(anchor_signatures)))
        and len(occurrence_keys) == 1
    )


def _extract_serialized_row_text(row: dict[str, Any]) -> str:
    """Extract source-visible text from one serialized table row.

    Parameters
    ----------
    row
        Serialized raw table row containing cell payloads.

    Returns
    -------
    str
        Cell text joined in source column order.
    """

    cell_texts: list[str] = []

    for cell in row.get("cells", []):
        if not isinstance(cell, dict):
            continue

        text_payload = cell.get("text")

        if isinstance(text_payload, dict):
            cell_text = str(text_payload.get("text") or "").strip()
        else:
            cell_text = str(text_payload or "").strip()

        if cell_text:
            cell_texts.append(cell_text)

    return "\n".join(cell_texts)


def _find_scope_value_conflicts(
    scope_value_maps: Sequence[dict[str, str]],
) -> dict[str, list[str]]:
    """Find contradictory values assigned to shared semantic scope dimensions.

    Parameters
    ----------
    scope_value_maps
        Source-backed scope mappings to compare.

    Returns
    -------
    dict[str, list[str]]
        Scope labels mapped to distinct conflicting values. An empty mapping means
        every shared scope dimension is compatible.
    """

    values_by_scope_label: dict[str, set[str]] = {}

    for scope_values in scope_value_maps:
        for scope_label, scope_value in scope_values.items():
            values_by_scope_label.setdefault(scope_label, set()).add(scope_value)

    return {
        scope_label: sorted(scope_values)
        for scope_label, scope_values in sorted(values_by_scope_label.items())
        if len(scope_values) > 1
    }


def _get_candidate_cited_source_text(
    *, candidate: SFICandidate, window: ExtractionWindow
) -> str:
    """Collect raw source text from the table locations cited by a candidate.

    Block candidates use the complete block-window source text. Table candidates use
    only the raw header and body rows cited by the candidate.

    Parameters
    ----------
    candidate
        Candidate containing optional table source indexes.
    window
        Source extraction window.

    Returns
    -------
    str
        Source-visible text from the candidate's cited locations.
    """

    table = window.table

    if table is None:
        return window.source_text

    cited_rows: list[dict[str, Any]] = []

    for header_index in candidate.table_header_indexes:
        if 0 <= header_index < len(table.header_rows):
            cited_rows.append(table.header_rows[header_index])

    rows_by_index = dict(zip(table.row_indexes, table.rows))

    for row_index in candidate.table_row_indexes:
        row = rows_by_index.get(row_index)

        if row is not None:
            cited_rows.append(row)

    return "\n".join(_extract_serialized_row_text(row) for row in cited_rows)


def _log_duplicate_sfi_candidate_warnings(
    extraction_result: SFIExtractionResult,
) -> None:
    """Log non-blocking warnings for exact repeated candidates in one result.

    Candidates are compared using canonical statement type, formatting-normalized code,
    and normalized complete description. The warning is intentionally diagnostic: some
    documents repeat legitimate items, while the checker decides whether repeated
    source occurrences should be represented by one candidate with combined provenance.

    Parameters
    ----------
    extraction_result
        Structured extraction result to inspect.
    """

    candidates_by_key: dict[tuple[str, str, str], list[SFICandidate]] = {}

    for candidate in extraction_result.sfi_candidates:
        key = (
            candidate.statement_type,
            _normalize_code_for_parent_match(candidate.statement_code),
            _normalize_text(candidate.description),
        )
        candidates_by_key.setdefault(key, []).append(candidate)

    for candidates in candidates_by_key.values():
        if len(candidates) < 2:
            continue

        candidate_context = [
            {
                "candidate_id": candidate.candidate_id,
                "table_header_indexes": candidate.table_header_indexes,
                "table_row_indexes": candidate.table_row_indexes,
            }
            for candidate in candidates
        ]
        logger.warning(
            f"SFI extraction result contains repeated candidates with the same "
            f"statement_type, normalized statement_code, and normalized description; "
            f"window_id={extraction_result.window_id!r}, "
            f"candidates={candidate_context!r}."
        )


def _normalize_anchor_composition_text(value: str) -> str:
    """Normalize source-anchor composition text without changing case or punctuation.

    Parameters
    ----------
    value
        Source-visible text to normalize.

    Returns
    -------
    str
        Text with whitespace collapsed and leading/trailing whitespace removed.
    """

    return re.sub(r"\s+", " ", str(value or "")).strip()


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


def _resolve_source_anchors(
    *,
    anchors: Sequence[SFISourceAnchor],
    candidate_id: str,
    field_name: str,
    source_unit_map: dict[str, SFISourceUnit],
) -> list[tuple[SFISourceUnit, int, int]]:
    """Resolve and validate one ordered source-anchor list.

    Anchors may be noncontiguous, including multiple excerpts from one source unit.
    Python verifies exact references, source order, and non-overlap but does not
    require intervening source text to be included.

    Parameters
    ----------
    anchors
        Exact source anchors returned by the producer/checker.
    candidate_id
        Window-local candidate identifier used in validation errors.
    field_name
        Human-readable anchor-field name used in validation errors.
    source_unit_map
        Exact source-visible units keyed by stable source-unit ID.

    Returns
    -------
    list[tuple[SFISourceUnit, int, int]]
        Resolved source units and inclusive-start/exclusive-end spans in supplied order.

    Raises
    ------
    QualityError
        If an anchor is unknown, invalid, out of source order, or overlaps another
        anchor in the same source unit.
    """

    resolved_anchors: list[tuple[SFISourceUnit, int, int]] = []

    for anchor in anchors:
        source_unit = source_unit_map.get(anchor.source_unit_id)

        if source_unit is None:
            raise QualityError(
                f"Candidate {candidate_id!r} {field_name} references unknown "
                f"source_unit_id {anchor.source_unit_id!r}."
            )

        try:
            start_char, end_char = find_source_anchor_span(
                anchor=anchor, source_unit=source_unit
            )
        except ValueError as exc:
            raise QualityError(
                f"Candidate {candidate_id!r} has an invalid {field_name} anchor: "
                f"{exc}"
            ) from exc

        resolved_anchors.append((source_unit, start_char, end_char))

    positions = [
        (source_unit.source_order, start_char, end_char)
        for source_unit, start_char, end_char in resolved_anchors
    ]

    if positions != sorted(positions):
        raise QualityError(
            f"Candidate {candidate_id!r} {field_name} must follow source order."
        )

    _validate_anchor_spans_do_not_overlap(
        candidate_id=candidate_id,
        field_name=field_name,
        resolved_anchors=resolved_anchors,
    )
    return resolved_anchors


def _text_equals_anchor_fragments(*, fragments: Sequence[str], value: str) -> bool:
    """Check whether text is composed only from ordered anchor fragments.

    Parameters
    ----------
    fragments
        Exact source fragments in source order.
    value
        Candidate description to compare.

    Returns
    -------
    bool
        Whether the value equals the fragments with only optional whitespace inserted
        at source-unit boundaries.
    """

    normalized_fragments = [
        _normalize_anchor_composition_text(fragment) for fragment in fragments
    ]

    if not normalized_fragments or any(
        not fragment for fragment in normalized_fragments
    ):
        return False

    pattern = r"\s*".join(re.escape(fragment) for fragment in normalized_fragments)
    return re.fullmatch(pattern, _normalize_anchor_composition_text(value)) is not None


def _validate_anchor_spans_do_not_overlap(
    *,
    candidate_id: str,
    field_name: str,
    resolved_anchors: Sequence[tuple[SFISourceUnit, int, int]],
) -> None:
    """Reject overlapping anchor excerpts while permitting visible gaps.

    Parameters
    ----------
    candidate_id
        Window-local candidate identifier used in validation errors.
    field_name
        Human-readable anchor-field name used in validation errors.
    resolved_anchors
        Exact source units and spans in source order.

    Raises
    ------
    QualityError
        If consecutive anchors overlap within the same source unit.
    """

    for previous_anchor, current_anchor in zip(resolved_anchors, resolved_anchors[1:]):
        previous_unit, _previous_start, previous_end = previous_anchor
        current_unit, current_start, _current_end = current_anchor

        if (
            previous_unit.source_unit_id == current_unit.source_unit_id
            and current_start < previous_end
        ):
            raise QualityError(
                f"Candidate {candidate_id!r} has overlapping {field_name} in source "
                f"unit {current_unit.source_unit_id!r}."
            )


def _validate_auxiliary_source_text(
    *, auxiliary_id: str, source_text: str, source_units: Sequence[SFISourceUnit]
) -> None:
    """Validate one auxiliary excerpt against source-visible units.

    Parameters
    ----------
    auxiliary_id
        Window-local auxiliary identifier used in validation errors.
    source_text
        Verbatim auxiliary excerpt returned by the model.
    source_units
        Exact source-visible units available in the extraction window.

    Raises
    ------
    QualityError
        If the auxiliary excerpt is not an exact substring of any source-visible unit.
    """

    if not any(source_text in source_unit.source_text for source_unit in source_units):
        raise QualityError(
            f"Auxiliary candidate {auxiliary_id!r} source_text is not an exact "
            f"source-visible excerpt from the extraction window."
        )


def _validate_candidate_code(
    *,
    candidate: SFICandidate,
    code_patterns_by_type: dict[str, str],
    statement_type_code_type_by_label: dict[str, Optional[str]],
    window: ExtractionWindow,
) -> Optional[str]:
    """Validate an optional candidate code and return its resolved code type.

    The check is limited to configured syntax, statement-type compatibility, and exact
    candidate-window code evidence. It does not decide whether the source code is
    semantically correct for the curriculum statement.

    Parameters
    ----------
    candidate
        Candidate containing an optional statement code.
    code_patterns_by_type
        Configured code regexes keyed by code type.
    statement_type_code_type_by_label
        Configured code type for each canonical statement type.
    window
        Source extraction window containing candidate-local code matches.

    Returns
    -------
    Optional[str]
        Resolved configured code type, or `None` for an uncoded candidate.

    Raises
    ------
    QualityError
        If the code is invalid, incompatible, or absent from typed window evidence.
    """

    if candidate.statement_code is None:
        return None

    try:
        code_resolution = resolve_candidate_code(
            code_patterns=code_patterns_by_type,
            expected_code_type=statement_type_code_type_by_label.get(
                candidate.statement_type
            ),
            statement_code=candidate.statement_code,
        )
    except ValueError as exc:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has invalid statement_code "
            f"{candidate.statement_code!r}: {exc}"
        ) from exc

    resolved_code_type = code_resolution.resolved_code_type

    if resolved_code_type is None:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has statement_code "
            f"{candidate.statement_code!r}, but no configured code type was resolved."
        )

    if not any(
        code_match.code_type == resolved_code_type
        and code_match.normalized_value == candidate.statement_code
        for code_match in window.code_matches
    ):
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has statement_code "
            f"{candidate.statement_code!r} resolved as code_type "
            f"{resolved_code_type!r}, but that exact typed normalized code is not "
            f"exposed by the extraction window."
        )

    return resolved_code_type


def _validate_candidate_code_evidence(
    *,
    candidate: SFICandidate,
    resolved_code_type: Optional[str],
    window: ExtractionWindow,
) -> None:
    """Validate exact code-anchor agreement with the accepted statement code.

    Parameters
    ----------
    candidate
        Candidate whose code anchors are being validated.
    resolved_code_type
        Configured code type resolved for the candidate, or `None` when uncoded.
    window
        Source extraction window supplying typed code matches.

    Raises
    ------
    QualityError
        If code presence and anchors disagree or anchors cite nonmatching raw evidence.
    """

    if candidate.statement_code is None:
        if candidate.code_source_anchors:
            raise QualityError(
                f"Uncoded candidate {candidate.candidate_id!r} must have empty "
                f"code_source_anchors."
            )

        return

    if resolved_code_type is None:
        raise QualityError(
            f"Coded candidate {candidate.candidate_id!r} has no resolved code type."
        )

    if not candidate.code_source_anchors:
        raise QualityError(
            f"Coded candidate {candidate.candidate_id!r} must provide exact "
            f"code_source_anchors."
        )

    matching_raw_values = {
        code_match.raw_value
        for code_match in window.code_matches
        if code_match.code_type == resolved_code_type
        and code_match.normalized_value == candidate.statement_code
    }
    anchored_code_values = {
        anchor.source_text for anchor in candidate.code_source_anchors
    }

    if not anchored_code_values.issubset(matching_raw_values):
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} code_source_anchors must cite only "
            f"exact raw evidence for statement_code={candidate.statement_code!r}; "
            f"allowed={sorted(matching_raw_values)!r}, got="
            f"{sorted(anchored_code_values)!r}."
        )


def _validate_candidate_scope_contracts(
    *,
    candidate: SFICandidate,
    code_scope_statement_types: dict[str, list[str]],
    identity_scope_statement_types: dict[str, list[str]],
    resolved_code_type: Optional[str],
    scope_value_alias_maps: dict[str, dict[str, str]],
) -> None:
    """Validate candidate identity and code scope without inferring semantic values.

    Parameters
    ----------
    candidate
        Checker-approved extraction candidate.
    code_scope_statement_types
        Ordered scope dimensions keyed by configured code type.
    identity_scope_statement_types
        Ordered scope dimensions keyed by candidate statement type.
    resolved_code_type
        Resolved configured code type, or `None` for an uncoded candidate.
    scope_value_alias_maps
        Configured controlled-value aliases by scope statement type.

    Raises
    ------
    QualityError
        If either candidate scope field violates its runtime-configured contract.
    """

    _validate_scope_values_contract(
        candidate_id=candidate.candidate_id,
        provided_scope_values=candidate.identity_scope_values,
        scope_field_name="identity_scope_values",
        scope_statement_types=identity_scope_statement_types.get(
            candidate.statement_type, []
        ),
        scope_value_alias_maps=scope_value_alias_maps,
    )

    expected_code_scope_statement_types = (
        code_scope_statement_types.get(resolved_code_type, [])
        if resolved_code_type is not None
        else []
    )
    _validate_scope_values_contract(
        candidate_id=candidate.candidate_id,
        provided_scope_values=candidate.code_scope_values,
        scope_field_name="code_scope_values",
        scope_statement_types=expected_code_scope_statement_types,
        scope_value_alias_maps=scope_value_alias_maps,
    )


def _validate_candidate_source_anchors(
    *,
    candidate: SFICandidate,
    resolved_code_type: Optional[str],
    source_unit_map: dict[str, SFISourceUnit],
    window: ExtractionWindow,
) -> None:
    """Validate exact anchor, description, source-text, and table-reference integrity.

    Noncontiguous description anchors are permitted. The complete description must
    still be composed from the exact ordered anchor fragments after whitespace
    normalization. Python does not judge whether the fragments semantically belong
    together; that remains the checker LLM's responsibility.

    Parameters
    ----------
    candidate
        Candidate carrying exact description and optional code anchors.
    resolved_code_type
        Configured code type resolved for the candidate, or `None` when uncoded.
    source_unit_map
        Exact source-visible units available in the extraction window.
    window
        Source extraction window containing bounded source locations.

    Raises
    ------
    QualityError
        If anchors, description composition, source_text, code evidence, or table
        references violate universal integrity constraints.
    """

    resolved_description_anchors = _resolve_source_anchors(
        anchors=candidate.description_source_anchors,
        candidate_id=candidate.candidate_id,
        field_name="description_source_anchors",
        source_unit_map=source_unit_map,
    )
    resolved_code_anchors = _resolve_source_anchors(
        anchors=candidate.code_source_anchors,
        candidate_id=candidate.candidate_id,
        field_name="code_source_anchors",
        source_unit_map=source_unit_map,
    )

    if not _text_equals_anchor_fragments(
        fragments=[
            anchor.source_text for anchor in candidate.description_source_anchors
        ],
        value=candidate.description,
    ):
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} description must be composed "
            f"exactly from description_source_anchors after whitespace normalization."
        )

    _validate_candidate_code_evidence(
        candidate=candidate, resolved_code_type=resolved_code_type, window=window
    )
    _validate_candidate_table_indexes(
        candidate=candidate,
        resolved_anchors=[*resolved_description_anchors, *resolved_code_anchors],
        window=window,
    )
    _validate_candidate_source_text(candidate=candidate, window=window)


def _validate_candidate_source_text(
    *, candidate: SFICandidate, window: ExtractionWindow
) -> None:
    """Validate that candidate source text is bounded to its cited source locations.

    The producer/checker selects `source_text`; Python does not reconstruct it. This
    check only verifies that the selected evidence is source-visible within the block
    or the candidate's cited table rows and headers after whitespace normalization.

    Parameters
    ----------
    candidate
        Candidate whose bounded source evidence should be checked.
    window
        Source extraction window containing the cited source locations.

    Raises
    ------
    QualityError
        If source_text is not visible within the candidate's bounded source locations.
    """

    cited_source_text = _get_candidate_cited_source_text(
        candidate=candidate, window=window
    )
    cited_text_normalized = _normalize_anchor_composition_text(cited_source_text)
    source_text_normalized = _normalize_anchor_composition_text(candidate.source_text)

    if (
        not source_text_normalized
        or source_text_normalized not in cited_text_normalized
    ):
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} source_text is not visible within "
            f"its cited block or table source locations."
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
    *,
    candidate: SFICandidate,
    resolved_anchors: Sequence[tuple[SFISourceUnit, int, int]],
    window: ExtractionWindow,
) -> None:
    """Validate candidate table references and anchor-to-row alignment.

    Parameters
    ----------
    candidate
        Candidate containing table header/body references.
    resolved_anchors
        Resolved description and code anchors for the candidate.
    window
        Source extraction window.

    Raises
    ------
    QualityError
        If references are invalid, absent for table candidates, or fail to include all
        source rows and headers cited by exact anchors.
    """

    table = window.table

    if table is None:
        if candidate.table_header_indexes or candidate.table_row_indexes:
            raise QualityError(
                f"Block-window candidate {candidate.candidate_id!r} must not cite "
                f"table header or body indexes."
            )

        return

    if not candidate.table_header_indexes and not candidate.table_row_indexes:
        raise QualityError(
            f"Table-window candidate {candidate.candidate_id!r} must cite at least "
            f"one supplied header or body row index."
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

    anchored_header_indexes = {
        int(source_unit.source_locator["header_row_index"])
        for source_unit, _start_char, _end_char in resolved_anchors
        if source_unit.source_unit_kind == "table_header_cell"
    }
    anchored_row_indexes = {
        int(source_unit.source_locator["row_index"])
        for source_unit, _start_char, _end_char in resolved_anchors
        if source_unit.source_unit_kind == "table_body_cell"
    }
    missing_header_indexes = sorted(
        anchored_header_indexes - set(candidate.table_header_indexes)
    )
    missing_row_indexes = sorted(
        anchored_row_indexes - set(candidate.table_row_indexes)
    )

    if missing_header_indexes:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} table_header_indexes omit exact "
            f"anchor rows: {missing_header_indexes}."
        )

    if missing_row_indexes:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} table_row_indexes omit exact "
            f"anchor rows: {missing_row_indexes}."
        )


def _validate_dedup_canonical_code_selections(
    *, review_request: SFIDedupReviewRequest, review_response: SFIDedupReviewResponse
) -> None:
    """Validate canonical-code source selections for dedup decision groups.

    Python validates only stable cross-object contracts. It does not decide which of
    multiple source-visible codes is semantically authoritative; the producer/checker
    LLM must make that source-grounded choice under the runtime instructions.

    Parameters
    ----------
    review_request
        Bounded review request containing candidate code evidence.
    review_response
        Structured dedup response whose code selections should be checked.

    Raises
    ------
    QualityError
        If canonical-code selection fields are missing, unexpected, out of group,
        uncoded, or inconsistent with the selected request candidate.
    """

    candidates_by_id = {
        candidate.registry_candidate_id: candidate
        for candidate in review_request.candidates
    }

    for group_index, decision_group in enumerate(review_response.decision_groups):
        group_candidates = [
            candidates_by_id[candidate_id]
            for candidate_id in decision_group.candidate_ids
        ]
        distinct_normalized_codes = sorted(
            {
                candidate.normalized_statement_code
                for candidate in group_candidates
                if candidate.normalized_statement_code
            }
        )
        selection_candidate_id = decision_group.canonical_code_source_candidate_id
        selection_reason = decision_group.canonical_code_selection_reason
        requires_selection = (
            decision_group.decision == "merge" and len(distinct_normalized_codes) > 1
        )

        if not requires_selection:
            if selection_candidate_id is not None or selection_reason is not None:
                raise QualityError(
                    f"Dedup decision group {group_index} must leave canonical-code "
                    f"selection fields null unless decision='merge' and multiple "
                    f"distinct normalized source codes are present."
                )

            continue

        if selection_candidate_id is None:
            raise QualityError(
                f"Dedup mixed-code merge group {group_index} must provide "
                f"canonical_code_source_candidate_id."
            )

        if selection_reason is None:
            raise QualityError(
                f"Dedup mixed-code merge group {group_index} must provide "
                f"canonical_code_selection_reason."
            )

        if selection_candidate_id not in decision_group.candidate_ids:
            raise QualityError(
                f"Dedup mixed-code merge group {group_index} selected candidate "
                f"{selection_candidate_id!r}, which is outside that decision group."
            )

        selected_candidate = candidates_by_id.get(selection_candidate_id)

        if selected_candidate is None:
            raise QualityError(
                f"Dedup mixed-code merge group {group_index} selected unknown "
                f"candidate {selection_candidate_id!r}."
            )

        if (
            selected_candidate.statement_code is None
            or selected_candidate.normalized_statement_code is None
            or selected_candidate.resolved_code_type is None
        ):
            raise QualityError(
                f"Dedup mixed-code merge group {group_index} selected uncoded "
                f"or unresolved candidate {selection_candidate_id!r}."
            )

        if (
            selected_candidate.normalized_statement_code
            not in distinct_normalized_codes
        ):
            raise QualityError(
                f"Dedup mixed-code merge group {group_index} selected candidate "
                f"{selection_candidate_id!r} whose normalized code is not among the "
                f"group's preserved source codes."
            )


def _validate_dedup_canonical_type_selections(
    *, review_request: SFIDedupReviewRequest, review_response: SFIDedupReviewResponse
) -> None:
    """Validate canonical statement-type selections for dedup decision groups.

    Python validates only stable selection integrity. The producer/checker LLM decides
    which existing candidate has the correct classification when duplicate extraction
    produced multiple statement-type pairs.

    Parameters
    ----------
    review_request
        Bounded review request containing candidate type evidence.
    review_response
        Structured dedup response whose type selections should be checked.

    Raises
    ------
    QualityError
        If canonical type selection fields are missing, unexpected, out of group, or
        inconsistent with the selected candidate's existing type pair.
    """

    candidates_by_id = {
        candidate.registry_candidate_id: candidate
        for candidate in review_request.candidates
    }

    for group_index, decision_group in enumerate(review_response.decision_groups):
        group_candidates = [
            candidates_by_id[candidate_id]
            for candidate_id in decision_group.candidate_ids
        ]
        distinct_type_pairs = {
            (candidate.statement_type, candidate.normalized_statement_type)
            for candidate in group_candidates
        }
        selection_candidate_id = decision_group.canonical_type_source_candidate_id
        selection_reason = decision_group.canonical_type_selection_reason
        requires_selection = (
            decision_group.decision == "merge" and len(distinct_type_pairs) > 1
        )

        if not requires_selection:
            if selection_candidate_id is not None or selection_reason is not None:
                raise QualityError(
                    f"Dedup decision group {group_index} must leave canonical-type "
                    f"selection fields null unless decision='merge' and multiple "
                    f"statement-type pairs are present."
                )

            continue

        if selection_candidate_id is None:
            raise QualityError(
                f"Dedup mixed-type merge group {group_index} must provide "
                f"canonical_type_source_candidate_id."
            )

        if selection_reason is None:
            raise QualityError(
                f"Dedup mixed-type merge group {group_index} must provide "
                f"canonical_type_selection_reason."
            )

        if selection_candidate_id not in decision_group.candidate_ids:
            raise QualityError(
                f"Dedup mixed-type merge group {group_index} selected candidate "
                f"{selection_candidate_id!r}, which is outside that decision group."
            )

        selected_candidate = candidates_by_id.get(selection_candidate_id)

        if selected_candidate is None:
            raise QualityError(
                f"Dedup mixed-type merge group {group_index} selected unknown "
                f"candidate {selection_candidate_id!r}."
            )

        selected_pair = (
            selected_candidate.statement_type,
            selected_candidate.normalized_statement_type,
        )

        if selected_pair not in distinct_type_pairs:
            raise QualityError(
                f"Dedup mixed-type merge group {group_index} selected a type pair "
                f"that is not preserved in the decision group."
            )


def _validate_dedup_issue_candidate_ids(
    *,
    review_request: SFIDedupReviewRequest,
    validation_verdict: SFIDedupValidationVerdict,
) -> None:
    """Validate checker issue references against the bounded review request.

    Parameters
    ----------
    review_request
        Bounded review request supplied to both LLM stages.
    validation_verdict
        Parsed checker verdict whose issue references should be validated.

    Raises
    ------
    QualityError
        If an issue references a candidate outside the review request.
    """

    expected_candidate_ids = {
        candidate.registry_candidate_id for candidate in review_request.candidates
    }

    for issue_index, issue in enumerate(validation_verdict.issues, start=1):
        unknown_candidate_ids = sorted(
            set(issue.candidate_ids) - expected_candidate_ids
        )

        if unknown_candidate_ids:
            raise QualityError(
                f"SFI dedup validation issue {issue_index} references candidate IDs "
                f"outside the review request: {unknown_candidate_ids}."
            )


def _validate_dedup_merge_identity_scope_compatibility(
    *, review_request: SFIDedupReviewRequest, review_response: SFIDedupReviewResponse
) -> None:
    """Validate semantic identity-scope compatibility for proposed merges.

    Same-type merges require exact configured identity-scope equality. Mixed-type
    merges retain that strict path when possible; otherwise they may use canonical-type
    scope resolution only when direct same-source-occurrence cross-type evidence covers
    the proposed group and every shared scope dimension agrees.

    Parameters
    ----------
    review_request
        Bounded review request containing candidate identity-scope evidence.
    review_response
        Structured dedup response containing proposed merge groups.

    Raises
    ------
    QualityError
        If a proposed merge has incompatible identity scope or attempts relaxed
        mixed-type scope resolution without direct source-occurrence evidence.
    """

    candidates_by_id = {
        candidate.registry_candidate_id: candidate
        for candidate in review_request.candidates
    }

    for group_index, decision_group in enumerate(review_response.decision_groups):
        if decision_group.decision != "merge":
            continue

        group_candidates = [
            candidates_by_id[candidate_id]
            for candidate_id in decision_group.candidate_ids
        ]
        scope_signatures = {
            candidate.identity_scope_key or "" for candidate in group_candidates
        }
        scope_value_signatures = {
            tuple(candidate.identity_scope_values.items())
            for candidate in group_candidates
        }
        strict_scope_compatible = bool(
            len(scope_signatures) == 1 and len(scope_value_signatures) == 1
        )

        if strict_scope_compatible:
            continue

        type_pairs = {
            (candidate.statement_type, candidate.normalized_statement_type)
            for candidate in group_candidates
        }
        has_cross_type_occurrence_signal = (
            _decision_group_shares_exact_description_source_anchors(
                candidate_ids=decision_group.candidate_ids,
                review_request=review_request,
            )
        )

        if len(type_pairs) == 1 or not has_cross_type_occurrence_signal:
            raise QualityError(
                f"Dedup merge group {group_index} combines candidates from different "
                f"configured semantic identity scopes without direct same-source-"
                f"occurrence cross-type evidence. Return keep_separate, conflict, or "
                f"needs_review."
            )

        if decision_group.canonical_type_source_candidate_id is None:
            raise QualityError(
                f"Dedup mixed-type merge group {group_index} requires a canonical "
                f"type source candidate before identity scope can be resolved."
            )

        scope_conflicts = _find_scope_value_conflicts(
            [candidate.identity_scope_values for candidate in group_candidates]
        )

        if scope_conflicts:
            raise QualityError(
                f"Dedup mixed-type merge group {group_index} has contradictory "
                f"shared identity-scope values: {scope_conflicts}."
            )


def _validate_dedup_merge_scope_compatibility(
    *, review_request: SFIDedupReviewRequest, review_response: SFIDedupReviewResponse
) -> None:
    """Validate code-type and code-scope compatibility for proposed merges.

    Same-type coded merges require exact code-policy and configured scope agreement.
    Mixed-type merges retain that strict path when possible. When classification alone
    causes code-policy differences, direct same-source-occurrence cross-type evidence
    permits canonical source-code policy as long as shared scope dimensions do not
    contradict one another.

    Parameters
    ----------
    review_request
        Bounded review request containing candidate code type and scope evidence.
    review_response
        Structured dedup response containing proposed merge groups.

    Raises
    ------
    QualityError
        If a coded merge has no source-backed canonical code, preserves contradictory
        scope, or attempts relaxed mixed-type code policy without direct occurrence
        evidence.
    """

    candidates_by_id = {
        candidate.registry_candidate_id: candidate
        for candidate in review_request.candidates
    }

    for group_index, decision_group in enumerate(review_response.decision_groups):
        if decision_group.decision != "merge":
            continue

        group_candidates = [
            candidates_by_id[candidate_id]
            for candidate_id in decision_group.candidate_ids
        ]
        coded_candidates = [
            candidate
            for candidate in group_candidates
            if candidate.normalized_statement_code is not None
        ]

        if not coded_candidates:
            continue

        distinct_normalized_codes = {
            candidate.normalized_statement_code for candidate in coded_candidates
        }

        if len(distinct_normalized_codes) > 1:
            selected_candidate_id = decision_group.canonical_code_source_candidate_id
            canonical_candidate = candidates_by_id.get(selected_candidate_id or "")
        else:
            canonical_type_candidate = candidates_by_id.get(
                decision_group.canonical_type_source_candidate_id or ""
            )
            shared_normalized_code = next(iter(distinct_normalized_codes))

            if (
                canonical_type_candidate is not None
                and canonical_type_candidate.normalized_statement_code
                == shared_normalized_code
                and canonical_type_candidate.resolved_code_type is not None
            ):
                canonical_candidate = canonical_type_candidate
            else:
                canonical_candidate = sorted(
                    coded_candidates,
                    key=lambda candidate: candidate.registry_candidate_id,
                )[0]

        if (
            canonical_candidate is None
            or canonical_candidate.resolved_code_type is None
        ):
            raise QualityError(
                f"Dedup coded merge group {group_index} has no fully resolved "
                f"canonical code source candidate."
            )

        canonical_code_type = canonical_candidate.resolved_code_type
        applicable_code_types = {
            candidate.applicable_code_type for candidate in group_candidates
        }
        scope_signatures = {
            candidate.code_scope_key or "" for candidate in group_candidates
        }
        scope_value_signatures = {
            tuple(candidate.code_scope_values.items()) for candidate in group_candidates
        }
        strict_scope_compatible = bool(
            applicable_code_types == {canonical_code_type}
            and len(scope_signatures) == 1
            and len(scope_value_signatures) == 1
        )

        if strict_scope_compatible:
            continue

        type_pairs = {
            (candidate.statement_type, candidate.normalized_statement_type)
            for candidate in group_candidates
        }
        has_cross_type_occurrence_signal = (
            _decision_group_shares_exact_description_source_anchors(
                candidate_ids=decision_group.candidate_ids,
                review_request=review_request,
            )
        )

        if len(type_pairs) == 1 or not has_cross_type_occurrence_signal:
            raise QualityError(
                f"Dedup coded merge group {group_index} combines incompatible code "
                f"types or scopes without direct same-source-occurrence cross-type "
                f"evidence. Return keep_separate, conflict, or needs_review."
            )

        scope_conflicts = _find_scope_value_conflicts(
            [candidate.code_scope_values for candidate in group_candidates]
        )

        if scope_conflicts:
            raise QualityError(
                f"Dedup mixed-type coded merge group {group_index} has contradictory "
                f"shared code-scope values: {scope_conflicts}."
            )


def _validate_dedup_representative_selections(
    *, review_request: SFIDedupReviewRequest, review_response: SFIDedupReviewResponse
) -> None:
    """Validate representative-candidate selections for dedup decision groups.

    Python validates only stable cross-object contracts. The producer/checker LLM
    decides which existing candidate has the cleanest faithful source-facing form.

    Parameters
    ----------
    review_request
        Bounded review request containing the available candidates.
    review_response
        Structured dedup response whose representative selections should be checked.

    Raises
    ------
    QualityError
        If a merge omits its representative, a non-merge asserts one, or the selected
        candidate is unknown or outside the decision group.
    """

    candidates_by_id = {
        candidate.registry_candidate_id: candidate
        for candidate in review_request.candidates
    }

    for group_index, decision_group in enumerate(review_response.decision_groups):
        representative_candidate_id = decision_group.representative_candidate_id

        if decision_group.decision != "merge":
            if representative_candidate_id is not None:
                raise QualityError(
                    f"Dedup decision group {group_index} must leave "
                    f"representative_candidate_id null unless decision='merge'."
                )

            continue

        if representative_candidate_id is None:
            raise QualityError(
                f"Dedup merge group {group_index} must provide representative_candidate_id."
            )

        if representative_candidate_id not in decision_group.candidate_ids:
            raise QualityError(
                f"Dedup merge group {group_index} selected representative candidate "
                f"{representative_candidate_id!r}, which is outside that decision group."
            )

        if representative_candidate_id not in candidates_by_id:
            raise QualityError(
                f"Dedup merge group {group_index} selected unknown representative "
                f"candidate {representative_candidate_id!r}."
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
            child_id=child_id,
            parent_candidates_by_id=parent_candidates_by_child_id.get(child_id, {}),
            selected_parent_ids=selected_parent_ids,
        )
        _validate_resolved_child_uses_strongest_local_parent(
            child_id=child_id,
            parent_candidates_by_id=parent_candidates_by_child_id.get(child_id, {}),
            selected_parent_ids=selected_parent_ids,
        )


def _validate_resolved_child_prefers_source_visible_parent(
    *,
    child_id: str,
    parent_candidates_by_id: dict[str, SFIHasChildParentCandidate],
    selected_parent_ids: set[str],
) -> None:
    """Validate that resolved children do not choose weak parents over visible parents.

    Source-visible direct-parent evidence is a strong signal against root fallback and
    weak semantic/topic fallback. It is not an absolute veto over another selected
    non-root parent carrying equally corroborated direct-parent evidence, such as a
    locally compatible code-parent hint, typed source-local controlled scope, or an
    explicit source-scope grouping relationship.

    Parameters
    ----------
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

        if _candidate_direct_parent_evidence_tier(candidate) <= 1:
            continue

        selected_weak_parent_ids.append(endpoint_id)

    if not selected_weak_parent_ids:
        return

    raise QualityError(
        f"hasChild response for child {child_id!r} selected weak parent "
        f"endpoint IDs {sorted(selected_weak_parent_ids)}, but the bounded "
        f"candidate set includes source-visible direct parent endpoint IDs "
        f"{sorted(source_visible_parent_ids)}. Select the source-visible direct "
        f"parent, or select a non-root candidate with equally corroborated "
        f"direct-parent evidence such as a locally compatible code-parent hint, "
        f"typed source-local controlled scope, or source-scope grouping. Do not "
        f"choose a root, nearby, "
        f"same-topic, or semantic parent over a source-visible direct parent."
    )


def _validate_resolved_child_uses_strongest_local_parent(
    *,
    child_id: str,
    parent_candidates_by_id: dict[str, SFIHasChildParentCandidate],
    selected_parent_ids: set[str],
) -> None:
    """Validate that selected parents do not lose to stronger same-type local parents.

    This dominance guard catches semantically wrong but structurally valid hasChild
    selections: for example, selecting a nearby previous grouping when another
    candidate of the same allowed parent type has a locally compatible code-parent
    hint, typed source-local controlled scope, or source-scope grouping evidence. The
    rule remains curriculum-agnostic because it compares only candidate evidence tiers
    and configured statement types.

    Parameters
    ----------
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
        candidate of the same direct parent statement type. Corroborated candidates in
        tiers zero and one are peers; Python does not force a source-visible candidate
        over a locally compatible code-parent candidate or vice versa.
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
            if _candidate_direct_parent_evidence_tier(candidate) <= 1
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
        candidate_tier = _candidate_direct_parent_evidence_tier(candidate)
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
        candidate_tier = _candidate_direct_parent_evidence_tier(candidate)
        selected_tier = selected_best_tier_by_statement_type.get(
            candidate.statement_type
        )

        if selected_tier is None or selected_tier <= 1:
            continue

        if candidate_tier > 1 or candidate_tier >= selected_tier:
            continue

        stronger_parent_ids.append(endpoint_id)
        dominated_parent_ids.extend(
            selected_candidate.endpoint_id
            for selected_candidate in selected_non_root_candidates
            if selected_candidate.statement_type == candidate.statement_type
            and _candidate_direct_parent_evidence_tier(selected_candidate)
            > candidate_tier
        )

    if not stronger_parent_ids:
        return

    raise QualityError(
        f"hasChild response for child {child_id!r} selected weaker parent "
        f"endpoint IDs {sorted(set(dominated_parent_ids))}, but the bounded "
        f"candidate set contains stronger local direct-parent candidates of the "
        f"same statement type: {sorted(set(stronger_parent_ids))}. Select the "
        f"corroborated parent, or explain a source conflict by choosing a parent "
        f"with equal or stronger direct-parent evidence."
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


def _validate_scope_values_contract(
    *,
    candidate_id: str,
    provided_scope_values: dict[str, str],
    scope_field_name: str,
    scope_statement_types: Sequence[str],
    scope_value_alias_maps: dict[str, dict[str, str]],
) -> None:
    """Validate one LLM-selected semantic scope against runtime configuration.

    The LLM chooses the semantic values. Python checks only exact configured dimensions
    and controlled-value membership. Configured aliases are accepted because the
    registry mechanically canonicalizes them before key construction.

    Parameters
    ----------
    candidate_id
        Window-local candidate identifier used in validation errors.
    provided_scope_values
        Scope mapping returned by the producer/checker.
    scope_field_name
        Human-readable field name used in validation errors.
    scope_statement_types
        Ordered configured scope dimensions.
    scope_value_alias_maps
        Normalized configured aliases mapped to canonical controlled values.

    Raises
    ------
    QualityError
        If dimensions are missing, extra, or contain unknown values.
    """

    expected_scope_statement_types = list(scope_statement_types)
    actual_scope_statement_types = list(provided_scope_values)

    if set(actual_scope_statement_types) != set(expected_scope_statement_types):
        raise QualityError(
            f"Candidate {candidate_id!r} {scope_field_name} keys must exactly match "
            f"the configured dimensions {expected_scope_statement_types!r}; got "
            f"{actual_scope_statement_types!r}."
        )

    for scope_statement_type in expected_scope_statement_types:
        alias_to_canonical = scope_value_alias_maps.get(scope_statement_type)

        if alias_to_canonical is None:
            raise QualityError(
                f"Candidate {candidate_id!r} {scope_field_name} requires configured "
                f"controlled values for {scope_statement_type!r}."
            )

        scope_value = provided_scope_values[scope_statement_type]
        scope_value_key = normalize_controlled_value_key(scope_value)

        if scope_value_key not in alias_to_canonical:
            raise QualityError(
                f"Candidate {candidate_id!r} supplied unknown {scope_field_name} "
                f"value {scope_value!r} for {scope_statement_type!r}."
            )


def verify_sfi_dedup_review_integrity(
    *, review_request: SFIDedupReviewRequest, review_response: SFIDedupReviewResponse
) -> None:
    """Validate universal integrity of one structured SFI dedup response.

    This function intentionally avoids curriculum semantics, duplicate-identity
    judgments, code-compatibility rules, hierarchy inference, and wording heuristics.
    Those decisions belong to the producer/checker LLM flow under the runtime
    curriculum instructions.

    Parameters
    ----------
    review_request
        Bounded review request supplied to the LLM.
    review_response
        Parsed producer or final dedup review response.

    Raises
    ------
    QualityError
        If review-set identity, exact candidate coverage, or canonical-code selection
        integrity is invalid.
    """

    if review_response.review_set_id != review_request.review_set_id:
        raise QualityError(
            f"Dedup response review_set_id {review_response.review_set_id!r} does "
            f"not match request review_set_id {review_request.review_set_id!r}."
        )

    _validate_dedup_response_candidate_coverage(
        review_request=review_request, review_response=review_response
    )
    _validate_dedup_canonical_code_selections(
        review_request=review_request, review_response=review_response
    )
    _validate_dedup_canonical_type_selections(
        review_request=review_request, review_response=review_response
    )
    _validate_dedup_merge_scope_compatibility(
        review_request=review_request, review_response=review_response
    )
    _validate_dedup_merge_identity_scope_compatibility(
        review_request=review_request, review_response=review_response
    )
    _validate_dedup_representative_selections(
        review_request=review_request, review_response=review_response
    )


def verify_sfi_dedup_validation_integrity(
    *,
    draft_response: SFIDedupReviewResponse,
    review_request: SFIDedupReviewRequest,
    validation_verdict: SFIDedupValidationVerdict,
) -> None:
    """Validate universal integrity of an SFI dedup checker verdict.

    Python verifies only stable cross-object contracts. It does not judge whether the
    checker made the correct semantic merge decision; curriculum-specific semantic
    review remains the checker's responsibility under the runtime instructions.

    Parameters
    ----------
    draft_response
        First-stage dedup response reviewed by the checker.
    review_request
        Original bounded dedup request.
    validation_verdict
        Parsed second-stage checker verdict.

    Raises
    ------
    QualityError
        If verdict identity, issue references, or selected response integrity is
        invalid.
    """

    if validation_verdict.review_set_id != review_request.review_set_id:
        raise QualityError(
            f"Dedup validation review_set_id "
            f"{validation_verdict.review_set_id!r} does not match request "
            f"review_set_id {review_request.review_set_id!r}."
        )

    verify_sfi_dedup_review_integrity(
        review_request=review_request, review_response=draft_response
    )
    _validate_dedup_issue_candidate_ids(
        review_request=review_request, validation_verdict=validation_verdict
    )

    selected_response = (
        draft_response
        if validation_verdict.passed
        else validation_verdict.corrected_response
    )

    if selected_response is None:
        raise QualityError(
            "A failing SFI dedup validation verdict must provide a complete corrected "
            "response."
        )

    verify_sfi_dedup_review_integrity(
        review_request=review_request, review_response=selected_response
    )


def verify_sfi_extraction_integrity(
    *,
    extraction_result: SFIExtractionResult,
    kg_config: CreateKGConfig,
    window: ExtractionWindow,
) -> None:
    """Validate universal integrity constraints for an SFI extraction result.

    Python enforces exact source references, configured statement/scope contracts, code
    syntax/evidence, and cross-object identity. It does not decide which curriculum
    statements exist, compose inherited stems, select semantic scope, or repair source
    anomalies; those decisions belong to the producer/checker LLM flow.

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
        If the result violates a stable identity, source-reference, configuration, or
        cross-object integrity constraint.
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
    scope_value_alias_maps = _build_scope_value_alias_maps(kg_config)
    source_unit_map = build_sfi_source_unit_map(window)
    source_units = list(source_unit_map.values())

    _validate_result_identity(result=extraction_result, window=window)

    for candidate in extraction_result.sfi_candidates:
        _validate_candidate_statement_type(
            candidate=candidate,
            statement_type_alias_to_canonical=statement_type_alias_to_canonical,
            statement_type_normalized_by_label=statement_type_normalized_by_label,
        )
        resolved_code_type = _validate_candidate_code(
            candidate=candidate,
            code_patterns_by_type=dict(kg_config.academic_standards.code_patterns),
            statement_type_code_type_by_label=statement_type_code_type_by_label,
            window=window,
        )
        _validate_candidate_scope_contracts(
            candidate=candidate,
            code_scope_statement_types=(
                kg_config.academic_standards.code_scope_statement_types
            ),
            identity_scope_statement_types=(
                kg_config.academic_standards.identity_scope_statement_types
            ),
            resolved_code_type=resolved_code_type,
            scope_value_alias_maps=scope_value_alias_maps,
        )
        _validate_candidate_source_anchors(
            candidate=candidate,
            resolved_code_type=resolved_code_type,
            source_unit_map=source_unit_map,
            window=window,
        )

    _log_duplicate_sfi_candidate_warnings(extraction_result)

    for auxiliary_candidate in extraction_result.auxiliary_candidates:
        _validate_auxiliary_source_text(
            auxiliary_id=auxiliary_candidate.auxiliary_id,
            source_text=auxiliary_candidate.source_text,
            source_units=source_units,
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
