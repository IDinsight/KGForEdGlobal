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
    SFICandidateParentReference,
    SFIExtractionResult,
)
from skg.page_ir_extraction.validators import QualityError


@dataclass(frozen=True)
class SFIExtractionQualityCtx:
    """Context for SFI extraction quality checks.

    Attributes
    ----------
    extraction_result
        Parsed SFI extraction result produced for the window.
    source_visible_text_normalized
        Normalized text built only from source-visible extraction-window text, raw
        table headers, and raw table rows. Deterministic hints, KG config text, and
        helper-only filldown context are intentionally excluded.
    window
        Source extraction window passed to the LLM.
    """

    extraction_result: SFIExtractionResult
    source_visible_text_normalized: str
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


def _build_source_visible_text_blob(window: ExtractionWindow) -> str:
    """Build a source-visible text blob for extraction quality checks.

    The blob intentionally excludes deterministic hints, KG config instructions,
    code-parent hints, and filldown/helper views. It should contain only text that can
    be quoted as source evidence: ``window.source_text``, raw block text, canonical
    table headers, raw table header rows, and raw selected table rows.

    Parameters
    ----------
    window
        Source extraction window to inspect.

    Returns
    -------
    str
        Newline-joined source-visible text snippets.
    """

    texts: list[str] = []

    if window.source_text.strip():
        texts.append(window.source_text.strip())

    if window.block is not None:
        block_source_text = str(window.block.get("combined_text") or "").strip()

        if block_source_text:
            texts.append(block_source_text)

    if window.table is not None:
        for header_row in window.table.header_rows_canonical:
            for header_label in header_row:
                header_label_clean = str(header_label or "").strip()

                if header_label_clean:
                    texts.append(header_label_clean)

        for row in window.table.header_rows:
            _append_row_cell_texts(row=row, texts=texts)

        for row in window.table.rows:
            _append_row_cell_texts(row=row, texts=texts)

    return "\n".join(texts)


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


def _validate_candidate_parent_references(ctx: SFIExtractionQualityCtx) -> None:
    """Validate that parent/context references are source-grounded hints only.

    Parameters
    ----------
    ctx
        Quality-check context.

    Raises
    ------
    QualityError
        If any reference is not grounded in source-visible window text.
    """

    for candidate in ctx.extraction_result.sfi_candidates:
        for reference in (
            candidate.parent_references + candidate.ancestor_context_references
        ):
            _validate_parent_reference_is_source_grounded(
                candidate_id=candidate.candidate_id,
                ctx=ctx,
                reference=reference,
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


def _validate_candidate_table_row_indexes(ctx: SFIExtractionQualityCtx) -> None:
    """Validate table row indexes against the window table payload.

    Parameters
    ----------
    ctx
        Quality-check context.

    Raises
    ------
    QualityError
        If a candidate uses row indexes outside the table window, omits row indexes for
        a table-derived candidate, or uses row indexes in a block window.
    """

    if ctx.window.table is None:
        invalid_block_candidates = [
            candidate.candidate_id
            for candidate in ctx.extraction_result.sfi_candidates
            if candidate.table_row_indexes
        ]

        if invalid_block_candidates:
            raise QualityError(
                f"Block-window candidates must not include table_row_indexes: "
                f"{invalid_block_candidates}"
            )

        return

    allowed_row_indexes = set(ctx.window.table.row_indexes)

    for candidate in ctx.extraction_result.sfi_candidates:
        if not candidate.table_row_indexes:
            raise QualityError(
                f"Table-window candidate {candidate.candidate_id!r} must include at "
                f"least one table_row_index from this window. Allowed row indexes are "
                f"{sorted(allowed_row_indexes)}."
            )

        invalid = sorted(set(candidate.table_row_indexes) - allowed_row_indexes)

        if invalid:
            raise QualityError(
                f"Candidate {candidate.candidate_id!r} references table_row_indexes "
                f"outside this window: {invalid}. Allowed row indexes are "
                f"{sorted(allowed_row_indexes)}."
            )


def _validate_parent_reference_is_source_grounded(
    *,
    candidate_id: str,
    ctx: SFIExtractionQualityCtx,
    reference: SFICandidateParentReference,
) -> None:
    """Validate that a parent/context reference is source-grounded.

    Parameters
    ----------
    candidate_id
        Candidate ID that emitted the reference.
    ctx
        Quality-check context.
    reference
        Parent/context reference to validate.

    Raises
    ------
    QualityError
        If the reference is not grounded in source-visible window text.
    """

    _validate_source_text_is_visible(
        ctx=ctx,
        entity_label=f"Candidate {candidate_id!r} parent/context reference",
        source_text=reference.source_text,
    )

    if reference.statement_code is None:
        return

    code_normalized = _normalize_text(reference.statement_code)

    if code_normalized not in ctx.source_visible_text_normalized:
        raise QualityError(
            f"Candidate {candidate_id!r} emitted parent/context reference code "
            f"{reference.statement_code!r}, but that code is not visible in the "
            f"source window. Omit reference codes that are not source-visible."
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
        f"window instead of paraphrasing or using helper-only context."
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
    *, extraction_result: SFIExtractionResult, window: ExtractionWindow
) -> None:
    """Run SFI extraction quality checks on a structured LLM response.

    Parameters
    ----------
    extraction_result
        Parsed SFI extraction result.
    window
        Source extraction window passed to the LLM.

    Raises
    ------
    QualityError
        If any quality check fails.
    """

    ctx = SFIExtractionQualityCtx(
        extraction_result=extraction_result,
        source_visible_text_normalized=_normalize_text(
            _build_source_visible_text_blob(window)
        ),
        window=window,
    )

    _validate_window_identity(ctx)

    for candidate in ctx.extraction_result.sfi_candidates:
        _validate_candidate_code_is_visible(candidate=candidate, ctx=ctx)
        _validate_candidate_source_text_is_visible(candidate=candidate, ctx=ctx)

    _validate_candidate_table_row_indexes(ctx)
    _validate_candidate_parent_references(ctx)

    for auxiliary_candidate in ctx.extraction_result.auxiliary_candidates:
        _validate_source_text_is_visible(
            ctx=ctx,
            entity_label=f"Auxiliary candidate {auxiliary_candidate.auxiliary_id!r}",
            source_text=auxiliary_candidate.source_text,
        )
