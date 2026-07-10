"""This module contains utility functions for knowledge graph creation."""

# Standard Library
import json
import re
import unicodedata
import uuid

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

# Third Party Library
from loguru import logger
from pydantic import BaseModel

# Package Library
from skg.config import Settings
from skg.document_ir.schemas import DocumentIR, TableSegment
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
        Counts showing how table segments match the complete CreateKGConfig
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


def _build_table_section_selection_text(
    *, max_page_lookback: int, segment: TableSegment
) -> str:
    """Build bounded source context for table section-pattern matching.

    Section paths may retain older headings that are no longer active. This function
    therefore uses headings on the table's start page or within a configurable number
    of preceding pages. When no heading falls inside that page window, the nearest
    preceding section-path entry is retained as a conservative fallback. Table-local
    code and column-signature metadata are appended after the bounded heading context.

    Parameters
    ----------
    max_page_lookback
        Maximum number of pages before the table start page from which section-path
        headings may be used. Zero restricts matching to headings on the start page.
    segment
        Candidate stitched table segment.

    Returns
    -------
    str
        Bounded heading context plus table-local metadata for selection matching.

    Raises
    ------
    ValueError
        If max_page_lookback is negative.
    """

    if max_page_lookback < 0:
        raise ValueError("max_page_lookback must be non-negative.")

    table_start_page = min(
        provenance.page_index for provenance in segment.segment_provenance
    )
    earliest_heading_page = table_start_page - max_page_lookback
    nearby_heading_refs = [
        heading_ref
        for heading_ref in segment.section_path
        if earliest_heading_page <= heading_ref.page_index <= table_start_page
    ]

    if not nearby_heading_refs and segment.section_path:
        nearby_heading_refs = [segment.section_path[-1]]

    parts = [heading_ref.text for heading_ref in nearby_heading_refs]

    if segment.local_code:
        parts.append(str(segment.local_code))

    if segment.columns_signature:
        parts.append(str(segment.columns_signature))

    return "\n".join(part for part in parts if part)


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

    # 1. Extract the same source-visible text used by extraction windows.
    texts: list[str] = []

    for segment in document_ir.segments:
        if segment.kind == "block":
            block_payload = segment.model_dump(mode="json")

            if block_text := extract_block_source_text(block_payload):
                texts.append(block_text)
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
    """Count matches against the complete table-selection policy.

    Counts cover exact column signatures, bounded section-pattern matches, and the
    final number of table segments selected after exclusion precedence.

    Parameters
    ----------
    document_ir
        The stitched DocumentIR to inspect.
    kg_config
        The validated CreateKGConfig containing table-selection rules.

    Returns
    -------
    dict[str, Any]
        Selection counts by configured rule plus aggregate table-selection totals.
    """

    academic_standards = kg_config.academic_standards
    observed_counts: Counter[str] = Counter()
    included_section_pattern_counts = {
        pattern: 0 for pattern in academic_standards.included_table_section_patterns
    }
    excluded_section_pattern_counts = {
        pattern: 0 for pattern in academic_standards.excluded_table_section_patterns
    }
    included_section_pattern_segment_count = 0
    excluded_section_pattern_segment_count = 0
    selected_table_segment_count = 0
    table_segment_count = 0

    for segment in document_ir.segments:
        if segment.kind != "table":
            continue

        table_segment_count += 1
        observed_counts[segment.columns_signature or "<missing>"] += 1
        section_text = _build_table_section_selection_text(
            max_page_lookback=academic_standards.table_section_pattern_page_lookback,
            segment=segment,
        )
        included_pattern_matched = False
        excluded_pattern_matched = False

        for pattern in academic_standards.included_table_section_patterns:
            if re.search(pattern, section_text, flags=re.IGNORECASE):
                included_section_pattern_counts[pattern] += 1
                included_pattern_matched = True

        for pattern in academic_standards.excluded_table_section_patterns:
            if re.search(pattern, section_text, flags=re.IGNORECASE):
                excluded_section_pattern_counts[pattern] += 1
                excluded_pattern_matched = True

        if included_pattern_matched:
            included_section_pattern_segment_count += 1

        if excluded_pattern_matched:
            excluded_section_pattern_segment_count += 1

        if get_table_selection_reasons(kg_config=kg_config, segment=segment):
            selected_table_segment_count += 1

    excluded_signature_counts = {
        signature: observed_counts.get(signature, 0)
        for signature in academic_standards.excluded_table_columns_signatures
    }
    included_signature_counts = {
        signature: observed_counts.get(signature, 0)
        for signature in academic_standards.included_table_columns_signatures
    }
    return {
        "excluded_table_section_pattern_counts": excluded_section_pattern_counts,
        "excluded_table_section_pattern_match_total": (
            excluded_section_pattern_segment_count
        ),
        "excluded_table_signature_counts": excluded_signature_counts,
        "excluded_table_signature_match_total": sum(excluded_signature_counts.values()),
        "included_table_section_pattern_counts": included_section_pattern_counts,
        "included_table_section_pattern_match_total": (
            included_section_pattern_segment_count
        ),
        "included_table_signature_counts": included_signature_counts,
        "included_table_signature_match_total": sum(included_signature_counts.values()),
        "selected_table_segment_count": selected_table_segment_count,
        "table_segment_count": table_segment_count,
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


def _extract_figure_source_text(block_payload: dict[str, Any]) -> str:
    """Extract source-visible text from serialized figure fields.

    Figure alt text is descriptive metadata and is not treated as source-visible
    evidence. Only embedded text and source-visible captions are eligible for the
    extraction-window source text used by prompting and code matching.

    Parameters
    ----------
    block_payload
        JSON-serializable block payload containing optional figure data.

    Returns
    -------
    str
        Embedded text or caption text, otherwise an empty string.
    """

    figure = block_payload.get("figure")

    if not isinstance(figure, dict):
        return ""

    embedded_text = figure.get("embedded_text")

    if isinstance(embedded_text, dict) and embedded_text.get("text"):
        return str(embedded_text["text"]).strip()

    caption = figure.get("caption")

    if isinstance(caption, dict) and caption.get("text"):
        return str(caption["text"]).strip()

    if isinstance(caption, str) and caption.strip():
        return caption.strip()

    return ""


def _extract_list_items_source_text(list_items: list[Any]) -> str:
    """Render serialized list items with visible markers in source order.

    Each output line preserves the original item text. The structured marker is
    prepended only when the text does not already begin with the same complete marker.
    This keeps list-based identifiers available to extraction prompts and code matching
    without duplicating markers already embedded in source text.

    Parameters
    ----------
    list_items
        Serialized list-item payloads.

    Returns
    -------
    str
        Newline-delimited source-visible list text, or an empty string.
    """

    if not isinstance(list_items, list):
        return ""

    item_lines: list[str] = []

    for item in list_items:
        if not isinstance(item, dict):
            continue

        marker = str(item.get("marker") or "").strip()
        item_text = item.get("text")

        if isinstance(item_text, dict):
            text = str(item_text.get("text") or "").strip()
        elif item_text is not None:
            text = str(item_text).strip()
        else:
            text = ""

        if (
            marker
            and text
            and text_starts_with_complete_marker(marker=marker, text=text)
        ):
            line = text
        else:
            line = " ".join(part for part in [marker, text] if part)

        if line:
            item_lines.append(line)

    return "\n".join(item_lines)


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


def _matches_any_pattern(*, patterns: Sequence[str], text: str) -> bool:
    """Return whether any configured regex pattern matches text.

    Parameters
    ----------
    patterns
        Regex patterns to test.
    text
        Text to inspect.

    Returns
    -------
    bool
        True when at least one pattern matches; otherwise False.
    """

    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


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
        Counts showing how table segments match the complete selection policy.

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

    table_inclusion_rules_configured = bool(
        kg_config.academic_standards.included_table_columns_signatures
        or kg_config.academic_standards.included_table_section_patterns
    )

    if (
        table_inclusion_rules_configured
        and table_selection_match_counts["selected_table_segment_count"] == 0
    ):
        if segment_counts.get("table", 0) == 0:
            raise ValueError(
                "CreateKGConfig contains table inclusion rules, but the DocumentIR "
                "contains no table segments."
            )

        raise ValueError(
            "CreateKGConfig contains table inclusion rules, but no table segments "
            "were selected after applying bounded section-pattern matching and "
            "exclusion precedence. Check included/excluded table column signatures, "
            "included/excluded table section patterns, and "
            "table_section_pattern_page_lookback."
        )

    return warnings


def append_jsonl_model(*, fp: Path, model: BaseModel) -> None:
    """Append one Pydantic model payload to a JSONL artifact.

    The parent directory is created before writing. If the target file already exists
    and its final byte is not a newline, a separating newline is written before the new
    model payload so the file remains valid JSONL.

    Parameters
    ----------
    fp
        JSONL artifact path to append to.
    model
        Pydantic model instance to serialize as one JSONL record.
    """

    make_dir(fp.parent)

    if fp.exists() and fp.stat().st_size > 0:
        with fp.open("rb") as f:
            f.seek(-1, 2)
            missing_trailing_newline = f.read(1) != b"\n"

        if missing_trailing_newline:
            with fp.open("a", encoding="utf-8") as f:
                f.write("\n")

    with fp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(model.model_dump(mode="json"), ensure_ascii=False))
        f.write("\n")


def assert_model_sequences_equal(
    *, actual: Sequence[Any], artifact_label: str, expected: Sequence[Any]
) -> None:
    """Validate that two persisted model sequences are exactly equivalent.

    Parameters
    ----------
    actual
        Models loaded from an artifact.
    artifact_label
        Human-readable artifact label for error messages.
    expected
        Expected models computed during the current run.

    Raises
    ------
    ValueError
        If the sequences differ in length or model payload.
    """

    if len(actual) != len(expected):
        raise ValueError(
            f"{artifact_label} has {len(actual)} records, but expected "
            f"{len(expected)} records."
        )

    for index, (actual_model, expected_model) in enumerate(
        zip(actual, expected, strict=True), start=1
    ):
        if model_dump_key(actual_model) != model_dump_key(expected_model):
            raise ValueError(
                f"{artifact_label} record {index} does not match the current "
                f"planned artifact payload."
            )


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
            "table_section_pattern_page_lookback": kg_config.academic_standards.table_section_pattern_page_lookback,
        },
        "warnings": kg_run_inputs.warnings,
    }


def build_standards_framework_uuid(doc_key: str) -> uuid.UUID:
    """Build the deterministic StandardsFramework root UUID for one source document.

    Parameters
    ----------
    doc_key
        Stable source DocumentIR key used to scope all KG identifiers for the run.

    Returns
    -------
    uuid.UUID
        Deterministic UUIDv5 for the single StandardsFramework root node.

    Raises
    ------
    ValueError
        If the document key is missing or blank.
    """

    doc_key_clean = str(doc_key or "").strip()

    if not doc_key_clean:
        raise ValueError("Cannot mint StandardsFramework UUID without a doc_key.")

    identity_key = f"lc:curriculum:{doc_key_clean}:standards_framework"
    return uuid.uuid5(Settings.LC_CANONICAL_NAMESPACE_UUID, identity_key)


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


def extract_block_source_text(block_payload: dict[str, Any]) -> str:
    """Build source-faithful text from a serialized block segment payload.

    The extraction order matches the Academic Standards window builder: combined text,
    ordinary text, list-item text, then figure text. Keeping this logic in one shared
    function ensures preflight code-pattern scanning and extraction-window construction
    agree about which block content is extractable.

    Parameters
    ----------
    block_payload
        JSON-serializable block segment payload.

    Returns
    -------
    str
        Source-visible block text, or an empty string when no useful text is present.
    """

    if text := block_payload.get("combined_text"):
        return str(text).strip()

    text_unit = block_payload.get("text")

    if isinstance(text_unit, dict) and text_unit.get("text"):
        return str(text_unit["text"]).strip()

    return _extract_list_items_source_text(
        block_payload.get("list_items") or []
    ) or _extract_figure_source_text(block_payload)


def get_table_selection_reasons(
    *, kg_config: CreateKGConfig, segment: TableSegment
) -> list[str]:
    """Return deterministic reasons for selecting a table for SFI extraction.

    Exclusion rules take precedence over inclusion rules. Section-pattern rules are
    evaluated against bounded nearby heading context built with
    `table_section_pattern_page_lookback` so stale section-path entries cannot select
    unrelated later tables.

    Parameters
    ----------
    kg_config
        Country/document-specific KG extraction configuration.
    segment
        Candidate stitched table segment.

    Returns
    -------
    list[str]
        Stable selection reasons, or an empty list when the table is not selected.
    """

    academic_standards = kg_config.academic_standards
    columns_signature = segment.columns_signature or "<missing>"
    section_text = _build_table_section_selection_text(
        max_page_lookback=academic_standards.table_section_pattern_page_lookback,
        segment=segment,
    )

    if columns_signature in academic_standards.excluded_table_columns_signatures:
        return []

    if _matches_any_pattern(
        patterns=academic_standards.excluded_table_section_patterns, text=section_text
    ):
        return []

    reasons: list[str] = []

    if columns_signature in academic_standards.included_table_columns_signatures:
        reasons.append("table_columns_signature_included_match")

    if _matches_any_pattern(
        patterns=academic_standards.included_table_section_patterns, text=section_text
    ):
        reasons.append("table_section_included_pattern_match")

    return reasons


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


def model_dump_key(value: BaseModel) -> str:
    """Build a stable JSON comparison key for a Pydantic model.

    Parameters
    ----------
    value
        Pydantic model to serialize.

    Returns
    -------
    str
        Stable JSON representation suitable for exact artifact comparison.
    """

    return json.dumps(value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


def normalize_code(value: Any) -> str | None:
    """Normalize a source code for hint matching.

    Parameters
    ----------
    value
        Raw code-like value.

    Returns
    -------
    str | None
        Normalized code or None.
    """

    if value is None:
        return None

    normalized = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    normalized = re.sub(r"\s+", "", normalized).strip(" .:;-)–—")
    return normalized or None


def normalize_text(value: Any) -> str:
    """Normalize text.

    Parameters
    ----------
    value
        Raw text value.

    Returns
    -------
    str
        Normalized text.
    """

    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


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


def reset_output_files(output_fps: Sequence[Path]) -> None:
    """Remove stale output artifacts and initialize empty JSONL artifacts.

    Each parent directory is created before the corresponding artifact is reset. Any
    existing artifact is deleted. Paths with a `.jsonl` suffix are recreated as empty
    files so downstream append-only progress writers can assume the file exists.

    Parameters
    ----------
    output_fps
        Output artifact paths to reset.
    """

    for output_fp in output_fps:
        make_dir(output_fp.parent)

        if output_fp.exists():
            output_fp.unlink()

        if output_fp.suffix == ".jsonl":
            output_fp.write_text("", encoding="utf-8")


def text_starts_with_complete_marker(*, marker: str, text: str) -> bool:
    """Return whether text begins with the same complete structured list marker.

    NFKC normalization and whitespace collapsing are used only for comparison; the
    caller retains the original marker and text for output. Numeric hierarchy
    continuations are not treated as complete matches, so marker `2.` does not match
    text beginning with `2.1` and marker `2.1` does not match `2.1.1`.

    Parameters
    ----------
    marker
        Structured source-visible list marker.
    text
        Source-visible list-item text.

    Returns
    -------
    bool
        True when text already begins with the complete marker; otherwise False.
    """

    marker_key = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", marker)).strip()
    text_key = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).lstrip()

    if not marker_key or not text_key.startswith(marker_key):
        return False

    remainder = text_key[len(marker_key) :]

    if not remainder or remainder[0].isspace():
        return True

    if marker_key.endswith("."):
        return not remainder[0].isdigit()

    if marker_key[-1].isalnum():
        return remainder[0] in {":", ";", ")", "]", "}"}

    return True


def unique_nonempty(values: Iterable[Any]) -> list[str]:
    """Return unique non-empty string values while preserving order.

    Parameters
    ----------
    values
        Raw values.

    Returns
    -------
    list[str]
        Unique cleaned string values.
    """

    output: list[str] = []
    seen: set[str] = set()

    for value in values:
        if value is None:
            continue

        value_clean = str(value).strip()

        if not value_clean or value_clean in seen:
            continue

        output.append(value_clean)
        seen.add(value_clean)

    return output
