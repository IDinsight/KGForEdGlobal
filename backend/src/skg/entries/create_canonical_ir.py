"""This module contains the entry point for converting the DocumentIR JSON (layout)
from step 3 into CanonicalIR (semantic). This is step 4.

Step 4 does the following:

1. Loads the DocumentIR JSON from the stitching run results directory.
2. Loads the custom parser config for the PDF.
3. Parses the document into a CanonicalIR using country/PDF-family-specific rules.
4. Exports the CanonicalIR JSON to the canonical IR creation results directory.

Invoke from the backend directory via:

python src/skg/entries/create_canonical_ir.py ../examples/tanzania/parser_config.json ../data/tanzania/tanzania.pdf /path/to/stitching_run_results
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
from skg.canonical_ir.parse_document import parse_document
from skg.canonical_ir.schemas import ParserConfig
from skg.canonical_ir.utils import (
    CanonicalIRDirs,
    export_canonical_ir,
    persist_creation_run,
)
from skg.document_ir.schemas import DocumentIR
from skg.utils.general import open_json_type, write_to_json
from skg.utils.pdf import compute_doc_key

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


def create_canonical_ir(
    *,
    creation_dirs: CanonicalIRDirs,
    document_ir_fp: Path,
    overwrite: bool,
    parser_config: ParserConfig,
    wizard_mode: bool,
) -> None:
    """Create a CanonicalIR JSON from a single DocumentIR JSON.

    Parameters
    ----------
    creation_dirs
        The canonical IR creation directories.
    document_ir_fp
        The file path to the DocumentIR JSON.
    overwrite
        Whether to overwrite existing canonical IR JSON.
    parser_config
        The parser configuration to use.
    wizard_mode
        Whether to enable wizard mode to capture additional diagnostics for unmatched
        content.
    """

    canonical_ir_fp = creation_dirs.root / "canonical_ir.json"

    if not overwrite and canonical_ir_fp.exists():
        logger.warning(
            f"Canonical IR JSON already exists at {canonical_ir_fp}. "
            f"Skipping creation. If you wish to overwrite, pass the --overwrite flag."
        )
        return

    # Validate and load the Document IR.
    document_ir = DocumentIR.model_validate(open_json_type(document_ir_fp))

    # Parse the document IR into a canonical IR.
    canonical_ir = parse_document(
        config=parser_config, document_ir=document_ir, wizard_mode=wizard_mode
    )

    # Export the canonical IR.
    export_canonical_ir(canonical_ir=canonical_ir, output_path=canonical_ir_fp)


@cli.command()
def create(
    parser_config_fp: Path = typer.Argument(
        ...,
        dir_okay=False,
        exists=True,
        file_okay=True,
        help="File path to a custom parser config JSON.",
        readable=True,
        resolve_path=True,
    ),
    pdf_fp: Path = typer.Argument(
        ...,
        dir_okay=False,
        exists=True,
        file_okay=True,
        help="The file path to the PDF document to create the canonical IR for.",
        readable=True,
        resolve_path=True,
    ),
    stitching_run_results_dir: Path = typer.Argument(
        ...,
        dir_okay=True,
        exists=True,
        file_okay=False,
        help="The stitching run results directory.",
        resolve_path=True,
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Overwrite existing canonical IR JSON."
    ),
    wizard_mode: bool = typer.Option(
        True,
        "--wizard-mode",
        help="Whether to enable wizard mode to capture additional diagnostics for unmatched content.",
    ),
) -> None:
    """Create a CanonicalIR JSON from a single DocumentIR JSON.

    The process is as follows:

    1. Persist canonical IR creation run metadata.
    2. Load parser config.
    3. Create canonical IR from DocumentIR JSON.
    4. Persist canonical IR creation run metadata.

    Parameters
    ----------
    parser_config_fp
        File path to a custom parser config JSON.
    pdf_fp
        The file path to the PDF document to create the canonical IR for.
    stitching_run_results_dir
        Directory containing the stitching run results.
    overwrite
        Whether to overwrite existing canonical IR JSON.
    wizard_mode
        Whether to enable wizard mode to capture additional diagnostics for unmatched
        content.

    Raises
    ------
    Exception
        If any part of the canonical IR creation process fails.
    ValueError
        If the expected doc_key does not match the computed doc key.
    """

    stitching_run_results_dir = stitching_run_results_dir.resolve()
    document_ir_fp = stitching_run_results_dir / "document_ir.json"
    stitching_config_fp = stitching_run_results_dir / "stitching_run.json"
    stitching_run_config = open_json_type(stitching_config_fp)
    creation_results_dir = stitching_run_results_dir.parent / "creation"

    # 1.
    creation_dirs, creation_run = persist_creation_run(
        output_dir=creation_results_dir, **stitching_run_config
    )

    expected_doc_key = stitching_run_config.get("extra", {}).get("doc_key")
    computed_doc_key = compute_doc_key(n_hex=64, pdf_fp=pdf_fp)

    if computed_doc_key != expected_doc_key:
        raise ValueError(
            f"PDF doc_key mismatch.\n"
            f"  PDF provided to verify():  {pdf_fp}\n"
            f"  computed doc_key:          {computed_doc_key}\n"
            f"  stitching_run.json key:    {expected_doc_key}\n"
            f"You are likely creating a canonical IR against a different PDF than the "
            f"one used for stitching. Pass the same PDF used in the stitching run or "
            f"re-run stitching."
        )

    logger.info(
        f"Starting canonical IR creation process using document IR JSON: {document_ir_fp}"
    )
    logger.info(f"Loaded stitching run config: {stitching_run_config}")
    logger.info(f"Saving creation results to: {creation_dirs}")

    try:
        # 2.
        config_dict = open_json_type(parser_config_fp)
        parser_config = ParserConfig.model_validate(config_dict)

        # 3.
        create_canonical_ir(
            creation_dirs=creation_dirs,
            document_ir_fp=document_ir_fp,
            overwrite=overwrite,
            parser_config=parser_config,
            wizard_mode=wizard_mode,
        )
        creation_run.extra["status"] = "success"
        logger.success("Canonical IR creation completed successfully!")
    except Exception as e:  # pylint: disable=broad-except
        creation_run.extra["status"] = "error"
        creation_run.extra["error"] = {
            "message": str(e),
            "traceback": traceback.format_exc(limit=20),
            "type": e.__class__.__name__,
        }
        raise
    finally:
        # 4.
        creation_run.completed_at = datetime.now(timezone.utc)
        write_to_json(
            fp=creation_dirs.root / "creation_run.json", json_info=creation_run
        )


if __name__ == "__main__":
    cli()
