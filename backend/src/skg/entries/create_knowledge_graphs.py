"""This module contains the entry point for exporting a *shape-only* Learning Commons
KG from CanonicalIR. This is step 5.

Step 5 does the following:

1. Persist KG creation run metadata.
2. Check for existing outputs; skip if present and not overwriting.
3. Load KG config.
4. Create knowledge graphs:
    4a. Deterministically filter CanonicalIR nodes/edges.
    4b. Map CanonicalIR nodes to StandardsFramework and StandardsFrameworkItems.
    4c. Rebuild hasChild relationships with deterministic tree policy.
    4d. Optionally generate LearningComponents and supports relationships.
5. Write:
    - knowledge_graph.json
    - graph_stats.json
    - graph_validation.json
6. Persist KG creation run metadata.

Invoke from the backend directory via:

python src/skg/entries/create_knowledge_graphs.py /path/to/kg_config.json /path/to/canonical_ir_run_results
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
from skg.kgs.schemas import (
    GraphValidationReport,
    KnowledgeGraphConfig,
    KnowledgeGraphExport,
)
from skg.kgs.utils import (
    DefaultsResolver,
    DeterministicIdRegistry,
    KGDirs,
    build_graph_stats,
    build_has_child_relationships,
    build_index,
    build_learning_components,
    filter_canonical_ir,
    map_canonical_to_entities,
    merge_validation_reports,
    persist_kg_run,
    sort_export_lists_in_place,
)
from skg.utils.general import open_json_type, write_to_json

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


def create_knowledge_graphs(
    *, canonical_ir_fp: Path, kg_config: KnowledgeGraphConfig, kg_dirs: KGDirs
) -> dict[str, Path]:
    """Create (shape-only) Learning Commons knowledge graphs from CanonicalIR JSON.

    Parameters
    ----------
    canonical_ir_fp
        File path to the CanonicalIR JSON.
    kg_config
        Knowledge graph configuration.
    kg_dirs
        Knowledge graph creation directories.

    Returns
    -------
    dict[str, Path]
        File paths to created knowledge graph artifacts.
    """

    # Load, validate, and index canonical IR JSON.
    canonical_ir_index = build_index(
        canonical_ir=CanonicalIR.model_validate(open_json_type(canonical_ir_fp))
    )

    # Filter canonical IR according to KG config.
    filtered = filter_canonical_ir(config=kg_config, index=canonical_ir_index)
    base_report = filtered.report.model_copy(deep=True)

    # Shared wiring.
    defaults = DefaultsResolver(config=kg_config)
    ids = DeterministicIdRegistry(config=kg_config, doc_key=filtered.canonical.doc_key)

    # Map CanonicalNode to entities (framework and SFIs).
    mapping = map_canonical_to_entities(
        config=kg_config, defaults=defaults, filtered=filtered, ids=ids
    )
    report: GraphValidationReport = merge_validation_reports(
        base=base_report, other=mapping.report
    )

    # Build hasChild relationships (tree policy enforced).
    hierarchy = build_has_child_relationships(
        defaults=defaults,
        filtered=filtered,
        framework=mapping.framework,
        ids=ids,
        sfi_uuid_by_canonical_id=mapping.sfi_uuid_by_canonical_id,
        sfis=mapping.standards_framework_items,
    )
    report = merge_validation_reports(base=report, other=hierarchy.report)

    # Build LearningComponents and supports relationships.
    lc = build_learning_components(
        config=kg_config,
        defaults=defaults,
        ids=ids,
        report=report,
        sfis=mapping.standards_framework_items,
    )
    report = lc.report

    # Assemble final export and deterministic ordering.
    export = KnowledgeGraphExport(
        exportDialect="shape_only",
        frameworks=[mapping.framework],
        learningComponents=lc.learning_components,
        relationships=[*hierarchy.relationships, *lc.supports_relationships],
        standardsFrameworkItems=mapping.standards_framework_items,
    )
    sort_export_lists_in_place(export)

    # Write results to file.
    kg_fp = kg_dirs.root / "knowledge_graph.json"
    write_to_json(
        fp=kg_fp,
        json_info=export.model_dump(by_alias=True, exclude_none=True, mode="json"),
    )

    graph_validation_fp = kg_dirs.root / "graph_validation.json"
    write_to_json(
        fp=graph_validation_fp,
        json_info=report.model_dump(by_alias=True, exclude_none=True, mode="json"),
    )

    stats = build_graph_stats(
        canonical_doc_key=filtered.canonical.doc_key, export=export, report=report
    )

    graph_stats_fp = kg_dirs.root / "graph_stats.json"
    write_to_json(fp=graph_stats_fp, json_info=stats)

    return {
        "graph_stats": graph_stats_fp,
        "graph_validation": graph_validation_fp,
        "knowledge_graph": kg_fp,
    }


@cli.command()
def create_kgs(
    kg_config_fp: Path = typer.Argument(
        ...,
        dir_okay=False,
        exists=True,
        file_okay=True,
        help="File path to kg_config.json.",
        readable=True,
        resolve_path=True,
    ),
    canonical_ir_run_results_dir: Path = typer.Argument(
        ...,
        dir_okay=True,
        exists=True,
        file_okay=False,
        help="The canonical IR run results directory.",
        resolve_path=True,
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Overwrite existing output files if present."
    ),
) -> None:
    """Create (shape-only) LC KGs from the CanonicalIR JSON.

    The process is as follows:

    1. Persist KG creation run metadata.
    2. Check for existing outputs; skip if present and not overwriting.
    3. Load KG config.
    4. Create knowledge graphs:
        4a. Deterministically filter CanonicalIR nodes/edges.
        4b. Map CanonicalIR nodes to StandardsFramework and StandardsFrameworkItems.
        4c. Rebuild hasChild relationships with deterministic tree policy.
        4d. Optionally generate LearningComponents and supports relationships.
    5. Write:
        - knowledge_graph.json
        - graph_stats.json
        - graph_validation.json
    6. Persist KG creation run metadata.

    Parameters
    ----------
    kg_config_fp
        File path to a custom KG config JSON.
    canonical_ir_run_results_dir
        Directory containing the canonical IR run results.
    overwrite
        Whether to overwrite existing output files if present.
    """

    canonical_ir_run_results_dir = canonical_ir_run_results_dir.resolve()
    canonical_ir_fp = canonical_ir_run_results_dir / "canonical_ir.json"
    creation_config_fp = canonical_ir_run_results_dir / "creation_run.json"
    creation_run_config = open_json_type(creation_config_fp)
    kg_results_dir = canonical_ir_run_results_dir.parent / "kgs"

    # 1.
    kg_dirs, kg_run = persist_kg_run(output_dir=kg_results_dir, **creation_run_config)

    # 2.
    expected_outputs = [
        kg_dirs.root / "knowledge_graph.json",
        kg_dirs.root / "graph_stats.json",
        kg_dirs.root / "graph_validation.json",
    ]
    if not overwrite and [p for p in expected_outputs if p.exists()]:
        logger.warning(
            "One or more KG outputs already exist. Skipping KG creation. If you wish "
            "to overwrite, pass the --overwrite flag."
        )
        return

    logger.info(
        f"Starting KG creation process using canonical IR JSON: {canonical_ir_fp}"
    )
    logger.info(f"Loaded creation run config: {creation_config_fp}")
    logger.info(f"Saving KG results to: {kg_results_dir}")

    try:
        # 3.
        config_dict = open_json_type(kg_config_fp)
        kg_config = KnowledgeGraphConfig.model_validate(config_dict)

        # 4.
        kg_outputs = create_knowledge_graphs(
            canonical_ir_fp=canonical_ir_fp, kg_config=kg_config, kg_dirs=kg_dirs
        )
        kg_run.status = "success"

        # 5.
        kg_run.outputs = {k: str(v) for k, v in kg_outputs.items()}
        logger.success("KG creation completed successfully!")
    except Exception as e:  # pylint: disable=broad-except
        kg_run.status = "error"
        kg_run.error = {
            "message": str(e),
            "traceback": traceback.format_exc(limit=30),
            "type": e.__class__.__name__,
        }
        raise
    finally:
        # 6.
        kg_run.completed_at = datetime.now(timezone.utc)
        write_to_json(fp=kg_dirs.root / "kg_run.json", json_info=kg_run)


if __name__ == "__main__":
    cli()
