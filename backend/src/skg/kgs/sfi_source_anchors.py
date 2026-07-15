"""This module builds and validates exact source units used by SFI candidate anchors.

The functions in this module translate one persisted `ExtractionWindow` into stable,
curriculum-neutral source units. Extraction prompts expose those units to the LLM,
while deterministic validators resolve returned anchors against the same units. This
keeps physical source-occurrence identity independent of extraction-window overlap,
candidate classification, and curriculum-specific semantics.
"""

# Standard Library
from dataclasses import dataclass
from typing import Any, Optional, Sequence

# Package Library
from skg.kgs.schemas import ExtractionWindow, SFISourceAnchor, SFISourceUnitKind
from skg.kgs.utils import text_starts_with_complete_marker


@dataclass(frozen=True)
class SFISourceUnit:
    """One exact source-visible unit that an SFI candidate may cite.

    Attributes
    ----------
    language
        Best available source-language tag for the visible unit.
    source_locator
        Compact human-readable structural coordinates for prompt review and audit.
    source_order
        Deterministic source-order key used to validate ordered candidate anchors.
    source_text
        Complete source-visible text of the unit.
    source_unit_id
        Stable structural identifier independent of extraction-window identity.
    source_unit_kind
        Closed structural kind for the source unit.
    """

    language: str
    source_locator: dict[str, Any]
    source_order: tuple[int, int, int, int]
    source_text: str
    source_unit_id: str
    source_unit_kind: SFISourceUnitKind

    def to_prompt_payload(self) -> dict[str, Any]:
        """Serialize the source unit for an extraction prompt.

        Returns
        -------
        dict[str, Any]
            JSON-serializable prompt payload containing stable source coordinates.
        """

        return {
            "language": self.language,
            "source_locator": self.source_locator,
            "source_text": self.source_text,
            "source_unit_id": self.source_unit_id,
            "source_unit_kind": self.source_unit_kind,
            "source_visibility": "source_visible_unit",
        }


def _build_block_figure_source_unit(
    *,
    fallback_language: str,
    figure: dict[str, Any],
    slice_index: int,
    source_segment_id: str,
) -> Optional[SFISourceUnit]:
    """Build the first available source unit for one block figure.

    The figure's embedded text is preferred over its caption; the first field that
    yields a visible source unit wins.

    Parameters
    ----------
    fallback_language
        Language used when a figure text payload has no language tag.
    figure
        Serialized DocumentIR figure payload.
    slice_index
        Index of the slice that owns the figure.
    source_segment_id
        Stable DocumentIR segment identifier.

    Returns
    -------
    Optional[SFISourceUnit]
        Source unit for the first field with visible text, or `None`.
    """

    for field_name, source_unit_kind in [
        ("embedded_text", "figure_embedded_text"),
        ("caption", "figure_caption"),
    ]:
        figure_text_payload = figure.get(field_name)

        if isinstance(figure_text_payload, dict):
            figure_source_text = str(figure_text_payload.get("text") or "")
        elif figure_text_payload is None:
            figure_source_text = ""
        else:
            figure_source_text = str(figure_text_payload)

        source_unit = _build_text_source_unit(
            fallback_language=fallback_language,
            source_locator={"figure_field": field_name, "slice_index": slice_index},
            source_order=(0, slice_index, 0, 0),
            source_segment_id=source_segment_id,
            source_text=figure_source_text,
            source_unit_index=slice_index,
            source_unit_kind=source_unit_kind,
            text_payload=figure_text_payload,
        )

        if source_unit is not None:
            return source_unit

    return None


def _build_block_list_item_source_text(item: dict[str, Any]) -> str:
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
    text_payload = item.get("text")

    if isinstance(text_payload, dict):
        text = str(text_payload.get("text") or "").strip()
    elif text_payload is None:
        text = ""
    else:
        text = str(text_payload).strip()

    if marker and text and text_starts_with_complete_marker(marker=marker, text=text):
        return text

    return " ".join(value for value in [marker, text] if value)


def _build_block_slice_source_unit(
    *,
    fallback_language: str,
    slice_index: int,
    slice_payload: dict[str, Any],
    source_segment_id: str,
) -> Optional[SFISourceUnit]:
    """Build one source unit for a single block slice.

    Slice text is preferred; when absent, the slice's figure text (if any) is used
    instead.

    Parameters
    ----------
    fallback_language
        Language used when a slice text payload has no language tag.
    slice_index
        Index of the slice within the block.
    slice_payload
        Serialized DocumentIR slice payload.
    source_segment_id
        Stable DocumentIR segment identifier.

    Returns
    -------
    Optional[SFISourceUnit]
        Source unit for the slice, or `None` when it has no visible text.
    """

    text_payload = slice_payload.get("text")

    if isinstance(text_payload, dict):
        source_unit = _build_text_source_unit(
            fallback_language=fallback_language,
            source_locator={"slice_index": slice_index},
            source_order=(0, slice_index, 0, 0),
            source_segment_id=source_segment_id,
            source_text=str(text_payload.get("text") or ""),
            source_unit_index=slice_index,
            source_unit_kind="block_slice_text",
            text_payload=text_payload,
        )

        if source_unit is not None:
            return source_unit

    figure = slice_payload.get("figure")

    if not isinstance(figure, dict):
        return None

    return _build_block_figure_source_unit(
        fallback_language=fallback_language,
        figure=figure,
        slice_index=slice_index,
        source_segment_id=source_segment_id,
    )


def _build_text_source_unit(
    *,
    fallback_language: str,
    source_locator: dict[str, Any],
    source_order: tuple[int, int, int, int],
    source_segment_id: str,
    source_text: str,
    source_unit_index: Optional[int],
    source_unit_kind: SFISourceUnitKind,
    text_payload: Any,
) -> Optional[SFISourceUnit]:
    """Build one text-bearing source unit when visible text exists.

    Parameters
    ----------
    fallback_language
        Language used when the serialized text payload has no language tag.
    source_locator
        Human-readable structural coordinates.
    source_order
        Deterministic source-order key.
    source_segment_id
        Stable DocumentIR segment identifier.
    source_text
        Source-visible text for the unit.
    source_unit_index
        Stable block unit index, when applicable.
    source_unit_kind
        Structural source-unit kind.
    text_payload
        Serialized text payload used to recover source language.

    Returns
    -------
    Optional[SFISourceUnit]
        Source unit, or `None` when no visible text exists.
    """

    text = str(source_text or "").strip()

    if not text:
        return None

    return SFISourceUnit(
        language=_get_text_unit_language(
            fallback=fallback_language, text_payload=text_payload
        ),
        source_locator=source_locator,
        source_order=source_order,
        source_text=text,
        source_unit_id=build_sfi_source_unit_id(
            column_end_index_exclusive=None,
            column_start_index=None,
            row_index=None,
            source_segment_id=source_segment_id,
            source_unit_index=source_unit_index,
            source_unit_kind=source_unit_kind,
        ),
        source_unit_kind=source_unit_kind,
    )


def _get_single_source_segment_id(extraction_window: ExtractionWindow) -> str:
    """Return the single DocumentIR segment represented by an extraction window.

    Parameters
    ----------
    extraction_window
        Source-faithful extraction window.

    Returns
    -------
    str
        The sole source segment identifier.

    Raises
    ------
    ValueError
        If the extraction window represents zero or multiple source segments.
    """

    source_segment_ids = list(extraction_window.source_segment_ids)

    if len(source_segment_ids) != 1:
        raise ValueError(
            f"Exact SFI source anchors require each extraction window to represent "
            f"exactly one DocumentIR segment; got {source_segment_ids!r}."
        )

    return source_segment_ids[0]


def _get_text_unit_language(*, fallback: str, text_payload: Any) -> str:
    """Return a serialized text unit's language or a fallback.

    Parameters
    ----------
    fallback
        Language used when no source-language tag is present.
    text_payload
        Serialized text payload.

    Returns
    -------
    str
        Source language when available, otherwise `fallback`.
    """

    if isinstance(text_payload, dict):
        language = text_payload.get("language")

        if isinstance(language, str) and language.strip():
            return language.strip()

    return fallback


def _iter_block_list_item_source_units(
    *, fallback_language: str, list_items: list[Any], source_segment_id: str
) -> list[SFISourceUnit]:
    """Build source-visible units for a block's list items.

    Parameters
    ----------
    fallback_language
        Language used when a list-item text payload has no language tag.
    list_items
        Serialized DocumentIR list-item payloads.
    source_segment_id
        Stable DocumentIR segment identifier.

    Returns
    -------
    list[SFISourceUnit]
        Source-visible list-item units in source order.
    """

    source_units: list[SFISourceUnit] = []

    for item_index, item in enumerate(list_items):
        if not isinstance(item, dict):
            continue

        source_unit = _build_text_source_unit(
            fallback_language=fallback_language,
            source_locator={"item_index": item_index},
            source_order=(0, item_index, 0, 0),
            source_segment_id=source_segment_id,
            source_text=_build_block_list_item_source_text(item),
            source_unit_index=item_index,
            source_unit_kind="block_list_item",
            text_payload=item.get("text"),
        )

        if source_unit is not None:
            source_units.append(source_unit)

    return source_units


def _iter_block_slice_source_units(
    *, fallback_language: str, slices: list[Any], source_segment_id: str
) -> list[SFISourceUnit]:
    """Build source-visible units for a block's slices.

    Parameters
    ----------
    fallback_language
        Language used when a slice text payload has no language tag.
    slices
        Serialized DocumentIR slice payloads.
    source_segment_id
        Stable DocumentIR segment identifier.

    Returns
    -------
    list[SFISourceUnit]
        Source-visible slice units in source order.
    """

    source_units: list[SFISourceUnit] = []

    for slice_index, slice_payload in enumerate(slices):
        if not isinstance(slice_payload, dict):
            continue

        source_unit = _build_block_slice_source_unit(
            fallback_language=fallback_language,
            slice_index=slice_index,
            slice_payload=slice_payload,
            source_segment_id=source_segment_id,
        )

        if source_unit is not None:
            source_units.append(source_unit)

    return source_units


def _iter_block_source_units(
    extraction_window: ExtractionWindow,
) -> list[SFISourceUnit]:
    """Build stable source-visible units for one block extraction window.

    Units are taken from the first non-empty source among the block's list items, its
    slices, and finally the whole-block text fallback.

    Parameters
    ----------
    extraction_window
        Block extraction window.

    Returns
    -------
    list[SFISourceUnit]
        Source-visible block units in deterministic source order.
    """

    block = extraction_window.block

    if block is None:
        return []

    source_segment_id = _get_single_source_segment_id(extraction_window)
    fallback_language = extraction_window.primary_language
    list_items = block.get("list_items")

    if isinstance(list_items, list) and list_items:
        source_units = _iter_block_list_item_source_units(
            fallback_language=fallback_language,
            list_items=list_items,
            source_segment_id=source_segment_id,
        )

        if source_units:
            return source_units

    slices = block.get("slices")

    if isinstance(slices, list):
        source_units = _iter_block_slice_source_units(
            fallback_language=fallback_language,
            slices=slices,
            source_segment_id=source_segment_id,
        )

        if source_units:
            return source_units

    fallback_source_unit = _build_text_source_unit(
        fallback_language=fallback_language,
        source_locator={"block_unit_index": 0},
        source_order=(0, 0, 0, 0),
        source_segment_id=source_segment_id,
        source_text=extraction_window.source_text,
        source_unit_index=0,
        source_unit_kind="block_text",
        text_payload=block.get("text"),
    )
    return [fallback_source_unit] if fallback_source_unit is not None else []


def _iter_table_body_source_units(
    extraction_window: ExtractionWindow,
) -> list[SFISourceUnit]:
    """Build stable source-visible units for selected table body cells.

    Parameters
    ----------
    extraction_window
        Table extraction window.

    Returns
    -------
    list[SFISourceUnit]
        Source-visible body-cell units in source order.
    """

    table = extraction_window.table

    if table is None:
        return []

    _validate_table_body_row_placement(extraction_window)
    source_segment_id = _get_single_source_segment_id(extraction_window)
    grid_sources_by_row: Sequence[Optional[list[dict[str, Any]]]] = (
        table.grid_sources
        if table.grid_sources is not None
        else [None] * len(table.rows)
    )
    source_units: list[SFISourceUnit] = []

    for grid_sources, row, row_index in zip(
        grid_sources_by_row, table.rows, table.row_indexes
    ):
        if grid_sources is None:
            available_column_indexes = set(range(table.n_cols))
        else:
            available_column_indexes = {
                column_index
                for column_index, source_info in enumerate(grid_sources)
                if source_info.get("source_row") == row_index
            }

        mapped_cells = map_raw_row_cells_to_column_ranges(
            available_column_indexes=available_column_indexes,
            n_cols=table.n_cols,
            row=row,
        )

        for cell, column_start_index, column_end_index_exclusive in mapped_cells:
            text_payload = cell.get("text") or {}
            source_text = (
                str(text_payload.get("text") or "")
                if isinstance(text_payload, dict)
                else str(text_payload or "")
            ).strip()

            if not source_text:
                continue

            source_units.append(
                SFISourceUnit(
                    language=_get_text_unit_language(
                        fallback=extraction_window.primary_language,
                        text_payload=text_payload,
                    ),
                    source_locator={
                        "column_range": [
                            column_start_index,
                            column_end_index_exclusive,
                        ],
                        "row_index": row_index,
                    },
                    source_order=(1, row_index, column_start_index, 0),
                    source_text=source_text,
                    source_unit_id=build_sfi_source_unit_id(
                        column_end_index_exclusive=column_end_index_exclusive,
                        column_start_index=column_start_index,
                        row_index=row_index,
                        source_segment_id=source_segment_id,
                        source_unit_index=None,
                        source_unit_kind="table_body_cell",
                    ),
                    source_unit_kind="table_body_cell",
                )
            )

    return source_units


def _iter_table_header_source_units(
    extraction_window: ExtractionWindow,
) -> list[SFISourceUnit]:
    """Build stable source-visible units for table header cells.

    Parameters
    ----------
    extraction_window
        Table extraction window.

    Returns
    -------
    list[SFISourceUnit]
        Source-visible header-cell units in source order.
    """

    table = extraction_window.table

    if table is None:
        return []

    source_segment_id = _get_single_source_segment_id(extraction_window)
    active_rowspans = [0] * table.n_cols
    source_units: list[SFISourceUnit] = []

    for header_row_index, header_row in enumerate(table.header_rows):
        available_column_indexes = {
            column_index
            for column_index, remaining_rows in enumerate(active_rowspans)
            if remaining_rows == 0
        }
        mapped_cells = map_raw_row_cells_to_column_ranges(
            available_column_indexes=available_column_indexes,
            n_cols=table.n_cols,
            row=header_row,
        )
        next_active_rowspans = [
            max(0, remaining_rows - 1) for remaining_rows in active_rowspans
        ]

        for cell, column_start_index, column_end_index_exclusive in mapped_cells:
            text_payload = cell.get("text") or {}
            source_text = (
                str(text_payload.get("text") or "")
                if isinstance(text_payload, dict)
                else str(text_payload or "")
            ).strip()

            if source_text:
                source_units.append(
                    SFISourceUnit(
                        language=_get_text_unit_language(
                            fallback=extraction_window.primary_language,
                            text_payload=text_payload,
                        ),
                        source_locator={
                            "column_range": [
                                column_start_index,
                                column_end_index_exclusive,
                            ],
                            "header_row_index": header_row_index,
                        },
                        source_order=(0, header_row_index, column_start_index, 0),
                        source_text=source_text,
                        source_unit_id=build_sfi_source_unit_id(
                            column_end_index_exclusive=column_end_index_exclusive,
                            column_start_index=column_start_index,
                            row_index=header_row_index,
                            source_segment_id=source_segment_id,
                            source_unit_index=None,
                            source_unit_kind="table_header_cell",
                        ),
                        source_unit_kind="table_header_cell",
                    )
                )

            row_span = max(1, int(cell.get("row_span") or 1))

            if row_span > 1:
                for column_index in range(
                    column_start_index, column_end_index_exclusive
                ):
                    next_active_rowspans[column_index] = max(
                        next_active_rowspans[column_index], row_span - 1
                    )

        active_rowspans = next_active_rowspans

    return source_units


def _validate_table_body_row_placement(extraction_window: ExtractionWindow) -> None:
    """Reject body-cell placement that is ambiguous without grid-source metadata.

    Parameters
    ----------
    extraction_window
        Table extraction window to validate.

    Raises
    ------
    ValueError
        If a selected row contains row-span structure but `grid_sources` is absent.
    """

    table = extraction_window.table

    if table is None or table.grid_sources is not None:
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
            f"Cannot build exact SFI source units without grid_sources because "
            f"selected body rows contain row-span structure at source row indexes "
            f"{unsafe_row_indexes}."
        )


def build_sfi_source_unit_id(
    *,
    column_end_index_exclusive: Optional[int],
    column_start_index: Optional[int],
    row_index: Optional[int],
    source_segment_id: str,
    source_unit_index: Optional[int],
    source_unit_kind: SFISourceUnitKind,
) -> str:
    """Build a stable readable identifier for one source-visible unit.

    Parameters
    ----------
    column_end_index_exclusive
        Exclusive table-grid ending column for a table cell, when applicable.
    column_start_index
        Inclusive table-grid starting column for a table cell, when applicable.
    row_index
        Source header/body row index for a table cell, when applicable.
    source_segment_id
        Stable DocumentIR segment identifier.
    source_unit_index
        Stable block list-item or slice index, when applicable.
    source_unit_kind
        Structural source-unit kind.

    Returns
    -------
    str
        Stable source-unit identifier independent of extraction-window identity.
    """

    parts = [source_segment_id, source_unit_kind]

    if source_unit_index is not None:
        parts.append(f"unit={source_unit_index}")

    if row_index is not None:
        parts.append(f"row={row_index}")

    if column_start_index is not None and column_end_index_exclusive is not None:
        parts.append(f"columns={column_start_index}:{column_end_index_exclusive}")

    return "|".join(parts)


def build_sfi_source_unit_map(
    extraction_window: ExtractionWindow,
) -> dict[str, SFISourceUnit]:
    """Build the exact source-unit map for one extraction window.

    Parameters
    ----------
    extraction_window
        Source-faithful extraction window.

    Returns
    -------
    dict[str, SFISourceUnit]
        Source units keyed by stable `source_unit_id`.

    Raises
    ------
    ValueError
        If duplicate source-unit identifiers are produced.
    """

    source_units = build_sfi_source_units(extraction_window)
    source_unit_map = {
        source_unit.source_unit_id: source_unit for source_unit in source_units
    }

    if len(source_unit_map) != len(source_units):
        raise ValueError("Exact SFI source-unit identifiers must be unique per window.")

    return source_unit_map


def build_sfi_source_units(extraction_window: ExtractionWindow) -> list[SFISourceUnit]:
    """Build exact source-visible units for an extraction window.

    Parameters
    ----------
    extraction_window
        Source-faithful extraction window.

    Returns
    -------
    list[SFISourceUnit]
        Source-visible units in deterministic source order.
    """

    if extraction_window.segment_kind == "block":
        return _iter_block_source_units(extraction_window)

    return [
        *_iter_table_header_source_units(extraction_window),
        *_iter_table_body_source_units(extraction_window),
    ]


def find_source_anchor_span(
    *, anchor: SFISourceAnchor, source_unit: SFISourceUnit
) -> tuple[int, int]:
    """Resolve one returned source anchor to exact character offsets.

    Parameters
    ----------
    anchor
        Candidate anchor containing an exact source excerpt and occurrence index.
    source_unit
        Source unit referenced by the anchor.

    Returns
    -------
    tuple[int, int]
        Inclusive start and exclusive end offsets within `source_unit.source_text`.

    Raises
    ------
    ValueError
        If the excerpt does not occur at the requested occurrence index.
    """

    starts: list[int] = []
    search_start = 0

    while search_start <= len(source_unit.source_text):
        match_start = source_unit.source_text.find(anchor.source_text, search_start)

        if match_start < 0:
            break

        starts.append(match_start)
        search_start = match_start + len(anchor.source_text)

    if anchor.occurrence_index >= len(starts):
        raise ValueError(
            f"Source excerpt {anchor.source_text!r} does not have occurrence_index="
            f"{anchor.occurrence_index} in source unit {source_unit.source_unit_id!r}."
        )

    start_char = starts[anchor.occurrence_index]
    return start_char, start_char + len(anchor.source_text)


def map_raw_row_cells_to_column_ranges(
    *, available_column_indexes: set[int], n_cols: int, row: dict[str, Any]
) -> list[tuple[dict[str, Any], int, int]]:
    """Map raw non-placeholder table cells to span-expanded column ranges.

    Parameters
    ----------
    available_column_indexes
        Grid columns originating in the current raw source row.
    n_cols
        Total span-expanded table width.
    row
        Serialized raw source row.

    Returns
    -------
    list[tuple[dict[str, Any], int, int]]
        Cells paired with inclusive start and exclusive end columns.

    Raises
    ------
    ValueError
        If a raw cell cannot be placed in one contiguous available column range.
    """

    column_cursor = 0
    mapped_cells: list[tuple[dict[str, Any], int, int]] = []

    for cell in row.get("cells") or []:
        if cell.get("rowspan_placeholder"):
            continue

        col_span = max(1, int(cell.get("col_span") or 1))

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
            raise ValueError(
                f"Could not place a raw table cell in the exact structural grid: "
                f"column_cursor={column_cursor}, col_span={col_span}, n_cols={n_cols}, "
                f"available_columns={sorted(available_column_indexes)}."
            )

    return mapped_cells


def source_anchor_set_signature(
    source_anchors: Sequence[SFISourceAnchor],
) -> tuple[tuple[str, int, str], ...]:
    """Build an order-independent exact-location signature for source anchors.

    Parameters
    ----------
    source_anchors
        Validated source anchors.

    Returns
    -------
    tuple[tuple[str, int, str], ...]
        Sorted exact anchor signatures.

    """

    return tuple(
        sorted(
            (anchor.source_unit_id, anchor.occurrence_index, anchor.source_text)
            for anchor in source_anchors
        )
    )
