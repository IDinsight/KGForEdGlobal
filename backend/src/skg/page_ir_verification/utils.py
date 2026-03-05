"""This module contains utility functions related to page IR **verification**."""

# Standard Library
import re
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional

# Third Party Library
from loguru import logger

# Package Library
from skg.page_ir_extraction.schemas import Block, PageIR, Table, TextUnit
from skg.page_ir_verification.schemas import PageIRContinuityVerdict
from skg.schemas import ExtractionConfig, RunCtx, VerificationConfig
from skg.utils.constants import BlockType, ItemBoundary, PageBoundaryState
from skg.utils.general import (
    compare_directories,
    make_dir,
    open_json_type,
    write_to_json,
)


class EdgeVerdictRecord(NamedTuple):
    """Edge verdict record between two page IR candidates."""

    next_item_index: int
    next_page_index: int
    prev_item_index: int
    prev_page_index: int
    verdict: PageIRContinuityVerdict


@dataclass(frozen=True)
class PageIRVerificationDirs:
    """Dataclass for page IR verification directories."""

    root: Path
    page_irs_pair_crops: Path
    page_irs_pair_reports: Path
    page_irs_verified: Path


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


def cross_check_extraction_run(
    *,
    computed_doc_key: str,
    end_page: int,
    expected_doc_key: str,
    extraction_config: ExtractionConfig,
    page_images_dir: Path,
    page_irs_dir: Path,
    start_page: int,
) -> tuple[int, int]:
    """Cross-check that the extraction run matches expected parameters and that page
    IRs are present.

    Parameters
    ----------
    computed_doc_key
        The document key computed from the source PDF bytes by the caller.
    end_page
        The exclusive end page index for continuity verification.
    expected_doc_key
        The expected document key (hex string) from the extraction run metadata.
    extraction_config
        The extraction configuration used for the run.
    page_images_dir
        Directory containing the rendered page images from extraction.
    page_irs_dir
        Directory containing the extracted page IR JSON files.
    start_page
        The inclusive start page index for continuity verification.

    Returns
    -------
    tuple[int, int]
        The verified (start_page, end_page) range for which page IR continuity can be
        verified.

    Raises
    ------
    ValueError
        If the computed document key does not match the expected key.
        If page IR continuity verification fails due to missing pages in the specified
            range.
    """

    if start_page < 0 or end_page < 0:
        raise ValueError(
            f"start_page and end_page must be non-negative "
            f"(got start_page={start_page}, end_page={end_page})."
        )

    if start_page >= end_page:
        raise ValueError(
            f"start_page must be less than end_page "
            f"(got start_page={start_page}, end_page={end_page})."
        )

    if not compare_directories(page_images_dir, page_irs_dir):
        raise ValueError(
            f"Page images and page IR directories do not have matching files:\n"
            f"  page_images_dir: {page_images_dir}\n"
            f"  page_irs_dir:    {page_irs_dir}"
        )

    if computed_doc_key != expected_doc_key:
        raise ValueError(
            f"PDF doc_key mismatch.\n"
            f"  PDF provided to verify(): {extraction_config.pdf_fp}\n"
            f"  computed doc_key:         {computed_doc_key}\n"
            f"  extraction_run.json key:  {expected_doc_key}\n"
            f"You are likely verifying against a different PDF than the one used for "
            f"extraction. Pass the same PDF used in the extraction step or re-run "
            f"extraction."
        )

    json_fps = sorted(page_irs_dir.glob("*.json"))
    page_indices = sorted(int(fp.stem) for fp in json_fps if fp.stem.isdigit())

    if not page_indices:
        raise ValueError(f"No page IR JSONs found in: {page_irs_dir}")

    start = max(start_page, page_indices[0])
    end = min(end_page, page_indices[-1] + 1)  # +1 because end is exclusive

    if start >= end:
        raise ValueError(
            f"Requested verification range [{start_page}, {end_page}) does not overlap "
            f"available extracted pages [{page_indices[0]}, {page_indices[-1] + 1}). "
            f"Computed range after clamping is [{start}, {end}), which is empty. "
            f"Adjust start_page/end_page or re-run extraction."
        )

    # Continuity verification requires every page in [start, end) to exist.
    required = set(range(start, end))
    available = set(page_indices)
    missing = sorted(required - available)

    if missing:
        raise ValueError(
            f"Page IR continuity verification requires contiguous pages in "
            f"[{start}, {end}), but page IRs are missing for indices: {missing}. "
            f"Re-run extraction for the missing pages or adjust start_page/end_page."
        )

    return start, end


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

    items = page_ir.items
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
        item.boundary in {ItemBoundary.RESUMED, ItemBoundary.BOTH}
        for item in candidates
    )
    to_next = any(
        item.boundary in {ItemBoundary.TRUNCATED, ItemBoundary.BOTH}
        for item in candidates
    )

    if from_prev and to_next:
        return PageBoundaryState.BOTH
    if from_prev:
        return PageBoundaryState.CONTINUES_FROM_PREV
    if to_next:
        return PageBoundaryState.CONTINUES_TO_NEXT
    return PageBoundaryState.STANDALONE


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

    return False if item.kind != "block" else item.block_type == BlockType.ARTIFACT


def is_probable_header_footer_noise(
    *, image_height: float, item: Block | Table
) -> bool:
    """Heuristic to exclude common header/footer noise, primarily page-number-like
    tokens.

    This heuristic is intentionally conservative: it targets short page-number patterns
    near the top/bottom margins and does not attempt to remove arbitrary running
    headers.

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


def load_edge_verdict_from_pair_report(pair_report_fp: Path) -> EdgeVerdictRecord:
    """Load a single EdgeVerdictRecord from a persisted pair report JSON.

    Parameters
    ----------
    pair_report_fp
        Path to the pair report JSON file (e.g., `0003_0004.json`).

    Returns
    -------
    EdgeVerdictRecord
        The reconstructed edge verdict record.

    Raises
    ------
    KeyError
        If the pair report JSON is missing required fields.
    """

    data = open_json_type(pair_report_fp)
    verdict = PageIRContinuityVerdict.model_validate(data["verdict"])
    selection = data["selected_candidate_selection"]
    return EdgeVerdictRecord(
        next_item_index=selection["next_item_index"],
        next_page_index=verdict.next_page_index,
        prev_item_index=selection["prev_item_index"],
        prev_page_index=verdict.prev_page_index,
        verdict=verdict,
    )


def load_page_irs_from_verification(
    *, doc_key: Optional[str], verified_page_irs_dir: Path
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
            "extraction_run.json is missing extra.doc_key (expected_doc_key)."
        )

    doc_keys = {p.doc_key for p in page_irs if p.doc_key}
    pdf_names = {p.pdf_name for p in page_irs if p.pdf_name}

    if not doc_keys:
        raise ValueError(
            "All verified PageIRs are missing doc_key. "
            "Ensure extraction step populates PageIR.doc_key for every page."
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

    if len(coord_spaces) > 1 or len(dpis) > 1:
        raise ValueError(
            "Inconsistent coordinate spaces or DPIs across pages:\n"
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


def load_verification_verdicts(
    verdict_dir: Path,
) -> dict[tuple[int, int], EdgeVerdictRecord]:
    """Load all verification verdict JSONs and return validated EdgeVerdictRecords.

    Delegates to `load_edge_verdict_from_pair_report` for each file, ensuring
    consistent parsing logic across resumed runs and batch loading. Files that fail to
    parse or have None page indices are skipped with a warning.

    Parameters
    ----------
    verdict_dir
        Directory containing `*.json` verdict files (e.g., `0003_0004.json`).

    Returns
    -------
    dict[tuple[int, int], EdgeVerdictRecord]
        Mapping of (prev_page_index, next_page_index) to validated record.

    Raises
    ------
    NotADirectoryError
        If the specified verdict_dir is not a directory.
    """

    verdicts: dict[tuple[int, int], EdgeVerdictRecord] = {}

    if not verdict_dir.is_dir():
        raise NotADirectoryError(f"Verdict directory not found: {verdict_dir}")

    for fp in sorted(verdict_dir.glob("*.json")):
        record = load_edge_verdict_from_pair_report(fp)
        assert (
            record.prev_page_index is not None and record.next_page_index is not None
        ), "load_edge_verdict_from_pair_report should raise if page indices are missing"
        verdicts[(record.prev_page_index, record.next_page_index)] = record

    logger.info(f"Loaded {len(verdicts)} verification verdict(s) from: {verdict_dir}")

    return verdicts


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
    exclude_keys = {"model", "overwrite"}
    extra = {
        k: v for k, v in config.model_dump(mode="json").items() if k not in exclude_keys
    }
    verification_run = RunCtx(
        extra=extra,
        models=[config.model],
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(fp=output_dir / "verification_run.json", json_info=verification_run)

    logger.info(f"Saving verification results to: {verification_dirs.root}")

    return verification_dirs, verification_run


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
