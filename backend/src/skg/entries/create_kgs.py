"""This module contains the entry point for creating Learning Commons KGs from a
stitched DocumentIR.

Invoke from the backend directory via:

python src/skg/entries/create_kgs.py ../examples/ghana/config_math_curriculum.json
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
from skg.kgs.lc_selection import select_lc_source_sfis
from skg.kgs.llm import KGUsageTracker
from skg.kgs.sfi_dedup import merge_sfi_candidates
from skg.kgs.sfi_export import compile_academic_standards_kg
from skg.kgs.sfi_extraction import extract_sfi_candidates_from_windows
from skg.kgs.sfi_extraction_windows import (
    build_llm_extraction_windows,
    plan_extraction_windows,
)
from skg.kgs.sfi_finalization import mint_final_sfi_ids
from skg.kgs.sfi_registry import build_candidate_registry
from skg.kgs.sfi_relationships import resolve_has_child_edges
from skg.kgs.utils import (
    KGDirs,
    build_run_manifest,
    cross_check_stitching_run,
    load_and_validate_inputs,
    persist_kg_run,
)
from skg.schemas import CreateKGConfig, RunConfig
from skg.utils.general import open_json_type, write_to_json
from skg.utils.pdf import compute_doc_key

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


def build_kgs(
    *,
    config: CreateKGConfig,
    document_ir_fp: Path,
    kg_dirs: KGDirs,
    usage_tracker: KGUsageTracker,
) -> Path:
    """Build Academic Standards + Learning Components KG artifacts for a DocumentIR.

    The process is as follows:

    1. Load the stitched DocumentIR and validate it against the KG config parameters.
    2. Build and persist `kg_run_manifest.json`.
    3. Plan source DocumentIR units for Academic Standards (SFI) extraction windows.
    4. Build LLM-ready extraction windows.
    5. Extract source-grounded SFI candidates from extraction windows using an LLM.
    6. Build and persist the global SFI candidate registry for merge review.
    7. Merge duplicate SFI registry candidates into merge groups.
    8. Mint deterministic final SFI records from merge groups.
    9. Resolve source-grounded final hasChild edges.
    10. Compile, validate, and write final Academic Standards KG export artifacts.
    11. Gate LC generation on the step-10 bundle (validation + unresolved items).
    12. Select eligible LC-source SFIs (profile allowlist or leaf default).

    LC steps 13-18 (requests, LLM decomposition, LC minting, supports edges,
    summary, AS+LC bundle merge) are wired in as they are built.

    Parameters
    ----------
    config
        KG creation configuration from the global runtime config.
    document_ir_fp
        Path to the stitched DocumentIR JSON.
    kg_dirs
        Directories for storing KG run artifacts.
    usage_tracker
        Tracker to accumulate token usage in the KG pipeline.

    Returns
    -------
    Path
        The path to the persisted `kg_run_manifest.json` artifact.
    """

    # 1.
    kg_run_inputs = load_and_validate_inputs(
        config=config, document_ir_fp=document_ir_fp, kg_dirs=kg_dirs
    )

    # 2.
    kg_run_manifest = build_run_manifest(kg_run_inputs)
    kg_run_manifest_fp = kg_dirs.root / "kg_run_manifest.json"
    write_to_json(fp=kg_run_manifest_fp, json_info=kg_run_manifest)

    # 3.
    plan_items = plan_extraction_windows(
        document_ir=kg_run_inputs.document_ir,
        kg_config=kg_run_inputs.kg_config,
        save_fp=kg_dirs.root / "extraction_window_plan.json",
    )

    # 4.
    extraction_windows = build_llm_extraction_windows(
        document_ir=kg_run_inputs.document_ir,
        kg_config=kg_run_inputs.kg_config,
        plan_items=plan_items,
        save_fp=kg_dirs.root / "extraction_windows.jsonl",
    )

    # 5.
    sfi_extraction_results = extract_sfi_candidates_from_windows(
        extraction_windows=extraction_windows,
        kg_config=kg_run_inputs.kg_config,
        overwrite=config.overwrite,
        save_fp=kg_dirs.root / "sfi_extraction_results.jsonl",
        summary_fp=kg_dirs.root / "sfi_extraction_summary.json",
        usage_tracker=usage_tracker,
    )

    # 6.
    sfi_candidate_registry = build_candidate_registry(
        extraction_windows=extraction_windows,
        kg_config=kg_run_inputs.kg_config,
        save_fp=kg_dirs.root / "sfi_candidate_registry.json",
        sfi_extraction_results=sfi_extraction_results,
    )

    # 7.
    sfi_merge_report = merge_sfi_candidates(
        kg_config=kg_run_inputs.kg_config,
        kg_dirs=kg_dirs,
        overwrite=config.overwrite,
        sfi_candidate_registry=sfi_candidate_registry,
        usage_tracker=usage_tracker,
    )

    # 8.
    sfi_final_records = mint_final_sfi_ids(
        document_ir=kg_run_inputs.document_ir,
        kg_config=kg_run_inputs.kg_config,
        kg_dirs=kg_dirs,
        sfi_candidate_registry=sfi_candidate_registry,
        sfi_merge_report=sfi_merge_report,
    )

    # 9.
    has_child_edges = resolve_has_child_edges(
        document_ir=kg_run_inputs.document_ir,
        kg_config=kg_run_inputs.kg_config,
        kg_dirs=kg_dirs,
        overwrite=config.overwrite,
        sfi_final_records=sfi_final_records,
        usage_tracker=usage_tracker,
    )

    # 10.
    final_bundle = compile_academic_standards_kg(
        document_ir=kg_run_inputs.document_ir,
        has_child_edges=has_child_edges,
        kg_config=kg_run_inputs.kg_config,
        kg_dirs=kg_dirs,
        overwrite=config.overwrite,
    )

    logger.success(
        f"Final Academic Standards KG export count: "
        f"frameworks={final_bundle.summary.framework_count}; "
        f"items={final_bundle.summary.final_sfi_count}; "
        f"hasChild_relationships={final_bundle.summary.has_child_relationship_count}"
    )

    # 11-12. Later LC steps (13-18) are wired in as they are built; the LLM
    # decomposition step (14) additionally requires reviewed per-curriculum
    # generation_instructions before it may run.
    select_lc_source_sfis(
        academic_standards_bundle=final_bundle,
        has_child_edges=has_child_edges,
        kg_dirs=kg_dirs,
        lc_config=kg_run_inputs.kg_config.learning_components,
        sfi_final_records=sfi_final_records,
    )

    return kg_run_manifest_fp


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
    """Create the initial KG run artifacts from the global runtime config.

    The process is as follows:

    1. Load the global run config and resolve KG, extraction, and stitching paths.
    2. Cross-check stitching run results.
    3. Persist KG run metadata.
    4. Create a usage tracker to accumulate token costs for extracting SFIs.
    5. Build the knowledge graphs.

    Parameters
    ----------
    config_fp
        The file path to the global runtime config file for the pipeline.

    Raises
    ------
    Exception
        If any error occurs during knowledge graph creation.
    ValueError
        If the runtime config does not contain a kgs section.
    FileNotFoundError
        If the required upstream extraction/stitching artifacts do not exist.
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
    document_ir_fp = cross_check_stitching_run(
        computed_doc_key=computed_doc_key, extraction_config=extraction_config
    )

    # 3.
    kg_results_dir = extraction_config.output_dir / computed_doc_key / "kgs"
    kg_dirs, kg_run = persist_kg_run(config=config, output_dir=kg_results_dir)

    # 4.
    usage_tracker = KGUsageTracker()

    try:
        logger.info(
            f"Starting KG creation using runtime config: {config_fp}; "
            f"DocumentIR: {document_ir_fp}; "
            f"KG config framework: {config.metadata.framework_title}"
        )

        # 4.
        kg_run_manifest_fp = build_kgs(
            config=config,
            document_ir_fp=document_ir_fp,
            kg_dirs=kg_dirs,
            usage_tracker=usage_tracker,
        )
        kg_run.extra["status"] = "success"

        logger.success(f"KG creation completed successfully: {kg_run_manifest_fp}")
    except Exception as e:  # pylint: disable=broad-except
        kg_run.extra["status"] = "error"
        kg_run.extra["error"] = {
            "message": str(e),
            "traceback": traceback.format_exc(limit=20),
            "type": e.__class__.__name__,
        }
        raise
    finally:
        kg_run.extra["usage"] = usage_tracker.to_dict()
        kg_run.completed_at = datetime.now(timezone.utc)
        write_to_json(fp=kg_dirs.root / "kg_run.json", json_info=kg_run)


if __name__ == "__main__":
    cli()
