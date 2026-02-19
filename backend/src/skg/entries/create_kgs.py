"""This module contains the entry point for exporting Learning Commons knowledge graphs
from a canonical IR JSON file. This is step 5.

Step 5 does the following:

1. Loads the canonical IR JSON and validates it.
2. Builds the knowledge graph export context.
3. Exports academic standards to the knowledge graphs.
4. Exports Learning Components KG and writes combined Standards + Learning Components
    graph bundle.
5. Optionally exports Learning Progressions KG and writes combined Standards +
    Learning Components + Learning Progressions graph bundle.
6. Builds reporting and validation artifacts, writes to disk, and logs console summary.

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
from skg.kgs.export_academic_standards import export_academic_standards
from skg.kgs.export_learning_components import export_learning_components
from skg.kgs.export_learning_progressions import export_learning_progressions
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
    canonical_ir_fp: Path,
    config: CreateKGConfig,
    kg_dirs: KGDirs,
    provenance_context: dict | None = None,
) -> None:
    """Create Learning Commons knowledge graphs from a single CanonicalIR JSON.

    The process is as follows:

    1. Load the CanonicalIR JSON and validate it.
    2. Build the knowledge graph export context.
    3. Export academic standards to the knowledge graphs.
    4. Export Learning Components KG and write combined Standards + Learning Components
        graph bundle.
    5. Optionally export Learning Progressions KG and write combined Standards +
        Learning Components + Learning Progressions graph bundle.
    6. Build reporting and validation artifacts, write to disk, and log console summary.

    Parameters
    ----------
    canonical_ir_fp
        The file path to the CanonicalIR JSON.
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
    canonical_ir = CanonicalIR.model_validate(open_json_type(canonical_ir_fp))

    # 2.
    kg_export_ctx = build_kg_export_context(canonical_ir=canonical_ir, config=config)

    # 3.
    academic_standards = export_academic_standards(
        canonical_ir_created_at=canonical_ir.created_at,
        config=config,
        ctx=kg_export_ctx,
        decision_set_id=canonical_ir.decision_set_id,
        kg_dirs=kg_dirs,
        provenance_context=provenance_context,
    )
    logger.info(
        f"Exported Academic Standards KG: "
        f"{len(academic_standards.items)} items, "
        f"{len(academic_standards.relationships)} `hasChild` relationships"
    )

    # 4.
    learning_components = export_learning_components(
        academic_standards=academic_standards,
        config=config,
        ctx=kg_export_ctx,
        kg_dirs=kg_dirs,
    )

    logger.info(
        f"Exported Learning Components KG: "
        f"{len(learning_components.learning_components)} components, "
        f"{len(learning_components.supports_relationships)} `supports` relationships"
    )

    academic_bundle = open_json_type(
        kg_dirs.academic_standards / "academic_standards_kg.json"
    )
    lc_bundle = open_json_type(
        kg_dirs.learning_components / "learning_components_kg.json"
    )
    combined_bundle = merge_graph_bundles(
        bundles=[academic_bundle, lc_bundle],
        doc_key=kg_export_ctx.doc_key,
        export_dialect=str(config.export_dialect),
    )
    write_to_json(
        fp=kg_dirs.combined / "academic_standards_plus_learning_components_kg.json",
        json_info=combined_bundle,
    )

    # 5.
    learning_progressions = None

    if config.generate_progressions is True:
        learning_progressions = export_learning_progressions(
            academic_standards=academic_standards,
            config=config,
            ctx=kg_export_ctx,
            kg_dirs=kg_dirs,
        )

        logger.info(
            f"Exported Learning Progressions KG: "
            f"{len(learning_progressions.builds_towards_relationships)} `buildsTowards` relationships, "
            f"{len(learning_progressions.relates_to_relationships)} `relatesTo` relationships"
        )

        lp_bundle = open_json_type(
            kg_dirs.learning_progressions / "learning_progressions_kg.json"
        )
        combined_bundle = merge_graph_bundles(
            bundles=[academic_bundle, lc_bundle, lp_bundle],
            doc_key=kg_export_ctx.doc_key,
            export_dialect=str(config.export_dialect),
        )
        write_to_json(
            fp=kg_dirs.combined
            / "academic_standards_plus_learning_components_plus_learning_progressions_kg.json",
            json_info=combined_bundle,
        )

    # 6.
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
        policy_report=policy_report,
        validation_report=validation_report,
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
    2. Check doc_key consistency.
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
        / "canonical_ir"
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

        create_kgs(
            canonical_ir_fp=canonical_ir_fp,
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
