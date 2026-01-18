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
from skg.canonical_ir.llm import generate_segment_decision
from skg.canonical_ir.utils import (
    CanonicalIRDirs,
    decision_key,
    load_or_initialize_segment_decision_set,
    make_table_chunk_payload,
    persist_canonical_run,
    save_segment_decision_set,
    table_chunks_for_segment,
)
from skg.canonical_ir.validators import validate_table_chunk_coverage_and_overlap
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

    canonical_ir_fp = creation_dirs.root / "canonical_ir.json"

    if not config.overwrite and canonical_ir_fp.exists():
        logger.warning(
            f"Canonical IR JSON already exists at {canonical_ir_fp}. Skipping creation. "
            f"If you wish to overwrite, pass the --overwrite flag."
        )
        return

    # Validate and load the Document IR.
    document_ir = DocumentIR.model_validate(open_json_type(document_ir_fp))

    # Load or initialize decision set.
    decision_set, segment_decisions_fp = load_or_initialize_segment_decision_set(
        creation_dirs=creation_dirs,
        doc_key=doc_key,
        document_ir=document_ir,
        segment_decisions_fp=config.segment_decisions_fp,
    )

    # Generate decisions for any undecided segments in DocumentIR order.
    num_segments = len(document_ir.segments)
    existing_keys = {decision_key(d) for d in decision_set.decisions}

    for i, segment in enumerate(document_ir.segments, 1):
        logger.info(f"Processing segment ({segment.segment_id}): {i}/{num_segments}")

        # Block segments: always 1 decision (unchunked).
        if segment.kind == "block":
            key = (segment.segment_id, None, None)

            if key in existing_keys:
                continue

            segment_decision = generate_segment_decision(
                doc_key=doc_key,
                force_llm_retry_on_first_attempt=config.force_retry_on_first_attempt,
                model=config.model,
                segment=segment,
            )

            decision_set.decisions.append(segment_decision)
            existing_keys.add(key)

            # Persist checkpoint after every decision.
            decision_set = save_segment_decision_set(
                decision_set=decision_set, segment_decisions_fp=segment_decisions_fp
            )
            continue

        # Table segments: chunk only if needed. If an unchunked table decision already
        # exists, do NOT mix chunked + unchunked.
        unchunked_key = (segment.segment_id, None, None)
        if unchunked_key in existing_keys:
            continue

        chunks = table_chunks_for_segment(
            max_body_rows=config.max_table_rows_per_decision, segment=segment
        )

        # Unchunked table == 1 decision.
        if len(chunks) == 1 and chunks[0] == (None, None):
            key = unchunked_key

            if key in existing_keys:
                continue

            segment_decision = generate_segment_decision(
                doc_key=doc_key,
                force_llm_retry_on_first_attempt=config.force_retry_on_first_attempt,
                model=config.model,
                segment=segment,
            )

            decision_set.decisions.append(segment_decision)
            existing_keys.add(key)

            decision_set = save_segment_decision_set(
                decision_set=decision_set, segment_decisions_fp=segment_decisions_fp
            )
            continue

        # Chunked table == N decisions.
        for start, end in chunks:
            key = (segment.segment_id, start, end)

            if key in existing_keys:
                continue

            payload = make_table_chunk_payload(end=end, segment=segment, start=start)

            segment_decision = generate_segment_decision(
                doc_key=doc_key,
                force_llm_retry_on_first_attempt=config.force_retry_on_first_attempt,
                model=config.model,
                row_range_end=end,
                row_range_start=start,
                segment=segment,
                segment_payload=payload,
            )

            decision_set.decisions.append(segment_decision)
            existing_keys.add(key)

            decision_set = save_segment_decision_set(
                decision_set=decision_set, segment_decisions_fp=segment_decisions_fp
            )

        logger.success(
            f"Finished processing segment ({segment.segment_id}): {i}/{num_segments}!"
        )

    decided_segment_ids = {d.segment_id for d in decision_set.decisions if d.segment_id}
    logger.info(
        f"Segment decision set generation complete: "
        f"{len(decided_segment_ids)}/{len(document_ir.segments)} segments have at least one decision "
        f"({len(decision_set.decisions)} decisions total)."
    )

    # Parse the document IR into a canonical IR.

    # Write results to file.
    # save_canonical_ir(canonical_ir=canonical_ir, canonical_ir_fp=canonical_ir_fp)


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
