"""This module contains the entry point for converting the DocumentIR JSON (layout)
from step 3 into CanonicalIR (semantic). This is step 4.

Step 4 does the following:

1. Loads the DocumentIR JSON from the stitching run results directory.
2. Loads the SegmentDecisionSet JSON (LLM-produced, persisted, replayable).
3. Deterministically compiles a CanonicalIR from (DocumentIR + SegmentDecisionSet).
4. Exports the CanonicalIR JSON to the canonical IR creation results directory.

Invoke from the backend directory via:

python src/skg/entries/create_canonical_ir.py ../examples/tanzania/config.json
"""

# Standard Library
import sys
import traceback

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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
from skg.canonical_ir.llm import generate_grouping_canonicalization_map
from skg.canonical_ir.schemas import SegmentDecisionSet, compute_decision_set_id
from skg.canonical_ir.segment_decisions import (
    load_or_build_caption_bindings,
    process_segment_decisions,
    segment_hint,
)
from skg.canonical_ir.spine_corrector import apply_spine_policy_to_decision_set
from skg.canonical_ir.utils import (
    CanonicalIRDirs,
    apply_grouping_canonicalization_map,
    build_context_hint_from_decision,
    clean_up_segment_decisions,
    collect_unique_grouping_keys,
    compile_canonical_ir,
    load_segment_decision_set,
    persist_canonical_run,
    save_canonical_ir,
)
from skg.canonical_ir.validators import validate_table_chunk_coverage_and_overlap
from skg.document_ir.schemas import DocumentIR
from skg.schemas import CreateCanonicalConfig, RunConfig, RunCtx
from skg.utils.constants import SegmentDecisionType
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

    1. Validate and load the Document IR.
    2. Build or load one-shot caption to next table bindings.
    3. Initialize decision set.
    4. Generate decisions for any undecided segments in DocumentIR order.
    5. Update context hint using the most recently-added decision.
    6. Persist warnings for this segment.
    7. Load the raw segment decisions.
    8. Clean up segment decisions before LLM canonicalization.
    9. Apply spine correction deterministically.
    10. Collect unique grouping keys for validation later.
    11. Generate and apply grouping canonicalization map.
    12. Apply grouping canonicalization map to segment decisions.
    13. Decision-set level validation for chunked tables.
    14. Parse the segment decisions into a canonical IR.
    15. Write results to file.

    Parameters
    ----------
    config
        The canonical IR creation run configuration.
    doc_key
        The expected document key for all page IRs.
    creation_dirs
        The canonical IR creation directories.
    document_ir_fp
        The file path to the DocumentIR JSON.
    """

    canonical_ir_fp = creation_dirs.canonical_ir / "canonical_ir.json"
    segment_decisions_fp = creation_dirs.segment_decisions / "segment_decisions.json"

    if not config.overwrite and canonical_ir_fp.exists():
        logger.warning(
            f"Canonical IR JSON already exists at {canonical_ir_fp}. Skipping creation. "
            f"If you wish to overwrite, pass the --overwrite flag."
        )
        return

    # 1.
    document_ir = DocumentIR.model_validate(open_json_type(document_ir_fp))

    # 2.
    caption_bindings = load_or_build_caption_bindings(
        creation_dirs=creation_dirs, document_ir=document_ir, overwrite=config.overwrite
    )

    if not config.overwrite and segment_decisions_fp.exists():
        logger.warning(
            f"Segment decisions JSON already exists at {segment_decisions_fp}. "
            f"Reusing existing segment decisions. "
            f"If you wish to overwrite, pass the --overwrite flag."
        )
    else:
        # 3.
        decision_set = SegmentDecisionSet.model_validate(
            {
                "decision_set_id": compute_decision_set_id(decisions=[]),
                "decisions": [],
                "doc_key": doc_key,
                "generator": config.model,
                "pdf_name": document_ir.pdf_name,
            }
        )

        # 4.
        context_hint: list[dict[str, Any]] = []
        existing_keys: set[tuple[str, Optional[int], Optional[int]]] = set()
        num_segments = len(document_ir.segments)
        segment_warnings_by_segment: dict[str, list[str]] = {}
        segment_warnings_fp = creation_dirs.segment_decisions / "segment_warnings.json"

        for i, segment in enumerate(document_ir.segments, 1):
            logger.info(
                f"Processing segment ({segment.segment_id}): {i}/{num_segments}"
            )

            prev_number_decisions = len(decision_set.decisions)
            warnings: list[str] = []

            prev_seg = document_ir.segments[i - 2] if i > 1 else None
            next_seg = document_ir.segments[i] if i < num_segments else None
            prev_seg_hint = segment_hint(prev_seg) if prev_seg else None
            next_seg_hint = segment_hint(next_seg) if next_seg else None

            decision_set = process_segment_decisions(
                caption_bindings=caption_bindings,
                config=config,
                context_hint=context_hint,
                decision_set=decision_set,
                doc_key=doc_key,
                existing_keys=existing_keys,
                next_segment_hint=next_seg_hint,
                prev_segment_hint=prev_seg_hint,
                segment=segment,
                segment_decisions_fp=segment_decisions_fp,
                warnings=warnings,
            )
            new_decisions = decision_set.decisions[prev_number_decisions:]

            # 5.
            if new_decisions:
                last = new_decisions[-1]
                if last.decision_type in {
                    SegmentDecisionType.EMIT_GROUPINGS_AND_LEAVES,
                    SegmentDecisionType.EMIT_GROUPINGS_ONLY,
                    SegmentDecisionType.EMIT_LEAVES_ONLY,
                }:
                    context_hint = build_context_hint_from_decision(last)

            # 6.
            segment_key = f"{i:05d}_{segment.segment_id}"
            segment_warnings_by_segment[segment_key] = warnings

            logger.success(
                f"Finished processing segment ({segment.segment_id}): {i}/{num_segments}!"
            )

        write_to_json(fp=segment_warnings_fp, json_info=segment_warnings_by_segment)
        logger.info(f"Saved segment warnings to: {segment_warnings_fp}")

        decided_segment_ids = {
            d.segment_id for d in decision_set.decisions if d.segment_id
        }
        logger.info(
            f"Segment decision set generation complete: "
            f"{len(decided_segment_ids)}/{len(document_ir.segments)} "
            f"segments have at least one decision "
            f"({len(decision_set.decisions)} decisions total)."
        )

    # 7.
    segment_decisions = load_segment_decision_set(
        expected_doc_key=doc_key,
        pdf_name=document_ir.pdf_name,
        segment_decisions_fp=segment_decisions_fp,
    )

    # 8.
    segment_decisions = clean_up_segment_decisions(
        creation_dirs=creation_dirs,
        overwrite=config.overwrite,
        segment_decisions=segment_decisions,
    )

    # 9.
    segment_decisions = apply_spine_policy_to_decision_set(
        caption_bindings=caption_bindings,
        creation_dirs=creation_dirs,
        document_ir=document_ir,
        decision_set=segment_decisions,
        overwrite=config.overwrite,
        spine=config.spine_policy,
    )

    # 10.
    grouping_keys = collect_unique_grouping_keys(
        creation_dirs=creation_dirs,
        overwrite=config.overwrite,
        segment_decisions=segment_decisions,
    )

    # 11.
    mapping = generate_grouping_canonicalization_map(
        creation_dirs=creation_dirs,
        doc_key=doc_key,
        grouping_keys=grouping_keys,
        model=config.model,
        overwrite=config.overwrite,
    )

    # 12.
    segment_decisions = apply_grouping_canonicalization_map(
        canonical_grouping_min_confidence=config.canonical_grouping_min_confidence,
        canonicalization_skip_roles=config.canonicalization_skip_roles,
        creation_dirs=creation_dirs,
        mapping=mapping,
        overwrite=config.overwrite,
        segment_decisions=segment_decisions,
    )

    # 13.
    validate_table_chunk_coverage_and_overlap(
        document_ir=document_ir, segment_decisions=segment_decisions
    )

    # 14.
    canonical_ir = compile_canonical_ir(
        doc_key=doc_key,
        document_ir=document_ir,
        segment_decision_conf_threshold=config.segment_decision_conf_threshold,
        segment_decisions=segment_decisions,
        structural_leaf_warn_threshold=config.structural_leaf_warn_threshold,
    )

    # 15.
    save_canonical_ir(
        canonical_ir=canonical_ir,
        canonical_ir_fp=canonical_ir_fp,
        segment_decision_conf_threshold=config.segment_decision_conf_threshold,
        structural_leaf_warn_threshold=config.structural_leaf_warn_threshold,
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
            f"Starting canonical IR creation process using document IR JSON: {document_ir_fp}"
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
