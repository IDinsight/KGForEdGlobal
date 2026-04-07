"""This module contains the entry point for converting the document IR JSON (layout)
from step 3 into a canonical IR JSON (semantic). This is step 4.

Step 4 does the following:

1. Loads the DocumentIR JSON from the stitching run results directory.
2. Builds caption bindings (deterministic caption -> table segment mapping).
3. Loads and validates the CurriculumSkeleton JSON.
4. Runs the curriculum skeleton matching engine (deterministic segment -> skeleton-node
    matching).
5. Translates matches into a list of SegmentDecisions (one per segment, no chunking).
6. Wraps SegmentDecisions into a SegmentDecisionSet and persists to disk.
7. Compiles a CanonicalIR from DocumentIR + SegmentDecisionSet using the compiler.
8. Generates a curriculum skeleton match report for diagnostics.

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
from skg.canonical_ir.curriculum_skeleton import (
    build_caption_bindings,
    generate_curriculum_match_report,
    load_curriculum_skeleton,
    match_curriculum,
    prepare_matchable_segments,
    translate_segments,
)
from skg.canonical_ir.schemas import SegmentDecisionSet, compute_decision_set_id
from skg.canonical_ir.utils import (
    CanonicalIRDirs,
    compile_canonical_ir,
    cross_check_stitching_run,
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
    document_ir: DocumentIR,
) -> None:
    """Create a CanonicalIR JSON from a single DocumentIR JSON.

    The process is as follows:

    1. Build deterministic caption -> table bindings.
    2. Adapt DocumentIR segments into CurriculumMatchableSegment.
    3. Load and validate the curriculum skeleton file.
    4. Run the forward-only curriculum skeleton matching engine.
    5. Translate curriculum matches into a list of SegmentDecisions (one decision per
        segment).
    6. Wrap SegmentDecisions into a SegmentDecisionSet and persist to disk.
    7. Compile a CanonicalIR from DocumentIR + SegmentDecisionSet using the compiler.
    8. Generate a curriculum skeleton match report for diagnostics.

    Parameters
    ----------
    config
        The canonical IR creation run configuration.
    creation_dirs
        The canonical IR creation directories.
    doc_key
        The expected document key for all page IRs.
    document_ir
        The validated DocumentIR to convert into a CanonicalIR.
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

    # 1.
    caption_bindings = build_caption_bindings(
        bind_unknown_caption=config.bind_unknown_caption,
        creation_dirs=creation_dirs,
        document_ir=document_ir,
        max_gap_segments=config.caption_max_gap_segments,
        max_page_distance=config.caption_max_page_distance,
    )

    # 2.
    matchable_segments = prepare_matchable_segments(
        caption_bindings=caption_bindings, document_ir=document_ir
    )

    # 3.
    curriculum_skeleton = load_curriculum_skeleton(config.curriculum_skeleton_fp)

    # 4.
    curriculum_match_results = match_curriculum(
        curriculum_skeleton=curriculum_skeleton,
        max_skip_distance=config.max_skip_distance,
        segments=matchable_segments,
    )

    # 5.
    segment_decisions = translate_segments(
        curriculum_match_results=curriculum_match_results,
        doc_key=doc_key,
        matchable_segments=matchable_segments,
        role_order=curriculum_skeleton.metadata.context_groupings_role_order,
    )

    # 6.
    decision_set = SegmentDecisionSet.model_validate(
        {
            "decision_set_id": compute_decision_set_id(decisions=segment_decisions),
            "decisions": [d.model_dump(mode="json") for d in segment_decisions],
            "doc_key": doc_key,
            "generator": f"curriculum_skeleton:{curriculum_skeleton.skeleton_id}",
            "pdf_name": document_ir.pdf_name,
        }
    )
    write_to_json(fp=segment_decisions_fp, json_info=decision_set)

    logger.success(f"Saved segment decisions to: {segment_decisions_fp}")

    # 7.
    compile_canonical_ir(
        canonical_ir_fp=canonical_ir_fp,
        doc_key=doc_key,
        document_ir=document_ir,
        segment_decisions=decision_set,
    )

    # 8.
    generate_curriculum_match_report(
        curriculum_match_report_fp=curriculum_match_report_fp,
        curriculum_match_results=curriculum_match_results,
        curriculum_skeleton=curriculum_skeleton,
        total_segments=len(matchable_segments),
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
    """Create a semantic CanonicalIR JSON from a single DocumentIR JSON.

    The process is as follows:

    1. Load config and validate extraction run existence.
    2. Cross-check stitching run results.
    3. Persist canonical IR creation run metadata.
    4. Create canonical IR from the document IR JSON.

    Parameters
    ----------
    config_fp
        The file path to the global config file for the pipeline.

    Raises
    ------
    Exception
        If any part of the canonical IR creation process fails.
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
    expected_doc_key = extraction_run_config.extra["doc_key"]

    # 2.
    document_ir = cross_check_stitching_run(
        canonical_ir_config=config,
        computed_doc_key=computed_doc_key,
        expected_doc_key=expected_doc_key,
        extraction_config=extraction_config,
        document_ir_fp=document_ir_fp,
    )

    # 3.
    creation_results_dir = extraction_config.output_dir / expected_doc_key / "canonical"
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
            document_ir=document_ir,
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
