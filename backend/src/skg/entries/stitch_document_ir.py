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
3. Generate deterministic LC KG IDs.
4. Export Learning Commons KG nodes/edges.
5. Create Learning Commons KG entities/edges.

Those belong in later stages (i.e., canonicalization and knowledge graph creation).

Invoke from the backend directory via:

python src/skg/entries/stitch_document_ir.py ../examples/tanzania/config.json
"""

# Standard Library
import sys
import traceback

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
from skg.document_ir.schemas import SectionHeadingRef, Segment
from skg.document_ir.utils import (
    DocumentIRDirs,
    ItemKey,
    assert_page_items_consumed_exactly_once,
    build_continuation_chain,
    compute_page_break_links,
    materialize_segment,
    normalize_page_items,
    persist_stitching_run,
    save_document_ir,
    update_section_stack,
)
from skg.page_ir_extraction.schemas import Block, PageIR, Table
from skg.page_ir_verification.utils import load_page_irs_from_verification
from skg.schemas import RunConfig, RunCtx, StitchingConfig
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

    warnings: list[str] = []

    # Normalize items per page.
    items_mapping: dict[int, list[tuple[int, Block | Table]]] = {
        page_ir.page_index: normalize_page_items(
            keep_artifacts=config.keep_artifacts,
            page_ir=page_ir,
            sort_items_by_bbox=config.sort_items_by_bbox,
            warnings=warnings,
        )
        for page_ir in page_irs
    }

    # Debug collectors for report output.
    link_debug: list[dict[str, Any]] = []
    page_pair_debug: list[dict[str, Any]] = []

    # Compute page break links based on verified boundary flags.
    links = compute_page_break_links(
        items_mapping=items_mapping,
        link_debug=link_debug,
        min_link_score=config.min_link_score,
        page_irs=page_irs,
        page_pair_debug=page_pair_debug,
        warnings=warnings,
    )

    # Set of destination keys to identify items that are continuations.
    continuations = set(links.values())

    # Reverse map: destination -> list of sources that point to it (for debugging).
    reverse_links: dict[ItemKey, list[ItemKey]] = defaultdict(list)
    for src, dst in links.items():
        reverse_links[dst].append(src)

    # Maintain a semantic-light heading context for later canonicalization. This is
    # intentionally simple: we keep the most recent headings in reading order (no
    # heading-level inference at this stage).
    items_lookup: dict[int, dict[int, Block | Table]] = {
        page_index: dict(items) for page_index, items in items_mapping.items()
    }
    section_path_stack: list[SectionHeadingRef] = []
    segments: list[Segment] = []
    visited: set[ItemKey] = set()

    # Iterate in document reading order: page order, then item order.
    for page_ir in page_irs:
        page_index = page_ir.page_index
        page_items = items_mapping.get(page_index, [])

        logger.info(f"Stitching page {page_index}...\n")

        for orig_item_index, item in page_items:
            key = (page_index, orig_item_index)

            if key in visited:  # Skip if already processed
                continue

            # If this item is a continuation destination but wasn't actually consumed
            # by a previous chain, treat it as an "orphan continuation" and process it
            # as a standalone chain start (with a warning).
            if key in continuations:
                text = (
                    f"Orphan continuation destination encountered; "
                    f"it was pointed-to by a prior page-break link but not consumed in any chain. "
                    f"dest={key}, sources={reverse_links.get(key, [])}. "
                    f"Processing as standalone."
                )
                logger.warning(text)
                warnings.append(text)
                input(999)

            # Build the continuation chains.
            chain = build_continuation_chain(
                items_lookup=items_lookup,
                links=links,
                start_item=item,
                start_key=key,
                warnings=warnings,
            )

            # Mark all items in chain as visited.
            for page_index, item_index, _ in chain:
                visited.add((page_index, item_index))

            # Snapshot section_path *before* materializing this segment. The current
            # item should not appear in its own section path.
            section_path_snapshot = list(section_path_stack)

            # Materialize a stitched segment from the chain.
            segments.append(
                materialize_segment(
                    chain=chain,
                    doc_key=doc_key,
                    item_index=orig_item_index,
                    page_index=page_index,
                    repair_hyphenation=config.repair_hyphenation,
                    section_path=section_path_snapshot,
                    table_filldown_enabled=config.table_filldown_enabled,
                    table_filldown_group_cols_max=config.table_filldown_group_cols_max,
                    warnings=warnings,
                )
            )

            # Update section heading stack *after* processing a heading block. We use
            # the first item in the chain (heading segments are standalone).
            section_path_stack = update_section_stack(
                chain=chain,
                max_len=config.max_section_path_length,
                section_path_stack=section_path_stack,
                warnings=warnings,
            )

    logger.success("Successfully stitched page IRs!")

    # Check that very normalized PageIR item must be consumed exactly once.
    assert_page_items_consumed_exactly_once(
        items_mapping=items_mapping, segments=segments
    )

    # Write results to file.
    save_document_ir(
        doc_key=doc_key,
        document_ir_fp=document_ir_fp,
        link_debug=link_debug,
        links=links,
        page_irs=page_irs,
        page_pair_debug=page_pair_debug,
        pdf_name=pdf_name,
        segments=segments,
        stitching_dirs=stitching_dirs,
        warnings=warnings,
    )


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
    run_config = RunConfig.model_validate(open_json_type(config_fp))
    config = run_config.document_ir
    extraction_config = run_config.page_ir_extraction
    computed_doc_key = compute_doc_key(n_hex=64, pdf_fp=extraction_config.pdf_fp)
    extraction_run_results_dir = (
        extraction_config.output_dir / computed_doc_key / "extraction"
    )
    page_irs_verified_dir = (
        extraction_config.output_dir
        / computed_doc_key
        / "verification"
        / "page_irs_verified"
    )
    extraction_run_config = RunCtx.model_validate(
        open_json_type(extraction_run_results_dir / "extraction_run.json")
    )

    # 2.
    expected_doc_key = extraction_run_config.extra["doc_key"]

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

    # 3.
    verified_page_irs = load_page_irs_from_verification(
        doc_key=expected_doc_key, verified_page_irs_dir=page_irs_verified_dir
    )

    # 4.
    stitching_dirs, stitching_run = persist_stitching_run(
        config=config, output_dir=stitching_results_dir
    )

    try:
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
