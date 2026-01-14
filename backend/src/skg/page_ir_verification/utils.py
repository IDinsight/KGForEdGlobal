"""This module contains utility functions related to page IR **verification**."""

# Standard Library
import re
import uuid

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

# Third Party Library
from loguru import logger

# Package Library
from skg.page_ir_extraction.schemas import Block, PageIR, Table, TableCell, TextUnit
from skg.page_ir_verification.schemas import PageIRContinuityVerdict
from skg.schemas import RunCtx, VerificationConfig
from skg.utils.constants import (
    BlockType,
    ItemBoundary,
    PageBoundaryState,
    PageContinuationKind,
)
from skg.utils.general import make_dir, open_json_type, write_to_json


class EdgeVerdictRecord(NamedTuple):
    """Edge verdict record between two page IR candidates."""

    next_candidate_index: int
    next_page_index: int
    prev_candidate_index: int
    prev_page_index: int
    verdict: PageIRContinuityVerdict


@dataclass(frozen=True)
class PageIRVerificationDirs:
    """Dataclass for page IR verification directories."""

    root: Path
    page_irs_pair_crops: Path
    page_irs_pair_reports: Path
    page_irs_verified: Path


def _apply_single_edge_verdict(
    *,
    bools: dict[tuple[int, int], list[bool]],
    min_confidence: float,
    record: EdgeVerdictRecord,
    repeats_header_patch: dict[tuple[int, int], bool],
) -> dict[str, Any]:
    """Apply logic for a single edge verdict and return the summary dict. Updates
    `bools` and `repeats_header_patch` in-place.

    Parameters
    ----------
    bools
        Mapping of (page_idx, item_idx) to [from_prev, to_next] booleans.
    min_confidence
        Minimum confidence threshold to apply edits.
    record
        The edge verdict record to apply.
    repeats_header_patch
        Mapping of (page_idx, item_idx) to desired repeats_header boolean.

    Returns
    -------
    dict[str, Any]
        Summary of the applied edge verdict.
    """

    verdict = record.verdict
    prev_key = (record.prev_page_index, record.prev_candidate_index)
    next_key = (record.next_page_index, record.next_candidate_index)
    should_apply = verdict.confidence >= min_confidence

    if should_apply:
        if verdict.is_continuation:
            bools[prev_key][1] = True  # prev.to_next
            bools[next_key][0] = True  # next.from_prev

            if (
                verdict.continuation_kind == PageContinuationKind.TABLE
                and verdict.set_next_table_repeats_header is not None
            ):
                repeats_header_patch[next_key] = verdict.set_next_table_repeats_header
        else:
            # Clear only the directional connection for THIS candidate pair.
            bools[prev_key][1] = False
            bools[next_key][0] = False

    # Return summary for reporting.
    return {
        "prev_index": record.prev_candidate_index,
        "next_index": record.next_candidate_index,
        "prev_page": record.prev_page_index,
        "next_page": record.next_page_index,
        "is_continuation": verdict.is_continuation,
        "continuation_kind": verdict.continuation_kind.value,
        "confidence": verdict.confidence,
        "applied": should_apply,
    }


def _boundary_to_bools(boundary: ItemBoundary | None) -> tuple[bool, bool]:
    """Return (from_prev, to_next).

    Parameters
    ----------
    boundary
        The item boundary state.

    Returns
    -------
    tuple[bool, bool]
        A tuple indicating (from_prev, to_next).
    """

    if boundary == ItemBoundary.BOTH:
        return True, True

    if boundary == ItemBoundary.RESUMED:
        return True, False

    if boundary == ItemBoundary.TRUNCATED:
        return False, True

    return False, False  # COMPLETE or None


def _bools_to_boundary(from_prev: bool, to_next: bool) -> ItemBoundary:
    """Convert (from_prev, to_next) booleans to ItemBoundary enum.

    Parameters
    ----------
    from_prev
        Whether the item continues from the previous page.
    to_next
        Whether the item continues onto the next page.

    Returns
    -------
    ItemBoundary
        The corresponding ItemBoundary enum.
    """

    if from_prev and to_next:
        return ItemBoundary.BOTH

    if from_prev:
        return ItemBoundary.RESUMED

    if to_next:
        return ItemBoundary.TRUNCATED

    return ItemBoundary.COMPLETE


def _reconcile_item_state(
    *,
    item: Any,
    item_index: int,
    flags: list[bool],
    page_index: int,
    repeats_header_patch: dict[tuple[int, int], bool],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Update item boundary and table headers based on calculated flags.

    Parameters
    ----------
    item
        The PageIR item to update.
    item_index
        The index of the item on the page.
    flags
        The (from_prev, to_next) boolean flags.
    page_index
        The index of the page containing the item.
    repeats_header_patch
        Mapping of (page_idx, item_idx) to desired repeats_header boolean.

    Returns
    -------
    tuple[dict[str, Any] | None, dict[str, Any] | None]
        A tuple of (boundary_change, header_change) summaries, or None if no change.
    """

    from_prev, to_next = flags
    boundary_change = None
    header_change = None

    before_boundary = item.boundary
    after_boundary = _bools_to_boundary(from_prev, to_next)

    if before_boundary != after_boundary:
        boundary_change = {
            "page": page_index,
            "item_index": item_index,
            "before": getattr(before_boundary, "value", None),
            "after": after_boundary.value,
        }
        item.boundary = after_boundary

    # repeats_header only meaningful if table continues from prev.
    if item.kind == "table":
        table = item

        # Case A: Connection broken (not RESUMED or BOTH) --> Clear header.
        if item.boundary not in {ItemBoundary.RESUMED, ItemBoundary.BOTH}:
            if table.repeats_header is not None:
                header_change = {
                    "page": page_index,
                    "item_index": item_index,
                    "before": table.repeats_header,
                    "after": None,
                }
                table.repeats_header = None

        # Case B: Connection exists --> Apply patch if present.
        else:
            key = (page_index, item_index)
            if key in repeats_header_patch:
                desired = repeats_header_patch[key]
                if table.repeats_header != desired:
                    header_change = {
                        "page": page_index,
                        "item_index": item_index,
                        "before": table.repeats_header,
                        "after": desired,
                    }
                    table.repeats_header = desired

    return boundary_change, header_change


def compile_continuity_from_edge_verdicts(
    *,
    edge_records: list[EdgeVerdictRecord],
    min_confidence_to_patch: float,
    page_irs: dict[int, PageIR],
) -> dict[str, Any]:
    """Apply all continuity decisions in one pass as follows:

    1. Positive edge --> set prev.to_next and next.from_prev.
    2. Negative edge --> clear ONLY that directional connection

    Then recompute ItemBoundary enums from bits and also enforce repeats_header
    consistency with boundary state.

    Parameters
    ----------
    edge_records
        List of edge verdict records.
    min_confidence_to_patch
        Minimum confidence threshold to apply edits.
    page_irs
        Mapping of page_index to PageIR objects.

    Returns
    -------
    dict[str, Any]
        A summary of applied edits.
    """

    # Initialize bools: (page_idx, item_idx) -> [from_prev, to_next].
    bools: dict[tuple[int, int], list[bool]] = {}

    for page_index, page in page_irs.items():
        for item_index, item in enumerate(page.items or []):
            fp, tn = _boundary_to_bools(item.boundary)
            bools[(page_index, item_index)] = [fp, tn]

    # Apply edge decisions (set or clear only that edge).
    applied_edges: list[dict[str, Any]] = []
    repeats_header_patch: dict[tuple[int, int], bool] = {}

    for record in edge_records:
        summary = _apply_single_edge_verdict(
            bools=bools,
            min_confidence=min_confidence_to_patch,
            record=record,
            repeats_header_patch=repeats_header_patch,
        )
        applied_edges.append(summary)

    # Write boundaries back and enforce repeats_header consistency.
    boundary_changes: list[dict[str, Any]] = []
    repeats_header_changes: list[dict[str, Any]] = []

    for (page_index, item_index), flags in bools.items():
        item = (page_irs[page_index].items or [])[item_index]

        b_change, h_change = _reconcile_item_state(
            item=item,
            item_index=item_index,
            flags=flags,
            page_index=page_index,
            repeats_header_patch=repeats_header_patch,
        )

        if b_change:
            boundary_changes.append(b_change)
        if h_change:
            repeats_header_changes.append(h_change)

    return {
        "applied_edges": applied_edges,
        "boundary_changes": boundary_changes,
        "repeats_header_changes": repeats_header_changes,
    }


def _bbox_intersects_y_range(*, bbox: list[float], y_max: float, y_min: float) -> bool:
    """Return True if bbox intersects the vertical range [y_min, y_max]. bbox is
    [x0, y0, x1, y1] in full-page pixel coords.

    Parameters
    ----------
    bbox
        The bounding box to check.
    y_max
        The maximum y coordinate of the range.
    y_min
        The minimum y coordinate of the range.

    Returns
    -------
    bool
        True if the bbox intersects the y range, False otherwise.
    """

    y0 = float(bbox[1])
    y1 = float(bbox[3])

    return not (y1 < float(y_min) or y0 > float(y_max))


def bottommost_continuity_candidate(
    *,
    image_height: float,
    items: list[Block | Table],
    visible_y_min: float | None = None,
) -> tuple[int, Block | Table]:
    """Pick the best "bottom of page" candidate for continuity checks.

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    items
        List of PageIR items on the page.
    visible_y_min
        If provided, restrict candidate selection to items whose bbox intersects the
        visible crop range [visible_y_min, image_height] in full-page coordinates.

    Returns
    -------
    tuple[int, Block | Table]
        The index and item of the chosen bottom-most candidate.

    Raises
    ------
    ValueError
        If no non-artifact items are found.
    """

    # Filter candidates.
    candidates = [
        (i, item)
        for i, item in enumerate(items)
        if not (
            is_artifact(item)
            or is_probable_header_footer_noise(image_height=image_height, item=item)
        )
    ]

    # If we are verifying using a bottom-crop image, restrict candidates to items that
    # actually appear in that crop (in full-page coordinate space). This prevents
    # choosing an item the model cannot see, which would cause false negatives.
    if visible_y_min is not None:
        y_min = float(visible_y_min)
        cropped = [
            (i, item)
            for i, item in candidates
            if _bbox_intersects_y_range(
                bbox=item.bbox, y_max=float(image_height), y_min=y_min
            )
        ]
        if cropped:
            candidates = cropped
        else:
            logger.warning(
                "No bottom-crop-visible candidates found; falling back to full-page "
                "candidate selection."
            )

    if not candidates:
        raise ValueError("No non-artifact items found.")

    # Sort by bottom-edge (y1) descending (bbox is [x0, y0, x1, y1]).
    candidates.sort(key=lambda c: float(c[1].bbox[3]), reverse=True)

    # Prefer a Table if it is "near" the bottom (within the bottom 5 items). This
    # protects against cases where a small footnote or page number (that wasn't caught
    # by the noise filter) sits slightly below a large table.
    for i, item in candidates[:5]:
        if item.kind == "table":
            return i, item

    # If we're anchoring a block-to-block continuity check, do NOT pick a heading or
    # caption as the boundary candidate (these often sit at page edges but should not
    # be treated as continuations of text).
    for i, item in candidates:
        if item.kind != "table":
            if isinstance(item, Block) and item.block_type in (
                BlockType.CAPTION,
                BlockType.HEADING,
            ):
                continue

            return i, item

    # Fallback is to just take the absolute bottom item.
    return candidates[0]


def create_page_ir_verification_dirs(*, output_dir: Path) -> PageIRVerificationDirs:
    """Create page IR verification directories for a given verification run.

    Parameters
    ----------
    output_dir
        The output directory root.

    Returns
    -------
    PageIRVerificationDirs
        The created page IR verification directories.
    """

    root = output_dir
    page_irs_pair_crops = root / "page_irs_pair_crops"
    page_irs_pair_reports = root / "page_irs_pair_reports"
    page_irs_verified = root / "page_irs_verified"

    for p in [root, page_irs_pair_crops, page_irs_pair_reports, page_irs_verified]:
        make_dir(p)

    return PageIRVerificationDirs(
        root=root,
        page_irs_pair_crops=page_irs_pair_crops,
        page_irs_pair_reports=page_irs_pair_reports,
        page_irs_verified=page_irs_verified,
    )


def derive_page_boundary_state(*, page_ir: PageIR) -> PageBoundaryState:
    """Derive page-level boundary_state from verified item boundaries.

    Parameters
    ----------
    page_ir
        The page IR dictionary.

    Returns
    -------
    PageBoundaryState
        The derived page-level boundary state.
    """

    items = page_ir.items or []
    image_height = page_ir.image_height

    candidates = [
        item
        for item in items
        if not is_artifact(item)
        and not is_probable_header_footer_noise(image_height=image_height, item=item)
    ]

    if not candidates:
        return PageBoundaryState.STANDALONE

    from_prev = any(
        item.boundary.value in (ItemBoundary.RESUMED.value, ItemBoundary.BOTH.value)
        for item in candidates
    )
    to_next = any(
        item.boundary.value in (ItemBoundary.TRUNCATED.value, ItemBoundary.BOTH.value)
        for item in candidates
    )

    if from_prev and to_next:
        return PageBoundaryState.BOTH
    if from_prev:
        return PageBoundaryState.CONTINUES_FROM_PREV
    if to_next:
        return PageBoundaryState.CONTINUES_TO_NEXT
    return PageBoundaryState.STANDALONE


def find_caption_code(items: list[Block | Table]) -> str | None:
    """Find the first valid Table-like local_code from a caption block.

    Parameters
    ----------
    items
        List of PageIR items.

    Returns
    -------
    str | None
        The found caption local_code, or None if not found.
    """

    for item in items:
        if item.kind == "block" and item.block_type.value == BlockType.CAPTION.value:
            code = item.local_code or ""
            if code.strip().lower().startswith("table"):
                return code

    return None


def is_artifact(item: Block | Table) -> bool:
    """Check if an item is an artifact.

    Parameters
    ----------
    item
        The item to check.

    Returns
    -------
    bool
        True if the item is an artifact, False otherwise.
    """

    return (
        False
        if item.kind != "block"
        else item.block_type.value == BlockType.ARTIFACT.value
    )


def is_probable_header_footer_noise(
    *, image_height: float, item: Block | Table
) -> bool:
    """Heuristic to exclude common header/footer noise (page numbers, running headers).

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    item
        The item to check.

    Returns
    -------
    bool
        True if the item is likely header/footer noise, False otherwise.
    """

    if item.kind != "block":
        return False

    text_or_none = item.text
    text = text_or_none.text.strip() if isinstance(text_or_none, TextUnit) else ""

    if not text:
        return False

    # Very small box height is usually a strong cue (page numbers, running headers).
    bbox = item.bbox
    _, y0, _, y1 = map(float, bbox)
    near_top = y0 <= 0.06 * image_height
    near_bottom = y1 >= 0.94 * image_height

    if not (near_top or near_bottom):
        return False

    # Require small box height to avoid sparse pages from being mis-classified as
    # footer/header noise.
    box_h = y1 - y0

    if box_h > max(90.0, 0.05 * image_height):
        return False

    # Common page number/footer patterns (keep conservative).
    t = re.sub(r"\s+", " ", text).strip()
    if (len(t) <= 12 and re.fullmatch(r"(\d+|[ivxlcdm]+)", t.lower())) or (
        len(t) <= 20 and re.fullmatch(r"(page\s*)?\d+(\s*/\s*\d+)?", t.lower())
    ):
        return True

    return False


def load_page_irs_from_verification(
    *, doc_key: str, verified_page_irs_dir: Path
) -> list[PageIR]:
    """Load and validate all verified page IR JSONs from the verification output
    directory.

    Parameters
    ----------
    doc_key
        The document key for all page IRs.
    verified_page_irs_dir
        Directory containing the verified page IR JSONs.

    Returns
    -------
    list[PageIR]
        The loaded and validated PageIRs in filename order.

    Raises
    ------
    FileNotFoundError
        If no verified page IR JSON files are found in the specified directory.
    ValueError
        If any verified PageIR is missing page_index.
        If the page_index sequence is non-contiguous or does not start at 0.
        If there are inconsistent doc_key or pdf_name values across pages.
        If there are inconsistent coord_space, dpi, image_width, or image_height
            values across pages.
    """

    json_files = sorted(verified_page_irs_dir.glob("*.json"))

    if not json_files:
        raise FileNotFoundError(
            f"No verified page IR JSON files found in: {verified_page_irs_dir}"
        )

    page_irs: list[PageIR] = [
        PageIR.model_validate(open_json_type(fp)) for fp in json_files
    ]

    # Validate page_index exists and sort by it (not filename).
    if any(p.page_index is None for p in page_irs):
        raise ValueError(
            "One or more verified PageIRs are missing page_index. Cannot stitch reliably."
        )

    page_irs.sort(key=lambda p: p.page_index)
    page_indexes = [p.page_index for p in page_irs]
    expected = list(range(len(page_irs)))

    if page_indexes != expected:
        raise ValueError(
            f"Non-contiguous page_index sequence. Got {page_indexes[:10]}..."
        )

    # Validate doc_key consistency + presence.
    if doc_key is None:
        raise ValueError(
            "verification_run.json is missing extra.doc_key (expected_doc_key)."
        )

    doc_keys = {p.doc_key for p in page_irs if p.doc_key}
    pdf_names = {p.pdf_name for p in page_irs if p.pdf_name}

    if not doc_keys:
        raise ValueError(
            "All verified PageIRs are missing doc_key. "
            "Ensure step 1/2 populates PageIR.doc_key for every page."
        )
    if len(doc_keys) > 1 or len(pdf_names) > 1:
        raise ValueError(
            "Inconsistent pdf_name or doc_key across pages:\n"
            f"{sorted(doc_keys)}\n{sorted(pdf_names)}"
        )

    only_doc_key = next(iter(doc_keys))
    if only_doc_key != doc_key:
        raise ValueError(f"Expected doc_key '{doc_key}', got '{only_doc_key}'")

    # Validate coordinate space, dimensions, and dpi consistency/presence.
    coord_spaces = {p.coord_space for p in page_irs if p.coord_space is not None}
    dpis = {p.dpi for p in page_irs if p.dpi is not None}
    heights = {p.image_height for p in page_irs if p.image_height is not None}
    widths = {p.image_width for p in page_irs if p.image_width is not None}

    if len(coord_spaces) > 1 or len(dpis) > 1 or len(widths) > 1 or len(heights) > 1:
        raise ValueError(
            "Inconsistent coordinate space, page dimensions, or dpi across pages:\n"
            f"{coord_spaces=}\n{dpis=}\n{widths=}\n{heights=}"
        )

    if (
        any(p.dpi is None for p in page_irs)
        or any(p.image_width is None for p in page_irs)
        or any(p.image_height is None for p in page_irs)
    ):
        raise ValueError(
            "One or more verified PageIRs are missing dpi, image_width, or image_height."
        )

    return page_irs


def normalize_table_row_cell_counts(
    *, page_irs: dict[int, PageIR]
) -> list[dict[str, Any]]:
    """Ensure each table row has exactly n_cols cells. Fixes the common LLM error of
    dropping empty cells at the start/end of rows.

    Parameters
    ----------
    page_irs
        Mapping of page index to PageIR dict.

    Returns
    -------
    list[dict[str, Any]]
        A list of change records describing the modifications made.
    """

    changes: list[dict[str, Any]] = []

    for page_index in sorted(page_irs.keys()):
        page_ir = page_irs[page_index]
        for item_index, item in enumerate(page_ir.items or []):
            if item.kind != "table":
                continue

            # We only fix tables where the LLM explicitly stated the column count.
            n_cols = item.n_cols
            if not isinstance(n_cols, int) or n_cols <= 0:
                continue

            rows = item.rows or []
            for row_index, row in enumerate(rows):
                cells = row.cells or []

                # Count effective columns, respecting merged cells.
                effective_cols = sum((cell.col_span or 1) for cell in cells)

                # Keep as is if the row already covers the table width (including
                # merged cells).
                if effective_cols >= n_cols:
                    continue

                missing = n_cols - effective_cols

                # Heuristic: left vs. right padding. If the first cell contains a code
                # (e.g., "3.2"), the missing cells are likely leading empty columns
                # (Subject/Competency columns). Otherwise, we assume they are trailing
                # empty columns.
                first_text = ""
                if cells and cells[0].text:
                    first_text = cells[0].text.text or ""

                # Regex for "1.2", "A.1", "3.2.1" at start of string.
                codeish = bool(
                    re.search(r"(^|\n)\s*[A-Z0-9]+(\.[A-Z0-9]+)+", first_text)
                )
                pad = [
                    TableCell(col_span=1, row_span=1, text=None) for _ in range(missing)
                ]
                row.cells = (pad + cells) if codeish else (cells + pad)
                changes.append(
                    {
                        "after": n_cols,
                        "before_cells": len(cells),
                        "before_effective_cols": effective_cols,
                        "item_index": item_index,
                        "page": page_index,
                        "row_index": row_index,
                        "side": "left" if codeish else "right",
                        "type": "pad_table_row_cells",
                    }
                )

    return changes


def persist_verification_run(
    *, config: VerificationConfig, output_dir: Path
) -> tuple[PageIRVerificationDirs, RunCtx]:
    """Persist verification run metadata.

    Parameters
    ----------
    config
        The verification run configuration.
    output_dir
        The output directory for the verification run results.

    Returns
    -------
    tuple[PageIRVerificationDirs, RunCtx]
        The created verification directories and persisted verification run metadata.
    """

    verification_dirs = create_page_ir_verification_dirs(output_dir=output_dir)
    verification_run = RunCtx(
        extra={
            "end_page_cli": config.end_page,  # Keep original config value (may be None)
            "min_confidence_to_patch": config.min_confidence_to_patch,
            "start_page_cli": config.start_page,
        },
        models=[config.model],
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(fp=output_dir / "verification_run.json", json_info=verification_run)
    logger.info(f"Saving verification results to: {verification_dirs.root}")

    return verification_dirs, verification_run


def postprocess_verified_page_irs(
    *, page_irs: dict[int, PageIR], verification_dirs: PageIRVerificationDirs
) -> None:
    """Run all postpass fixes before writing verified JSONs.

    NB: Order matters

    1. Propagate codes (relies on correct boundaries from verification).
    2. Normalize cells (relies on correct table structures).

    Parameters
    ----------
    page_irs
        The dictionary of page IRs by page index.
    verification_dirs
        The verification directories.
    """

    # 1. Enrich data by flowing local codes across the now-verified boundaries.
    table_code_changes = propagate_table_local_codes(page_irs=page_irs)

    # Fix structural "empty cell" hallucinations from the extraction model.
    pad_changes = normalize_table_row_cell_counts(page_irs=page_irs)

    # Persist what was changed for audit/debug.
    write_to_json(
        fp=verification_dirs.root / "postprocess_report.json",
        json_info={
            "table_local_code_changes": table_code_changes,
            "table_row_padding_changes": pad_changes,
        },
    )


def propagate_table_local_codes(*, page_irs: dict[int, PageIR]) -> list[dict[str, Any]]:
    """Carry forward "Table X" codes across VERIFIED continuation boundaries. This
    relies on the 'is_continuation' verdict having already set the correct
    TRUNCATED/RESUMED/BOTH flags.

    Parameters
    ----------
    page_irs
        The dictionary of page IRs by page index.

    Returns
    -------
    list[dict[str, Any]]
        A list of changes made during the postpass.
    """

    carried_table_code: str | None = None
    changes: list[dict[str, Any]] = []

    for i in sorted(page_irs.keys()):
        page = page_irs[i]
        items = page.items or []

        # Update context if page has a caption (e.g., "Table 1 (continued)").
        if (caption_code := find_caption_code(items)) is not None:
            carried_table_code = caption_code

        table_continues_to_next = False

        for idx, item in enumerate(items):
            if item.kind != "table":
                continue

            # Check verified boundaries.
            boundary = item.boundary
            is_resumed_or_both = boundary in {ItemBoundary.BOTH, ItemBoundary.RESUMED}

            # If verify step said "Not a continuation", we break the chain.
            if not is_resumed_or_both:
                carried_table_code = None

            # Resolve local code.
            current_code = (item.local_code or "").strip()

            # Update carry if THIS item has an explicit code.
            code = (item.local_code or "").strip()
            if code:
                carried_table_code = code

            if current_code:
                # This table has its own code, it becomes the new carrier.
                carried_table_code = current_code
            elif is_resumed_or_both and carried_table_code:
                # Continuation without code --> inherit from previous.
                item.local_code = carried_table_code
                changes.append(
                    {
                        "item_index": idx,
                        "page": i,
                        "set_local_code": carried_table_code,
                        "type": "propagate_table_local_code",
                    }
                )

            # Prepare for next page. If verify step said "Truncated", we keep the code
            # alive for page N+1.
            if boundary in {ItemBoundary.TRUNCATED, ItemBoundary.BOTH}:
                table_continues_to_next = True

        # If the chain breaks at the page level, reset.
        carried_table_code = None if not table_continues_to_next else carried_table_code

    return changes


def save_verified_page_irs(
    *, page_irs: dict[int, PageIR], verification_dirs: PageIRVerificationDirs
) -> None:
    """Save verified page IRs to the verified directory.

    Parameters
    ----------
    page_irs
        The dictionary of page IRs by page index.
    verification_dirs
        The verification directories.
    """

    logger.info(
        f"Saving all verified page IR JSONs to: {verification_dirs.page_irs_verified}"
    )

    for i in sorted(page_irs.keys()):
        page_ir = page_irs[i]

        # Derive page-level boundary_state from verified item boundaries.
        page_ir.boundary_state = derive_page_boundary_state(page_ir=page_ir)

        # Write verified JSON.
        write_to_json(
            fp=verification_dirs.page_irs_verified / f"{i:04}.json", json_info=page_ir
        )

    logger.success("All verified page IR JSONs saved successfully!")


def strip_continuity_hints(item_json: dict[str, Any]) -> dict[str, Any]:
    """Remove continuity metadata so the LLM isn't biased by extractor state.

    Parameters
    ----------
    item_json
        The item JSON dictionary.

    Returns
    -------
    dict[str, Any]
        The cleaned item JSON dictionary.
    """

    output = deepcopy(item_json)
    output.pop("boundary", None)

    # Only tables have repeats_header.
    if output.get("kind") == "table":
        output.pop("repeats_header", None)

    return output


def topmost_continuity_candidate_paired(
    *,
    image_height: float,
    items: list[Block | Table],
    prev_item: Block | Table,
    visible_y_max: float | None = None,
) -> tuple[int, Block | Table]:
    """Pick the best "top of page" candidate, preferring the same kind as prev_item.

    The process is as follows:

    1. Filter out artifacts and noise.
    2. Sort by top edge (y0) ascending (closest to top first).
    3. Scan the top items:
        - If we find an item of the SAME kind as prev_item (Table/Block), return it.
        - This allows us to skip over a heading/caption to link Table-to-Table, or skip
            over a top-aligned Table to link Text-to-Text.
    4. Fallback: Return the absolute top-most item.

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    items
        List of items to search.
    prev_item
        The chosen previous page candidate item.
    visible_y_max
        If provided, restrict candidate selection to items whose bbox intersects the
        visible crop range [0, visible_y_max] in full-page coordinates.

    Returns
    -------
    tuple[int, Block | Table]
        The index and the chosen item.

    Raises
    ------
    ValueError
        If no non-artifact items or continuity candidates are found.
    """

    candidates = [
        (i, item)
        for i, item in enumerate(items)
        if not (
            is_artifact(item)
            or is_probable_header_footer_noise(image_height=image_height, item=item)
        )
    ]

    # If we are verifying using a top-crop image, restrict candidates to items that
    # actually appear in that crop (in full-page coordinate space). This prevents
    # choosing an item the model cannot see, which would cause false negatives.
    if visible_y_max is not None:
        y_max = float(visible_y_max)
        cropped = [
            (i, item)
            for i, item in candidates
            if _bbox_intersects_y_range(bbox=item.bbox, y_max=y_max, y_min=0.0)
        ]
        if cropped:
            candidates = cropped
        else:
            logger.warning(
                "No top-crop-visible candidates found; falling back to full-page "
                "candidate selection."
            )

    if not candidates:
        raise ValueError("No non-artifact items found.")

    # Sort by top-edge (y0) ascending (bbox is [x0, y0, x1, y1]).
    candidates.sort(key=lambda p: float(p[1].bbox[1]))

    # If prev ended with a TABLE, only choose a TABLE if it appears very near the top.
    # This aligns candidate selection with the "top crop" image used in verification.
    if prev_item.kind == "table":
        for i, item in candidates:
            if item.kind == "table":
                return i, item

        # No top-visible table candidate (likely not in the crop). Fall back to the
        # absolute topmost item.
        return candidates[0]

    # Otherwise (prev ended with a Block), pick the first non-table Block near the top,
    # but never anchor text continuation on a HEADING/CAPTION.
    for i, item in candidates:
        if item.kind != "table":
            if isinstance(item, Block) and item.block_type in {
                BlockType.CAPTION,
                BlockType.HEADING,
            }:
                continue
            return i, item

    return candidates[0]
