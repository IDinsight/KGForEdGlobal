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
)
from skg.kgs.sfi_source_anchors import (
    SFISourceUnit,
    build_sfi_source_unit_map,
    find_source_anchor_span,
    source_anchor_set_signature,
)
from skg.kgs.utils import resolve_candidate_code
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
SAME_SOURCE_OCCURRENCE_CROSS_TYPE_SIGNAL = "same_source_occurrence_cross_type"
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


def _log_candidate_source_integrity_warnings(
    *,
    candidate: SFICandidate,
    statement_type_code_type_by_label: dict[str, Optional[str]],
    window: ExtractionWindow,
) -> None:
    """Log non-blocking warnings for candidate-local source-integrity risks.

    These checks are intentionally diagnostic. They flag suspicious field alignment
    that may require semantic review, but they do not reject an extraction result
    because source layouts and curriculum conventions vary across documents.

    Parameters
    ----------
    candidate
        Candidate to inspect.
    statement_type_code_type_by_label
        Configured code type for each canonical statement type.
    window
        Source extraction window.
    """

    cited_source_text = _get_candidate_cited_source_text(
        candidate=candidate, window=window
    )
    cited_source_text_normalized = _normalize_text(cited_source_text)
    description_normalized = _normalize_text(candidate.description)
    source_text_normalized = _normalize_text(candidate.source_text)
    warning_context = (
        f"window_id={window.window_id!r}, candidate_id={candidate.candidate_id!r}, "
        f"statement_type={candidate.statement_type!r}, "
        f"statement_code={candidate.statement_code!r}, "
        f"table_header_indexes={candidate.table_header_indexes!r}, "
        f"table_row_indexes={candidate.table_row_indexes!r}"
    )

    if description_normalized not in cited_source_text_normalized:
        logger.warning(
            f"SFI candidate description is not visibly contained in its cited source "
            f"locations; {warning_context}."
        )

    if (
        window.table is not None
        and source_text_normalized not in cited_source_text_normalized
    ):
        logger.warning(
            f"SFI candidate source_text is present in the extraction window but not "
            f"visibly contained in its cited table locations; {warning_context}."
        )

    statement_code = candidate.statement_code

    if statement_code is not None:
        code_key = _normalize_code_for_parent_match(statement_code)
        description_key = _normalize_code_for_parent_match(candidate.description)
        source_text_key = _normalize_code_for_parent_match(candidate.source_text)
        cited_source_key = _normalize_code_for_parent_match(cited_source_text)

        if description_key.startswith(code_key) or description_key.endswith(code_key):
            logger.warning(
                f"SFI candidate description appears to include its separately "
                f"represented statement_code; {warning_context}."
            )

        if code_key not in source_text_key:
            logger.warning(
                f"SFI candidate statement_code is validated at window level but is "
                f"not visibly present in the candidate source_text; {warning_context}."
            )

        if window.table is not None and code_key not in cited_source_key:
            logger.warning(
                f"SFI candidate statement_code is validated at window level but is "
                f"not visibly present in its cited table locations; {warning_context}."
            )

        return

    configured_code_type = statement_type_code_type_by_label.get(
        candidate.statement_type
    )
    candidate_source_code_key = _normalize_code_for_parent_match(candidate.source_text)
    matching_code_values = sorted(
        {
            code_match.normalized_value
            for code_match in window.code_matches
            if (
                configured_code_type is None
                or code_match.code_type == configured_code_type
            )
            and _normalize_code_for_parent_match(code_match.raw_value)
            in candidate_source_code_key
        }
    )

    if matching_code_values:
        logger.warning(
            f"SFI candidate has statement_code=None even though its source_text "
            f"contains configured code evidence {matching_code_values!r}; "
            f"{warning_context}."
        )


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


def _render_candidate_source_text(
    resolved_spans: Sequence[tuple[SFISourceUnit, int, int]],
) -> str:
    """Render deterministic candidate evidence from resolved source spans.

    Exact source text is preserved inside every span. Whitespace between spans in the
    same source unit is preserved when the intervening source contains only whitespace.
    Spans from different source units, or spans separated by other visible source text,
    are joined with a newline so the rendering does not imply false contiguity.

    Parameters
    ----------
    resolved_spans
        Unique candidate evidence spans in deterministic source order.

    Returns
    -------
    str
        Non-empty deterministic evidence rendering.

    Raises
    ------
    QualityError
        If no non-empty source evidence can be rendered.
    """

    rendered_parts: list[str] = []
    previous_span: Optional[tuple[SFISourceUnit, int, int]] = None

    for source_unit, start_char, end_char in resolved_spans:
        fragment = source_unit.source_text[start_char:end_char]

        if not fragment:
            continue

        if previous_span is None:
            rendered_parts.append(fragment)
        else:
            previous_unit, _previous_start, previous_end = previous_span

            if previous_unit.source_unit_id == source_unit.source_unit_id:
                intervening_text = source_unit.source_text[previous_end:start_char]
                separator = intervening_text if not intervening_text.strip() else "\n"
            else:
                separator = "\n"

            rendered_parts.extend([separator, fragment])

        previous_span = (source_unit, start_char, end_char)

    rendered_text = "".join(rendered_parts).strip()

    if not rendered_text:
        raise QualityError(
            "An SFI candidate must resolve to non-empty source-anchor evidence."
        )

    return rendered_text


def _resolve_candidate_evidence_spans(
    *, candidate: SFICandidate, source_unit_map: dict[str, SFISourceUnit]
) -> list[tuple[SFISourceUnit, int, int]]:
    """Resolve and order the unique source spans supporting one candidate.

    Description and code anchors are resolved against the same stable source-unit map.
    Exact duplicate spans shared by both anchor lists are retained once. Overlapping
    description/code evidence spans in one source unit are coalesced for rendering;
    description-anchor overlap remains subject to its separate integrity validator.

    Parameters
    ----------
    candidate
        Candidate whose description and code anchors are being resolved.
    source_unit_map
        Stable source units available in the extraction window.

    Returns
    -------
    list[tuple[SFISourceUnit, int, int]]
        Unique source units and inclusive-start/exclusive-end character spans in
        deterministic source order.

    Raises
    ------
    QualityError
        If an anchor references an unknown source unit or identifies a nonexistent
        excerpt occurrence.
    """

    resolved_spans: list[tuple[SFISourceUnit, int, int]] = []
    seen_span_keys: set[tuple[str, int, int]] = set()

    for anchor in [
        *candidate.description_source_anchors,
        *candidate.code_source_anchors,
    ]:
        source_unit = source_unit_map.get(anchor.source_unit_id)

        if source_unit is None:
            raise QualityError(
                f"Candidate {candidate.candidate_id!r} references unknown "
                f"source_unit_id {anchor.source_unit_id!r}."
            )

        try:
            start_char, end_char = find_source_anchor_span(
                anchor=anchor, source_unit=source_unit
            )
        except ValueError as exc:
            raise QualityError(
                f"Candidate {candidate.candidate_id!r} has an invalid source anchor: "
                f"{exc}"
            ) from exc

        span_key = (source_unit.source_unit_id, start_char, end_char)

        if span_key in seen_span_keys:
            continue

        seen_span_keys.add(span_key)
        resolved_spans.append((source_unit, start_char, end_char))

    resolved_spans.sort(key=lambda item: (item[0].source_order, item[1], item[2]))
    coalesced_spans: list[tuple[SFISourceUnit, int, int]] = []

    for source_unit, start_char, end_char in resolved_spans:
        if not coalesced_spans:
            coalesced_spans.append((source_unit, start_char, end_char))
            continue

        previous_unit, previous_start, previous_end = coalesced_spans[-1]

        if (
            previous_unit.source_unit_id == source_unit.source_unit_id
            and start_char < previous_end
        ):
            coalesced_spans[-1] = (
                previous_unit,
                previous_start,
                max(previous_end, end_char),
            )
            continue

        coalesced_spans.append((source_unit, start_char, end_char))

    return coalesced_spans


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


def _validate_candidate_code(
    *,
    candidate: SFICandidate,
    code_patterns_by_type: dict[str, str],
    statement_type_code_type_by_label: dict[str, Optional[str]],
    window: ExtractionWindow,
) -> None:
    """Validate that an optional candidate code is configured and source-exposed.

    The shared deterministic resolver identifies the candidate's authoritative code
    type. A statement type's declared code type is enforced when present; otherwise,
    the candidate code must match exactly one configured pattern. Python then verifies
    that the same typed code is exposed by the extraction window.

    This check deliberately does not decide whether the code is semantically attached
    to the correct statement. The validation LLM handles that judgment.

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
        If the code cannot be resolved to an allowed configured type or the resolved
        typed code is not exposed by the extraction window.
    """

    if candidate.statement_code is None:
        return

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

    matches_code_evidence = any(
        code_match.code_type == resolved_code_type
        and code_match.normalized_value == candidate.statement_code
        for code_match in window.code_matches
    )

    if not matches_code_evidence:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has statement_code "
            f"{candidate.statement_code!r} resolved as code_type "
            f"{resolved_code_type!r}, but that exact typed normalized code is not "
            f"exposed by the extraction window."
        )


def _validate_candidate_code_evidence(
    *, candidate: SFICandidate, window: ExtractionWindow
) -> None:
    """Validate that code anchors cite exact configured code evidence.

    Candidates without a `statement_code` require no code evidence and pass without
    further checks.

    Parameters
    ----------
    candidate
        Candidate whose code anchors are being validated.
    window
        Source extraction window supplying configured code matches.

    Raises
    ------
    QualityError
        If the candidate declares a `statement_code` but its code_source_anchors do not
        cite the exact raw configured code evidence for that code.
    """

    if candidate.statement_code is None:
        return

    matching_raw_values = {
        code_match.raw_value
        for code_match in window.code_matches
        if code_match.normalized_value == candidate.statement_code
    }
    anchored_code_values = {
        anchor.source_text for anchor in candidate.code_source_anchors
    }

    if not anchored_code_values.intersection(matching_raw_values):
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} code_source_anchors do not cite "
            f"the exact raw configured code evidence for statement_code="
            f"{candidate.statement_code!r}."
        )


def _validate_candidate_description_language(
    *,
    candidate: SFICandidate,
    description_languages: Sequence[str],
    window: ExtractionWindow,
) -> None:
    """Validate that the candidate language matches its description anchors.

    Parameters
    ----------
    candidate
        Candidate whose declared language is being validated.
    description_languages
        Source-unit languages for the candidate's description anchors, in anchor
        order.
    window
        Source extraction window supplying the fallback primary language.

    Raises
    ------
    QualityError
        If the candidate language does not match the language implied by its exact
        description source anchors.
    """

    nonempty_languages = sorted(
        {language.strip() for language in description_languages if language.strip()}
    )
    expected_language = (
        nonempty_languages[0]
        if len(nonempty_languages) == 1
        else "mul" if len(nonempty_languages) > 1 else window.primary_language
    )

    if candidate.language != expected_language:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} language must match its exact "
            f"description source anchors. Expected {expected_language!r}; got "
            f"{candidate.language!r}."
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


def _validate_candidate_source_anchors(
    *,
    candidate: SFICandidate,
    source_unit_map: dict[str, SFISourceUnit],
    window: ExtractionWindow,
) -> None:
    """Validate exact source anchors for one extracted SFI candidate.

    Parameters
    ----------
    candidate
        Candidate carrying exact description and optional code anchors.
    source_unit_map
        Stable source units available in the extraction window.
    window
        Source extraction window used for language and code-evidence checks.

    Raises
    ------
    QualityError
        If an anchor is unknown, points to a nonexistent excerpt occurrence, appears
        out of source order, fails to compose the candidate description, or does not
        support the candidate's code and language.
    """

    _resolve_candidate_evidence_spans(
        candidate=candidate, source_unit_map=source_unit_map
    )

    description_languages: list[str] = []
    description_positions: list[tuple[tuple[int, int, int, int], int, int]] = []
    resolved_description_anchors: list[tuple[SFISourceUnit, int, int]] = []

    for anchor in candidate.description_source_anchors:
        source_unit = source_unit_map[anchor.source_unit_id]
        start_char, end_char = find_source_anchor_span(
            anchor=anchor, source_unit=source_unit
        )
        description_positions.append((source_unit.source_order, start_char, end_char))
        description_languages.append(source_unit.language)
        resolved_description_anchors.append((source_unit, start_char, end_char))

    if description_positions != sorted(description_positions):
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} description_source_anchors must "
            f"follow source order."
        )

    _validate_description_anchor_span_contiguity(
        candidate=candidate, resolved_description_anchors=resolved_description_anchors
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

    _validate_candidate_description_language(
        candidate=candidate, description_languages=description_languages, window=window
    )
    _validate_candidate_code_evidence(candidate=candidate, window=window)


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


def _validate_description_anchor_span_contiguity(
    *,
    candidate: SFICandidate,
    resolved_description_anchors: Sequence[tuple[SFISourceUnit, int, int]],
) -> None:
    """Reject overlapping or selectively gapped excerpts within one source unit.

    Parameters
    ----------
    candidate
        Candidate whose description anchors are being validated.
    resolved_description_anchors
        Description source units and resolved character spans in candidate order.

    Raises
    ------
    QualityError
        If two consecutive description anchors in one source unit overlap or omit
        non-whitespace source-visible text between their excerpts.
    """

    for previous_anchor, current_anchor in zip(
        resolved_description_anchors, resolved_description_anchors[1:]
    ):
        previous_unit, _previous_start, previous_end = previous_anchor
        current_unit, current_start, _current_end = current_anchor

        if previous_unit.source_unit_id != current_unit.source_unit_id:
            continue

        if current_start < previous_end:
            raise QualityError(
                f"Candidate {candidate.candidate_id!r} has overlapping description "
                f"anchors in source unit {current_unit.source_unit_id!r}."
            )

        omitted_text = current_unit.source_text[previous_end:current_start]

        if omitted_text.strip():
            raise QualityError(
                f"Candidate {candidate.candidate_id!r} description anchors omit "
                f"source-visible text within source unit "
                f"{current_unit.source_unit_id!r}. Use one contiguous exact excerpt "
                f"or include the omitted text in the description anchors."
            )


def _validate_extraction_result_is_canonical(
    *, extraction_result: SFIExtractionResult, window: ExtractionWindow
) -> None:
    """Validate deterministic candidate rendering, ordering, IDs, and references.

    Parameters
    ----------
    extraction_result
        Extraction result expected to already be in canonical form.
    window
        Source extraction window supplying stable source units.

    Raises
    ------
    QualityError
        If any candidate evidence rendering, candidate position, candidate ID, or
        auxiliary candidate reference differs from deterministic canonical form.
    """

    canonical_result = canonicalize_sfi_extraction_result(
        extraction_result=extraction_result, window=window
    )

    if extraction_result.model_dump(mode="python") != canonical_result.model_dump(
        mode="python"
    ):
        raise QualityError(
            "SFI extraction results must use deterministic anchor-composed source_text, "
            "candidate source order, sequential candidate IDs, and remapped auxiliary "
            "candidate references."
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


def canonicalize_sfi_extraction_result(
    *, extraction_result: SFIExtractionResult, window: ExtractionWindow
) -> SFIExtractionResult:
    """Canonicalize candidate evidence text, source order, IDs, and references.

    Exact source anchors are authoritative. Python deterministically renders each
    candidate's `source_text` from its description and code anchors, orders candidates
    by the first contributing source span, assigns sequential `sfi_1` through `sfi_N`
    identifiers, and remaps auxiliary candidate references. Curriculum semantics,
    candidate classification, and occurrence-boundary decisions are not changed.

    Parameters
    ----------
    extraction_result
        Parsed producer or checker extraction result.
    window
        Source extraction window supplying stable source units.

    Returns
    -------
    SFIExtractionResult
        A newly validated result in deterministic canonical form.

    Raises
    ------
    QualityError
        If candidate anchors cannot be resolved into valid deterministic evidence.
    """

    source_unit_map = build_sfi_source_unit_map(window)
    sortable_candidates: list[
        tuple[
            tuple[tuple[int, int, int, int], int, int],
            int,
            SFICandidate,
            str,
        ]
    ] = []

    for original_index, candidate in enumerate(extraction_result.sfi_candidates):
        resolved_spans = _resolve_candidate_evidence_spans(
            candidate=candidate, source_unit_map=source_unit_map
        )

        if not resolved_spans:
            raise QualityError(
                f"Candidate {candidate.candidate_id!r} has no resolvable source "
                f"evidence spans."
            )

        first_source_unit, first_start_char, first_end_char = resolved_spans[0]
        source_order_key = (
            first_source_unit.source_order,
            first_start_char,
            first_end_char,
        )
        canonical_source_text = _render_candidate_source_text(resolved_spans)
        sortable_candidates.append(
            (
                source_order_key,
                original_index,
                candidate,
                canonical_source_text,
            )
        )

    sortable_candidates.sort(key=lambda item: (item[0], item[1]))
    candidate_id_map: dict[str, str] = {}
    canonical_candidates: list[SFICandidate] = []

    for candidate_number, (
        _source_order_key,
        _original_index,
        candidate,
        canonical_source_text,
    ) in enumerate(sortable_candidates, start=1):
        canonical_candidate_id = f"sfi_{candidate_number}"
        candidate_id_map[candidate.candidate_id] = canonical_candidate_id
        canonical_candidates.append(
            candidate.model_copy(
                deep=True,
                update={
                    "candidate_id": canonical_candidate_id,
                    "source_text": canonical_source_text,
                },
            )
        )

    canonical_auxiliary_candidates = [
        auxiliary_candidate.model_copy(
            deep=True,
            update={
                "related_candidate_ids": [
                    candidate_id_map[candidate_id]
                    for candidate_id in auxiliary_candidate.related_candidate_ids
                ]
            },
        )
        for auxiliary_candidate in extraction_result.auxiliary_candidates
    ]
    canonical_payload = extraction_result.model_dump(mode="python")
    canonical_payload.update(
        {
            "auxiliary_candidates": canonical_auxiliary_candidates,
            "sfi_candidates": canonical_candidates,
        }
    )
    return SFIExtractionResult.model_validate(canonical_payload)


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
        If the result violates a stable identity, policy, canonical anchor-backed
        evidence, or reference-integrity constraint.
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
    _validate_extraction_result_is_canonical(
        extraction_result=extraction_result, window=window
    )
    _validate_candidate_ids(extraction_result)
    source_unit_map = build_sfi_source_unit_map(window)

    for candidate in extraction_result.sfi_candidates:
        _validate_candidate_source_anchors(
            candidate=candidate, source_unit_map=source_unit_map, window=window
        )
        _validate_candidate_statement_type(
            candidate=candidate,
            statement_type_alias_to_canonical=statement_type_alias_to_canonical,
            statement_type_normalized_by_label=statement_type_normalized_by_label,
        )
        _validate_candidate_table_indexes(candidate=candidate, window=window)
        _validate_candidate_code(
            candidate=candidate,
            code_patterns_by_type=dict(kg_config.academic_standards.code_patterns),
            statement_type_code_type_by_label=statement_type_code_type_by_label,
            window=window,
        )
        _log_candidate_source_integrity_warnings(
            candidate=candidate,
            statement_type_code_type_by_label=statement_type_code_type_by_label,
            window=window,
        )

    _log_duplicate_sfi_candidate_warnings(extraction_result)
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
