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
from typing import Any, Iterable, Mapping, Optional, Sequence

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
class ConfiguredCodeMatch:
    """One source-visible match for a configured curriculum code pattern.

    Attributes
    ----------
    code_type
        Runtime-config code-pattern key that produced the match.
    end_char
        Exclusive end character offset in the scanned source text.
    normalized_value
        Formatting-normalized code value suitable for structured fields.
    raw_value
        Exact source-visible code surface matched in the source text.
    start_char
        Inclusive start character offset in the scanned source text.
    """

    code_type: str
    end_char: int
    normalized_value: str
    raw_value: str
    start_char: int


@dataclass(frozen=True)
class ConfiguredCodeMatchSourceUnit:
    """Atomic source-text unit eligible for configured code matching.

    Regex matching is confined to one unit, so a configured code cannot be manufactured
    across unrelated source boundaries such as table cells or rows. `start_char`
    locates the unit within a caller-defined coordinate space. This lets extraction
    windows preserve offsets into their full rendered source text while preflight
    counting can retain deterministic source order without concatenating units for
    matching.

    Attributes
    ----------
    start_char
        Inclusive character offset of `text` in the caller-defined coordinate space.
    text
        Source-visible text to scan as one atomic matching unit.
    """

    start_char: int
    text: str

    def __post_init__(self) -> None:
        """Validate the source unit offset and text value.

        Raises
        ------
        TypeError
            If `text` is not a string.
        ValueError
            If `start_char` is negative.
        """

        if self.start_char < 0:
            raise ValueError(
                "Configured code match source-unit offsets must be non-negative."
            )

        if not isinstance(self.text, str):
            raise TypeError("Configured code match source-unit text must be a string.")


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


@dataclass(frozen=True)
class ResolvedCandidateCode:
    """Resolved code identity for one extracted SFI candidate.

    Attributes
    ----------
    normalized_statement_code
        Formatting-insensitive normalized code used for registry bucketing and
        downstream identity logic, or `None` when the candidate is uncoded.
    resolved_code_type
        Configured code-pattern key that authoritatively matched the candidate code,
        or `None` when the candidate is uncoded.
    """

    normalized_statement_code: str | None
    resolved_code_type: str | None

    def __post_init__(self) -> None:
        """Validate that normalized code and resolved type are present together.

        Raises
        ------
        ValueError
            If exactly one of the two resolved-code fields is present.
        """

        if bool(self.normalized_statement_code) != bool(self.resolved_code_type):
            raise ValueError(
                "normalized_statement_code and resolved_code_type must either both "
                "be present or both be null."
            )


def _build_glued_code_pattern(pattern: str) -> str | None:
    """Build a conservative fallback regex for a code glued to statement text.

    The configured regex remains authoritative. This function removes one terminal word
    boundary only when the pattern ends with a literal `\\b` token. The shared matcher
    then accepts a fallback match only when alphabetic statement text follows
    immediately and the isolated code still fully matches the original regex.

    Parameters
    ----------
    pattern
        Configured source-visible code regex.

    Returns
    -------
    str | None
        Regex without one terminal word-boundary token, or `None` when no supported
        fallback can be built.
    """

    pattern_clean = pattern.rstrip()

    if not pattern_clean.endswith(r"\b"):
        return None

    return pattern_clean[:-2]


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


def _compile_code_patterns(
    code_patterns: Mapping[str, str | re.Pattern[str]],
) -> dict[str, re.Pattern[str]]:
    """Compile and validate configured code patterns keyed by code type.

    Code type keys are stripped of surrounding whitespace, required to be non-empty,
    and required to remain unique after stripping. Pattern values that are already
    compiled regular expressions are used unchanged; string values are stripped,
    required to be non-empty, and compiled case-insensitively. Keys are processed in
    sorted order so validation is deterministic.

    Parameters
    ----------
    code_patterns
        Configured source-facing code regexes keyed by code type. Values may be raw
        pattern strings or compiled regular expressions.

    Returns
    -------
    dict[str, re.Pattern[str]]
        Compiled regular expressions keyed by cleaned code type.

    Raises
    ------
    ValueError
        If a code type key is empty or duplicated after stripping, or if a configured
        pattern is empty or is not a valid regular expression.
    """

    compiled_patterns: dict[str, re.Pattern[str]] = {}

    for code_type, pattern in sorted(code_patterns.items()):
        code_type_clean = str(code_type or "").strip()

        if not code_type_clean:
            raise ValueError(
                "Configured code patterns must use non-empty code type keys."
            )

        if code_type_clean in compiled_patterns:
            raise ValueError(
                f"Configured code type {code_type_clean!r} is duplicated after "
                f"stripping whitespace."
            )

        if isinstance(pattern, re.Pattern):
            compiled_pattern = pattern
        else:
            pattern_clean = str(pattern or "").strip()

            if not pattern_clean:
                raise ValueError(
                    f"Configured code pattern for code type {code_type_clean!r} "
                    f"must be non-empty."
                )

            try:
                compiled_pattern = re.compile(pattern_clean, flags=re.IGNORECASE)
            except re.error as exc:
                raise ValueError(
                    f"Configured code pattern for code type {code_type_clean!r} is "
                    f"invalid: {pattern_clean!r}."
                ) from exc

        compiled_patterns[code_type_clean] = compiled_pattern

    return compiled_patterns


def _count_code_pattern_matches(
    *, document_ir: DocumentIR, kg_config: CreateKGConfig
) -> dict[str, dict[str, int]]:
    """Count configured code matches across source-visible DocumentIR text units.

    The same shared matcher used by extraction-window construction is applied to each
    source-visible block or table-cell text unit. Scanning units independently avoids
    manufacturing matches across unrelated cells or segments while keeping strict and
    glued-code recognition identical to the later detector.

    Parameters
    ----------
    document_ir
        The stitched DocumentIR to scan.
    kg_config
        The validated CreateKGConfig containing regex code patterns.

    Returns
    -------
    dict[str, dict[str, int]]
        Mapping of pattern name to total and unique normalized match counts.

    Raises
    ------
    ValueError
        If a segment kind is unrecognized.
    """

    source_offset = 0
    source_units: list[ConfiguredCodeMatchSourceUnit] = []

    for segment in document_ir.segments:
        if segment.kind == "block":
            block_payload = segment.model_dump(mode="json")

            if block_text := extract_block_source_text(block_payload):
                source_units.append(
                    ConfiguredCodeMatchSourceUnit(
                        start_char=source_offset, text=block_text
                    )
                )
                source_offset += len(block_text) + 1
        elif segment.kind == "table":
            for row in segment.rows:
                for cell in row.cells:
                    if cell_text := _extract_cell_text(cell):
                        source_units.append(
                            ConfiguredCodeMatchSourceUnit(
                                start_char=source_offset, text=cell_text
                            )
                        )
                        source_offset += len(cell_text) + 1
        else:
            raise ValueError(f"Unrecognized segment kind: {segment.kind}")

    total_counts: dict[str, int] = {
        code_type: 0 for code_type in kg_config.academic_standards.code_patterns
    }
    unique_values: dict[str, set[str]] = {
        code_type: set() for code_type in kg_config.academic_standards.code_patterns
    }

    for match in find_configured_code_matches_in_source_units(
        code_patterns=kg_config.academic_standards.code_patterns,
        source_units=source_units,
    ):
        total_counts[match.code_type] += 1
        unique_values[match.code_type].add(match.normalized_value)

    return {
        code_type: {
            "total": total_counts[code_type],
            "unique": len(unique_values[code_type]),
        }
        for code_type in kg_config.academic_standards.code_patterns
    }


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


def _normalize_code_match_value(*, pattern: str, raw_value: str) -> str:
    """Normalize punctuation-adjacent whitespace in a matched code surface.

    The compacted value is used only when it still fully matches the configured regex;
    otherwise the stripped source-visible value is retained.

    Parameters
    ----------
    pattern
        Configured regex that matched the source-visible code.
    raw_value
        Exact source-visible matched code surface.

    Returns
    -------
    str
        Formatting-normalized code suitable for structured code fields.
    """

    raw_value_clean = raw_value.strip()
    compacted_value = re.sub(r"\s*([^\w\s])\s*", r"\1", raw_value_clean)

    if re.fullmatch(pattern, compacted_value) is not None:
        return compacted_value

    return raw_value_clean


def _resolve_code_type(
    *,
    compiled_patterns: Mapping[str, re.Pattern[str]],
    expected_code_type: str | None,
    matching_code_types: Sequence[str],
    statement_code: str | None,
) -> str:
    """Resolve the authoritative configured code type for a coded candidate.

    A declared expected code type acts as an explicit constraint: it must be a
    configured code type and must appear among the candidate's matching code types.
    When no code type is declared, the candidate code must match exactly one configured
    code type.

    Parameters
    ----------
    compiled_patterns
        Compiled code patterns keyed by cleaned code type, used to validate that a
        declared expected code type is configured.
    expected_code_type
        Optional code type declared for the candidate's canonical statement type.
    matching_code_types
        Configured code types whose pattern matched the candidate code exactly.
    statement_code
        Candidate statement code, used only for error messages.

    Returns
    -------
    str
        The uniquely resolved configured code type.

    Raises
    ------
    ValueError
        If the expected code type is not configured or does not match, or if an untyped
        statement code matches zero or multiple code types.
    """

    expected_code_type_clean = str(expected_code_type or "").strip() or None

    if expected_code_type_clean is not None:
        if expected_code_type_clean not in compiled_patterns:
            raise ValueError(
                f"Expected code type {expected_code_type_clean!r} is not present in "
                f"the configured code patterns."
            )

        if expected_code_type_clean not in matching_code_types:
            raise ValueError(
                f"Statement code {statement_code!r} does not match expected code "
                f"type {expected_code_type_clean!r}; matched code types "
                f"{list(matching_code_types)}."
            )

        return expected_code_type_clean

    if len(matching_code_types) != 1:
        raise ValueError(
            f"Statement code {statement_code!r} must match exactly one configured "
            f"code type when its statement type has no declared code_type; "
            f"matched {list(matching_code_types)}."
        )

    return matching_code_types[0]


def _validate_document_ir(*, document_ir_fp: Path, expected_doc_key: str) -> DocumentIR:
    """Validate basic DocumentIR assumptions required by KG creation.

    Parameters
    ----------
    document_ir_fp
        The file path to the stitched DocumentIR JSON.
    expected_doc_key
        The document key computed from the configured source PDF.

    Returns
    -------
    DocumentIR
        The validated stitched DocumentIR.

    Raises
    ------
    ValueError
        If the expected document key is blank, the DocumentIR key does not match it, or
        required document-level fields or segment identifiers are missing.
    """

    expected_doc_key_clean = str(expected_doc_key or "").strip()

    if not expected_doc_key_clean:
        raise ValueError("Expected DocumentIR doc_key must be non-empty.")

    document_ir = DocumentIR.model_validate(open_json_type(document_ir_fp))
    document_ir_doc_key = document_ir.doc_key.strip()

    if not document_ir_doc_key:
        raise ValueError("DocumentIR.doc_key must be non-empty.")

    if document_ir_doc_key != expected_doc_key_clean:
        raise ValueError(
            f"DocumentIR doc_key mismatch.\n"
            f"  DocumentIR path:       {document_ir_fp}\n"
            f"  expected doc_key:      {expected_doc_key_clean}\n"
            f"  DocumentIR.doc_key:    {document_ir_doc_key}\n"
            f"The loaded DocumentIR does not belong to the configured source PDF."
        )

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


def find_configured_code_matches(
    *, code_patterns: Mapping[str, str], source_text: str
) -> list[ConfiguredCodeMatch]:
    """Find strict and conservatively glued configured codes in one source unit.

    Each configured regex is applied unchanged first. Patterns ending in a literal
    terminal word boundary also receive a conservative fallback that removes only that
    boundary. A fallback match is accepted only when an alphabetic character follows
    immediately and the isolated matched value fully satisfies the original configured
    regex. Strict and fallback results are then sorted and deduplicated.

    Parameters
    ----------
    code_patterns
        Configured source-visible regexes keyed by code type.
    source_text
        One atomic source-visible text unit to scan.

    Returns
    -------
    list[ConfiguredCodeMatch]
        Ordered, deduplicated configured code matches with source offsets.
    """

    matches: list[ConfiguredCodeMatch] = []

    for code_type, pattern in code_patterns.items():
        for match in re.finditer(pattern, source_text):
            raw_value = match.group(0)
            matches.append(
                ConfiguredCodeMatch(
                    code_type=code_type,
                    end_char=match.end(),
                    normalized_value=_normalize_code_match_value(
                        pattern=pattern, raw_value=raw_value
                    ),
                    raw_value=raw_value,
                    start_char=match.start(),
                )
            )

        glued_pattern = _build_glued_code_pattern(pattern)

        if glued_pattern is None:
            continue

        for match in re.finditer(glued_pattern, source_text):
            if match.end() >= len(source_text):
                continue

            raw_value = match.group(0)

            if not source_text[match.end()].isalpha():
                continue

            if re.fullmatch(pattern, raw_value) is None:
                continue

            matches.append(
                ConfiguredCodeMatch(
                    code_type=code_type,
                    end_char=match.end(),
                    normalized_value=_normalize_code_match_value(
                        pattern=pattern, raw_value=raw_value
                    ),
                    raw_value=raw_value,
                    start_char=match.start(),
                )
            )

    matches.sort(key=lambda item: (item.start_char, item.end_char, item.code_type))
    deduplicated_matches: list[ConfiguredCodeMatch] = []
    seen: set[tuple[str, int, str, str, int]] = set()

    for code_match in matches:
        match_key = (
            code_match.code_type,
            code_match.end_char,
            code_match.normalized_value,
            code_match.raw_value,
            code_match.start_char,
        )

        if match_key in seen:
            continue

        seen.add(match_key)
        deduplicated_matches.append(code_match)

    return deduplicated_matches


def find_configured_code_matches_in_source_units(
    *,
    code_patterns: Mapping[str, str],
    source_units: Sequence[ConfiguredCodeMatchSourceUnit],
) -> list[ConfiguredCodeMatch]:
    """Find configured code matches without crossing source-unit boundaries.

    Each source unit is scanned independently with `find_configured_code_matches`.
    Unit-local offsets are translated into the caller-defined coordinate space. Matches
    from separate units are intentionally not deduplicated because equal codes in
    different cells or source units represent distinct occurrences.

    Parameters
    ----------
    code_patterns
        Configured source-visible regexes keyed by code type.
    source_units
        Ordered atomic source-text units with offsets in a caller-defined coordinate
        space.

    Returns
    -------
    list[ConfiguredCodeMatch]
        Ordered configured code matches whose offsets use the caller-defined coordinate
        space and whose spans never cross a source-unit boundary.
    """

    matches: list[ConfiguredCodeMatch] = []

    for source_unit in source_units:
        for match in find_configured_code_matches(
            code_patterns=code_patterns, source_text=source_unit.text
        ):
            matches.append(
                ConfiguredCodeMatch(
                    code_type=match.code_type,
                    end_char=source_unit.start_char + match.end_char,
                    normalized_value=match.normalized_value,
                    raw_value=match.raw_value,
                    start_char=source_unit.start_char + match.start_char,
                )
            )

    matches.sort(key=lambda item: (item.start_char, item.end_char, item.code_type))
    return matches


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
    *,
    config: CreateKGConfig,
    document_ir_fp: Path,
    expected_doc_key: str,
    kg_dirs: KGDirs,
) -> KGInputs:
    """Load, validate, and prep KG creation run inputs.

    Parameters
    ----------
    config
        KG creation config with embedded country/document-specific extraction
        attributes.
    document_ir_fp
        Path to the stitched DocumentIR JSON file.
    expected_doc_key
        Document key computed from the configured source PDF.
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
    document_ir = _validate_document_ir(
        document_ir_fp=document_ir_fp, expected_doc_key=expected_doc_key
    )

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


def resolve_candidate_code(
    *,
    code_patterns: Mapping[str, str | re.Pattern[str]],
    expected_code_type: str | None,
    statement_code: str | None,
) -> ResolvedCandidateCode:
    """Resolve one candidate code to exactly one configured code family.

    A statement type's configured `code_type` acts as an explicit constraint. When no
    code type is configured for the statement type, a non-null candidate code must
    match exactly one configured pattern. The returned resolved code type is the
    authoritative input for code-scope construction.

    Configured patterns are source-facing regexes. Each pattern is searched against the
    candidate code, and a match counts only when its normalized text equals the
    normalized complete candidate code. This supports source formatting variants
    without allowing a partial code match to determine the code family.

    Parameters
    ----------
    code_patterns
        Configured source-facing code regexes keyed by code type. Values may be raw
        pattern strings or compiled regular expressions.
    expected_code_type
        Optional code type declared for the candidate's canonical statement type.
    statement_code
        Candidate statement code, or `None` for an uncoded candidate.

    Returns
    -------
    ResolvedCandidateCode
        Normalized statement code and uniquely resolved configured code type. Both
        fields are `None` for an uncoded candidate.

    Raises
    ------
    ValueError
        If a configured pattern is invalid, the expected code type is unavailable or
        does not match, or an untyped statement code matches zero or multiple code
        types.
    """

    statement_code_clean = str(statement_code or "").strip()
    normalized_statement_code = normalize_code(statement_code_clean)

    if normalized_statement_code is None:
        return ResolvedCandidateCode(
            normalized_statement_code=None, resolved_code_type=None
        )

    compiled_patterns = _compile_code_patterns(code_patterns)

    matching_code_types: list[str] = []

    for code_type, pattern in compiled_patterns.items():
        for match in pattern.finditer(statement_code_clean):
            if normalize_code(match.group(0)) == normalized_statement_code:
                matching_code_types.append(code_type)
                break

    resolved_code_type = _resolve_code_type(
        compiled_patterns=compiled_patterns,
        expected_code_type=expected_code_type,
        matching_code_types=matching_code_types,
        statement_code=statement_code,
    )

    return ResolvedCandidateCode(
        normalized_statement_code=normalized_statement_code,
        resolved_code_type=resolved_code_type,
    )


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
