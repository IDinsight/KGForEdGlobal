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
)
from skg.kgs.utils import text_starts_with_complete_marker
from skg.schemas import CreateKGConfig
from skg.utils.general import PromptPair, json_dumps


def _build_compact_block_list_item_source_units(
    extraction_window: ExtractionWindow,
) -> list[dict[str, Any]]:
    """Build source-visible units from a block's serialized list items.

    Each list item retains its own item-level language so multilingual lists are not
    flattened into the configured primary language. Items without visible source text
    are skipped.

    Parameters
    ----------
    extraction_window
        Source-faithful extraction window whose block carries the list items.

    Returns
    -------
    list[dict[str, Any]]
        Source-visible list-item units in source order.
    """

    block = extraction_window.block

    if block is None:
        return []

    list_items = block.get("list_items")

    if not (isinstance(list_items, list) and list_items):
        return []

    source_units: list[dict[str, Any]] = []

    for item_index, item in enumerate(list_items):
        if not isinstance(item, dict):
            continue

        source_text = _build_list_item_source_text(item)

        if not source_text:
            continue

        language = _get_text_unit_language(
            fallback=extraction_window.primary_language, text_unit=item.get("text")
        )
        source_units.append(
            {
                "item_index": item_index,
                "language": language,
                "marker": item.get("marker"),
                "source_text": source_text,
                "source_visibility": "source_visible_list_item",
            }
        )

    return source_units


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


def _build_compact_block_slice_source_unit(
    *,
    fallback_language: str,
    slice_index: int,
    slice_payload: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Build one source-visible unit from a serialized block slice.

    Parameters
    ----------
    fallback_language
        Language to use when the slice lacks source-language metadata.
    slice_index
        Source-order slice index within the stitched block.
    slice_payload
        Serialized DocumentIR block-slice payload.

    Returns
    -------
    Optional[dict[str, Any]]
        Prompt-facing source unit, or `None` when the slice has no visible text.
    """

    text_unit = slice_payload.get("text")

    if isinstance(text_unit, dict):
        text = str(text_unit.get("text") or "").strip()

        if text:
            return {
                "language": _get_text_unit_language(
                    fallback=fallback_language, text_unit=text_unit
                ),
                "slice_index": slice_index,
                "source_text": text,
                "source_visibility": "source_visible_block_slice",
            }

    figure = slice_payload.get("figure")

    if isinstance(figure, dict):
        for field_name in ["embedded_text", "caption"]:
            figure_text = figure.get(field_name)

            if isinstance(figure_text, dict):
                text = str(figure_text.get("text") or "").strip()

                if text:
                    return {
                        "language": _get_text_unit_language(
                            fallback=fallback_language, text_unit=figure_text
                        ),
                        "slice_index": slice_index,
                        "source_text": text,
                        "source_visibility": "source_visible_block_slice",
                    }
            elif isinstance(figure_text, str) and figure_text.strip():
                return {
                    "language": fallback_language,
                    "slice_index": slice_index,
                    "source_text": figure_text.strip(),
                    "source_visibility": "source_visible_block_slice",
                }

    return None


def _build_compact_block_slice_source_units(
    extraction_window: ExtractionWindow,
) -> list[dict[str, Any]]:
    """Build source-visible units from a block's serialized slices.

    Slices without visible text are skipped. Returns an empty list when the block has
    no slices or none of them expose source-visible text.

    Parameters
    ----------
    extraction_window
        Source-faithful extraction window whose block carries the slices.

    Returns
    -------
    list[dict[str, Any]]
        Source-visible slice units in source order.
    """

    block = extraction_window.block

    if block is None:
        return []

    slices = block.get("slices")

    if not isinstance(slices, list):
        return []

    source_units: list[dict[str, Any]] = []

    for slice_index, slice_payload in enumerate(slices):
        if not isinstance(slice_payload, dict):
            continue

        source_unit = _build_compact_block_slice_source_unit(
            fallback_language=extraction_window.primary_language,
            slice_index=slice_index,
            slice_payload=slice_payload,
        )

        if source_unit is not None:
            source_units.append(source_unit)

    return source_units


def _build_compact_block_source_units(
    extraction_window: ExtractionWindow,
) -> list[dict[str, Any]]:
    """Build source-visible block units with their source languages.

    List items retain item-level languages so multilingual lists are not flattened into
    the configured primary language. Other block kinds expose the source block text as
    one unit with the best available DocumentIR language tag.

    Parameters
    ----------
    extraction_window
        Source-faithful extraction window containing a block payload.

    Returns
    -------
    list[dict[str, Any]]
        Source-visible block units in source order.
    """

    block = extraction_window.block

    if block is None:
        return []

    list_item_source_units = _build_compact_block_list_item_source_units(
        extraction_window
    )

    if list_item_source_units:
        return list_item_source_units

    slice_source_units = _build_compact_block_slice_source_units(extraction_window)

    if slice_source_units:
        return slice_source_units

    return [
        {
            "language": _get_block_language(extraction_window),
            "source_text": extraction_window.source_text,
            "source_visibility": "source_visible_block",
        }
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
        "source_context": {
            "section_path": [
                {
                    "item_index": section_ref.get("item_index"),
                    "page_index": section_ref.get("page_index"),
                    "source_visibility": "context_only",
                    "text": section_ref.get("text"),
                }
                for section_ref in extraction_window.source_section_path
            ],
            "source_context_policy": (
                "Use section_path only to determine document scope and the source role of "
                "the visible block/table content. It is not candidate evidence, not an "
                "inferred KG ancestor chain, and must not be quoted as candidate or "
                "auxiliary source_text or description unless the same wording is also "
                "visible in block or table source content."
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
    *, header_rows: list[dict[str, Any]], n_cols: int
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
        mapped_cells = _map_raw_row_cells_to_column_ranges(
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
        "country": kg_config.metadata.country,
        "grades_or_stages": kg_config.metadata.grades_or_stages,
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
    *, cell: dict[str, Any], column_end_index_exclusive: int, column_start_index: int
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

    mapped_cells = _map_raw_row_cells_to_column_ranges(
        available_column_indexes=available_column_indexes, n_cols=n_cols, row=row
    )
    cells: list[dict[str, Any]] = []

    for cell, column_start_index, column_end_index_exclusive in mapped_cells:
        cell_payload = _build_compact_source_cell_payload(
            cell=cell,
            column_end_index_exclusive=column_end_index_exclusive,
            column_start_index=column_start_index,
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

    grid_sources_by_row = (
        table.grid_sources
        if table.grid_sources is not None
        else [None] * len(table.rows)
    )
    source_rows = [
        _build_compact_source_row_payload(
            grid_sources=grid_sources, n_cols=table.n_cols, row=row, row_index=row_index
        )
        for grid_sources, row, row_index in zip(
            grid_sources_by_row, table.rows, table.row_indexes
        )
    ]
    payload: dict[str, Any] = {
        "declared_header_row_count": table.header_row_count,
        "header_rows": _build_compact_header_rows(
            header_rows=table.header_rows, n_cols=table.n_cols
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
            "rather than assuming every source_rows entry is ordinary data. Quote "
            "source_text only from block.source_text, table.header_rows cell text, "
            "or table.source_rows cell text. For table-derived SFI candidates, "
            "description must be copied from the same cited table.header_rows and/or "
            "table.source_rows text. If unsure, set description equal to source_text. "
            "Do not clean, translate, correct spelling, normalize, expand, or infer "
            "table descriptions from surrounding context. If an official table "
            "statement is split across adjacent source rows or cells, use all visible "
            "contributing fragments and include all contributing table_row_indexes. "
            "Use rowspan_context_rows and filldown_context_rows only to understand "
            "structural carryover; never quote helper_context_only rows as source_text "
            "or description."
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


def _get_block_language(extraction_window: ExtractionWindow) -> str:
    """Return the best available language for a non-list block window.

    The block-level TextUnit is authoritative when present. Figure embedded text or
    caption language is used for figure windows. The KG primary language is only a
    fallback when DocumentIR has no source-language metadata.

    Parameters
    ----------
    extraction_window
        Source-faithful extraction window containing an optional block payload.

    Returns
    -------
    str
        Source block language when available; otherwise the window primary language.
    """

    block = extraction_window.block

    if block is None:
        return extraction_window.primary_language

    language = _get_text_unit_language(fallback="", text_unit=block.get("text"))

    if language:
        return language

    figure = block.get("figure")

    if isinstance(figure, dict):
        for field_name in ["embedded_text", "caption"]:
            language = _get_text_unit_language(
                fallback="", text_unit=figure.get(field_name)
            )

            if language:
                return language

    block_language = block.get("language")

    if isinstance(block_language, str) and block_language.strip():
        return block_language.strip()

    return extraction_window.primary_language


def _get_text_unit_language(*, fallback: str, text_unit: Any) -> str:
    """Return a TextUnit language or a configured fallback.

    Parameters
    ----------
    fallback
        Language to use when the text unit has no usable language tag.
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


def _map_raw_row_cells_to_column_ranges(
    *, available_column_indexes: set[int], n_cols: int, row: dict[str, Any]
) -> list[tuple[dict[str, Any], int, int]]:
    """Map raw table cells to their true span-expanded grid column ranges.

    Synthetic row-span placeholders are omitted because they are represented separately
    as helper-only inherited row-span context. Empty source cells still participate in
    placement so following visible cells retain correct columns.

    Parameters
    ----------
    available_column_indexes
        Grid columns originating from the current source row.
    n_cols
        Total table grid width.
    row
        Raw serialized table row.

    Returns
    -------
    list[tuple[dict[str, Any], int, int]]
        Raw non-placeholder cells with inclusive start and exclusive end columns.

    Raises
    ------
    ValueError
        If no contiguous range can represent the raw cell within the supplied grid.
    """

    column_cursor = 0
    mapped_cells: list[tuple[dict[str, Any], int, int]] = []

    for cell in row.get("cells") or []:
        if cell.get("rowspan_placeholder"):
            continue

        col_span = max(1, int(cell.get("col_span") or 1))

        # Find the next contiguous available column range.
        for column_start_index in range(column_cursor, n_cols - col_span + 1):
            column_end_index_exclusive = column_start_index + col_span

            if available_column_indexes.issuperset(
                range(column_start_index, column_end_index_exclusive)
            ):
                mapped_cells.append(
                    (cell, column_start_index, column_end_index_exclusive)
                )
                column_cursor = column_end_index_exclusive
                break
        else:
            # If the loop finishes without breaking, a valid range was not found.
            raise ValueError(
                f"Could not place a raw table cell in the compact structural grid: "
                f"column_cursor={column_cursor}, col_span={col_span}, n_cols={n_cols}, "
                f"available_columns={sorted(available_column_indexes)}."
            )

    return mapped_cells


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
- Use source_context.section_path to determine document scope and the source role of the visible block/table content when the runtime config depends on section context. Treat it as context only, not as candidate evidence or an inferred hierarchy.
- Return zero SFI candidates when the window contains front matter, examples only, teacher guidance only, activities only, resources only, assessment suggestions only, or unrelated content.
- Do not infer hierarchy or relationships in this step. Extract only SFI candidates directly visible in this compact source window; final hasChild relationships are resolved later from finalized SFIs and source provenance.
- Extract grouping SFIs only when the grouping label itself is visible in block or table source content. Do not emit a grouping solely because it appears in source_context.section_path, and do not add absent grade, strand, sub-strand, or parent context.
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

## Candidate field policy
- Return sfi_candidates in source order and assign candidate_id exactly by list position: sfi_1, sfi_2, through sfi_N with no gaps, alternate prefixes, or reordered numbers. For block content, order candidates by the first source-visible text that contributes to each candidate. For tables, order raw header-derived candidates before body-row-derived candidates, then follow table.source_rows order and left-to-right cell/text order within each row. Candidates sharing the same exact source location may use either stable relative order.
- Preserve source occurrences during extraction; do not perform logical deduplication. Each independently printed or otherwise independently source-visible SFI occurrence must remain a separate candidate, even when another occurrence has the same statement type, code, description, normalized wording, or apparent logical identity.
- Combine source locations into one candidate only when they are visible fragments of one source occurrence, such as a single statement continuing across adjacent cells, rows, slices, or headers. Do not combine complete independently printed occurrences merely because they repeat, overlap semantically, or are expected to merge in a later stage.
- Apply the runtime `sfi_extraction_instructions` whenever they define more specific occurrence, continuation, splitting, or repetition behavior. If a generic example or heuristic conflicts with those instructions, the runtime instructions take precedence.
- Leave logical identity resolution and merging of repeated source occurrences to the downstream SFI deduplication stage. The same code with materially different source-visible wording must also remain separate.
- description should preserve the complete exact source-language wording of the SFI. For learning expectations, use the full official statement text. For groupings, preserve the complete grouping label or heading text, including visible hierarchy terms, ordinal numbers, punctuation, and separators such as "Grade", "Strand", "Sub-Strand", "3", and ":". Remove only a separately represented item identifier code. Do not mistake a hierarchy label or ordinal organizer prefix for a code. When a visible code functions as a separate item identifier, exclude that code from description and place its normalized form only in statement_code while preserving its raw source form in source_text. Removing a separately represented identifier is not a wording correction. When multiple explicit labeled fields appear on one physical line, such as one label-value field followed by another label-value field, treat each complete visible field as a separately bounded source span and preserve their source order. Do not clean, translate, correct spelling, normalize, expand, infer, or truncate the actual statement wording.
- statement_type must use exactly one canonical source-facing role from statement_type_policy.
- statement_code must use the candidate-local code_matches[].normalized_value whose code_type matches statement_type_policy.code_type for the candidate. The matching code_matches[].raw_value must be directly paired with the complete candidate description in the candidate's own source_text and cited source locations. Formatting normalization may remove whitespace around punctuation or separate a complete code printed immediately adjacent to statement text, but it must never change a code prefix, alphanumeric character, delimiter, or numeric component. Treat block.local_code and table.local_code as context only unless the same candidate-local code is also exposed by code_matches. When statement_type_policy.code_type is null, emit a code only when exactly one candidate-local code_match is unambiguous. Use null for nearby codes, segment labels, table identifiers that are not item codes, ambiguous or incomplete codes, codes not visible in the candidate's own source_text, or codes that belong to another statement. Do not leave statement_code null solely because code_matches[].raw_value contains spacing around punctuation or is glued to the statement text.
- language must match the source language tag on the exact block.source_units or table cells that support description/source_text. Use "mul" when the candidate combines visible text from more than one source language. Use the KG config primary language only when the supporting source units have no language metadata.
- confidence should reflect how clearly the source window supports the candidate.
- table_header_indexes and table_row_indexes are source-text location fields, not general context fields.
- For table candidates, both description and source_text must be supported by the cited table_header_indexes and/or table_row_indexes.
- Populate table_header_indexes only when the candidate source_text or description is quoted from table.header_rows.
- Populate table_row_indexes only when the candidate source_text or description is quoted from table.source_rows.
- Do not include table_header_indexes merely because a row appears under a relevant column header such as Content Standard or Indicators and Exemplars; use the header text as classification context only.
- Include both table_header_indexes and table_row_indexes only when the candidate source_text visibly includes quoted text from both table.header_rows and table.source_rows.
- description should contain the complete source-visible SFI statement or grouping label, including visible continuation fragments when an official statement is split across adjacent table rows or cells.
- source_text is a source-visible evidence quote for validation. It is not the final canonical KG statement text and it is not the only downstream provenance.
- For every SFI candidate, source_text must be a contiguous excerpt of description, or contain the complete description with tightly bounded visible source context. Do not use an unrelated quote from another cited row or source unit.
- Whenever statement_code is non-null, source_text must contain the corresponding code_matches[].raw_value directly paired with the complete description, while statement_code contains code_matches[].normalized_value. Preserve the source's spacing or lack of spacing between the raw code and statement text. Use only "raw code + complete description" or "complete description + raw code" as visibly presented in the source. Keep the separately represented code out of description. Do not include another statement, another code, a segment label, a table identifier, or unrelated surrounding text merely to make a code visible.
- Keep source_text concise but sufficient. For coded table statements, quote only the official code and complete statement text, not examples, exemplars, teacher guidance, activities, competencies, or neighboring statements. When a statement is split across multiple visible rows/cells, quote the complete visible statement only if the contributing fragments can be represented as a source-visible excerpt; otherwise omit statement_code and quote the strongest exact visible fragment while relying on table_row_indexes/table_header_indexes for downstream source recovery.
- For table candidates, description may equal source_text only when source_text contains the complete official statement and no separately represented identifier code or unrelated context. When source_text includes a separate item code, description must contain only the complete official statement wording. A description may also be a complete source-visible cell, contiguous cell range, bounded clause, or statement assembled from adjacent cited cells or adjacent cited rows. Do not create a description by deleting, interleaving, or truncating words from the actual statement wording.

## Source fidelity rules
- Preserve source-language text. Do not translate. Use block.source_units and table-cell language fields to assign candidate and auxiliary language accurately.
- For a non-list stitched block, block.source_text is the complete source-visible logical block and may contain one statement continuing across multiple block.source_units/slices.
- For every candidate and auxiliary record, source_text must be a verbatim source-visible excerpt from block.source_text/block.source_units, table.header_rows cell text, or table.source_rows cell text. Never quote source_context.section_path or helper-only filldown context.
- For table candidates, description must be source-visible in the cited table_header_indexes and/or table_row_indexes. Cite exactly the raw header/body rows that contribute to the candidate description and source_text; do not include unrelated context rows. Do not use text from another visible row/header unless that row/header index is also cited.
- If a table statement visibly continues across adjacent source rows or cells, include every contributing table_row_index and assemble the complete official statement in description from those visible fragments. Do not truncate description at the first row/cell.
- The final KG-building stages recover full source provenance from window_id, window_source_segment_ids, table_row_indexes, table_header_indexes, and the persisted ExtractionWindow/DocumentIR. Do not use source_text to carry hidden context, parentage, or non-visible text.
- Use code_matches as typed source evidence, not as final KG nodes. Assign code_matches[].normalized_value to statement_code only when the corresponding code_matches[].raw_value is directly paired with that candidate's complete description in source_text and cited source locations. Immediate adjacency between a complete raw code and the first character of statement text is valid pairing when that is how the source is printed.
- Treat block.local_code and table.local_code as context that can help locate the relevant candidate-local code_match, not as an independent source of statement_code. Do not automatically copy a table identifier or segment label into statement_code. When the same normalized code is visibly repeated for distinct source items, multiple candidates may preserve it only when each candidate's own source_text and exact cited source locations independently contain the corresponding raw code directly paired with its complete description.
- Table headers are source-visible structural evidence. When the curriculum-specific KG config says a table-header label is an official grouping SFI, extract it as a Standard Grouping candidate.
- For table-row-derived SFI candidates, table_row_indexes must be non-empty and must use the visible table.source_rows[].row_index values containing the quoted candidate source_text.
- For table-header-derived SFI candidates, table_header_indexes must be non-empty and must use the visible table.header_rows[].header_row_index values containing the quoted candidate source_text; table_row_indexes should be empty unless the quoted source_text also includes visible text from table.source_rows.
- Do not cite a table header row as source evidence when the header only explains the meaning of a body-row column. In that case, use the header as classification context and cite only the body row indexes that contain the candidate source_text.
- If a table candidate's quoted source_text includes visible text from both header rows and body rows, include both table_header_indexes and table_row_indexes; otherwise cite only the header rows or body rows that contain the quoted source_text.
- Treat table.filldown_context_rows as helper context only. These cells repeat row-span context for interpretation, but they are not source-visible evidence. Do not quote helper_context_only cells as candidate source_text or auxiliary source_text unless the same text is also visible in block.source_text, table.header_rows, or table.source_rows.

## Output contract
Copy window_id, window_index, and window_source_segment_ids exactly from the compact source window. Return sfi_candidates in nondecreasing source order with candidate_id values exactly matching their 1-based list positions.
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
- When multiple parent candidates share the same normalized code or otherwise look plausible by topic, disambiguate using source-local hierarchy evidence before semantic similarity. Prefer the candidate supported by the child's actual source row/span, active local outline, same source context key, same source segment/window, and same table context over a same-code candidate whose description merely seems more topically related.
- For table-derived children, table row/span context is strong hierarchy evidence. If a parent statement is visible in a table row, row-spanned/continued into the child's row, or represented by the child's source_context_key/table context, prefer that source-local parent. Do not select a different same-code or same-topic parent when the child's cited table rows and local table context point to another parent.
- Treat topical or semantic similarity as weaker than source-local table/context evidence when resolving duplicate-code or repeated-label candidates.
- Page overlap alone is weak evidence and must not override stronger source hierarchy evidence.
- DocumentIR section-path labels are evidence, not a guaranteed clean ancestor chain.
- In each child_context, `section_path_labels` is ordered from most recent/local source context to older/broader context after bounded truncation. Earlier labels in that list are usually more useful for direct parent selection; later labels may be stale carryover and should be treated cautiously.
- `active_outline_stack_parent`, `nearest_preceding_grouping`, `nearby_source_context_key`, and `matched_section_path_label` evidence are carry-forward retrieval signals unless they are also supported by hard local evidence such as `same_table_immediate_parent`, `same_table_context`, `source_scope_grouping`, or `code_parent_hint`.
- `active_outline_stack_parent` evidence means source-order scanning of finalized SFIs found the candidate as the active immediate parent type under the configured statement-type hierarchy. This is a strong candidate-preservation signal for same-page or same-window headings, but it is still not automatic truth; confirm against the child context, parent context, runtime hierarchy instructions, codes, and source locality.
- Source-visible hierarchy outranks inferred code hierarchy when they conflict. Codes are strong evidence, but source-visible table rows/spans, continuation rows, active local outline headings, and local section-path evidence are stronger when they identify a direct parent of the correct type.
- `source_visible_direct_parent` is a strong source-local signal, not an absolute veto. It should normally beat root fallback, same-topic fallback, page/window proximity, broad semantic similarity, and stale carry-forward evidence.
- Do not choose the StandardsFramework root or mark a child unresolved solely because the exact code-implied parent is missing when a supplied non-root candidate has `source_visible_direct_parent` evidence and the correct direct parent type.
- For table-derived children, a same-row, row-spanned, or continued-row parent candidate with `source_visible_direct_parent`, `same_table_immediate_parent`, or `same_table_context` evidence should beat a nearby or semantically related candidate that lacks that source-local table evidence.
- For coded children with hierarchical codes, avoid sibling fallback based only on code or topic. However, if a supplied non-root parent candidate has exact code-parent evidence, a direct hierarchical code-prefix match, or otherwise strong source-local direct-parent evidence, you may select it over a `source_visible_direct_parent` candidate that points to a wrong topic, wrong code scope, wrong strand/grade, or stale carried-over source context. Explain why the visible candidate is not the true direct parent.
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
- A coded merge may include an uncoded duplicate occurrence only when every candidate has the same applicable_code_type and the same code_scope_key/code_scope_values. If any candidate has a different applicable code type, contradictory scope, or unresolved scope while another candidate is scoped, use keep_separate, conflict, or needs_review.

## Compact evidence model
- review_signals are deterministic retrieval evidence. Each signal applies only to its listed candidate_ids; never assume it applies to the entire connected review set.
- A same_normalized_source_text signal means the listed subset has exact equality after internal normalization. It is not an automatic merge rule, and normalized text itself is intentionally omitted from the prompt.
- A canonical-value, code-bucket, text-bucket, warning, or shared-table-location signal is also review evidence rather than a merge decision.
- context_windows are shared nearby source windows. Each candidate's context_window_indexes identifies which windows are relevant to that candidate.
- context_items contain only runtime-configured context-bearing statement types. Their absence does not prove that no other source content exists.
- section_labels, boundary_markers, page_indexes, and source_text_excerpt are compact, fallible context. They are not resolved hierarchy or parentage.
- candidate description and source_text preserve the source-facing evidence. Use them rather than guessing omitted normalized forms or internal bucket keys.

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
- context_windows are shared nearby windows. Use each candidate's context_window_indexes to locate the context relevant to that candidate.
- context_items contain only runtime-configured context-bearing statement types. Their absence is not proof that no other source material exists.
- section_labels, boundary_markers, page indexes, and excerpts are compact and fallible. They are not final hierarchy, parentage, or merge identity.
- Candidate description, source_text, code fields, statement types, table indexes, canonical values, and runtime instructions are the primary evidence.
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
12. For every coded merge, verify that every candidate has the canonical applicable_code_type and the same resolved code_scope_key/code_scope_values. An uncoded occurrence may merge only when it independently preserves that same type and scope.
13. Treat representative_candidate_id, canonical_type_source_candidate_id, and canonical_code_source_candidate_id as independent selections; they may identify different candidates.
14. Use needs_review only when the bounded evidence remains genuinely insufficient after applying the runtime instructions.

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
        "country": kg_config.metadata.country,
        "grades_or_stages": kg_config.metadata.grades_or_stages,
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
  types, codes, languages, source text, source locations, auxiliary records, notes,
  ordering, and IDs when the source and runtime policy require it.
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

### 3. Source fidelity
- Candidate descriptions and `source_text` must preserve source-visible wording and
  language. Do not translate, paraphrase, normalize, repair spelling, or silently add
  missing text.
- `source_context.section_path`, canonical headers, and filldown helper values are
  context only unless the same wording is visible in raw block/header/body content.
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
- `description` contains the complete official statement or grouping wording. Remove
  only a separately represented item code. Preserve every other visible part of an
  organizer label, including hierarchy terms, ordinal numbers, punctuation, and
  separators. Shortening a visible label such as `Sub-Strand 3: Variables and
  Equations` to only its topic wording is a material error.
- When `statement_code` is non-null, it equals the applicable candidate-local
  `code_matches[].normalized_value`, and the corresponding `code_matches[].raw_value`
  is visibly paired with this candidate's complete description in its own `source_text`
  and cited source locations. Immediate code-to-text adjacency is allowed when
  source-visible.
- When `statement_code` is null, check the candidate's own cited source and
  candidate-local `code_matches` for a complete compatible code. A raw code that differs
  from its normalized value only through spacing around punctuation, trailing layout
  punctuation excluded by the match, or direct adhesion to statement text must use the
  supplied `normalized_value`. Leaving such a complete code null is a material error.
  Do not repair incomplete codes or change code characters or components.
- The cited table rows or headers visibly support this candidate's description and
  evidence quote, rather than only containing a nearby or parallel statement.
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
and a complete corrected result.

### 6. Ordering and identifiers
- Return candidates in source order.
- In any corrected result, renumber candidates exactly as `sfi_1` through `sfi_N` after
  all additions, removals, splits, merges, and reordering.
- Update auxiliary `related_candidate_ids` to the corrected IDs.
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
