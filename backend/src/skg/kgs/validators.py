"""This module contains functionalities related to validating LLM-produced knowledge
graph artifacts.

NB: The Pydantic schemas validate structure and field-level invariants. The validators
in this module enforce quality checks that require access to other inputs.
"""

# Standard Library
import re

from dataclasses import dataclass
from typing import Any, Optional

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
from skg.kgs.utils import text_starts_with_complete_marker
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


@dataclass(frozen=True)
class SourceTextSupport:
    """One source-visible evidence span available to an extracted candidate.

    Attributes
    ----------
    description_complete
        Whether the span may establish a complete candidate description. Aggregate
        list/window supports remain useful for auxiliary evidence visibility but do not
        establish complete SFI descriptions.
    languages
        Source language tags contributing to the span.
    table_header_indexes
        Exact raw table header-row indexes contributing to the span.
    table_row_indexes
        Exact raw table body-row indexes contributing to the span.
    text_normalized
        Whitespace-normalized, casefolded source-visible text for the span.
    """

    description_complete: bool
    languages: frozenset[str]
    table_header_indexes: frozenset[int]
    table_row_indexes: frozenset[int]
    text_normalized: str


@dataclass(frozen=True)
class SFIExtractionQualityCtx:
    """Context for cross-input SFI extraction quality checks.

    Attributes
    ----------
    code_patterns_by_type
        Configured source-code regex patterns keyed by code type.
    extraction_result
        Parsed SFI extraction result produced for the window.
    statement_type_alias_to_canonical
        Mapping from normalized canonical labels and aliases to canonical labels.
    statement_type_code_type_by_label
        Optional configured code type for each canonical statement type.
    statement_type_normalized_by_label
        Mapping from canonical statement-type labels to configured normalized types.
    window
        Source extraction window passed to the LLM.
    """

    code_patterns_by_type: dict[str, str]
    extraction_result: SFIExtractionResult
    statement_type_alias_to_canonical: dict[str, str]
    statement_type_code_type_by_label: dict[str, Optional[str]]
    statement_type_normalized_by_label: dict[str, str]
    window: ExtractionWindow


def _append_source_text_support(
    *,
    description_complete: bool = True,
    languages: set[str],
    supports: list[SourceTextSupport],
    table_header_indexes: Optional[set[int]] = None,
    table_row_indexes: Optional[set[int]] = None,
    text: str,
) -> None:
    """Append one non-empty normalized source evidence span.

    Parameters
    ----------
    description_complete
        Whether this span may establish a complete candidate description.
    languages
        Source language tags contributing to `text`.
    supports
        Mutable support accumulator.
    table_header_indexes
        Exact raw table header-row indexes contributing to the span.
    table_row_indexes
        Exact raw table body-row indexes contributing to the span.
    text
        Source-visible text to normalize and append.
    """

    text_normalized = _normalize_text(text)

    if not text_normalized:
        return

    support = SourceTextSupport(
        description_complete=description_complete,
        languages=frozenset(language for language in languages if language),
        table_header_indexes=frozenset(table_header_indexes or set()),
        table_row_indexes=frozenset(table_row_indexes or set()),
        text_normalized=text_normalized,
    )

    if support not in supports:
        supports.append(support)


def _append_source_text_unit_supports(
    *,
    languages: set[str],
    supports: list[SourceTextSupport],
    table_header_indexes: Optional[set[int]] = None,
    table_row_indexes: Optional[set[int]] = None,
    text: str,
) -> None:
    """Append a complete source unit and its visible line-level units.

    Line-level spans preserve legitimate complete statements separated by source line
    breaks. Sentence and clause boundaries within each span are evaluated by
    `_source_support_contains_complete_text`; arbitrary interior substrings are not
    treated as complete descriptions.

    Parameters
    ----------
    languages
        Source language tags contributing to the source unit.
    supports
        Mutable support accumulator.
    table_header_indexes
        Exact raw table header-row indexes contributing to the unit.
    table_row_indexes
        Exact raw table body-row indexes contributing to the unit.
    text
        Source-visible unit text.
    """

    text_clean = str(text or "").strip()

    if not text_clean:
        return

    _append_source_text_support(
        languages=languages,
        supports=supports,
        table_header_indexes=table_header_indexes,
        table_row_indexes=table_row_indexes,
        text=text_clean,
    )

    for line in text_clean.splitlines():
        line_clean = line.strip()

        if line_clean:
            _append_source_text_support(
                languages=languages,
                supports=supports,
                table_header_indexes=table_header_indexes,
                table_row_indexes=table_row_indexes,
                text=line_clean,
            )


def _build_block_payload_source_text_supports(
    *, fallback_language: str, payload: dict[str, Any]
) -> list[SourceTextSupport]:
    """Build source supports from one serialized block or block-slice payload.

    Parameters
    ----------
    fallback_language
        Language used when the payload lacks source-language metadata.
    payload
        Serialized block segment or block-slice payload.

    Returns
    -------
    list[SourceTextSupport]
        Source-visible text supports available from the payload.
    """

    supports: list[SourceTextSupport] = []
    text_unit = payload.get("text")

    if isinstance(text_unit, dict):
        text = str(text_unit.get("text") or "").strip()

        if text:
            language = _get_text_unit_language(
                fallback=fallback_language, text_unit=text_unit
            )
            _append_source_text_unit_supports(
                languages={language}, supports=supports, text=text
            )

    figure = payload.get("figure")

    if isinstance(figure, dict):
        for field_name in ["embedded_text", "caption"]:
            figure_text = figure.get(field_name)

            if isinstance(figure_text, dict):
                text = str(figure_text.get("text") or "").strip()

                if text:
                    language = _get_text_unit_language(
                        fallback=fallback_language, text_unit=figure_text
                    )
                    _append_source_text_unit_supports(
                        languages={language}, supports=supports, text=text
                    )
                    break
            elif isinstance(figure_text, str) and figure_text.strip():
                _append_source_text_unit_supports(
                    languages={fallback_language}, supports=supports, text=figure_text
                )
                break

    return supports


def _build_block_source_text_supports(
    window: ExtractionWindow,
) -> list[SourceTextSupport]:
    """Build block source supports at list-item and slice granularity.

    Parameters
    ----------
    window
        Source extraction window containing a block payload.

    Returns
    -------
    list[SourceTextSupport]
        Source-visible supports for exact wording and language validation.
    """

    block = window.block

    if block is None:
        return []

    supports: list[SourceTextSupport] = []
    list_items = block.get("list_items")
    is_list_block = isinstance(list_items, list) and bool(list_items)

    if is_list_block:
        for item in list_items:
            if not isinstance(item, dict):
                continue

            source_text = _build_list_item_source_text(item)

            if not source_text:
                continue

            language = _get_text_unit_language(
                fallback=window.primary_language, text_unit=item.get("text")
            )
            _append_source_text_unit_supports(
                languages={language}, supports=supports, text=source_text
            )

            item_text_unit = item.get("text")
            item_text = (
                str(item_text_unit.get("text") or "").strip()
                if isinstance(item_text_unit, dict)
                else str(item_text_unit or "").strip()
            )

            if item_text and _normalize_text(item_text) != _normalize_text(source_text):
                _append_source_text_unit_supports(
                    languages={language}, supports=supports, text=item_text
                )
    else:
        slices = block.get("slices")

        if isinstance(slices, list):
            for slice_payload in slices:
                if not isinstance(slice_payload, dict):
                    continue

                supports.extend(
                    _build_block_payload_source_text_supports(
                        fallback_language=window.primary_language, payload=slice_payload
                    )
                )

        if not supports:
            supports.extend(
                _build_block_payload_source_text_supports(
                    fallback_language=window.primary_language, payload=block
                )
            )

    combined_languages = {
        language for support in supports for language in support.languages
    } or {window.primary_language}
    _append_source_text_support(
        description_complete=not is_list_block,
        languages=combined_languages,
        supports=supports,
        text=window.source_text,
    )
    return supports


def _build_candidate_source_text_supports(
    *, candidate: SFICandidate, ctx: SFIExtractionQualityCtx
) -> list[SourceTextSupport]:
    """Build source supports allowed for one candidate.

    Parameters
    ----------
    candidate
        Candidate whose cited source indexes constrain table support.
    ctx
        Extraction quality context.

    Returns
    -------
    list[SourceTextSupport]
        Candidate-scoped source-visible supports.
    """

    if ctx.window.table is None:
        return _build_block_source_text_supports(ctx.window)

    return _build_table_source_text_supports(candidate=candidate, window=ctx.window)


def _build_description_support_targets(
    *, description: str, statement_code: Optional[str]
) -> list[str]:
    """Build complete-description targets with optional visible code prefixes.

    Parameters
    ----------
    description
        Candidate description text.
    statement_code
        Optional separately captured source-visible code.

    Returns
    -------
    list[str]
        Normalized description targets. Code-prefixed variants allow a complete
        statement to be validated when its source cell begins with the same code but
        the candidate stores that code separately in `statement_code`.
    """

    targets = [_normalize_text(description)]

    if statement_code is None:
        return targets

    code = str(statement_code).strip().rstrip(" .:;-)–—")

    if not code:
        return targets

    for separator in [" ", ". ", ": ", ") ", " - ", " – ", " — "]:
        target = _normalize_text(f"{code}{separator}{description}")

        if target not in targets:
            targets.append(target)

    return targets


def _build_list_item_source_text(item: dict[str, Any]) -> str:
    """Render one serialized list item with its visible marker.

    Parameters
    ----------
    item
        Serialized DocumentIR list-item payload.

    Returns
    -------
    str
        Source-visible list-item text with a non-duplicated marker.
    """

    marker = str(item.get("marker") or "").strip()
    text_unit = item.get("text")

    if isinstance(text_unit, dict):
        text = str(text_unit.get("text") or "").strip()
    elif text_unit is not None:
        text = str(text_unit).strip()
    else:
        text = ""

    if marker and text and text_starts_with_complete_marker(marker=marker, text=text):
        return text

    return " ".join(part for part in [marker, text] if part)


def _build_row_source_text_supports(
    *,
    primary_language: str,
    row: dict[str, Any],
    table_header_indexes: set[int],
    table_row_indexes: set[int],
) -> list[SourceTextSupport]:
    """Build evidence spans for every contiguous visible cell range in one row.

    Parameters
    ----------
    primary_language
        Fallback language when a cell lacks language metadata.
    row
        Serialized raw table row.
    table_header_indexes
        Exact raw table header-row indexes contributing to this row.
    table_row_indexes
        Exact raw table body-row indexes contributing to this row.

    Returns
    -------
    list[SourceTextSupport]
        Spans for every contiguous range of text-bearing cells. Each span carries only
        the languages used by that range and the exact contributing table location.
    """

    visible_cells: list[tuple[str, str]] = []

    for cell in row.get("cells") or []:
        text_unit = cell.get("text") or {}
        text = str(text_unit.get("text") or "").strip()

        if not text:
            continue

        visible_cells.append(
            (
                text,
                _get_text_unit_language(fallback=primary_language, text_unit=text_unit),
            )
        )

    supports: list[SourceTextSupport] = []

    for start_index in range(len(visible_cells)):
        range_languages: set[str] = set()
        range_texts: list[str] = []

        for end_index in range(start_index, len(visible_cells)):
            text, language = visible_cells[end_index]
            range_languages.add(language)
            range_texts.append(text)
            if start_index == end_index:
                _append_source_text_unit_supports(
                    languages=range_languages.copy(),
                    supports=supports,
                    table_header_indexes=table_header_indexes,
                    table_row_indexes=table_row_indexes,
                    text="\n".join(range_texts),
                )
            else:
                _append_source_text_support(
                    languages=range_languages.copy(),
                    supports=supports,
                    table_header_indexes=table_header_indexes,
                    table_row_indexes=table_row_indexes,
                    text="\n".join(range_texts),
                )

    return supports


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


def _build_table_channel_source_text_supports(
    *,
    indexes: list[int],
    is_header: bool,
    ordered_indexes: list[int],
    primary_language: str,
    rows_by_index: dict[int, dict[str, Any]],
) -> list[SourceTextSupport]:
    """Build supports for one selected table channel.

    A channel is either raw header rows or raw body rows. Supports include individual
    cells, complete rows, complete text from each adjacent selected-row run, and
    same-column text across adjacent selected rows. This permits genuine continuation
    statements without allowing descriptions to skip intervening rows or words.

    Parameters
    ----------
    indexes
        Selected source indexes cited by the candidate.
    is_header
        Whether the selected indexes identify raw table header rows rather than body
        rows.
    ordered_indexes
        Complete source-order indexes available in the window channel.
    primary_language
        Fallback language when a cell lacks language metadata.
    rows_by_index
        Raw table rows keyed by source index.

    Returns
    -------
    list[SourceTextSupport]
        Source-visible supports for the selected channel.
    """

    selected_indexes_set = set(indexes)
    selected_indexes = [
        index for index in ordered_indexes if index in selected_indexes_set
    ]
    supports: list[SourceTextSupport] = []

    for index in selected_indexes:
        supports.extend(
            _build_row_source_text_supports(
                primary_language=primary_language,
                row=rows_by_index[index],
                table_header_indexes={index} if is_header else set(),
                table_row_indexes=set() if is_header else {index},
            )
        )

    for contiguous_indexes in _group_contiguous_ordered_indexes(
        indexes=selected_indexes, ordered_indexes=ordered_indexes
    ):
        run_texts: list[str] = []
        run_languages: set[str] = set()
        max_columns = max(
            len(rows_by_index[index].get("cells") or []) for index in contiguous_indexes
        )

        for index in contiguous_indexes:
            for cell in rows_by_index[index].get("cells") or []:
                text_unit = cell.get("text") or {}
                text = str(text_unit.get("text") or "").strip()

                if not text:
                    continue

                run_texts.append(text)
                run_languages.add(
                    _get_text_unit_language(
                        fallback=primary_language, text_unit=text_unit
                    )
                )

        _append_source_text_support(
            languages=run_languages or {primary_language},
            supports=supports,
            table_header_indexes=set(contiguous_indexes) if is_header else set(),
            table_row_indexes=set() if is_header else set(contiguous_indexes),
            text="\n".join(run_texts),
        )

        if len(contiguous_indexes) < 2:
            continue

        for column_index in range(max_columns):
            column_texts: list[str] = []
            column_languages: set[str] = set()

            for index in contiguous_indexes:
                cells = rows_by_index[index].get("cells") or []

                if column_index >= len(cells):
                    continue

                text_unit = cells[column_index].get("text") or {}
                text = str(text_unit.get("text") or "").strip()

                if not text:
                    continue

                column_texts.append(text)
                column_languages.add(
                    _get_text_unit_language(
                        fallback=primary_language, text_unit=text_unit
                    )
                )

            if len(column_texts) > 1:
                _append_source_text_support(
                    languages=column_languages,
                    supports=supports,
                    table_header_indexes=(
                        set(contiguous_indexes) if is_header else set()
                    ),
                    table_row_indexes=(set() if is_header else set(contiguous_indexes)),
                    text="\n".join(column_texts),
                )

    return supports


def _build_table_source_text_supports(
    *, candidate: Optional[SFICandidate], window: ExtractionWindow
) -> list[SourceTextSupport]:
    """Build table source supports for a candidate or the full window.

    Parameters
    ----------
    candidate
        Candidate whose indexes constrain support, or `None` for all visible rows.
    window
        Table extraction window.

    Returns
    -------
    list[SourceTextSupport]
        Candidate-scoped or full-window source-visible supports.

    Raises
    ------
    QualityError
        If the function is called for a non-table window.
    """

    table = window.table

    if table is None:
        raise QualityError("Table source support requires a table window.")

    header_rows_by_index = dict(enumerate(table.header_rows))
    body_rows_by_index = dict(zip(table.row_indexes, table.rows))
    header_indexes = (
        candidate.table_header_indexes
        if candidate is not None
        else list(header_rows_by_index)
    )
    body_indexes = (
        candidate.table_row_indexes
        if candidate is not None
        else list(body_rows_by_index)
    )
    supports: list[SourceTextSupport] = []

    if header_indexes:
        supports.extend(
            _build_table_channel_source_text_supports(
                indexes=header_indexes,
                is_header=True,
                ordered_indexes=list(header_rows_by_index),
                primary_language=window.primary_language,
                rows_by_index=header_rows_by_index,
            )
        )

    if body_indexes:
        supports.extend(
            _build_table_channel_source_text_supports(
                indexes=body_indexes,
                is_header=False,
                ordered_indexes=table.row_indexes,
                primary_language=window.primary_language,
                rows_by_index=body_rows_by_index,
            )
        )

    if header_indexes and body_indexes:
        header_groups = _group_contiguous_ordered_indexes(
            indexes=header_indexes, ordered_indexes=list(header_rows_by_index)
        )
        body_groups = _group_contiguous_ordered_indexes(
            indexes=body_indexes, ordered_indexes=table.row_indexes
        )

        for header_group in header_groups:
            for body_group in body_groups:
                combined_texts: list[str] = []
                combined_languages: set[str] = set()

                for row in [
                    *(header_rows_by_index[index] for index in header_group),
                    *(body_rows_by_index[index] for index in body_group),
                ]:
                    for cell in row.get("cells") or []:
                        text_unit = cell.get("text") or {}
                        text = str(text_unit.get("text") or "").strip()

                        if not text:
                            continue

                        combined_texts.append(text)
                        combined_languages.add(
                            _get_text_unit_language(
                                fallback=window.primary_language, text_unit=text_unit
                            )
                        )

                _append_source_text_support(
                    languages=combined_languages or {window.primary_language},
                    supports=supports,
                    table_header_indexes=set(header_group),
                    table_row_indexes=set(body_group),
                    text="\n".join(combined_texts),
                )

    return supports


def _build_window_source_text_supports(
    ctx: SFIExtractionQualityCtx,
) -> list[SourceTextSupport]:
    """Build all source-visible supports for one extraction window.

    Parameters
    ----------
    ctx
        Extraction quality context.

    Returns
    -------
    list[SourceTextSupport]
        Full-window supports for auxiliary source validation.
    """

    if ctx.window.table is None:
        return _build_block_source_text_supports(ctx.window)

    return _build_table_source_text_supports(candidate=None, window=ctx.window)


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


def _candidate_source_text_is_linked_to_description(
    candidate: SFICandidate,
) -> bool:
    """Check whether candidate evidence text directly quotes its description.

    A source quote may begin with the same separately captured statement code. In that
    case, the exact standalone leading code is removed before testing whether the
    remaining visible quote is a contiguous excerpt of the description.

    Parameters
    ----------
    candidate
        Candidate whose description and evidence quote should refer to the same source
        statement.

    Returns
    -------
    bool
        True when the normalized source quote, with an optional exact leading code
        removed, and the normalized description are contiguous excerpts of one another.
    """

    description_normalized = _normalize_text(candidate.description)
    source_text_normalized = _normalize_text(candidate.source_text)
    source_text_without_code = _remove_leading_statement_code(
        source_text=candidate.source_text, statement_code=candidate.statement_code
    )
    source_text_variants = {source_text_normalized, source_text_without_code}
    return bool(
        description_normalized
        and any(
            source_text_variant
            and (
                description_normalized in source_text_variant
                or source_text_variant in description_normalized
            )
            for source_text_variant in source_text_variants
        )
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


def _find_language_sets_for_text(
    *, supports: list[SourceTextSupport], target_text: str
) -> list[frozenset[str]]:
    """Find the most local language sets supporting source-visible text.

    Parameters
    ----------
    supports
        Candidate-scoped source supports.
    target_text
        Candidate description or evidence quote.

    Returns
    -------
    list[frozenset[str]]
        Language sets from the shortest matching supports.
    """

    target_text_normalized = _normalize_text(target_text)

    if not target_text_normalized:
        return []

    matches = [
        support
        for support in supports
        if target_text_normalized in support.text_normalized
    ]

    if not matches:
        return []

    shortest_length = min(len(support.text_normalized) for support in matches)
    shortest_matches = [
        support
        for support in matches
        if len(support.text_normalized) == shortest_length
    ]
    smallest_language_count = min(
        len(support.languages) for support in shortest_matches
    )
    language_sets: list[frozenset[str]] = []

    for support in shortest_matches:
        if len(support.languages) != smallest_language_count:
            continue

        if support.languages not in language_sets:
            language_sets.append(support.languages)

    return language_sets


def _find_redundant_candidate_table_locations(
    *, candidate: SFICandidate, ctx: SFIExtractionQualityCtx
) -> list[str]:
    """Find cited table locations that are unnecessary for candidate support.

    Each cited header/body row must contribute to the joint evidence span. A location
    is redundant when removing only that location still leaves a valid source span for
    the candidate's description, quote, optional code, language, and remaining
    citations.

    Parameters
    ----------
    candidate
        Table-derived candidate whose citations should be minimal.
    ctx
        Extraction quality context containing the table window.

    Returns
    -------
    list[str]
        Stable labels for individually removable table locations.
    """

    if ctx.window.table is None:
        return []

    redundant_locations: list[str] = []

    for field_name in ["table_header_indexes", "table_row_indexes"]:
        indexes = list(getattr(candidate, field_name))

        for index in indexes:
            reduced_header_indexes = list(candidate.table_header_indexes)
            reduced_row_indexes = list(candidate.table_row_indexes)

            if field_name == "table_header_indexes":
                reduced_header_indexes.remove(index)
            else:
                reduced_row_indexes.remove(index)

            if not reduced_header_indexes and not reduced_row_indexes:
                continue

            reduced_candidate = candidate.model_copy(
                update={
                    "table_header_indexes": reduced_header_indexes,
                    "table_row_indexes": reduced_row_indexes,
                }
            )
            reduced_supports = _build_candidate_source_text_supports(
                candidate=reduced_candidate, ctx=ctx
            )

            if any(
                _source_support_matches_candidate(
                    candidate=reduced_candidate, support=support, window=ctx.window
                )
                for support in reduced_supports
            ):
                redundant_locations.append(f"{field_name}={index}")

    return redundant_locations


def _get_text_unit_language(*, fallback: str, text_unit: Any) -> str:
    """Return a serialized TextUnit language or a fallback.

    Parameters
    ----------
    fallback
        Language to use when the text unit lacks a usable tag.
    text_unit
        Serialized TextUnit payload or another value.

    Returns
    -------
    str
        Source language tag when available, otherwise `fallback`.
    """

    if isinstance(text_unit, dict):
        language = text_unit.get("language")

        if isinstance(language, str) and language.strip():
            return language.strip()

    return fallback


def _get_window_local_code(window: ExtractionWindow) -> Optional[str]:
    """Return the source segment/table local code exposed by one window.

    Parameters
    ----------
    window
        Source extraction window.

    Returns
    -------
    Optional[str]
        Stripped block or table local code, or `None` when absent.
    """

    local_code: Any

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


def _group_contiguous_ordered_indexes(
    *, indexes: list[int], ordered_indexes: list[int]
) -> list[list[int]]:
    """Group selected indexes that are adjacent in source order.

    Parameters
    ----------
    indexes
        Selected indexes.
    ordered_indexes
        Complete source-order index sequence.

    Returns
    -------
    list[list[int]]
        Source-ordered contiguous selected-index runs.
    """

    selected_indexes = set(indexes)
    groups: list[list[int]] = []
    current_group: list[int] = []

    for index in ordered_indexes:
        if index in selected_indexes:
            current_group.append(index)
            continue

        if current_group:
            groups.append(current_group)
            current_group = []

    if current_group:
        groups.append(current_group)

    return groups


def _is_strong_source_boundary(*, boundary_index: int, source_text: str) -> bool:
    """Return whether source punctuation marks a strong text boundary.

    Periods embedded in decimal values, hierarchical codes, or dotted abbreviations are
    not treated as sentence boundaries. Other supported punctuation marks are always
    strong boundaries.

    Parameters
    ----------
    boundary_index
        Index of the punctuation character in `source_text`.
    source_text
        Normalized source text containing the candidate boundary.

    Returns
    -------
    bool
        True when the indexed punctuation is a strong boundary.

    Raises
    ------
    ValueError
        If `boundary_index` does not identify a supported punctuation character.
    """

    boundary_character = source_text[boundary_index]

    if boundary_character in "!?;:":
        return True

    if boundary_character != ".":
        raise ValueError(
            f"Unsupported source-boundary character: {boundary_character!r}."
        )

    previous_character = source_text[boundary_index - 1] if boundary_index > 0 else ""
    next_character = (
        source_text[boundary_index + 1] if boundary_index + 1 < len(source_text) else ""
    )

    if previous_character.isalnum() and next_character.isalnum():
        return False

    token_match = re.search(r"([0-9a-z.]+)\.$", source_text[: boundary_index + 1])

    if token_match is None:
        return True

    token_body = token_match.group(1)

    if "." in token_body:
        return False

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

    Candidate `source_text` is an evidence quote, so it must be present in the
    source-visible support text after whitespace normalization. It must not pass merely
    because its tokens appear as a non-contiguous ordered subsequence.

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


def _remove_leading_statement_code(
    *, source_text: str, statement_code: Optional[str]
) -> str:
    """Remove an exact standalone leading statement code from source text.

    The code is removed only when it appears at the beginning of the normalized source
    quote and is followed by a supported source-visible separator. This avoids treating
    a longer code, embedded code-like text, or a code occurrence in the statement body
    as a removable prefix.

    Parameters
    ----------
    source_text
        Candidate evidence quote that may begin with the separately captured code.
    statement_code
        Optional source-visible statement code stored on the candidate.

    Returns
    -------
    str
        Normalized source text with the leading code and separator removed when they
        form an exact standalone prefix; otherwise the unchanged normalized text.
    """

    source_text_normalized = _normalize_text(source_text)

    if statement_code is None:
        return source_text_normalized

    code = str(statement_code).strip().rstrip(" .:;-)–—")
    code_normalized = _normalize_text(code)

    if not code_normalized:
        return source_text_normalized

    separator_pattern = r"(?:\s*[-–—]\s*|[.:)]\s*|\s+)"
    prefix_match = re.match(
        rf"^{re.escape(code_normalized)}{separator_pattern}",
        source_text_normalized,
    )

    if prefix_match is None:
        return source_text_normalized

    return source_text_normalized[prefix_match.end() :].strip()


def _remove_trailing_statement_code(
    *, source_text: str, statement_code: Optional[str]
) -> str:
    """Remove an exact standalone trailing statement code from source text.

    The code is removed only when it appears at the end of the normalized source quote
    and is preceded by a supported source-visible separator.

    Parameters
    ----------
    source_text
        Candidate evidence quote that may end with the separately captured code.
    statement_code
        Optional source-visible statement code stored on the candidate.

    Returns
    -------
    str
        Normalized source text with the trailing code and separator removed when they
        form an exact standalone suffix; otherwise the unchanged normalized text.
    """

    source_text_normalized = _normalize_text(source_text)

    if statement_code is None:
        return source_text_normalized

    code = str(statement_code).strip().rstrip(" .:;-)–—")
    code_normalized = _normalize_text(code)

    if not code_normalized:
        return source_text_normalized

    separator_pattern = r"(?:\s*[-–—]\s*|[.:)]\s*|\s+)"
    suffix_match = re.search(
        rf"{separator_pattern}{re.escape(code_normalized)}$",
        source_text_normalized,
    )

    if suffix_match is None:
        return source_text_normalized

    return source_text_normalized[: suffix_match.start()].strip()


def _resolve_candidate_statement_code_type(
    *, candidate: SFICandidate, ctx: SFIExtractionQualityCtx
) -> str:
    """Resolve and validate the configured code type for one candidate code.

    The statement-type policy is authoritative when it declares `code_type`. When it
    does not, the candidate code must unambiguously match exactly one configured code
    pattern.

    Parameters
    ----------
    candidate
        Candidate containing a non-null statement code.
    ctx
        Extraction quality context containing code patterns and statement-type policy.

    Returns
    -------
    str
        Resolved configured code type.

    Raises
    ------
    QualityError
        If the code matches no configured pattern, conflicts with the statement-type
        policy, or is ambiguous across multiple configured patterns.
    """

    assert candidate.statement_code is not None
    matching_code_types = sorted(
        code_type
        for code_type, pattern in ctx.code_patterns_by_type.items()
        if re.fullmatch(pattern, candidate.statement_code) is not None
    )
    configured_code_type = ctx.statement_type_code_type_by_label.get(
        candidate.statement_type
    )

    if configured_code_type is not None:
        if configured_code_type not in matching_code_types:
            raise QualityError(
                f"Candidate {candidate.candidate_id!r} has statement_code "
                f"{candidate.statement_code!r}, which does not fully match the "
                f"configured code pattern {configured_code_type!r} for statement_type "
                f"{candidate.statement_type!r}."
            )

        return configured_code_type

    if not matching_code_types:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has statement_code "
            f"{candidate.statement_code!r}, but it does not fully match any configured "
            f"code pattern. Use null when no configured source code applies."
        )

    if len(matching_code_types) > 1:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has statement_code "
            f"{candidate.statement_code!r}, which ambiguously matches configured code "
            f"types {matching_code_types}. Configure statement_type_policy.code_type "
            f"for statement_type {candidate.statement_type!r} or return null."
        )

    return matching_code_types[0]


def _source_code_values_match(*, candidate_code: str, source_code: str) -> bool:
    """Check exact source-surface equality for two code values.

    Parameters
    ----------
    candidate_code
        Candidate code emitted by the LLM.
    source_code
        Code value present in the extraction window.

    Returns
    -------
    bool
        True when the stripped values are exactly equal, including case and punctuation.
    """

    return candidate_code.strip() == source_code.strip()


def _source_supports_contiguous_text(
    *, supports: list[SourceTextSupport], target_text_normalized: str
) -> bool:
    """Check whether source supports contain the target as contiguous wording.

    Parameters
    ----------
    supports
        Candidate-scoped cell, row, adjacent-column, or block supports.
    target_text_normalized
        Normalized candidate description.

    Returns
    -------
    bool
        True when a source support contains the complete target text contiguously.
    """

    return bool(target_text_normalized) and any(
        target_text_normalized in support.text_normalized for support in supports
    )


def _source_support_contains_complete_text(
    *, support: SourceTextSupport, target_text_normalized: str
) -> bool:
    """Check whether one evidence span supports a complete description target.

    Parameters
    ----------
    support
        Source evidence span to inspect.
    target_text_normalized
        Normalized candidate description target.

    Returns
    -------
    bool
        True when the target equals the complete span or begins and ends at strong
        source boundaries within it.
    """

    if not support.description_complete or not target_text_normalized:
        return False

    boundary_characters = frozenset(".!?;:")
    source_text = support.text_normalized
    search_start = 0

    while True:
        match_start = source_text.find(target_text_normalized, search_start)

        if match_start < 0:
            return False

        match_end = match_start + len(target_text_normalized)
        left_boundary_index = match_start - 1

        while left_boundary_index >= 0 and source_text[left_boundary_index].isspace():
            left_boundary_index -= 1

        right_boundary_index = match_end

        while (
            right_boundary_index < len(source_text)
            and source_text[right_boundary_index].isspace()
        ):
            right_boundary_index += 1

        left_bounded = left_boundary_index < 0 or (
            source_text[left_boundary_index] in boundary_characters
            and _is_strong_source_boundary(
                boundary_index=left_boundary_index, source_text=source_text
            )
        )
        target_boundary_index = len(target_text_normalized) - 1
        target_ends_at_boundary = target_text_normalized[
            target_boundary_index
        ] in boundary_characters and _is_strong_source_boundary(
            boundary_index=target_boundary_index,
            source_text=target_text_normalized,
        )
        right_bounded = (
            right_boundary_index >= len(source_text)
            or (
                source_text[right_boundary_index] in boundary_characters
                and _is_strong_source_boundary(
                    boundary_index=right_boundary_index, source_text=source_text
                )
            )
            or target_ends_at_boundary
        )

        if left_bounded and right_bounded:
            return True

        search_start = match_start + 1


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


def _source_support_has_candidate_language(
    *, candidate: SFICandidate, support: SourceTextSupport, window: ExtractionWindow
) -> bool:
    """Check candidate language against one joint source evidence span.

    Parameters
    ----------
    candidate
        Candidate whose language tag should match the evidence span.
    support
        Joint source evidence span supporting the candidate.
    window
        Extraction window providing the fallback primary language.

    Returns
    -------
    bool
        True when the candidate language equals the span language or `mul` for a
        multilingual span.
    """

    languages = support.languages or frozenset({window.primary_language})
    expected_language = next(iter(languages)) if len(languages) == 1 else "mul"
    return candidate.language == expected_language


def _source_support_has_candidate_locations(
    *, candidate: SFICandidate, support: SourceTextSupport, window: ExtractionWindow
) -> bool:
    """Check that one evidence span exactly matches candidate table citations.

    Parameters
    ----------
    candidate
        Candidate whose table citations should identify the evidence span.
    support
        Joint source evidence span supporting the candidate.
    window
        Extraction window containing either a block or table payload.

    Returns
    -------
    bool
        True for block windows, or when table header/body indexes exactly equal the
        span's contributing locations.
    """

    if window.table is None:
        return not support.table_header_indexes and not support.table_row_indexes

    return support.table_header_indexes == frozenset(
        candidate.table_header_indexes
    ) and support.table_row_indexes == frozenset(candidate.table_row_indexes)


def _source_support_matches_candidate(
    *, candidate: SFICandidate, support: SourceTextSupport, window: ExtractionWindow
) -> bool:
    """Check whether one source span jointly supports all candidate evidence fields.

    Parameters
    ----------
    candidate
        Candidate to validate.
    support
        Source-visible evidence span to inspect.
    window
        Source extraction window containing the span.

    Returns
    -------
    bool
        True when the same span supports the complete description, evidence quote,
        language, and exact table citations. Candidate code evidence is validated
        separately because a block/table local_code may be metadata rather than text.
    """

    description_targets = _build_description_support_targets(
        description=candidate.description, statement_code=candidate.statement_code
    )

    if not any(
        _source_support_contains_complete_text(
            support=support, target_text_normalized=description_target
        )
        for description_target in description_targets
    ):
        return False

    source_text_normalized = _normalize_text(candidate.source_text)

    if not _normalized_source_contains_visible_excerpt(
        source_text_normalized=support.text_normalized,
        target_text_normalized=source_text_normalized,
    ):
        return False

    return _source_support_has_candidate_language(
        candidate=candidate, support=support, window=window
    ) and _source_support_has_candidate_locations(
        candidate=candidate, support=support, window=window
    )


def _statement_code_is_directly_linked_to_description(candidate: SFICandidate) -> bool:
    """Check that a text-derived code is directly paired with its description.

    A valid coded quote either contains the code as part of the complete description,
    or consists only of the complete description plus the same standalone leading or
    trailing code. This rejects codes borrowed from another statement in a broader
    block or cited table range.

    Parameters
    ----------
    candidate
        Candidate whose code, description, and source quote should form one source item.

    Returns
    -------
    bool
        True when the source quote directly pairs the code and complete description.
    """

    if candidate.statement_code is None:
        return True

    description_normalized = _normalize_text(candidate.description)
    source_text_normalized = _normalize_text(candidate.source_text)
    statement_code_normalized = _normalize_text(candidate.statement_code)

    if not description_normalized or not source_text_normalized:
        return False

    if (
        source_text_normalized == description_normalized
        and _source_text_contains_statement_code(
            source_text_normalized=description_normalized,
            statement_code_normalized=statement_code_normalized,
        )
    ):
        return True

    source_text_without_leading_code = _remove_leading_statement_code(
        source_text=candidate.source_text,
        statement_code=candidate.statement_code,
    )
    source_text_without_trailing_code = _remove_trailing_statement_code(
        source_text=candidate.source_text,
        statement_code=candidate.statement_code,
    )
    return description_normalized in {
        source_text_without_leading_code,
        source_text_without_trailing_code,
    }


def _validate_candidate_source_evidence(
    *, candidate: SFICandidate, ctx: SFIExtractionQualityCtx
) -> None:
    """Validate all source-grounded candidate fields against one evidence span.

    The same source-visible span must support the complete description, evidence quote,
    language, and exact table citations. Candidate code type and locality are validated
    separately so source-visible block/table local_code metadata is handled correctly.

    Parameters
    ----------
    candidate
        Candidate to validate.
    ctx
        Extraction quality context containing the source window.

    Raises
    ------
    QualityError
        If description and source text are unrelated, or no single source span jointly
        supports all candidate evidence fields.
    """

    if not _candidate_source_text_is_linked_to_description(candidate):
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has source_text that does not quote "
            f"its description. source_text must be a contiguous excerpt of description, "
            f"or contain the complete description with visible code/context."
        )

    supports = _build_candidate_source_text_supports(candidate=candidate, ctx=ctx)
    matching_supports = [
        support
        for support in supports
        if _source_support_matches_candidate(
            candidate=candidate, support=support, window=ctx.window
        )
    ]

    if matching_supports:
        redundant_locations = _find_redundant_candidate_table_locations(
            candidate=candidate, ctx=ctx
        )

        if redundant_locations:
            raise QualityError(
                f"Candidate {candidate.candidate_id!r} cites table locations that do "
                f"not contribute to its joint source evidence: "
                f"{redundant_locations}. Cite only the exact raw rows needed for the "
                f"description, source_text, optional statement_code, and language."
            )

        return

    location_guidance = (
        " For table candidates, table_header_indexes and table_row_indexes must equal "
        "the exact raw rows contributing to that same evidence span; do not include "
        "unrelated context rows."
        if ctx.window.table is not None
        else ""
    )
    raise QualityError(
        f"Candidate {candidate.candidate_id!r} is not jointly supported by one "
        f"source-visible evidence span. The same span must support its complete "
        f"description, source_text, language, and source locations. "
        f"statement_code is validated separately against local_code or typed code "
        f"matches.{location_guidance}"
    )


def _validate_candidate_statement_code(
    *, candidate: SFICandidate, ctx: SFIExtractionQualityCtx
) -> None:
    """Validate candidate code type, source identity, and statement association.

    Segment/table `local_code` is accepted as first-class metadata evidence when it
    exactly equals the candidate code and matches the resolved configured code type.
    Otherwise, the code must exactly equal a typed `window.code_matches` value, occur
    in candidate `source_text`, and be directly paired with the complete candidate
    description.

    Parameters
    ----------
    candidate
        Candidate to validate.
    ctx
        Extraction quality context containing the source window and code policy.

    Raises
    ------
    QualityError
        If the candidate code is untyped, unsupported, not source-visible, or
        associated with a different statement.
    """

    if candidate.statement_code is None:
        return

    code_type = _resolve_candidate_statement_code_type(candidate=candidate, ctx=ctx)
    local_code = _get_window_local_code(ctx.window)

    if local_code is not None and _source_code_values_match(
        candidate_code=candidate.statement_code, source_code=local_code
    ):
        return

    matching_code_matches = [
        code_match
        for code_match in ctx.window.code_matches
        if code_match.code_type == code_type
        and _source_code_values_match(
            candidate_code=candidate.statement_code,
            source_code=code_match.value,
        )
    ]

    if not matching_code_matches:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has statement_code "
            f"{candidate.statement_code!r}, but it is neither the exact window "
            f"local_code nor an exact {code_type!r} code_match value. Copy the exact "
            f"source code or use null."
        )

    if not _source_text_contains_statement_code(
        source_text_normalized=_normalize_text(candidate.source_text),
        statement_code_normalized=_normalize_text(candidate.statement_code),
    ):
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} uses text-derived statement_code "
            f"{candidate.statement_code!r}, but candidate source_text does not contain "
            f"that exact standalone code. Quote the code together with the complete "
            f"statement description, or use null."
        )

    if not _statement_code_is_directly_linked_to_description(candidate):
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} uses text-derived statement_code "
            f"{candidate.statement_code!r}, but source_text does not directly pair that "
            f"code with the complete candidate description. Do not borrow a code from "
            f"another statement in the block or cited table rows."
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


def _validate_source_language(
    *,
    description: Optional[str],
    entity_label: str,
    language: str,
    source_text: str,
    supports: list[SourceTextSupport],
) -> None:
    """Validate output language against source units supporting the text.

    Parameters
    ----------
    description
        Candidate description, or `None` for auxiliary material.
    entity_label
        Human-readable entity label for errors.
    language
        Output language tag to validate.
    source_text
        Source-visible evidence quote.
    supports
        Candidate-scoped source text and language supports.

    Raises
    ------
    QualityError
        If the output language is incompatible with all source-supported language
        combinations for the description and source quote.
    """

    source_language_sets = _find_language_sets_for_text(
        supports=supports, target_text=source_text
    )

    if not source_language_sets:
        return

    description_language_sets = (
        _find_language_sets_for_text(supports=supports, target_text=description)
        if description is not None
        else [frozenset()]
    )

    if description is not None and not description_language_sets:
        return

    allowed_languages = {
        next(iter(combined)) if len(combined) == 1 else "mul"
        for source_languages in source_language_sets
        for description_languages in description_language_sets
        for combined in (source_languages | description_languages,)
    }

    if language in allowed_languages:
        return

    raise QualityError(
        f"{entity_label} has language {language!r}, but its source-supported text "
        f"requires one of {sorted(allowed_languages)!r}. Use the source TextUnit/cell "
        f"language, or 'mul' when the candidate combines multiple source languages."
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

    supports = _build_window_source_text_supports(ctx)

    if _source_supports_contiguous_text(
        supports=supports, target_text_normalized=source_text_normalized
    ):
        return

    raise QualityError(
        f"{entity_label} source_text is not a contiguous source-visible excerpt in "
        f"the source window after whitespace normalization. Quote visible block/table "
        f"text instead of paraphrasing, skipping intervening source units, using "
        f"constructed table source_text, source-context headings, or helper-only "
        f"context."
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

    ctx = SFIExtractionQualityCtx(
        code_patterns_by_type=dict(kg_config.academic_standards.code_patterns),
        extraction_result=extraction_result,
        statement_type_alias_to_canonical=_build_statement_type_alias_map(kg_config),
        statement_type_code_type_by_label={
            item.statement_type: item.code_type
            for item in kg_config.academic_standards.statement_type_policy
        },
        statement_type_normalized_by_label={
            item.statement_type: item.normalized_statement_type
            for item in kg_config.academic_standards.statement_type_policy
        },
        window=window,
    )

    _validate_window_identity(ctx)
    _validate_candidate_table_indexes(ctx)

    for candidate in ctx.extraction_result.sfi_candidates:
        _validate_candidate_statement_type_policy(candidate=candidate, ctx=ctx)
        _validate_candidate_statement_code(candidate=candidate, ctx=ctx)
        _validate_candidate_source_evidence(candidate=candidate, ctx=ctx)

    window_supports = _build_window_source_text_supports(ctx)

    for auxiliary_candidate in ctx.extraction_result.auxiliary_candidates:
        _validate_source_text_is_visible(
            ctx=ctx,
            entity_label=f"Auxiliary candidate {auxiliary_candidate.auxiliary_id!r}",
            source_text=auxiliary_candidate.source_text,
        )
        _validate_source_language(
            description=None,
            entity_label=f"Auxiliary candidate {auxiliary_candidate.auxiliary_id!r}",
            language=auxiliary_candidate.language,
            source_text=auxiliary_candidate.source_text,
            supports=window_supports,
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
