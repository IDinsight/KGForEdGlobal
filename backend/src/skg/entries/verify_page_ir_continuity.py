"""This module contains the entry point for verifying the continuity of the extracted
page IR JSONs from step 1. This is step 2.

Step 2 reads per-page PageIR JSONs from Step 1, runs pairwise (N, N+1) continuity
verification on selected boundary-candidate items, and writes corrected PageIR JSONs
to the verification output directory.

Key guarantee for Step 3: boundaries are patched when confidence is high; otherwise
extractor boundaries are preserved.

Invoke from the backend directory via:

python src/skg/entries/verify_page_ir_continuity.py ../data/tanzania/tanzania.pdf /path/to/extraction_run_results
python src/skg/entries/verify_page_ir_continuity.py ../data/zambia/zambia.pdf /path/to/extraction_run_results
python src/skg/entries/verify_page_ir_continuity.py ../data/uganda/uganda.pdf /path/to/extraction_run_results
python src/skg/entries/verify_page_ir_continuity.py ../data/ghana/ghana.pdf /path/to/extraction_run_results
"""

# Standard Library
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
from skg.page_ir.llm import verify_page_ir_continuity_verdict, verify_page_ir_pairs
from skg.page_ir.schemas import PageIR, PageIRContinuityVerdict
from skg.page_ir.utils import (
    PageIRVerificationDirs,
    _boundary_val,
    bottommost_continuity_candidate,
    create_page_ir_verification_dirs,
    derive_page_boundary_state,
    ensure_boundary,
    find_caption_code,
    get_negative_threshold_based_on_kind,
    get_threshold_based_on_kind,
    is_figure_block,
    item_snippet,
    min_crop_height_px,
    pad_inches,
    topmost_continuity_candidate_paired,
)
from skg.schemas import RunCtx
from skg.utils.constants import ItemBoundary, PageContinuationKind
from skg.utils.general import compare_directories, open_json_type, write_to_json
from skg.utils.pdf import (
    compute_doc_key,
    crop_image_to_bottom,
    crop_image_to_top,
    validate_page_count,
)

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


def apply_continuity_edits(
    *,
    next_idx: int,
    next_item: dict[str, Any],
    next_page_items: list[dict[str, Any]],
    prev_idx: int,
    prev_item: dict[str, Any],
    prev_page_items: list[dict[str, Any]],
    verdict: PageIRContinuityVerdict,
) -> None:
    """Apply minimal edits to the extracted page IRs based on the continuity verdict.

    Parameters
    ----------
    next_idx
        Index of the continuity candidate item on the next page.
    next_item
        The actual item dictionary for the next page candidate.
    next_page_items
        The list of items on the next page.
    prev_idx
        Index of the continuity candidate item on the previous page.
    prev_item
        The actual item dictionary for the previous page candidate.
    prev_page_items
        The list of items on the previous page.
    verdict
        The continuity verdict from the model.
    """

    # If this pair is NOT a continuation or confidence is below the threshold, then
    # there are no continuity edits to apply.
    threshold = get_threshold_based_on_kind(
        next_item=next_item, prev_item=prev_item, verdict=verdict
    )
    if not verdict.is_continuation or float(verdict.clamped_confidence) < threshold:
        return

    # Update item-level boundaries (explicit edits from model).
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

    # Enforce item-level consistency (implicit edits). If model verified continuity but
    # didn't explicitly set boundaries, force defaults.
    if verdict.set_prev_item_boundary is None:
        ensure_boundary(
            desired=ItemBoundary.TRUNCATED.value, index=prev_idx, items=prev_page_items
        )
    if verdict.set_next_item_boundary is None:
        ensure_boundary(
            desired=ItemBoundary.RESUMED.value, index=next_idx, items=next_page_items
        )

    # Table header repetition: set repeats_header only when the model provides it for a
    # verified table continuation.
    header_setting = verdict.set_next_table_repeats_header
    is_next_table = next_item.get("kind") == "table"
    kind = getattr(verdict.continuation_kind, "value", verdict.continuation_kind)
    is_table_continuation = (
        verdict.is_continuation and kind == PageContinuationKind.TABLE.value
    )
    if header_setting is not None and is_next_table and is_table_continuation:
        next_page_items[next_idx]["repeats_header"] = header_setting


def apply_non_continuity_edits(
    *,
    next_idx: int,
    next_item: dict[str, Any],
    next_page_items: list[dict[str, Any]],
    prev_idx: int,
    prev_item: dict[str, Any],
    prev_page_items: list[dict[str, Any]],
    verdict: PageIRContinuityVerdict,
) -> None:
    """If the model is VERY confident there is no continuation between these two
    candidates, clear seam-level continuity flags on just these items.

    This is a 'patch' for extractor false-positives (e.g., a table marked resumed when
    it's actually a new table).

    NB: We only clear the seam-relevant side, preserving the other side when
    boundary="both".

    Parameters
    ----------
    next_idx
        Index of the continuity candidate item on the next page.
    next_item
        The actual item dictionary for the next page candidate.
    next_page_items
        The list of items on the next page.
    prev_idx
        Index of the continuity candidate item on the previous page.
    prev_item
        The actual item dictionary for the previous page candidate.
    prev_page_items
        The list of items on the previous page.
    verdict
        The continuity verdict from the model.
    """

    # If this pair IS a continuation or confidence is below the negative threshold,
    # then there are no non-continuity edits to apply.
    neg_threshold = get_negative_threshold_based_on_kind(
        next_item=next_item, prev_item=prev_item
    )
    if verdict.is_continuation or float(verdict.clamped_confidence) < neg_threshold:
        return

    # Previous page item (seam to NEXT corresponds to TRUNCATED).
    # NB: If prev_boundary is RESUMED or COMPLETE, leave it as-is.
    prev_boundary = _boundary_val(prev_page_items[prev_idx].get("boundary"))
    if prev_boundary == ItemBoundary.TRUNCATED.value:
        # It only claimed "continues to next"; clear it.
        ensure_boundary(
            allow_downgrade_both=True,
            desired=ItemBoundary.COMPLETE.value,
            index=prev_idx,
            items=prev_page_items,
        )
    elif prev_boundary == ItemBoundary.BOTH.value:
        # Remove the "to next" claim but preserve "from prev".
        ensure_boundary(
            allow_downgrade_both=True,
            desired=ItemBoundary.RESUMED.value,
            index=prev_idx,
            items=prev_page_items,
        )

    # Next page item (seam from PREV corresponds to RESUMED).
    # NB: If next_boundary is TRUNCATED or COMPLETE, leave it as-is.
    next_boundary = _boundary_val(next_page_items[next_idx].get("boundary"))
    if next_boundary == ItemBoundary.RESUMED.value:
        # It only claimed "continues from prev"; clear it.
        ensure_boundary(
            allow_downgrade_both=True,
            desired=ItemBoundary.COMPLETE.value,
            index=next_idx,
            items=next_page_items,
        )
    elif next_boundary == ItemBoundary.BOTH.value:
        # Remove the "from prev" claim but preserve "to next".
        ensure_boundary(
            allow_downgrade_both=True,
            desired=ItemBoundary.TRUNCATED.value,
            index=next_idx,
            items=next_page_items,
        )

    # repeats_header only has meaning for resumed/both table continuations.
    if next_item.get("kind") == "table":
        next_page_items[next_idx]["repeats_header"] = None


def load_page_irs_from_extraction(
    *, end_page: int, page_irs_dir: Path, start_page: int
) -> dict[int, dict[str, Any]]:
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
    dict[int, dict[str, Any]]
        The dictionary of page IRs by page index.
    """

    page_irs: dict[int, dict[str, Any]] = {
        i: PageIR.model_validate(
            open_json_type(page_irs_dir / f"{i:04}.json")
        ).model_dump(mode="json")
        for i in range(start_page, end_page)
    }

    # Preserve extraction hints (internal-only) so reports can show what the extractor
    # believed. Verification will PATCH only when confidence is high.
    for page_ir in page_irs.values():
        for item in page_ir.get("items", []):
            item["_orig_boundary"] = item.get("boundary")

    return page_irs


def persist_verification_run(
    *,
    end_page: Optional[int],
    model: str,
    output_dir: Path,
    start_page: int,
    **kwargs: dict[str, Any],
) -> tuple[PageIRVerificationDirs, RunCtx]:
    """Persist verification run metadata.

    Parameters
    ----------
    end_page
        0-based end page (exclusive).
    model
        OpenAI model for page IR continuity verification.
    output_dir
        The output directory for the verified page IR JSONs.
    start_page
        0-based start page (inclusive).
    kwargs
        Additional extraction run configuration parameters.

    Returns
    -------
    tuple[PageIRVerificationDirs, RunCtx]
        The created verification directories and persisted verification run metadata.
    """

    extra = kwargs.get("extra", {})
    extra.update(
        {
            "end_page_cli": end_page,  # Keep original CLI value (may be None)
            "start_page_cli": start_page,
        }
    )
    extra.pop("status", None)
    verification_dirs = create_page_ir_verification_dirs(output_dir=output_dir)
    verification_run = RunCtx(
        extra=extra,
        models=[model],
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(fp=output_dir / "verification_run.json", json_info=verification_run)
    logger.info(f"Verification directory: {output_dir}")

    return verification_dirs, verification_run


def postprocess_verified_page_irs(
    *, page_irs: dict[int, dict[str, Any]], verification_dirs: PageIRVerificationDirs
) -> None:
    """Run all postpass fixes before writing verified JSONs.

    Parameters
    ----------
    page_irs
        The dictionary of page IRs by page index.
    verification_dirs
        The verification directories.
    """

    table_code_changes = propagate_table_local_codes(page_irs=page_irs)

    # Persist what was changed for audit/debug.
    write_to_json(
        fp=verification_dirs.root / "postprocess_report.json",
        json_info={"table_local_code_changes": table_code_changes},
    )


def propagate_table_local_codes(
    *, page_irs: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Carry forward the most recent "Table X" local_code across continuation segments
    when repeats_header=true or boundary indicates continuation and local_code is null.

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
        items = page.get("items", [])

        # Capture any caption-defined table code on the page (preferred source of
        # truth).
        if (caption_code := find_caption_code(items)) is not None:
            carried_table_code = caption_code

        table_continues_to_next = False

        for idx, it in enumerate(items):
            if it.get("kind") != "table":
                continue

            boundary = _boundary_val(it.get("boundary"), default_val="")
            is_continuation_segment = boundary in (
                ItemBoundary.BOTH.value,
                ItemBoundary.RESUMED.value,
                ItemBoundary.TRUNCATED.value,
            ) or bool(it.get("repeats_header"))

            # Update carry if this segment has an explicit local_code.
            if it.get("local_code") is not None:
                carried_table_code = str(
                    _boundary_val(it.get("local_code"), default_val="")
                )

            # Fill missing local_code if we’re clearly in a continuation chain.
            elif is_continuation_segment and carried_table_code:
                it["local_code"] = carried_table_code
                changes.append(
                    {
                        "type": "propagate_table_local_code",
                        "page": i,
                        "item_index": idx,
                        "set_local_code": carried_table_code,
                    }
                )

            if boundary in (ItemBoundary.TRUNCATED.value, ItemBoundary.BOTH.value):
                table_continues_to_next = True

        # Only keep carrying forward when the table actually continues to the next page.
        if not table_continues_to_next:
            carried_table_code = None

    return changes


def sanitize_verdict_for_candidate_kinds(
    *,
    next_item: dict[str, Any],
    prev_item: dict[str, Any],
    verdict: PageIRContinuityVerdict,
) -> PageIRContinuityVerdict:
    """Drop (veto) continuations that are structurally impossible for the chosen
    candidates.

    Parameters
    ----------
    next_item
        The actual item dictionary for the next page candidate.
    prev_item
        The actual item dictionary for the previous page candidate.
    verdict
        The continuation verdict from the model.

    Returns
    -------
    PageIRContinuityVerdict
        The sanitized verdict.
    """

    kind = getattr(verdict.continuation_kind, "value", verdict.continuation_kind)

    if verdict.is_continuation and kind == PageContinuationKind.NONE.value:
        return veto_continuation(
            reason=f"continuation_kind={PageContinuationKind.NONE.value} is incompatible with is_continuation=true",
            verdict=verdict,
        )

    if (not verdict.is_continuation) and kind != PageContinuationKind.NONE.value:
        verdict.continuation_kind = PageContinuationKind.NONE
        verdict.set_prev_item_boundary = None
        verdict.set_next_item_boundary = None
        verdict.set_next_table_repeats_header = None
        return verdict

    prev_kind = prev_item.get("kind")
    next_kind = next_item.get("kind")

    # Text continuations must be block-to-block (never into/from a table).
    if kind == PageContinuationKind.TEXT.value and (
        prev_kind != "block" or next_kind != "block"
    ):
        return veto_continuation(
            reason="continuation_kind=text requires both candidates to be block items",
            verdict=verdict,
        )

    # Table continuations must be table-to-table.
    if kind == PageContinuationKind.TABLE.value and (
        prev_kind != "table" or next_kind != "table"
    ):
        return veto_continuation(
            reason="continuation_kind=table requires both candidates to be table items",
            verdict=verdict,
        )

    # Figure continuations must be figure-to-figure blocks.
    if kind == PageContinuationKind.FIGURE.value and (
        not (is_figure_block(prev_item) and is_figure_block(next_item))
    ):
        return veto_continuation(
            reason="continuation_kind=figure requires both candidates to be figure blocks",
            verdict=verdict,
        )

    return verdict


def save_verified_page_irs(
    *, page_irs: dict[int, dict[str, Any]], verification_dirs: PageIRVerificationDirs
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

        # Remove internal-only fields before writing outputs (schema forbids extras).
        for it in page_ir.get("items", []):
            it.pop("_orig_boundary", None)

        # Derive page-level boundary_state from verified item boundaries.
        page_ir["boundary_state"] = derive_page_boundary_state(page_ir=page_ir).value

        # Write verified JSON.
        write_to_json(
            fp=verification_dirs.page_irs_verified / f"{i:04}.json", json_info=page_ir
        )

    logger.success("All verified page IR JSONs saved successfully!")


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

        # Crop bottom of previous and top of next based on non-artifact bboxes.
        prev_kind = prev_item.get("kind", "block")
        if prev_kind == "block" and is_figure_block(prev_item):
            prev_kind = "figure"
        next_kind = next_item.get("kind", "block")
        if next_kind == "block" and is_figure_block(next_item):
            next_kind = "figure"
        prev_min_h = min_crop_height_px(
            kind=prev_kind, page_h_px=int(prev_page_ir["image_height"])
        )
        next_min_h = min_crop_height_px(
            kind=next_kind, page_h_px=int(next_page_ir["image_height"])
        )
        prev_crop_fp = verification_dirs.page_irs_pair_crops / f"{i:04}_bottom.png"
        next_crop_fp = verification_dirs.page_irs_pair_crops / f"{i + 1:04}_top.png"
        crop_image_to_bottom(
            bbox=prev_item["bbox"],
            desired_padding_inches=pad_inches(prev_kind),
            input_png_fp=page_images_dir / f"{i:04}.png",
            min_height_px=prev_min_h,
            output_png_fp=prev_crop_fp,
            render_dpi=render_dpi,
        )
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


def veto_continuation(
    *, reason: str, verdict: PageIRContinuityVerdict
) -> PageIRContinuityVerdict:
    """Veto a continuation claim by forcing is_continuation=False with low confidence.

    Parameters
    ----------
    reason
        The reason for vetoing the verdict.
    verdict
        The continuity verdict from the model.

    Returns
    -------
    PageIRContinuityVerdict
        The modified verdict with the veto applied.
    """

    logger.warning(f"Vetoing continuation due to: {reason}")

    verdict.is_continuation = False

    # Candidate mismatch means we can't trust the continuation claim between THESE two
    # items, not "there is definitely no continuation anywhere". Keep this
    # continuation_kind="none" and low-confidence so downstream edit-application
    # thresholds will not apply.
    verdict.clamped_confidence = min(float(verdict.confidence), 0.49)
    verdict.continuation_kind = PageContinuationKind.NONE
    verdict.rationale = (verdict.rationale or "") + f" | Postprocess veto: {reason}"

    # NB: Never apply edits if we veto the continuation claim.
    verdict.set_prev_item_boundary = None
    verdict.set_next_item_boundary = None
    verdict.set_next_table_repeats_header = None

    return verdict


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
            f"extraction. Pass the same PDF used in step 1 or re-run extraction."
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
