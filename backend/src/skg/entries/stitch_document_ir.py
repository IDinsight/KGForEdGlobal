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

Those belong in later stages (i.e., canonicalization and knowledge graph creation).

Invoke from the backend directory via:

python src/skg/entries/stitch_document_ir.py ../examples/tanzania/config.json
"""

# Standard Library
import sys
import traceback

from datetime import datetime, timezone
from pathlib import Path

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
from skg.document_ir.schemas import DocumentIR, Segment, StitchingConfig
from skg.document_ir.utils import (
    DocumentIRDirs,
    ItemKey,
    assert_page_items_consumed_exactly_once,
    build_continuation_chain,
    compute_page_break_links,
    materialize_segment,
    normalize_page_items,
    persist_stitching_run,
    uniquify_segment_keys,
)
from skg.page_ir_extraction.schemas import Block, ExtractionConfig, PageIR, Table
from skg.page_ir_verification.utils import load_page_irs_from_verification
from skg.schemas import RunCtx
from skg.utils.general import open_json_type, write_to_json
from skg.utils.pdf import compute_doc_key

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


def stitch_document_ir(
    *,
    config: StitchingConfig,
    doc_key: str,
    page_irs: list[PageIR],
    pdf_name: str,
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
    config
        The stitching run configuration.
    doc_key
        The expected document key for all page IRs.
    page_irs
        Validated PageIR list in page order.
    pdf_name
        The source PDF filename (no path).
    stitching_dirs
        Directories for storing the stitched DocumentIR.
    """

    document_ir_fp = stitching_dirs.root / "document_ir.json"

    if not config.overwrite and document_ir_fp.exists():
        logger.warning(
            f"Document IR JSON already exists at {document_ir_fp}. Skipping stitching."
            f"If you wish to overwrite, pass the --overwrite flag."
        )
        return

    # Normalized items per page and filter artifacts if applicable.
    items_with_idx: dict[int, list[tuple[int, Table | Block]]] = {
        page_ir.page_index: normalize_page_items(
            keep_artifacts=config.keep_artifacts, page_ir=page_ir
        )
        for page_ir in page_irs
    }
    items_lookup: dict[int, dict[int, Table | Block]] = {
        p_idx: dict(items) for p_idx, items in items_with_idx.items()
    }

    warnings: list[str] = []

    # Compute page break links based on verified boundary flags.
    links = compute_page_break_links(
        keep_artifacts=config.keep_artifacts, page_irs=page_irs, warnings=warnings
    )

    # Set of destination keys to identify items that are continuations.
    continuations = set(links.values())

    # Reverse map: destination --> list of sources that point to it (for debugging).
    reverse_links: dict[ItemKey, list[ItemKey]] = {}
    for src, dst in links.items():
        reverse_links.setdefault(dst, []).append(src)

    # Iterate in document reading order: page order, then item order.
    segments: list[Segment] = []
    visited: set[ItemKey] = set()

    for page_ir in page_irs:
        page_idx = page_ir.page_index
        page_items = items_with_idx.get(page_idx, [])

        for original_item_idx, item in page_items:
            key = (page_idx, original_item_idx)

            # Skip if already processed.
            if key in visited:
                continue

            # If this item is a continuation destination but wasn't actually consumed
            # by a previous chain, treat it as an "orphan continuation" and process it
            # as a standalone chain start (with a warning).
            if key in continuations:
                warnings.append(
                    f"Orphan continuation destination encountered;it was pointed-to by "
                    f"a prior page-break link but not consumed in any chain. "
                    f"dest={key}, sources={reverse_links.get(key, [])}. "
                    f"Processing as standalone."
                )

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
                    repair_hyphenation=config.repair_hyphenation,
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
    """Create a stitched DocumentIR JSON from verified PageIR JSONs.

    The process is as follows:

    1. Load config and validate extraction run existence.
    2. Check doc_key consistency.
    3. Load and validate verified PageIR JSONs from the verification output directory.
    4. Persist stitching run metadata.
    5. Stitch PageIRs into a single layout-level DocumentIR by merging cross-page
        continuations (tables and text/list blocks) and preserving provenance.

    Parameters
    ----------
    config_fp
        The file path to the global config file for the pipeline.

    Raises
    ------
    Exception
        If any part of the stitching process fails.
    """

    # 1.
    config = StitchingConfig.model_validate(open_json_type(config_fp)["document_ir"])
    extraction_config = ExtractionConfig.model_validate(
        open_json_type(config_fp)["page_ir_extraction"]
    )
    computed_doc_key = compute_doc_key(n_hex=64, pdf_fp=extraction_config.pdf_fp)
    extraction_run_results_dir = (
        extraction_config.output_dir / computed_doc_key / "extraction"
    )
    page_irs_verified_dir = (
        extraction_config.output_dir / computed_doc_key / "verification" / "page_irs"
    )
    extraction_run_config = RunCtx.model_validate(
        open_json_type(extraction_run_results_dir / "extraction_run.json")
    )

    # 2.
    expected_doc_key = extraction_run_config.extra["doc_key"]
    computed_doc_key = compute_doc_key(n_hex=64, pdf_fp=extraction_config.pdf_fp)

    if computed_doc_key != expected_doc_key:
        raise ValueError(
            f"PDF doc_key mismatch.\n"
            f"  PDF provided to verify():   {extraction_config.pdf_fp}\n"
            f"  computed doc_key:           {computed_doc_key}\n"
            f"  verification_run.json key:  {expected_doc_key}\n"
            f"You are likely stitching against a different PDF than the one used for "
            f"verification. Pass the same PDF used in the verification step or re-run "
            f"verification."
        )

    stitching_results_dir = (
        extraction_config.output_dir / expected_doc_key / "stitching"
    )

    try:
        # 3.
        verified_page_irs = load_page_irs_from_verification(
            doc_key=expected_doc_key, verified_page_irs_dir=page_irs_verified_dir
        )

        # 4.
        stitching_dirs, stitching_run = persist_stitching_run(
            config=config, output_dir=stitching_results_dir
        )

        # 5.
        logger.info(
            f"Starting document IR stitching process using verified page IR JSONs from: "
            f"{page_irs_verified_dir}"
        )

        stitch_document_ir(
            config=config,
            doc_key=expected_doc_key,
            page_irs=verified_page_irs,
            pdf_name=extraction_config.pdf_fp.name,
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
        stitching_run.completed_at = datetime.now(timezone.utc)
        write_to_json(
            fp=stitching_dirs.root / "stitching_run.json", json_info=stitching_run
        )


if __name__ == "__main__":
    cli()
