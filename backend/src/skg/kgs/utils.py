"""This module contains utility functions for knowledge graph creation."""

# Standard Library
import re
import uuid

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Third Party Library
from loguru import logger

# Package Library
from skg.document_ir.schemas import DocumentIR
from skg.page_ir_extraction.schemas import TableCell, TextUnit
from skg.schemas import CreateKGConfig, ExtractionConfig, RunCtx
from skg.utils.general import make_dir, open_json_type, write_to_json


@dataclass(frozen=True)
class KGDirs:
    """Dataclass for KG creation run directories.

    Parameters
    ----------
    root
        Root directory for the KG creation run artifacts.
    """

    root: Path


@dataclass(frozen=True)
class KGInputs:
    """Validated inputs for a KG creation run.

    Parameters
    ----------
    code_pattern_match_counts
        Match counts for each configured KG config code pattern.
    document_ir
        The validated stitched DocumentIR.
    document_ir_fp
        Path to the source DocumentIR JSON file.
    kg_dirs
        Output directories for this KG creation run.
    observed_languages
        Language tags observed in DocumentIR text units.
    kg_config
        The validated KG creation config with embedded extraction attributes.
    segment_counts
        Counts of DocumentIR segment kinds.
    table_columns_signature_counts
        Counts of table column signatures observed in table segments.
    table_selection_match_counts
        Counts showing how the observed table signatures match the CreateKGConfig
        table-selection policy.
    warnings
        Non-fatal warnings.
    """

    code_pattern_match_counts: dict[str, dict[str, int]]
    document_ir: DocumentIR
    document_ir_fp: Path
    kg_config: CreateKGConfig
    kg_dirs: KGDirs
    observed_languages: list[str]
    segment_counts: dict[str, int]
    table_columns_signature_counts: dict[str, int]
    table_selection_match_counts: dict[str, Any]
    warnings: list[str]


def _count_code_pattern_matches(
    *, document_ir: DocumentIR, kg_config: CreateKGConfig
) -> dict[str, dict[str, int]]:
    """Count configured code pattern matches in the DocumentIR.

    Parameters
    ----------
    document_ir
        The stitched DocumentIR to scan.
    kg_config
        The validated CreateKGConfig containing regex code patterns.

    Returns
    -------
    dict[str, dict[str, int]]
        Mapping of pattern name to total and unique match counts.

    Raises
    ------
    ValueError
        If a segment kind is unrecognized.
    """

    # 1. Extract text from block segments and table cells.
    texts: list[str] = []

    for segment in document_ir.segments:
        if segment.kind == "block":
            text = segment.combined_text

            if text is None and segment.text:
                text = segment.text.text

            if text and (clean_text := str(text).strip()):
                texts.append(clean_text)
        elif segment.kind == "table":
            for row in segment.rows:
                for cell in row.cells:
                    if cell_text := _extract_cell_text(cell):
                        texts.append(cell_text)
        else:
            raise ValueError(f"Unrecognized segment kind: {segment.kind}")

    # 2. Join extracted text and scan for code patterns.
    all_text = "\n".join(texts)
    counts: dict[str, dict[str, int]] = {}

    for name, pattern in kg_config.academic_standards.code_patterns.items():
        matches = [match.group(0) for match in re.finditer(pattern, all_text)]
        counts[name] = {"total": len(matches), "unique": len(set(matches))}

    return counts


def _count_table_columns_signatures(document_ir: DocumentIR) -> dict[str, int]:
    """Count table column signatures in DocumentIR table segments.

    Parameters
    ----------
    document_ir
        The stitched DocumentIR to summarize.

    Returns
    -------
    dict[str, int]
        Counts keyed by `columns_signature`, using `<missing>` when absent.
    """

    counts: Counter[str] = Counter()

    for segment in document_ir.segments:
        if segment.kind != "table":
            continue

        columns_signature = segment.columns_signature or "<missing>"
        counts[columns_signature] += 1

    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _count_table_selection_matches(
    *, document_ir: DocumentIR, kg_config: CreateKGConfig
) -> dict[str, Any]:
    """Count how observed table signatures match the KG config table-selection policy.

    Parameters
    ----------
    document_ir
        The stitched DocumentIR to inspect.
    kg_config
        The validated CreateKGConfig containing table-selection rules.

    Returns
    -------
    dict[str, Any]
        Summary counts for included and excluded table column signatures.
    """

    observed_counts: Counter[str] = Counter()

    for segment in document_ir.segments:
        if segment.kind != "table":
            continue

        observed_counts[segment.columns_signature or "<missing>"] += 1

    excluded_signature_counts = {
        signature: observed_counts.get(signature, 0)
        for signature in kg_config.academic_standards.excluded_table_columns_signatures
    }
    included_signature_counts = {
        signature: observed_counts.get(signature, 0)
        for signature in kg_config.academic_standards.included_table_columns_signatures
    }
    return {
        "excluded_table_signature_counts": excluded_signature_counts,
        "excluded_table_signature_match_total": sum(excluded_signature_counts.values()),
        "included_table_signature_counts": included_signature_counts,
        "included_table_signature_match_total": sum(included_signature_counts.values()),
    }


def _create_kg_dirs(output_dir: Path) -> KGDirs:
    """Create KG creation run directories.

    Parameters
    ----------
    output_dir
        The output directory root.

    Returns
    -------
    KGRunDirs
        The created KG run directories.
    """

    root = output_dir

    for p in [root]:
        make_dir(p)

    return KGDirs(root=root)


def _extract_cell_text(cell: TableCell) -> Optional[str]:
    """Extract text from a table cell-like object.

    Parameters
    ----------
    cell
        A table cell object from a DocumentIR table row.

    Returns
    -------
    Optional[str]
        The cell text when present and non-empty, otherwise None.
    """

    text_unit = cell.text

    if text_unit is None:
        return None

    text = text_unit.text

    if text is None:
        return None

    return str(text).strip() or None


def _extract_observed_languages(document_ir: DocumentIR) -> list[str]:
    """Extract a sorted list of unique languages observed in the DocumentIR.

    Parameters
    ----------
    document_ir
        The stitched DocumentIR to scan for language tags.

    Returns
    -------
    list[str]
        A sorted list of unique language tags found in the document's text units.
    """

    observed_languages_set: set[str] = set()

    for segment in document_ir.segments:
        if segment.kind == "block":
            if language := _extract_text_unit_language(segment.text):
                observed_languages_set.add(language)
        elif segment.kind == "table":
            for row in segment.rows:
                for cell in row.cells:
                    if language := _extract_text_unit_language(cell.text):
                        observed_languages_set.add(language)

    return sorted(observed_languages_set)


def _extract_text_unit_language(text_unit: TextUnit | None) -> Optional[str]:
    """Extract language from a TextUnit object.

    Parameters
    ----------
    text_unit
        A TextUnit object from a DocumentIR block or table cell.

    Returns
    -------
    Optional[str]
        The language tag when present and non-empty, otherwise None.
    """

    if text_unit is None:
        return None

    language = str(text_unit.language).strip()
    assert (
        language
    ), f"Expected non-empty language tag in TextUnit, got: '{text_unit.language}'"
    return language


def _language_base(language: str) -> str:
    """Return the base language subtag for loose language-overlap checks.

    Parameters
    ----------
    language
        A BCP-47-ish language tag.

    Returns
    -------
    str
        The lowercased base language subtag.
    """

    return language.replace("_", "-").split("-")[0].casefold()


def _validate_document_ir(document_ir_fp: Path) -> DocumentIR:
    """Validate basic DocumentIR assumptions required by KG creation.

    Parameters
    ----------
    document_ir_fp
        The file path to the stitched DocumentIR JSON.

    Raises
    ------
    ValueError
        If required document-level fields or segment identifiers are missing.
    """

    document_ir = DocumentIR.model_validate(open_json_type(document_ir_fp))

    if not document_ir.doc_key.strip():
        raise ValueError("DocumentIR.doc_key must be non-empty.")

    if not document_ir.pages:
        raise ValueError("DocumentIR.pages must be non-empty.")

    if not document_ir.segments:
        raise ValueError("DocumentIR.segments must be non-empty.")

    segment_ids = [segment.segment_id for segment in document_ir.segments]
    duplicate_segment_ids = sorted(
        segment_id for segment_id, count in Counter(segment_ids).items() if count > 1
    )

    if duplicate_segment_ids:
        raise ValueError(
            f"DocumentIR contains duplicate segment_id values: "
            f"{duplicate_segment_ids[:10]}"
        )

    return document_ir


def _validate_kg_config_compatibility(
    *,
    code_pattern_match_counts: dict[str, dict[str, int]],
    kg_config: CreateKGConfig,
    observed_languages: list[str],
    segment_counts: dict[str, int],
    table_selection_match_counts: dict[str, Any],
) -> list[str]:
    """Cross-check KG config assumptions against the stitched DocumentIR.

    Parameters
    ----------
    code_pattern_match_counts
        Match counts for each configured code pattern.
    kg_config
        The validated CreateKGConfig.
    observed_languages
        Language tags observed in the DocumentIR.
    segment_counts
        Counts of segment kinds in the DocumentIR.
    table_selection_match_counts
        Counts showing how observed table signatures match the table-selection policy.

    Returns
    -------
    list[str]
        Non-fatal compatibility warnings.

    Raises
    ------
    ValueError
        If a strict compatibility check fails.
    """

    warnings: list[str] = []

    kg_config_language_bases = {
        _language_base(language) for language in kg_config.metadata.languages
    }
    observed_language_bases = {
        _language_base(language) for language in observed_languages if language
    }

    if observed_language_bases and not (
        kg_config_language_bases & observed_language_bases
    ):
        warnings.append(
            f"KG config languages do not overlap with languages observed in the "
            f"DocumentIR: KG config languages: {sorted(kg_config.metadata.languages)}, "
            f"DocumentIR languages: {observed_languages}."
        )

    if kg_config.academic_standards.code_patterns:
        total_code_matches = sum(
            match_counts["total"] for match_counts in code_pattern_match_counts.values()
        )

        if total_code_matches == 0:
            message = (
                "CreateKGConfig configured as.code_patterns, but none of them matched "
                "text in the DocumentIR."
            )
            raise ValueError(message)

    if (
        kg_config.academic_standards.included_table_columns_signatures
        or kg_config.academic_standards.included_table_section_patterns
    ) and segment_counts.get("table", 0) == 0:
        warnings.append(
            "CreateKGConfig contains table-selection rules, but the DocumentIR "
            "contains no table segments."
        )

    if kg_config.academic_standards.included_table_columns_signatures:
        included_match_total = table_selection_match_counts[
            "included_table_signature_match_total"
        ]

        if included_match_total == 0:
            raise ValueError(
                "CreateKGConfig configured as.included_table_columns_signatures, but no "
                "matching table segments were observed in the DocumentIR. "
            )

    return warnings


def build_run_manifest(kg_run_inputs: KGInputs) -> dict[str, Any]:
    """Build a run manifest for the KG creation prep stage.

    Parameters
    ----------
    kg_run_inputs
        Validated KG run inputs and prep summaries.

    Returns
    -------
    dict[str, Any]
        JSON-serializable run manifest.
    """

    document_ir = kg_run_inputs.document_ir
    kg_config = kg_run_inputs.kg_config
    counts: Counter[str] = Counter()

    for segment in document_ir.segments:
        if segment.kind != "block":
            continue

        counts[str(getattr(segment.block_type, "value", segment.block_type))] += 1

    return {
        "block_type_counts": dict(sorted(counts.items())),
        "code_pattern_match_counts": kg_run_inputs.code_pattern_match_counts,
        "country": kg_config.metadata.country,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "doc_key": document_ir.doc_key,
        "document_ir_fp": str(kg_run_inputs.document_ir_fp),
        "framework_title": kg_config.metadata.framework_title,
        "kg_run_dir": str(kg_run_inputs.kg_dirs.root),
        "observed_languages": kg_run_inputs.observed_languages,
        "page_count": document_ir.page_count,
        "pdf_name": document_ir.pdf_name,
        "primary_language": kg_config.metadata.primary_language,
        "segment_counts": kg_run_inputs.segment_counts,
        "status": "prep_complete",
        "subject": kg_config.metadata.subject,
        "table_columns_signature_counts": kg_run_inputs.table_columns_signature_counts,
        "table_selection_match_counts": kg_run_inputs.table_selection_match_counts,
        "table_selection_policy": {
            "excluded_table_columns_signatures": kg_config.academic_standards.excluded_table_columns_signatures,
            "excluded_table_section_patterns": kg_config.academic_standards.excluded_table_section_patterns,
            "included_table_columns_signatures": kg_config.academic_standards.included_table_columns_signatures,
            "included_table_section_patterns": kg_config.academic_standards.included_table_section_patterns,
        },
        "warnings": kg_run_inputs.warnings,
    }


def cross_check_stitching_run(
    *, computed_doc_key: str, extraction_config: ExtractionConfig
) -> Path:
    """Cross-check that the extraction/stitching outputs match the source PDF.

    KG creation depends on the stitched DocumentIR produced for the same PDF bytes
    referenced by the runtime config. This helper keeps those run-output checks out
    of the CLI entry point. It verifies that:

    1. The extraction run metadata exists.
    2. The extraction run metadata contains a non-empty doc_key.
    3. The doc_key computed from the configured PDF matches the extraction run doc_key.
    4. The stitched DocumentIR exists under the expected doc_key output directory.

    Parameters
    ----------
    computed_doc_key
        The document key computed from the source PDF bytes by the caller.
    extraction_config
        The page-IR extraction configuration from the runtime config.

    Returns
    -------
    Path
        The stitched DocumentIR JSON path.

    Raises
    ------
    FileNotFoundError
        If extraction or stitching output files are missing.
    ValueError
        If the extraction run doc_key is missing/invalid or does not match the
        computed PDF doc_key.
    """

    extraction_run_results_dir = (
        extraction_config.output_dir / computed_doc_key / "extraction"
    )
    extraction_run_fp = extraction_run_results_dir / "extraction_run.json"

    if not extraction_run_fp.exists():
        raise FileNotFoundError(
            f"Expected extraction run metadata at {extraction_run_fp}. "
            f"Run page IR extraction before KG creation."
        )

    extraction_run_config = RunCtx.model_validate(open_json_type(extraction_run_fp))
    expected_doc_key = extraction_run_config.extra.get("doc_key")

    if not isinstance(expected_doc_key, str) or not expected_doc_key.strip():
        raise ValueError(
            f"Extraction run metadata at {extraction_run_fp} does not contain "
            f"a non-empty extra['doc_key'] value."
        )

    if expected_doc_key != computed_doc_key:
        raise ValueError(
            f"PDF doc_key mismatch.\n"
            f"  PDF provided to create_kgs():        {extraction_config.pdf_fp}\n"
            f"  computed doc_key:                    {computed_doc_key}\n"
            f"  extraction_run.json key:             {expected_doc_key}\n"
            f"You are likely creating KG artifacts against a different PDF than the "
            f"one used for DocumentIR stitching. Pass the same PDF used in the "
            f"extraction/stitching steps or re-run the upstream pipeline."
        )

    document_ir_fp = (
        extraction_config.output_dir
        / expected_doc_key
        / "stitching"
        / "document_ir.json"
    )

    if not document_ir_fp.exists():
        raise FileNotFoundError(
            f"Expected stitched DocumentIR JSON at {document_ir_fp}. "
            f"Run document IR stitching before KG creation."
        )

    return document_ir_fp


def load_and_validate_inputs(
    *, config: CreateKGConfig, document_ir_fp: Path, kg_dirs: KGDirs
) -> KGInputs:
    """Load, validate, and prep KG creation run inputs.

    Parameters
    ----------
    config
        KG creation config with embedded country/document-specific extraction
        attributes.
    document_ir_fp
        Path to the stitched DocumentIR JSON file.
    kg_dirs
        Directories for storing KG run artifacts.

    Returns
    -------
    KGInputs
        Validated inputs and prep summaries.

    Raises
    ------
    ValueError
        If the CreateKGConfig or DocumentIR fails prep validation.
    """

    # Validate the DocumentIR object. The CreateKGConfig has already been parsed and
    # validated by the runtime config loader.
    document_ir = _validate_document_ir(document_ir_fp)

    # Count code pattern matches in the document IR.
    code_pattern_match_counts = _count_code_pattern_matches(
        document_ir=document_ir, kg_config=config
    )

    # Extract unique languages observed in the DocumentIR.
    observed_languages = _extract_observed_languages(document_ir=document_ir)

    # Count segment kinds.
    segment_counts = dict(
        sorted(Counter(segment.kind for segment in document_ir.segments).items())
    )

    # Count table signatures.
    table_columns_signature_counts = _count_table_columns_signatures(document_ir)

    # Count matches against the KG config table-selection policy.
    table_selection_match_counts = _count_table_selection_matches(
        document_ir=document_ir, kg_config=config
    )

    # Check compatibility.
    warnings = _validate_kg_config_compatibility(
        code_pattern_match_counts=code_pattern_match_counts,
        kg_config=config,
        observed_languages=observed_languages,
        segment_counts=segment_counts,
        table_selection_match_counts=table_selection_match_counts,
    )

    return KGInputs(
        code_pattern_match_counts=code_pattern_match_counts,
        document_ir=document_ir,
        document_ir_fp=document_ir_fp,
        kg_config=config,
        kg_dirs=kg_dirs,
        observed_languages=observed_languages,
        segment_counts=segment_counts,
        table_columns_signature_counts=table_columns_signature_counts,
        table_selection_match_counts=table_selection_match_counts,
        warnings=warnings,
    )


def persist_kg_run(
    *, config: CreateKGConfig, output_dir: Path
) -> tuple[KGDirs, RunCtx]:
    """Persist KG run metadata.

    Parameters
    ----------
    config
        The KG creation run configuration.
    output_dir
        The output directory for the KG run results.

    Returns
    -------
    tuple[KGDirs, RunCtx]
        The created KG directories and persisted KG run metadata.
    """

    kg_dirs = _create_kg_dirs(output_dir=output_dir)
    exclude_keys = {"overwrite"}
    kg_run = RunCtx(
        extra={
            k: v
            for k, v in config.model_dump(mode="json").items()
            if k not in exclude_keys
        },
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(fp=kg_dirs.root / "kg_run.json", json_info=kg_run)

    logger.info(f"Saving KG results to: {kg_dirs.root}")

    return kg_dirs, kg_run
