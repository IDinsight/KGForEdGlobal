"""This module contains the entry point for verifying the continuity of the extracted
page IR JSONs from step 1. This is step 2.

Step 2 reads per-page PageIR JSONs from Step 1, runs pairwise (N, N+1) continuity
verification on selected boundary-candidate items, and writes corrected PageIR JSONs
to the verification output directory.

Key guarantee for Step 3: boundaries are patched when confidence is high; otherwise
extractor boundaries are preserved.

Invoke from the backend directory via:

python src/skg/entries/verify_page_ir_continuity.py ../data/tanzania/tanzania.pdf /path/to/extraction_run_results
"""

# Standard Library
import sys
import traceback

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
from skg.extract_page_ir.utils import load_page_irs_from_extraction
from skg.page_ir.llm import verify_page_ir_continuity_verdict, verify_page_ir_pairs
from skg.page_ir.utils import (
    PageIRVerificationDirs,
    apply_continuity_edits,
    apply_non_continuity_edits,
    bottommost_continuity_candidate,
    fix_false_repeats_header_on_continuation,
    fix_repeats_header_for_continued_tables,
    get_threshold_based_on_kind,
    is_figure_block,
    item_snippet,
    min_crop_height_px,
    pad_inches,
    persist_verification_run,
    postprocess_verified_page_irs,
    sanitize_verdict_for_candidate_kinds,
    save_verified_page_irs,
    should_veto_text_continuation_due_to_ordering_safety,
    topmost_continuity_candidate_paired,
    veto_continuation,
)
from skg.utils.general import compare_directories, open_json_type, write_to_json
from skg.utils.pdf import compute_doc_key, crop_image_to_top, validate_page_count

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


def verify_page_ir_continuity(
    *,
    doc: pymupdf.Document,
    end_page: int | None,
    model: str,
    page_images_dir: Path,
    page_irs_dir: Path,
    render_dpi: int,
    start_page: int,
    verification_dirs: PageIRVerificationDirs,
) -> None:
    """Perform verification of PageIR JSONs in pairs.

    Parameters
    ----------
    doc
        The PyMuPDF document.
    end_page
        0-based end page (exclusive).
    model
        OpenAI model for page IR verification.
    page_images_dir
        Directory containing the page images.
    page_irs_dir
        Directory containing the page IR JSONs.
    render_dpi
        The render DPI for the page images during the extraction stage.
    start_page
        0-based start page (inclusive).
    verification_dirs
        The verification directories.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    # Load all page IR indices (0000.json style).
    json_fps = sorted(page_irs_dir.glob("*.json"))
    page_indices = sorted(int(fp.stem) for fp in json_fps if fp.stem.isdigit())
    assert page_indices, f"No page IR JSONs found in: {page_irs_dir}"
    start = max(start_page, page_indices[0])
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
        prev_page_items = prev_page_ir.get("items", [])
        next_page_items = next_page_ir.get("items", [])

        if not (prev_page_items and next_page_items):
            logger.warning(
                f"Skipping continuity check for pages {i}-{i + 1}: "
                f"prev_items={len(prev_page_items)} next_items={len(next_page_items)}"
            )
            continue

        # Get bottommost and topmost continuity candidates using paired logic.
        prev_idx, prev_item = bottommost_continuity_candidate(
            image_height=prev_page_ir["image_height"], items=prev_page_items
        )
        next_idx, next_item = topmost_continuity_candidate_paired(
            image_height=next_page_ir["image_height"],
            items=next_page_items,
            prev_item=prev_item,
        )

        # Crop top of next based on non-artifact bboxes.
        next_kind = next_item.get("kind", "block")
        if next_kind == "block" and is_figure_block(next_item):
            next_kind = "figure"
        next_min_h = min_crop_height_px(
            kind=next_kind, page_h_px=int(next_page_ir["image_height"])
        )
        next_crop_fp = verification_dirs.page_irs_pair_crops / f"{i + 1:04}_top.png"
        crop_image_to_top(
            bbox=next_item["bbox"],
            desired_padding_inches=pad_inches(next_kind),
            input_png_fp=page_images_dir / f"{i + 1:04}.png",
            min_height_px=next_min_h,
            output_png_fp=next_crop_fp,
            render_dpi=render_dpi,
        )

        # Get item excerpts for the verifier.
        prev_excerpt = item_snippet(item=prev_item, text_mode="tail")
        next_excerpt = item_snippet(item=next_item, text_mode="head")

        # Don't bias the verifier with extractor continuity guesses.
        prev_excerpt["boundary"], next_excerpt["boundary"] = None, None
        if next_excerpt.get("kind") == "table":
            next_excerpt["repeats_header"] = None
        if prev_excerpt.get("kind") == "table":
            prev_excerpt["repeats_header"] = None

        # Invoke the model to verify the pair.
        logger.info(f"Verifying continuity between pages {i} and {i + 1}...")

        verdict = verify_page_ir_pairs(
            model=model,
            next_item_excerpt=next_excerpt,
            next_page_index=i + 1,
            next_png=next_crop_fp,
            prev_item_excerpt=prev_excerpt,
            prev_page_index=i,
            prev_png=page_images_dir / f"{i:04}.png",
        )
        verdict = sanitize_verdict_for_candidate_kinds(
            next_item=next_item, prev_item=prev_item, verdict=verdict
        )
        verify_page_ir_continuity_verdict(verdict)
        threshold = get_threshold_based_on_kind(
            next_item=next_item, prev_item=prev_item, verdict=verdict
        )
        if verdict.is_continuation and float(verdict.clamped_confidence) < threshold:
            verdict = veto_continuation(
                reason=f"confidence {float(verdict.clamped_confidence):.2f} < threshold {threshold:.2f}",
                verdict=verdict,
            )
            verify_page_ir_continuity_verdict(verdict)

        if should_veto_text_continuation_due_to_ordering_safety(
            next_idx=next_idx,
            next_item=next_item,
            next_page_items=next_page_items,
            verdict=verdict,
        ):
            verdict = veto_continuation(
                reason="ORDERING SAFETY: structural heading/caption above next candidate block (TEXT/LIST)",
                verdict=verdict,
            )
            verify_page_ir_continuity_verdict(verdict)

        # Persist the verdict.
        write_to_json(
            fp=verification_dirs.page_irs_pair_reports / f"{i:04}_{i + 1:04}.json",
            json_info={
                # Candidate selection provenance (for debugging).
                "candidate_selection": {
                    "prev_candidate_index": prev_idx,
                    "next_candidate_index": next_idx,
                    "prev_candidate_bbox": prev_item["bbox"],
                    "next_candidate_bbox": next_item["bbox"],
                    "prev_candidate_extraction_boundary": prev_item["_orig_boundary"],
                    "next_candidate_extraction_boundary": next_item["_orig_boundary"],
                },
                "verdict": verdict.model_dump(mode="json"),
            },
        )

        # Apply continuity edits based on the verdict.
        apply_continuity_edits(
            next_idx=next_idx,
            next_item=next_item,
            next_page_items=next_page_items,
            prev_idx=prev_idx,
            prev_item=prev_item,
            prev_page_items=prev_page_items,
            verdict=verdict,
        )

        # Post-edit normalization to enforce/correct repeated-header flags for
        # continued tables when we can determine it deterministically.
        fix_repeats_header_for_continued_tables(
            prev_item=prev_item, next_item=next_item
        )
        fix_false_repeats_header_on_continuation(
            prev_item=prev_item, next_item=next_item
        )

        # Apply non-continuity edits if VERY confident there is no continuation.
        apply_non_continuity_edits(
            next_idx=next_idx,
            next_item=next_item,
            next_page_items=next_page_items,
            prev_idx=prev_idx,
            prev_item=prev_item,
            prev_page_items=prev_page_items,
            verdict=verdict,
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
        help="OpenAI model for page IR verification.",
    ),
    start_page: int = typer.Option(
        0, "--start-page", "-s", help="0-based start page (inclusive)."
    ),
    end_page: Optional[int] = typer.Option(
        None, "--end-page", "-e", help="0-based end page (exclusive). Default: to end."
    ),
) -> None:
    """Verify page IR JSON continuity from the extraction step.

    The process is as follows:

    1. Check that the page images and page IR directories have matching files.
    2. Persist verification run metadata.
    3. Validate page range.
    4. Run pairwise continuity verification across (N, N+1) in the selected page
        range.
    5. Write verified PageIR JSONs and finalize the verification run record.

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

    Raises
    ------
    Exception
        If any part of the verification fails.
    ValueError
        If the expected doc_key does not match the computed doc key.
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
        start_page=start_page,
        **extraction_run_config,
    )

    expected_doc_key = extraction_run_config.get("extra", {}).get("doc_key")
    computed_doc_key = compute_doc_key(n_hex=64, pdf_fp=pdf_fp)

    if computed_doc_key != expected_doc_key:
        raise ValueError(
            f"PDF doc_key mismatch.\n"
            f"  PDF provided to verify(): {pdf_fp}\n"
            f"  computed doc_key:         {computed_doc_key}\n"
            f"  extraction_run.json key:  {expected_doc_key}\n"
            f"You are likely verifying against a different PDF than the one used for "
            f"extraction. Pass the same PDF used in the extraction step or re-run "
            f"extraction."
        )

    logger.info(
        f"Starting page IR continuity verification process using directories: "
        f"{page_images_dir} and {page_irs_dir}"
    )
    logger.info(f"Loaded extraction run config: {extraction_run_config}")
    logger.info(f"Saving verification results to: {verification_results_dir}")

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
            fp=verification_dirs.root / "verification_run.json",
            json_info=verification_run,
        )


if __name__ == "__main__":
    cli()
