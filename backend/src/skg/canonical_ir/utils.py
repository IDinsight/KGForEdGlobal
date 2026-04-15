"""This module contains utility functions for canonical Intermediate Representations."""

# Standard Library
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Third Party Library
from loguru import logger

# Package Library
from skg.document_ir.schemas import BlockSegment, DocumentIR
from skg.page_ir_extraction.schemas import TextUnit
from skg.schemas import CreateCanonicalConfig, ExtractionConfig, RunCtx
from skg.utils.general import make_dir, open_json_type, write_to_json


@dataclass(frozen=True)
class CanonicalIRDirs:
    """Dataclass for canonical IR directories."""

    root: Path
    canonical_ir: Path
    caption_binding: Path
    segment_decisions: Path


def create_canonical_ir_dirs(*, output_dir: Path) -> CanonicalIRDirs:
    """Create canonical IR directories for a given creation run.

    Parameters
    ----------
    output_dir
        The output directory root.

    Returns
    -------
    CanonicalIRDirs
        The created canonical IR directories.
    """

    root = output_dir
    canonical_ir = root / "canonical_ir"
    caption_binding = root / "caption_binding"
    segment_decisions = root / "segment_decisions"

    for p in [root, canonical_ir, caption_binding, segment_decisions]:
        make_dir(p)

    return CanonicalIRDirs(
        root=root,
        canonical_ir=canonical_ir,
        caption_binding=caption_binding,
        segment_decisions=segment_decisions,
    )


def cross_check_stitching_run(
    *,
    canonical_ir_config: CreateCanonicalConfig | None,
    computed_doc_key: str,
    expected_doc_key: str,
    extraction_config: ExtractionConfig,
    document_ir_fp: Path,
) -> DocumentIR:
    """Cross-check that the stitching run matches expected parameters and load the
    document IR for the canonical IR creation run.

    Parameters
    ----------
    canonical_ir_config
        The canonical IR configuration used for the run.
    computed_doc_key
        The document key computed from the source PDF bytes by the caller.
    expected_doc_key
        The expected document key (hex string) from the extraction run metadata.
    extraction_config
        The extraction configuration used for the run.
    document_ir_fp
        The file path to the DocumentIR JSON to load.

    Returns
    -------
    DocumentIR
        The loaded DocumentIR for the canonical IR creation run.

    Raises
    ------
    ValueError
        If canonical_ir_config is not provided.
        If the computed `doc_key` from the PDF does not match the `doc_key` in the
            stitching run metadata.
    """

    if not canonical_ir_config:
        raise ValueError("Canonical IR config is required")

    if computed_doc_key != expected_doc_key:
        raise ValueError(
            f"PDF doc_key mismatch.\n"
            f"  PDF provided to create_canonical_ir():  {extraction_config.pdf_fp}\n"
            f"  computed doc_key:                       {computed_doc_key}\n"
            f"  extraction_run.json key:                {expected_doc_key}\n"
            f"You are likely creating a canonical IR against a different PDF than the "
            f"one used for stitching. Pass the same PDF used in the stitching run or "
            f"re-run stitching."
        )

    return DocumentIR.model_validate(open_json_type(document_ir_fp))


def extract_block_segment_text(segment: BlockSegment) -> str | None:
    """Extract text from a BlockSegment.

    Parameters
    ----------
    segment
        The BlockSegment to extract text from.

    Returns
    -------
    str | None
        The extracted text, or None if not found.
    """

    if segment.combined_text and segment.combined_text.strip():
        return segment.combined_text.strip()

    if isinstance(segment.text, TextUnit) and segment.text.text.strip():
        return segment.text.text.strip()

    if segment.list_items:
        parts: list[str] = []

        for list_item in segment.list_items:
            text_unit = list_item.text

            if text_unit.text.strip():
                parts.append(text_unit.text.strip())

        if parts:
            return "\n".join(parts)

    return None


def persist_canonical_run(
    *, config: CreateCanonicalConfig, output_dir: Path
) -> tuple[CanonicalIRDirs, RunCtx]:
    """Persist canonical IR creation run metadata.

    Parameters
    ----------
    config
        The canonical IR creation run configuration.
    output_dir
        The output directory for the canonical IR creation run results.

    Returns
    -------
    tuple[CanonicalIRDirs, RunCtx]
        The created canonical IR directories and persisted canonical IR creation run
        metadata.
    """

    creation_dirs = create_canonical_ir_dirs(output_dir=output_dir)
    exclude_keys = {"overwrite"}
    creation_run = RunCtx(
        extra={
            k: v
            for k, v in config.model_dump(mode="json").items()
            if k not in exclude_keys
        },
        models={},
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(fp=output_dir / "creation_run.json", json_info=creation_run)

    logger.info(f"Saving canonical IR creation results to: {creation_dirs}")

    return creation_dirs, creation_run
