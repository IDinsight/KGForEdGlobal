"""This module contains the entry point for creating Learning Commons KG-ready
extraction artifacts from a stitched DocumentIR.

This module implements the first step of the simplified KG creation pipeline:

1. Load and validate a country/document-specific ``DocumentProfile``.
2. Load and validate the corresponding stitched ``DocumentIR``.
3. Cross-check that the profile is compatible with the DocumentIR.
4. Create the KG run output directory.
5. Persist a lightweight ``run_manifest.json`` for audit/debugging.

Later steps will build extraction windows, run LLM-based SFI candidate extraction,
compile final SFIs, generate LearningComponents, and export KG schema objects.

Invoke from the backend directory via:

python src/skg/entries/create_kgs.py \
    ../examples/ghana/document_ir.json \
    ../examples/ghana/document_profile.json \
    ../examples/ghana/output
"""

# Standard Library
import re
import sys
import traceback

from collections import Counter
from dataclasses import dataclass
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
from skg.document_ir.schemas import DocumentIR
from skg.kgs.schemas import DocumentProfile
from skg.page_ir_extraction.schemas import TableCell, TextUnit
from skg.utils.general import make_dir, open_json_type, write_to_json

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


@dataclass(frozen=True)
class KGRunDirs:
    """Dataclass for KG creation run directories.

    Parameters
    ----------
    root
        Root directory for the KG creation run artifacts.
    """

    root: Path


@dataclass(frozen=True)
class KGRunInputs:
    """Validated inputs for a KG creation run.

    Parameters
    ----------
    code_pattern_match_counts
        Match counts for each configured profile code pattern.
    document_ir
        The validated stitched DocumentIR.
    document_ir_fp
        Path to the source DocumentIR JSON file.
    kg_dirs
        Output directories for this KG creation run.
    observed_languages
        Language tags observed in DocumentIR text units.
    profile
        The validated country/document-specific DocumentProfile.
    profile_fp
        Path to the source DocumentProfile JSON file.
    segment_counts
        Counts of DocumentIR segment kinds.
    table_columns_signature_counts
        Counts of table column signatures observed in table segments.
    warnings
        Non-fatal warnings.
    """

    code_pattern_match_counts: dict[str, dict[str, int]]
    document_ir: DocumentIR
    document_ir_fp: Path
    document_profile: DocumentProfile
    document_profile_fp: Path
    kg_dirs: KGRunDirs
    observed_languages: list[str]
    segment_counts: dict[str, int]
    table_columns_signature_counts: dict[str, int]
    warnings: list[str]


def _build_run_manifest(kg_run_inputs: KGRunInputs) -> dict[str, Any]:
    """Build a lightweight run manifest for the KG creation preflight stage.

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
    document_profile = kg_run_inputs.document_profile
    counts: Counter[str] = Counter()

    for segment in document_ir.segments:
        if segment.kind != "block":
            continue

        counts[str(getattr(segment.block_type, "value", segment.block_type))] += 1

    return {
        "block_type_counts": dict(sorted(counts.items())),
        "code_pattern_match_counts": kg_run_inputs.code_pattern_match_counts,
        "completed_at": None,
        "country": document_profile.country,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "doc_key": document_ir.doc_key,
        "document_ir_fp": str(kg_run_inputs.document_ir_fp),
        "document_profile_fp": str(kg_run_inputs.document_profile_fp),
        "framework_title": document_profile.framework_title,
        "has_stable_codes": document_profile.has_stable_codes,
        "kg_run_dir": str(kg_run_inputs.kg_dirs.root),
        "observed_languages": kg_run_inputs.observed_languages,
        "page_count": document_ir.page_count,
        "pdf_name": document_ir.pdf_name,
        "primary_language": document_profile.primary_language,
        "segment_counts": kg_run_inputs.segment_counts,
        "status": "preflight_complete",
        "subject": document_profile.subject,
        "table_columns_signature_counts": kg_run_inputs.table_columns_signature_counts,
        "warnings": kg_run_inputs.warnings,
    }


def _count_code_pattern_matches(
    *, document_ir: DocumentIR, document_profile: DocumentProfile
) -> dict[str, dict[str, int]]:
    """Count configured code pattern matches in the DocumentIR.

    Parameters
    ----------
    document_ir
        The stitched DocumentIR to scan.
    document_profile
        The validated DocumentProfile containing regex code patterns.

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

    for name, pattern in document_profile.code_patterns.items():
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


def _create_kg_run_dirs(*, doc_key: str, output_dir: Path) -> KGRunDirs:
    """Create KG creation run directories for a stitched document.

    Parameters
    ----------
    doc_key
        Deterministic document key from the stitched DocumentIR.
    output_dir
        Output directory root supplied by the caller.

    Returns
    -------
    KGRunDirs
        The created KG run directories.
    """

    root = output_dir / doc_key / "kgs"

    for p in [root]:
        make_dir(p)

    return KGRunDirs(root=root)


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


def _validate_document_profile_compatibility(
    *,
    code_pattern_match_counts: dict[str, dict[str, int]],
    document_profile: DocumentProfile,
    observed_languages: list[str],
    segment_counts: dict[str, int],
) -> list[str]:
    """Cross-check document profile assumptions against the stitched DocumentIR.

    Parameters
    ----------
    code_pattern_match_counts
        Match counts for each configured code pattern.
    document_profile
        The validated DocumentProfile.
    observed_languages
        Language tags observed in the DocumentIR.
    segment_counts
        Counts of segment kinds in the DocumentIR.

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

    document_profile_language_bases = {
        _language_base(language) for language in document_profile.languages
    }
    observed_language_bases = {
        _language_base(language) for language in observed_languages if language
    }

    if observed_language_bases and not (
        document_profile_language_bases & observed_language_bases
    ):
        warnings.append(
            f"Document profile languages do not overlap with languages observed in the "
            f"DocumentIR: Document Profile languages: {sorted(document_profile.languages)}, "
            f"DocumentIR languages: {observed_languages}."
        )

    if document_profile.has_stable_codes and not document_profile.code_patterns:
        raise ValueError(
            "DocumentProfile.has_stable_codes is true, but no code_patterns were configured."
        )

    if document_profile.has_stable_codes:
        total_code_matches = sum(
            match_counts["total"] for match_counts in code_pattern_match_counts.values()
        )

        if total_code_matches == 0:
            message = (
                "DocumentProfile.has_stable_codes is true, but none of the configured "
                "code_patterns matched text in the DocumentIR."
            )
            raise ValueError(message)

    if (
        document_profile.table_window_mode in {"row_chunks", "whole_table"}
        and segment_counts.get("table", 0) == 0
    ):
        warnings.append(
            "Profile table_window_mode expects table segments, but the DocumentIR "
            "contains no table segments."
        )

    if document_profile.row_overlap >= document_profile.max_rows_per_table_window:
        raise ValueError(
            "DocumentProfile.row_overlap must be smaller than max_rows_per_table_window."
        )

    return warnings


def load_and_validate_inputs(
    *,
    document_ir_fp: Path,
    document_profile_fp: Path,
    output_dir: Path,
    overwrite: bool,
) -> KGRunInputs:
    """Load, validate, and prep KG creation run inputs.

    Parameters
    ----------
    document_ir_fp
        Path to the stitched DocumentIR JSON file.
    document_profile_fp
        Path to the country/document-specific DocumentProfile JSON file.
    output_dir
        Output directory root for KG creation artifacts.
    overwrite
        Whether an existing run manifest may be overwritten.

    Returns
    -------
    KGRunInputs
        Validated inputs and preflight summaries.

    Raises
    ------
    FileExistsError
        If the run manifest already exists and overwrite is False.
    ValueError
        If the profile or DocumentIR fails preflight validation.
    """

    # Validate the DocumentIR and DocumentProfile objects.
    document_ir = _validate_document_ir(document_ir_fp)
    document_profile = DocumentProfile.model_validate(
        open_json_type(document_profile_fp)
    )

    # Create directories for the KG run.
    kg_dirs = _create_kg_run_dirs(doc_key=document_ir.doc_key, output_dir=output_dir)
    kg_manifest_fp = kg_dirs.root / "kg_run_manifest.json"

    if kg_manifest_fp.exists() and not overwrite:
        raise FileExistsError(
            f"KG run manifest already exists at: {kg_manifest_fp}. "
            f"Pass --overwrite to replace it."
        )

    # Count code pattern matches in the document IR.
    code_pattern_match_counts = _count_code_pattern_matches(
        document_ir=document_ir, document_profile=document_profile
    )

    # Extract unique languages observed in the DocumentIR.
    observed_languages = _extract_observed_languages(document_ir=document_ir)

    # Count segment kinds.
    segment_counts = Counter(segment.kind for segment in document_ir.segments)
    segment_counts = dict(sorted(segment_counts.items()))

    # Count table signatures.
    table_columns_signature_counts = _count_table_columns_signatures(document_ir)

    # Check compatibility.
    warnings = _validate_document_profile_compatibility(
        code_pattern_match_counts=code_pattern_match_counts,
        document_profile=document_profile,
        observed_languages=observed_languages,
        segment_counts=segment_counts,
    )

    return KGRunInputs(
        code_pattern_match_counts=code_pattern_match_counts,
        document_ir=document_ir,
        document_ir_fp=document_ir_fp,
        document_profile=document_profile,
        document_profile_fp=document_profile_fp,
        kg_dirs=kg_dirs,
        observed_languages=observed_languages,
        segment_counts=segment_counts,
        table_columns_signature_counts=table_columns_signature_counts,
        warnings=warnings,
    )


@cli.command()
def create(
    document_ir_fp: Path = typer.Argument(
        ...,
        dir_okay=False,
        exists=True,
        file_okay=True,
        help="The file path to the stitched DocumentIR JSON.",
        readable=True,
        resolve_path=True,
    ),
    document_profile_fp: Path = typer.Argument(
        ...,
        dir_okay=False,
        exists=True,
        file_okay=True,
        help="The file path to the country/document-specific DocumentProfile JSON.",
        readable=True,
        resolve_path=True,
    ),
    output_dir: Path = typer.Argument(
        ...,
        dir_okay=True,
        file_okay=False,
        help="The output directory root for KG creation artifacts.",
        resolve_path=True,
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Overwrite an existing KG run manifest.",
    ),
) -> None:
    """Create the initial KG run manifest from a profile and stitched DocumentIR.

    The process is as follows:

    1. Load and validate the DocumentProfile JSON.
    2. Load and validate the stitched DocumentIR JSON.
    3. Cross-check basic profile/document compatibility.
    4. Create the KG run output directory.
    5. Persist a run_manifest.json file for audit/debugging.

    Parameters
    ----------
    document_ir_fp
        The file path to the stitched DocumentIR JSON.
    document_profile_fp
        The file path to the country/document-specific DocumentProfile JSON.
    output_dir
        The output directory root for KG creation artifacts.
    overwrite
        Whether to overwrite an existing KG run manifest.

    Raises
    ------
    Exception
        If any error occurs during knowledge graph creation.
    """

    kg_run_manifest: dict[str, Any] | None = None
    kg_run_manifest_fp: Path | None = None

    try:
        logger.info(
            f"Starting KG creation prep using DocumentIR: {document_ir_fp} and "
            f"document profile: {document_profile_fp} "
        )

        kg_run_inputs = load_and_validate_inputs(
            document_ir_fp=document_ir_fp,
            document_profile_fp=document_profile_fp,
            output_dir=output_dir,
            overwrite=overwrite,
        )
        kg_run_manifest = _build_run_manifest(kg_run_inputs)
        kg_run_manifest_fp = kg_run_inputs.kg_dirs.root / "kg_run_manifest.json"
        write_to_json(fp=kg_run_manifest_fp, json_info=kg_run_manifest)

        logger.success(f"KG creation prep completed successfully: {kg_run_manifest_fp}")
    except Exception as e:  # pylint: disable=broad-except
        logger.error(f"KG creation prep failed: {e.__class__.__name__}: {str(e)}")

        if kg_run_manifest is not None and kg_run_manifest_fp is not None:
            kg_run_manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
            kg_run_manifest["error"] = {
                "message": str(e),
                "traceback": traceback.format_exc(limit=20),
                "type": e.__class__.__name__,
            }
            kg_run_manifest["status"] = "error"
            write_to_json(fp=kg_run_manifest_fp, json_info=kg_run_manifest)

        raise


if __name__ == "__main__":
    cli()
