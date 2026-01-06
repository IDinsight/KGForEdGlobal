"""This module contains the entry point for stitching together a document IR JSON from
individual (verified) page IR JSONs from step 2. This is step 3.

Step 3 does the following:

1. Load and validate a directory of *verified* PageIR JSON files (one per page).
2. Normalize items (i.e, filter artifacts if applicable).
3. Stitch PageIRs into a single layout-level DocumentIR by merging cross-page
    continuations (tables and text/list blocks), preserving provenance.

Step 3 is intentionally layout-oriented. It does NOT:

1. Infer semantic hierarchy (grade/subject/topic).
2. Assign statement roles (expectation/guidance/etc.).
3. generate deterministic LC KG IDs,
4. export Learning Commons KG nodes/edges.
5. Create Learning Commons KG entities/edges.

Those belong in later stages (canonicalization + export).

Expected inputs
---------------

A directory containing page-level JSON files that validate against PageIR
(see schemas.py). These JSONs should already have continuity metadata verified
(i.e., correct ItemBoundary flags and repeats_header where applicable).

Invoke from the backend directory via:

python src/skg/entries/stitch_document_ir.py ../data/tanzania/tanzania.pdf /path/to/verification_run_results
"""

# Standard Library
import sys
import traceback

from datetime import datetime, timezone
from pathlib import Path
from typing import Union

# Third Party Library
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
from skg.document_ir.schemas import DocumentIR, Segment
from skg.document_ir.utils import (
    DocumentIRDirs,
    ItemKey,
    assert_page_items_consumed_exactly_once,
    build_continuation_chain,
    compute_page_break_links,
    load_page_irs_from_verification,
    materialize_segment,
    normalize_page_items,
    persist_stitching_run,
    uniquify_segment_keys,
)
from skg.page_ir.schemas import CurriculumBlock, CurriculumTable, PageIR
from skg.utils.general import open_json_type, write_to_json
from skg.utils.pdf import compute_doc_key

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


def stitch_document_ir(
    *,
    doc_key: str,
    keep_artifacts: bool,
    overwrite: bool,
    page_irs: list[PageIR],
    pdf_name: str,
    repair_hyphenation: bool,
    stitching_dirs: DocumentIRDirs,
) -> None:
    """Stitch verified PageIRs into a single DocumentIR.

    The process is as follows:

    1. Normalize items per page.
    2. Link cross-page continuations using boundary flags.
    3. Merge chains of continuations into stitched segments.
    4. Preserve provenance.

    Parameters
    ----------
    doc_key
        The expected document key for all page IRs.
    keep_artifacts
        If True, keep artifact blocks during stitching.
    overwrite
        Overwrite existing document IR JSON.
    page_irs
        Validated PageIR list in page order.
    pdf_name
        The source PDF filename (no path).
    repair_hyphenation
        Whether to repair hyphenation in stitched text blocks.
    stitching_dirs
        Directories for storing the stitched DocumentIR.

    Raises
    ------
    ValueError
        If no pages are provided.
    """

    if not page_irs:
        raise ValueError("No page IRs provided.")

    document_ir_fp = stitching_dirs.root / "document_ir.json"

    if not overwrite and document_ir_fp.exists():
        logger.warning(
            f"Document IR JSON already exists at {document_ir_fp}. Skipping stitching."
            f"If you wish to overwrite, pass the --overwrite flag."
        )
        return

    # Normalized items per page and filter artifacts if applicable.
    items_with_idx: dict[
        int, list[tuple[int, Union[CurriculumTable, CurriculumBlock]]]
    ] = {
        page_ir.page_index: normalize_page_items(
            keep_artifacts=keep_artifacts, page_ir=page_ir
        )
        for page_ir in page_irs
    }
    items_lookup: dict[int, dict[int, Union[CurriculumTable, CurriculumBlock]]] = {
        p_idx: dict(items) for p_idx, items in items_with_idx.items()
    }

    warnings: list[str] = []

    # Compute page break links based on verified boundary flags.
    links = compute_page_break_links(
        keep_artifacts=keep_artifacts, page_irs=page_irs, warnings=warnings
    )

    # Set of destination keys to identify items that are continuations.
    continuations = set(links.values())

    # Iterate in document reading order: page order, then item order.
    segments: list[Segment] = []
    visited: set[ItemKey] = set()

    for page_ir in page_irs:
        page_idx = page_ir.page_index
        page_items = items_with_idx.get(page_idx, [])

        for original_item_idx, item in page_items:
            key = (page_idx, original_item_idx)

            # Skip if already processed or if it's the middle/end of a chain.
            if key in visited or key in continuations:
                visited.add(key)
                continue

            # Build the chains.
            chain, chain_warnings = build_continuation_chain(
                links=links,
                items_lookup=items_lookup,
                start_item=item,
                start_key=key,
            )

            if chain_warnings:
                warnings.extend(chain_warnings)

            # Mark all items in chain as visited.
            for page_idx, item_idx, _ in chain:
                visited.add((page_idx, item_idx))

            # Materialize a stitched segment from the chain.
            segments.append(
                materialize_segment(
                    chain=chain,
                    item_index=original_item_idx,
                    page_index=page_idx,
                    repair_hyphenation=repair_hyphenation,
                    warnings=warnings,
                )
            )

    # De-duplicate any accidental segment_key collisions (rare, but possible).
    segments = uniquify_segment_keys(segments=segments)

    # Perform integrity check --> every normalized PageIR item must be consumed exactly
    # once.
    assert_page_items_consumed_exactly_once(
        items_with_idx=items_with_idx, segments=segments, strict=True, warnings=warnings
    )

    # Write document IR JSON.
    first_page = page_irs[0]
    document_ir = DocumentIR(
        coord_space=first_page.coord_space,
        doc_key=doc_key,
        dpi=first_page.dpi,
        image_height=first_page.image_height,
        image_width=first_page.image_width,
        page_count=len(page_irs),
        pdf_name=pdf_name,
        segments=segments,
        warnings=warnings,
    )

    write_to_json(fp=document_ir_fp, json_info=document_ir)


@cli.command()
def stitch(
    pdf_fp: Path = typer.Argument(
        ...,
        dir_okay=False,
        exists=True,
        file_okay=True,
        help="The file path to the PDF document to stitch a document IR from.",
        readable=True,
        resolve_path=True,
    ),
    verification_run_results_dir: Path = typer.Argument(
        ...,
        dir_okay=True,
        exists=True,
        file_okay=False,
        help="The verification run results directory.",
        resolve_path=True,
    ),
    keep_artifacts: bool = typer.Option(
        False,
        "--keep-artifacts",
        help="Whether to keep artifacts such as page numbers, headers, footers, etc. after stitching.",
    ),
    repair_hyphenation: bool = typer.Option(
        True,
        "--repair-hyphenation",
        help="Whether to repair hyphenation for stitched text.",
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Overwrite existing document IR JSON."
    ),
) -> None:
    """Create a stitched DocumentIR JSON from verified PageIR JSONs.

    The process is as follows:

    1. Persist stitching run metadata.
    2. Load and validate verified PageIR JSONs from the verification output directory.
    3. Stitch PageIRs into a single layout-level DocumentIR by merging cross-page
        continuations (tables and text/list blocks) and preserving provenance.
    4. Persist stitching run metadata.

    Parameters
    ----------
    pdf_fp
        The file path to the PDF document to stitch.
    verification_run_results_dir
        Directory containing the verification run results.
    keep_artifacts
        Whether to keep artifacts such as page numbers, headers, footers, etc. after
        stitching.
    repair_hyphenation
        Whether to disable hyphenation repair for stitched text.
    overwrite
        Whether to overwrite existing document IR JSON.

    Raises
    ------
    Exception
        If any part of the stitching process fails.
    ValueError
        If the expected doc_key does not match the computed doc key.
    """

    verification_run_results_dir = verification_run_results_dir.resolve()
    page_irs_verified_dir = verification_run_results_dir / "page_irs_verified"
    verification_config_fp = verification_run_results_dir / "verification_run.json"
    verification_run_config = open_json_type(verification_config_fp)
    stitching_results_dir = verification_run_results_dir.parent / "stitching"

    # 1.
    stitching_dirs, stitching_run = persist_stitching_run(
        output_dir=stitching_results_dir, **verification_run_config
    )

    expected_doc_key = verification_run_config.get("extra", {}).get("doc_key")
    computed_doc_key = compute_doc_key(n_hex=64, pdf_fp=pdf_fp)

    if computed_doc_key != expected_doc_key:
        raise ValueError(
            f"PDF doc_key mismatch.\n"
            f"  PDF provided to verify():   {pdf_fp}\n"
            f"  computed doc_key:           {computed_doc_key}\n"
            f"  verification_run.json key:  {expected_doc_key}\n"
            f"You are likely stitching against a different PDF than the one used for "
            f"verification. Pass the same PDF used in step 1 or re-run verification."
        )

    logger.info(
        f"Starting document IR stitching process using verified page IR JSONs from: "
        f"{page_irs_verified_dir}"
    )
    logger.info(f"Loaded verification run config: {verification_run_config}")
    logger.info(f"Saving stitching results to: {stitching_results_dir}")

    try:
        # 2.
        verified_page_irs = load_page_irs_from_verification(
            expected_doc_key=expected_doc_key,
            verified_page_irs_dir=page_irs_verified_dir,
        )

        # 3.
        stitch_document_ir(
            doc_key=expected_doc_key,
            keep_artifacts=keep_artifacts,
            overwrite=overwrite,
            page_irs=verified_page_irs,
            pdf_name=pdf_fp.name,
            repair_hyphenation=repair_hyphenation,
            stitching_dirs=stitching_dirs,
        )
        stitching_run.extra["status"] = "success"
        logger.success("Document IR stitching completed successfully!")
    except Exception as e:  # pylint: disable=broad-except
        stitching_run.extra["status"] = "error"
        stitching_run.extra["error"] = {
            "message": str(e),
            "traceback": traceback.format_exc(limit=20),
            "type": e.__class__.__name__,
        }
        raise
    finally:
        # 4.
        stitching_run.completed_at = datetime.now(timezone.utc)
        write_to_json(
            fp=stitching_dirs.root / "stitching_run.json", json_info=stitching_run
        )


if __name__ == "__main__":
    cli()
