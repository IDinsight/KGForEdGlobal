"""This module contains the entry point for verifying the continuity of the extracted
page IR JSONs from step 1. This is step 2.

Step 2 reads per-page PageIR JSONs from Step 1, runs pairwise (N, N+1) continuity
verification on selected boundary-candidate items, and writes corrected PageIR JSONs
to the verification output directory.

Key guarantee for Step 3: boundaries are patched when confidence is high; otherwise
extraction boundaries are preserved.

Invoke from the backend directory via:

python src/kgfeg/entries/verify_page_ir_continuity.py ../examples/tanzania/config.json
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
from kgfeg.page_ir_extraction.schemas import PageIR
from kgfeg.page_ir_verification.compile_continuity import run_compile_step
from kgfeg.page_ir_verification.llm import VerificationUsageTracker
from kgfeg.page_ir_verification.postprocess_page_irs import run_postprocess_step
from kgfeg.page_ir_verification.utils import (
    EdgeVerdictRecord,
    PageIRVerificationDirs,
    cross_check_extraction_run,
    load_edge_verdict_from_pair_report,
    persist_verification_run,
    save_verified_page_irs,
)
from kgfeg.page_ir_verification.verify_page_pairs import verify_single_page_pair
from kgfeg.schemas import RunConfig, RunCtx, VerificationConfig
from kgfeg.utils.general import open_json_type, write_to_json
from kgfeg.utils.pdf import compute_doc_key, validate_page_count

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


def verify_page_ir_continuity(
    *,
    config: VerificationConfig,
    page_images_dir: Path,
    page_irs: dict[int, PageIR],
    start: int,
    stop: int,
    usage_tracker: VerificationUsageTracker,
    verification_dirs: PageIRVerificationDirs,
) -> None:
    """Perform verification of PageIR JSONs in pairs.

    Parameters
    ----------
    config
        The verification run configuration.
    page_images_dir
        Directory containing the page images.
    page_irs
        The dictionary of page IRs by page index.
    start
        0-based start page (inclusive).
    stop
        0-based end page (exclusive).
    usage_tracker
        Tracker to accumulate token usage across all page verifications.
    verification_dirs
        The verification directories.

    Raises
    ------
    RuntimeError
        If continuity verification fails completely for any page pair.
        If the compile step needs to run but cannot because of existing outputs while
            overwrite=False.
        If the postprocess step needs to run but cannot because of existing outputs
            while overwrite=False.
        If the save step needs to run but cannot because of existing outputs while
            overwrite=False.
    """

    # Edge records will hold one record per boundary (page i -> i + 1) containing:
    #  1. Which candidate items were compared
    #  2. What the model decided (continuation, type, confidence, etc.)
    edge_records: list[EdgeVerdictRecord] = []

    # Iterate in pairs, skipping boundaries that already have a pair report on disk
    # (resumed run). Existing reports are reloaded into edge_records so the compile
    # step sees the full picture.
    for i in range(start, stop - 1):
        pair_report_fp = (
            verification_dirs.page_irs_pair_reports / f"{i:04}_{i + 1:04}.json"
        )

        if pair_report_fp.exists() and not config.overwrite:
            try:
                edge_records.append(load_edge_verdict_from_pair_report(pair_report_fp))
                logger.info(
                    f"Reloaded existing pair report for pages {i}-{i + 1} "
                    f"(skipping verification)."
                )
                continue
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    f"Failed to reload pair report for pages {i}-{i + 1}, "
                    f"re-verifying: {e}"
                )

        record = verify_single_page_pair(
            config=config,
            page_images_dir=page_images_dir,
            page_index=i,
            page_irs=page_irs,
            usage_tracker=usage_tracker,
            verification_dirs=verification_dirs,
        )

        if record:
            edge_records.append(record)

    compile_ran, verified_table_edges = run_compile_step(
        config=config,
        edge_records=edge_records,
        page_irs=page_irs,
        verification_dirs=verification_dirs,
    )

    postprocess_ran = run_postprocess_step(
        compile_ran=compile_ran,
        config=config,
        page_irs=page_irs,
        verification_dirs=verification_dirs,
        verified_table_edges=verified_table_edges,
    )

    save_verified_page_irs(
        compile_ran=compile_ran,
        config=config,
        page_irs=page_irs,
        postprocess_ran=postprocess_ran,
        start=start,
        stop=stop,
        verification_dirs=verification_dirs,
    )


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

    1. Load the global run config and directory paths for the extraction run results.
    2. Create a usage tracker to accumulate token costs across all pages.
    3. Validate page range.
    4. Check that the page images and page IR directories have matching files and the
        document key matches the PDF.
    5. Persist verification run metadata.
    6. Load all page IR JSONs so that we can apply edits and then write once.
    7. Run pairwise continuity verification across (N, N+1) in the selected page range
        and write verified page IR JSONs to file.

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
    run_config = RunConfig.model_validate(open_json_type(config_fp))
    config = run_config.page_ir_verification
    extraction_config = run_config.page_ir_extraction
    computed_doc_key = compute_doc_key(n_hex=64, pdf_fp=extraction_config.pdf_fp)
    extraction_run_results_dir = (
        extraction_config.output_dir / computed_doc_key / "extraction"
    )
    page_images_dir = extraction_run_results_dir / "page_images"
    page_irs_dir = extraction_run_results_dir / "page_irs"
    extraction_run_config = RunCtx.model_validate(
        open_json_type(extraction_run_results_dir / "extraction_run.json")
    )
    expected_doc_key = extraction_run_config.extra["doc_key"]

    # 2.
    usage_tracker = VerificationUsageTracker()

    with pymupdf.open(str(extraction_config.pdf_fp)) as doc:
        # 3.
        _, start_page, end_page = validate_page_count(
            doc=doc, end_page=config.end_page, start_page=config.start_page
        )

    # 4.
    start, end = cross_check_extraction_run(
        computed_doc_key=computed_doc_key,
        end_page=end_page,
        expected_doc_key=expected_doc_key,
        extraction_config=extraction_config,
        page_images_dir=page_images_dir,
        page_irs_dir=page_irs_dir,
        start_page=start_page,
    )

    # 5.
    verification_dirs, verification_run = persist_verification_run(
        config=config,
        output_dir=extraction_config.output_dir / expected_doc_key / "verification",
    )

    try:
        # 6.
        page_irs = {
            i: PageIR.model_validate(open_json_type(page_irs_dir / f"{i:04}.json"))
            for i in range(start, end)
        }

        # 7.
        logger.info(
            f"Starting page IR continuity verification process using directories: "
            f"{page_images_dir} and {page_irs_dir}"
        )

        verify_page_ir_continuity(
            config=config,
            page_images_dir=page_images_dir,
            page_irs=page_irs,
            start=start,
            stop=end,
            usage_tracker=usage_tracker,
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
        verification_run.extra["usage"] = usage_tracker.to_dict()
        verification_run.completed_at = datetime.now(timezone.utc)
        write_to_json(
            fp=verification_dirs.root / "verification_run.json",
            json_info=verification_run,
        )


if __name__ == "__main__":
    cli()
