"""This module contains utility functions related to page IR **extraction**."""

# Standard Library
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Third Party Library
from loguru import logger

# Package Library
from skg.page_ir_extraction.schemas import PageIR
from skg.schemas import ExtractionConfig, RunCtx
from skg.utils.constants import ItemBoundary, PageBoundaryState
from skg.utils.general import make_dir, open_json_type, write_to_json
from skg.utils.pdf import compute_doc_key


@dataclass(frozen=True)
class PageIRExtractionDirs:
    """Dataclass for page IR extraction directories."""

    root: Path
    page_images: Path
    page_irs: Path
    page_irs_raw: Path


def create_page_ir_extraction_dirs(
    *, doc_key: str, output_dir: Path
) -> PageIRExtractionDirs:
    """Create page IR extraction directories for a given document key.

    Parameters
    ----------
    doc_key
        The document key.
    output_dir
        The output directory root.

    Returns
    -------
    PageIRExtractionDirs
        The created page IR extraction directories.
    """

    root = output_dir / doc_key / "extraction"
    page_images = root / "page_images"
    page_irs = root / "page_irs"
    page_irs_raw = root / "page_irs_raw"

    for p in [root, page_images, page_irs, page_irs_raw]:
        make_dir(p)

    return PageIRExtractionDirs(
        root=root, page_images=page_images, page_irs=page_irs, page_irs_raw=page_irs_raw
    )


def derive_boundary_state_from_items(
    non_artifact_items: list[tuple[int, Any]],
) -> PageBoundaryState:
    """Derive the page boundary state from item boundaries.

    Parameters
    ----------
    non_artifact_items
        The list of non-artifact items with their indices.

    Returns
    -------
    PageBoundaryState
        The derived page boundary state.
    """

    if not non_artifact_items:
        return PageBoundaryState.STANDALONE

    # Only consider non-artifact items for continuity.
    non_artifacts = [item for _, item in non_artifact_items]

    any_from_prev = any(is_resumed(item.boundary.value) for item in non_artifacts)
    any_to_next = any(is_truncated(item.boundary.value) for item in non_artifacts)

    if any_from_prev and any_to_next:
        return PageBoundaryState.BOTH
    if any_from_prev:
        return PageBoundaryState.CONTINUES_FROM_PREV
    if any_to_next:
        return PageBoundaryState.CONTINUES_TO_NEXT
    return PageBoundaryState.STANDALONE


def is_full_page_bbox(
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


def is_resumed(boundary: str) -> bool:
    """Check if a boundary string indicates a resumed or both item.

    Parameters
    ----------
    boundary
        The boundary string to check.

    Returns
    -------
    bool
        True if the boundary indicates a resumed or both item, False otherwise.
    """

    return boundary in (ItemBoundary.RESUMED.value, ItemBoundary.BOTH.value)


def is_truncated(boundary: str) -> bool:
    """Check if a boundary string indicates a truncated or both item.

    Parameters
    ----------
    boundary
        The boundary string to check.

    Returns
    -------
    bool
        True if the boundary indicates a truncated or both item, False otherwise.
    """

    return boundary in (ItemBoundary.TRUNCATED.value, ItemBoundary.BOTH.value)


def load_page_irs_from_extraction(
    *, end_page: int, page_irs_dir: Path, start_page: int
) -> dict[int, PageIR]:
    """Load page IR JSONs from the extraction output directory.

    Parameters
    ----------
    end_page
        0-based end page (exclusive).
    page_irs_dir
        Directory containing the page IR JSONs.
    start_page
        0-based start page (inclusive).

    Returns
    -------
    dict[int, PageIR]
        The dictionary of page IRs by page index.
    """

    return {
        i: PageIR.model_validate(open_json_type(page_irs_dir / f"{i:04}.json"))
        for i in range(start_page, end_page)
    }


def persist_extraction_run(
    *, config: ExtractionConfig
) -> tuple[str, PageIRExtractionDirs, RunCtx]:
    """Persist extraction run metadata.

    Parameters
    ----------
    config
        The extraction run configuration.

    Returns
    -------
    tuple[str, ExtractionDirs, RunCtx]
        The document key, extraction directories, and extraction run record.
    """

    doc_key = compute_doc_key(n_hex=64, pdf_fp=config.pdf_fp)
    extraction_dirs = create_page_ir_extraction_dirs(
        doc_key=doc_key, output_dir=config.output_dir
    )
    exclude_keys = {"model", "overwrite"}
    extra = {
        k: v for k, v in config.model_dump(mode="json").items() if k not in exclude_keys
    }
    extra["doc_key"] = doc_key
    extraction_run = RunCtx(
        extra=extra,
        models=[config.model],
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(
        fp=extraction_dirs.root / "extraction_run.json", json_info=extraction_run
    )
    logger.info(f"Saving extraction results to: {extraction_dirs.root}")

    return doc_key, extraction_dirs, extraction_run
