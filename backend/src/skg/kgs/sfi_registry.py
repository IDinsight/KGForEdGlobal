"""This module contains functionalities for building a global registry of extracted SFI
candidates.

It flattens validated window-local SFI extraction results into document-level candidate
records, computes lightweight code/text keys, and emits possible duplicate buckets for
later LLM-assisted merge review.
"""

# Standard Library
import hashlib
import re
import unicodedata

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional, Sequence

# Package Library
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
from skg.kgs.validators import verify_sfi_extraction_quality
from skg.page_ir_extraction.validators import QualityError
from skg.schemas import CreateKGConfig
from skg.utils.general import make_dir, write_to_json

_LEADING_CODE_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"[A-Za-z]{1,6}\d*(?:\.\s*\d+){1,8}\.?"
    r"|\d+(?:\.\s*\d+){1,8}\.?"
    r"|[A-Za-z]{1,6}\d+\."
    r")\s*[:.)\-–—]?\s+"
)
_CODE_LIKE_RE = re.compile(
    r"\b(?:[A-Za-z]{1,6}\d*(?:\.\s*\d+){1,8}|\d+(?:\.\s*\d+){1,8})\b"
)


def _add_registry_warning(
    *,
    bucket_id: Optional[str],
    message: str,
    registry_candidate_ids: Sequence[str],
    severity: str,
    warning_index: int,
    warning_type: str,
) -> SFIRegistryWarning:
    """Create one registry warning.

    Parameters
    ----------
    bucket_id
        Optional duplicate bucket ID associated with this warning.
    message
        Human-readable warning message.
    registry_candidate_ids
        Registry candidate IDs associated with the warning.
    severity
        Warning severity label.
    warning_index
        1-based warning index used to create a stable local ID.
    warning_type
        Machine-readable warning type.

    Returns
    -------
    SFIRegistryWarning
        Validated warning record.
    """

    return SFIRegistryWarning(
        bucket_id=bucket_id,
        message=message,
        registry_candidate_ids=list(registry_candidate_ids),
        severity=severity,
        warning_id=f"warning_{warning_index:04d}",
        warning_type=warning_type,
    )


def _build_duplicate_bucket(
    *,
    bucket_key: str,
    bucket_type: str,
    candidates_by_id: dict[str, SFIRegistryCandidate],
    registry_candidate_ids: Sequence[str],
) -> SFIRegistryDuplicateBucket:
    """Build one possible duplicate bucket from registry candidate IDs.

    Parameters
    ----------
    bucket_key
        Normalized bucket key shared by all candidates in the bucket.
    bucket_type
        Bucket type, such as `code` or `description_text`.
    candidates_by_id
        Lookup of registry candidates by registry candidate ID.
    registry_candidate_ids
        Candidate IDs belonging to this bucket.

    Returns
    -------
    SFIRegistryDuplicateBucket
        Validated duplicate bucket record for later merge review.
    """

    candidate_ids = sorted(registry_candidate_ids)
    candidate_records = [
        candidates_by_id[candidate_id] for candidate_id in candidate_ids
    ]
    bucket_id = _deterministic_bucket_id(bucket_key=bucket_key, bucket_type=bucket_type)
    evidence_strength = {
        "code": "strong_signal",
        "description_text": "medium_signal",
        "source_text": "weak_signal",
    }[bucket_type]
    statement_types = sorted(
        {candidate.statement_type for candidate in candidate_records}
    )
    window_indexes = sorted({candidate.window_index for candidate in candidate_records})

    return SFIRegistryDuplicateBucket(
        bucket_id=bucket_id,
        bucket_key=bucket_key,
        bucket_type=bucket_type,
        candidate_count=len(candidate_ids),
        description_examples=_unique_limited(
            [candidate.description for candidate in candidate_records], limit=5
        ),
        evidence_strength=evidence_strength,
        merge_policy_hint="review_required",
        registry_candidate_ids=candidate_ids,
        statement_types=statement_types,
        window_indexes=window_indexes,
    )


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

    buckets: list[SFIRegistryDuplicateBucket] = []
    bucket_maps: dict[str, dict[str, list[str]]] = {
        "code": defaultdict(list),
        "description_text": defaultdict(list),
        "source_text": defaultdict(list),
    }

    for candidate in candidates:
        if candidate.code_bucket_key:
            bucket_maps["code"][candidate.code_bucket_key].append(
                candidate.registry_candidate_id
            )

        bucket_maps["description_text"][candidate.text_bucket_key].append(
            candidate.registry_candidate_id
        )
        bucket_maps["source_text"][candidate.source_text_bucket_key].append(
            candidate.registry_candidate_id
        )

    candidates_by_id = {
        candidate.registry_candidate_id: candidate for candidate in candidates
    }

    for bucket_type in ["code", "description_text", "source_text"]:
        for bucket_key, registry_candidate_ids in sorted(
            bucket_maps[bucket_type].items()
        ):
            if len(registry_candidate_ids) < 2:
                continue

            buckets.append(
                _build_duplicate_bucket(
                    bucket_key=bucket_key,
                    bucket_type=bucket_type,
                    candidates_by_id=candidates_by_id,
                    registry_candidate_ids=registry_candidate_ids,
                )
            )

    return sorted(
        buckets,
        key=lambda bucket: (
            {"code": 0, "description_text": 1, "source_text": 2}[bucket.bucket_type],
            bucket.bucket_key,
        ),
    )


def _build_registry_candidate(
    *,
    candidate: SFICandidate,
    extraction_window: ExtractionWindow,
    source_window_candidate_index: int,
) -> SFIRegistryCandidate:
    """Build one flattened registry candidate from a window-local SFI candidate.

    Parameters
    ----------
    candidate
        Window-local candidate from an SFI extraction result.
    extraction_window
        Source extraction window that produced the candidate.
    source_window_candidate_index
        0-based candidate position within the extraction result.

    Returns
    -------
    SFIRegistryCandidate
        Registry candidate with normalized keys and source references.
    """

    normalized_description = _normalize_text(candidate.description)
    normalized_source_text = _normalize_text(candidate.source_text)
    normalized_description_without_leading_code = _strip_leading_code_prefix(
        normalized_description
    )
    normalized_source_text_without_leading_code = _strip_leading_code_prefix(
        normalized_source_text
    )
    normalized_statement_code = _normalize_code(candidate.statement_code)
    registry_candidate_id = _deterministic_registry_candidate_id(
        candidate_id=candidate.candidate_id,
        window_id=extraction_window.window_id,
        window_index=extraction_window.window_index,
    )
    statement_type_key = _normalize_text(candidate.statement_type)
    text_bucket_key = _join_bucket_key(
        statement_type_key, normalized_description_without_leading_code
    )
    source_text_bucket_key = _join_bucket_key(
        statement_type_key, normalized_source_text_without_leading_code
    )
    code_bucket_key = (
        _join_bucket_key(statement_type_key, normalized_statement_code)
        if normalized_statement_code
        else None
    )

    return SFIRegistryCandidate(
        candidate_payload=candidate,
        code_bucket_key=code_bucket_key,
        confidence=candidate.confidence,
        description=candidate.description,
        language=candidate.language,
        normalized_description=normalized_description,
        normalized_description_without_leading_code=normalized_description_without_leading_code,
        normalized_source_text=normalized_source_text,
        normalized_source_text_without_leading_code=normalized_source_text_without_leading_code,
        normalized_statement_code=normalized_statement_code,
        normalized_statement_type=candidate.normalized_statement_type,
        registry_candidate_id=registry_candidate_id,
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
    duplicate_buckets: Sequence[SFIRegistryDuplicateBucket],
) -> list[SFIRegistryWarning]:
    """Build lightweight non-fatal warnings for candidate review.

    Parameters
    ----------
    candidates
        Flattened registry candidates.
    duplicate_buckets
        Possible duplicate buckets generated from candidate keys.

    Returns
    -------
    list[SFIRegistryWarning]
        Non-fatal warnings for Step 7 review and debugging.
    """

    warnings: list[SFIRegistryWarning] = []
    warning_signatures: set[tuple[Any, ...]] = set()
    candidates_by_code: dict[str, list[SFIRegistryCandidate]] = defaultdict(list)
    candidates_by_id = {
        candidate.registry_candidate_id: candidate for candidate in candidates
    }
    candidates_by_text_and_window: dict[tuple[str, int], list[SFIRegistryCandidate]] = (
        defaultdict(list)
    )
    candidates_by_text_bucket: dict[str, list[SFIRegistryCandidate]] = defaultdict(list)

    for candidate in candidates:
        if candidate.normalized_statement_code:
            candidates_by_code[candidate.normalized_statement_code].append(candidate)

        candidates_by_text_and_window[
            (candidate.text_bucket_key, candidate.window_index)
        ].append(candidate)
        candidates_by_text_bucket[candidate.text_bucket_key].append(candidate)

        if candidate.statement_code is None and _has_code_like_text(
            candidate.source_text
        ):
            _maybe_append_warning(
                bucket_id=None,
                message=(
                    f"Candidate {candidate.registry_candidate_id} has code-like "
                    "source text but statement_code is null."
                ),
                registry_candidate_ids=[candidate.registry_candidate_id],
                severity="warning",
                warning_signatures=warning_signatures,
                warning_type="code_like_source_text_with_null_statement_code",
                warnings=warnings,
            )

        if (
            candidate.normalized_statement_type == "Standard Grouping"
            and candidate.normalized_statement_code is not None
            and re.fullmatch(r"\d+", candidate.normalized_statement_code)
        ):
            _maybe_append_warning(
                bucket_id=None,
                message=(
                    f"Grouping candidate {candidate.registry_candidate_id} has a "
                    f"simple numeric statement_code {candidate.statement_code!r}; "
                    "treat it as a weak local label, not a globally stable code."
                ),
                registry_candidate_ids=[candidate.registry_candidate_id],
                severity="info",
                warning_signatures=warning_signatures,
                warning_type="simple_numeric_grouping_code",
                warnings=warnings,
            )

    for bucket in duplicate_buckets:
        bucket_candidates = [
            candidates_by_id[candidate_id]
            for candidate_id in bucket.registry_candidate_ids
        ]
        description_keys = {
            candidate.normalized_description_without_leading_code
            for candidate in bucket_candidates
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

    for (text_bucket_key, window_index), text_candidates in sorted(
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
                "Same statement_type + normalized text appears across multiple "
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

    return [
        warning.model_copy(update={"warning_id": f"warning_{index:04d}"})
        for index, warning in enumerate(warnings, start=1)
    ]


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


def _deterministic_registry_candidate_id(
    *, candidate_id: str, window_id: str, window_index: int
) -> str:
    """Create a deterministic temporary registry candidate ID.

    Parameters
    ----------
    candidate_id
        Window-local candidate ID.
    window_id
        Source extraction window ID.
    window_index
        Source extraction window index.

    Returns
    -------
    str
        Temporary registry candidate ID for review and merge reporting.
    """

    candidate_slug = re.sub(r"[^0-9A-Za-z_\-]+", "_", candidate_id.strip()).strip("_")
    digest = hashlib.sha256(f"{window_id}|{candidate_id}".encode("utf-8")).hexdigest()
    return f"w{window_index:04d}:{candidate_slug or 'candidate'}:{digest[:8]}"


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


def _has_code_like_text(value: str) -> bool:
    """Check whether text contains a visible code-like pattern.

    Parameters
    ----------
    value
        Source text to inspect.

    Returns
    -------
    bool
        Whether the text contains a code-like pattern.
    """

    return bool(_CODE_LIKE_RE.search(value or ""))


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
        _add_registry_warning(
            bucket_id=bucket_id,
            message=message,
            registry_candidate_ids=candidate_ids,
            severity=severity,
            warning_index=len(warnings) + 1,
            warning_type=warning_type,
        )
    )


def _normalize_code(value: Optional[str]) -> Optional[str]:
    """Normalize an optional statement code for duplicate bucketing.

    Parameters
    ----------
    value
        Optional source-visible statement code.

    Returns
    -------
    Optional[str]
        Normalized code, or `None` when no code is present.
    """

    if value is None:
        return None

    normalized = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    normalized = re.sub(r"\s+", "", normalized).strip(" .:;-)–—")
    return normalized or None


def _normalize_text(value: str) -> str:
    """Normalize text for duplicate bucketing.

    Parameters
    ----------
    value
        Text to normalize.

    Returns
    -------
    str
        Unicode-normalized, casefolded text with collapsed whitespace.
    """

    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _strip_leading_code_prefix(value: str) -> str:
    """Strip an obvious leading source-code prefix from normalized text.

    Parameters
    ----------
    value
        Normalized candidate text.

    Returns
    -------
    str
        Text with an obvious leading code prefix removed, when present.
    """

    stripped = _LEADING_CODE_PREFIX_RE.sub("", value or "", count=1).strip()
    return stripped or value


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
        verify_sfi_extraction_quality(
            extraction_result=extraction_result,
            kg_config=kg_config,
            window=extraction_window,
        )
    except QualityError as e:
        raise ValueError(
            f"SFI extraction result {result_index} failed current quality validation: {e}"
        ) from e


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

    for result_index, extraction_result in enumerate(sfi_extraction_results):
        extraction_window = extraction_windows[result_index]
        _validate_result_window_alignment(
            extraction_result=extraction_result,
            extraction_window=extraction_window,
            kg_config=kg_config,
            result_index=result_index,
        )
        auxiliary_candidate_count += len(extraction_result.auxiliary_candidates)

        for source_window_candidate_index, candidate in enumerate(
            extraction_result.sfi_candidates
        ):
            candidates.append(
                _build_registry_candidate(
                    candidate=candidate,
                    extraction_window=extraction_window,
                    source_window_candidate_index=source_window_candidate_index,
                )
            )

    duplicate_buckets = _build_duplicate_buckets(candidates)
    warnings = _build_registry_warnings(
        candidates=candidates, duplicate_buckets=duplicate_buckets
    )
    summary = _build_registry_summary(
        auxiliary_candidate_count=auxiliary_candidate_count,
        candidates=candidates,
        duplicate_buckets=duplicate_buckets,
        extraction_window_count=len(extraction_windows),
        warnings=warnings,
    )
    artifact = SFIRegistryArtifact(
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

    write_to_json(fp=save_fp, json_info=artifact)

    return artifact
