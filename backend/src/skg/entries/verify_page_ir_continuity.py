"""This module contains the entry point for verifying the continuity of the extracted
page IR JSONs from step 1. This is step 2

Invoke from the backend directory via:

python src/skg/entries/verify_page_ir_continuity.py ../data/ghana/ghana.pdf /path/to/extraction_run_results
python src/skg/entries/verify_page_ir_continuity.py ../data/tanzania/tanzania.pdf /path/to/extraction_run_results
python src/skg/entries/verify_page_ir_continuity.py ../data/uganda/uganda.pdf /path/to/extraction_run_results
python src/skg/entries/verify_page_ir_continuity.py ../data/zambia/zambia.pdf /path/to/extraction_run_results
"""

# Standard Library
import json
import sys
import traceback
import uuid

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Third Party Library
import pymupdf
import typer

from loguru import logger

# Append the framework path. NB: This is required if this entry point is invoked from
# the command line. However, it is not necessary if it is imported from a pip install.
if __name__ == "__main__":
    PACKAGE_PATH = Path(__file__).resolve().parents[2]
    if PACKAGE_PATH not in sys.path:
        print(f"Appending '{PACKAGE_PATH}' to system path...")
        sys.path.append(str(PACKAGE_PATH))

# Package Library
from skg.page_ir.schemas import PageIR, PageIRContinuityVerdict
from skg.page_ir.utils import (
    PageIRVerificationDirs,
    bottommost_continuity_candidate,
    create_page_ir_verification_dirs,
    ensure_boundary,
    is_figure_block,
    item_snippet,
    min_crop_height_px,
    pad_inches,
    set_boundary_flag,
    topmost_continuity_candidate,
)
from skg.schemas import VerificationRunIR
from skg.utils.constants import ItemBoundary, PageBoundaryState
from skg.utils.general import compare_directories, open_json_type, write_to_json
from skg.utils.openai_ import validate_continuity_verdict, verify_page_ir_pairs
from skg.utils.pdf import crop_image_to_bottom, crop_image_to_top, validate_page_count

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


def apply_continuity_edits(
    *,
    next_idx: int,
    next_item: dict[str, Any],
    next_page_ir: dict[str, Any],
    next_page_items: list[dict[str, Any]],
    prev_idx: int,
    prev_page_ir: dict[str, Any],
    prev_page_items: list[dict[str, Any]],
    verdict: PageIRContinuityVerdict,
) -> None:
    """Apply minimal edits to page IRs based on the continuity verdict.

    Parameters
    ----------
    next_idx
        Index of the continuity candidate item on the next page.
    next_item
        The actual item dictionary for the next page candidate.
    next_page_ir
        The full PageIR object for the next page.
    next_page_items
        The list of items on the next page.
    prev_idx
        Index of the continuity candidate item on the previous page.
    prev_page_ir
        The full PageIR object for the previous page.
    prev_page_items
        The list of items on the previous page.
    verdict
        The continuity verdict from the model.
    """

    # 1. Update page-level boundary states (pairwise-safe, only one direction each).
    # If the model suggests an explicit boundary state, apply it directly.
    # Otherwise, default to verdict.is_continuation.
    prev_to_next = (
        verdict.is_continuation
        if verdict.set_prev_boundary_state is None
        else (
            getattr(
                verdict.set_prev_boundary_state,
                "value",
                verdict.set_prev_boundary_state,
            )
            == "to_next"
        )
    )
    next_from_prev = (
        verdict.is_continuation
        if verdict.set_next_boundary_state is None
        else (
            getattr(
                verdict.set_next_boundary_state,
                "value",
                verdict.set_next_boundary_state,
            )
            == "from_prev"
        )
    )
    set_boundary_flag(
        page_ir=prev_page_ir,
        flag=PageBoundaryState.CONTINUES_TO_NEXT.value,
        value=prev_to_next,
    )
    set_boundary_flag(
        page_ir=next_page_ir,
        flag=PageBoundaryState.CONTINUES_FROM_PREV.value,
        value=next_from_prev,
    )

    # 2. Update item-level boundaries (explicit edits from model).
    if verdict.set_prev_item_boundary is not None:
        ensure_boundary(
            desired=getattr(
                verdict.set_prev_item_boundary, "value", verdict.set_prev_item_boundary
            ),
            items=prev_page_items,
            index=prev_idx,
        )

    if verdict.set_next_item_boundary is not None:
        ensure_boundary(
            desired=getattr(
                verdict.set_next_item_boundary, "value", verdict.set_next_item_boundary
            ),
            items=next_page_items,
            index=next_idx,
        )

    # 3. Enforce item-level consistency (implicit edits).
    if verdict.is_continuation:
        # If model verified continuity but didn't explicitly set boundaries, force
        # defaults.
        if not verdict.set_prev_item_boundary:
            ensure_boundary(
                desired=ItemBoundary.TRUNCATED.value,
                items=prev_page_items,
                index=prev_idx,
            )
        if not verdict.set_next_item_boundary:
            ensure_boundary(
                desired=ItemBoundary.RESUMED.value,
                items=next_page_items,
                index=next_idx,
            )
    else:
        # If confident NOT a continuation, clear stray "truncated"/"resumed" flags on
        # these specific candidates.
        if not verdict.set_prev_item_boundary and prev_page_items[prev_idx].get(
            "boundary"
        ) in (ItemBoundary.TRUNCATED.value, ItemBoundary.RESUMED.value):
            ensure_boundary(
                desired=ItemBoundary.COMPLETE.value,
                items=prev_page_items,
                index=prev_idx,
            )
        if not verdict.set_next_item_boundary and next_page_items[next_idx].get(
            "boundary"
        ) in (ItemBoundary.TRUNCATED.value, ItemBoundary.RESUMED.value):
            ensure_boundary(
                desired=ItemBoundary.COMPLETE.value,
                items=next_page_items,
                index=next_idx,
            )

    # 4. Table header updates.
    if (
        verdict.set_next_table_repeats_header is not None
        and next_item.get("kind") == "table"
        and verdict.is_continuation
        and getattr(verdict.continuation_kind, "value", verdict.continuation_kind)
        == "table"
    ):
        next_page_items[next_idx][
            "repeats_header"
        ] = verdict.set_next_table_repeats_header


def get_threshold_based_on_kind(
    *,
    next_item: dict[str, Any],
    prev_item: dict[str, Any],
    verdict: PageIRContinuityVerdict,
) -> float:
    """Get confidence threshold based on continuation kind.

    Parameters
    ----------
    next_item
        The actual item dictionary for the next page candidate.
    prev_item
        The actual item dictionary for the previous page candidate.
    verdict
        The continuity verdict from the model.

    Returns
    -------
    float
        The confidence threshold for applying implicit edits.
    """

    # Apply minimal edits for both loaded and new verdicts (only if model is confident
    # enough).
    kind = getattr(verdict.continuation_kind, "value", verdict.continuation_kind)

    # NB: "unclear" should almost never trigger implicit edits. Only exception we
    # allow is for true figure continuations (rare) and require high confidence.
    is_fig_pair = is_figure_block(prev_item) or is_figure_block(next_item)
    if kind == "table":
        threshold = 0.80
    elif kind == "text":
        threshold = 0.70
    elif kind == "none":
        threshold = 0.75  # Slightly higher than text; optional
    # Only apply if this is a figure continuation; otherwise skip edits entirely.
    elif not (verdict.is_continuation and is_fig_pair):
        threshold = 1.1  # Impossible threshold --> no edits
    else:
        threshold = 0.90
    return threshold


def persist_verification_run(
    *,
    end_page: Optional[int],
    model: str,
    output_dir: Path,
    overwrite: bool,
    start_page: int,
    **kwargs: dict[str, Any],
) -> tuple[PageIRVerificationDirs, VerificationRunIR]:
    """Persist verification run metadata.

    Parameters
    ----------
    end_page
        0-based end page (exclusive).
    model
        OpenAI model for page IR continuity verification.
    output_dir
        The output directory for the verified page IR JSONs.
    overwrite
        Specifies whether to overwrite existing per-page artifacts.
    start_page
        0-based start page (inclusive).
    kwargs
        Additional extraction run configuration parameters.

    Returns
    -------
    tuple[PageIRVerificationDirs, VerificationRunIR]
        The created verification directories and persisted verification run metadata.
    """

    extra = kwargs.get("extra", {})
    extra.update(
        {
            "end_page_cli": end_page,  # Keep original CLI value (may be None)
            "overwrite": overwrite,
            "start_page_cli": start_page,
        }
    )
    extra.pop("status", None)
    verification_dirs = create_page_ir_verification_dirs(output_dir=output_dir)
    verification_run = VerificationRunIR(
        models=[model],
        pipeline_version="0.1",
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
        extra=extra,
    )
    write_to_json(
        output_dir / "verification_run.json",
        json.loads(verification_run.model_dump_json(indent=2)),
    )
    logger.info(f"Verification directory: {output_dir}")

    return verification_dirs, verification_run


def verify_page_ir_continuity(
    *,
    doc: pymupdf.Document,
    end_page: int | None,
    verification_dirs: PageIRVerificationDirs,
    model: str,
    overwrite: bool,
    page_images_dir: Path,
    page_irs_dir: Path,
    render_dpi: int,
    start_page: int,
) -> None:
    """Perform verification of PageIR JSONs in pairs.

    Parameters
    ----------
    doc
        The PyMuPDF document.
    end_page
        0-based end page (exclusive).
    verification_dirs
        The verification directories.
    model
        OpenAI model for page IR verification.
    overwrite
        Overwrite existing per-page artifacts.
    page_images_dir
        Directory containing the page images.
    page_irs_dir
        Directory containing the page IR JSONs.
    render_dpi
        The render DPI for the page images during the extraction stage.
    start_page
        0-based start page (inclusive).
    """

    # Load all page IR indices (0000.json style).
    json_fps = sorted(page_irs_dir.glob("*.json"))
    page_indices = sorted(int(fp.stem) for fp in json_fps if fp.stem.isdigit())
    assert page_indices, f"No page IR JSONs found in: {page_irs_dir}"

    doc_page_count = doc.page_count
    start = max(start_page, page_indices[0])
    stop = (
        end_page if end_page is not None else min(doc_page_count, page_indices[-1] + 1)
    )

    # Load all page IR JSONs so that we can apply edits and then write once.
    page_irs: dict[int, dict[str, Any]] = {
        i: PageIR.model_validate(
            open_json_type(page_irs_dir / f"{i:04}.json")
        ).model_dump(mode="json")
        for i in range(start, stop)
    }

    # Iterate in pairs.
    for i in range(start, stop - 1):
        report_fp = verification_dirs.page_irs_pair_reports / f"{i:04}_{i + 1:04}.json"

        # Always load the pair and choose candidates (needed whether we call the model
        # or load report).
        assert (
            i in page_irs and (i + 1) in page_irs
        ), f"Missing page IR for pages {i} or {i + 1}"

        prev_page_ir, next_page_ir = page_irs[i], page_irs[i + 1]
        prev_page_items = prev_page_ir.get("items", [])
        next_page_items = next_page_ir.get("items", [])

        if not prev_page_items or not next_page_items:
            logger.warning(
                f"Skipping continuity check for pages {i}-{i + 1}: "
                f"prev_items={len(prev_page_items)} next_items={len(next_page_items)}"
            )
            # Conservative: assume no continuation across an empty page boundary.
            set_boundary_flag(
                page_ir=prev_page_ir,
                flag=PageBoundaryState.CONTINUES_TO_NEXT.value,
                value=False,
            )
            set_boundary_flag(
                page_ir=next_page_ir,
                flag=PageBoundaryState.CONTINUES_FROM_PREV.value,
                value=False,
            )
            continue

        prev_idx, prev_item = bottommost_continuity_candidate(
            image_height=prev_page_ir["image_height"], items=prev_page_items
        )
        next_idx, next_item = topmost_continuity_candidate(
            image_height=next_page_ir["image_height"], items=next_page_items
        )

        if report_fp.exists() and not overwrite:
            logger.info(f"Report exists for {i}-{i + 1}; loading (overwrite=False).")
            verdict = PageIRContinuityVerdict.model_validate(open_json_type(report_fp))
            validate_continuity_verdict(verdict)
        else:
            logger.info(f"Verifying continuity between pages {i} and {i + 1}...")

            prev_image_full = page_images_dir / f"{i:04}.png"
            next_image_full = page_images_dir / f"{i + 1:04}.png"

            # Crop bottom of prev and top of next based on non-artifact bboxes.
            prev_crop_fp = verification_dirs.page_irs_pair_crops / f"{i:04}_bottom.png"
            next_crop_fp = verification_dirs.page_irs_pair_crops / f"{i + 1:04}_top.png"

            prev_page_h_px = int(prev_page_ir["image_height"])
            next_page_h_px = int(next_page_ir["image_height"])

            prev_kind = prev_item.get("kind", "block")
            if prev_kind == "block" and is_figure_block(prev_item):
                prev_kind = "figure"

            next_kind = next_item.get("kind", "block")
            if next_kind == "block" and is_figure_block(next_item):
                next_kind = "figure"

            prev_min_h = min_crop_height_px(page_h_px=prev_page_h_px, kind=prev_kind)
            next_min_h = min_crop_height_px(page_h_px=next_page_h_px, kind=next_kind)

            crop_image_to_bottom(
                bbox=prev_item["bbox"],
                desired_padding_inches=pad_inches(prev_kind),
                input_png_fp=prev_image_full,
                min_height_px=prev_min_h,
                output_png_fp=prev_crop_fp,
                render_dpi=render_dpi,
            )
            crop_image_to_top(
                bbox=next_item["bbox"],
                desired_padding_inches=pad_inches(next_kind),
                input_png_fp=next_image_full,
                min_height_px=next_min_h,
                output_png_fp=next_crop_fp,
                render_dpi=render_dpi,
            )

            # Invoke the model to verify the pair.
            verdict = verify_page_ir_pairs(
                model=model,
                next_page_index=i + 1,
                next_top_png=next_crop_fp,
                prev_bottom_png=prev_crop_fp,
                prev_page_index=i,
                prev_item_excerpt=item_snippet(item=prev_item),
                next_item_excerpt=item_snippet(item=next_item),
            )

            # Persist the verdict.
            write_to_json(report_fp, verdict.model_dump(mode="json"))

        threshold = get_threshold_based_on_kind(
            next_item=next_item, prev_item=prev_item, verdict=verdict
        )
        if verdict.confidence >= threshold:
            apply_continuity_edits(
                next_idx=next_idx,
                next_item=next_item,
                next_page_ir=next_page_ir,
                next_page_items=next_page_items,
                prev_idx=prev_idx,
                prev_page_ir=prev_page_ir,
                prev_page_items=prev_page_items,
                verdict=verdict,
            )

    # Write verified JSONs.
    for i, page_ir in page_irs.items():
        write_to_json(verification_dirs.page_irs_verified / f"{i:04}.json", page_ir)
    logger.success("All verified page IR JSONs written successfully!")


@cli.command()
def verify(  # pylint: disable=too-many-positional-arguments
    pdf_fp: Path = typer.Argument(
        ...,
        dir_okay=False,
        exists=True,
        file_okay=True,
        help="The file path to the PDF document to extract curriculum data from.",
        readable=True,
        resolve_path=True,
    ),
    extraction_run_results_dir: Path = typer.Argument(
        ...,
        dir_okay=True,
        exists=True,
        file_okay=False,
        help="The extraction run results directory.",
        resolve_path=True,
    ),
    model: str = typer.Option(
        "gpt-5.2-2025-12-11",
        "--model",
        "-m",
        help="OpenAI model for page IR extraction.",
    ),
    start_page: int = typer.Option(
        0, "--start-page", "-s", help="0-based start page (inclusive)."
    ),
    end_page: Optional[int] = typer.Option(
        None, "--end-page", "-e", help="0-based end page (exclusive). Default: to end."
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Overwrite existing per-page artifacts."
    ),
) -> None:
    """Verify page IR JSON continuity from the extraction step.

    The process is as follows:

    1. Check that the page images and page IR directories have matching files.
    2. Persist verification run metadata.
    3. Validate page range.
    4. For each page in the specified range:
    5. Finalize verification run record.

    Parameters
    ----------
    pdf_fp
        The file path to the PDF document to verify continuity for.
    extraction_run_results_dir
        Directory containing the extraction run results.
    model
        OpenAI model for page IR continuity verification.
    start_page
        0-based start page (inclusive).
    end_page
        0-based end page (exclusive). Default: to end.
    overwrite
        Overwrite existing per-page artifacts.
    """

    extraction_run_results_dir = extraction_run_results_dir.resolve()
    page_images_dir = extraction_run_results_dir / "page_images"
    page_irs_dir = extraction_run_results_dir / "page_irs"
    extraction_config_fp = extraction_run_results_dir / "extraction_run.json"
    extraction_run_config = open_json_type(extraction_config_fp)
    verification_results_dir = extraction_run_results_dir.parent / "verification"

    # 1.
    assert compare_directories(page_images_dir, page_irs_dir)

    # 2.
    verification_dirs, verification_run = persist_verification_run(
        end_page=end_page,
        model=model,
        output_dir=verification_results_dir,
        overwrite=overwrite,
        start_page=start_page,
        **extraction_run_config,
    )

    logger.info(
        f"Starting page IR continuity verification process using directories: "
        f"{page_images_dir} and {page_irs_dir}"
    )
    logger.info(f"Loaded extraction run config: {extraction_run_config}")
    logger.info(f"Outputting verification results to: {verification_results_dir}")

    try:
        with pymupdf.open(str(pdf_fp)) as doc:
            # 3.
            _, end_page = validate_page_count(
                doc=doc, end_page=end_page, start_page=start_page
            )

            # 4.
            verify_page_ir_continuity(
                doc=doc,
                end_page=end_page,
                verification_dirs=verification_dirs,
                model=model,
                overwrite=overwrite,
                page_images_dir=page_images_dir,
                page_irs_dir=page_irs_dir,
                render_dpi=verification_run.extra["dpi"],
                start_page=start_page,
            )
        verification_run.extra["status"] = "success"
        logger.success("Page IR continuity verification completed successfully!")
    except Exception as e:  # pylint: disable=broad-except
        verification_run.extra["status"] = "error"
        verification_run.extra["error"] = {
            "message": str(e),
            "traceback": traceback.format_exc(limit=20),
            "type": e.__class__.__name__,
        }
        raise
    finally:
        # 5.
        verification_run.completed_at = datetime.now(timezone.utc)
        write_to_json(
            verification_dirs.root / "verification_run.json",
            verification_run.model_dump(mode="json"),
        )


if __name__ == "__main__":
    cli()
