"""This module contains the entry point for converting the DocumentIR JSON (layout)
from step 3 into CanonicalIR (semantic). This is step 4.

Step 4 does the following:

1. Loads the DocumentIR JSON from the stitching run results directory.
2. Builds caption bindings (deterministic caption -> table segment mapping).
3. Loads and validates the CurriculumSkeleton JSON.
4. Runs the curriculum skeleton matching engine (deterministic segment -> skeleton-node
    matching).
5. Translates matches into a SegmentDecisionSet (one decision per segment, no chunking).
6. Compiles a CanonicalIR from DocumentIR + SegmentDecisionSet using the compiler.
7. Exports the CanonicalIR JSON and a skeleton MatchReport to the canonical IR creation
   results directory.

Invoke from the backend directory via:

python src/skg/entries/create_canonical_ir.py ../examples/tanzania/config.json
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
from skg.canonical_ir.utils import (
    CanonicalIRDirs,
    create_canonical_ir_from_curriculum_skeleton,
    load_curriculum_skeleton,
    load_or_build_caption_bindings,
    persist_canonical_run,
)
from skg.document_ir.schemas import DocumentIR
from skg.schemas import CreateCanonicalConfig, RunConfig, RunCtx
from skg.utils.general import open_json_type, write_to_json
from skg.utils.pdf import compute_doc_key

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


def create_canonical_ir(
    *,
    config: CreateCanonicalConfig,
    creation_dirs: CanonicalIRDirs,
    doc_key: str,
    document_ir_fp: Path,
) -> None:
    """Create a CanonicalIR JSON from a single DocumentIR JSON.

    The process is as follows:

    1. Load and validate the DocumentIR.
    2. Build or load deterministic caption -> table bindings.
    3. Load and validate the CurriculumSkeleton.
    4. Adapt DocumentIR segments into MatchableSegments.
    5. Run the forward-only skeleton matching engine.
    6. Translate matches into a SegmentDecisionSet (one decision per segment).
    7. Compile CanonicalIR from DocumentIR + SegmentDecisionSet.
    8. Export CanonicalIR JSON and MatchReport diagnostics.

    Parameters
    ----------
    config
        The canonical IR creation run configuration.
    creation_dirs
        The canonical IR creation directories.
    doc_key
        The expected document key for all page IRs.
    document_ir_fp
        The file path to the DocumentIR JSON.
    """

    canonical_ir_fp = creation_dirs.canonical_ir / "canonical_ir.json"
    curriculum_match_report_fp = (
        creation_dirs.root / "curriculum_skeleton_match_report.json"
    )
    segment_decisions_fp = creation_dirs.segment_decisions / "segment_decisions.json"

    if not config.overwrite and canonical_ir_fp.exists():
        logger.warning(
            f"Canonical IR JSON already exists at {canonical_ir_fp}. Skipping creation. "
            f"If you wish to overwrite, pass the --overwrite flag."
        )
        return

    # 1. Load DocumentIR.
    document_ir = DocumentIR.model_validate(open_json_type(document_ir_fp))

    # 2. Build caption bindings.
    caption_bindings = load_or_build_caption_bindings(
        bind_unknown_caption=config.bind_unknown_caption,
        creation_dirs=creation_dirs,
        document_ir=document_ir,
        max_gap_segments=config.caption_max_gap_segments,
        max_page_distance=config.caption_max_page_distance,
        overwrite=config.overwrite,
    )

    # 3. Load CurriculumSkeleton.
    logger.info(
        f"Using skeleton pipeline with skeleton: {config.curriculum_skeleton_fp}"
    )

    curriculum_skeleton = load_curriculum_skeleton(config.curriculum_skeleton_fp)

    # 4 - 8.
    report = create_canonical_ir_from_curriculum_skeleton(
        canonical_ir_fp=canonical_ir_fp,
        caption_bindings=caption_bindings,
        curriculum_match_report_fp=curriculum_match_report_fp,
        curriculum_skeleton=curriculum_skeleton,
        doc_key=doc_key,
        document_ir=document_ir,
        segment_decisions_fp=segment_decisions_fp,
    )

    if not report.is_healthy:
        logger.warning(
            f"Curriculum skeleton match is NOT healthy "
            f"(node coverage={report.node_coverage:.1%}, "
            f"jumps={len(report.cursor_jumps)}). "
            f"Review the curriculum match report at: {curriculum_match_report_fp}"
        )


@cli.command()
def create(
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
    """Create a CanonicalIR JSON from a single DocumentIR JSON.

    The process is as follows:

    1. Load config and validate extraction run existence.
    2. Check doc_key consistency.
    3. Persist canonical IR creation run metadata.
    4. Create canonical IR from DocumentIR JSON.

    Parameters
    ----------
    config_fp
        The file path to the global config file for the pipeline.

    Raises
    ------
    Exception
        If any part of the canonical IR creation process fails.
    ValueError
        If the computed doc_key from the PDF does not match the doc_key in the
        stitching run metadata.
    """

    # 1.
    run_config = RunConfig.model_validate(open_json_type(config_fp))
    config = run_config.canonical_ir
    extraction_config = run_config.page_ir_extraction
    computed_doc_key = compute_doc_key(n_hex=64, pdf_fp=extraction_config.pdf_fp)
    extraction_run_results_dir = (
        extraction_config.output_dir / computed_doc_key / "extraction"
    )
    document_ir_fp = (
        extraction_config.output_dir
        / computed_doc_key
        / "stitching"
        / "document_ir.json"
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
            f"  extraction_run.json key:   {expected_doc_key}\n"
            f"You are likely creating a canonical IR against a different PDF than the "
            f"one used for stitching. Pass the same PDF used in the stitching run or "
            f"re-run stitching."
        )

    creation_results_dir = extraction_config.output_dir / expected_doc_key / "canonical"

    # 3.
    creation_dirs, creation_run = persist_canonical_run(
        config=config, output_dir=creation_results_dir
    )

    try:
        # 4.
        logger.info(
            f"Starting canonical IR creation process using document IR JSON: "
            f"{document_ir_fp}"
        )

        create_canonical_ir(
            config=config,
            creation_dirs=creation_dirs,
            doc_key=expected_doc_key,
            document_ir_fp=document_ir_fp,
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
        creation_run.completed_at = datetime.now(timezone.utc)
        write_to_json(
            fp=creation_dirs.root / "creation_run.json", json_info=creation_run
        )


if __name__ == "__main__":
    cli()
