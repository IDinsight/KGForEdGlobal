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

python src/kgfeg/entries/stitch_document_ir.py ../examples/tanzania/config.json
"""

# Standard Library
import sys
import traceback

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
from kgfeg.document_ir.compute_page_break_links import compute_page_break_links
from kgfeg.document_ir.normalize_page_items import normalize_page_items
from kgfeg.document_ir.stitch_segments import build_stitched_segments
from kgfeg.document_ir.utils import (
    DocumentIRDirs,
    assert_page_items_consumed_exactly_once,
    cross_check_verification_run,
    persist_stitching_run,
    save_document_ir,
)
from kgfeg.page_ir_extraction.schemas import Block, PageIR, Table
from kgfeg.page_ir_verification.utils import EdgeVerdictRecord
from kgfeg.schemas import RunConfig, RunCtx, StitchingConfig
from kgfeg.utils.general import open_json_type, write_to_json
from kgfeg.utils.pdf import compute_doc_key

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


def stitch_document_ir(
    *,
    config: StitchingConfig,
    doc_key: str,
    page_irs: list[PageIR],
    pdf_name: str,
    stitching_dirs: DocumentIRDirs,
    verdicts: dict[tuple[int, int], EdgeVerdictRecord],
) -> None:
    """Stitch verified PageIRs into a single DocumentIR.

    The process is as follows:

    1. Normalize items per page (filter artifacts, sort by bbox, propagate captions).
    2. Link cross-page continuations using boundary flags and verification verdicts.
    3. Merge chains of continuations into stitched segments.
    4. Assert that every normalized item is consumed exactly once.
    5. Persist the DocumentIR and stitch report to disk.

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
    verdicts
        The verification verdicts for all page pairs, used for debugging and linking.
    """

    document_ir_fp = stitching_dirs.root / "document_ir.json"

    if not config.overwrite and document_ir_fp.exists():
        logger.warning(
            f"Document IR JSON already exists at {document_ir_fp}. Skipping stitching. "
            f"If you wish to overwrite, pass the --overwrite flag."
        )
        return

    warnings: list[str] = []

    # 1.
    items_mapping: dict[int, list[tuple[int, Block | Table]]] = {
        page_ir.page_index: normalize_page_items(
            keep_artifacts=config.keep_artifacts,
            page_ir=page_ir,
            sort_items_by_bbox=config.sort_items_by_bbox,
            warnings=warnings,
        )
        for page_ir in page_irs
    }

    # 2.
    link_debug: list[dict[str, Any]] = []
    page_pair_debug: list[dict[str, Any]] = []
    links = compute_page_break_links(
        items_mapping=items_mapping,
        link_debug=link_debug,
        min_link_score=config.min_link_score,
        page_irs=page_irs,
        page_pair_debug=page_pair_debug,
        verdict_confidence_threshold=config.verification_auto_stitch_confidence,
        verdicts=verdicts,
        warnings=warnings,
    )

    # 3.
    segments = build_stitched_segments(
        config=config,
        doc_key=doc_key,
        items_mapping=items_mapping,
        links=links,
        page_irs=page_irs,
        warnings=warnings,
    )

    # 4.
    assert_page_items_consumed_exactly_once(
        items_mapping=items_mapping, segments=segments
    )

    # 5.
    save_document_ir(
        doc_key=doc_key,
        document_ir_fp=document_ir_fp,
        items_mapping=items_mapping,
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

    1. Load the global run config and directory paths for the extraction and
        verification run results.
    2. Cross-check verification run results and load verified PageIRs and their
        verdicts.
    3. Persist stitching run metadata.
    4. Stitch PageIRs into a single layout-level DocumentIR by merging cross-page
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
    expected_doc_key = extraction_run_config.extra["doc_key"]

    # 2.
    verdicts, verified_page_irs = cross_check_verification_run(
        computed_doc_key=computed_doc_key,
        expected_doc_key=expected_doc_key,
        extraction_config=extraction_config,
        verified_page_irs_dir=page_irs_verified_dir,
    )

    # 3.
    stitching_results_dir = (
        extraction_config.output_dir / expected_doc_key / "stitching"
    )
    stitching_dirs, stitching_run = persist_stitching_run(
        config=config, output_dir=stitching_results_dir
    )

    try:
        # 4.
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
            verdicts=verdicts,
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
