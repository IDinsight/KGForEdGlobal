"""This module contains functionalities related to validating the **extracted** PageIR
information.

NB: Validators here enforce extraction-specific quality constraints that CANNOT live in
the Pydantic schemas because they either:

1. Require cross-item/page-level context (image dimensions, all bboxes, item count).
2. Are extraction-specific rules that don't apply universally (e.g., text_en must be
   null during extraction, but is populated later during translation).
3. Are heuristic quality checks with soft thresholds (e.g., footnote position, table
   collapse detection, short list item check).

Schema-level invariants (per-item structural rules) are enforced by Pydantic model
validators and run at structured-output parse time.
"""

# Standard Library
import re

from collections import Counter
from dataclasses import dataclass
from typing import Any

# Package Library
from skg.page_ir_extraction.schemas import PageIR, TextUnit
from skg.utils.constants import BlockType, FigureKind, ItemBoundary, PageBoundaryState

_NonArtifacts = {
    "abbreviations and acronyms",
    "acknowledgements",
    "acknowledgments",
    "bibliography",
    "contents",
    "list of figures",
    "list of tables",
    "preface",
    "reference list",
    "references",
    "table of contents",
}


@dataclass
class PageIRExtractionQualityCtx:
    """Context for PageIR extraction quality checks."""

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
    """Statistics about table widths for PageIR extraction quality checks.

    Attributes
    ----------
    cell_counts
        The raw cell counts per row.
    eff_widths
        The effective widths (span-aware) per row.
    max_eff
        The maximum effective width of the table (sum of col_span).
    """

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


def _is_full_page_bbox(
    *, bbox: tuple[float, ...], page_bbox: tuple[float, ...], tol: float
) -> bool:
    """Check if a bbox is effectively full-page within tolerance.

    Parameters
    ----------
    bbox
        The bbox to check.
    page_bbox
        The page bbox.
    tol
        The tolerance for comparison.

    Returns
    -------
    bool
        True if the bbox is full-page within tolerance, False otherwise.
    """

    x0, y0, x1, y1 = bbox

    return (
        abs(x0 - page_bbox[0]) <= tol
        and abs(y0 - page_bbox[1]) <= tol
        and abs(x1 - page_bbox[2]) <= tol
        and abs(y1 - page_bbox[3]) <= tol
    )


def _is_resumed(boundary: ItemBoundary) -> bool:
    """Check if a boundary indicates a resumed or both item.

    Parameters
    ----------
    boundary
        The ItemBoundary enum value to check.

    Returns
    -------
    bool
        True if the boundary indicates a resumed or both item, False otherwise.
    """

    return boundary in (ItemBoundary.RESUMED, ItemBoundary.BOTH)


def _is_truncated(boundary: ItemBoundary) -> bool:
    """Check if a boundary indicates a truncated or both item.

    Parameters
    ----------
    boundary
        The ItemBoundary enum value to check.

    Returns
    -------
    bool
        True if the boundary indicates a truncated or both item, False otherwise.
    """

    return boundary in (ItemBoundary.TRUNCATED, ItemBoundary.BOTH)


def _validate_table_cells_text_en(*, index: int, rows: list[Any]) -> None:
    """Validate that table cell text_en is null during extraction.

    Parameters
    ----------
    index
        The index of the table in items.
    rows
        The rows of the table.

    Raises
    ------
    QualityError
        If any cell has text_en populated.
    """

    for r, row in enumerate(rows):
        for c, cell in enumerate(row.cells):
            _validate_text_en_is_none(
                text=cell.text, where_=f"items[{index}].rows[{r}].cells[{c}].text"
            )


def _validate_table_collapse_by_header_body(
    *, cell_counts: list[int], eff_widths: list[int], header_row_count: int, index: int
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
    index
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
            f"Table at items[{index}] likely collapsed: header shows {header_max} columns "
            f"(effective, spans-aware; raw max cells={header_cell_max}), but "
            f"{frac_single:.0%} of body rows have effective width 1. Split body rows "
            f"into separate cells per visible column (or set correct col_span values)."
        )


def _validate_table_has_any_text(*, index: int, rows: list[Any]) -> None:
    """Validate that the table has any text content.

    Parameters
    ----------
    index
        The index of the table in items.
    rows
        The rows of the table.

    Raises
    ------
    QualityError
        If the table has no text content.
    """

    for row in rows:
        for cell in row.cells or []:
            text_or_none = cell.text

            if isinstance(text_or_none, TextUnit) and text_or_none.text.strip():
                return

    raise QualityError(f"Table at items[{index}] contains no text content.")


def _validate_table_inconsistent_widths(
    *, eff_widths: list[int], index: int, max_eff: int
) -> None:
    """Catch wildly inconsistent table widths overall (span-aware).

    Parameters
    ----------
    eff_widths
        The effective widths (span-aware) per row.
    index
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
            f"Table at items[{index}] appears mostly single-column rows "
            f"({frac_single_all:.0%} of rows have effective width 1). This often "
            f"indicates the table grid was collapsed. Represent each visible column as "
            f"a separate cell (or set correct col_span values)."
        )


def _validate_table_n_cols(*, index: int, n_cols: Any) -> None:
    """Validate that table n_cols is within a plausible range.

    Parameters
    ----------
    index
        The index of the table in items.
    n_cols
        The n_cols of the table.

    Raises
    ------
    QualityError
        If n_cols is outside a plausible range.
    """

    if n_cols is None:
        return

    if not isinstance(n_cols, int):
        raise QualityError(
            f"n_cols must be an int or null at items[{index}].n_cols; got {type(n_cols)}"
        )
    if n_cols > 50:
        raise QualityError(
            f"Suspicious n_cols={n_cols} at items[{index}].n_cols (expected 1..50 or null)."
        )


def _validate_text_en_is_none(*, text: TextUnit | None, where_: str) -> None:
    """Enforce that extraction does not populate English translations.

    Parameters
    ----------
    text
        Either a TextUnit object or None.
    where_
        Description of where the TextUnit is located (for error messages).

    Raises
    ------
    QualityError
        If text_en is populated during extraction.
    """

    if text is None:
        return

    # TextUnit.text_en must be null/omitted during extraction; translation happens
    # later.
    if text.text_en is not None:
        raise QualityError(f"text_en must be null during extraction at: {where_}.")


def compute_boundary_state_from_items(page_ir: PageIR) -> PageBoundaryState:
    """Derive the page boundary state from item boundaries.

    Parameters
    ----------
    page_ir
        The PageIR to analyze.

    Returns
    -------
    PageBoundaryState
        The derived page boundary state.
    """

    non_artifact_items = [
        (i, item)
        for i, item in enumerate(page_ir.items)
        if item.kind != "block" or item.block_type != BlockType.ARTIFACT
    ]

    if not non_artifact_items:
        return PageBoundaryState.STANDALONE

    non_artifacts = [item for _, item in non_artifact_items]

    any_from_prev = any(_is_resumed(item.boundary) for item in non_artifacts)
    any_to_next = any(_is_truncated(item.boundary) for item in non_artifacts)

    if any_from_prev and any_to_next:
        return PageBoundaryState.BOTH
    if any_from_prev:
        return PageBoundaryState.CONTINUES_FROM_PREV
    if any_to_next:
        return PageBoundaryState.CONTINUES_TO_NEXT

    return PageBoundaryState.STANDALONE


def validate_artifacts_are_true_artifacts(ctx: PageIRExtractionQualityCtx) -> None:
    """Reject cases where structural headings are mis-labeled as ARTIFACT.

    Parameters
    ----------
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If any artifact block appears to be a structural heading.
    """

    for i, item in enumerate(ctx.items):
        if item.kind != "block" or item.block_type != BlockType.ARTIFACT:
            continue

        text_unit_or_none = item.text
        text = (
            text_unit_or_none.text.strip().lower()
            if isinstance(text_unit_or_none, TextUnit)
            else ""
        )

        # If it has a local_code, it's almost certainly not an artifact.
        if item.local_code is not None:
            raise QualityError(
                f"Item {i} is block_type={BlockType.ARTIFACT.value} but has "
                f"local_code='{item.local_code}'. "
                f"Structural labels must be {BlockType.HEADING.value}."
            )

        # Common structural section labels.
        if text in _NonArtifacts:
            raise QualityError(
                f"Item {i} is block_type={BlockType.ARTIFACT.value} but text='{text}'. "
                f"Section titles must be {BlockType.HEADING.value}."
            )

        # "Section One/Two/..." should be a HEADING.
        if re.match(r"^\s*section\s+\w+", text):
            raise QualityError(
                f"Item {i} is block_type={BlockType.ARTIFACT.value} but looks like a "
                f"section heading: '{text}'. Classify as {BlockType.HEADING.value}."
            )


def validate_basic_block_invariants(ctx: PageIRExtractionQualityCtx) -> None:
    """Validate extraction-specific block invariants not covered by Pydantic schemas.

    Parameters
    ----------
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If any extraction-specific block invariant is violated.
    """

    for i, item in enumerate(ctx.items):
        if item.kind != "block":
            continue

        if item.block_type == BlockType.LIST:
            list_items = item.list_items or []

            for j, list_item in enumerate(list_items):
                list_item_text_or_none = list_item.text

                _validate_text_en_is_none(
                    text=list_item_text_or_none,
                    where_=f"items[{i}].list_items[{j}].text",
                )

                # Heuristic: if marker is missing AND the text is extremely short, it's
                # probably not a list item.
                marker_missing = (list_item.marker is None) or (
                    not list_item.marker.strip()
                )

                if (
                    marker_missing
                    and list_item_text_or_none is not None
                    and len(list_item_text_or_none.text.strip()) < 3
                ):
                    raise QualityError(
                        f"List item at items[{i}].list_items[{j}] has no marker and "
                        "insufficient text. This should likely be a paragraph."
                    )


def validate_continuity_for_extraction(ctx: PageIRExtractionQualityCtx) -> None:
    """Validate that item-level boundary markers are internally consistent.

    This validator does NOT check or mutate `boundary_state` on the PageIR---that field
    is computed by Python after all quality checks pass. Instead, it checks that
    item-level boundaries don't contradict each other (e.g., a standalone page
    shouldn't have resumed/truncated items).

    In addition, this validator enforces **positional** continuity---i.e., resumed
    items near the top and truncated items near the bottom.

    Parameters
    ----------
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If any continuity check fails.
    """

    # Derive expected boundary state from items to validate item-level consistency.
    expected = compute_boundary_state_from_items(ctx.page_ir)

    states_requiring_prev = {
        PageBoundaryState.CONTINUES_FROM_PREV.value,
        PageBoundaryState.BOTH.value,
    }
    states_requiring_next = {
        PageBoundaryState.CONTINUES_TO_NEXT.value,
        PageBoundaryState.BOTH.value,
    }

    if expected.value in states_requiring_prev:
        if not ctx.non_artifact_items:
            raise QualityError(
                "Item boundaries imply continuation from previous page, but there are "
                "no non-artifact items."
            )

        # Look in the first few non-artifact items for a resumed marker.
        if not any(
            _is_resumed(item.boundary)
            for item in [item for _, item in ctx.non_artifact_items[:5]]
        ):
            raise QualityError(
                "Item boundaries imply continuation from previous page, but no "
                "resumed boundary found in first few non-artifact items."
            )

    if expected.value in states_requiring_next:
        if not ctx.non_artifact_items:
            raise QualityError(
                "Item boundaries imply continuation to next page, but there are "
                "no non-artifact items."
            )

        # Look in the last few non-artifact items for a truncated marker.
        if not any(
            _is_truncated(item.boundary)
            for item in [item for _, item in ctx.non_artifact_items[-5:]]
        ):
            raise QualityError(
                "Item boundaries imply continuation to next page, but no "
                "truncated boundary found in last few non-artifact items."
            )


def validate_extraction_text_constraints(ctx: PageIRExtractionQualityCtx) -> None:
    """Validate extraction-specific text constraints on blocks.

    Checks that text_en is null on all text-bearing fields (extraction produces source
    text only; translation happens in a later pass) and that figure captions also have
    text_en=null.

    Parameters
    ----------
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If any extraction-specific text constraint is violated.
    """

    for i, item in enumerate(ctx.items):
        if item.kind != "block":
            continue

        # text_en must be null during extraction.
        _validate_text_en_is_none(text=item.text, where_=f"items[{i}].text")

        # Check figure captions for text_en.
        if item.figure is not None and item.figure.caption is not None:
            _validate_text_en_is_none(
                text=item.figure.caption, where_=f"items[{i}].figure.caption"
            )


def validate_figure_blocks_are_well_formed(ctx: PageIRExtractionQualityCtx) -> None:
    """Validate extraction-specific figure block constraints.

    Parameters
    ----------
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If any extraction-specific figure constraint is violated.
    """

    for i, item in enumerate(ctx.items):
        if item.kind != "block" or item.block_type != BlockType.FIGURE:
            continue

        if item.figure is None:
            continue

        fig = item.figure

        # Extraction-specific: no translations during extraction.
        if fig.caption is not None:
            _validate_text_en_is_none(
                text=fig.caption, where_=f"items[{i}].figure.caption"
            )

        if fig.embedded_text is not None:
            _validate_text_en_is_none(
                text=fig.embedded_text, where_=f"items[{i}].figure.embedded_text"
            )


def validate_footnote_blocks_are_plausible(ctx: PageIRExtractionQualityCtx) -> None:
    """Reject likely misuses of block_type='footnote'. Intended: bottom-of-page
    numbered notes (often separated by a rule). Not intended: page numbers, running
    headers/footers, normal paragraphs.

    Parameters
    ----------
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If any footnote block appears implausible.
    """

    # Tunable thresholds (keep lenient).
    h = float(ctx.image_height)
    bottom_band_y0 = 0.55 * h  # Footnote should *start* in the bottom ~45%
    max_height_frac = 0.35  # Footnote block shouldn't be huge

    for i, item in enumerate(ctx.items):
        if item.kind != "block" or item.block_type != BlockType.FOOTNOTE:
            continue

        # Boundary: footnotes should almost always be complete.
        if item.boundary != ItemBoundary.COMPLETE:
            raise QualityError(
                f"Footnote blocks should usually have boundary='{ItemBoundary.COMPLETE.value}'. "
                f"Found boundary='{item.boundary.value}' at items[{i}]."
            )

        # Bbox placement: near the bottom.
        _, y0, _, y1 = map(float, item.bbox)

        if y0 < bottom_band_y0:
            raise QualityError(
                f"Footnote block appears too high on the page at items[{i}].bbox={list(item.bbox)}. "
                f"Footnotes should be near the bottom; otherwise use '{BlockType.PARAGRAPH.value}'."
            )

        # Size sanity: avoid classifying half-page content as a footnote.
        if (y1 - y0) / h > max_height_frac:
            raise QualityError(
                f"Footnote block is unusually tall at items[{i}] (bbox height fraction={(y1 - y0) / h:.2f}). "
                f"This is likely not a footnote; use '{BlockType.PARAGRAPH.value}'."
            )


def validate_full_page_bboxes(ctx: PageIRExtractionQualityCtx) -> None:
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

    full_page_bboxes = [
        i
        for i, bbox in enumerate(ctx.top_level_bboxes)
        if _is_full_page_bbox(bbox=bbox, page_bbox=ctx.page_bbox, tol=ctx.tol)
    ]

    if not full_page_bboxes:
        return

    # Only allow if it's literally one item and it's a figure.
    if not (
        len(ctx.items) == 1
        and ctx.items[0].kind == "block"
        and ctx.items[0].block_type == BlockType.FIGURE
    ):
        raise QualityError(
            f"Full-page bbox used as a placeholder for items {full_page_bboxes}. "
            f"BBoxes must be tight to each item. Full-page bbox is only allowed for a "
            f"single full-page figure."
        )

    # Check figure metadata.
    figure_or_none = ctx.items[0].figure

    if figure_or_none is None:
        raise QualityError("Full-page figure block must include figure metadata.")
    if figure_or_none.figure_kind in (None, FigureKind.UNKNOWN):
        valid_options = ", ".join(
            [k.value for k in FigureKind if k != FigureKind.UNKNOWN]
        )
        raise QualityError(
            f"Full-page bbox is not allowed for figure_kind='unknown'. "
            f"If this is a real figure, please set figure_kind to one of: {valid_options}."
        )

    contains_text_or_none = figure_or_none.contains_text

    if contains_text_or_none is None:
        raise QualityError(
            f"Full-page bbox is only allowed when figure.contains_text is explicitly "
            f"set to true/false (not null). If this page has body text, extract it into "
            f"{BlockType.HEADING.value}/{BlockType.PARAGRAPH.value}/{BlockType.LIST.value} "
            f"blocks instead of a full-page figure."
        )
    if contains_text_or_none is True:
        raise QualityError(
            "Full-page bbox is not allowed for contains_text=true. "
            "This usually indicates a text page being misclassified as a figure."
        )


def validate_full_page_figure_requires_double_check(
    *, attempt: int, ctx: PageIRExtractionQualityCtx
) -> None:
    """Catch the following specific failure mode: the model outputs exactly one
    full-page FIGURE with contains_text=false but the page is actually a scanned text
    page (text-as-image).

    Parameters
    ----------
    attempt
        The extraction attempt number.
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If a likely missed text-as-image scenario is detected.
    """

    if len(ctx.items) != 1:
        return

    item = ctx.items[0]

    if (
        item.kind != "block"
        or item.block_type != BlockType.FIGURE
        or not ctx.top_level_bboxes
        or item.figure is None
    ):
        return

    # Must actually be full-page bbox.
    if not _is_full_page_bbox(
        bbox=ctx.top_level_bboxes[0], page_bbox=ctx.page_bbox, tol=ctx.tol
    ):
        return

    # Force double check.
    if attempt == 0:
        # If we get here, it is literally "one item, full-page figure". Force one retry
        # with a strong re-check instruction.
        raise QualityError(
            f"Page index {ctx.page_ir.page_index}: You returned exactly ONE full-page FIGURE "
            f"item. Full-page figures are rare. Re-check the page carefully. If there "
            f"is ANY readable body text or tables (including scanned text), you MUST "
            f"extract it into heading/paragraph/list/table items with tight bboxes. "
            f"Only return a single full-page figure if the page is truly an image-only "
            f"page with no readable text."
        )

    fig = item.figure
    alt = (fig.alt_text or "").strip().lower()

    # Scan-like hint.
    looks_like_scan = bool(re.search(r"\bscan(ned)?\b", alt))

    # If it doesn't present as a scan, allow figure-only pages (photos/diagrams).
    if not looks_like_scan:
        return

    embedded_text_or_none = fig.embedded_text
    embedded_text = (
        (embedded_text_or_none.text or "").strip()
        if isinstance(embedded_text_or_none, TextUnit)
        else ""
    )
    has_embedded = bool(embedded_text)

    caption_text_or_none = fig.caption
    caption_text = (
        (caption_text_or_none.text or "").strip()
        if isinstance(caption_text_or_none, TextUnit)
        else ""
    )
    has_caption = bool(caption_text)

    # If the model says there's visible text, embedded_text should exist.
    if fig.contains_text is True and not has_embedded:
        raise QualityError(
            f"Page index {ctx.page_ir.page_index}: figure.contains_text=true but "
            f"embedded_text is empty. If there is visible text in the figure region, "
            f"populate embedded_text with best-effort verbatim text."
        )

    # If model says contains_text=false and also provides no embedded text/caption, we
    # consider this a missed scanned-text extraction.
    if fig.contains_text is False and (not has_embedded) and (not has_caption):
        raise QualityError(
            f"Page index {ctx.page_ir.page_index}: extractor returned a full-page scanned "
            f"FIGURE with contains_text=false and no embedded_text/caption. This is "
            f"likely a missed text-as-image page. Extract the page contents as "
            f"headings/paragraphs/lists/tables instead of a single full-page figure, "
            f"or set contains_text=true and populate embedded_text."
        )

    # If contains_text is null/unknown but also no text evidence, also fail for
    # scan-like pages.
    if fig.contains_text is None and (not has_embedded) and (not has_caption):
        raise QualityError(
            f"Page index {ctx.page_ir.page_index}: extractor returned a full-page scanned "
            f"FIGURE but provided no text evidence (contains_text is null and "
            f"embedded_text/caption are empty). Extract text blocks/tables, or set "
            f"contains_text=true and populate embedded_text."
        )


def validate_gross_reading_order(ctx: PageIRExtractionQualityCtx) -> None:
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

    prev_bbox = non_artifact_items[0][1].bbox
    prev_x0, prev_y0 = float(prev_bbox[0]), float(prev_bbox[1])

    max_y_backjump = 0.15 * float(ctx.image_height)  # Big jump threshold
    same_col_dx = 0.20 * float(ctx.image_width)  # "Same column" threshold

    for i, item in non_artifact_items[1:]:
        bbox = item.bbox
        x0, y0 = float(bbox[0]), float(bbox[1])
        y_backjump = prev_y0 - y0
        x_diff = x0 - prev_x0  # Pos means moved right, neg means moved left

        # Only flag if we jumped UP and moved LEFT (back to start of a new column/line)
        # or if we jumped UP and stayed in the same horizontal lane.
        if y_backjump > max_y_backjump and (
            x_diff < -same_col_dx or abs(x_diff) < same_col_dx
        ):
            raise QualityError(
                f"Likely reading-order violation at items[{i}]. "
                f"Items jump upwards significantly without a clear column shift."
            )

        prev_x0, prev_y0 = x0, y0


def validate_image_dimensions(ctx: PageIRExtractionQualityCtx) -> None:
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
        bbox = item.bbox

        if bbox is None:
            raise QualityError(
                f"Missing required item bbox at items[{i}].bbox. "
                f"Every block/table must have an item-level bbox."
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


def validate_no_duplicate_item_bboxes(ctx: PageIRExtractionQualityCtx) -> None:
    """Disallow exact duplicate top-level item bboxes. Placeholder bbox checks only
    catch *many* duplicates; this catches *any* duplicate.

    Parameters
    ----------
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If any duplicate item-level bbox is found.
    """

    if len(ctx.top_level_bboxes) < 2:
        return

    counts = Counter(ctx.top_level_bboxes)
    dup_bboxes = [bbox for bbox, cnt in counts.items() if cnt > 1]

    if not dup_bboxes:
        return

    # Map bbox to item indices (ctx.top_level_bboxes is appended in items order).
    bbox_to_indices: dict[tuple[float, float, float, float], list[int]] = {}
    for i, bbox in enumerate(ctx.top_level_bboxes):
        bbox_to_indices.setdefault(bbox, []).append(i)

    details = []

    for bbox in dup_bboxes:
        idxs = bbox_to_indices.get(bbox, [])
        details.append(f"bbox={list(bbox)} used by items={idxs}")

    # Keep the error readable if there are many duplicates.
    max_show = 6
    suffix = (
        "" if len(details) <= max_show else f" (and {len(details) - max_show} more)"
    )
    details_str = "; ".join(details[:max_show]) + suffix

    raise QualityError(
        "Duplicate item bboxes detected. Each block/table must have a unique, tight bbox. "
        + details_str
    )


def validate_placeholder_bboxes(ctx: PageIRExtractionQualityCtx) -> None:
    """Detect placeholder bboxes used across many items.

    If lots of items all get assigned to the same bbox, then that looks suspicious.
    This function looks for the most frequently repeated bbox and raises an error if it
    looks like one of two common placeholder patterns:

    1. A box starting at the page origin (0, 0, ..., ...).
    2. A box covering almost the whole page.

    Parameters
    ----------
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If too many items share a placeholder bbox.
    """

    # Only check if there are at least 3 bboxes to avoid flagging tiny samples.
    if len(ctx.top_level_bboxes) >= 3:
        # Count how many times each bbox appears so that if many items have exactly the
        # same bbox, then we can find that repeated bbox.
        counts = Counter(ctx.top_level_bboxes)
        most_common_bbox, most_common_count = counts.most_common(1)[0]

        # Compute the fraction of items using that common bbox to see if it exceeds a
        # threshold. Example: 10 total items, same bbox appears 4 times, then
        # frac = 0.40.
        frac = most_common_count / max(1, len(ctx.top_level_bboxes))

        # Check whether that bbox starts at the origin.
        x0, y0, x1, y1 = most_common_bbox
        starts_at_origin = abs(x0) <= ctx.tol and abs(y0) <= ctx.tol

        # Compute how much of the page the bbox covers. `area_frac` is percentage of
        # the page covered by the bbox. For example, if `area_frac` = 0.90, then the
        # bbox covers 90% of the page.
        area = max(0.0, (x1 - x0)) * max(0.0, (y1 - y0))
        page_area = float(ctx.image_width) * float(ctx.image_height)
        area_frac = area / page_area if page_area > 0 else 0.0

        # Case 1: Too many items share the same origin-anchored bbox. This looks like
        # the model defaulted several items to the same top-left bbox instead of
        # locating each item properly.
        if frac >= 0.30 and starts_at_origin:
            raise QualityError(
                f"Too many items share the same origin-anchored bbox "
                f"{list(most_common_bbox)} (count={most_common_count}, frac={frac:.2f}). "
                f"This looks like a placeholder; bboxes must be localized to each item."
            )

        # Case 2: Too many items share a near-full-page bbox. If at least 20% of items
        # use the same bbox and that bbox covers at least 85% of the page, then we also
        # treat it as a placeholder. This is when we the model tries to use one giant
        # almost-full-page bbox for multiple distinct items.
        if frac >= 0.20 and area_frac >= 0.85:
            raise QualityError(
                f"Too many items share a near-full-page bbox "
                f"{list(most_common_bbox)} (count={most_common_count}, "
                f"frac={frac:.2f}, area_frac={area_frac:.2f}). "
                f"Do not use near-full-page bboxes as placeholders; bboxes must be localized."
            )


def validate_table_integrity(ctx: PageIRExtractionQualityCtx) -> None:
    """Validate table integrity via heuristic quality checks.

    Parameters
    ----------
    ctx
        The PageIR extraction quality context.

    Raises
    ------
    QualityError
        If any table quality check fails.
    """

    for i, item in enumerate(ctx.items):
        if item.kind != "table":
            continue

        rows = item.rows or []

        # Extraction-specific: text_en must be null on all cells.
        _validate_table_cells_text_en(index=i, rows=rows)

        # Lightweight column-count sanity checks (span-aware). NB: We must account for
        # col_span when assessing whether a table has "collapsed" into single-cell
        # rows. Many tables use spanning header/label cells, so ignoring spans can
        # cause false positives.
        cell_counts = [len(row.cells or []) for row in rows]
        eff_widths = [
            w
            for row in rows
            if (w := sum(int(cell.col_span) for cell in (row.cells or []))) > 0
        ]
        stats = (
            None
            if not eff_widths
            else PageIRExtractionTableWidthStats(
                cell_counts=cell_counts, eff_widths=eff_widths, max_eff=max(eff_widths)
            )
        )

        if stats is not None:
            _validate_table_n_cols(index=i, n_cols=item.n_cols)
            _validate_table_collapse_by_header_body(
                cell_counts=stats.cell_counts,
                eff_widths=stats.eff_widths,
                header_row_count=int(item.header_row_count),
                index=i,
            )
            _validate_table_inconsistent_widths(
                eff_widths=stats.eff_widths, index=i, max_eff=stats.max_eff
            )

        _validate_table_has_any_text(index=i, rows=rows)
