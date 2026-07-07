"""This module contains functionalities related to validating LLM-produced knowledge
graph artifacts.

NB: The Pydantic schemas validate structure and field-level invariants. The validators
in this module enforce quality checks that require access to other inputs.
"""

# Standard Library
import re

from dataclasses import dataclass
from typing import Any

# Package Library
from skg.kgs.schemas import (
    ExtractionWindow,
    SFICandidate,
    SFIDedupReviewRequest,
    SFIDedupReviewResponse,
    SFIExtractionResult,
    SFIHasChildParentCandidate,
    SFIHasChildResolutionRequest,
    SFIHasChildResolutionResponse,
)
from skg.page_ir_extraction.validators import QualityError
from skg.schemas import CreateKGConfig

ACTIVE_OUTLINE_STACK_PARENT_REASON = "active_outline_stack_parent"
CANONICAL_SCOPE_PARENT_MATCH_REASON = "canonical_scope_parent_match"
CODE_PARENT_HINT_REASON = "code_parent_hint"
MATCHED_SECTION_PATH_LABEL_REASON = "matched_section_path_label"
NEARBY_SOURCE_CONTEXT_KEY_REASON = "nearby_source_context_key"
NEAREST_PRECEDING_GROUPING_REASON = "nearest_preceding_grouping"
ROOT_EVIDENCE_REASON = "root_fallback"
SAME_SOURCE_CONTEXT_KEY_REASON = "same_source_context_key"
SAME_SOURCE_SEGMENT_REASON = "same_source_segment"
SAME_SOURCE_WINDOW_REASON = "same_source_window"
SAME_TABLE_CONTEXT_REASON = "same_table_context"
SAME_TABLE_IMMEDIATE_PARENT_REASON = "same_table_immediate_parent"
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
        SAME_TABLE_CONTEXT_REASON,
        SAME_TABLE_IMMEDIATE_PARENT_REASON,
        SOURCE_SCOPE_GROUPING_REASON,
    }
)


@dataclass(frozen=True)
class SFIExtractionQualityCtx:
    """Context for SFI extraction quality checks.

    Attributes
    ----------
    extraction_result
        Parsed SFI extraction result produced for the window.
    source_visible_text_normalized
        Normalized text built only from source-visible extraction-window text, raw
        table headers, and raw table body rows. Deterministic hints, KG config text,
        constructed table source_text, and helper-only filldown context are
        intentionally excluded.
    statement_type_alias_to_canonical
        Mapping from normalized canonical labels and aliases to canonical labels.
    statement_type_normalized_by_label
        Mapping from canonical statement-type labels to configured normalized types.
    table_header_text_normalized
        Normalized raw table-header text for the source window. Empty for block windows.
    table_header_text_normalized_by_index
        Normalized raw table-header text keyed by source header-row index. Empty for
        block windows.
    table_row_text_normalized
        Normalized raw table-body-row text for the source window. Empty for block
        windows.
    table_row_text_normalized_by_index
        Normalized raw table-body-row text keyed by source body-row index. Empty for
        block windows.
    window
        Source extraction window passed to the LLM.
    """

    extraction_result: SFIExtractionResult
    source_visible_text_normalized: str
    statement_type_alias_to_canonical: dict[str, str]
    statement_type_normalized_by_label: dict[str, str]
    table_header_text_normalized: str
    table_header_text_normalized_by_index: dict[int, str]
    table_row_text_normalized: str
    table_row_text_normalized_by_index: dict[int, str]
    window: ExtractionWindow


def _append_row_cell_texts(*, row: dict[str, Any], texts: list[str]) -> None:
    """Append text-bearing cells from one raw table row to a text accumulator.

    Parameters
    ----------
    row
        Raw table row payload from the extraction window.
    texts
        Mutable accumulator for source-visible text snippets.
    """

    for cell in row.get("cells") or []:
        text_unit = cell.get("text") or {}
        text = str(text_unit.get("text") or "").strip()

        if text:
            texts.append(text)


def _build_candidate_cited_table_support_text(
    *, candidate: SFICandidate, ctx: SFIExtractionQualityCtx
) -> str:
    """Build normalized support text from a table candidate's cited source indexes.

    Table candidate descriptions must be supported by the same source-visible header
    rows and body rows that the candidate cites. This prevents a candidate from using a
    description copied from a different row in the same extraction window while
    pointing its provenance fields at another row.

    Parameters
    ----------
    candidate
        Table-derived candidate whose cited indexes define the allowed support text.
    ctx
        Quality-check context with normalized table text keyed by source indexes.

    Returns
    -------
    str
        Normalized source-visible text from the candidate's cited header and body rows.

    Raises
    ------
    QualityError
        If the function is called for a non-table window.
    """

    if ctx.window.table is None:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} cited-table support validation "
            f"requires a table window."
        )

    support_parts: list[str] = []

    if candidate.table_header_indexes:
        support_parts.append(
            _build_normalized_text_blob_for_indexes(
                indexes=candidate.table_header_indexes,
                ordered_indexes=list(range(len(ctx.window.table.header_rows))),
                text_by_index=ctx.table_header_text_normalized_by_index,
            )
        )

    if candidate.table_row_indexes:
        support_parts.append(
            _build_normalized_text_blob_for_indexes(
                indexes=candidate.table_row_indexes,
                ordered_indexes=ctx.window.table.row_indexes,
                text_by_index=ctx.table_row_text_normalized_by_index,
            )
        )

    return _normalize_text("\n".join(support_parts))


def _build_normalized_text_blob_for_indexes(
    *, indexes: list[int], ordered_indexes: list[int], text_by_index: dict[int, str]
) -> str:
    """Build normalized source-visible text for a selected set of indexes.

    Parameters
    ----------
    indexes
        Header or body-row indexes cited by a candidate.
    ordered_indexes
        Source-order list of indexes available in the current table window.
    text_by_index
        Normalized source-visible text keyed by header or body-row index.

    Returns
    -------
    str
        Normalized source-visible text from the cited indexes, in source order.
    """

    selected_indexes = set(indexes)
    return _normalize_text(
        "\n".join(
            text_by_index.get(index, "")
            for index in ordered_indexes
            if index in selected_indexes
        )
    )


def _build_source_visible_text_blob(
    *, table_header_text_blob: str, table_row_text_blob: str, window: ExtractionWindow
) -> str:
    """Build the full source-visible text blob for extraction quality checks.

    Parameters
    ----------
    table_header_text_blob
        Newline-joined source-visible raw table header text.
    table_row_text_blob
        Newline-joined source-visible raw table body-row text.
    window
        Source extraction window to inspect.

    Returns
    -------
    str
        Newline-joined source-visible text snippets for the window.
    """

    if window.table is None:
        parts = [window.source_text.strip()]

        if window.block:
            parts.append(str(window.block.get("combined_text") or "").strip())

        return "\n".join(p for p in parts if p)

    return "\n".join(
        text for text in (table_header_text_blob, table_row_text_blob) if text.strip()
    )


def _build_statement_type_alias_map(kg_config: CreateKGConfig) -> dict[str, str]:
    """Build canonical statement-type lookup from runtime config policy.

    Parameters
    ----------
    kg_config
        Runtime KG configuration containing statement-type policy items.

    Returns
    -------
    dict[str, str]
        Mapping from normalized canonical labels and aliases to canonical labels.
    """

    alias_to_canonical: dict[str, str] = {}

    for item in kg_config.academic_standards.statement_type_policy:
        for label in [item.statement_type, *item.aliases]:
            key = _normalize_statement_type_key(label)

            if key:
                alias_to_canonical[key] = item.statement_type

    return alias_to_canonical


def _build_table_header_visible_text_blob(window: ExtractionWindow) -> str:
    """Build normalized source-visible text from raw table header rows.

    Parameters
    ----------
    window
        Source extraction window to inspect.

    Returns
    -------
    str
        Newline-joined normalized source-visible raw table-header text snippets.
    """

    if window.table is None:
        return ""

    return "\n".join(_build_table_header_visible_text_by_index(window).values())


def _build_table_header_visible_text_by_index(
    window: ExtractionWindow,
) -> dict[int, str]:
    """Build normalized source-visible table-header text by header index.

    Parameters
    ----------
    window
        Source extraction window to inspect.

    Returns
    -------
    dict[int, str]
        Normalized raw table-header text keyed by source header-row index.
    """

    if window.table is None:
        return {}

    text_by_index: dict[int, str] = {}

    for header_index, row in enumerate(window.table.header_rows):
        texts: list[str] = []
        _append_row_cell_texts(row=row, texts=texts)
        text_by_index[header_index] = _normalize_text("\n".join(texts))

    return text_by_index


def _build_table_row_visible_text_blob(window: ExtractionWindow) -> str:
    """Build normalized source-visible text from raw selected table body rows.

    Parameters
    ----------
    window
        Source extraction window to inspect.

    Returns
    -------
    str
        Newline-joined normalized source-visible raw table body-row text snippets.
    """

    if window.table is None:
        return ""

    return "\n".join(_build_table_row_visible_text_by_index(window).values())


def _build_table_row_visible_text_by_index(window: ExtractionWindow) -> dict[int, str]:
    """Build normalized source-visible table-body text by source row index.

    Parameters
    ----------
    window
        Source extraction window to inspect.

    Returns
    -------
    dict[int, str]
        Normalized raw table-body-row text keyed by source body-row index.
    """

    if window.table is None:
        return {}

    text_by_index: dict[int, str] = {}

    for row_index, row in zip(window.table.row_indexes, window.table.rows):
        texts: list[str] = []
        _append_row_cell_texts(row=row, texts=texts)
        text_by_index[row_index] = _normalize_text("\n".join(texts))

    return text_by_index


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
        True when the candidate has code-local, canonical-scope, same-table,
        source-scope, or direct code-prefix evidence.
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


def _is_ordered_token_subsequence(
    *, source_text_normalized: str, target_text_normalized: str
) -> bool:
    """Check whether target tokens appear in source order.

    This supports table descriptions assembled from multiple visible cells or rows
    without requiring the description to be one contiguous source quote. It still
    rejects description words that are absent from the visible source text or appear
    only in an incompatible order.

    Parameters
    ----------
    source_text_normalized
        Normalized source-visible text used as the support text.
    target_text_normalized
        Normalized candidate text that must be supported by the source text.

    Returns
    -------
    bool
        True when every target token appears in order in the source tokens.
    """

    source_tokens = source_text_normalized.split()
    target_tokens = target_text_normalized.split()

    if not target_tokens:
        return False

    source_index = 0

    for target_token in target_tokens:
        while (
            source_index < len(source_tokens)
            and source_tokens[source_index] != target_token
        ):
            source_index += 1

        if source_index == len(source_tokens):
            return False

        source_index += 1

    return True


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
    """Build a stable comparison key for statement-type labels and aliases.

    Parameters
    ----------
    value
        Statement-type label or alias.

    Returns
    -------
    str
        Casefolded key with non-alphanumeric runs collapsed to one space.
    """

    return re.sub(r"[^0-9a-z]+", " ", str(value or "").casefold()).strip()


def _normalize_text(value: str) -> str:
    """Normalize source text for containment checks.

    Parameters
    ----------
    value
        Raw text.

    Returns
    -------
    str
        Lowercased text with collapsed whitespace.
    """

    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _normalized_source_contains_visible_excerpt(
    *, source_text_normalized: str, target_text_normalized: str
) -> bool:
    """Check whether normalized source text contains a visible source excerpt.

    This is intentionally stricter than `_normalized_source_supports_text`. Candidate
    `source_text` is an evidence quote, so it must be present in the source-visible
    support text after whitespace normalization. It must not pass merely because its
    tokens appear as a non-contiguous ordered subsequence.

    Parameters
    ----------
    source_text_normalized
        Normalized source-visible text used as support.
    target_text_normalized
        Normalized candidate source_text that must be a visible excerpt.

    Returns
    -------
    bool
        True when the normalized target text is directly contained in the normalized
        source text, otherwise False.
    """

    return (
        bool(target_text_normalized)
        and target_text_normalized in source_text_normalized
    )


def _normalized_source_supports_text(
    *, source_text_normalized: str, target_text_normalized: str
) -> bool:
    """Check whether normalized source text supports normalized target text.

    Direct containment handles ordinary verbatim excerpts. Ordered-token subsequence
    support handles table text assembled from multiple visible cells, rows, or header
    and body sources without allowing absent or reordered words.

    Parameters
    ----------
    source_text_normalized
        Normalized source-visible text used as support.
    target_text_normalized
        Normalized candidate text that must be supported.

    Returns
    -------
    bool
        True when the target is directly contained in the source or appears as an
        ordered token subsequence of the source.
    """

    if not target_text_normalized:
        return False

    if target_text_normalized in source_text_normalized:
        return True

    return _is_ordered_token_subsequence(
        source_text_normalized=source_text_normalized,
        target_text_normalized=target_text_normalized,
    )


def _source_text_contains_statement_code(
    *, source_text_normalized: str, statement_code_normalized: str
) -> bool:
    """Check whether source text contains an exact statement-code occurrence.

    The check uses normalized text while rejecting obvious embedded-code matches. For
    example, a parent code such as `1.2` must not pass only because a child code such
    as `1.2.3` is visible in the same source text.

    Parameters
    ----------
    source_text_normalized
        Normalized source-visible text to search.
    statement_code_normalized
        Normalized statement code to locate.

    Returns
    -------
    bool
        True when the statement code appears as its own code-like token in the source
        text, otherwise False.
    """

    if not source_text_normalized or not statement_code_normalized:
        return False

    code_boundary_chars = r"0-9a-z._/-"
    pattern = (
        rf"(?<![{code_boundary_chars}])"
        rf"{re.escape(statement_code_normalized)}"
        rf"(?![{code_boundary_chars}])"
    )
    return re.search(pattern, source_text_normalized) is not None


def _validate_candidate_code_is_visible(
    *, candidate: SFICandidate, ctx: SFIExtractionQualityCtx
) -> None:
    """Validate that a candidate statement code is visible in its source evidence.

    Block-window candidates may use any visible text in the block window as statement
    code evidence. Table-window candidates must use only the raw table header/body rows
    cited by that candidate. This keeps statement_code aligned with the same candidate-
    scoped evidence used to validate table source_text and description.

    Parameters
    ----------
    candidate
        Candidate to validate.
    ctx
        Quality-check context.

    Raises
    ------
    QualityError
        If the candidate has a statement code not visible in its allowed source
        evidence.
    """

    if candidate.statement_code is None:
        return

    code_normalized = _normalize_text(candidate.statement_code)

    if not code_normalized:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has an empty statement_code. "
            f"Use null if no official code is visible."
        )

    if ctx.window.table is None:
        support_label = "the visible source window"
        support_text_normalized = ctx.source_visible_text_normalized
    else:
        if not candidate.table_header_indexes and not candidate.table_row_indexes:
            raise QualityError(
                f"Table-window candidate {candidate.candidate_id!r} must include at "
                f"least one table_header_index or table_row_index before its "
                f"statement_code can be source-validated."
            )

        support_label = "the cited table header/body rows"
        support_text_normalized = _build_candidate_cited_table_support_text(
            candidate=candidate, ctx=ctx
        )

    if _source_text_contains_statement_code(
        source_text_normalized=support_text_normalized,
        statement_code_normalized=code_normalized,
    ):
        return

    raise QualityError(
        f"Candidate {candidate.candidate_id!r} has statement_code "
        f"{candidate.statement_code!r}, but that code is not visible in "
        f"{support_label}. Use null if no official code is visible in the "
        f"candidate's source evidence, and do not copy a code from another row, "
        f"header, or source location."
    )


def _validate_candidate_description_is_source_supported(
    *, candidate: SFICandidate, ctx: SFIExtractionQualityCtx
) -> None:
    """Validate that candidate description is supported by cited visible source text.

    Block candidate descriptions may be supported by any visible text in the block
    window. Table candidate descriptions must be supported only by the raw table header
    rows and body rows cited on that candidate. This keeps table provenance and
    description text aligned, while still allowing descriptions assembled from multiple
    visible cells or adjacent cited rows.

    Parameters
    ----------
    candidate
        Candidate whose description should be source-supported.
    ctx
        Quality-check context.

    Raises
    ------
    QualityError
        If the candidate description is empty, not supported by visible source text, or
        not supported by the candidate's cited table rows/header rows.
    """

    description_normalized = _normalize_text(candidate.description)

    if not description_normalized:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has empty description."
        )

    if ctx.window.table is None:
        support_label = "the visible source window"
        support_text_normalized = ctx.source_visible_text_normalized
    else:
        if not candidate.table_header_indexes and not candidate.table_row_indexes:
            raise QualityError(
                f"Table-window candidate {candidate.candidate_id!r} must include at "
                f"least one table_header_index or table_row_index before its "
                f"description can be source-validated."
            )

        support_label = "the cited table header/body rows"
        support_text_normalized = _build_candidate_cited_table_support_text(
            candidate=candidate, ctx=ctx
        )

    if _normalized_source_supports_text(
        source_text_normalized=support_text_normalized,
        target_text_normalized=description_normalized,
    ):
        return

    raise QualityError(
        f"Candidate {candidate.candidate_id!r} has description that is not "
        f"source-supported by {support_label}. Use only visible source-language "
        f"wording from the cited block/table text and do not add inferred parent "
        f"context, translations, paraphrases, normalized spellings, or hidden "
        f"context."
    )


def _validate_candidate_source_text_is_visible(
    *, candidate: SFICandidate, ctx: SFIExtractionQualityCtx
) -> None:
    """Validate that candidate source text is source-visible for SFI extraction.

    Block candidates must quote visible block text. Table candidates must be supported
    by their cited raw table header rows and/or body rows, which allows source quotes
    assembled from both header and row text when both locations are cited.

    Parameters
    ----------
    candidate
        Candidate to validate.
    ctx
        Quality-check context.

    Raises
    ------
    QualityError
        If source text is empty or not recoverable from cited source-visible text.
    """

    if ctx.window.table is None:
        _validate_source_text_is_visible(
            ctx=ctx,
            entity_label=f"Candidate {candidate.candidate_id!r}",
            source_text=candidate.source_text,
        )
        return

    source_text_normalized = _normalize_text(candidate.source_text)

    if not source_text_normalized:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has empty source_text."
        )

    if not candidate.table_header_indexes and not candidate.table_row_indexes:
        raise QualityError(
            f"Table-window candidate {candidate.candidate_id!r} must include at "
            f"least one table_header_index or table_row_index before its source_text "
            f"can be source-validated."
        )

    support_text_normalized = _build_candidate_cited_table_support_text(
        candidate=candidate, ctx=ctx
    )

    if _normalized_source_contains_visible_excerpt(
        source_text_normalized=support_text_normalized,
        target_text_normalized=source_text_normalized,
    ):
        return

    raise QualityError(
        f"Candidate {candidate.candidate_id!r} source_text is not supported by its "
        f"cited raw table header/body rows. Quote source-visible text from the "
        f"cited table rows/header rows instead of paraphrasing, using constructed "
        f"table source_text, or using helper-only context."
    )


def _validate_candidate_statement_type_policy(
    *, candidate: SFICandidate, ctx: SFIExtractionQualityCtx
) -> None:
    """Validate candidate statement_type against runtime policy.

    Parameters
    ----------
    candidate
        Candidate to validate.
    ctx
        Quality-check context.

    Raises
    ------
    QualityError
        If the candidate uses an unknown alias, non-canonical label, or mismatched
        normalized_statement_type.
    """

    statement_type_key = _normalize_statement_type_key(candidate.statement_type)
    canonical_statement_type = ctx.statement_type_alias_to_canonical.get(
        statement_type_key
    )
    allowed_statement_types = sorted(ctx.statement_type_normalized_by_label)

    if canonical_statement_type is None:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has unsupported statement_type "
            f"{candidate.statement_type!r}. Use one of the configured canonical "
            f"statement types: {allowed_statement_types}."
        )

    if candidate.statement_type != canonical_statement_type:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} uses statement_type "
            f"{candidate.statement_type!r}, which is an alias or non-canonical "
            f"label. Use canonical statement_type {canonical_statement_type!r}."
        )

    expected_normalized_statement_type = ctx.statement_type_normalized_by_label[
        canonical_statement_type
    ]

    if candidate.normalized_statement_type != expected_normalized_statement_type:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has statement_type "
            f"{candidate.statement_type!r}, which must use "
            f"normalized_statement_type {expected_normalized_statement_type!r}; "
            f"got {candidate.normalized_statement_type!r}."
        )


def _validate_candidate_table_indexes(ctx: SFIExtractionQualityCtx) -> None:
    """Validate table header/body indexes against the window table payload.

    Parameters
    ----------
    ctx
        Quality-check context.

    Raises
    ------
    QualityError
        If a candidate uses indexes outside the table window, omits all table indexes
        for a table-derived candidate, or uses table indexes in a block window.
    """

    if ctx.window.table is None:
        invalid_block_candidates = [
            candidate.candidate_id
            for candidate in ctx.extraction_result.sfi_candidates
            if candidate.table_header_indexes or candidate.table_row_indexes
        ]

        if invalid_block_candidates:
            raise QualityError(
                f"Block-window candidates must not include table_header_indexes or "
                f"table_row_indexes: {invalid_block_candidates}"
            )

        return

    allowed_header_indexes = set(range(len(ctx.window.table.header_rows)))
    allowed_row_indexes = set(ctx.window.table.row_indexes)

    for candidate in ctx.extraction_result.sfi_candidates:
        if not candidate.table_header_indexes and not candidate.table_row_indexes:
            raise QualityError(
                f"Table-window candidate {candidate.candidate_id!r} must include at "
                f"least one table_header_index or table_row_index from this window. "
                f"Allowed header indexes are {sorted(allowed_header_indexes)}; "
                f"allowed row indexes are {sorted(allowed_row_indexes)}."
            )

        invalid_header_indexes = sorted(
            set(candidate.table_header_indexes) - allowed_header_indexes
        )
        invalid_row_indexes = sorted(
            set(candidate.table_row_indexes) - allowed_row_indexes
        )

        if invalid_header_indexes:
            raise QualityError(
                f"Candidate {candidate.candidate_id!r} references "
                f"table_header_indexes outside this window: {invalid_header_indexes}. "
                f"Allowed header indexes are {sorted(allowed_header_indexes)}."
            )

        if invalid_row_indexes:
            raise QualityError(
                f"Candidate {candidate.candidate_id!r} references table_row_indexes "
                f"outside this window: {invalid_row_indexes}. Allowed row indexes are "
                f"{sorted(allowed_row_indexes)}."
            )

        _validate_table_candidate_source_location(candidate=candidate, ctx=ctx)


def _validate_combined_source_location(
    *,
    candidate: SFICandidate,
    cited_header_text_normalized: str,
    cited_row_text_normalized: str,
    source_supported_by_cited_headers: bool,
    source_supported_by_cited_rows: bool,
    source_text_normalized: str,
) -> None:
    """Validate a candidate that cites both header and row indexes.

    Combined citation is only legitimate when neither channel alone supports the
    complete quote but their combined cited text does.

    Parameters
    ----------
    candidate
        Candidate to validate.
    cited_header_text_normalized
        Normalized text blob built from the cited header indexes.
    cited_row_text_normalized
        Normalized text blob built from the cited row indexes.
    source_supported_by_cited_headers
        Whether the cited header rows alone support the source_text.
    source_supported_by_cited_rows
        Whether the cited body rows alone support the source_text.
    source_text_normalized
        Normalized candidate source_text.

    Raises
    ------
    QualityError
        If the combined cited text does not support the source_text, or if either
        channel alone supports it (so the other channel should not be populated).
    """

    cited_table_text_normalized = _normalize_text(
        "\n".join([cited_header_text_normalized, cited_row_text_normalized])
    )
    source_supported_by_cited_table_text = _normalized_source_contains_visible_excerpt(
        source_text_normalized=cited_table_text_normalized,
        target_text_normalized=source_text_normalized,
    )

    if not source_supported_by_cited_table_text:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} includes "
            f"table_header_indexes={candidate.table_header_indexes!r} and "
            f"table_row_indexes={candidate.table_row_indexes!r}, but its "
            f"source_text is not supported by those cited raw table header/body rows."
        )

    if source_supported_by_cited_headers and not source_supported_by_cited_rows:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} source_text is supported by its "
            f"cited table header rows alone, so table_row_indexes should not be "
            f"populated."
        )

    if source_supported_by_cited_rows and not source_supported_by_cited_headers:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} source_text is supported by its "
            f"cited table body rows alone, so table_header_indexes should not be "
            f"populated."
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


def _validate_header_only_source_location(
    *, candidate: SFICandidate, source_supported_by_cited_headers: bool
) -> None:
    """Validate a candidate that cites only table header indexes.

    Parameters
    ----------
    candidate
        Candidate to validate.
    source_supported_by_cited_headers
        Whether the cited header rows alone support the candidate source_text.

    Raises
    ------
    QualityError
        If the cited header rows do not support the source_text.
    """

    if source_supported_by_cited_headers:
        return

    raise QualityError(
        f"Candidate {candidate.candidate_id!r} includes "
        f"table_header_indexes={candidate.table_header_indexes!r}, but its "
        f"source_text is not supported by those specific raw table header rows."
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


def _validate_row_only_source_location(
    *, candidate: SFICandidate, source_supported_by_cited_rows: bool
) -> None:
    """Validate a candidate that cites only table row indexes.

    Parameters
    ----------
    candidate
        Candidate to validate.
    source_supported_by_cited_rows
        Whether the cited body rows alone support the candidate source_text.

    Raises
    ------
    QualityError
        If the cited body rows do not support the source_text.
    """

    if source_supported_by_cited_rows:
        return

    raise QualityError(
        f"Candidate {candidate.candidate_id!r} includes "
        f"table_row_indexes={candidate.table_row_indexes!r}, but its "
        f"source_text is not supported by those specific raw table body rows."
    )


def _validate_source_text_is_visible(
    *, ctx: SFIExtractionQualityCtx, entity_label: str, source_text: str
) -> None:
    """Validate that text is a non-empty source-visible excerpt.

    Parameters
    ----------
    ctx
        Quality-check context.
    entity_label
        Human-readable label for the candidate or auxiliary record being validated.
    source_text
        Source text claimed by the LLM output.

    Raises
    ------
    QualityError
        If source text is empty or not visible in source-visible window text.
    """

    source_text_normalized = _normalize_text(source_text)

    if not source_text_normalized:
        raise QualityError(f"{entity_label} has empty source_text.")

    if source_text_normalized in ctx.source_visible_text_normalized:
        return

    raise QualityError(
        f"{entity_label} source_text is not visible in the source window after "
        f"whitespace normalization. Quote source-visible text from the extraction "
        f"window instead of paraphrasing, using constructed table source_text, or "
        f"using helper-only context."
    )


def _validate_table_candidate_source_location(
    *, candidate: SFICandidate, ctx: SFIExtractionQualityCtx
) -> None:
    """Validate that table candidate indexes match source-text location.

    The candidate source_text must be supported by the cited source-visible table
    location. Header-only source_text must cite header indexes only. Row-only
    source_text must cite row indexes only. Source_text assembled from visible header
    and body-row text may cite both channels when neither channel alone supports the
    complete quote but their combined cited text does.

    Parameters
    ----------
    candidate
        Candidate to validate.
    ctx
        Quality-check context.

    Raises
    ------
    QualityError
        If a candidate cites indexes that do not support its source_text, cites row
        indexes for header-only text, cites header indexes for row-only text, or omits
        the source location implied by its source_text.
    """

    if ctx.window.table is None:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} source-location validation "
            f"requires a table window."
        )

    source_text_normalized = _normalize_text(candidate.source_text)

    if not source_text_normalized:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has empty source_text."
        )

    if not candidate.table_header_indexes and not candidate.table_row_indexes:
        raise QualityError(
            f"Table-window candidate {candidate.candidate_id!r} must include at "
            f"least one table_header_index or table_row_index before its source_text "
            f"location can be source-validated."
        )

    cited_header_text_normalized = ""
    cited_row_text_normalized = ""

    if candidate.table_header_indexes:
        cited_header_text_normalized = _build_normalized_text_blob_for_indexes(
            indexes=candidate.table_header_indexes,
            ordered_indexes=list(range(len(ctx.window.table.header_rows))),
            text_by_index=ctx.table_header_text_normalized_by_index,
        )

    if candidate.table_row_indexes:
        cited_row_text_normalized = _build_normalized_text_blob_for_indexes(
            indexes=candidate.table_row_indexes,
            ordered_indexes=ctx.window.table.row_indexes,
            text_by_index=ctx.table_row_text_normalized_by_index,
        )

    source_supported_by_cited_headers = bool(
        candidate.table_header_indexes
    ) and _normalized_source_contains_visible_excerpt(
        source_text_normalized=cited_header_text_normalized,
        target_text_normalized=source_text_normalized,
    )
    source_supported_by_cited_rows = bool(
        candidate.table_row_indexes
    ) and _normalized_source_contains_visible_excerpt(
        source_text_normalized=cited_row_text_normalized,
        target_text_normalized=source_text_normalized,
    )

    if candidate.table_header_indexes and not candidate.table_row_indexes:
        _validate_header_only_source_location(
            candidate=candidate,
            source_supported_by_cited_headers=source_supported_by_cited_headers,
        )
        return

    if candidate.table_row_indexes and not candidate.table_header_indexes:
        _validate_row_only_source_location(
            candidate=candidate,
            source_supported_by_cited_rows=source_supported_by_cited_rows,
        )
        return

    _validate_combined_source_location(
        candidate=candidate,
        cited_header_text_normalized=cited_header_text_normalized,
        cited_row_text_normalized=cited_row_text_normalized,
        source_supported_by_cited_headers=source_supported_by_cited_headers,
        source_supported_by_cited_rows=source_supported_by_cited_rows,
        source_text_normalized=source_text_normalized,
    )


def _validate_window_identity(ctx: SFIExtractionQualityCtx) -> None:
    """Validate that the LLM copied the SFI extraction window identity correctly.

    Parameters
    ----------
    ctx
        Quality-check context.

    Raises
    ------
    QualityError
        If window identifiers do not match the source window.
    """

    result = ctx.extraction_result
    window = ctx.window

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


def verify_sfi_extraction_quality(
    *,
    extraction_result: SFIExtractionResult,
    kg_config: CreateKGConfig,
    window: ExtractionWindow,
) -> None:
    """Run SFI extraction quality checks on a structured LLM response.

    Parameters
    ----------
    extraction_result
        Parsed SFI extraction result.
    kg_config
        Runtime KG configuration containing statement-type policy.
    window
        Source extraction window passed to the LLM.

    Raises
    ------
    QualityError
        If any quality check fails.
    """

    table_header_text_blob = _build_table_header_visible_text_blob(window)
    table_header_text_normalized_by_index = _build_table_header_visible_text_by_index(
        window
    )
    table_row_text_blob = _build_table_row_visible_text_blob(window)
    table_row_text_normalized_by_index = _build_table_row_visible_text_by_index(window)
    ctx = SFIExtractionQualityCtx(
        extraction_result=extraction_result,
        source_visible_text_normalized=_normalize_text(
            _build_source_visible_text_blob(
                table_header_text_blob=table_header_text_blob,
                table_row_text_blob=table_row_text_blob,
                window=window,
            )
        ),
        statement_type_alias_to_canonical=_build_statement_type_alias_map(kg_config),
        statement_type_normalized_by_label={
            item.statement_type: item.normalized_statement_type
            for item in kg_config.academic_standards.statement_type_policy
        },
        table_header_text_normalized=_normalize_text(table_header_text_blob),
        table_header_text_normalized_by_index=table_header_text_normalized_by_index,
        table_row_text_normalized=_normalize_text(table_row_text_blob),
        table_row_text_normalized_by_index=table_row_text_normalized_by_index,
        window=window,
    )

    _validate_window_identity(ctx)
    _validate_candidate_table_indexes(ctx)

    for candidate in ctx.extraction_result.sfi_candidates:
        _validate_candidate_statement_type_policy(candidate=candidate, ctx=ctx)
        _validate_candidate_code_is_visible(candidate=candidate, ctx=ctx)
        _validate_candidate_description_is_source_supported(
            candidate=candidate, ctx=ctx
        )
        _validate_candidate_source_text_is_visible(candidate=candidate, ctx=ctx)

    for auxiliary_candidate in ctx.extraction_result.auxiliary_candidates:
        _validate_source_text_is_visible(
            ctx=ctx,
            entity_label=f"Auxiliary candidate {auxiliary_candidate.auxiliary_id!r}",
            source_text=auxiliary_candidate.source_text,
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
