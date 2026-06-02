"""This module contains utility functions for document Intermediate Representations."""

# Standard Library
import re
import unicodedata
import uuid

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Third Party Library
from loguru import logger

# Package Library
from skg.document_ir.schemas import DocumentIR, DocumentPageMeta, Segment
from skg.page_ir_extraction.schemas import (
    Block,
    PageIR,
    Table,
    TableRow,
    TextUnit,
)
from skg.page_ir_verification.utils import (
    EdgeVerdictRecord,
    load_page_irs_from_verification,
    load_verification_verdicts,
)
from skg.regexes import (
    CAPTION_IDENTIFIER_RE,
    FIGURE_PREFIX_PATTERN,
    TABLE_PREFIX_PATTERN,
    WS_RE,
)
from skg.schemas import ExtractionConfig, RunCtx, StitchingConfig
from skg.utils.constants import BlockType
from skg.utils.general import make_dir, write_to_json

ItemKey = tuple[int, int]


@dataclass(frozen=True)
class DocumentIRDirs:
    """Dataclass for document IR directories."""

    root: Path


@dataclass(frozen=True)
class ParsedCaptionCode:
    """Parsed representation of a leading table/figure caption label.

    Parameters
    ----------
    identifier_normalized
        A comparison-safe normalization of `identifier_raw`.
    identifier_raw
        The identifier exactly as it appeared in the source label.
    kind
        The normalized caption kind: "table" or "figure".
    label_raw
        The full matched label surface form, preserving the original prefix and
        identifier.
    prefix_raw
        The matched caption prefix exactly as it appeared in the source text.
    """

    identifier_normalized: str
    identifier_raw: str
    kind: str
    label_raw: str
    prefix_raw: str


def _create_document_ir_dirs(*, output_dir: Path) -> DocumentIRDirs:
    """Create document IR directories for a given stitching run.

    Parameters
    ----------
    output_dir
        The output directory root.

    Returns
    -------
    DocumentIRDirs
        The created document IR directories.
    """

    root = output_dir

    for p in [root]:
        make_dir(p)

    return DocumentIRDirs(root=root)


def _normalize_caption_identifier(identifier: str) -> str:
    """Normalize a caption identifier for comparisons.

    Parameters
    ----------
    identifier
        The raw identifier token.

    Returns
    -------
    str
        The normalized identifier.
    """

    normalized_identifier = re.sub(r"\s+", "", identifier or "")
    return normalized_identifier.casefold()


def _strip_caption_trailing_separator(text: str) -> str:
    """Strip trailing caption punctuation from a matched label fragment.

    Parameters
    ----------
    text
        The matched label fragment.

    Returns
    -------
    str
        The cleaned label fragment.
    """

    return re.sub(r"[\s:.\-–—]+$", "", text).strip()


def assert_page_items_consumed_exactly_once(
    *,
    items_mapping: dict[int, list[tuple[int, Block | Table]]],
    segments: list[Segment],
) -> None:
    """Validate that every normalized PageIR item is consumed exactly once by segments.
    Expected universe of segments is derived from `items_mapping` (i.e.,
    post-normalization, with artifacts filtered if keep_artifacts=False upstream).

    Parameters
    ----------
    items_mapping
        Mapping of page_index to list of (item_index, item) tuples after normalization.
    segments
        The list of segments to validate.

    Raises
    ------
    ValueError
        Any of:
            - Missing items (expected but not present in any
                segment.segment_provenance).
            - Extra items (present in segment.segment_provenance but not expected).
            - Duplicate consumption (same (page_index,item_index) appears in > 1
                segment.segment_provenance).
    """

    expected: set[ItemKey] = {
        (page_index, orig_item_index)
        for page_index, items in items_mapping.items()
        for (orig_item_index, _) in items
    }
    used_by: dict[ItemKey, list[str]] = defaultdict(list)

    for segment in segments:
        for provenance in segment.segment_provenance:
            k: ItemKey = (provenance.page_index, provenance.item_index)
            used_by[k].append(segment.segment_id)

    seen = set(used_by.keys())
    missing = sorted(expected - seen)
    extra = sorted(seen - expected)
    dupes = sorted([k for k, seg_keys in used_by.items() if len(seg_keys) > 1])

    if not (missing or extra or dupes):
        return

    def _fmt_keys(keys: list[ItemKey], limit: int = 25) -> str:
        """Format a list of (page_index, item_index) keys for display.

        Parameters
        ----------
        keys
            The list of keys.
        limit
            The maximum number of keys to show.

        Returns
        -------
        str
            The formatted string.
        """

        if not keys:
            return "[]"

        head = ", ".join([f"(p={p}, i={i})" for p, i in keys[:limit]])
        tail = "" if len(keys) <= limit else f", ... (+{len(keys) - limit} more)"

        return f"[{head}{tail}]"

    # For dupes, show which segments consumed them.
    dupe_details = ""

    if dupes:
        lines = [f"  {k} -> {used_by[k]}" for k in dupes[:10]]
        remaining = len(dupes) - 10

        if remaining > 0:
            lines.append(f"  ... (+{remaining} more)")

        joined_lines = "\n".join(lines)
        dupe_details = f"\nDuplicate details ...\n{joined_lines}"

    raise ValueError(
        f"Integrity check failed: normalized PageIR items were not consumed exactly once.\n"
        f"Missing (expected but not consumed): {_fmt_keys(missing)}\n"
        f"Extra (consumed but not expected): {_fmt_keys(extra)}\n"
        f"Duplicates (consumed >1 time): {_fmt_keys(dupes)}"
        f"{dupe_details}"
    )


def canonicalize_local_code_for_compare(local_code: Optional[str]) -> Optional[str]:
    """Canonicalize a local code into a comparison-safe key.

    This function preserves the existing raw local_code storage policy while making
    comparisons robust to recognized caption-prefix variants. For example, recognized
    table prefixes such as `Table`, `Tab.`, and `Tableau` all normalize through
    `extract_table_or_figure_local_code` before whitespace and case normalization are
    applied. Unrecognized codes still fall back to plain local-code normalization.

    Parameters
    ----------
    local_code
        The raw local code, caption label, or caption-leading text to canonicalize.

    Returns
    -------
    Optional[str]
        A comparison-safe local-code key, or None if the input is empty.
    """

    if not local_code or not local_code.strip():
        return None

    canonical_local_code = extract_table_or_figure_local_code(text=local_code)
    local_code_for_compare = canonical_local_code or local_code
    return normalize_local_code(local_code_for_compare)


def compatible_kinds_for_stitch(
    *, next_item: Block | Table, prev_item: Block | Table
) -> bool:
    """Return True if two items are stitch-compatible.

    NB: This function is a conservative gate for cross-page continuation. It allows
    exact type matches for like-with-like stitching (for example, table-to-table or
    figure-to-figure), plus a narrow paragraph/list fallback because extraction can
    legitimately flip between those two text block types across a page break.

    Rules:

    1. If either item is not a Block, then both must be Table to stitch.
    2. Headings never stitch.
    3. Caption-to-caption stitching is only allowed if they have the same anchored
        table/figure code, either from local_code or parsed text.
    4. Otherwise, exact block-type matches are allowed.
    5. Paragraph/list is also allowed as a fallback pair, because extraction can flip
        those across pages.

    Example:

    Allowed:
        Table <-> Table
        Paragraph <-> Paragraph
        Paragraph <-> List
        Caption("Table 4") <-> Caption("Table 4")

    Blocked:
        Heading <-> anything
        Caption("Table 4") <-> Caption("Figure 4")
        Table <-> Figure

    Parameters
    ----------
    next_item
        The next item.
    prev_item
        The previous item.

    Returns
    -------
    bool
        True if the two items are stitch-compatible.
    """

    # If either isn't a Block, they must both be Tables to stitch.
    if not (isinstance(prev_item, Block) and isinstance(next_item, Block)):
        return isinstance(next_item, Table) and isinstance(prev_item, Table)

    prev_type = prev_item.block_type
    next_type = next_item.block_type

    # Headings should never stitch.
    if BlockType.HEADING in (prev_type, next_type):
        return False

    # Allow CAPTION <-> CAPTION *only* when strongly anchored.
    if prev_type == BlockType.CAPTION and next_type == BlockType.CAPTION:
        next_code = canonicalize_local_code_for_compare(next_item.local_code)
        prev_code = canonicalize_local_code_for_compare(prev_item.local_code)

        # Short-circuit early to avoid text extraction overhead if possible.
        if next_code and prev_code and next_code == prev_code:
            return True

        next_text = (
            next_item.text.text.strip() if isinstance(next_item.text, TextUnit) else ""
        )
        prev_text = (
            prev_item.text.text.strip() if isinstance(prev_item.text, TextUnit) else ""
        )

        next_ext_code = canonicalize_local_code_for_compare(next_text)
        prev_ext_code = canonicalize_local_code_for_compare(prev_text)

        # Return the boolean result of the text match directly.
        return bool(next_ext_code and prev_ext_code and next_ext_code == prev_ext_code)

    # Final catch-all: Exact type match OR Paragraph/List fallback.
    paragraph_list_types = {BlockType.LIST, BlockType.PARAGRAPH}
    return (prev_type == next_type) or (
        prev_type in paragraph_list_types and next_type in paragraph_list_types
    )


def cross_check_verification_run(
    *,
    computed_doc_key: str,
    expected_doc_key: str,
    extraction_config: ExtractionConfig,
    verified_page_irs_dir: Path,
) -> tuple[dict[tuple[int, int], EdgeVerdictRecord], list[PageIR]]:
    """Cross-check that the verification run matches expected parameters and load
    verified page IRs and their verdicts.

    Parameters
    ----------
    computed_doc_key
        The document key computed from the source PDF bytes by the caller.
    expected_doc_key
        The expected document key (hex string) from the extraction run metadata.
    extraction_config
        The extraction configuration used for the run.
    verified_page_irs_dir
        The directory where verified page IRs are stored.

    Returns
    -------
    tuple[dict[tuple[int, int], EdgeVerdictRecord], list[PageIR]]
        The loaded verdicts and verified page IRs.

    Raises
    ------
    ValueError
        If the computed document key does not match the expected key.
        If no verified PageIRs are found in the verification output directory.
        If any verified PageIR has an invalid page_index (non-integer or negative).
        If the verified PageIRs do not have consecutive page_index values.
    """

    if computed_doc_key != expected_doc_key:
        raise ValueError(
            f"PDF doc_key mismatch.\n"
            f"  PDF provided to stitch_document_ir():   {extraction_config.pdf_fp}\n"
            f"  computed doc_key:                       {computed_doc_key}\n"
            f"  extraction_run.json key:                {expected_doc_key}\n"
            f"You are likely stitching against a different PDF than the one used for "
            f"verification. Pass the same PDF used in the verification step or re-run "
            f"verification."
        )

    verdict_dir = (
        extraction_config.output_dir
        / computed_doc_key
        / "verification"
        / "page_irs_pair_reports"
    )

    # Load and validate verified PageIR JSONs from the verification output directory.
    verified_page_irs = load_page_irs_from_verification(
        doc_key=expected_doc_key, verified_page_irs_dir=verified_page_irs_dir
    )

    if not verified_page_irs:
        raise ValueError(
            "Cannot stitch a DocumentIR from an empty verified-page set. "
            "Expected at least one verified PageIR JSON."
        )

    invalid_page_indices = [
        page_ir.page_index
        for page_ir in verified_page_irs
        if not isinstance(page_ir.page_index, int) or page_ir.page_index < 0
    ]

    if invalid_page_indices:
        raise ValueError(
            f"Every verified PageIR must have a non-negative integer page_index before stitching. "
            f"Got invalid page_index values: {invalid_page_indices}"
        )

    page_index_counts = Counter(page_ir.page_index for page_ir in verified_page_irs)
    duplicate_page_indices = sorted(
        idx for idx, count in page_index_counts.items() if count > 1
    )

    if duplicate_page_indices:
        raise ValueError(
            f"Duplicate page_index values detected in verified PageIRs: "
            f"{duplicate_page_indices}"
        )

    # Load verification verdicts for debugging and linking purposes.
    verdicts = load_verification_verdicts(verdict_dir)
    sorted_page_irs = sorted(verified_page_irs, key=lambda page_ir: page_ir.page_index)

    for current_page_ir, next_page_ir in zip(sorted_page_irs, sorted_page_irs[1:]):
        expected_next_page_index = current_page_ir.page_index + 1

        if next_page_ir.page_index != expected_next_page_index:
            raise ValueError(
                f"Page-break stitching requires consecutive page numbers, but "
                f"received {current_page_ir.page_index}->{next_page_ir.page_index}."
            )

    return verdicts, sorted_page_irs


def extract_table_or_figure_local_code(text: str) -> Optional[str]:
    """Extract a canonical table/figure local code from a label string.

    Parameters
    ----------
    text
        The text to extract from.

    Returns
    -------
    Optional[str]
        A canonicalized local code such as `"Table 2-1"` or `"Figure III"`, or `None`
        when the text does not begin with a recognizable caption label.
    """

    parsed_caption_code = parse_caption_code(text=text)

    if parsed_caption_code is None:
        return None

    canonical_kind = "Table" if parsed_caption_code.kind == "table" else "Figure"
    return f"{canonical_kind} {parsed_caption_code.identifier_raw}"


def normalize_local_code(local_code: Optional[str]) -> Optional[str]:
    """Normalize a local code for comparison.

    Parameters
    ----------
    local_code
        The local code.

    Returns
    -------
    Optional[str]
        The normalized local code, or None if empty.
    """

    normalized_local_code = (
        local_code.strip() if local_code and local_code.strip() else None
    )

    if not normalized_local_code:
        return None

    # Collapse internal whitespace then case-fold.
    normalized_local_code = WS_RE.sub(" ", normalized_local_code.strip())
    return normalized_local_code.casefold()


def normalize_text(text: Optional[str]) -> str:
    """Normalize text for comparisons.

    Parameters
    ----------
    text
        The text to normalize.

    Returns
    -------
    str
        The normalized text.
    """

    if text is None:
        return ""

    # Normalize unicode characters (e.g., standardize accents). NFKC form is usually
    # best for compatibility comparisons.
    text = unicodedata.normalize("NFKC", text)

    # Collapse whitespace, strip, and lowercase.
    return re.sub(r"\s+", " ", text).strip().lower()


def parse_caption_code(text: str) -> Optional[ParsedCaptionCode]:
    """Parse a caption label into structured parts.

    NB: This parser is intentionally strict about the identifier token. It accepts
    common caption-code forms such as `1`, `2.1`, `2-1`, `3/A`, `III`, and `A`, but
    rejects plain words like `leau`. That prevents OCR/layout splits such as
    `Tab leau 3` from being misread as a valid `tab + leau` caption label. The parser
    also supports dotted abbreviations such as `Tab. 3` and `Fig. IV`.

    Parameters
    ----------
    text
        Candidate caption text.

    Returns
    -------
    Optional[ParsedCaptionCode]
        Parsed caption metadata if the text begins with a recognized caption label;
        otherwise None.
    """

    normalized_text = (text or "").strip()

    if not normalized_text:
        return None

    table_match = re.match(
        rf"""
        ^
        \s*
        (?P<prefix>{TABLE_PREFIX_PATTERN})
        (?=
            \s*
            (?:(?:no|n|na)\.?\s*)?
            {CAPTION_IDENTIFIER_RE}
        )
        \s*
        (?:(?:no|n|na)\.?\s*)?
        (?P<identifier>{CAPTION_IDENTIFIER_RE})
        (?:
            \s*(?:[:.\-–—])\s*
        )?
        """,
        normalized_text,
        flags=re.IGNORECASE | re.VERBOSE,
    )

    if table_match is not None:
        identifier_raw = table_match.group("identifier")
        label_raw = _strip_caption_trailing_separator(table_match.group(0))
        return ParsedCaptionCode(
            identifier_normalized=_normalize_caption_identifier(identifier_raw),
            identifier_raw=identifier_raw,
            kind="table",
            label_raw=label_raw,
            prefix_raw=table_match.group("prefix"),
        )

    figure_match = re.match(
        rf"""
        ^
        \s*
        (?P<prefix>{FIGURE_PREFIX_PATTERN})
        (?=
            \s*
            (?:(?:no|n|na)\.?\s*)?
            {CAPTION_IDENTIFIER_RE}
        )
        \s*
        (?:(?:no|n|na)\.?\s*)?
        (?P<identifier>{CAPTION_IDENTIFIER_RE})
        (?:
            \s*(?:[:.\-–—])\s*
        )?
        """,
        normalized_text,
        flags=re.IGNORECASE | re.VERBOSE,
    )

    if figure_match is not None:
        identifier_raw = figure_match.group("identifier")
        label_raw = _strip_caption_trailing_separator(figure_match.group(0))
        return ParsedCaptionCode(
            identifier_normalized=_normalize_caption_identifier(identifier_raw),
            identifier_raw=identifier_raw,
            kind="figure",
            label_raw=label_raw,
            prefix_raw=figure_match.group("prefix"),
        )

    return None


def persist_stitching_run(
    *, config: StitchingConfig, output_dir: Path
) -> tuple[DocumentIRDirs, RunCtx]:
    """Persist stitching run metadata.

    Parameters
    ----------
    config
        The stitching run configuration.
    output_dir
        The output directory for the stitching run results.

    Returns
    -------
    tuple[DocumentIRDirs, RunCtx]
        The created stitching directories and persisted stitching run metadata.
    """

    stitching_dirs = _create_document_ir_dirs(output_dir=output_dir)
    exclude_keys = {"overwrite"}
    stitching_run = RunCtx(
        extra={
            k: v
            for k, v in config.model_dump(mode="json").items()
            if k not in exclude_keys
        },
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(fp=output_dir / "stitching_run.json", json_info=stitching_run)

    logger.info(f"Saving stitching results to: {stitching_dirs.root}")

    return stitching_dirs, stitching_run


def row_signature(row: TableRow) -> tuple[str, ...]:
    """Create a stable signature for a table row based on normalized cell texts.

    Parameters
    ----------
    row
        The table row.

    Returns
    -------
    tuple[str, ...]
        The row signature.
    """

    row_sig: list[str] = []

    for cell in row.cells:
        text_or_none = cell.text
        text = (
            normalize_text(text_or_none.text)
            if isinstance(text_or_none, TextUnit)
            else ""
        )
        row_sig.append(text)

    return tuple(row_sig)


def save_document_ir(
    *,
    doc_key: str,
    document_ir_fp: Path,
    items_mapping: dict[int, list[tuple[int, Block | Table]]],
    link_debug: list[dict[str, Any]],
    links: dict[tuple[int, int], tuple[int, int]],
    page_irs: list[PageIR],
    page_pair_debug: list[dict[str, Any]],
    pdf_name: str,
    segments: list[Segment],
    stitching_dirs: DocumentIRDirs,
    warnings: list[str],
) -> None:
    """Persist the final DocumentIR and stitch report.

    Parameters
    ----------
    doc_key
        Deterministic hash key of the PDF bytes (SHA-256 hex).
    document_ir_fp
        The output file path for the DocumentIR JSON.
    items_mapping
        Mapping of page_index to normalized retained items after artifact filtering and
        page-level normalization.
    link_debug
        List of per-link debug info.
    links
        Forward links for items that continue across page breaks.
    page_irs
        The list of PageIRs.
    page_pair_debug
        List of per-page-pair debug info.
    pdf_name
        The PDF file name.
    segments
        The list of stitched segments.
    stitching_dirs
        The stitching directories.
    warnings
        A list of warning messages.

    Raises
    ------
    ValueError
        If page_irs is empty.
        If any PageIR in page_irs has an invalid page_index (non-integer or negative).
    """

    if not page_irs:
        raise ValueError(
            "Cannot save a DocumentIR from an empty verified-page set. "
            "Expected at least one stitched PageIR."
        )

    # Write DocumentIR to file.
    first_page = page_irs[0]
    pages_meta: list[DocumentPageMeta] = []

    invalid_page_indices = [
        page_ir.page_index
        for page_ir in page_irs
        if not isinstance(page_ir.page_index, int) or page_ir.page_index < 0
    ]

    if invalid_page_indices:
        raise ValueError(
            f"DocumentIR serialization requires every PageIR to have a non-negative integer page_index. "
            f"Got invalid page_index values: {invalid_page_indices}"
        )

    for page_ir in page_irs:
        pages_meta.append(
            DocumentPageMeta(
                coord_space=page_ir.coord_space,
                dpi=page_ir.dpi,
                image_height=page_ir.image_height,
                image_width=page_ir.image_width,
                is_blank=(len(items_mapping.get(page_ir.page_index, [])) == 0),
                page_index=page_ir.page_index,
            )
        )

    # Warn if pages have heterogeneous dimensions so downstream consumers use per-page
    # metadata from DocumentIR.pages[i] rather than assuming the first page's
    # dimensions apply everywhere.
    unique_dims = {(pm.image_width, pm.image_height) for pm in pages_meta}

    if len(unique_dims) > 1:
        warnings.append(
            f"Heterogeneous page dimensions detected ({len(unique_dims)} distinct sizes): "
            f"{sorted(unique_dims)}. Use DocumentIR.pages[i].image_width / "
            f"DocumentIR.pages[i].image_height for per-page bbox interpretation."
        )

    # Check for page index gaps before constructing DocumentIR (so warnings are
    # included in the serialized output).
    page_indices = sorted({p.page_index for p in page_irs if p.page_index is not None})

    if page_indices:
        expected = set(range(page_indices[0], page_indices[-1] + 1))
        missing = sorted(expected - set(page_indices))
        if missing:
            warnings.append(
                f"PageIR coverage has gaps: missing page_index values {missing}. "
                f"This may indicate omitted blank pages or extraction failures."
            )

    document_ir = DocumentIR(
        coord_space=first_page.coord_space,
        doc_key=doc_key,
        dpi=first_page.dpi,
        page_count=len(page_irs),
        pages=pages_meta,
        pdf_name=pdf_name,
        segments=segments,
        warnings=warnings,
    )

    write_to_json(fp=document_ir_fp, json_info=document_ir)

    # Write a stitch report JSON artifact.
    stitch_report_fp = stitching_dirs.root / "stitch_report.json"
    table_segments_summary: list[dict[str, Any]] = []

    for segment in segments:
        if segment.kind != "table":
            continue

        pages = [sl.page_index for sl in segment.slices]
        table_segments_summary.append(
            {
                "segment_id": segment.segment_id,
                "local_code": segment.local_code,
                "page_start": min(pages) if pages else None,
                "page_end": max(pages) if pages else None,
                "slice_count": len(segment.slices),
                "header_row_count": segment.header_row_count,
                "slices": [
                    {
                        "page_index": sl.page_index,
                        "item_index": sl.item_index,
                        "boundary": sl.boundary.value,
                        "repeats_header": sl.repeats_header,
                        "dropped_header_rows": sl.dropped_header_rows,
                    }
                    for sl in segment.slices
                ],
            }
        )

    table_segments_summary.sort(
        key=lambda x: (
            x["page_start"] if x["page_start"] is not None else 10**9,
            x["segment_id"],
        )
    )
    stitch_report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "block_type_counts": dict(
            Counter(
                [
                    s.block_type.value
                    for s in segments
                    if getattr(s, "kind", None) == "block"
                ]
            )
        ),
        "doc_key": doc_key,
        "link_count": len(links),
        "link_debug": link_debug,
        "page_count": len(page_irs),
        "page_pair_debug": page_pair_debug,
        "pdf_name": pdf_name,
        "segment_kind_counts": dict(Counter([s.kind for s in segments])),
        "table_segments": table_segments_summary,
        "warnings": warnings,
    }

    write_to_json(fp=stitch_report_fp, json_info=stitch_report)
