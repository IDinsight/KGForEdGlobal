"""This module contains functionalities related to validating PageIR information."""

# Standard Library
from collections import Counter
from dataclasses import dataclass
from typing import Any, Optional

# Third Party Library
from loguru import logger

# Package Library
from skg.page_ir.schemas import PageIR, PageIRContinuityVerdict
from skg.page_ir.utils import (
    _boundary_str,
    _derive_boundary_state_from_items,
    is_full_page_bbox,
    is_resumed,
    is_truncated,
    textunit_text,
)
from skg.utils.constants import BlockType, FigureKind, ItemBoundary, PageBoundaryState


@dataclass
class PageIRExtractionQualityCtx:
    """Context for PageIR extraction quality checks."""

    boundary_state: Any
    image_height: int
    image_width: int
    items: list[Any]
    non_artifact_items: list[tuple[int, Any]]
    page_bbox: tuple[float, float, float, float]
    page_ir: PageIR
    tol: float
    top_level_bboxes: list[tuple[float, float, float, float]]


@dataclass(frozen=True)
class PageIRExtractionTableWidthStats:
    """Statistics about table widths for PageIR extraction quality checks."""

    cell_counts: list[int]
    eff_widths: list[int]
    max_eff: int


class QualityError(Exception):
    """Raised when the LLM returns valid JSON that is semantically poor."""

    def __init__(self, message: str, failed_content: str | None = None):
        """

        Parameters
        ----------
        message
            The error message.
        failed_content
            The content that caused the failure, if applicable.
        """

        super().__init__(message)

        self.failed_content = failed_content


def build_page_ir_extraction_quality_ctx(
    *, image_height: int, image_width: int, page_ir: PageIR
) -> PageIRExtractionQualityCtx:
    """Build the PageIR extraction quality context.

    Parameters
    ----------
    image_height
        The image height in pixels.
    image_width
        The image width in pixels.
    page_ir
        The PageIR object.

    Returns
    -------
    PageIRExtractionQualityCtx
        The built quality context.
    """

    tol = 2.0  # Small tolerance for rounding
    items = page_ir.items or []
    boundary_state = getattr(page_ir, "boundary_state", PageBoundaryState.STANDALONE)
    page_bbox = (0.0, 0.0, float(image_width), float(image_height))

    # Only consider non-artifact items for continuity checks.
    non_artifact_items = [
        (i, it)
        for i, it in enumerate(items)
        if getattr(it, "kind", None) != "block"
        or getattr(it, "block_type", None) != BlockType.ARTIFACT
    ]

    return PageIRExtractionQualityCtx(
        boundary_state=boundary_state,
        image_height=image_height,
        image_width=image_width,
        items=items,
        non_artifact_items=non_artifact_items,
        page_bbox=page_bbox,
        page_ir=page_ir,
        tol=tol,
        top_level_bboxes=[],
    )


def compute_table_width_stats(
    *, rows: list[Any]
) -> Optional[PageIRExtractionTableWidthStats]:
    """Compute table width statistics.

    Parameters
    ----------
    rows
        The list of table rows.

    Returns
    -------
    Optional[PageIRExtractionTableWidthStats]
        The computed table width statistics, or None if no rows.
    """

    # Lightweight column-count sanity checks (span-aware).
    # NB: We must account for col_span when assessing whether a table has
    # "collapsed" into single-cell rows. Many curricula tables use spanning
    # header/label cells, so ignoring spans causes false positives.
    cell_counts = [len(getattr(rw, "cells", None) or []) for rw in rows]
    eff_widths = [
        sum(
            int(getattr(c, "col_span", 1) or 1)
            for c in (getattr(rw, "cells", None) or [])
        )
        for rw in rows
    ]
    eff_widths = [w for w in eff_widths if w > 0]
    return (
        None
        if not eff_widths
        else PageIRExtractionTableWidthStats(
            cell_counts=cell_counts, eff_widths=eff_widths, max_eff=max(eff_widths)
        )
    )


def ensure_text_en_none(tu: Any, where_: str) -> None:
    """Enforce that extraction does not populate English translations.

    Parameters
    ----------
    tu
        The TextUnit-like object.
    where_
        Description of where the TextUnit is located (for error messages).

    Raises
    ------
    QualityError
        If text_en is populated during extraction.
    """

    if tu is None:
        return

    # TextUnit.text_en must be null/omitted during extraction; translation happens
    # later.
    if getattr(tu, "text_en", None) is not None:
        raise QualityError(f"text_en must be null during extraction at {where_}.")


# Extraction validators.
def _validate_one_table(*, i: int, item: Any) -> None:
    """Validate a single table's integrity.

    Parameters
    ----------
    i
        The index of the table in items.
    item
        The table item.
    """

    rows = getattr(item, "rows", None) or []
    validate_table_rows_nonempty(i=i, rows=rows)
    validate_table_cells_and_spans(i=i, rows=rows)
    stats = compute_table_width_stats(rows=rows)
    if stats is not None:
        validate_table_n_cols(
            i=i, max_eff=stats.max_eff, n_cols=getattr(item, "n_cols", None)
        )
        validate_table_collapse_by_header_body(
            cell_counts=stats.cell_counts,
            eff_widths=stats.eff_widths,
            header_row_count=int(getattr(item, "header_row_count", 0) or 0),
            i=i,
        )
        validate_table_inconsistent_widths(
            eff_widths=stats.eff_widths, i=i, max_eff=stats.max_eff
        )
    validate_table_has_any_text(i=i, rows=rows)


def validate_basic_block_invariants(
    ctx: PageIRExtractionQualityCtx,
) -> None:
    """Validate basic block invariants.

    Parameters
    ----------
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If any basic block invariant is violated.
    """

    for i, item in enumerate(ctx.items):
        if getattr(item, "kind", None) != "block":
            continue

        block_type = getattr(item, "block_type", None)
        list_items = getattr(item, "list_items", None) or []
        fig = getattr(item, "figure", None)

        # Non-figure blocks must not carry figure metadata.
        if block_type != BlockType.FIGURE and fig is not None:
            raise QualityError(
                f"Non-figure block must have figure=null at items[{i}].figure."
            )

        if block_type == BlockType.LIST:
            for j, li in enumerate(list_items):
                # If marker is empty and text is short, it's likely a misclassification.
                raw_marker = getattr(li, "marker", None)
                marker = raw_marker if isinstance(raw_marker, str) else ""
                li_text = textunit_text(getattr(li, "text", None))
                ensure_text_en_none(
                    getattr(li, "text", None), f"items[{i}].list_items[{j}].text"
                )
                if not marker.strip() and len(li_text.strip()) < 3:
                    raise QualityError(
                        f"List item at items[{i}].list_items[{j}] has no marker and "
                        "insufficient text. This should likely be a paragraph."
                    )


def validate_continuity_for_extraction(
    ctx: PageIRExtractionQualityCtx,
) -> None:
    """Validate and reconcile page/item continuity signals.

    Parameters
    ----------
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If any continuity check fails.
    """

    page_ir = ctx.page_ir
    boundary_state = ctx.boundary_state
    expected_bs = _derive_boundary_state_from_items(ctx.non_artifact_items)

    if boundary_state != expected_bs:
        logger.warning(
            f"boundary_state mismatch on page {getattr(page_ir, 'page_index', None)}: "
            f"got={boundary_state} expected={expected_bs}. Overwriting."
        )
        page_ir.boundary_state = expected_bs
        boundary_state = expected_bs
        ctx.boundary_state = boundary_state

    bs = (
        boundary_state.value
        if hasattr(boundary_state, "value")
        else str(boundary_state)
    )
    states_requiring_prev = {
        PageBoundaryState.CONTINUES_FROM_PREV.value,
        PageBoundaryState.BOTH.value,
    }
    states_requiring_next = {
        PageBoundaryState.CONTINUES_TO_NEXT.value,
        PageBoundaryState.BOTH.value,
    }
    needs_from_prev = bs in states_requiring_prev
    needs_to_next = bs in states_requiring_next

    if needs_from_prev:
        if not ctx.non_artifact_items:
            raise QualityError(
                f"boundary_state='{boundary_state}' implies content continues from "
                f"previous page, but there are no non-artifact items."
            )

        # Look in the first few non-artifact items for a resumed marker.
        window = [it for _, it in ctx.non_artifact_items[:5]]

        if not any(is_resumed(_boundary_str(it)) for it in window):
            raise QualityError(
                f"boundary_state='{boundary_state}' implies content continues from "
                f" previous page, but no resumed boundary found in first few "
                f"non-artifact items."
            )

    if needs_to_next:
        if not ctx.non_artifact_items:
            raise QualityError(
                f"boundary_state='{boundary_state}' implies content continues to next "
                f"page, but there are no non-artifact items."
            )

        # Look in the last few non-artifact items for a truncated marker.
        window = [it for _, it in ctx.non_artifact_items[-5:]]

        if not any(is_truncated(_boundary_str(it)) for it in window):
            raise QualityError(
                f"boundary_state='{boundary_state}' implies content continues to "
                f" next page, but no truncated boundary found in last few non-artifact "
                f"items."
            )

    if bs == PageBoundaryState.STANDALONE.value and any(
        is_resumed(_boundary_str(it)) or is_truncated(_boundary_str(it))
        for _, it in ctx.non_artifact_items
    ):
        raise QualityError(
            f"boundary_state='{PageBoundaryState.STANDALONE.value}' but found "
            f"resumed/truncated boundaries on non-artifact items."
        )


def validate_full_page_bboxes(
    ctx: PageIRExtractionQualityCtx,
) -> None:
    """Reject full-page bboxes unless the page is a single full-page figure.

    Parameters
    ----------
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If any full-page bbox is found that is not a single full-page figure.
    """

    full_page_idxs = [
        i
        for i, bb in enumerate(ctx.top_level_bboxes)
        if is_full_page_bbox(bb=bb, page_bbox=ctx.page_bbox, tol=ctx.tol)
    ]
    if not full_page_idxs:
        return

    # Only allow if it's literally one item and it's a figure.
    if not (
        len(ctx.items) == 1
        and getattr(ctx.items[0], "kind", None) == "block"
        and getattr(ctx.items[0], "block_type", None) == BlockType.FIGURE
    ):
        raise QualityError(
            f"Full-page bbox used as a placeholder for items {full_page_idxs}. BBoxes "
            f"must be tight to each item. Full-page bbox is only allowed for a single "
            f"full-page figure."
        )

    fig = getattr(ctx.items[0], "figure", None)
    if fig is None:
        raise QualityError("Full-page figure block must include figure metadata.")
    if getattr(fig, "figure_kind", None) in (None, FigureKind.UNKNOWN):
        raise QualityError(
            "Full-page bbox is not allowed for figure_kind='unknown'. "
            "If this is a text page, extract blocks. If it's a real figure, set figure_kind."
        )

    contains_text = getattr(fig, "contains_text", None)
    if contains_text is None:
        raise QualityError(
            "Full-page bbox is only allowed when figure.contains_text is explicitly "
            "set to true/false (not null). If this page has body text, extract it into "
            "HEADING/PARAGRAPH/LIST blocks instead of a full-page figure."
        )
    if contains_text is True:
        raise QualityError(
            "Full-page bbox is not allowed for contains_text=true. "
            "This usually indicates a text page being misclassified as a figure."
        )


def validate_gross_reading_order(
    ctx: PageIRExtractionQualityCtx,
) -> None:
    """Validate gross reading-order consistency.

    Parameters
    ----------
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If any gross reading-order violation is detected.
    """

    non_artifact_items = ctx.non_artifact_items

    if len(non_artifact_items) < 3:
        return

    prev_bbox = getattr(non_artifact_items[0][1], "bbox", [0.0, 0.0, 0.0, 0.0])
    prev_x0, prev_y0 = float(prev_bbox[0]), float(prev_bbox[1])

    max_y_backjump = 0.15 * float(ctx.image_height)  # Big jump threshold
    same_col_dx = 0.20 * float(ctx.image_width)  # "Same column" threshold

    for idx, it in non_artifact_items[1:]:
        bbox = getattr(it, "bbox", None)

        if bbox is None:
            continue

        x0, y0 = float(bbox[0]), float(bbox[1])
        y_backjump = prev_y0 - y0
        x_diff = x0 - prev_x0  # Pos means moved right, neg means moved left

        # Only flag if we jumped UP and moved LEFT (back to start of a new column/line)
        # or if we jumped UP and stayed in the same horizontal lane.
        if y_backjump > max_y_backjump and (
            x_diff < -same_col_dx or abs(x_diff) < same_col_dx
        ):
            raise QualityError(
                f"Likely reading-order violation at items[{idx}]. Items jump upwards "
                f"significantly without a clear column shift."
            )

        prev_x0, prev_y0 = x0, y0


def validate_image_dimensions(
    ctx: PageIRExtractionQualityCtx,
) -> None:
    """Validate image dimensions.

    Parameters
    ----------
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If image dimensions are invalid.
    """

    if ctx.image_width <= 0 or ctx.image_height <= 0:
        raise QualityError(
            f"Invalid image dimensions: {ctx.image_width}x{ctx.image_height}."
        )


def validate_item_bboxes_required_and_in_bounds(
    ctx: PageIRExtractionQualityCtx,
) -> None:
    """Validate that item-level bboxes are present and in-bounds.

    Parameters
    ----------
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If any item-level bbox is missing or invalid.
    """

    for i, item in enumerate(ctx.items):
        bbox = getattr(item, "bbox", None)

        if bbox is None:
            raise QualityError(
                f"Missing required item bbox at items[{i}].bbox. "
                "Every block/table must have an item-level bbox."
            )

        x0, y0, x1, y1 = bbox
        if (
            x0 < -ctx.tol
            or y0 < -ctx.tol
            or x1 > ctx.image_width + ctx.tol
            or y1 > ctx.image_height + ctx.tol
        ):
            raise QualityError(
                f"Out-of-bounds bbox at items[{i}].bbox for "
                f"{ctx.image_width}x{ctx.image_height}: {bbox}"
            )

        ctx.top_level_bboxes.append((float(x0), float(y0), float(x1), float(y1)))


def validate_no_whitespace_or_empty_blocks(
    ctx: PageIRExtractionQualityCtx,
) -> None:
    """Validate that there are no whitespace-only or empty blocks.

    Parameters
    ----------
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If any whitespace-only or empty blocks are found.
    """

    for i, item in enumerate(ctx.items):
        if getattr(item, "kind", None) != "block":
            continue

        text_unit = getattr(item, "text", None)
        ensure_text_en_none(text_unit, f"items[{i}].text")

        block_type = getattr(item, "block_type", None)
        raw_text = getattr(text_unit, "text", None) if text_unit else None
        list_items = getattr(item, "list_items", None) or []
        local_code = getattr(item, "local_code", None)
        fig = getattr(item, "figure", None)

        # Figure captions are also TextUnits.
        if fig is not None:
            ensure_text_en_none(
                getattr(fig, "caption", None), f"items[{i}].figure.caption"
            )

        # Check for whitespace-only main text. We define has_text as: exists, is
        # string, and is not empty.
        has_text = False
        if isinstance(raw_text, str):
            if not raw_text.strip():
                raise QualityError(
                    f"Whitespace-only block text at items[{i}].text; remove this block."
                )
            has_text = True

        # Check for whitespace-only list items.
        for j, li in enumerate(list_items):
            li_unit = getattr(li, "text", None)
            li_raw = getattr(li_unit, "text", None) if li_unit else None
            if isinstance(li_raw, str) and not li_raw.strip():
                raise QualityError(
                    f"Whitespace-only list item text at items[{i}].list_items[{j}].text; "
                    "remove this list item."
                )

        # Check for empty blocks (no payload at all).
        has_list = len(list_items) > 0
        has_code = isinstance(local_code, str) and bool(local_code.strip())
        has_figure = (block_type == BlockType.FIGURE) and (fig is not None)
        if not (has_text or has_list or has_code or has_figure):
            raise QualityError(
                f"Empty block at items[{i}]: text, list_items, and local_code are all "
                f"null/empty (and figure is null). Do not emit placeholder blocks."
            )


def validate_placeholder_bboxes(
    ctx: PageIRExtractionQualityCtx,
) -> None:
    """Detect placeholder bboxes used across many items.

    Parameters
    ----------
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If too many items share a placeholder bbox.
    """

    if len(ctx.top_level_bboxes) >= 3:
        counts = Counter(ctx.top_level_bboxes)
        most_common_bbox, most_common_count = counts.most_common(1)[0]
        frac = most_common_count / max(1, len(ctx.top_level_bboxes))

        x0, y0, x1, y1 = most_common_bbox
        starts_at_origin = abs(x0) <= ctx.tol and abs(y0) <= ctx.tol
        area = max(0.0, (x1 - x0)) * max(0.0, (y1 - y0))
        page_area = float(ctx.image_width) * float(ctx.image_height)
        area_frac = area / page_area if page_area > 0 else 0.0

        if frac >= 0.30 and starts_at_origin:
            raise QualityError(
                "Too many items share the same origin-anchored bbox "
                f"{list(most_common_bbox)} (count={most_common_count}, frac={frac:.2f}). "
                "This looks like a placeholder; bboxes must be localized to each item."
            )

        if frac >= 0.20 and area_frac >= 0.85:
            raise QualityError(
                "Too many items share a near-full-page bbox "
                f"{list(most_common_bbox)} (count={most_common_count}, frac={frac:.2f}, area_frac={area_frac:.2f}). "
                "Do not use near-full-page bboxes as placeholders; bboxes must be localized."
            )


def validate_table_cells_and_spans(*, i: int, rows: list[Any]) -> None:
    """Validate table cells and their spans.

    Parameters
    ----------
    i
        The index of the table in items.
    rows
        The rows of the table.

    Raises
    ------
    QualityError
        If any cell is invalid or has invalid spans.
    """

    for r, row in enumerate(rows):
        cells = getattr(row, "cells", None) or []
        if len(cells) == 0:
            raise QualityError(
                f"Table row with zero cells at items[{i}].rows[{r}].cells."
            )
        for c, cell in enumerate(cells):
            ensure_text_en_none(
                getattr(cell, "text", None), f"items[{i}].rows[{r}].cells[{c}].text"
            )


def validate_table_collapse_by_header_body(
    *,
    cell_counts: list[int],
    eff_widths: list[int],
    header_row_count: int,
    i: int,
) -> None:
    """Detect likely table collapse via header vs. body effective widths.

    Parameters
    ----------
    cell_counts
        The raw cell counts per row.
    eff_widths
        The effective widths (span-aware) per row.
    header_row_count
        The header_row_count of the table.
    i
        The index of the table in items.

    Raises
    ------
    QualityError
        If the table likely collapsed.
    """

    # Detect likely collapse: header shows multiple columns but body is mostly
    # single-column in effective width.
    if not 0 < header_row_count < len(eff_widths):
        return

    header_max = max(eff_widths[:header_row_count])
    body_widths = eff_widths[header_row_count:]

    if not body_widths:
        return

    body_sorted = sorted(body_widths)
    body_median = body_sorted[len(body_sorted) // 2]
    frac_single = sum(w == 1 for w in body_widths) / len(body_widths)

    if header_max >= 3 and body_median <= 1 and frac_single >= 0.60:
        # Include raw cell counts for debugging.
        header_cell_max = max(cell_counts[:header_row_count])
        raise QualityError(
            f"Table at items[{i}] likely collapsed: header shows {header_max} columns "
            f"(effective, spans-aware; raw max cells={header_cell_max}), but "
            f"{frac_single:.0%} of body rows have effective width 1. Split body rows "
            f"into separate cells per visible column (or set correct col_span values)."
        )


def validate_table_has_any_text(*, i: int, rows: list[Any]) -> None:
    """Validate that the table has any text content.

    Parameters
    ----------
    i
        The index of the table in items.
    rows
        The rows of the table.

    Raises
    ------
    QualityError
        If the table has no text content.
    """

    for row in rows:
        for cell in getattr(row, "cells", None) or []:
            t = textunit_text(getattr(cell, "text", None))
            if t.strip():
                return
    raise QualityError(f"Table at items[{i}] contains no text content.")


def validate_table_inconsistent_widths(
    *, eff_widths: list[int], i: int, max_eff: int
) -> None:
    """Catch wildly inconsistent table widths overall (span-aware).

    Parameters
    ----------
    eff_widths
        The effective widths (span-aware) per row.
    i
        The index of the table in items.
    max_eff
        The maximum effective width of the table (sum of col_span).

    Raises
    ------
    QualityError
        If the table appears mostly single-column.
    """

    if max_eff < 4:
        return

    # Catch wildly inconsistent widths overall (span-aware).
    mode_eff, _ = Counter(eff_widths).most_common(1)[0]
    frac_single_all = sum(w == 1 for w in eff_widths) / len(eff_widths)
    if mode_eff == 1 and frac_single_all >= 0.70:
        raise QualityError(
            f"Table at items[{i}] appears mostly single-column rows "
            f"({frac_single_all:.0%} of rows have effective width 1). This often "
            f"indicates the table grid was collapsed. Represent each visible column as "
            f"a separate cell (or set correct col_span values)."
        )


def validate_table_integrity(ctx: PageIRExtractionQualityCtx) -> None:
    """Validate table integrity.

    Parameters
    ----------
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If any table integrity check fails.
    """

    for i, item in enumerate(ctx.items):
        if getattr(item, "kind", None) != "table":
            continue
        _validate_one_table(i=i, item=item)


def validate_table_n_cols(*, i: int, max_eff: int, n_cols: Any) -> None:
    """Validate that table n_cols is valid.

    Parameters
    ----------
    i
        The index of the table in items.
    max_eff
        The maximum effective width of the table (sum of col_span).
    n_cols
        The n_cols of the table.

    Raises
    ------
    QualityError
        If n_cols is invalid or cannot accommodate the widest row.
    """

    if n_cols is None:
        return

    # If the model provided n_cols, ensure it can accommodate the widest row.
    if not isinstance(n_cols, int):
        raise QualityError(
            f"n_cols must be an int or null at items[{i}].n_cols; got {type(n_cols)}"
        )
    if n_cols < 1 or n_cols > 50:
        raise QualityError(
            f"Suspicious n_cols={n_cols} at items[{i}].n_cols (expected 1..50 or null)."
        )
    if max_eff > n_cols:
        raise QualityError(
            f"Table at items[{i}] has a row with effective width {max_eff} "
            f"(sum of col_span) but n_cols={n_cols}. Either increase n_cols or "
            f"adjust/split/merge cells to match the visual grid."
        )


def validate_table_rows_nonempty(*, i: int, rows: list[Any]) -> None:
    """Validate that table rows are non-empty.

    Parameters
    ----------
    i
        The index of the table in items.
    rows
        The rows of the table.

    Raises
    ------
    QualityError
        If the table has no rows.
    """

    if len(rows) == 0:
        raise QualityError(
            f"Empty table (rows=[]) at items[{i}].rows. Do not emit empty tables."
        )


# Verification validators.
def validate_non_continuation_has_no_resumed_truncated_boundaries(
    verdict: PageIRContinuityVerdict,
) -> None:
    """Validate that a non-continuation verdict has no resumed/truncated boundaries.

    Parameters
    ----------
    verdict
        The PageIRContinuityVerdict to validate.

    Raises
    ------
    QualityError
        If the non-continuation verdict suggests resumed/truncated boundaries.
    """

    if verdict.is_continuation:
        return

    # If this is NOT a continuation, it makes no sense to suggest 'truncated'/'resumed'
    # boundaries on the boundary items.
    invalid = (ItemBoundary.TRUNCATED, ItemBoundary.RESUMED, ItemBoundary.BOTH)
    if (
        verdict.set_prev_item_boundary in invalid
        or verdict.set_next_item_boundary in invalid
    ):
        raise QualityError(
            "is_continuation=false but verdict suggests truncated/resumed item boundaries."
        )


def validate_page_indices(verdict: PageIRContinuityVerdict) -> None:
    """Validate the page indices in a continuity verdict.

    Parameters
    ----------
    verdict
        The PageIRContinuityVerdict to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if verdict.prev_page_index >= verdict.next_page_index:
        raise QualityError(
            f"Invalid page index order: previous page index={verdict.prev_page_index} "
            f"next page index={verdict.next_page_index}"
        )
    if verdict.next_page_index - verdict.prev_page_index != 1:
        logger.warning(
            f"Non-adjacent page indices in verdict "
            f"({verdict.prev_page_index}->{verdict.next_page_index}); expected adjacency."
        )
