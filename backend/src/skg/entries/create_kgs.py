"""This module contains the entry point for exporting Learning Commons knowledge graphs
from a canonical IR JSON file. This is step 5.

Step 5 does the following:

1. Builds the knowledge graph export context.
2. Exports academic standards to the knowledge graphs.
3. Exports Learning Components KG and writes combined Standards + Learning Components
    graph bundle.
4. Optionally exports Learning Progressions KG and writes combined Standards +
    Learning Components + Learning Progressions graph bundle.
5. Builds reporting and validation artifacts, writes to disk, and logs console summary.

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
from skg.canonical_ir.schemas import CanonicalIR
from skg.kgs.export_academic_standards import load_or_export_academic_standards
from skg.kgs.export_learning_components import load_or_export_learning_components
from skg.kgs.export_learning_progressions import load_or_export_learning_progressions
from skg.kgs.reporting import (
    build_entity_provenance_export,
    build_policy_coverage_report,
    log_console_summary,
    validate_graph,
    write_reports,
)
from skg.kgs.utils import (
    KGDirs,
    build_kg_export_context,
    cross_check_canonical_ir_run,
    get_page_image_dims,
    merge_graph_bundles,
    persist_kg_run,
)
from skg.schemas import CreateKGConfig, RunConfig, RunCtx
from skg.utils.general import open_json_type, write_to_json
from skg.utils.pdf import compute_doc_key

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


def create_kgs(
    *,
    canonical_ir: CanonicalIR,
    config: CreateKGConfig,
    kg_dirs: KGDirs,
    provenance_context: dict | None = None,
) -> None:
    """Create Learning Commons knowledge graphs from a single CanonicalIR.

    Each export phase checks whether its sentinel bundle file already exists on disk.
    When `config.overwrite` is `False` and the sentinel exists, the prior export is
    loaded from disk instead of being re-generated. This enables cheap incremental
    re-runs: for example, re-running only Learning Progressions after tuning thresholds
    while reusing the (expensive) Academic Standards and Learning Components exports.

    The process is as follows:

    1. Build the knowledge graph export context.
    2. Export (or load) academic standards.
    3. Export (or load) Learning Components KG and write combined Standards + Learning
        Components graph bundle.
    4. Optionally export (or load) Learning Progressions KG and write combined
        Standards + Learning Components + Learning Progressions graph bundle.
    5. Build reporting and validation artifacts, write to disk, and log console summary.

    Parameters
    ----------
    canonical_ir
        The CanonicalIR object loaded from the canonical IR JSON file.
    config
        The knowledge graph run configuration.
    kg_dirs
        The knowledge graph run directories.
    provenance_context
        An optional dictionary containing provenance context information to be included
        in the knowledge graphs.

    Raises
    ------
    ValueError
        If the CanonicalIR JSON is invalid or if any part of the knowledge graph export
        process fails.
    """

    # 1.
    kg_export_ctx = build_kg_export_context(canonical_ir=canonical_ir, config=config)

    # 2.
    academic_standards, as_reused = load_or_export_academic_standards(
        canonical_ir_created_at=canonical_ir.created_at,
        config=config,
        ctx=kg_export_ctx,
        decision_set_id=canonical_ir.decision_set_id,
        kg_dirs=kg_dirs,
        provenance_context=provenance_context,
    )

    # 3.
    learning_components, lc_reused = load_or_export_learning_components(
        academic_standards=academic_standards,
        config=config,
        ctx=kg_export_ctx,
        kg_dirs=kg_dirs,
    )

    # Sentinels needed for combined bundles.
    as_sentinel = kg_dirs.academic_standards / "academic_standards_kg.json"
    lc_sentinel = kg_dirs.learning_components / "learning_components_kg.json"

    # Combined Academic Standards + Learning Components bundle.
    combined_as_lc_fp = (
        kg_dirs.combined / "academic_standards_plus_learning_components_kg.json"
    )

    if combined_as_lc_fp.exists() and as_reused and lc_reused:
        logger.info(
            "Combined Academic Standards and Learning Components bundle already exists "
            "(both components reused)--skipping."
        )
    else:
        academic_bundle = open_json_type(as_sentinel)
        lc_bundle = open_json_type(lc_sentinel)
        combined_bundle = merge_graph_bundles(
            bundles=[academic_bundle, lc_bundle],
            doc_key=kg_export_ctx.doc_key,
            export_dialect=config.as_export_dialect,
        )
        write_to_json(fp=combined_as_lc_fp, json_info=combined_bundle)

    # 4.
    learning_progressions = None

    if config.generate_progressions is True:
        learning_progressions, lp_reused = load_or_export_learning_progressions(
            academic_standards=academic_standards,
            config=config,
            ctx=kg_export_ctx,
            kg_dirs=kg_dirs,
        )

        # Combined Academic Standards + Learning Components + Learning Progressions
        # bundle.
        combined_all_fp = (
            kg_dirs.combined
            / "academic_standards_plus_learning_components_plus_learning_progressions_kg.json"
        )

        if combined_all_fp.exists() and as_reused and lc_reused and lp_reused:
            logger.info(
                "Combined AS+LC+LP bundle already exists (all components reused)---skipping."
            )
        else:
            academic_bundle = open_json_type(as_sentinel)
            lc_bundle = open_json_type(lc_sentinel)
            lp_bundle = open_json_type(
                kg_dirs.learning_progressions / "learning_progressions_kg.json"
            )
            combined_bundle = merge_graph_bundles(
                bundles=[academic_bundle, lc_bundle, lp_bundle],
                doc_key=kg_export_ctx.doc_key,
                export_dialect=config.as_export_dialect,
            )
            write_to_json(fp=combined_all_fp, json_info=combined_bundle)

    # 5.
    policy_report = build_policy_coverage_report(
        academic_standards=academic_standards,
        ctx=kg_export_ctx,
        learning_components=learning_components,
        learning_progressions=learning_progressions,
    )
    entity_provenance = build_entity_provenance_export(
        academic_standards=academic_standards,
        ctx=kg_export_ctx,
        learning_components=learning_components,
    )
    validation_report = validate_graph(
        academic_standards=academic_standards,
        ctx=kg_export_ctx,
        learning_components=learning_components,
        learning_progressions=learning_progressions,
    )
    write_reports(
        entity_provenance=entity_provenance,
        kg_dirs=kg_dirs,
        policy_report=policy_report,
        validation_report=validation_report,
    )
    log_console_summary(
        policy_report=policy_report, validation_report=validation_report
    )

    if validation_report.has_errors():
        raise ValueError(
            f"Graph validation failed with {len(validation_report.errors())} error(s). "
            f"See graph_validation_report.json for details."
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
    """Create LC KGs from the CanonicalIR JSON.

    The process is as follows:

    1. Load config and validate extraction run existence.
    2. Cross-check canonical IR run results.
    3. Persist KG creation run metadata.
    4. Create Learning Commons knowledge graphs.

    Parameters
    ----------
    config_fp
        The file path to the global config file for the pipeline.

    Raises
    ------
    Exception
        If any part of the knowledge graph creation process fails.
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
        / "canonical_ir"
        / "canonical_ir.json"
    )
    extraction_run_config = RunCtx.model_validate(
        open_json_type(extraction_run_results_dir / "extraction_run.json")
    )
    expected_doc_key = extraction_run_config.extra["doc_key"]

    # 2.
    canonical_ir = cross_check_canonical_ir_run(
        canonical_ir_fp=canonical_ir_fp,
        computed_doc_key=computed_doc_key,
        expected_doc_key=expected_doc_key,
        extraction_config=extraction_config,
        kg_config=config,
    )

    # 3.
    kg_results_dir = extraction_config.output_dir / expected_doc_key / "kgs"
    kg_dirs, kg_run = persist_kg_run(config=config, output_dir=kg_results_dir)

    try:
        # 4.
        logger.info(
            f"Starting KG creation process using canonical IR JSON: {canonical_ir_fp}"
        )

        create_kgs(
            canonical_ir=canonical_ir,
            config=config,
            kg_dirs=kg_dirs,
            provenance_context={
                "bbox": {
                    "coord_space": "px",
                    "format": "[x0, y0, x1, y1]",
                    "note": (
                        "BBox coords are absolute pixels in rendered page images. "
                        "Use page_index+width_px/height_px for normalization when available."
                    ),
                    "origin": "top_left",
                    "page_images": get_page_image_dims(extraction_run_results_dir),
                    "render_dpi": extraction_config.dpi,
                }
            },
        )
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
