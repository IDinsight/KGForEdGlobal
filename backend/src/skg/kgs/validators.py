"""This module contains functionalities related to validating LLM-produced knowledge
graph artifacts.

NB: The Pydantic schemas validate structure and field-level invariants. The validators
in this module enforce quality checks that require access to other inputs.
"""

# Standard Library
import re

from dataclasses import dataclass

# Package Library
from skg.kgs.schemas import (
    ExtractionWindow,
    SFIAuxiliaryCandidate,
    SFICandidate,
    SFICandidateParentReference,
    SFIExtractionResult,
)
from skg.page_ir_extraction.validators import QualityError


@dataclass(frozen=True)
class SFIExtractionQualityCtx:
    """Context for SFI extraction quality checks."""

    extraction_result: SFIExtractionResult
    window: ExtractionWindow
    window_source_text_normalized: str
    window_text_blob_normalized: str


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


def _validate_auxiliary_source_text_is_visible(
    *, auxiliary_candidate: SFIAuxiliaryCandidate, ctx: SFIExtractionQualityCtx
) -> None:
    """Validate that auxiliary source text is source-visible for SFI extraction.

    NB: The `<= 20` character validation is meant to catch LLM outputs that claim
    source evidence the window does not actually contain.

    Parameters
    ----------
    auxiliary_candidate
        Auxiliary candidate to validate.
    ctx
        Quality-check context.

    Raises
    ------
    QualityError
        If source text is not recoverable from the source window.
    """

    source_text_normalized = _normalize_text(auxiliary_candidate.source_text)

    if source_text_normalized in ctx.window_text_blob_normalized:
        return

    if len(source_text_normalized) <= 20:
        return

    raise QualityError(
        f"Auxiliary candidate {auxiliary_candidate.auxiliary_id!r} source_text is "
        f"not visible in the source window after whitespace normalization."
    )


def _validate_candidate_code_is_visible(
    *, candidate: SFICandidate, ctx: SFIExtractionQualityCtx
) -> None:
    """Validate that a candidate statement code is visible in the source window for SFI
    extraction.

    Parameters
    ----------
    candidate
        Candidate to validate.
    ctx
        Quality-check context.

    Raises
    ------
    QualityError
        If the candidate has a statement code not visible in the window.
    """

    if candidate.statement_code is None:
        return

    visible_codes = {
        code_match.value.strip()
        for code_match in ctx.window.code_matches
        if code_match.value.strip()
    }

    if (
        candidate.statement_code not in visible_codes
        and _normalize_text(candidate.statement_code)
        not in ctx.window_text_blob_normalized
    ):
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has statement_code "
            f"{candidate.statement_code!r}, but that code is not visible in the "
            f"source window. Use null if no official code is visible."
        )


def _validate_candidate_parent_references(ctx: SFIExtractionQualityCtx) -> None:
    """Validate that parent/context references are source-grounded hints only for SFI
    extraction.

    Parameters
    ----------
    ctx
        Quality-check context.

    Raises
    ------
    QualityError
        If any reference is not grounded in the source window.
    """

    for candidate in ctx.extraction_result.sfi_candidates:
        for reference in (
            candidate.parent_references + candidate.ancestor_context_references
        ):
            _validate_parent_reference_is_source_grounded(
                candidate_id=candidate.candidate_id, ctx=ctx, reference=reference
            )


def _validate_candidate_source_text_is_visible(
    *, candidate: SFICandidate, ctx: SFIExtractionQualityCtx
) -> None:
    """Validate that candidate source text is source-visible for SFI extraction.

    NB: The `<= 20` character validation is meant to catch LLM outputs that claim
    source evidence the window does not actually contain.

    Parameters
    ----------
    candidate
        Candidate to validate.
    ctx
        Quality-check context.

    Raises
    ------
    QualityError
        If source text is not recoverable from the source window.
    """

    source_text_normalized = _normalize_text(candidate.source_text)

    if not source_text_normalized:
        raise QualityError(
            f"Candidate {candidate.candidate_id!r} has empty source_text."
        )

    if source_text_normalized in ctx.window_text_blob_normalized:
        return

    if len(source_text_normalized) <= 20:
        return

    raise QualityError(
        f"Candidate {candidate.candidate_id!r} source_text is not visible in the "
        f"window source payload after whitespace normalization. Quote the source text "
        f"verbatim from the extraction window instead of paraphrasing."
    )


def _validate_candidate_table_row_indexes(ctx: SFIExtractionQualityCtx) -> None:
    """Validate table row indexes against the window table payload for SFI extraction.

    Parameters
    ----------
    ctx
        Quality-check context.

    Raises
    ------
    QualityError
        If a candidate uses row indexes outside the table window.
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
    """Validate that a parent/context reference is source-grounded for SFI extraction.

    NB: The `<= 20` character validation is meant to catch LLM outputs that claim
    source evidence the window does not actually contain.

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
        If the reference is not grounded in the source window.
    """

    reference_source_text_normalized = _normalize_text(reference.source_text)

    if reference_source_text_normalized in ctx.window_text_blob_normalized:
        return

    if len(reference_source_text_normalized) <= 20:
        return

    raise QualityError(
        f"Candidate {candidate_id!r} emitted parent/context reference "
        f"{reference.source_text!r}, but that text is not visible in the source "
        f"window. Omit references that are not source-visible."
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
        window=window,
        window_source_text_normalized=_normalize_text(window.source_text),
        window_text_blob_normalized=_normalize_text(
            "\n".join([window.source_text, window.model_dump_json()])
        ),
    )

    _validate_window_identity(ctx)

    for candidate in ctx.extraction_result.sfi_candidates:
        _validate_candidate_code_is_visible(candidate=candidate, ctx=ctx)
        _validate_candidate_source_text_is_visible(candidate=candidate, ctx=ctx)

    _validate_candidate_table_row_indexes(ctx)
    _validate_candidate_parent_references(ctx)

    for auxiliary_candidate in ctx.extraction_result.auxiliary_candidates:
        _validate_auxiliary_source_text_is_visible(
            auxiliary_candidate=auxiliary_candidate, ctx=ctx
        )
