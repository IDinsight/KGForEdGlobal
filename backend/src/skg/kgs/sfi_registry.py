"""This module contains functionalities for building a global registry of extracted SFI
candidates.

It flattens validated window-local SFI extraction results into document-level candidate
records, computes lightweight code/text keys, and emits possible duplicate buckets for
later LLM-assisted merge review.
"""

# Standard Library
import hashlib
import re

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

# Third Party Library
from loguru import logger

# Package Library
from skg.document_ir.schemas import TableSegment
from skg.kgs.schemas import (
    ExtractionWindow,
    SFICandidate,
    SFIExtractionResult,
    SFIRegistryArtifact,
    SFIRegistryCandidate,
    SFIRegistryDuplicateBucket,
    SFIRegistrySummary,
    SFIRegistryWarning,
)
from skg.kgs.utils import normalize_code, normalize_text
from skg.kgs.validators import verify_sfi_extraction_integrity
from skg.page_ir_extraction.validators import QualityError
from skg.schemas import CreateKGConfig, normalize_controlled_value_key
from skg.utils.general import make_dir, write_to_json


@dataclass(frozen=True)
class _SourceOrderParentMatch:
    """Source-order match for one controlled parent value."""

    candidate_index: int
    canonical_statement_value_key: Optional[str]
    table_header_indexes: tuple[int, ...]
    table_row_indexes: tuple[int, ...]
    window_index: int


@dataclass(frozen=True)
class _StatementValuePolicy:
    """Controlled value canonicalization policy for one statement type."""

    alias_to_canonical: dict[str, str]
    controlled_value_scope: str
    controlled_value_scope_parent_statement_types: tuple[str, ...]
    controlled_value_scope_resolution_statement_types: tuple[str, ...]


def _build_block_source_context(block: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Extract context labels and key parts from a block payload.

    Parameters
    ----------
    block
        Block payload from an extraction window.

    Returns
    -------
    tuple[list[str], list[str]]
        Context labels and key parts derived from the block.
    """

    labels: list[str] = []
    block_type = str(block.get("block_type") or "").strip()
    local_code = str(block.get("local_code") or "").strip()
    section_texts = _extract_section_texts(block.get("section_path") or [])

    # DocumentIR section paths can be cumulative. Emit block context labels
    # recent/local-first so label truncation preserves the nearest section evidence
    # instead of stale broad headings from earlier in the document. Keep key_parts in
    # source path order below to avoid unnecessary source-context-key churn.
    for section_label in reversed(section_texts):
        labels.append(f"section:{section_label}")

    if block_type:
        labels.append(f"block_type:{block_type}")

    if local_code:
        labels.append(f"block_local_code:{local_code}")

    key_parts = [
        f"block_type:{block_type}",
        f"block_local_code:{local_code}",
        "section_path:" + normalize_text(" > ".join(section_texts)),
    ]
    return labels, key_parts


def _build_candidate_source_context(
    *, candidate: SFICandidate, extraction_window: ExtractionWindow
) -> tuple[str, list[str]]:
    """Build source-derived context for one registry candidate.

    The context is intentionally derived only from the persisted extraction window and
    the candidate's source references. It gives no-code candidates a stable source
    scope for duplicate bucketing so repeated labels such as grade, strand, sub-strand,
    topic, week, or palier headings are not bucketed by label alone across unrelated
    hierarchy contexts.

    Parameters
    ----------
    candidate
        Window-local SFI candidate being flattened into the registry.
    extraction_window
        Source extraction window that produced the candidate.

    Returns
    -------
    tuple[str, list[str]]
        A deterministic context key and compact human-readable context labels.
    """

    labels: list[str] = []
    key_parts: list[str] = [
        extraction_window.doc_key,
        extraction_window.segment_kind,
        ",".join(extraction_window.source_segment_ids),
    ]

    if extraction_window.block is not None:
        block_labels, block_key_parts = _build_block_source_context(
            extraction_window.block
        )
        labels.extend(block_labels)
        key_parts.extend(block_key_parts)

    if extraction_window.table is not None:
        table_labels, table_key_parts = _build_table_source_context(
            candidate=candidate, table=extraction_window.table
        )
        labels.extend(table_labels)
        key_parts.extend(table_key_parts)

    if not labels:
        labels.append(f"window:{extraction_window.window_index}")

    context_basis = "|".join(normalize_text(part) for part in key_parts if part)
    source_context_key = hashlib.sha256(context_basis.encode("utf-8")).hexdigest()[:32]
    return source_context_key, _unique_limited(labels, limit=12)


def _build_canonical_statement_scope_key(
    *,
    candidate: SFICandidate,
    canonical_statement_value_key: Optional[str],
    source_context_key: str,
    statement_value_policies: dict[str, _StatementValuePolicy],
) -> Optional[str]:
    """Build the configured deduplication scope for a canonical value.

    Parameters
    ----------
    canonical_statement_value_key
        Normalized canonical controlled value key for the candidate, if any.
    candidate
        Window-local candidate being canonicalized.
    source_context_key
        Deterministic source-derived context key already computed for the candidate.
    statement_value_policies
        Controlled value policies keyed by statement_type.

    Returns
    -------
    Optional[str]
        Controlled-value scope key, or None when the candidate has no canonical value.
    """

    policy = statement_value_policies.get(candidate.statement_type)

    if not canonical_statement_value_key or policy is None:
        return None

    fallback = f"source_context:{source_context_key}"

    if policy.controlled_value_scope == "document":
        return "document"

    if policy.controlled_value_scope == "source_context":
        return fallback

    # Cumulative section-path labels can retain stale organizers across hierarchy
    # boundaries. Nearest-parent scopes are therefore resolved only in the later
    # source-order pass, where broader parent occurrences can bound narrower searches.
    return fallback


def _build_canonical_statement_value(
    *,
    candidate: SFICandidate,
    statement_value_policies: dict[str, _StatementValuePolicy],
) -> tuple[Optional[str], Optional[str]]:
    """Canonicalize a candidate description/source_text with configured values.

    Parameters
    ----------
    candidate
        Window-local candidate to canonicalize.
    statement_value_policies
        Controlled value policies keyed by statement_type.

    Returns
    -------
    tuple[Optional[str], Optional[str]]
        Canonical controlled value and its normalized key, when matched.
    """

    policy = statement_value_policies.get(candidate.statement_type)

    if policy is None:
        return None, None

    for value in [candidate.description, candidate.source_text]:
        canonical_value = _match_controlled_value(
            allow_contained=False, policy=policy, value=value
        )

        if canonical_value:
            return canonical_value, normalize_controlled_value_key(canonical_value)

    return None, None


def _build_duplicate_buckets(
    candidates: Sequence[SFIRegistryCandidate],
) -> list[SFIRegistryDuplicateBucket]:
    """Build possible duplicate buckets by code, description text, and source text.

    Parameters
    ----------
    candidates
        Flattened registry candidates.

    Returns
    -------
    list[SFIRegistryDuplicateBucket]
        Possible duplicate buckets with at least two candidates each.
    """

    bucket_maps: dict[str, dict[str, list[SFIRegistryCandidate]]] = {
        "code": defaultdict(list),
        "description_text": defaultdict(list),
        "source_text": defaultdict(list),
    }

    for candidate in candidates:
        if candidate.code_bucket_key:
            bucket_maps["code"][candidate.code_bucket_key].append(candidate)

        bucket_maps["description_text"][candidate.text_bucket_key].append(candidate)
        bucket_maps["source_text"][candidate.source_text_bucket_key].append(candidate)

    evidence_strength_map = {
        "code": "strong_signal",
        "description_text": "medium_signal",
        "source_text": "weak_signal",
    }
    buckets: list[SFIRegistryDuplicateBucket] = []

    # Iterate in the exact order required for the final return.
    for bucket_type in ["code", "description_text", "source_text"]:
        for bucket_key, bucket_candidates in sorted(bucket_maps[bucket_type].items()):
            if len(bucket_candidates) < 2:
                continue

            # Sort candidates by ID to maintain deterministic order.
            bucket_candidates.sort(key=lambda c: c.registry_candidate_id)
            candidate_ids = [c.registry_candidate_id for c in bucket_candidates]

            buckets.append(
                SFIRegistryDuplicateBucket(
                    bucket_id=_deterministic_bucket_id(
                        bucket_key=bucket_key, bucket_type=bucket_type
                    ),
                    bucket_key=bucket_key,
                    bucket_type=bucket_type,
                    candidate_count=len(candidate_ids),
                    description_examples=_unique_limited(
                        [c.description for c in bucket_candidates], limit=5
                    ),
                    evidence_strength=evidence_strength_map[bucket_type],
                    merge_policy_hint="review_required",
                    registry_candidate_ids=candidate_ids,
                    statement_types=sorted(
                        {c.statement_type for c in bucket_candidates}
                    ),
                    window_indexes=sorted({c.window_index for c in bucket_candidates}),
                )
            )

    return buckets


def _build_registry_candidate(
    *,
    candidate: SFICandidate,
    code_patterns: dict[str, re.Pattern[str]],
    extraction_window: ExtractionWindow,
    source_window_candidate_index: int,
    statement_type_code_types: dict[str, str],
    statement_value_policies: dict[str, _StatementValuePolicy],
) -> SFIRegistryCandidate:
    """Build one flattened registry candidate from a window-local SFI candidate.

    Parameters
    ----------
    candidate
        Window-local candidate from an SFI extraction result.
    code_patterns
        Compiled curriculum-specific code patterns keyed by code type.
    extraction_window
        Source extraction window that produced the candidate.
    source_window_candidate_index
        0-based candidate position within the extraction result.
    statement_type_code_types
        Mapping from canonical statement_type labels to expected code types.
    statement_value_policies
        Controlled value canonicalization policies keyed by statement_type.

    Returns
    -------
    SFIRegistryCandidate
        Registry candidate with normalized code and literal text bucket keys.
    """

    raw_normalized_statement_code = normalize_code(candidate.statement_code)
    matching_code_types = _get_configured_code_types(
        code_patterns=code_patterns,
        statement_code=candidate.statement_code,
    )
    expected_code_type = statement_type_code_types.get(candidate.statement_type)
    normalized_statement_code = (
        raw_normalized_statement_code
        if matching_code_types
        and (expected_code_type is None or expected_code_type in matching_code_types)
        else None
    )

    source_context_key, source_context_labels = _build_candidate_source_context(
        candidate=candidate, extraction_window=extraction_window
    )
    (
        canonical_statement_value,
        canonical_statement_value_key,
    ) = _build_canonical_statement_value(
        candidate=candidate, statement_value_policies=statement_value_policies
    )
    canonical_statement_scope_key = _build_canonical_statement_scope_key(
        candidate=candidate,
        canonical_statement_value_key=canonical_statement_value_key,
        source_context_key=source_context_key,
        statement_value_policies=statement_value_policies,
    )

    # Generate deterministic temporary registry candidate ID.
    candidate_slug = re.sub(
        r"[^0-9A-Za-z_\-]+", "_", candidate.candidate_id.strip()
    ).strip("_")
    digest = hashlib.sha256(
        f"{extraction_window.window_id}|{candidate.candidate_id}".encode("utf-8")
    ).hexdigest()
    registry_candidate_id = f"w{extraction_window.window_index:04d}:{candidate_slug or 'candidate'}:{digest[:8]}"

    # Create bucket keys for source text and code.
    normalized_description = normalize_text(candidate.description)
    normalized_source_text = normalize_text(candidate.source_text)
    statement_type_key = normalize_text(candidate.statement_type)
    text_bucket_key = _build_text_bucket_key(
        canonical_statement_scope_key=canonical_statement_scope_key,
        canonical_statement_value_key=canonical_statement_value_key,
        normalized_statement_code=normalized_statement_code,
        normalized_text=normalized_description,
        source_context_key=source_context_key,
        statement_type_key=statement_type_key,
    )
    source_text_bucket_key = _build_text_bucket_key(
        canonical_statement_scope_key=canonical_statement_scope_key,
        canonical_statement_value_key=canonical_statement_value_key,
        normalized_statement_code=normalized_statement_code,
        normalized_text=normalized_source_text,
        source_context_key=source_context_key,
        statement_type_key=statement_type_key,
    )
    code_bucket_key = (
        _join_bucket_key(statement_type_key, normalized_statement_code)
        if normalized_statement_code
        else None
    )

    return SFIRegistryCandidate(
        candidate_payload=candidate,
        canonical_statement_scope_key=canonical_statement_scope_key,
        canonical_statement_value=canonical_statement_value,
        canonical_statement_value_key=canonical_statement_value_key,
        code_bucket_key=code_bucket_key,
        confidence=candidate.confidence,
        description=candidate.description,
        language=candidate.language,
        normalized_description=normalized_description,
        normalized_source_text=normalized_source_text,
        normalized_statement_code=normalized_statement_code,
        normalized_statement_type=candidate.normalized_statement_type,
        registry_candidate_id=registry_candidate_id,
        source_context_key=source_context_key,
        source_context_labels=source_context_labels,
        source_segment_ids=extraction_window.source_segment_ids,
        source_text=candidate.source_text,
        source_text_bucket_key=source_text_bucket_key,
        source_window_candidate_id=candidate.candidate_id,
        source_window_candidate_index=source_window_candidate_index,
        statement_code=candidate.statement_code,
        statement_type=candidate.statement_type,
        table_header_indexes=candidate.table_header_indexes,
        table_row_indexes=candidate.table_row_indexes,
        text_bucket_key=text_bucket_key,
        window_id=extraction_window.window_id,
        window_index=extraction_window.window_index,
    )


def _build_registry_summary(
    *,
    auxiliary_candidate_count: int,
    candidates: Sequence[SFIRegistryCandidate],
    duplicate_buckets: Sequence[SFIRegistryDuplicateBucket],
    extraction_window_count: int,
    warnings: Sequence[SFIRegistryWarning],
) -> SFIRegistrySummary:
    """Build aggregate counts for the candidate registry artifact.

    Parameters
    ----------
    auxiliary_candidate_count
        Total auxiliary candidates observed in the extraction results.
    candidates
        Flattened registry candidates.
    duplicate_buckets
        Possible duplicate buckets produced for later merge review.
    extraction_window_count
        Number of extraction windows aligned with the extraction results.
    warnings
        Lightweight non-fatal registry warnings.

    Returns
    -------
    SFIRegistrySummary
        Summary counts for the candidate registry artifact.
    """

    language_counts: Counter[str] = Counter()
    normalized_counts: Counter[str] = Counter()
    statement_type_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()

    for candidate in candidates:
        language_counts[candidate.language] += 1
        normalized_counts[candidate.normalized_statement_type] += 1
        statement_type_counts[candidate.statement_type] += 1

    for warning in warnings:
        warning_counts[warning.warning_type] += 1

    largest_duplicate_buckets = [
        {
            "bucket_id": bucket.bucket_id,
            "bucket_type": bucket.bucket_type,
            "candidate_count": bucket.candidate_count,
            "evidence_strength": bucket.evidence_strength,
            "registry_candidate_ids": bucket.registry_candidate_ids[:10],
        }
        for bucket in sorted(
            duplicate_buckets,
            key=lambda bucket: (
                -bucket.candidate_count,
                bucket.bucket_type,
                bucket.bucket_id,
            ),
        )[:10]
    ]

    return SFIRegistrySummary(
        auxiliary_candidate_count=auxiliary_candidate_count,
        candidate_count=len(candidates),
        candidate_count_by_language=dict(sorted(language_counts.items())),
        candidate_count_by_normalized_statement_type=dict(
            sorted(normalized_counts.items())
        ),
        candidate_count_by_statement_type=dict(sorted(statement_type_counts.items())),
        candidates_with_statement_code=sum(
            1 for candidate in candidates if candidate.statement_code is not None
        ),
        candidates_without_statement_code=sum(
            1 for candidate in candidates if candidate.statement_code is None
        ),
        extraction_window_count=extraction_window_count,
        largest_duplicate_buckets=largest_duplicate_buckets,
        possible_duplicate_bucket_count=len(duplicate_buckets),
        warning_count=len(warnings),
        warning_count_by_type=dict(sorted(warning_counts.items())),
    )


def _build_registry_warnings(
    *,
    candidates: Sequence[SFIRegistryCandidate],
    code_patterns: dict[str, re.Pattern[str]],
    duplicate_buckets: Sequence[SFIRegistryDuplicateBucket],
    statement_type_code_types: dict[str, str],
) -> list[SFIRegistryWarning]:
    """Build non-fatal warnings for candidate review.

    Parameters
    ----------
    candidates
        Flattened registry candidates.
    code_patterns
        Compiled curriculum-specific code patterns keyed by code type.
    duplicate_buckets
        Possible duplicate buckets generated from candidate keys.
    statement_type_code_types
        Mapping from canonical statement_type labels to expected code types.
    statement_value_policies
        Controlled value canonicalization policies keyed by statement_type.

    Returns
    -------
    list[SFIRegistryWarning]
        Non-fatal warnings for review and debugging.
    """

    candidates_by_id = {
        candidate.registry_candidate_id: candidate for candidate in candidates
    }
    warning_signatures: set[tuple[Any, ...]] = set()
    warnings: list[SFIRegistryWarning] = []

    _warn_on_per_candidate_issues(
        candidates=candidates,
        code_patterns=code_patterns,
        statement_type_code_types=statement_type_code_types,
        warning_signatures=warning_signatures,
        warnings=warnings,
    )
    _warn_on_duplicate_buckets(
        candidates_by_id=candidates_by_id,
        duplicate_buckets=duplicate_buckets,
        warning_signatures=warning_signatures,
        warnings=warnings,
    )
    _warn_on_code_across_statement_types(
        candidates=candidates,
        warning_signatures=warning_signatures,
        warnings=warnings,
    )
    _warn_on_text_repeated_within_window(
        candidates=candidates,
        warning_signatures=warning_signatures,
        warnings=warnings,
    )
    _warn_on_text_repeated_across_windows(
        candidates=candidates,
        duplicate_buckets=duplicate_buckets,
        warning_signatures=warning_signatures,
        warnings=warnings,
    )

    return [
        warning.model_copy(update={"warning_id": f"warning_{index:04d}"})
        for index, warning in enumerate(warnings, start=1)
    ]


def _build_scope_part_label(statement_type: str) -> str:
    """Build a generic controlled-scope key label from a statement type.

    Parameters
    ----------
    statement_type
        Source-facing statement type label.

    Returns
    -------
    str
        Lowercase, underscore-separated scope key label.
    """

    label = normalize_controlled_value_key(statement_type).replace(" ", "_")
    return label or "scope"


def _build_statement_value_policies(
    kg_config: CreateKGConfig,
) -> dict[str, _StatementValuePolicy]:
    """Build controlled value lookup tables from runtime configuration.

    Parameters
    ----------
    kg_config
        Runtime KG configuration with statement_type_policy entries.

    Returns
    -------
    dict[str, _StatementValuePolicy]
        Controlled value policies keyed by canonical statement_type.
    """

    policies: dict[str, _StatementValuePolicy] = {}
    for item in kg_config.academic_standards.statement_type_policy:
        alias_to_canonical: dict[str, str] = {}

        for controlled_value in item.controlled_values:
            for alias in [controlled_value.canonical_value, *controlled_value.aliases]:
                alias_key = normalize_controlled_value_key(alias)

                if alias_key:
                    alias_to_canonical[alias_key] = controlled_value.canonical_value

        if not alias_to_canonical:
            continue

        parent_statement_types = tuple(
            item.controlled_value_scope_parent_statement_types
        )
        policies[item.statement_type] = _StatementValuePolicy(
            alias_to_canonical=alias_to_canonical,
            controlled_value_scope=item.controlled_value_scope,
            controlled_value_scope_parent_statement_types=parent_statement_types,
            controlled_value_scope_resolution_statement_types=(
                _order_controlled_scope_parent_statement_types(
                    kg_config=kg_config,
                    parent_statement_types=parent_statement_types,
                )
                if item.controlled_value_scope == "nearest_parent_values"
                else parent_statement_types
            ),
        )

    return policies


def _build_table_header_labels(*, candidate: SFICandidate, table: Any) -> list[str]:
    """Build human-readable context labels for referenced table headers.

    Parameters
    ----------
    candidate
        Window-local SFI candidate referencing specific table headers.
    table
        Table payload from an extraction window.

    Returns
    -------
    list[str]
        Context labels for each referenced header row with visible text.
    """

    labels: list[str] = []

    for header_index in candidate.table_header_indexes:
        if 0 <= header_index < len(table.header_rows):
            header_text = _extract_table_row_text(table.header_rows[header_index])

            if header_text:
                labels.append(
                    f"table_header[{header_index}]:"
                    + _truncate_context_label(header_text)
                )

    return labels


def _build_table_row_labels(*, candidate: SFICandidate, table: Any) -> list[str]:
    """Build human-readable context labels for referenced table data rows.

    Parameters
    ----------
    candidate
        Window-local SFI candidate referencing specific table rows.
    table
        Table payload from an extraction window.

    Returns
    -------
    list[str]
        Context labels for each referenced data row with visible text.
    """

    labels: list[str] = []
    rows_by_index = dict(zip(table.row_indexes, table.rows))

    for row_index in candidate.table_row_indexes:
        row = rows_by_index.get(row_index)

        if row is None:
            continue

        row_text = _extract_table_row_text(row)

        if row_text:
            labels.append(
                f"table_row[{row_index}]:" + _truncate_context_label(row_text)
            )

    return labels


def _build_table_source_context(
    *, candidate: SFICandidate, table: TableSegment
) -> tuple[list[str], list[str]]:
    """Extract context labels and key parts from a table payload.

    Parameters
    ----------
    candidate
        Window-local SFI candidate referencing specific table rows and headers.
    table
        Table payload from an extraction window.

    Returns
    -------
    tuple[list[str], list[str]]
        Context labels and key parts derived from the table.
    """

    labels: list[str] = []
    columns_signature = str(table.columns_signature or "").strip()
    local_code = str(table.local_code or "").strip()
    row_indexes = ",".join(str(index) for index in candidate.table_row_indexes)
    header_indexes = ",".join(str(index) for index in candidate.table_header_indexes)

    # Keep candidate-local source evidence before broader table metadata so generic
    # nearest-value matching selects the most relevant controlled scope.
    labels.extend(_build_table_row_labels(candidate=candidate, table=table))
    labels.extend(_build_table_header_labels(candidate=candidate, table=table))

    if local_code:
        labels.append(f"table_local_code:{local_code}")

    if columns_signature:
        labels.append("table_columns:" + _truncate_context_label(columns_signature))

    key_parts = [
        f"table_columns:{columns_signature}",
        f"table_header_indexes:{header_indexes}",
        f"table_local_code:{local_code}",
        f"table_row_indexes:{row_indexes}",
    ]
    return labels, key_parts


def _build_text_bucket_key(
    *,
    canonical_statement_scope_key: Optional[str],
    canonical_statement_value_key: Optional[str],
    normalized_statement_code: Optional[str],
    normalized_text: str,
    source_context_key: str,
    statement_type_key: str,
) -> str:
    """Build a duplicate bucket key for candidate text.

    Coded candidates retain the text-bucket behavior because official codes provide
    stronger identity evidence and text buckets are only secondary review signals.
    No-code candidates with configured controlled values use the canonical value and
    configured scope so source-visible punctuation variants such as `PRIMARY THREE` and
    `PRIMARY: THREE` enter the same review neighborhood. Other no-code candidates
    remain scoped by source context so repeated labels are not treated as possible
    duplicates solely because their visible text matches.

    Parameters
    ----------
    canonical_statement_scope_key
        Controlled-value deduplication scope key, when configured for this candidate.
    canonical_statement_value_key
        Normalized canonical controlled value key, when configured for this candidate.
    normalized_statement_code
        Registry-normalized official statement code, when one was accepted.
    normalized_text
        Registry-normalized description or source-text value.
    source_context_key
        Deterministic source-derived context key for the candidate.
    statement_type_key
        Registry-normalized candidate statement type.

    Returns
    -------
    str
        Context-aware text duplicate bucket key.
    """

    if normalized_statement_code:
        return _join_bucket_key(statement_type_key, normalized_text)

    if canonical_statement_scope_key and canonical_statement_value_key:
        return _join_bucket_key(
            statement_type_key,
            canonical_statement_scope_key,
            canonical_statement_value_key,
        )

    return _join_bucket_key(statement_type_key, source_context_key, normalized_text)


def _deterministic_bucket_id(*, bucket_key: str, bucket_type: str) -> str:
    """Create a deterministic possible-duplicate bucket ID.

    Parameters
    ----------
    bucket_key
        Normalized bucket key.
    bucket_type
        Bucket type label.

    Returns
    -------
    str
        Deterministic bucket ID.
    """

    digest = hashlib.sha256(f"{bucket_type}|{bucket_key}".encode("utf-8")).hexdigest()
    return f"bucket_{bucket_type}_{digest[:16]}"


def _extract_section_texts(section_path: Sequence[Any]) -> list[str]:
    """Extract and truncate visible text labels from a block section path.

    Parameters
    ----------
    section_path
        Section path entries from a block payload.  Each entry may be a dict
        with a "text" key or a plain string.

    Returns
    -------
    list[str]
        Truncated section labels in original path order, excluding empty entries.
        Callers may reverse this list when they need recent/local-first labels.
    """

    section_texts: list[str] = []

    for section_ref in section_path:
        if isinstance(section_ref, dict):
            section_text = str(section_ref.get("text") or "").strip()
        else:
            section_text = str(section_ref or "").strip()

        if not section_text:
            continue

        section_texts.append(_truncate_context_label(section_text))

    return section_texts


def _extract_table_row_text(row: dict[str, Any]) -> str:
    """Extract visible text from a compact table row payload.

    Parameters
    ----------
    row
        Table row payload from an extraction window table payload.

    Returns
    -------
    str
        Visible row text joined in cell order.
    """

    cell_texts: list[str] = []

    for cell in row.get("cells") or []:
        if not isinstance(cell, dict):
            continue

        text_unit = cell.get("text") or {}

        if isinstance(text_unit, dict):
            cell_text = str(text_unit.get("text") or "").strip()
        else:
            cell_text = str(text_unit or "").strip()

        if cell_text:
            cell_texts.append(cell_text)

    return " | ".join(cell_texts)


def _find_bucket_id(
    *,
    bucket_key: str,
    bucket_type: str,
    duplicate_buckets: Sequence[SFIRegistryDuplicateBucket],
) -> Optional[str]:
    """Find a duplicate bucket ID by bucket key and type.

    Parameters
    ----------
    bucket_key
        Bucket key to find.
    bucket_type
        Bucket type to find.
    duplicate_buckets
        Duplicate buckets to inspect.

    Returns
    -------
    Optional[str]
        Matching bucket ID, if present.
    """

    for bucket in duplicate_buckets:
        if bucket.bucket_key == bucket_key and bucket.bucket_type == bucket_type:
            return bucket.bucket_id

    return None


def _find_configured_code_matches_in_text(
    *, code_patterns: dict[str, re.Pattern[str]], value: str
) -> list[str]:
    """Find configured code-pattern matches in source-visible text.

    Parameters
    ----------
    code_patterns
        Compiled curriculum-specific code patterns keyed by code type.
    value
        Source-visible text to inspect.

    Returns
    -------
    list[str]
        Matched configured code strings, preserving config pattern order.
    """

    matches: list[str] = []
    seen: set[str] = set()

    for pattern in code_patterns.values():
        for match in pattern.finditer(value or ""):
            match_text = match.group(0).strip()
            match_key = normalize_code(match_text)

            if match_key is None or match_key in seen:
                continue

            matches.append(match_text)
            seen.add(match_key)

    return matches


def _get_configured_code_types(
    *, code_patterns: dict[str, re.Pattern[str]], statement_code: Optional[str]
) -> list[str]:
    """Return configured code types matching a candidate statement code.

    Configured code patterns are authored for source-visible code text and are used
    elsewhere as raw-text regexes. This function therefore applies each pattern to the
    raw candidate `statement_code`, normalizes the matched substring, and compares it
    with the normalized full candidate code. This accepts source-visible formatting
    variants without requiring config authors to maintain separate normalized-code
    regexes.

    Parameters
    ----------
    code_patterns
        Compiled curriculum-specific source-text code patterns keyed by code type.
    statement_code
        Candidate statement_code copied from visible source text, or None when absent.

    Returns
    -------
    list[str]
        Configured code type keys whose source-text patterns match the candidate code.
    """

    statement_code_clean = str(statement_code or "").strip()
    normalized_statement_code = normalize_code(statement_code_clean)

    if normalized_statement_code is None:
        return []

    matching_code_types: list[str] = []

    for code_type, pattern in sorted(code_patterns.items()):
        for match in pattern.finditer(statement_code_clean):
            if normalize_code(match.group(0)) == normalized_statement_code:
                matching_code_types.append(code_type)
                break

    return matching_code_types


def _join_bucket_key(*values: Optional[str]) -> str:
    """Join normalized bucket-key parts.

    Parameters
    ----------
    values
        Normalized values to join.

    Returns
    -------
    str
        Stable bucket key.
    """

    return "|".join(str(value or "").strip() for value in values)


def _match_controlled_value(
    *, allow_contained: bool, policy: _StatementValuePolicy, value: str
) -> Optional[str]:
    """Match source-visible text to a configured canonical value.

    Parameters
    ----------
    allow_contained
        Whether aliases may match as complete phrases inside a larger label.
    policy
        Controlled value policy to use for matching.
    value
        Source-visible text, source context label, or candidate description.

    Returns
    -------
    Optional[str]
        Canonical controlled value, when matched.
    """

    value_key = normalize_controlled_value_key(value)

    if not value_key:
        return None

    candidate_keys = (
        value_key,
        normalize_controlled_value_key(_strip_controlled_label_prefixes(value)),
    )
    for candidate_key in candidate_keys:
        exact = policy.alias_to_canonical.get(candidate_key)
        if exact:
            return exact

    if not allow_contained:
        return None

    padded_value_key = f" {value_key} "
    contained_matches = [
        (alias_key, canonical_value)
        for alias_key, canonical_value in policy.alias_to_canonical.items()
        if f" {alias_key} " in padded_value_key
    ]

    if not contained_matches:
        return None

    highest_specificity = max(
        (len(alias_key.split()), len(alias_key)) for alias_key, _ in contained_matches
    )
    most_specific_values = {
        canonical_value
        for alias_key, canonical_value in contained_matches
        if (len(alias_key.split()), len(alias_key)) == highest_specificity
    }

    if len(most_specific_values) != 1:
        return None

    return next(iter(most_specific_values))


def _maybe_append_warning(
    *,
    bucket_id: Optional[str],
    message: str,
    registry_candidate_ids: Sequence[str],
    severity: str,
    warning_signatures: set[tuple[Any, ...]],
    warning_type: str,
    warnings: list[SFIRegistryWarning],
) -> None:
    """Append a warning unless an equivalent warning already exists.

    Parameters
    ----------
    bucket_id
        Optional duplicate bucket ID associated with the warning.
    message
        Human-readable warning message.
    registry_candidate_ids
        Candidate IDs associated with the warning.
    severity
        Warning severity label.
    warning_signatures
        Mutable set of warning signatures already emitted.
    warning_type
        Machine-readable warning type.
    warnings
        Mutable warning accumulator.
    """

    candidate_ids = sorted(set(registry_candidate_ids))
    signature = (bucket_id, tuple(candidate_ids), warning_type)

    if signature in warning_signatures:
        return

    warning_signatures.add(signature)
    warnings.append(
        SFIRegistryWarning(
            bucket_id=bucket_id,
            message=message,
            registry_candidate_ids=list(candidate_ids),
            severity=severity,
            warning_id=f"warning_{len(warnings) + 1:04d}",
            warning_type=warning_type,
        )
    )


def _narrow_source_order_scope_bounds(
    *,
    child_index: int,
    lower_bound: int,
    parent_match: _SourceOrderParentMatch,
    parent_matches: Sequence[_SourceOrderParentMatch],
    upper_bound: int,
) -> Optional[tuple[int, int]]:
    """Narrow one active branch using all visible parent occurrences.

    Every occurrence of the parent statement type participates in branch boundaries,
    including occurrences whose controlled value could not be canonicalized. Repeated
    occurrences of the selected canonical value remain in one branch. A different or
    uncanonicalized occurrence closes the branch so stale recognized parents cannot
    carry across a visible but unresolved hierarchy change.

    Parameters
    ----------
    child_index
        Source-order index of the controlled child candidate.
    lower_bound
        Current inclusive lower branch bound.
    parent_match
        Selected canonical parent occurrence for this hierarchy level.
    parent_matches
        All visible occurrences of this parent statement type inside the current
        branch, including uncanonicalized occurrences.
    upper_bound
        Current inclusive upper branch bound.

    Returns
    -------
    Optional[tuple[int, int]]
        Updated inclusive bounds, or None when the selected occurrence is inconsistent
        with the resulting branch.
    """

    selected_value_key = parent_match.canonical_statement_value_key

    if selected_value_key is None:
        return None

    previous_boundary_matches = [
        match
        for match in parent_matches
        if match.candidate_index < child_index
        and match.canonical_statement_value_key != selected_value_key
    ]
    next_boundary_matches = [
        match
        for match in parent_matches
        if match.candidate_index > child_index
        and match.canonical_statement_value_key != selected_value_key
    ]

    branch_lower_bound = (
        previous_boundary_matches[-1].candidate_index + 1
        if previous_boundary_matches
        else lower_bound
    )
    branch_upper_bound = (
        next_boundary_matches[0].candidate_index - 1
        if next_boundary_matches
        else upper_bound
    )

    if parent_match.candidate_index < child_index:
        preceding_same_value_indexes = [
            match.candidate_index
            for match in parent_matches
            if branch_lower_bound <= match.candidate_index < child_index
            and match.canonical_statement_value_key == selected_value_key
        ]

        if not preceding_same_value_indexes:
            return None

        branch_lower_bound = max(branch_lower_bound, min(preceding_same_value_indexes))

    if not (
        branch_lower_bound <= parent_match.candidate_index <= branch_upper_bound
        and branch_lower_bound <= child_index <= branch_upper_bound
    ):
        return None

    return branch_lower_bound, branch_upper_bound


def _order_controlled_scope_parent_statement_types(
    *, kg_config: CreateKGConfig, parent_statement_types: Sequence[str]
) -> tuple[str, ...]:
    """Order controlled-scope parent types from broadest to narrowest.

    An explicit statement-type hierarchy is authoritative when configured. Otherwise,
    the order of ``statement_type_policy`` defines the hierarchy, matching the runtime
    configuration contract. The direct-parent policy, when available, is used only to
    validate that the requested parent types form one ancestor chain.

    Parameters
    ----------
    kg_config
        Runtime KG configuration containing hierarchy policies.
    parent_statement_types
        Configured parent statement types used in the controlled scope key.

    Returns
    -------
    tuple[str, ...]
        Parent statement types ordered from broadest to narrowest.

    Raises
    ------
    ValueError
        If a configured parent type is absent from the selected hierarchy or the
        requested types do not form one ancestor chain under the direct-parent policy.
    """

    configured_types = tuple(parent_statement_types)

    if len(configured_types) < 2:
        return configured_types

    explicit_hierarchy = tuple(
        kg_config.academic_standards.sfi_has_child_statement_type_hierarchy or []
    )
    policy_hierarchy = tuple(
        item.statement_type
        for item in kg_config.academic_standards.statement_type_policy
    )
    hierarchy = explicit_hierarchy or policy_hierarchy
    hierarchy_indexes = {
        statement_type: index for index, statement_type in enumerate(hierarchy)
    }
    missing_types = sorted(set(configured_types) - set(hierarchy_indexes))

    if missing_types:
        hierarchy_source = (
            "sfi_has_child_statement_type_hierarchy"
            if explicit_hierarchy
            else "statement_type_policy"
        )
        raise ValueError(
            f"Controlled scope parent statement types are missing from "
            f"{hierarchy_source}: {missing_types}."
        )

    ordered_types = tuple(sorted(configured_types, key=hierarchy_indexes.__getitem__))
    parent_policy = (
        kg_config.academic_standards.sfi_has_child_parent_statement_types or {}
    )

    if parent_policy:
        for ancestor_statement_type, descendant_statement_type in zip(
            ordered_types, ordered_types[1:]
        ):
            if not _statement_type_is_ancestor(
                ancestor_statement_type=ancestor_statement_type,
                descendant_statement_type=descendant_statement_type,
                parent_statement_types=parent_policy,
            ):
                raise ValueError(
                    f"Controlled scope parent statement types must form one "
                    f"broad-to-narrow ancestor chain; "
                    f"{ancestor_statement_type!r} is not an ancestor of "
                    f"{descendant_statement_type!r}."
                )

    return ordered_types


def _resolve_canonical_statement_scope_from_source_order(
    *,
    candidate: SFIRegistryCandidate,
    candidate_index: int,
    ordered_candidates: Sequence[SFIRegistryCandidate],
    statement_value_policies: dict[str, _StatementValuePolicy],
) -> Optional[str]:
    """Resolve a controlled-value scope from extracted source-order organizers.

    Parameters
    ----------
    candidate
        Registry candidate whose scope should be resolved.
    candidate_index
        Source-order index for `candidate` in `ordered_candidates`.
    ordered_candidates
        Registry candidates in source order.
    statement_value_policies
        Controlled value policies keyed by statement type.

    Returns
    -------
    Optional[str]
        Corrected canonical statement scope key, or None when the candidate has no
        controlled value.
    """

    policy = statement_value_policies.get(candidate.statement_type)

    if not candidate.canonical_statement_value_key or policy is None:
        return None

    fallback = f"source_context:{candidate.source_context_key}"

    if policy.controlled_value_scope == "document":
        return "document"

    if policy.controlled_value_scope == "source_context":
        return fallback

    if policy.controlled_value_scope != "nearest_parent_values":
        return fallback

    resolved_parent_matches = _resolve_source_order_scope_parent_matches(
        child_index=candidate_index,
        ordered_candidates=ordered_candidates,
        parent_statement_types=(
            policy.controlled_value_scope_resolution_statement_types
        ),
    )

    if resolved_parent_matches is None:
        return fallback

    resolved_matches_by_statement_type = dict(resolved_parent_matches)
    scope_parts: list[str] = []

    for parent_statement_type in policy.controlled_value_scope_parent_statement_types:
        parent_match = resolved_matches_by_statement_type[parent_statement_type]
        scope_parts.append(
            f"{_build_scope_part_label(parent_statement_type)}:"
            f"{parent_match.canonical_statement_value_key}"
        )
    return "|".join(scope_parts) if scope_parts else fallback


def _resolve_source_order_scope_parent_matches(
    *,
    child_index: int,
    ordered_candidates: Sequence[SFIRegistryCandidate],
    parent_statement_types: Sequence[str],
) -> Optional[list[tuple[str, _SourceOrderParentMatch]]]:
    """Resolve one hierarchy-consistent controlled parent chain in source order.

    Parent types are resolved in configured broad-to-narrow order. Each selected parent
    value narrows the active source branch before the next parent type is resolved.
    All visible occurrences of each parent statement type participate in branch
    boundaries, including occurrences whose values are not canonicalized. Table-derived
    children first use an unambiguous canonical parent value from their own table
    window; otherwise the nearest unobstructed preceding parent is used.

    Parameters
    ----------
    child_index
        Source-order index of the controlled child candidate.
    ordered_candidates
        Registry candidates in source order.
    parent_statement_types
        Configured parent statement types in broad-to-narrow resolution order.

    Returns
    -------
    Optional[list[tuple[str, _SourceOrderParentMatch]]]
        Complete hierarchy-consistent parent chain, or None when source order cannot
        safely resolve every configured parent level.
    """

    if child_index < 0 or child_index >= len(ordered_candidates):
        return None

    child_candidate = ordered_candidates[child_index]
    lower_bound = 0
    upper_bound = len(ordered_candidates) - 1
    resolved_matches: list[tuple[str, _SourceOrderParentMatch]] = []

    for parent_statement_type in parent_statement_types:
        parent_matches = [
            _SourceOrderParentMatch(
                candidate_index=parent_index,
                canonical_statement_value_key=(
                    parent_candidate.canonical_statement_value_key
                ),
                table_header_indexes=tuple(parent_candidate.table_header_indexes),
                table_row_indexes=tuple(parent_candidate.table_row_indexes),
                window_index=parent_candidate.window_index,
            )
            for parent_index, parent_candidate in enumerate(ordered_candidates)
            if lower_bound <= parent_index <= upper_bound
            and parent_index != child_index
            and parent_candidate.statement_type == parent_statement_type
        ]
        parent_match = _select_source_order_parent_match(
            child_candidate=child_candidate,
            child_index=child_index,
            parent_matches=parent_matches,
        )

        if parent_match is None:
            return None

        narrowed_bounds = _narrow_source_order_scope_bounds(
            child_index=child_index,
            lower_bound=lower_bound,
            parent_match=parent_match,
            parent_matches=parent_matches,
            upper_bound=upper_bound,
        )

        if narrowed_bounds is None:
            return None

        lower_bound, upper_bound = narrowed_bounds
        resolved_matches.append((parent_statement_type, parent_match))

    return resolved_matches


def _select_source_order_parent_match(
    *,
    child_candidate: SFIRegistryCandidate,
    child_index: int,
    parent_matches: Sequence[_SourceOrderParentMatch],
) -> Optional[_SourceOrderParentMatch]:
    """Select a conservative parent occurrence within one active source branch.

    A table-derived child may use a same-window parent that has affirmative table-scope
    evidence: either the parent is header-derived, or the parent and child cite the
    same raw table row/header. All such applicable occurrences must be canonicalized
    and agree on one value. This supports table-wide header organizers on a different
    raw header row while preventing a later unrelated body-row parent from capturing an
    earlier child merely because both occur in the same extraction window.

    When no affirmative same-table parent is available, the nearest preceding parent
    occurrence is used only if its controlled value is canonicalized. An
    uncanonicalized nearest occurrence blocks older parents and causes source-context
    fallback.

    Parameters
    ----------
    child_candidate
        Controlled child candidate whose scope is being resolved.
    child_index
        Source-order index of the controlled child candidate.
    parent_matches
        All parent-type occurrences already restricted to one active source branch.

    Returns
    -------
    Optional[_SourceOrderParentMatch]
        Selected canonical parent occurrence, or None when the available evidence is
        absent or ambiguous.
    """

    child_is_table_derived = bool(
        child_candidate.table_header_indexes or child_candidate.table_row_indexes
    )

    if child_is_table_derived:
        child_row_indexes = set(child_candidate.table_row_indexes)
        affirmative_table_matches = [
            match
            for match in parent_matches
            if match.window_index == child_candidate.window_index
            and (
                match.table_header_indexes
                or child_row_indexes.intersection(match.table_row_indexes)
            )
        ]

        if affirmative_table_matches:
            canonical_value_keys = {
                match.canonical_statement_value_key
                for match in affirmative_table_matches
            }

            if None in canonical_value_keys or len(canonical_value_keys) != 1:
                return None

            return min(
                affirmative_table_matches,
                key=lambda match: (
                    abs(match.candidate_index - child_index),
                    match.candidate_index,
                ),
            )

    preceding_matches = [
        match for match in parent_matches if match.candidate_index < child_index
    ]

    if not preceding_matches:
        return None

    nearest_preceding_match = preceding_matches[-1]

    if nearest_preceding_match.canonical_statement_value_key is None:
        return None

    return nearest_preceding_match


def _statement_type_is_ancestor(
    *,
    ancestor_statement_type: str,
    descendant_statement_type: str,
    parent_statement_types: dict[str, list[str]],
) -> bool:
    """Check whether one configured statement type is an ancestor of another.

    Parameters
    ----------
    ancestor_statement_type
        Candidate ancestor statement type.
    descendant_statement_type
        Candidate descendant statement type.
    parent_statement_types
        Configured direct-parent statement types keyed by child type.

    Returns
    -------
    bool
        True when the ancestor is reachable through one or more parent links.
    """

    pending = list(parent_statement_types.get(descendant_statement_type, []))
    visited: set[str] = set()

    while pending:
        statement_type = pending.pop()

        if statement_type == ancestor_statement_type:
            return True

        if statement_type in visited:
            continue

        visited.add(statement_type)
        pending.extend(parent_statement_types.get(statement_type, []))

    return False


def _strip_controlled_label_prefixes(value: str) -> str:
    """Remove common source-label prefixes before controlled-value matching.

    Parameters
    ----------
    value
        Source-visible value or source context label.

    Returns
    -------
    str
        Value with non-semantic labeling prefixes removed.
    """

    stripped = str(value or "").strip()
    stripped = re.sub(r"^section\s*:\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(
        r"^(theme|sub[-\s]*theme|subtheme)\s*:\s*", "", stripped, flags=re.IGNORECASE
    )
    return stripped.strip()


def _truncate_context_label(value: str) -> str:
    """Normalize and truncate one human-readable context label.

    Parameters
    ----------
    value
        Raw context label text.

    Returns
    -------
    str
        Single-line context label suitable for registry and review artifacts.
    """

    value_clean = re.sub(r"\s+", " ", str(value or "")).strip()

    if len(value_clean) <= 180:
        return value_clean

    return value_clean[:177].rstrip() + "..."


def _unique_limited(values: Sequence[str], *, limit: int) -> list[str]:
    """Return unique non-empty strings, preserving order and applying a limit.

    Parameters
    ----------
    values
        Raw values.
    limit
        Maximum number of unique values to return.

    Returns
    -------
    list[str]
        Unique strings up to the requested limit.
    """

    output: list[str] = []
    seen: set[str] = set()

    for value in values:
        value_clean = str(value or "").strip()

        if not value_clean or value_clean in seen:
            continue

        output.append(value_clean)
        seen.add(value_clean)

        if len(output) >= limit:
            break

    return output


def _validate_result_window_alignment(
    *,
    extraction_result: SFIExtractionResult,
    extraction_window: ExtractionWindow,
    kg_config: CreateKGConfig,
    result_index: int,
) -> None:
    """Validate that one extraction result aligns to one extraction window.

    Parameters
    ----------
    extraction_result
        SFI extraction result to validate.
    extraction_window
        Extraction window expected at the same position.
    kg_config
        Runtime KG config used for quality validation.
    result_index
        0-based extraction-result position.

    Raises
    ------
    ValueError
        If alignment or current quality validation fails.
    """

    if extraction_result.window_id != extraction_window.window_id:
        raise ValueError(
            f"SFI extraction result {result_index} has window_id "
            f"{extraction_result.window_id!r}, but the matching extraction window has "
            f"window_id {extraction_window.window_id!r}."
        )

    if extraction_result.window_index != extraction_window.window_index:
        raise ValueError(
            f"SFI extraction result {result_index} has window_index "
            f"{extraction_result.window_index!r}, but the matching extraction window "
            f"has window_index {extraction_window.window_index!r}."
        )

    if (
        extraction_result.window_source_segment_ids
        != extraction_window.source_segment_ids
    ):
        raise ValueError(
            f"SFI extraction result {result_index} has source segment IDs "
            f"{extraction_result.window_source_segment_ids!r}, but the matching "
            f"extraction window has source segment IDs "
            f"{extraction_window.source_segment_ids!r}."
        )

    try:
        verify_sfi_extraction_integrity(
            extraction_result=extraction_result,
            kg_config=kg_config,
            window=extraction_window,
        )
    except QualityError as e:
        raise ValueError(
            f"SFI extraction result {result_index} failed current quality validation: {e}"
        ) from e


def _validate_statement_type_code_types(
    kg_config: CreateKGConfig,
) -> tuple[dict[str, re.Pattern[str]], dict[str, str]]:
    """Compile code patterns, build statement-type mappings, and validate.

    Parameters
    ----------
    kg_config
        Runtime KG config containing curriculum-specific policies and patterns.

    Returns
    -------
    tuple[dict[str, re.Pattern[str]], dict[str, str]]
        - Compiled regex patterns keyed by configured code type.
        - Mapping from canonical statement_type labels to configured code_pattern keys.

    Raises
    ------
    ValueError
        If code patterns are invalid, or if a statement_type_policy references a
        missing code_pattern key. Empty code_patterns are allowed for no-code curricula
        when no statement type declares a code_type.
    """

    # 1. Compile code patterns.
    code_patterns: dict[str, re.Pattern[str]] = {}

    for code_type, pattern in kg_config.academic_standards.code_patterns.items():
        code_type_clean = str(code_type or "").strip()
        pattern_clean = str(pattern or "").strip()

        if not code_type_clean:
            raise ValueError("KG config code_patterns contains an empty code type key.")

        if not pattern_clean:
            raise ValueError(
                f"KG config code pattern for code type {code_type_clean!r} is empty."
            )

        try:
            code_patterns[code_type_clean] = re.compile(
                pattern_clean, flags=re.IGNORECASE
            )
        except re.error as e:
            raise ValueError(
                f"KG config code pattern for code type {code_type_clean!r} could "
                f"not be compiled: {pattern_clean!r}."
            ) from e

    # 2. Build statement-type to code-type mapping.
    statement_type_code_types = {
        item.statement_type: item.code_type
        for item in kg_config.academic_standards.statement_type_policy
        if item.code_type
    }

    # 3. Validate references.
    missing_code_types = sorted(
        set(statement_type_code_types.values()) - set(code_patterns)
    )

    if missing_code_types:
        raise ValueError(
            f"statement_type_policy references code_type values that are missing from "
            f"kg_config.academic_standards.code_patterns: {missing_code_types}."
        )

    return code_patterns, statement_type_code_types


def _validate_unique_window_candidate_ids(
    *, extraction_result: SFIExtractionResult, result_index: int
) -> None:
    """Validate that SFI candidate IDs are unique within one extraction result.

    Parameters
    ----------
    extraction_result
        SFI extraction result whose window-local candidate IDs should be unique.
    result_index
        0-based extraction-result position used for error reporting.

    Raises
    ------
    ValueError
        If any `SFICandidate.candidate_id` value appears more than once within the same
        extraction result.
    """

    candidate_id_counts = Counter(
        candidate.candidate_id for candidate in extraction_result.sfi_candidates
    )
    duplicate_candidate_ids = sorted(
        candidate_id for candidate_id, count in candidate_id_counts.items() if count > 1
    )

    if duplicate_candidate_ids:
        raise ValueError(
            f"SFI extraction result {result_index} has duplicate window-local "
            f"candidate_id values: {duplicate_candidate_ids}. Candidate IDs must "
            f"be unique within each extraction result so registry_candidate_id "
            f"values remain globally addressable."
        )


def _warn_on_code_across_statement_types(
    *,
    candidates: Sequence[SFIRegistryCandidate],
    warning_signatures: set[tuple[Any, ...]],
    warnings: list[SFIRegistryWarning],
) -> None:
    """Warn when one normalized statement_code spans multiple statement types.

    Parameters
    ----------
    candidates
        Flattened registry candidates.
    warning_signatures
        Mutable set of warning signatures already emitted.
    warnings
        Mutable warning accumulator.
    """

    candidates_by_code: dict[str, list[SFIRegistryCandidate]] = defaultdict(list)

    for candidate in candidates:
        if candidate.normalized_statement_code:
            candidates_by_code[candidate.normalized_statement_code].append(candidate)

    for normalized_statement_code, code_candidates in sorted(
        candidates_by_code.items()
    ):
        statement_types = {candidate.statement_type for candidate in code_candidates}

        if len(statement_types) > 1:
            _maybe_append_warning(
                bucket_id=None,
                message=(
                    f"Statement code {normalized_statement_code!r} appears across "
                    f"multiple statement types: {sorted(statement_types)}."
                ),
                registry_candidate_ids=[
                    candidate.registry_candidate_id for candidate in code_candidates
                ],
                severity="warning",
                warning_signatures=warning_signatures,
                warning_type="same_code_across_multiple_statement_types",
                warnings=warnings,
            )


def _warn_on_duplicate_buckets(
    *,
    candidates_by_id: dict[str, SFIRegistryCandidate],
    duplicate_buckets: Sequence[SFIRegistryDuplicateBucket],
    warning_signatures: set[tuple[Any, ...]],
    warnings: list[SFIRegistryWarning],
) -> None:
    """Warn on heterogeneous descriptions or languages within duplicate buckets.

    Parameters
    ----------
    candidates_by_id
        Lookup from registry candidate ID to candidate.
    duplicate_buckets
        Possible duplicate buckets generated from candidate keys.
    warning_signatures
        Mutable set of warning signatures already emitted.
    warnings
        Mutable warning accumulator.
    """

    for bucket in duplicate_buckets:
        bucket_candidates = [
            candidates_by_id[candidate_id]
            for candidate_id in bucket.registry_candidate_ids
        ]
        description_keys = {
            candidate.normalized_description for candidate in bucket_candidates
        }
        languages = {candidate.language for candidate in bucket_candidates}

        if bucket.bucket_type == "code" and len(description_keys) > 1:
            _maybe_append_warning(
                bucket_id=bucket.bucket_id,
                message=(
                    "Same statement_type + statement_code bucket contains multiple "
                    "normalized descriptions; review before merge."
                ),
                registry_candidate_ids=bucket.registry_candidate_ids,
                severity="warning",
                warning_signatures=warning_signatures,
                warning_type="same_type_code_different_descriptions",
                warnings=warnings,
            )

        if (
            bucket.bucket_type in {"description_text", "source_text"}
            and len(languages) > 1
        ):
            _maybe_append_warning(
                bucket_id=bucket.bucket_id,
                message=(
                    "Near-duplicate text bucket contains multiple language tags; "
                    "review bilingual or cross-language evidence before merge."
                ),
                registry_candidate_ids=bucket.registry_candidate_ids,
                severity="info",
                warning_signatures=warning_signatures,
                warning_type="language_differs_across_text_bucket",
                warnings=warnings,
            )


def _warn_on_per_candidate_issues(
    *,
    candidates: Sequence[SFIRegistryCandidate],
    code_patterns: dict[str, re.Pattern[str]],
    statement_type_code_types: dict[str, str],
    warning_signatures: set[tuple[Any, ...]],
    warnings: list[SFIRegistryWarning],
) -> None:
    """Warn on candidate-level statement_code anomalies.

    Parameters
    ----------
    candidates
        Flattened registry candidates.
    code_patterns
        Compiled curriculum-specific code patterns keyed by code type.
    statement_type_code_types
        Mapping from canonical statement_type labels to expected code types.
    statement_value_policies
        Controlled value canonicalization policies keyed by statement_type.
    warning_signatures
        Mutable set of warning signatures already emitted.
    warnings
        Mutable warning accumulator.
    """

    for candidate in candidates:
        raw_normalized_statement_code = normalize_code(candidate.statement_code)
        matching_code_types = _get_configured_code_types(
            code_patterns=code_patterns,
            statement_code=candidate.statement_code,
        )
        expected_code_type = statement_type_code_types.get(candidate.statement_type)

        if candidate.statement_code is None and _find_configured_code_matches_in_text(
            code_patterns=code_patterns, value=candidate.source_text
        ):
            _maybe_append_warning(
                bucket_id=None,
                message=(
                    f"Candidate {candidate.registry_candidate_id} has source text "
                    f"matching configured code_patterns but statement_code is null."
                ),
                registry_candidate_ids=[candidate.registry_candidate_id],
                severity="warning",
                warning_signatures=warning_signatures,
                warning_type="configured_code_source_text_with_null_statement_code",
                warnings=warnings,
            )

        if raw_normalized_statement_code is not None and not code_patterns:
            _maybe_append_warning(
                bucket_id=None,
                message=(
                    f"Candidate {candidate.registry_candidate_id} has statement_code "
                    f"{candidate.statement_code!r}, but kg_config.academic_standards."
                    f"code_patterns is empty. The code is preserved on the "
                    f"candidate payload but excluded from code buckets."
                ),
                registry_candidate_ids=[candidate.registry_candidate_id],
                severity="warning",
                warning_signatures=warning_signatures,
                warning_type="statement_code_without_configured_code_patterns",
                warnings=warnings,
            )
            continue

        if raw_normalized_statement_code is not None and not matching_code_types:
            _maybe_append_warning(
                bucket_id=None,
                message=(
                    f"Candidate {candidate.registry_candidate_id} has statement_code "
                    f"{candidate.statement_code!r}, normalized as "
                    f"{raw_normalized_statement_code!r}, but it does not match any "
                    f"configured code_patterns: {sorted(code_patterns)}. The code is "
                    f"preserved on the candidate payload but excluded from code "
                    f"buckets."
                ),
                registry_candidate_ids=[candidate.registry_candidate_id],
                severity="warning",
                warning_signatures=warning_signatures,
                warning_type="statement_code_does_not_match_configured_code_patterns",
                warnings=warnings,
            )
            continue

        if (
            raw_normalized_statement_code is not None
            and expected_code_type is not None
            and expected_code_type not in matching_code_types
        ):
            _maybe_append_warning(
                bucket_id=None,
                message=(
                    f"Candidate {candidate.registry_candidate_id} has statement_type "
                    f"{candidate.statement_type!r}, which expects code_type "
                    f"{expected_code_type!r}, but statement_code "
                    f"{candidate.statement_code!r} matches configured code types "
                    f"{matching_code_types}. The code is preserved on the "
                    f"candidate payload but excluded from code buckets."
                ),
                registry_candidate_ids=[candidate.registry_candidate_id],
                severity="warning",
                warning_signatures=warning_signatures,
                warning_type="statement_code_mismatched_expected_code_type",
                warnings=warnings,
            )
            continue

        if (
            candidate.normalized_statement_code is not None
            and candidate.statement_type not in statement_type_code_types
        ):
            _maybe_append_warning(
                bucket_id=None,
                message=(
                    f"Candidate {candidate.registry_candidate_id} has statement_code "
                    f"{candidate.statement_code!r}, but statement_type "
                    f"{candidate.statement_type!r} does not define a code_type in "
                    f"statement_type_policy."
                ),
                registry_candidate_ids=[candidate.registry_candidate_id],
                severity="warning",
                warning_signatures=warning_signatures,
                warning_type="statement_code_on_statement_type_without_code_type",
                warnings=warnings,
            )


def _warn_on_text_repeated_across_windows(
    *,
    candidates: Sequence[SFIRegistryCandidate],
    duplicate_buckets: Sequence[SFIRegistryDuplicateBucket],
    warning_signatures: set[tuple[Any, ...]],
    warnings: list[SFIRegistryWarning],
) -> None:
    """Warn when the same text bucket key spans multiple windows.

    Parameters
    ----------
    candidates
        Flattened registry candidates.
    duplicate_buckets
        Possible duplicate buckets generated from candidate keys.
    warning_signatures
        Mutable set of warning signatures already emitted.
    warnings
        Mutable warning accumulator.
    """

    candidates_by_text_bucket: dict[str, list[SFIRegistryCandidate]] = defaultdict(list)

    for candidate in candidates:
        candidates_by_text_bucket[candidate.text_bucket_key].append(candidate)

    for text_bucket_key, text_candidates in sorted(candidates_by_text_bucket.items()):
        window_indexes = {candidate.window_index for candidate in text_candidates}

        if len(window_indexes) < 2:
            continue

        _maybe_append_warning(
            bucket_id=_find_bucket_id(
                bucket_key=text_bucket_key,
                bucket_type="description_text",
                duplicate_buckets=duplicate_buckets,
            ),
            message=(
                f"Same statement_type + normalized text appears across multiple "
                f"windows: {sorted(window_indexes)}."
            ),
            registry_candidate_ids=[
                candidate.registry_candidate_id for candidate in text_candidates
            ],
            severity="info",
            warning_signatures=warning_signatures,
            warning_type="same_text_repeated_across_windows",
            warnings=warnings,
        )


def _warn_on_text_repeated_within_window(
    *,
    candidates: Sequence[SFIRegistryCandidate],
    warning_signatures: set[tuple[Any, ...]],
    warnings: list[SFIRegistryWarning],
) -> None:
    """Warn when the same text bucket key repeats within a single window.

    Parameters
    ----------
    candidates
        Flattened registry candidates.
    warning_signatures
        Mutable set of warning signatures already emitted.
    warnings
        Mutable warning accumulator.
    """

    candidates_by_text_and_window: dict[tuple[str, int], list[SFIRegistryCandidate]] = (
        defaultdict(list)
    )

    for candidate in candidates:
        candidates_by_text_and_window[
            (candidate.text_bucket_key, candidate.window_index)
        ].append(candidate)

    for (_, window_index), text_candidates in sorted(
        candidates_by_text_and_window.items()
    ):
        if len(text_candidates) < 2:
            continue

        _maybe_append_warning(
            bucket_id=None,
            message=(
                f"Same statement_type + normalized text appears multiple times in "
                f"window {window_index}."
            ),
            registry_candidate_ids=[
                candidate.registry_candidate_id for candidate in text_candidates
            ],
            severity="info",
            warning_signatures=warning_signatures,
            warning_type="same_text_repeated_within_window",
            warnings=warnings,
        )


def _with_source_order_controlled_scopes(
    *,
    candidates: Sequence[SFIRegistryCandidate],
    statement_value_policies: dict[str, _StatementValuePolicy],
) -> list[SFIRegistryCandidate]:
    """Return registry candidates with source-order controlled scopes applied.

    Parameters
    ----------
    candidates
        Registry candidates in source order.
    statement_value_policies
        Controlled value policies keyed by statement type.

    Returns
    -------
    list[SFIRegistryCandidate]
        Registry candidates with corrected canonical scope keys and recomputed text
        duplicate bucket keys.
    """

    ordered_candidates = list(candidates)
    scoped_candidates: list[SFIRegistryCandidate] = []

    for candidate_index, candidate in enumerate(ordered_candidates):
        canonical_statement_scope_key = (
            _resolve_canonical_statement_scope_from_source_order(
                candidate=candidate,
                candidate_index=candidate_index,
                ordered_candidates=ordered_candidates,
                statement_value_policies=statement_value_policies,
            )
        )

        if canonical_statement_scope_key == candidate.canonical_statement_scope_key:
            scoped_candidates.append(candidate)
            continue

        text_bucket_key = _build_text_bucket_key(
            canonical_statement_scope_key=canonical_statement_scope_key,
            canonical_statement_value_key=candidate.canonical_statement_value_key,
            normalized_statement_code=candidate.normalized_statement_code,
            normalized_text=candidate.normalized_description,
            source_context_key=candidate.source_context_key,
            statement_type_key=normalize_text(candidate.statement_type),
        )
        source_text_bucket_key = _build_text_bucket_key(
            canonical_statement_scope_key=canonical_statement_scope_key,
            canonical_statement_value_key=candidate.canonical_statement_value_key,
            normalized_statement_code=candidate.normalized_statement_code,
            normalized_text=candidate.normalized_source_text,
            source_context_key=candidate.source_context_key,
            statement_type_key=normalize_text(candidate.statement_type),
        )
        scoped_candidates.append(
            candidate.model_copy(
                update={
                    "canonical_statement_scope_key": canonical_statement_scope_key,
                    "source_text_bucket_key": source_text_bucket_key,
                    "text_bucket_key": text_bucket_key,
                }
            )
        )

    return scoped_candidates


def build_candidate_registry(
    *,
    extraction_windows: Sequence[ExtractionWindow],
    kg_config: CreateKGConfig,
    save_fp: Path,
    sfi_extraction_results: Sequence[SFIExtractionResult],
) -> SFIRegistryArtifact:
    """Build and persist the global SFI candidate registry.

    This function flattens window-local candidates, computes lightweight code and text
    bucket keys, emits possible duplicate buckets for later LLM-assisted merge review,
    and records non-fatal warnings. It does not merge candidates, mint final
    StandardsFrameworkItem IDs, infer hierarchy, or recover full DocumentIR context.

    Parameters
    ----------
    extraction_windows
        Ordered extraction windows used for SFI extraction.
    kg_config
        Runtime KG creation config.
    save_fp
        File path for saving `sfi_candidate_registry.json`.
    sfi_extraction_results
        Ordered SFI extraction results to flatten.

    Returns
    -------
    SFIRegistryArtifact
        Validated candidate registry artifact.

    Raises
    ------
    ValueError
        If extraction results and windows do not align or have incompatible lengths.
    """

    if len(sfi_extraction_results) != len(extraction_windows):
        raise ValueError(
            f"Expected one SFI extraction result per extraction window, got "
            f"results={len(sfi_extraction_results)} and "
            f"windows={len(extraction_windows)}."
        )

    make_dir(save_fp.parent)

    auxiliary_candidate_count = 0
    candidates: list[SFIRegistryCandidate] = []
    code_patterns, statement_type_code_types = _validate_statement_type_code_types(
        kg_config
    )
    statement_value_policies = _build_statement_value_policies(kg_config)

    for result_index, extraction_result in enumerate(sfi_extraction_results):
        extraction_window = extraction_windows[result_index]
        _validate_result_window_alignment(
            extraction_result=extraction_result,
            extraction_window=extraction_window,
            kg_config=kg_config,
            result_index=result_index,
        )
        _validate_unique_window_candidate_ids(
            extraction_result=extraction_result, result_index=result_index
        )
        auxiliary_candidate_count += len(extraction_result.auxiliary_candidates)

        for source_window_candidate_index, candidate in enumerate(
            extraction_result.sfi_candidates
        ):
            candidates.append(
                _build_registry_candidate(
                    candidate=candidate,
                    code_patterns=code_patterns,
                    extraction_window=extraction_window,
                    source_window_candidate_index=source_window_candidate_index,
                    statement_type_code_types=statement_type_code_types,
                    statement_value_policies=statement_value_policies,
                )
            )

    candidates = _with_source_order_controlled_scopes(
        candidates=candidates, statement_value_policies=statement_value_policies
    )
    duplicate_buckets = _build_duplicate_buckets(candidates)
    warnings = _build_registry_warnings(
        candidates=candidates,
        code_patterns=code_patterns,
        duplicate_buckets=duplicate_buckets,
        statement_type_code_types=statement_type_code_types,
    )
    summary = _build_registry_summary(
        auxiliary_candidate_count=auxiliary_candidate_count,
        candidates=candidates,
        duplicate_buckets=duplicate_buckets,
        extraction_window_count=len(extraction_windows),
        warnings=warnings,
    )
    sfi_registry_artifact = SFIRegistryArtifact(
        candidates=candidates,
        country=kg_config.metadata.country,
        doc_key=extraction_windows[0].doc_key if extraction_windows else None,
        duplicate_buckets=duplicate_buckets,
        framework_title=kg_config.metadata.framework_title,
        pdf_name=extraction_windows[0].pdf_name if extraction_windows else None,
        primary_language=kg_config.metadata.primary_language,
        subject=kg_config.metadata.subject,
        summary=summary,
        warnings=warnings,
    )

    write_to_json(fp=save_fp, json_info=sfi_registry_artifact)

    logger.success(f"Saved SFI registry artifact to: {save_fp}")

    return sfi_registry_artifact
