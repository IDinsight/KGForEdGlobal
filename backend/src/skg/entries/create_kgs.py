"""This module contains the entry point for exporting Learning Commons knowledge graphs
from a canonical IR JSON file. This is step 5.

Step 5 does the following:

1. XXX

Invoke from the backend directory via:

python src/skg/entries/create_kgs.py ../examples/tanzania/config.json
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
from skg.kgs.utils import persist_kg_run
from skg.schemas import RunConfig, RunCtx
from skg.utils.general import open_json_type, write_to_json
from skg.utils.pdf import compute_doc_key

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


@cli.command()
def create_kgs(
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
    """Create LC KGs from the CanonicalIR JSON.

    The process is as follows:

    1. Load config and validate extraction run existence.
    2. Check doc_key consistency.
    3. Persist KG creation run metadata.
    4. Create Learning Commons knowledge graphs:
        4a. XXX

    Parameters
    ----------
    config_fp
        The file path to the global config file for the pipeline.

    Raises
    ------
    Exception
        If any part of the knowledge graph creation process fails.
    ValueError
        If the computed doc_key from the PDF does not match the doc_key in the
        canonical IR run metadata.
    """

    # 1.
    run_config = RunConfig.model_validate(open_json_type(config_fp))
    config = run_config.kgs
    extraction_config = run_config.page_ir_extraction
    computed_doc_key = compute_doc_key(n_hex=64, pdf_fp=extraction_config.pdf_fp)
    extraction_run_results_dir = (
        extraction_config.output_dir / computed_doc_key / "extraction"
    )
    canonical_ir_fp = (
        extraction_config.output_dir
        / computed_doc_key
        / "canonical"
        / "canonical_ir.json"
    )
    extraction_run_config = RunCtx.model_validate(
        open_json_type(extraction_run_results_dir / "extraction_run.json")
    )

    # 2.
    expected_doc_key = extraction_run_config.extra["doc_key"]

    if computed_doc_key != expected_doc_key:
        raise ValueError(
            f"PDF doc_key mismatch.\n"
            f"  PDF provided to verify():  {extraction_config.pdf_fp}\n"
            f"  computed doc_key:          {computed_doc_key}\n"
            f"  extraction_run.json key:    {expected_doc_key}\n"
            f"You are likely creating KGs using a different PDF than the one used to "
            f"create the canonical IR. Pass the same PDF used in the canonical IR run "
            f"or re-run the canonical IR."
        )

    kg_results_dir = extraction_config.output_dir / expected_doc_key / "kgs"

    # 3.
    kg_dirs, kg_run = persist_kg_run(config=config, output_dir=kg_results_dir)

    try:
        # 4.
        logger.info(
            f"Starting KG creation process using canonical IR JSON: {canonical_ir_fp}"
        )

        # 5.
        kg_run.extra["status"] = "success"
        logger.success("KG creation completed successfully!")
    except Exception as e:  # pylint: disable=broad-except
        kg_run.extra["status"] = "error"
        kg_run.extra["error"] = {
            "message": str(e),
            "traceback": traceback.format_exc(limit=30),
            "type": e.__class__.__name__,
        }
        raise
    finally:
        kg_run.completed_at = datetime.now(timezone.utc)
        write_to_json(fp=kg_dirs.root / "kg_run.json", json_info=kg_run)


if __name__ == "__main__":
    cli()
