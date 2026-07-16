"""This module contains prompt templates for the knowledge graph pipeline."""

# Standard Library
from textwrap import dedent
from typing import Any, Optional

# Package Library
from skg.kgs.schemas import (
    ExtractionWindow,
    ExtractionWindowTablePayload,
    SFIDedupReviewRequest,
    SFIDedupReviewResponse,
    SFIExtractionResult,
    SFIHasChildResolutionRequest,
    SFISourceUnitKind,
)
from skg.kgs.sfi_source_anchors import (
    build_sfi_source_unit_id,
    build_sfi_source_units,
    map_raw_row_cells_to_column_ranges,
)
from skg.schemas import CreateKGConfig
from skg.utils.general import PromptPair, json_dumps


def _build_compact_block_payload(
    extraction_window: ExtractionWindow,
) -> Optional[dict[str, Any]]:
    """Build a compact prompt-facing block payload for SFI extraction.

    Parameters
    ----------
    extraction_window
        Source-faithful extraction window to compact for the prompt.

    Returns
    -------
    Optional[dict[str, Any]]
        A compact block payload for block windows, or `None` for table windows.
    """

    if extraction_window.block is None:
        return None

    source_units = _build_compact_block_source_units(extraction_window)
    source_languages = sorted(
        {
            str(source_unit.get("language") or "").strip()
            for source_unit in source_units
            if str(source_unit.get("language") or "").strip()
        }
    )
    block_language = (
        source_languages[0]
        if len(source_languages) == 1
        else "mul" if len(source_languages) > 1 else extraction_window.primary_language
    )
    return {
        "block_type": extraction_window.block.get("block_type"),
        "language": block_language,
        "local_code": extraction_window.block.get("local_code"),
        "source_text": extraction_window.source_text,
        "source_units": source_units,
    }


def _build_compact_block_source_units(
    extraction_window: ExtractionWindow,
) -> list[dict[str, Any]]:
    """Build exact prompt-facing source units for one block window.

    Parameters
    ----------
    extraction_window
        Source-faithful block extraction window.

    Returns
    -------
    list[dict[str, Any]]
        Source-visible block units with stable IDs and structural locators.
    """

    return [
        source_unit.to_prompt_payload()
        for source_unit in build_sfi_source_units(extraction_window)
    ]


def _build_compact_dedup_review_payload(
    review_request: SFIDedupReviewRequest,
) -> dict[str, Any]:
    """Build a compact prompt payload for one SFI dedup review request.

    Parameters
    ----------
    review_request
        Bounded dedup review request to compact for the LLM prompt.

    Returns
    -------
    dict[str, Any]
        JSON-serializable dedup review payload with shared context windows and
        candidate-subset review signals.
    """

    payload: dict[str, Any] = {
        "candidates": [
            candidate.model_dump(exclude_none=True, mode="json")
            for candidate in review_request.candidates
        ],
        "context_windows": [
            context_window.model_dump(exclude_none=True, mode="json")
            for context_window in review_request.context_windows
        ],
        "review_set_id": review_request.review_set_id,
        "review_signals": [
            signal.model_dump(exclude_none=True, mode="json")
            for signal in review_request.review_signals
        ],
        "sfi_dedup_instructions": review_request.sfi_dedup_instructions,
    }

    if review_request.bilingual_pair_policy:
        payload["bilingual_pair_policy"] = review_request.bilingual_pair_policy

    return payload


def _build_compact_extraction_window_payload(
    extraction_window: ExtractionWindow,
) -> dict[str, Any]:
    """Build the compact prompt payload sent to the SFI extraction LLM.

    The persisted `ExtractionWindow` remains the complete source-faithful artifact.
    This prompt payload keeps only the fields the model needs to decide which candidate
    SFIs and auxiliary records are visible in one window.

    Parameters
    ----------
    extraction_window
        Source-faithful extraction window to compact for the prompt.

    Returns
    -------
    dict[str, Any]
        Compact JSON-serializable source payload for the extraction prompt.
    """

    payload: dict[str, Any] = {
        "code_matches": [
            code_match.model_dump(mode="json")
            for code_match in extraction_window.code_matches
        ],
        "segment_kind": extraction_window.segment_kind,
        "source_anchor_policy": (
            "Every source-visible block source_units entry and raw table cell has a "
            "stable source_unit_id. Candidate description_source_anchors and "
            "code_source_anchors must copy those IDs exactly. Each anchor source_text "
            "must be an exact non-empty excerpt of that source unit, and "
            "occurrence_index is the zero-based left-to-right non-overlapping "
            "occurrence of that exact excerpt within the complete source unit. "
            "Description anchors may be noncontiguous when runtime policy requires "
            "semantic composition from multiple visible fragments, such as a shared "
            "stem plus a later list item. Helper-only and context-only content cannot "
            "be anchored. The producer/checker selects source_text directly as bounded "
            "source-visible evidence; Python does not reconstruct or reorder semantic "
            "candidate fields."
        ),
        "source_context": {
            "following_same_page_headings": [
                context.model_dump(mode="json")
                for context in extraction_window.source_context_after
            ],
            "preceding_same_page_headings": [
                context.model_dump(mode="json")
                for context in extraction_window.source_context_before
            ],
            "scope_context_candidates": [
                candidate.model_dump(mode="json")
                for candidate in extraction_window.scope_context_candidates
            ],
            "section_path_recent_first": _build_recent_first_section_context(
                extraction_window
            ),
            "source_context_policy": (
                "The current visible block/table content is authoritative. "
                "Resolve each configured identity-scope dimension independently. "
                "scope_context_candidates contains deterministic controlled-value "
                "recognition from bounded neighbor headings and section-path context; "
                "it does not identify the governing value. context_origin and "
                "origin_rank preserve each candidate's evidence channel and "
                "nearest-first position within that channel. "
                "section_path_recent_first is ordered from nearest preceding context "
                "to farthest, and recency_rank=0 identifies the nearest entry. When a "
                "scope dimension is not explicit in the current source, apply clear "
                "authoritative runtime rules for bounded local neighbor context; "
                "otherwise use the nearest recognized candidate for that exact scope "
                "statement type. Do not skip a nearer candidate for an older one merely "
                "because the older value previously appeared with another repeated "
                "grouping label. A following heading does not automatically govern the "
                "target. All source_context content is context_only: it cannot create "
                "candidates or be cited in candidate anchors, descriptions, or "
                "source_text unless the same wording is also visible in the target "
                "block or table. These fields are fallible context, not an inferred KG "
                "ancestor chain."
            ),
        },
        "window_id": extraction_window.window_id,
        "window_index": extraction_window.window_index,
        "window_source_segment_ids": extraction_window.source_segment_ids,
    }

    if block_payload := _build_compact_block_payload(extraction_window):
        payload["block"] = block_payload

    if table_payload := _build_compact_table_payload(extraction_window):
        payload["table"] = table_payload

    return payload


def _build_compact_filldown_context_row_payload(
    *,
    filldown_row: dict[str, Any],
    raw_grid_row: dict[str, Any],
    row_index: int,
) -> Optional[dict[str, Any]]:
    """Build helper-only filldown context cells for one table row.

    The filldown and raw-grid rows are both rectangular and column-aligned. Only helper
    values that differ from the raw structural grid are retained, which keeps the
    prompt compact while exposing repeated grouping context when it is useful.

    Parameters
    ----------
    filldown_row
        Filldown helper-view row aligned to the raw structural grid row.
    raw_grid_row
        Span-expanded raw row used to distinguish added helper context from visible
        source content.
    row_index
        Source table row index for this row.

    Returns
    -------
    Optional[dict[str, Any]]
        Compact helper-context row payload, or `None` when filldown adds no context.
    """

    cells: list[dict[str, Any]] = []
    raw_grid_cells = raw_grid_row.get("cells") or []

    for column_index, cell in enumerate(filldown_row.get("cells") or []):
        text_unit = cell.get("text") or {}
        text = str(text_unit.get("text") or "").strip()

        if not text:
            continue

        raw_grid_cell = (
            raw_grid_cells[column_index] if column_index < len(raw_grid_cells) else {}
        )
        raw_text_unit = raw_grid_cell.get("text") or {}
        raw_text = str(raw_text_unit.get("text") or "").strip()

        if text == raw_text:
            continue

        cells.append(
            {
                "column_index": column_index,
                "language": text_unit.get("language"),
                "text": text,
            }
        )

    if not cells:
        return None

    return {
        "cells": cells,
        "row_index": row_index,
        "source_visibility": "helper_context_only",
    }


def _build_compact_header_rows(
    *, header_rows: list[dict[str, Any]], n_cols: int, source_segment_id: str
) -> list[dict[str, Any]]:
    """Build compact raw header rows with true grid column ranges.

    Row spans are tracked across declared header rows so every visible header cell is
    assigned to the columns it actually covers. The result remains sparse: merged cells
    are represented once with a column range rather than repeated across the expanded
    grid.

    Parameters
    ----------
    header_rows
        Raw source header rows in table order.
    n_cols
        Total table grid width.
    source_segment_id
        Stable DocumentIR table segment identifier.

    Returns
    -------
    list[dict[str, Any]]
        Compact source-visible header rows with exact cell placement.
    """

    active_rowspans = [0] * n_cols
    compact_rows: list[dict[str, Any]] = []

    for header_row_index, header_row in enumerate(header_rows):
        available_column_indexes = {
            column_index
            for column_index, remaining_rows in enumerate(active_rowspans)
            if remaining_rows == 0
        }
        mapped_cells = map_raw_row_cells_to_column_ranges(
            available_column_indexes=available_column_indexes,
            n_cols=n_cols,
            row=header_row,
        )
        cells: list[dict[str, Any]] = []
        next_active_rowspans = [
            max(0, remaining_rows - 1) for remaining_rows in active_rowspans
        ]

        for cell, column_start_index, column_end_index_exclusive in mapped_cells:
            cell_payload = _build_compact_source_cell_payload(
                cell=cell,
                column_end_index_exclusive=column_end_index_exclusive,
                column_start_index=column_start_index,
                row_index=header_row_index,
                source_segment_id=source_segment_id,
                source_unit_kind="table_header_cell",
            )

            if cell_payload is not None:
                cells.append(cell_payload)

            row_span = max(1, int(cell.get("row_span") or 1))

            if row_span > 1:
                for column_index in range(
                    column_start_index, column_end_index_exclusive
                ):
                    next_active_rowspans[column_index] = max(
                        next_active_rowspans[column_index], row_span - 1
                    )

        compact_rows.append(
            {
                "cells": cells,
                "header_row_index": header_row_index,
                "source_visibility": "source_visible_header",
            }
        )
        active_rowspans = next_active_rowspans

    return compact_rows


def _build_compact_kg_config_context(kg_config: CreateKGConfig) -> dict[str, Any]:
    """Build the compact KG config context for the extraction prompt.

    Parameters
    ----------
    kg_config
        Country/document-specific extraction configuration.

    Returns
    -------
    dict[str, Any]
        Compact KG config facts and extraction instructions needed by the LLM.
    """

    kg_config_context: dict[str, Any] = {
        "code_scope_statement_types": (
            kg_config.academic_standards.code_scope_statement_types
        ),
        "country": kg_config.metadata.country,
        "grades_or_stages": kg_config.metadata.grades_or_stages,
        "identity_scope_statement_types": (
            kg_config.academic_standards.identity_scope_statement_types
        ),
        "primary_language": kg_config.metadata.primary_language,
        "sfi_extraction_instructions": kg_config.academic_standards.sfi_extraction_instructions,
        "statement_type_policy": [
            item.model_dump(exclude_none=True, mode="json")
            for item in kg_config.academic_standards.statement_type_policy
        ],
        "subject": kg_config.metadata.subject,
    }

    if kg_config.academic_standards.bilingual_pair_policy:
        kg_config_context["bilingual_pair_policy"] = (
            kg_config.academic_standards.bilingual_pair_policy
        )

    return kg_config_context


def _build_compact_rowspan_context_row_payload(
    *, grid_row: dict[str, Any], grid_sources: list[dict[str, Any]], row_index: int
) -> Optional[dict[str, Any]]:
    """Build sparse helper context for cells inherited through row spans.

    Adjacent expanded-grid cells from the same source row with identical text are
    collapsed into one column range. These values explain table structure but remain
    explicitly non-quotable helper context.

    Parameters
    ----------
    grid_row
        Span-expanded row aligned to `grid_sources`.
    grid_sources
        Source-row origin metadata for each expanded grid column.
    row_index
        Current source table row index.

    Returns
    -------
    Optional[dict[str, Any]]
        Sparse inherited row-span context, or `None` when none is present.
    """

    grid_cells = grid_row.get("cells") or []
    cells: list[dict[str, Any]] = []
    column_index = 0

    while column_index < min(len(grid_cells), len(grid_sources)):
        source_row_index = grid_sources[column_index].get("source_row")
        text_unit = grid_cells[column_index].get("text") or {}
        text = str(text_unit.get("text") or "").strip()

        if source_row_index == row_index or not text:
            column_index += 1
            continue

        column_end_index_exclusive = column_index + 1

        while column_end_index_exclusive < min(len(grid_cells), len(grid_sources)):
            next_source_row_index = grid_sources[column_end_index_exclusive].get(
                "source_row"
            )
            next_text_unit = grid_cells[column_end_index_exclusive].get("text") or {}
            next_text = str(next_text_unit.get("text") or "").strip()

            if next_source_row_index != source_row_index or next_text != text:
                break

            column_end_index_exclusive += 1

        cells.append(
            {
                "column_range": [column_index, column_end_index_exclusive],
                "language": text_unit.get("language"),
                "source_row_index": source_row_index,
                "text": text,
            }
        )
        column_index = column_end_index_exclusive

    if not cells:
        return None

    return {
        "cells": cells,
        "row_index": row_index,
        "source_visibility": "helper_context_only",
    }


def _build_compact_source_cell_payload(
    *,
    cell: dict[str, Any],
    column_end_index_exclusive: int,
    column_start_index: int,
    row_index: int,
    source_segment_id: str,
    source_unit_kind: SFISourceUnitKind,
) -> Optional[dict[str, Any]]:
    """Build one compact text-bearing source cell with exact grid placement.

    Parameters
    ----------
    cell
        Raw serialized table cell.
    column_end_index_exclusive
        Exclusive ending grid column occupied by the cell.
    column_start_index
        Inclusive starting grid column occupied by the cell.
    row_index
        Source header/body row index.
    source_segment_id
        Stable DocumentIR table segment identifier.
    source_unit_kind
        Structural source-unit kind for the cell.

    Returns
    -------
    Optional[dict[str, Any]]
        Compact cell payload, or `None` for a cell without visible text.
    """

    text_unit = cell.get("text") or {}
    text = str(text_unit.get("text") or "").strip()

    if not text:
        return None

    payload: dict[str, Any] = {
        "column_range": [column_start_index, column_end_index_exclusive],
        "language": text_unit.get("language"),
        "source_unit_id": build_sfi_source_unit_id(
            column_end_index_exclusive=column_end_index_exclusive,
            column_start_index=column_start_index,
            row_index=row_index,
            source_segment_id=source_segment_id,
            source_unit_index=None,
            source_unit_kind=source_unit_kind,
        ),
        "source_unit_kind": source_unit_kind,
        "text": text,
    }
    row_span = max(1, int(cell.get("row_span") or 1))

    if row_span > 1:
        payload["row_span"] = row_span

    return payload


def _build_compact_source_row_payload(
    *,
    grid_sources: Optional[list[dict[str, Any]]],
    n_cols: int,
    row: dict[str, Any],
    row_index: int,
    source_segment_id: str,
) -> dict[str, Any]:
    """Build one compact body row with exact source-grid column placement.

    When grid-source metadata is available, columns occupied by inherited row spans are
    excluded from raw-cell placement. Without that optional helper, cells are placed
    from left to right using their explicit column spans.

    Parameters
    ----------
    grid_sources
        Optional span-expanded source-row origins for each grid column.
    n_cols
        Total table grid width.
    row
        Raw serialized source row.
    row_index
        Source table row index.
    source_segment_id
        Stable DocumentIR table segment identifier.

    Returns
    -------
    dict[str, Any]
        Compact source-visible row with exact cell column ranges.
    """

    if grid_sources is None:
        available_column_indexes = set(range(n_cols))
    else:
        available_column_indexes = {
            column_index
            for column_index, source_info in enumerate(grid_sources)
            if source_info.get("source_row") == row_index
        }

    mapped_cells = map_raw_row_cells_to_column_ranges(
        available_column_indexes=available_column_indexes, n_cols=n_cols, row=row
    )
    cells: list[dict[str, Any]] = []

    for cell, column_start_index, column_end_index_exclusive in mapped_cells:
        cell_payload = _build_compact_source_cell_payload(
            cell=cell,
            column_end_index_exclusive=column_end_index_exclusive,
            column_start_index=column_start_index,
            row_index=row_index,
            source_segment_id=source_segment_id,
            source_unit_kind="table_body_cell",
        )

        if cell_payload is not None:
            cells.append(cell_payload)

    return {
        "cells": cells,
        "row_index": row_index,
        "source_visibility": "source_visible_row",
    }


def _build_compact_table_payload(
    extraction_window: ExtractionWindow,
) -> Optional[dict[str, Any]]:
    """Build a compact prompt-facing table payload for SFI extraction.

    Raw visible cells are represented sparsely with exact span-expanded column ranges.
    Optional grid helpers contribute only inherited row-span and filldown context, so
    the prompt preserves table geometry without repeating the complete rectangular grid.

    Parameters
    ----------
    extraction_window
        Source-faithful extraction window to compact for the prompt.

    Returns
    -------
    Optional[dict[str, Any]]
        A compact table payload for table windows, or `None` for block windows.
    """

    table = extraction_window.table

    if table is None:
        return None

    _validate_compact_body_row_placement(table)

    if len(extraction_window.source_segment_ids) != 1:
        raise ValueError(
            "Exact SFI table source anchors require one source segment per window."
        )

    source_segment_id = extraction_window.source_segment_ids[0]
    grid_sources_by_row = (
        table.grid_sources
        if table.grid_sources is not None
        else [None] * len(table.rows)
    )
    source_rows = [
        _build_compact_source_row_payload(
            grid_sources=grid_sources,
            n_cols=table.n_cols,
            row=row,
            row_index=row_index,
            source_segment_id=source_segment_id,
        )
        for grid_sources, row, row_index in zip(
            grid_sources_by_row, table.rows, table.row_indexes
        )
    ]
    payload: dict[str, Any] = {
        "declared_header_row_count": table.header_row_count,
        "header_rows": _build_compact_header_rows(
            header_rows=table.header_rows,
            n_cols=table.n_cols,
            source_segment_id=source_segment_id,
        ),
        "local_code": table.local_code,
        "n_cols": table.n_cols,
        "source_rows": source_rows,
        "table_source_policy": (
            "The table is a zero-based grid with n_cols columns. Every raw visible "
            "cell is represented once by column_range=[start, end), using an "
            "exclusive ending column. Use those ranges and row order to interpret "
            "merged headers and body columns. A source_rows entry may itself be a "
            "continuation or "
            "subheader row, so determine its role from visible wording and geometry "
            "rather than assuming every source_rows entry is ordinary data. For "
            "table-derived SFI candidates, exact description/code anchors must cite "
            "table.header_rows and/or table.source_rows cells, and description must be "
            "supported by the cited description-anchor excerpts. Candidate source_text "
            "is selected directly by the producer/checker as bounded source-visible "
            "evidence and may be narrower than a semantically composed description. Do "
            "not clean, translate, correct spelling, normalize, expand, or infer table "
            "descriptions from surrounding context. If an official table "
            "statement is split across adjacent source rows or cells, use all visible "
            "contributing fragments and include all contributing table_row_indexes. "
            "Use rowspan_context_rows and filldown_context_rows only to understand "
            "structural carryover; never anchor helper_context_only rows or use them in "
            "description."
        ),
    }

    if table.rows_grid is not None and table.grid_sources is not None:
        rowspan_context_rows = _build_rowspan_context_rows(
            grid_rows=table.rows_grid,
            grid_sources_rows=table.grid_sources,
            row_indexes=table.row_indexes,
        )

        if rowspan_context_rows:
            payload["rowspan_context_rows"] = rowspan_context_rows

    if table.rows_filldown is not None and table.rows_grid is not None:
        filldown_context_rows = _build_filldown_context_rows(
            filldown_rows=table.rows_filldown,
            raw_grid_rows=table.rows_grid,
            row_indexes=table.row_indexes,
        )

        if filldown_context_rows:
            payload["filldown_context_rows"] = filldown_context_rows

    return payload


def _build_filldown_context_rows(
    *,
    filldown_rows: list[dict[str, Any]],
    raw_grid_rows: list[dict[str, Any]],
    row_indexes: list[int],
) -> list[dict[str, Any]]:
    """Build sparse filldown context aligned to structural grid columns.

    Parameters
    ----------
    filldown_rows
        Rectangular filldown helper rows aligned to `row_indexes`.
    raw_grid_rows
        Rectangular span-expanded raw rows aligned to `row_indexes`.
    row_indexes
        Source table row indexes represented by both helper sequences.

    Returns
    -------
    list[dict[str, Any]]
        Helper-only filldown rows containing only values added beyond the raw grid.
    """

    context_rows: list[dict[str, Any]] = []

    for filldown_row, raw_grid_row, row_index in zip(
        filldown_rows, raw_grid_rows, row_indexes
    ):
        context_row = _build_compact_filldown_context_row_payload(
            filldown_row=filldown_row, raw_grid_row=raw_grid_row, row_index=row_index
        )

        if context_row is not None:
            context_rows.append(context_row)

    return context_rows


def _build_recent_first_section_context(
    extraction_window: ExtractionWindow,
) -> list[dict[str, Any]]:
    """Build prompt-facing section context from nearest to farthest.

    The persisted extraction window retains `source_section_path` in source order. This
    function reverses only the prompt-facing view and adds a zero-based recency rank.
    It does not classify headings, resolve scope, or infer hierarchy in Python.

    Parameters
    ----------
    extraction_window
        Source-faithful extraction window carrying cumulative section context.

    Returns
    -------
    list[dict[str, Any]]
        Context-only section references ordered nearest-first, where `recency_rank=0`
        identifies the nearest preceding context entry.
    """

    return [
        {
            "item_index": section_ref.get("item_index"),
            "page_index": section_ref.get("page_index"),
            "recency_rank": recency_rank,
            "source_visibility": "context_only",
            "text": section_ref.get("text"),
        }
        for recency_rank, section_ref in enumerate(
            reversed(extraction_window.source_section_path)
        )
    ]


def _build_rowspan_context_rows(
    *,
    grid_rows: list[dict[str, Any]],
    grid_sources_rows: list[list[dict[str, Any]]],
    row_indexes: list[int],
) -> list[dict[str, Any]]:
    """Build sparse inherited row-span context for selected source rows.

    Parameters
    ----------
    grid_rows
        Span-expanded rows aligned to `row_indexes`.
    grid_sources_rows
        Per-column source-row origins aligned to `grid_rows`.
    row_indexes
        Source table row indexes represented by the helper sequences.

    Returns
    -------
    list[dict[str, Any]]
        Helper-only rows containing cells inherited from earlier source rows.
    """

    context_rows: list[dict[str, Any]] = []

    for grid_row, grid_sources, row_index in zip(
        grid_rows, grid_sources_rows, row_indexes
    ):
        context_row = _build_compact_rowspan_context_row_payload(
            grid_row=grid_row, grid_sources=grid_sources, row_index=row_index
        )

        if context_row is not None:
            context_rows.append(context_row)

    return context_rows


def _validate_compact_body_row_placement(table: ExtractionWindowTablePayload) -> None:
    """Validate that compact body-row column placement is source-safe.

    `grid_sources` identifies which expanded grid columns originate in each raw source
    row. Without that helper, left-to-right placement is safe only for body rows that
    contain neither row-spanning cells nor synthetic rowspan placeholders. Reject
    ambiguous row-spanned layouts instead of shifting visible cells into the wrong
    columns in the compact LLM prompt.

    Parameters
    ----------
    table
        Extraction-window table payload to validate before compact placement.

    Raises
    ------
    ValueError
        If `grid_sources` is absent and any selected body row contains a cell with
        `row_span > 1` or a `rowspan_placeholder`.
    """

    if table.grid_sources is not None:
        return

    unsafe_row_indexes: list[int] = []

    for row, row_index in zip(table.rows, table.row_indexes):
        for cell in row.get("cells") or []:
            if not isinstance(cell, dict):
                continue

            row_span = max(1, int(cell.get("row_span") or 1))

            if cell.get("rowspan_placeholder") or row_span > 1:
                unsafe_row_indexes.append(row_index)
                break

    if unsafe_row_indexes:
        raise ValueError(
            f"Cannot safely build compact table body geometry without grid_sources: "
            f"selected body rows contain row-spanning cells or rowspan placeholders "
            f"at source row indexes {unsafe_row_indexes}. Provide grid_sources so "
            f"visible cells can be assigned to their true expanded-grid columns."
        )


def extract_sfi_candidates_from_window(
    *, extraction_window: ExtractionWindow, kg_config: CreateKGConfig
) -> PromptPair:
    """Generate the prompts for extracting candidate SFIs from one extraction window.

    Parameters
    ----------
    extraction_window
        Source-faithful LLM-ready extraction window.
    kg_config
        Country/document-specific KG extraction configuration.

    Returns
    -------
    PromptPair
        A PromptPair containing the system and user messages for the SFI extraction
        agent.
    """

    kg_config_context = _build_compact_kg_config_context(kg_config)
    user_payload = _build_compact_extraction_window_payload(extraction_window)

    system_message = dedent(
        f"""You are an Academic Standards extraction agent for a Learning Commons-shaped Knowledge Graph. Inspect exactly one compact source window and return candidate StandardsFrameworkItem records.

## Learning Commons ontology target
- A StandardsFrameworkItem (SFI) is an individual statement or structural element inside an academic standards framework.
- Extract an SFI when the source text is an official standards-framework item: either a learning expectation or an organizational grouping.
- Learning expectations are normative statements that define what learners should know, understand, demonstrate, or be able to do. Examples include standards, competencies, objectives, outcomes, content standards, performance expectations, benchmarks, and indicators.
- Organizational groupings are source-visible structural items that organize learning expectations. Examples include grades/stages, domains, strands, substrands, clusters, topics, units, themes, paliers, and similar headings when they structure the official standards hierarchy.
- Do not extract LearningComponents in this step. A LearningComponent is a granular teachable skill or concept that breaks down a broader SFI for instruction, activities, assessment items, or lesson planning. LearningComponents are created later and may support SFIs.
- Do not extract final relationships. Final hasChild, supports, buildsTowards, relatesTo, hasEducationalAlignment, and other edges are resolved in later stages.

## Scope
- Extract candidate SFIs only from the provided compact source window.
- Use source_context.scope_context_candidates, source_context.section_path_recent_first, and bounded preceding/following same-page headings to determine document scope, resolve visual reading-order inversions, and identify the source role of the visible target block/table content when the runtime config depends on context. Treat all source_context fields as context only, not as candidate evidence or an inferred hierarchy.
- Return zero SFI candidates when the window contains front matter, examples only, teacher guidance only, activities only, resources only, assessment suggestions only, or unrelated content.
- Do not infer hierarchy or relationships in this step. Extract only SFI candidates directly visible in this compact source window; final hasChild relationships are resolved later from finalized SFIs and source provenance.
- Extract grouping SFIs only when the grouping label itself is visible in target block or table source content. Do not emit a grouping solely because it appears anywhere in source_context, and do not add absent grade, strand, sub-strand, or parent candidates from context-only headings.
- Use the curriculum-specific extraction KG config below to adapt the generic ontology rules to this document.
- Treat `sfi_extraction_instructions` and every other applicable runtime policy field as authoritative for document-specific extraction behavior, including scope, source-occurrence boundaries, and candidate splitting or continuation rules.
- If any generic instruction, example, heuristic, or default conflicts with the runtime config, follow the runtime config. Re-check the runtime instructions before finalizing the result rather than relying on a generic rule that appears elsewhere in this prompt.

## Curriculum-specific KG extraction config
{json_dumps(kg_config_context)}

## Candidate classification policy
- Use normalized_statement_type="Standard Grouping" for source-visible organizational groupings that should become SFI grouping nodes.
- Use normalized_statement_type="Standard" for source-visible learning expectations that should become SFI standard nodes.
- Use normalized_statement_type="Other" rarely, only when the KG config explicitly says a visible framework item should be retained as an SFI but it is neither a grouping nor a learning expectation.
- Do not classify examples, exemplars, competencies lists that describe cross-cutting skills, activities, assessment suggestions, resources, pedagogical notes, durations, teacher guidance, or learning-material content as SFIs unless the KG config explicitly says they are standards-framework items.

## Statement type policy
- The runtime config includes statement_type_policy. For every SFI candidate, output statement_type using exactly one canonical statement_type from that policy.
- Treat aliases in statement_type_policy as recognition hints only. If the source text or your draft label matches an alias, output the corresponding canonical statement_type.
- Do not invent statement_type labels outside statement_type_policy. If no configured statement_type fits visible source text, do not emit an SFI candidate for that text; use extraction_notes or an auxiliary candidate only when needed.
- The candidate normalized_statement_type must exactly match the normalized_statement_type configured for its canonical statement_type.

## Semantic identity scope policy
- The runtime config includes identity_scope_statement_types. For each candidate, look up the ordered scope statement types configured for that candidate's canonical statement_type.
- Populate identity_scope_values with exactly those configured keys in that order. For each key, output exactly one canonical_value from that scope statement type's controlled_values; use aliases only to recognize the visible source wording. Return an empty mapping when no identity scope is configured for the candidate statement type.
- Resolve each configured identity-scope dimension independently. Determine Grade from Grade evidence, domain from domain evidence, strand from strand evidence, and so on; do not infer one dimension by searching for an older historical combination of several grouping labels.
- Apply scope evidence in this order: a controlled value explicit in the current target source; an authoritative runtime rule governing clear bounded local neighbor context; then the nearest recognized source_context.scope_context_candidates entry for that exact scope_statement_type. Use source_context.section_path_recent_first as the underlying recent-first context when no recognized candidate is available.
- Do not skip a nearer recognized value for an older value merely because the older value previously appeared beside the same repeated grouping wording. Any override of the nearest recognized value must be supported by explicit current-target evidence, clear bounded neighbor evidence, or an authoritative runtime rule.
- Determine identity scope from the active structural context governing the candidate occurrence, including explicit headings, grouping cells, and source-context structure. Do not choose a scope value merely because its wording appears incidentally inside another statement, example, activity, resource, assessment, or explanatory cell.
- Do not claim that a page, heading, context direction, source text, or controlled value was supplied unless it is actually present in the compact source window.

## Semantic code scope policy
- The runtime config includes code_scope_statement_types. When statement_code is non-null, determine its configured code type from the candidate's canonical statement_type and the candidate-local code_matches evidence, then look up the ordered code-scope statement types for that code type.
- Populate code_scope_values with exactly those configured keys in that order, using one configured canonical_value for each key. Return an empty mapping when statement_code is null or the resolved code type has no configured code scope.
- Code scope and identity scope may use the same structural evidence, but they are separate contracts. Do not infer either scope from incidental curriculum vocabulary, and do not repair surprising source placement from code expectations alone.
- Preserve surprising source organization rather than correcting it from subject-matter expectations. When the bounded evidence is genuinely insufficient, follow the runtime policy for unresolved or omitted candidates rather than guessing.

## Candidate field policy
- Return sfi_candidates in source order and use unique candidate_id values. These IDs, the candidate order, and auxiliary related_candidate_ids are persisted as returned, so make them complete and internally consistent.
- Preserve source occurrences during extraction; do not perform logical deduplication. Each independently printed or otherwise independently source-visible SFI occurrence must remain a separate candidate, even when another occurrence has the same statement type, code, description, normalized wording, or apparent logical identity.
- Combine source locations into one candidate only when they are visible fragments of one source occurrence, such as a single statement continuing across adjacent cells, rows, slices, or headers. Do not combine complete independently printed occurrences merely because they repeat, overlap semantically, or are expected to merge in a later stage.
- Apply the runtime `sfi_extraction_instructions` whenever they define more specific occurrence, continuation, splitting, or repetition behavior. If a generic example or heuristic conflicts with those instructions, the runtime instructions take precedence.
- Leave logical identity resolution and merging of repeated source occurrences to the downstream SFI deduplication stage. The same code with materially different source-visible wording must also remain separate.
- Every candidate must provide non-empty description_source_anchors. Copy each source_unit_id exactly from the supporting block.source_units entry or raw table cell. For each anchor, copy an exact non-empty source excerpt into that anchor's source_text and set occurrence_index to the zero-based left-to-right non-overlapping occurrence of that exact excerpt within the complete referenced source unit. Use occurrence_index=0 when the excerpt appears once.
- description_source_anchors must identify the exact source-visible fragments that support the complete semantic description, in source order. Runtime policy may require noncontiguous composition, such as a shared stem plus one later list item; in that case, anchor the shared stem and the individual item separately without including intervening peer items. Do not anchor broader neighboring text, a complete row, or a complete block when narrower exact fragments support the statement.
- code_source_anchors must be empty when statement_code is null. When statement_code is non-null, code_source_anchors must identify the exact source-unit occurrence of the corresponding code_matches[].raw_value. Do not use description anchors, surrounding text, local_code, section context, or another candidate's code as a substitute.
- Stable source anchors define physical source occurrence. Two candidates in different table cells, list items, block slices, or different repeated excerpt occurrences are distinct source occurrences even when they share one row, segment, code, and wording.
- description should preserve the complete exact source-language wording of the SFI. For learning expectations, use the full official statement text. For groupings, preserve the complete grouping label or heading text, including visible hierarchy terms, ordinal numbers, punctuation, and separators such as "Grade", "Strand", "Sub-Strand", "3", and ":". Remove only a separately represented item identifier code. Do not mistake a hierarchy label or ordinal organizer prefix for a code. When a visible code functions as a separate item identifier, exclude that code from description, place its normalized form only in statement_code, and cite its raw source form in code_source_anchors. Removing a separately represented identifier is not a wording correction. When multiple explicit labeled fields appear on one physical line, such as one label-value field followed by another label-value field, treat each complete visible field as a separately bounded source span and preserve their source order. Do not clean, translate, correct spelling, normalize, expand, infer, or truncate the actual statement wording.
- statement_type must use exactly one canonical source-facing role from statement_type_policy.
- statement_code must use the candidate-local code_matches[].normalized_value whose code_type matches statement_type_policy.code_type for the candidate. The matching code_matches[].raw_value must be cited exactly by code_source_anchors and must belong to this candidate's source occurrence. Formatting normalization may remove whitespace around punctuation or separate a complete code printed immediately adjacent to statement text, but it must never change a code prefix, alphanumeric character, delimiter, or numeric component. Treat block.local_code and table.local_code as context only unless the same candidate-local code is also exposed by code_matches. When statement_type_policy.code_type is null, emit a code only when exactly one candidate-local code_match is unambiguous. Use null for nearby codes, segment labels, table identifiers that are not item codes, ambiguous or incomplete codes, codes not supported by this candidate's exact source anchors, or codes that belong to another statement. Do not leave statement_code null solely because code_matches[].raw_value contains spacing around punctuation or is glued to the statement text.
- language must match the source language tag on the exact block.source_units or table cells cited by description_source_anchors. Use "mul" when the description combines visible text from more than one source language. Use the KG config primary language only when the supporting source units have no language metadata.
- confidence should reflect how clearly the source window supports the candidate.
- table_header_indexes and table_row_indexes are source-text location fields, not general context fields.
- For table candidates, description and every description/code source anchor must be supported by the cited table_header_indexes and/or table_row_indexes.
- Populate table_header_indexes only when candidate anchors cite table.header_rows.
- Populate table_row_indexes only when candidate anchors cite table.source_rows.
- Do not include table_header_indexes merely because a row appears under a relevant column header such as Content Standard or Indicators and Exemplars; use the header text as classification context only.
- Include both table_header_indexes and table_row_indexes only when candidate anchors cite visible text from both table.header_rows and table.source_rows.
- description should contain the complete source-visible SFI statement or grouping label, including visible continuation fragments when an official statement is split across adjacent table rows or cells.
- source_text is the bounded source-visible evidence excerpt selected for this individual candidate. It is persisted as returned and is not reconstructed from description_source_anchors or code_source_anchors.
- source_text may be narrower than description when the complete semantic description inherits a visible shared stem or other source context. Keep source_text focused on the individual source occurrence and do not include intervening peer statements, hidden context, inferred wording, or unrelated neighboring text.
- Whenever statement_code is non-null, code_source_anchors must cite the corresponding code_matches[].raw_value while statement_code contains code_matches[].normalized_value. Keep the separately represented code out of description unless runtime policy says the printed code is part of the official wording.
- For table candidates, description may equal source_text when the bounded evidence already contains the complete official statement and no inherited source fragment is needed. A description may be a complete source-visible cell, contiguous cell range, bounded clause, or semantic statement assembled from exact visible fragments under runtime policy. Do not create a description by rewriting, interleaving unrelated statements, or truncating the official wording.

## Source fidelity rules
- Preserve source-language text. Do not translate. Use block.source_units and table-cell language fields to assign candidate and auxiliary language accurately.
- Use only source_unit_id values exposed on source-visible target block units or raw table cells. Never invent an ID and never anchor any source_context field, rowspan_context_rows, filldown_context_rows, canonical headers, or other helper/context content.
- If one exact source excerpt occurs more than once within a source unit, occurrence_index must identify the correct physical occurrence. Different occurrence_index values are different physical source occurrences.
- For a statement assembled from adjacent visible fragments, return one description anchor per contributing fragment. Do not combine independently complete cells or text occurrences into one candidate merely because their words are identical.
- For a non-list stitched block, block.source_text is the complete source-visible logical block and may contain one statement continuing across multiple block.source_units/slices.
- For SFI candidates, source_text must be bounded source-visible evidence from target block.source_units, table.header_rows cells, or table.source_rows cells. It need not contain every fragment used to compose description. Auxiliary candidate source_text remains one verbatim source-visible excerpt from block.source_text, table.header_rows, or table.source_rows. Never use any source_context field or helper-only filldown context as source_text.
- For table candidates, description and every description/code anchor must be source-visible in the cited table_header_indexes and/or table_row_indexes. Cite exactly the raw header/body rows that contribute anchored evidence; do not include unrelated context rows. Do not use text from another visible row/header unless that row/header index is also cited.
- If a table statement visibly continues across adjacent source rows or cells, include every contributing table_row_index and assemble the complete official statement in description from those visible fragments. Do not truncate description at the first row/cell.
- The final KG-building stages recover full source provenance from exact anchors, window_id, window_source_segment_ids, table_row_indexes, table_header_indexes, and the persisted ExtractionWindow/DocumentIR. Do not use source_text to carry hidden context, parentage, or non-visible text.
- Use code_matches as typed source evidence, not as final KG nodes. Assign code_matches[].normalized_value to statement_code only when the corresponding code_matches[].raw_value is cited by this candidate's exact code_source_anchors and the bounded source evidence associates it with the candidate occurrence. Immediate adjacency between a complete raw code and the first character of statement text is valid pairing when that is how the source is printed.
- Treat block.local_code and table.local_code as context that can help locate the relevant candidate-local code_match, not as an independent source of statement_code. Do not automatically copy a table identifier or segment label into statement_code. When the same normalized code is visibly repeated for distinct source items, multiple candidates may preserve it only when each candidate independently cites the corresponding raw code and its complete description with exact source anchors.
- Table headers are source-visible structural evidence. When the curriculum-specific KG config says a table-header label is an official grouping SFI, extract it as a Standard Grouping candidate.
- For table-row-derived SFI candidates, table_row_indexes must be non-empty and must use the visible table.source_rows[].row_index values containing candidate description/code anchors.
- For table-header-derived SFI candidates, table_header_indexes must be non-empty and must use the visible table.header_rows[].header_row_index values containing candidate description/code anchors; table_row_indexes should be empty unless anchors also cite visible text from table.source_rows.
- Do not cite a table header row as source evidence when the header only explains the meaning of a body-row column. In that case, use the header as classification context and cite only the body row indexes containing candidate anchors.
- If a table candidate's anchors cite visible text from both header rows and body rows, include both table_header_indexes and table_row_indexes; otherwise cite only the header rows or body rows containing anchored evidence.
- Treat table.filldown_context_rows as helper context only. These cells repeat row-span context for interpretation, but they are not source-visible evidence. Do not anchor helper_context_only cells or use them as auxiliary source_text unless the same text is also visible in block.source_text, table.header_rows, or table.source_rows.

## Output contract
Copy window_id, window_index, and window_source_segment_ids exactly from the compact source window. Return sfi_candidates in source order with unique candidate_id values and internally consistent auxiliary related_candidate_ids.
Every SFI candidate must include code_scope_values, identity_scope_values, exact description_source_anchors, and bounded non-empty source_text. A coded candidate also requires exact code_source_anchors and any configured code-scope dimensions; an uncoded candidate must return empty code_scope_values and code_source_anchors. Python enforces exact references and configured scope contracts but does not reinterpret semantic decisions.
Keep extraction_notes short; use them only for window-level extraction issues, not to summarize examples, competencies, or activities.
Return auxiliary candidates only when they clarify why prominent source-visible text was not extracted as an SFI; do not list ordinary examples, activities, competencies, or guidance notes.
Do not emit auxiliary candidates for routine front matter, ordinary examples, or repeated core-competency lists unless they are unusually ambiguous or likely to be mistaken for an SFI.
        """
    )

    user_message = dedent(
        f"""Extract candidate SFIs from this compact source window.

## Compact source window JSON
{json_dumps(user_payload)}
        """
    )

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )


def resolve_sfi_has_child_parents(
    resolution_request: SFIHasChildResolutionRequest,
) -> PromptPair:
    """Generate prompts for direct hasChild parent selection.

    Parameters
    ----------
    resolution_request
        Bounded parent-selection request containing finalized child SFIs and their
        source-grounded parent candidate sets.

    Returns
    -------
    PromptPair
        System and user messages for the hasChild parent-selection agent.
    """

    user_payload = resolution_request.model_dump(mode="json")
    system_message = dedent(
        """You are an Academic Standards hierarchy-resolution agent for a Learning Commons-shaped Knowledge Graph. Inspect finalized StandardsFrameworkItem children and their bounded parent candidate sets, then choose direct hasChild parent endpoints.

## Task boundary
- Choose only direct hasChild parents for the supplied finalized child SFIs.
- Select parent_endpoint_id values only from each child's provided parent_candidates list.
- Do not invent parent nodes, source codes, headings, registry candidates, merge groups, or relationships.
- Do not choose endpoints outside the bounded candidate set.
- Do not infer LearningComponents, supports, buildsTowards, relatesTo, or any relationship other than hasChild.
- A child may have one or more direct parents when the source evidence supports multiple direct hierarchy memberships.
- If none of the supplied candidates is source-supported as a direct parent, set unresolved=true and select no parents.

## Runtime hierarchy instructions
- The request payload includes `sfi_has_child_instructions`. Treat that field as the authoritative document-specific hierarchy policy for this request.
- If `sfi_has_child_instructions` conflicts with these generic instructions, follow `sfi_has_child_instructions` unless doing so would require selecting a parent outside the child's supplied `parent_candidates`, inventing an endpoint, or violating the output contract.

## Parent-selection policy
- Prefer the most direct source-grounded parent, not merely the broadest or most nearby candidate.
- Treat code-parent hints, active outline-stack parents, matched section labels, same table context, same source context, and nearest preceding grouping evidence as retrieval evidence, not as automatic truth.
- The StandardsFramework root is a valid direct parent only when the child is a top-level framework item or no source-supported SFI parent is available.
- Do not select the StandardsFramework root merely to guarantee reachability when one or more semantic SFI parents are selected.
- Do not choose a parent by source code alone. Same-code/different-content audit flags mean endpoints must remain distinct.
- When multiple parent candidates share the same normalized code or otherwise look plausible by topic, use source-local hierarchy evidence before semantic similarity. Actual source rows/spans, active local outline, source context keys, source segments/windows, and table context help rank candidates, but shared locality alone does not prove direct parentage.
- For table-derived children, table row/span context is strong retrieval evidence. Treat it as direct-parent evidence only when it is corroborated by an explicit grouping/header relationship, a locally compatible code-parent hint, typed source-local controlled scope, or other source-visible hierarchy evidence. A parallel or misaligned table row may contain an allowed parent type without establishing a hasChild relationship.
- Treat topical or semantic similarity as weaker than source-local table/context evidence when resolving duplicate-code or repeated-label candidates.
- Page overlap alone is weak evidence and must not override stronger source hierarchy evidence.
- DocumentIR section-path labels are evidence, not a guaranteed clean ancestor chain.
- In each child_context, `section_path_labels` is ordered from most recent/local source context to older/broader context after bounded truncation. Earlier labels in that list are usually more useful for direct parent selection; later labels may be stale carryover and should be treated cautiously.
- `same_table_context`, `active_outline_stack_parent`, `nearest_preceding_grouping`, `nearby_source_context_key`, and `matched_section_path_label` are retrieval/ranking signals, not automatic truth. Corroborated direct-parent evidence includes a locally compatible `code_parent_hint`, typed source-local controlled scope, or an explicit `source_scope_grouping` relationship.
- `active_outline_stack_parent` evidence means source-order scanning of finalized SFIs found the candidate as the active immediate parent type under the configured statement-type hierarchy. This is a strong candidate-preservation signal for same-page or same-window headings, but it is still not automatic truth; confirm against the child context, parent context, runtime hierarchy instructions, codes, and source locality.
- Source-visible hierarchy outranks inferred code hierarchy when they conflict. Codes are strong evidence, but source-visible table rows/spans, continuation rows, active local outline headings, and local section-path evidence are stronger when they identify a direct parent of the correct type.
- `source_visible_direct_parent` is a strong source-local signal, not an absolute veto. It should normally beat root fallback, same-topic fallback, page/window proximity, broad semantic similarity, and stale carry-forward evidence.
- A uniquely corroborated non-root direct parent should normally be selected. However, unresolved remains appropriate when runtime hierarchy instructions or source/code evidence materially contradict that candidate; explain the conflict rather than forcing an edge.
- For table-derived children, same-row, row-spanned, or continued-row evidence should rank a candidate above distant or merely semantic candidates, but it must not force selection when code, typed scope, source labels, or runtime hierarchy instructions contradict that candidate.
- For coded children, use only supplied `code_parent_hint` evidence as a strong code-parent signal. Do not infer that a textual dot-prefix is universally hierarchical; code systems may be scoped, reused, partially hierarchical, or non-hierarchical. Avoid sibling fallback based only on code or topic, and explain any source/code conflict.
- If a source-visible direct parent is supplied and selected over code inference, explain the source/code conflict briefly in the reason instead of inventing a missing parent.

## Output contract
- Copy request_id exactly.
- Return exactly one child_resolutions entry for every child in the request.
- For resolved children, selected_parent_endpoint_ids must contain one or more endpoint IDs from that child's parent_candidates.
- For unresolved children, selected_parent_endpoint_ids must be empty.
- Give a concise source-grounded reason for every child decision.
        """
    )
    user_message = dedent(
        f"""Resolve direct hasChild parents for this bounded request.

## Bounded hasChild parent-selection request JSON
{json_dumps(user_payload)}
        """
    )

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )


def review_sfi_dedup_candidates(review_request: SFIDedupReviewRequest) -> PromptPair:
    """Generate prompts for one bounded SFI merge/dedup review set.

    Parameters
    ----------
    review_request
        Bounded review set containing compact candidates, shared context windows, and
        explicit candidate-subset retrieval signals.

    Returns
    -------
    PromptPair
        System and user messages for the SFI dedup review agent.
    """

    user_payload = _build_compact_dedup_review_payload(review_request)
    system_message = dedent(
        """You are an Academic Standards SFI deduplication review agent for a Learning Commons-shaped Knowledge Graph. Inspect exactly one bounded candidate review set and decide which registry candidates represent the same logical source item.

## Task boundary
- Decide only within the supplied review_set_id and supplied candidate records.
- Do not ask for the full registry, full extraction windows, full DocumentIR, or outside source context.
- Do not invent new candidates, candidate IDs, statement codes, hierarchy nodes, or final StandardsFrameworkItem IDs.
- Do not infer hasChild parentage or other relationships.
- Do not generate, rewrite, combine, correct, translate, or paraphrase final KG text. A later step constructs final records from the selected source-backed candidate.
- For every merge, select exactly one existing candidate as the representative source-facing candidate.
- For a mixed-type merge, select only which existing source candidate supplies the canonical statement_type and normalized_statement_type pair. Do not invent or rewrite a type.
- For a mixed-code merge, select only which existing source candidate supplies the canonical statement code. Do not rewrite, normalize beyond the supplied fields, or invent a code.

## Decision labels
Use exactly one decision for each decision group:
- merge: all candidates in the group represent the same final source item.
- keep_separate: candidates are valid separate source items despite lexical, code, or provenance similarity.
- conflict: candidates appear to claim the same identity but contain materially incompatible text or source context.
- needs_review: evidence is insufficient for a safe automated decision.

## Required coverage
- Assign every input registry_candidate_id to exactly one decision group.
- Do not include candidate IDs outside the supplied review set.
- Use singleton groups when one candidate must be kept separate from the rest.
- Give a short source-grounded reason for every group.

## Representative candidate-selection contract
- representative_candidate_id is required for every decision=merge group and must be null for keep_separate, conflict, and needs_review groups.
- Select exactly one candidate inside the merge group whose existing description is the cleanest faithful source-facing form of the logical item.
- Prefer a candidate without presentation-only list or row numbering, duplicated boilerplate, or obvious OCR/extraction artifacts when the supplied evidence shows those elements are not part of the source item.
- Do not assume every number, prefix, punctuation mark, or repeated phrase is noise; preserve meaningful source wording and follow the runtime instructions when they define source conventions.
- Select only an existing candidate ID. Do not create a new description or rewrite, combine, correct, translate, or paraphrase candidate text.
- representative_candidate_id, canonical_type_source_candidate_id, and canonical_code_source_candidate_id are independent selections and may identify different candidates.

## Canonical statement-type selection contract
- Count distinct (statement_type, normalized_statement_type) pairs within each proposed decision group.
- canonical_type_source_candidate_id and canonical_type_selection_reason must be null unless decision=merge and the group contains more than one distinct statement-type pair.
- For a merge with one statement-type pair, leave both canonical-type selection fields null; deterministic pipeline logic resolves that pair.
- For a merge with multiple statement-type pairs, set canonical_type_source_candidate_id to exactly one candidate inside that decision group and explain the source-grounded selection in canonical_type_selection_reason.
- The selected candidate's existing statement_type and normalized_statement_type become canonical for the logical merged SFI. The selected pair must already be present on that candidate and must not be rewritten.
- If the bounded evidence and runtime instructions do not justify one canonical type source, do not return merge. Use needs_review or conflict.

## Canonical code-selection contract
- Count distinct non-null normalized_statement_code values within each proposed decision group.
- canonical_code_source_candidate_id and canonical_code_selection_reason must be null unless decision=merge and the group contains more than one distinct non-null normalized statement code.
- For a merge with zero or one distinct non-null normalized statement code, leave both canonical-code selection fields null; deterministic pipeline logic resolves that case.
- For a merge with multiple distinct non-null normalized statement codes, set canonical_code_source_candidate_id to exactly one coded candidate inside that decision group and explain the source-grounded selection in canonical_code_selection_reason.
- The selected candidate's existing statement_code and normalized_statement_code become the canonical code of the logical merged SFI. Preserve all other printed codes as source provenance.
- If the bounded evidence and runtime instructions do not justify selecting one coded source candidate, do not return merge. Use needs_review or conflict.
- Never select a candidate outside the decision group, a candidate with no code, or a code value that is not already present on the selected candidate.
- For same-type coded merges, every candidate must preserve the same applicable_code_type and the same code_scope_key/code_scope_values.
- A mixed-type subset covered by one same_source_occurrence_cross_type signal may preserve code-policy differences caused by its competing classifications. That signal is valid only when every listed candidate has one identical non-empty description_source_anchors set. Merge it only when those exact anchors identify one duplicate source occurrence, shared code-scope dimensions do not contradict one another, and one source-backed canonical code can be resolved. The canonical type source and canonical code source may be different candidates.
- Outside that narrow mixed-type same-occurrence case, incompatible code types, contradictory scope, or unresolved scope while another candidate is scoped require keep_separate, conflict, or needs_review.

## Semantic identity-scope contract
- identity_scope_key and identity_scope_values are deterministic, source-backed semantic scope resolved from runtime configuration. They are independent of official code scope and may be present for completely uncoded curricula.
- For same-type merges, every candidate must preserve the same identity_scope_key and identity_scope_values. Matching semantic scope permits comparison but never proves duplication.
- A mixed-type subset covered by one same_source_occurrence_cross_type signal may preserve different identity-scope shapes caused by its competing statement-type policies. Merge it only when every candidate preserves one identical non-empty description_source_anchors set and every scope dimension shared by two or more candidates has the same source-backed value. The selected canonical type source supplies the merged item's identity scope.
- Outside that narrow mixed-type same-occurrence case, different identity scope requires keep_separate, conflict, or needs_review.
- Do not reinterpret, rewrite, or infer missing identity-scope values. Use the supplied canonical values exactly.
- For uncoded same-type candidates, identical canonical_statement_value_key and identical identity scope define the same prospective no-code logical identity. This match is not automatic merge evidence, but do not return multiple eligible final groups with that exact identity when the runtime policy identifies one editorial duplication or one logical organizer. Apply the specific runtime rule and select one representative. When the source evidence instead supports genuinely distinct items but the supplied identity contract cannot distinguish them, use conflict or needs_review rather than silently producing colliding singleton groups.

## Compact evidence model
- review_signals are deterministic retrieval evidence. Each signal applies only to its listed candidate_ids; never assume it applies to the entire connected review set.
- A same_normalized_source_text signal means the listed subset has exact equality after internal normalization. It is not an automatic merge rule, and normalized text itself is intentionally omitted from the prompt.
- A same_source_occurrence_cross_type signal means the listed differently classified
  candidates preserve one identical validated set of exact description_source_anchors.
  Treat it as strong evidence of duplicate extraction, not an automatic merge rule.
  Same row, header, segment, window, code, or wording without identical anchors is not
  same-occurrence evidence. If you merge that subset, select the existing candidate
  whose source role supplies the correct canonical type.
- A canonical-value, code-bucket, text-bucket, warning, or shared-table-location signal is also review evidence rather than a merge decision.
- context_windows are shared nearby source windows. Each candidate's context_window_indexes identifies which windows are relevant to that candidate.
- context_items contain only runtime-configured context-bearing statement types. Their absence does not prove that no other source content exists.
- section_labels, boundary_markers, page_indexes, and source_text_excerpt are compact, fallible context. They are not resolved hierarchy or parentage.
- candidate description, source_text, and exact description/code source anchors preserve the source-facing evidence. Use them rather than guessing omitted normalized forms or internal bucket keys.

## Merge guardrails
- Apply evidence in this order: runtime sfi_dedup_instructions, visible candidate text and source references, shared context windows, then general dedup heuristics.
- Follow the runtime instructions whenever they are more specific, intentionally stricter, or define how the curriculum uses repeated organizers, aliases, progression, codes, or source anomalies.
- Do not merge candidates solely because they appear in the same review set, share a review signal, cite the same table row/header, occur in nearby windows, or have the same canonical value.
- Same statement_type plus same normalized_statement_code is strong evidence only when visible text and source context are compatible.
- Official codes are not guaranteed globally unique. When same-code candidates are visibly distinct source items, keep them separate and explain the same-code/different-content issue.
- Do not merge different statement types or normalized statement types unless visible evidence clearly shows duplicate extraction of one source item under inconsistent labels.
- Repeated organizers or objectives may be distinct under different source scopes. Conversely, visible punctuation, ranges, aliases, or expanded labels may preserve one logical organizer when the runtime instructions say so.
- Merge no-code candidates only when source-visible text and supplied context support one logical source item.
- Choose needs_review rather than guessing when the bounded payload is genuinely insufficient.
        """
    )
    user_message = dedent(
        f"""Review this bounded SFI deduplication candidate set.

## Bounded dedup review payload JSON
{json_dumps(user_payload)}
        """
    )

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )


def validate_sfi_dedup_response(
    *, draft_response: SFIDedupReviewResponse, review_request: SFIDedupReviewRequest
) -> PromptPair:
    """Generate prompts for reviewing and correcting one draft SFI dedup response.

    Parameters
    ----------
    draft_response
        First-stage structured SFI dedup response.
    review_request
        Original bounded review request supplied to the producer.

    Returns
    -------
    PromptPair
        System and user messages for the second-stage SFI dedup validation agent.
    """

    request_payload = _build_compact_dedup_review_payload(review_request)
    system_message = dedent(
        """You are a strict Academic Standards SFI deduplication validation and correction agent (CHECKER MODE) for a Learning Commons-shaped Knowledge Graph.

You will receive:
1. The complete bounded dedup review request.
2. The complete draft SFIDedupReviewResponse produced by a first-stage agent.
3. Generic validation rules below.
4. Curriculum-specific sfi_dedup_instructions in the request payload.

## Task
Independently determine the correct partition and decision for every supplied candidate. Do not assume the draft is correct. Return an SFIDedupValidationVerdict.

- Set passed=true only when the draft requires no material semantic correction.
- When passed=false, identify every material error and return a complete corrected SFIDedupReviewResponse in corrected_response.
- The corrected response replaces the draft. It must be complete and self-contained, not a patch.
- You may regroup candidates, change decisions, reasons, confidence values, representative-candidate selections, canonical-type source selections, and canonical-code source selections, or replace conflict/needs_review outcomes when the evidence and runtime policy require it.
- Do not invent, omit, or duplicate candidate IDs.

## Authority and compact evidence model
- Runtime sfi_dedup_instructions are authoritative for curriculum-specific identity, organizer scope, aliases, progression, codes, repeated headings, and known source anomalies.
- review_signals are candidate-subset retrieval evidence. Check the candidate_ids on every signal; a signal applying to one subset must not be generalized to the whole review set.
- A same_normalized_source_text signal records exact internal normalized equality for its listed subset only. It is not a universal merge rule.
- A same_source_occurrence_cross_type signal records one identical validated set of
  exact description_source_anchors across different classifications. Treat it as strong
  duplicate-extraction evidence, not an automatic merge rule, and verify any canonical
  type selection against the visible source role. Candidates that merely share a row,
  header, segment, window, code, or wording are not the same physical occurrence when
  their exact anchors differ.
- context_windows are shared nearby windows. Use each candidate's context_window_indexes to locate the context relevant to that candidate.
- context_items contain only runtime-configured context-bearing statement types. Their absence is not proof that no other source material exists.
- section_labels, boundary_markers, page indexes, and excerpts are compact and fallible. They are not final hierarchy, parentage, or merge identity.
- Candidate description, source_text, exact description/code source anchors, code fields, statement types, table indexes, canonical values, identity-scope fields, and runtime instructions are the primary evidence.
- A canonical controlled value is meaningful according to the runtime policy. Do not discard it solely because visible wording contains punctuation, qualifiers, ranges, aliases, or expanded labels. Do not merge solely because it matches when source context or runtime instructions require separation.

## Independent audit procedure
1. Reconstruct the candidate set and map every review signal to exactly its listed candidate subset.
2. For each candidate, inspect its own text, code/type fields, table references, and referenced context windows.
3. Independently create the best complete candidate partition before comparing it with the draft.
4. Check for under-merging, over-merging, incorrect singleton decisions, unsupported conflicts, and unjustified needs_review outcomes.
5. Verify every draft reason against visible evidence and runtime instructions. A fluent explanation does not make a contradictory decision valid.
6. Determine whether textual differences change logical identity under the runtime policy or merely preserve source-visible variants within one merged item's provenance.
7. Check same-code/different-content cases carefully. Codes are evidence, not guaranteed globally unique identities.
8. For every proposed merge, independently verify that representative_candidate_id identifies one candidate inside that merge group whose existing description is the cleanest faithful source-facing form. Reject missing, out-of-group, or rewritten representative text.
9. For every proposed merge, count distinct statement-type pairs. When there are multiple, independently verify that canonical_type_source_candidate_id identifies one candidate inside that group and that canonical_type_selection_reason justifies that exact existing type pair.
10. For every proposed merge, count distinct non-null normalized codes. When there are multiple, independently verify that canonical_code_source_candidate_id identifies one coded candidate inside that merge group and that canonical_code_selection_reason justifies that exact source-backed choice.
11. Reject a mixed-code merge that lacks a defensible source-candidate selection, selects an uncoded or out-of-group candidate, or invents a code. Use needs_review or conflict when no canonical source candidate can be justified.
12. For every same-type coded merge, verify that all candidates have the canonical applicable_code_type and the same resolved code_scope_key/code_scope_values. For a mixed-type subset, permit classification-derived code-policy differences only when every candidate has one identical non-empty description_source_anchors set, shared scope dimensions agree, and one source-backed canonical code policy can be resolved.
13. For every same-type merge, verify exact identity_scope_key and identity_scope_values equality. For a mixed-type subset, permit classification-derived scope-shape differences only when every candidate has one identical non-empty description_source_anchors set and shared scope dimensions agree; the selected canonical type source supplies final identity scope.
14. Reject relaxed mixed-type code or identity-scope treatment when the candidates do not preserve one identical exact description-source-anchor set, even if a coarse review signal, shared row, shared segment, matching code, or identical wording suggests similarity.
15. Treat representative_candidate_id, canonical_type_source_candidate_id, and canonical_code_source_candidate_id as independent selections; they may identify different candidates.
16. For uncoded same-type candidates with identical canonical_statement_value_key and identical identity scope, verify that the draft does not leave multiple eligible final groups with one indistinguishable logical identity. Matching fields do not force a merge, but a runtime-defined editorial duplication must be merged; if the source supports distinct items and the supplied identity contract cannot distinguish them, require conflict or needs_review.
17. Use needs_review only when the bounded evidence remains genuinely insufficient after applying the runtime instructions.

## Universal response contract
- Copy review_set_id exactly from the request.
- Cover every input registry_candidate_id exactly once.
- Include no candidate ID outside the request.
- Every decision group must have a source-grounded reason.
- representative_candidate_id is required for every merge group, must identify one candidate inside that group, and must be null for non-merge decisions.
- The representative selection must choose existing candidate text rather than generate, rewrite, combine, correct, translate, or paraphrase text.
- canonical_type_source_candidate_id and canonical_type_selection_reason must be null except for merge groups with multiple distinct statement-type pairs.
- A mixed-type merge must select one candidate inside the group and provide a source-grounded canonical_type_selection_reason.
- canonical_code_source_candidate_id and canonical_code_selection_reason must be null except for merge groups with multiple distinct non-null normalized source codes.
- A mixed-code merge must select one coded candidate inside the group and provide a source-grounded canonical_code_selection_reason.
- Every same-type merge must preserve one common identity_scope_key and identity_scope_values mapping. A directly signaled mixed-type same-occurrence merge may instead use the canonical type source candidate's mapping when shared scope dimensions are non-contradictory.
- Representative, canonical-type, and canonical-code source selections are independent and may identify different candidates.
- Do not infer hierarchy relationships or create final StandardsFrameworkItem IDs.

## Validation verdict contract
- issues should contain only meaningful findings. Use severity error for anything requiring a corrected response and warning only for non-blocking observations.
- passed=true: corrected_response must be null and no issue may have severity error.
- passed=false: include at least one error issue and provide a complete corrected response resolving all errors.
- Copy review_set_id exactly into the verdict and any corrected response.
- Explain the overall decision in a concise, source-grounded rationale.
- Do not return prose outside the structured verdict.
        """
    )
    user_message = dedent(
        f"""Validate this draft SFI dedup response against the complete bounded request.

## Bounded dedup review request JSON
{json_dumps(request_payload)}

## Draft SFIDedupReviewResponse JSON
{draft_response.model_dump_json(exclude_none=True)}
        """
    )

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )


def validate_sfi_extraction_result(
    *,
    draft_result: SFIExtractionResult,
    extraction_window: ExtractionWindow,
    kg_config: CreateKGConfig,
) -> PromptPair:
    """Generate prompts for reviewing and correcting one draft SFI extraction result.

    Parameters
    ----------
    draft_result
        First-stage structured SFI extraction result.
    extraction_window
        Source-faithful extraction window used by the first-stage agent.
    kg_config
        Country/document-specific KG extraction configuration.

    Returns
    -------
    PromptPair
        System and user messages for the second-stage SFI validation agent.
    """

    config_context = {
        "bilingual_pair_policy": (kg_config.academic_standards.bilingual_pair_policy),
        "code_patterns": kg_config.academic_standards.code_patterns,
        "code_scope_statement_types": (
            kg_config.academic_standards.code_scope_statement_types
        ),
        "country": kg_config.metadata.country,
        "grades_or_stages": kg_config.metadata.grades_or_stages,
        "identity_scope_statement_types": (
            kg_config.academic_standards.identity_scope_statement_types
        ),
        "primary_language": kg_config.metadata.primary_language,
        "sfi_extraction_instructions": (
            kg_config.academic_standards.sfi_extraction_instructions
        ),
        "sfi_validation_instructions": (
            kg_config.academic_standards.sfi_extraction_validation_instructions
        ),
        "statement_type_policy": [
            item.model_dump(exclude_none=True, mode="json")
            for item in kg_config.academic_standards.statement_type_policy
        ],
        "subject": kg_config.metadata.subject,
    }
    source_payload = _build_compact_extraction_window_payload(extraction_window)
    system_message = dedent(
        f"""You are a strict Academic Standards SFI validation and correction agent
(CHECKER MODE) for a Learning Commons-shaped Knowledge Graph.

You will receive:
1. The compact source window reviewed by an extraction agent.
2. The complete draft `SFIExtractionResult` produced by that agent.
3. Generic validation rules below.
4. Curriculum-specific extraction and validation instructions from runtime config.

## Task
Independently verify the draft against the supplied source window and runtime policy.
Do not assume the draft is correct. Return an `SFIExtractionValidationVerdict`.

- Set `passed=true` only when the draft requires no material correction.
- When `passed=false`, identify every material error and return a complete corrected
  `SFIExtractionResult` in `corrected_result`.
- The corrected result replaces the draft in the pipeline. It must be complete and
  self-contained, not a patch or list of edits.
- You may add omitted candidates, remove false positives, split candidates, or combine
  source fragments that belong to one source occurrence; you may also correct statement
  types, codes, languages, exact anchors, source locations, auxiliary records,
  notes, identity scope, and code scope when the source and runtime policy require it.
  The corrected result is persisted as returned, so source_text, candidate order, IDs,
  scope values, and auxiliary references must be complete and internally consistent.
  The checker-selected result is the final semantic authority. Python validates exact
  references, configured scope shape and membership, and cross-object integrity without
  reinterpreting curriculum semantics.
- Do not combine independently printed or independently source-visible occurrences as a
  form of logical deduplication. Logical merging belongs to the downstream SFI
  deduplication stage.

## Runtime curriculum policy
{json_dumps(config_context)}

The runtime `sfi_extraction_instructions`, `sfi_validation_instructions`,
`bilingual_pair_policy`, and every other applicable runtime policy field are
authoritative for curriculum-specific scope, source-occurrence boundaries,
continuation rules, multilingual handling, edge cases, and tricky distinctions. If any
generic instruction, example, heuristic, or default conflicts with the runtime config,
follow the runtime config unless doing so would invent source text or violate the
structured output contract. Re-check the runtime instructions before accepting or
correcting the draft.

## What to validate

### 1. Completeness and scope
- Check whether every source-visible occurrence that should be an SFI under the
  runtime extraction instructions is represented exactly once in the draft.
- Treat independently printed or independently source-visible repetitions as separate
  extraction occurrences unless the runtime instructions explicitly define a more
  specific source-fragment rule. Same type, code, description, normalized wording, or
  apparent logical identity does not make separate occurrences duplicates at this
  stage.
- Find material omissions, duplicate extraction of the same single source occurrence,
  false positives, and over-extraction. Do not label distinct repeated source
  occurrences as duplicates merely because they may merge downstream.
- Do not require ordinary examples, activities, resources, pedagogy, assessment notes,
  or other excluded material unless runtime instructions explicitly classify them as
  SFIs.
- Do not invent absent grouping labels or parent nodes from section context.

### 2. Semantic classification
- Verify each candidate's `statement_type` against the configured statement-type policy.
- Verify `normalized_statement_type` matches the configured canonical type.
- Distinguish official learning expectations and structural groupings from examples,
  content details, activities, teacher guidance, assessment criteria, and resources.
- Apply curriculum-specific exceptions and known source anomalies exactly as instructed.
- Verify identity_scope_values against identity_scope_statement_types for the candidate's canonical statement_type. The mapping must contain the configured scope dimensions in configured order, and each value must be an existing canonical_value for that scope statement type; use aliases only as recognition evidence. The mapping must be empty when no identity scope is configured.
- Verify code_scope_values against code_scope_statement_types for the candidate's resolved code type. The mapping must contain the configured dimensions in configured order when statement_code is present, and must be empty when statement_code is null or the code type has no configured scope.
- Independently determine whether each identity or code scope value reflects the active structural context. Reject scope selected from incidental vocabulary in another statement, example, activity, resource, assessment, or explanatory cell.
- Resolve every configured identity-scope dimension independently. For each dimension, identify the nearest recognized source_context.scope_context_candidates value for that exact scope_statement_type and compare it with the draft value. Do not infer one dimension by locating an older occurrence of the same combination of other grouping labels.
- A draft may override the nearest recognized scope candidate only when explicit current-target evidence, clear bounded neighbor evidence, or authoritative runtime policy supports the override. Reject an unexplained jump to an older candidate, including an older value historically paired with repeated neighboring labels.
- When scope_context_candidates is empty or incomplete for a dimension, inspect source_context.section_path_recent_first directly in nearest-first order and apply the same per-dimension rule.
- Verify all scope explanations and extraction notes against the actual compact payload. Citing an absent page, heading, context direction, source text, or context candidate is a material factual error.
- Preserve surprising source placement instead of replacing it with a more intuitive subject-matter classification.

### 3. Source fidelity
- Candidate descriptions and exact description/code anchors must preserve
  source-visible wording and language. Do not translate, paraphrase, normalize, repair
  spelling, or silently invent text. Candidate source_text is a checker-approved bounded
  source-visible evidence excerpt and is persisted as returned.
- Verify every description_source_anchor and code_source_anchor against the exact
  source_unit_id exposed in the compact source window. Each anchor source_text must be
  an exact excerpt of that source unit, and occurrence_index must select the correct
  zero-based repeated occurrence. Context-only and helper-only content cannot be
  anchored.
- description_source_anchors must support the complete semantic description. Permit
  noncontiguous anchors when runtime policy requires composition from visible fragments,
  such as a shared stem plus one later list item. Do not require intervening peer items to
  be included. A coded candidate must anchor the exact raw code surface; an uncoded
  candidate must have no code anchors.
- Treat different source_unit_id values or different occurrence_index values as
  different physical source occurrences, even when candidates share a row, block, code,
  or identical wording.
- Every `source_context` field, including section_path_recent_first and preceding/following
  same-page headings, is context only unless the same wording is visible in raw target
  block/header/body content. Canonical headers and filldown helper values are also
  context only.
- Verify that a table candidate cites the raw header/body rows that visibly support it.
  Citations should be sufficient and source-grounded; do not demand artificial
  minimality when multiple rows genuinely contribute to one statement.
- Verify that visible fragments of one statement split across adjacent cells or rows
  are combined only when they belong to the same source occurrence. Preserve complete
  independently printed occurrences as separate candidates, following any more specific
  runtime occurrence or continuation instructions.

### 4. Codes and language
- Keep `statement_code` null when no official source-visible item code applies.
- When a candidate-local `code_matches` entry applies, use its `normalized_value` in
  `statement_code` and require its exact `raw_value` in the candidate's own source
  evidence. Spacing around punctuation and direct code-to-text adhesion are formatting
  variants, not reasons to leave a complete code null.
- Do not borrow a segment label, section code, table identifier, or nearby statement's
  code, and do not alter any alphanumeric code component.
- Verify each candidate language from its supporting source text; use `mul` only when
  the candidate truly combines multiple source languages.

### 5. Mandatory candidate-by-candidate audit
For every draft candidate, explicitly verify all of the following before deciding that
it passes:
- `description_source_anchors` are exact, source-ordered, and support the complete
  semantic description, including permitted noncontiguous shared-stem composition;
  `code_source_anchors` exactly support the raw code when coded and are empty when
  uncoded.
- `identity_scope_values` contains the correct configured dimensions and values for the
  candidate's active structural context, without being inferred from incidental row or
  paragraph vocabulary. For each dimension, compare the draft value with the nearest
  recognized candidate for that exact scope_statement_type and require source-supported
  override evidence before accepting any older value.
- `code_scope_values` contains the exact configured dimensions and values for the coded
  candidate's resolved code type, and is empty when the candidate is uncoded or the code
  type is document-global.
- `description` contains the complete official statement or grouping wording. Remove
  only a separately represented item code. Preserve every other visible part of an
  organizer label, including hierarchy terms, ordinal numbers, punctuation, and
  separators. Shortening a visible label such as `Sub-Strand 3: Variables and
  Equations` to only its topic wording is a material error.
- When `statement_code` is non-null, it equals the applicable candidate-local
  `code_matches[].normalized_value`, and the corresponding `code_matches[].raw_value`
  is cited exactly by this candidate's code_source_anchors and belongs to the same
  bounded source occurrence as the complete description. Immediate code-to-text
  adjacency is allowed when source-visible.
- When `statement_code` is null, check the candidate's own cited source and
  candidate-local `code_matches` for a complete compatible code. A raw code that differs
  from its normalized value only through spacing around punctuation, trailing layout
  punctuation excluded by the match, or direct adhesion to statement text must use the
  supplied `normalized_value`. Leaving such a complete code null is a material error.
  Do not repair incomplete codes or change code characters or components.
- The cited table rows or headers visibly support this candidate's description and
  exact description/code anchors, rather than only containing a nearby or parallel
  statement.
- The candidate is an SFI under runtime policy, not an exemplar, activity, note,
  resource, repeated header, or other excluded material.
- Distinct same-code/different-content source items remain separate. Independently
  printed or independently source-visible occurrences also remain separate when their
  type, code, and wording are identical; do not group them into one logical candidate.
  Combine provenance only for multiple visible fragments of the same single source
  occurrence. Apply any more specific runtime occurrence, repetition, or continuation
  instructions before this generic rule.
- Routine excluded material is not emitted as an auxiliary record when runtime policy
  says auxiliaries are reserved for genuinely ambiguous source-visible text.

Any failure in this audit that changes candidate content, code, classification,
coverage, provenance, or auxiliary output requires `passed=false`, an `error` issue,
and a complete corrected result. Candidate source_text, ordering, IDs, identity scope,
code scope, and auxiliary related-ID references are part of the accepted result and must
be corrected when materially wrong or internally inconsistent.

### 6. Ordering and identifiers
- Return candidates in source order and use unique candidate IDs.
- Ensure auxiliary `related_candidate_ids` reference those IDs correctly.
- Copy `window_id`, `window_index`, and `window_source_segment_ids` exactly from the
  source window.

## Validation verdict contract
- `issues` should contain only meaningful findings. Use severity `error` for anything
  that requires a corrected result and `warning` only for non-blocking observations.
- `passed=true`: `corrected_result` must be null and no issue may have severity `error`.
- `passed=false`: include at least one error issue and provide a complete corrected
  result that resolves all error issues.
- Explain the overall decision in a concise, source-grounded `rationale`.
- Do not return prose outside the structured verdict.
        """
    )
    user_message = dedent(
        f"""Validate this draft SFI extraction result against the compact source window.

## Compact source window JSON
{json_dumps(source_payload)}

## Draft SFIExtractionResult JSON
{draft_result.model_dump_json(exclude_none=True)}
        """
    )

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )
