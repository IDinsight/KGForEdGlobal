"""This module contains the entry point for creating Learning Commons KG-ready
extraction artifacts from a stitched DocumentIR.

This module implements the first step of the simplified KG creation pipeline:

1. Load and validate a country/document-specific `DocumentProfile`.
2. Load and validate the corresponding stitched `DocumentIR`.
3. Cross-check that the profile is compatible with the DocumentIR.
4. Create the KG run output directory.
5. Persist a `kg_run_manifest.json` for audit/debugging.

Later steps will build extraction windows, run LLM-based SFI candidate extraction,
compile final SFIs, generate LearningComponents, infer LearningProgressions, and
export KG schema objects.

Invoke from the backend directory via:

python src/skg/entries/create_kgs.py ../examples/ghana/config_math_curriculum.json
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
from skg.kgs.utils import (
    build_run_manifest,
    cross_check_stitching_run,
    load_and_validate_inputs,
)
from skg.schemas import RunConfig
from skg.utils.general import open_json_type, write_to_json
from skg.utils.pdf import compute_doc_key

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


@cli.command()
def create(
    config_fp: Path = typer.Argument(
        ...,
        dir_okay=False,
        exists=True,
        file_okay=True,
        help="The file path to the global runtime config file for the pipeline.",
        readable=True,
        resolve_path=True,
    )
) -> None:
    """Create the initial KG run manifest from the global runtime config.

    The process is as follows:

    1. Load the global run config and resolve KG, extraction, and stitching paths.
    2. Cross-check stitching run results.
    3. Load and validate the DocumentProfile and stitched DocumentIR.
    4. Cross-check basic profile/document compatibility.
    5. Create the KG run output directory.
    6. Persist a kg_run_manifest.json file for audit/debugging.

    Parameters
    ----------
    config_fp
        The file path to the global runtime config file for the pipeline.

    Raises
    ------
    Exception
        If any error occurs during knowledge graph creation.
    ValueError
        If any error occurs during knowledge graph creation.
    FileNotFoundError
        If the extraction run metadata file does not exist.
    """

    # 1.
    run_config = RunConfig.model_validate(open_json_type(config_fp))
    config = run_config.kgs

    if config is None:
        raise ValueError(
            "RunConfig.kgs is required for KG creation, but the runtime config does "
            "not contain a kgs section."
        )

    extraction_config = run_config.page_ir_extraction
    computed_doc_key = compute_doc_key(n_hex=64, pdf_fp=extraction_config.pdf_fp)

    # 2.
    extraction_run_fp, document_ir_fp = cross_check_stitching_run(
        computed_doc_key=computed_doc_key, extraction_config=extraction_config
    )

    kg_run_manifest: dict[str, Any] = {}
    kg_run_manifest_fp: Path | None = None

    try:
        document_profile_fp = config.document_profile_fp
        output_dir = extraction_config.output_dir

        logger.info(
            f"Starting KG creation prep using runtime config: {config_fp}; "
            f"DocumentIR: {document_ir_fp}; document profile: {document_profile_fp}"
        )

        # 3-5.
        kg_run_inputs = load_and_validate_inputs(
            document_ir_fp=document_ir_fp,
            document_profile_fp=document_profile_fp,
            output_dir=output_dir,
            overwrite=config.overwrite,
        )

        # 6.
        kg_run_manifest = build_run_manifest(kg_run_inputs)
        kg_run_manifest["config_fp"] = str(config_fp)
        kg_run_manifest["computed_doc_key"] = computed_doc_key
        kg_run_manifest["extraction_run_fp"] = str(extraction_run_fp)
        kg_run_manifest["generate_learning_progressions"] = (
            config.generate_learning_progressions
        )
        kg_run_manifest_fp = kg_run_inputs.kg_dirs.root / "kg_run_manifest.json"
        write_to_json(fp=kg_run_manifest_fp, json_info=kg_run_manifest)

        logger.success(f"KG creation prep completed successfully: {kg_run_manifest_fp}")
    except Exception as e:  # pylint: disable=broad-except
        logger.error(f"KG creation prep failed: {e.__class__.__name__}: {str(e)}")

        if kg_run_manifest_fp is not None:
            kg_run_manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
            kg_run_manifest["error"] = {
                "message": str(e),
                "traceback": traceback.format_exc(limit=20),
                "type": e.__class__.__name__,
            }
            kg_run_manifest["status"] = "error"
            write_to_json(fp=kg_run_manifest_fp, json_info=kg_run_manifest)

        raise


if __name__ == "__main__":
    cli()
