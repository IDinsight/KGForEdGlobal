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
from skg.kgs.schemas import ExtractionWindow, SFICandidate, SFIExtractionResult
from skg.page_ir_extraction.validators import QualityError
from skg.schemas import CreateKGConfig


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
    table_row_text_normalized
        Normalized raw table-body-row text for the source window. Empty for block
        windows.
    window
        Source extraction window passed to the LLM.
    """

    extraction_result: SFIExtractionResult
    source_visible_text_normalized: str
    statement_type_alias_to_canonical: dict[str, str]
    statement_type_normalized_by_label: dict[str, str]
    table_header_text_normalized: str
    table_row_text_normalized: str
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

    for item in kg_config.as_statement_type_policy:
        for label in [item.statement_type, *item.aliases]:
            key = _normalize_statement_type_key(label)

            if key:
                alias_to_canonical[key] = item.statement_type

    return alias_to_canonical


def _build_table_header_visible_text_blob(window: ExtractionWindow) -> str:
    """Build source-visible text from raw table header rows.

    Parameters
    ----------
    window
        Source extraction window to inspect.

    Returns
    -------
    str
        Newline-joined source-visible raw table header text snippets.
    """

    if window.table is None:
        return ""

    texts: list[str] = []

    for row in window.table.header_rows:
        _append_row_cell_texts(row=row, texts=texts)

    return "\n".join(texts)


def _build_table_row_visible_text_blob(window: ExtractionWindow) -> str:
    """Build source-visible text from raw selected table body rows.

    Parameters
    ----------
    window
        Source extraction window to inspect.

    Returns
    -------
    str
        Newline-joined source-visible raw table body-row text snippets.
    """

    if window.table is None:
        return ""

    texts: list[str] = []

    for row in window.table.rows:
        _append_row_cell_texts(row=row, texts=texts)

    return "\n".join(texts)


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


def _validate_candidate_code_is_visible(
    *, candidate: SFICandidate, ctx: SFIExtractionQualityCtx
) -> None:
    """Validate that a candidate statement code is visible in source text.

    Parameters
    ----------
    candidate
        Candidate to validate.
    ctx
        Quality-check context.

    Raises
    ------
    QualityError
        If the candidate has a statement code not visible in the source window.
    """

    if candidate.statement_code is None:
        return

    code_normalized = _normalize_text(candidate.statement_code)
    visible_codes_normalized = {
        _normalize_text(code_match.value)
        for code_match in ctx.window.code_matches
        if code_match.value.strip()
    }

    if (
        code_normalized not in visible_codes_normalized
        and code_normalized not in ctx.source_visible_text_normalized
    ):
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has statement_code "
            f"{candidate.statement_code!r}, but that code is not visible in the "
            f"source window. Use null if no official code is visible."
        )


def _validate_candidate_source_text_is_visible(
    *, candidate: SFICandidate, ctx: SFIExtractionQualityCtx
) -> None:
    """Validate that candidate source text is source-visible for SFI extraction.

    Parameters
    ----------
    candidate
        Candidate to validate.
    ctx
        Quality-check context.

    Raises
    ------
    QualityError
        If source text is not recoverable from source-visible window text.
    """

    _validate_source_text_is_visible(
        ctx=ctx,
        entity_label=f"Candidate {candidate.candidate_id!r}",
        source_text=candidate.source_text,
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
    """Validate that table candidate indexes match the source-text location.

    Parameters
    ----------
    candidate
        Candidate to validate.
    ctx
        Quality-check context.

    Raises
    ------
    QualityError
        If a candidate cites header indexes for row-only text, cites row indexes for
        header-only text, or omits the source location implied by its source_text.
    """

    source_text_normalized = _normalize_text(candidate.source_text)
    visible_in_headers = source_text_normalized in ctx.table_header_text_normalized
    visible_in_rows = source_text_normalized in ctx.table_row_text_normalized

    if candidate.table_header_indexes and not visible_in_headers:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} includes table_header_indexes, "
            f"but its source_text is not visible in raw table header text."
        )

    if candidate.table_row_indexes and not visible_in_rows:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} includes table_row_indexes, but "
            f"its source_text is not visible in raw table body-row text."
        )

    if (
        visible_in_headers
        and not visible_in_rows
        and not candidate.table_header_indexes
    ):
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} source_text is table-header text, "
            f"so table_header_indexes must be populated."
        )

    if visible_in_rows and not visible_in_headers and not candidate.table_row_indexes:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} source_text is table-row text, so "
            f"table_row_indexes must be populated."
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
    table_row_text_blob = _build_table_row_visible_text_blob(window)
    ctx = SFIExtractionQualityCtx(
        extraction_result=extraction_result,
        statement_type_alias_to_canonical=_build_statement_type_alias_map(kg_config),
        statement_type_normalized_by_label={
            item.statement_type: item.normalized_statement_type
            for item in kg_config.as_statement_type_policy
        },
        source_visible_text_normalized=_normalize_text(
            _build_source_visible_text_blob(
                table_header_text_blob=table_header_text_blob,
                table_row_text_blob=table_row_text_blob,
                window=window,
            )
        ),
        table_header_text_normalized=_normalize_text(table_header_text_blob),
        table_row_text_normalized=_normalize_text(table_row_text_blob),
        window=window,
    )

    _validate_window_identity(ctx)

    for candidate in ctx.extraction_result.sfi_candidates:
        _validate_candidate_statement_type_policy(candidate=candidate, ctx=ctx)
        _validate_candidate_code_is_visible(candidate=candidate, ctx=ctx)
        _validate_candidate_source_text_is_visible(candidate=candidate, ctx=ctx)

    _validate_candidate_table_indexes(ctx)

    for auxiliary_candidate in ctx.extraction_result.auxiliary_candidates:
        _validate_source_text_is_visible(
            ctx=ctx,
            entity_label=f"Auxiliary candidate {auxiliary_candidate.auxiliary_id!r}",
            source_text=auxiliary_candidate.source_text,
        )
