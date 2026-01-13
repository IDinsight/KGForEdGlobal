"""This module contains utility functions related to page IR **verification**."""

# Standard Library
import re
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Third Party Library
from loguru import logger

# Package Library
from skg.page_ir_extraction.schemas import Block, PageIR, Table, TableCell, TextUnit
from skg.page_ir_verification.schemas import PageIRContinuityVerdict, VerificationConfig
from skg.schemas import RunCtx
from skg.utils.constants import BlockType, ItemBoundary, PageBoundaryState
from skg.utils.general import make_dir, write_to_json


@dataclass(frozen=True)
class PageIRVerificationDirs:
    """Dataclass for page IR verification directories."""

    root: Path
    page_irs_pair_crops: Path
    page_irs_pair_reports: Path
    page_irs_verified: Path


def _merge_boundary(
    *, existing: ItemBoundary | None, patch: ItemBoundary
) -> ItemBoundary:
    """Merge a boundary patch into an existing boundary, upgrading to BOTH when needed.

    Parameters
    ----------
    existing
        The existing boundary state, or None if not set.
    patch
        The boundary state to apply.

    Returns
    -------
    ItemBoundary
        The merged boundary state.
    """

    # If the patch is definitive (BOTH/COMPLETE) or we have no history, the patch wins.
    if existing is None or patch in (ItemBoundary.BOTH, ItemBoundary.COMPLETE):
        return patch

    # If existing is already BOTH, it absorbs partial updates (TRUNCATED/RESUMED).
    if existing == ItemBoundary.BOTH:
        return existing

    # If we have one TRUNCATED and one RESUMED (in any order), they combine to BOTH.
    # Note: We use a set for order-independent comparison.
    if {existing, patch} == {ItemBoundary.TRUNCATED, ItemBoundary.RESUMED}:
        return ItemBoundary.BOTH

    # In all other cases (e.g., existing==patch, or existing is COMPLETE but patch is
    # TRUNCATED), the patch simply overwrites the state.
    return patch


def apply_continuity_verdict(
    *,
    min_confidence_to_patch: float,
    next_item: Block | Table,
    prev_item: Block | Table,
    verdict: PageIRContinuityVerdict,
) -> dict[str, Any] | None:
    """Apply LLM verdict edits to the *actual* PageIR items (mutates in-place).

    Parameters
    ----------
    min_confidence_to_patch
        Minimum confidence threshold to apply edits.
    next_item
        The next page candidate item.
    prev_item
        The previous page candidate item.
    verdict
        The continuity verdict from the model.

    Returns
    -------
    dict[str, Any] | None
        A dictionary of applied edits, or None if no edits were applied.
    """

    if verdict.confidence < min_confidence_to_patch:
        logger.warning(
            f"Verdict confidence {verdict.confidence} below threshold "
            f"{min_confidence_to_patch}, skipping edits."
        )
        return None

    edits: dict[str, Any] = {}

    if verdict.set_prev_item_boundary is not None:
        before = prev_item.boundary
        after = _merge_boundary(existing=before, patch=verdict.set_prev_item_boundary)
        if after != before:
            prev_item.boundary = after
            edits["prev_boundary"] = {"before": before, "after": after}

    if verdict.set_next_item_boundary is not None:
        before = next_item.boundary
        after = _merge_boundary(existing=before, patch=verdict.set_next_item_boundary)
        if after != before:
            next_item.boundary = after
            edits["next_boundary"] = {"before": before, "after": after}

    if verdict.set_next_table_repeats_header is not None:
        # Validators should ensure this only happens when next_item is a table,
        # but keep it safe.
        if next_item.kind == "table":
            before = getattr(next_item, "repeats_header", None)
            after = verdict.set_next_table_repeats_header
            if after != before:
                next_item.repeats_header = after
                edits["next_repeats_header"] = {"before": before, "after": after}

    return edits or None


def bottommost_continuity_candidate(
    *, image_height: float, items: list[Block | Table]
) -> tuple[int, Block | Table]:
    """Pick the best "bottom of page" candidate for continuity checks.

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    items
        List of PageIR items on the page.

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

    if not candidates:
        raise ValueError("No non-artifact items found.")

    # Sort by bottom-edge (y1) descending (bbox is [x0, y0, x1, y1]).
    candidates.sort(key=lambda c: float(c[1].bbox[3]), reverse=True)

    # Prefer a Table if it is "near" the bottom (within the bottom 3 items). This
    # protects against cases where a small footnote or page number (that wasn't caught
    # by the noise filter) sits slightly below a large table.
    for i, item in candidates[:3]:
        if item.kind == "table":
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


def is_figure_block(item: Block | Table) -> bool:
    """Check if an item is a figure/diagram block.

    Parameters
    ----------
    item
        The item to check.

    Returns
    -------
    bool
        True if the item is a figure block, False otherwise.
    """

    return (
        False
        if item.kind != "block"
        else item.block_type.value == BlockType.FIGURE.value
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

    text_or_none = item.text
    text = text_or_none.text.strip() if isinstance(text_or_none, TextUnit) else ""

    if item.kind != "block" or not text:
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
    if len(t) <= 12 and re.fullmatch(r"(\d+|[ivxlcdm]+)", t.lower()):
        return True
    if len(t) <= 20 and re.fullmatch(r"(page\s*)?\d+(\s*/\s*\d+)?", t.lower()):
        return True

    return False


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

                # Keep as is.
                current_len = len(cells)
                if current_len >= n_cols:
                    continue

                missing = n_cols - len(cells)

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
                        "before": current_len,
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


def topmost_continuity_candidate_paired(
    *, image_height: float, items: list[Block | Table], prev_item: Block | Table
) -> tuple[int, Block | Table]:
    """Pick the best "top of page" candidate, preferring the same kind as prev_item.

    The process is as follows:

    1. Filter out artifacts and noise.
    2. Sort by top edge (y0) ascending (closest to top first).
    3. Scan the top 5 items:
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

    if not candidates:
        raise ValueError("No non-artifact items found.")

    # Sort by top-edge (y0) ascending (bbox is [x0, y0, x1, y1]).
    candidates.sort(key=lambda p: float(p[1].bbox[1]))

    # Scan top 5 items for a "kind" match. If the previous page ended with a Table, we
    # look for a Table at the top of this page (skipping headers/text). If it ended
    # with a Block, we look for a Block (skipping a table that might sit at the top).
    target_kind = prev_item.kind

    for i, item in candidates[:5]:
        current_kind = item.kind

        if target_kind == "table" and current_kind == "table":
            return i, item

        if target_kind != "table" and current_kind != "table":
            return i, item

    return candidates[0]
