"""This module contains the entry point for verifying the continuity of the extracted
page IR JSONs from step 1. This is step 2.

Step 2 reads per-page PageIR JSONs from Step 1, runs pairwise (N, N+1) continuity
verification on selected boundary-candidate items, and writes corrected PageIR JSONs
to the verification output directory.

Key guarantee for Step 3: boundaries are patched when confidence is high; otherwise
extraction boundaries are preserved.

Invoke from the backend directory via:

python src/skg/entries/verify_page_ir_continuity.py ../examples/tanzania/config.json
"""

# Standard Library
import sys
import traceback

from datetime import datetime, timezone
from pathlib import Path

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
from skg.page_ir_extraction.schemas import ExtractionConfig
from skg.page_ir_extraction.utils import load_page_irs_from_extraction
from skg.page_ir_verification.llm import verify_page_ir_pairs
from skg.page_ir_verification.schemas import VerificationConfig
from skg.page_ir_verification.utils import (
    PageIRVerificationDirs,
    apply_continuity_verdict,
    bottommost_continuity_candidate,
    persist_verification_run,
    postprocess_verified_page_irs,
    save_verified_page_irs,
    topmost_continuity_candidate_paired,
)
from skg.schemas import RunCtx
from skg.utils.general import compare_directories, open_json_type, write_to_json
from skg.utils.pdf import compute_doc_key, validate_page_count

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


def verify_page_ir_continuity(
    *,
    config: VerificationConfig,
    doc: pymupdf.Document,
    end_page: int | None,
    page_images_dir: Path,
    page_irs_dir: Path,
    verification_dirs: PageIRVerificationDirs,
) -> None:
    """Perform verification of PageIR JSONs in pairs.

    Parameters
    ----------
    config
        The verification run configuration.
    doc
        The PyMuPDF document.
    end_page
        0-based end page (exclusive).
    page_images_dir
        Directory containing the page images.
    page_irs_dir
        Directory containing the page IR JSONs.
    verification_dirs
        The verification directories.

    Raises
    ------
    RuntimeError
        If continuity verification fails completely for any page pair.
    """

    # Load all page IR indices (0000.json style).
    json_fps = sorted(page_irs_dir.glob("*.json"))
    page_indices = sorted(int(fp.stem) for fp in json_fps if fp.stem.isdigit())
    assert page_indices, f"No page IR JSONs found in: {page_irs_dir}"
    start = max(config.start_page, page_indices[0])
    stop = min(doc.page_count, page_indices[-1] + 1) if end_page is None else end_page

    # Load all page IR JSONs so that we can apply edits and then write once.
    page_irs = load_page_irs_from_extraction(
        end_page=stop, page_irs_dir=page_irs_dir, start_page=start
    )

    # Iterate in pairs.
    for i in range(start, stop - 1):
        assert (
            i in page_irs and (i + 1) in page_irs
        ), f"Missing page IR for pages {i} or {i + 1}"

        prev_page_ir, next_page_ir = page_irs[i], page_irs[i + 1]
        prev_page_items = prev_page_ir.items or []
        next_page_items = next_page_ir.items or []

        if not (prev_page_items and next_page_items):
            logger.warning(
                f"Skipping continuity check for pages {i}-{i + 1}: "
                f"prev_items={len(prev_page_items)} next_items={len(next_page_items)}"
            )
            continue

        # Get the bottommost continuity candidate from page N.
        prev_index, prev_item = bottommost_continuity_candidate(
            image_height=prev_page_ir.image_height, items=prev_page_items
        )

        # Crop top of next image (page N+1) and compute the crop height so candidate
        # selection is restricted to items the model can actually see. This prevents
        # false negatives when the topmost candidate is outside the crop.
        next_crop_fp = verification_dirs.page_irs_pair_crops / f"{i + 1:04}_top.png"
        crop_image_to_top(
            input_png_fp=page_images_dir / f"{i + 1:04}.png", output_png_fp=next_crop_fp
        )

        # The crop starts at y=0, so its pixel height is the y-extent visible to the
        # model.
        next_crop_height_px = float(pymupdf.Pixmap(str(next_crop_fp)).height)

        # Pick the next-page candidate from items that actually intersect the visible crop.
        next_index, next_item = topmost_continuity_candidate_paired(
            image_height=next_page_ir.image_height,
            items=next_page_items,
            prev_item=prev_item,
            visible_y_max=next_crop_height_px,
        )

        # Invoke the model to verify the pair.
        logger.info(f"Verifying continuity between pages {i} and {i + 1}...")

        prev_item_json = prev_item.model_dump(mode="json")
        next_item_json = next_item.model_dump(mode="json")
        verdict = verify_page_ir_pairs(
            model=config.model,
            next_item=next_item_json,
            next_page_index=i + 1,
            next_png=next_crop_fp,
            prev_item=prev_item_json,
            prev_page_index=i,
            prev_png=page_images_dir / f"{i:04}.png",
        )
        verdict.prev_page_index = i
        verdict.next_page_index = i + 1

        # Apply continuity verdict to page IRs.
        applied_edits = apply_continuity_verdict(
            min_confidence_to_patch=config.min_confidence_to_patch,
            next_item=next_item,
            prev_item=prev_item,
            verdict=verdict,
        )

        # Persist the verdict.
        write_to_json(
            fp=verification_dirs.page_irs_pair_reports / f"{i:04}_{i + 1:04}.json",
            json_info={
                "candidate_selection": {
                    "prev_candidate_index": prev_index,
                    "next_candidate_index": next_index,
                    "prev_candidate": prev_item_json,
                    "next_candidate": next_item_json,
                },
                "applied_edits": applied_edits,
                "verdict": verdict.model_dump(mode="json"),
            },
        )

        logger.success(f"Finished verifying continuity between pages {i} and {i + 1}!")

    # Perform postprocess fixes.
    postprocess_verified_page_irs(
        page_irs=page_irs, verification_dirs=verification_dirs
    )

    # Write verified page IRs after all edits have been applied.
    save_verified_page_irs(page_irs=page_irs, verification_dirs=verification_dirs)


@cli.command()
def verify(
    config_fp: Path = typer.Argument(
        ...,
        dir_okay=False,
        exists=True,
        file_okay=True,
        help="The file path to the global config file for the pipeline.",
        readable=True,
        resolve_path=True,
    )
) -> None:
    """Verify page IR continuity from the page IR extraction step.

    The process is as follows:

    1. Load config and validate extraction run existence.
    2. Check that the page images and page IR directories have matching files and the
        document key matches the PDF.
    3. Validate page range.
    4. Persist verification run metadata.
    5. Run pairwise continuity verification across (N, N+1) in the selected page
        range and write verified page IR JSONs to file.

    Parameters
    ----------
    config_fp
        The file path to the global config file for the pipeline.

    Raises
    ------
    Exception
        If any part of the verification fails.
    """

    # 1.
    config = VerificationConfig.model_validate(
        open_json_type(config_fp)["page_ir_verification"]
    )
    extraction_config = ExtractionConfig.model_validate(
        open_json_type(config_fp)["page_ir_extraction"]
    )
    computed_doc_key = compute_doc_key(n_hex=64, pdf_fp=extraction_config.pdf_fp)
    extraction_run_results_dir = (
        extraction_config.output_dir / computed_doc_key / "extraction"
    )
    page_images_dir = extraction_run_results_dir / "page_images"
    page_irs_dir = extraction_run_results_dir / "page_irs"
    extraction_run_config = RunCtx.model_validate(
        open_json_type(extraction_run_results_dir / "extraction_run.json")
    )

    # 2.
    assert compare_directories(page_images_dir, page_irs_dir)
    expected_doc_key = extraction_run_config.extra["doc_key"]
    computed_doc_key = compute_doc_key(n_hex=64, pdf_fp=extraction_config.pdf_fp)

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

    verification_results_dir = (
        extraction_config.output_dir / expected_doc_key / "verification"
    )

    try:
        with pymupdf.open(str(extraction_config.pdf_fp)) as doc:
            # 3.
            _, end_page = validate_page_count(
                doc=doc, end_page=config.end_page, start_page=config.start_page
            )

            # 4.
            verification_dirs, verification_run = persist_verification_run(
                config=config, output_dir=verification_results_dir
            )

            # 5.
            logger.info(
                f"Starting page IR continuity verification process using directories: "
                f"{page_images_dir} and {page_irs_dir}"
            )

            verify_page_ir_continuity(
                config=config,
                doc=doc,
                end_page=end_page,
                page_images_dir=page_images_dir,
                page_irs_dir=page_irs_dir,
                verification_dirs=verification_dirs,
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
        verification_run.completed_at = datetime.now(timezone.utc)
        write_to_json(
            fp=verification_dirs.root / "verification_run.json",
            json_info=verification_run,
        )

        # 4.
        verification_dirs, verification_run = persist_verification_run(
            config=config, output_dir=verification_results_dir
        )

        try:
            # 5.
            start = max(config.start_page, page_indices[0])
            max_available = min(doc.page_count, page_indices[-1] + 1)
            stop = max_available if end_page is None else min(end_page, max_available)
            page_irs = load_page_irs_from_extraction(
                end_page=stop, page_irs_dir=page_irs_dir, start_page=start
            )

            # 6.
            logger.info(
                f"Starting page IR continuity verification process using directories: "
                f"{page_images_dir} and {page_irs_dir}"
            )

            verify_page_ir_continuity(
                config=config,
                page_images_dir=page_images_dir,
                page_irs=page_irs,
                start=start,
                stop=stop,
                verification_dirs=verification_dirs,
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
            verification_run.completed_at = datetime.now(timezone.utc)
            write_to_json(
                fp=verification_dirs.root / "verification_run.json",
                json_info=verification_run,
            )


if __name__ == "__main__":
    cli()
