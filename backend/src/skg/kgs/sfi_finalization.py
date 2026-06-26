"""This module contains functionalities for finalizing deduplicated SFI merge groups
into deterministic final SFI records.

This module consumes the SFI merge report and candidate registry. It mints stable,
CASE-compatible final StandardsFrameworkItem identifiers and preserves enough source
and merge provenance for later source-context recovery and relationship resolution. It
does not infer hierarchy, compile final KG objects, or create relationships.
"""

# Standard Library
import hashlib
import re
import unicodedata
import uuid

from collections import Counter
from typing import Any, Iterable, Sequence

# Third Party Library
from loguru import logger

# Package Library
from skg.config import Settings
from skg.document_ir.schemas import DocumentIR
from skg.kgs.schemas import (
    FinalSFIRecord,
    FinalSFISummary,
    SFIMergeGroup,
    SFIMergeReport,
    SFIRegistryArtifact,
    SFIRegistryCandidate,
)
from skg.kgs.utils import KGDirs
from skg.schemas import CreateKGConfig
from skg.utils.general import make_dir, write_to_json

_SAME_CODE_DIFFERENT_CONTENT_AUDIT_FLAG = "same_code_different_content"


def _build_code_group_counts(
    eligible_merge_groups: Sequence[SFIMergeGroup],
) -> dict[tuple[str, str, str], int]:
    """Count eligible final groups sharing the same statement type and code.

    Parameters
    ----------
    eligible_merge_groups
        Merge groups that may be minted as final SFIs.

    Returns
    -------
    dict[tuple[str, str, str], int]
        Counts keyed by statement type, normalized statement type, and normalized code.
    """

    counts: Counter[tuple[str, str, str]] = Counter()

    for merge_group in eligible_merge_groups:
        normalized_statement_code = merge_group.normalized_statement_code

        if not normalized_statement_code:
            continue

        key = _build_code_group_key(merge_group)
        counts[key] += 1

    return dict(counts)


def _build_code_group_key(merge_group: SFIMergeGroup) -> tuple[str, str, str]:
    """Build a stable same-code grouping key for one merge group.

    Parameters
    ----------
    merge_group
        Merge group whose normalized code identity should be counted.

    Returns
    -------
    tuple[str, str, str]
        Statement type, normalized statement type, and normalized statement code.

    Raises
    ------
    ValueError
        If the merge group does not have the fields required for coded identity.
    """

    if not merge_group.normalized_statement_code:
        raise ValueError(
            f"Merge group {merge_group.merge_group_id!r} has no normalized code."
        )

    return (
        _shared_statement_type(merge_group),
        _shared_normalized_statement_type(merge_group),
        merge_group.normalized_statement_code,
    )


def _build_final_sfi_record(
    *,
    code_group_counts: dict[tuple[str, str, str], int],
    document_ir: DocumentIR,
    kg_config: CreateKGConfig,
    merge_group: SFIMergeGroup,
    segment_page_indexes_by_id: dict[str, list[int]],
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
) -> FinalSFIRecord:
    """Build one final SFI record from one eligible merge group.

    Parameters
    ----------
    code_group_counts
        Counts for coded merge groups keyed by statement type and normalized code.
    document_ir
        Source DocumentIR used to recover page-level provenance.
    kg_config
        Runtime KG configuration with framework metadata.
    merge_group
        Eligible SFI merge group to mint as a final SFI.
    segment_page_indexes_by_id
        Page-index lookup keyed by DocumentIR segment ID.
    sfi_candidates_by_id
        Registry candidates keyed by registry candidate ID.

    Returns
    -------
    FinalSFIRecord
        Deterministic final SFI record.
    """

    group_candidates = [
        sfi_candidates_by_id[candidate_id]
        for candidate_id in merge_group.registry_candidate_ids
    ]
    identity_key, uses_code_disambiguator = _build_identity_key(
        code_group_counts=code_group_counts,
        document_ir=document_ir,
        merge_group=merge_group,
    )
    final_sfi_uuid = uuid.uuid5(Settings.LC_CANONICAL_NAMESPACE_UUID, identity_key)
    source_context_keys = _source_context_keys(merge_group)
    source_page_indexes = sorted(
        {
            page_index
            for source_segment_id in merge_group.source_segment_ids
            for page_index in segment_page_indexes_by_id.get(source_segment_id, [])
        }
    )
    statement_type = _shared_statement_type(merge_group)
    return FinalSFIRecord(
        academic_subject=kg_config.metadata.subject,
        attribution_statement=kg_config.metadata.attribution_statement,
        audit_flags=merge_group.audit_flags,
        audit_notes=merge_group.audit_notes,
        audit_peer_merge_group_ids=merge_group.audit_peer_merge_group_ids,
        author=kg_config.metadata.author,
        candidate_descriptions=merge_group.candidate_descriptions,
        candidate_source_refs=merge_group.candidate_source_refs,
        candidate_source_texts=merge_group.candidate_source_texts,
        case_identifier_uri=f"urn:uuid:{final_sfi_uuid}",
        case_identifier_uuid=final_sfi_uuid,
        confidence_max=merge_group.confidence_max,
        confidence_min=merge_group.confidence_min,
        description=_choose_final_description(merge_group),
        final_sfi_uuid=final_sfi_uuid,
        identifier=final_sfi_uuid,
        identity_key=identity_key,
        in_language=_choose_language(
            group_candidates=group_candidates, kg_config=kg_config
        ),
        jurisdiction=kg_config.metadata.jurisdiction,
        language=_choose_language(
            group_candidates=group_candidates, kg_config=kg_config
        ),
        license=kg_config.metadata.license,
        merge_decision=merge_group.merge_decision,
        merge_group_id=merge_group.merge_group_id,
        merge_reason=merge_group.merge_reason,
        metadata={
            "country": kg_config.metadata.country,
            "doc_key": document_ir.doc_key,
            "framework_title": kg_config.metadata.framework_title,
            "identity": {
                "namespace_uuid": str(Settings.LC_CANONICAL_NAMESPACE_UUID),
                "uses_code_disambiguator": uses_code_disambiguator,
            },
            "pdf_name": document_ir.pdf_name,
            "primary_language": kg_config.metadata.primary_language,
            "same_code_different_content": (
                _SAME_CODE_DIFFERENT_CONTENT_AUDIT_FLAG in merge_group.audit_flags
            ),
        },
        normalized_statement_code=merge_group.normalized_statement_code,
        normalized_statement_type=_shared_normalized_statement_type(merge_group),
        provider=kg_config.metadata.provider,
        source_context_keys=source_context_keys,
        source_page_indexes=source_page_indexes,
        source_registry_candidate_ids=merge_group.registry_candidate_ids,
        source_segment_ids=merge_group.source_segment_ids,
        source_window_ids=merge_group.source_window_ids,
        source_window_indexes=merge_group.source_window_indexes,
        statement_code=merge_group.statement_code,
        statement_type=statement_type,
    )


def _build_final_sfi_summary(
    *,
    eligible_merge_group_count: int,
    excluded_conflict_group_count: int,
    excluded_needs_review_group_count: int,
    final_sfi_records: Sequence[FinalSFIRecord],
) -> FinalSFISummary:
    """Build aggregate counts for final SFI records.

    Parameters
    ----------
    eligible_merge_group_count
        Number of merge groups eligible for final SFI minting.
    excluded_conflict_group_count
        Number of conflict groups excluded from automatic final SFI minting.
    excluded_needs_review_group_count
        Number of needs-review groups excluded from automatic final SFI minting.
    final_sfi_records
        Final SFI records.

    Returns
    -------
    FinalSFISummary
        Aggregate final SFI summary.
    """

    audit_flag_counts: Counter[str] = Counter(
        audit_flag
        for final_sfi_record in final_sfi_records
        for audit_flag in final_sfi_record.audit_flags
    )
    normalized_statement_type_counts: Counter[str] = Counter(
        final_sfi_record.normalized_statement_type
        for final_sfi_record in final_sfi_records
    )
    statement_type_counts: Counter[str] = Counter(
        final_sfi_record.statement_type for final_sfi_record in final_sfi_records
    )
    return FinalSFISummary(
        audit_flag_count_by_type=dict(sorted(audit_flag_counts.items())),
        eligible_merge_group_count=eligible_merge_group_count,
        excluded_conflict_group_count=excluded_conflict_group_count,
        excluded_needs_review_group_count=excluded_needs_review_group_count,
        final_sfi_count=len(final_sfi_records),
        final_sfi_count_by_normalized_statement_type=dict(
            sorted(normalized_statement_type_counts.items())
        ),
        final_sfi_count_by_statement_type=dict(sorted(statement_type_counts.items())),
        final_sfis_with_statement_code=sum(
            1 for record in final_sfi_records if record.statement_code is not None
        ),
        final_sfis_without_statement_code=sum(
            1 for record in final_sfi_records if record.statement_code is None
        ),
        same_code_disambiguated_final_sfi_count=sum(
            1
            for record in final_sfi_records
            if record.metadata.get("identity", {}).get("uses_code_disambiguator")
        ),
        source_registry_candidate_count=sum(
            len(record.source_registry_candidate_ids) for record in final_sfi_records
        ),
    )


def _build_identity_key(
    *,
    code_group_counts: dict[tuple[str, str, str], int],
    document_ir: DocumentIR,
    merge_group: SFIMergeGroup,
) -> tuple[str, bool]:
    """Build a canonical identity string for deterministic final SFI UUID minting.

    Parameters
    ----------
    code_group_counts
        Counts for eligible coded groups by same-code key.
    document_ir
        Source DocumentIR whose doc_key scopes all final SFI identities.
    merge_group
        Merge group to identify.

    Returns
    -------
    tuple[str, bool]
        Identity key and whether a code disambiguator was included.
    """

    normalized_statement_type_key = _slug(
        _shared_normalized_statement_type(merge_group)
    )
    statement_type_key = _slug(_shared_statement_type(merge_group))
    base_key = (
        f"lc:curriculum:{document_ir.doc_key}:sfi:"
        f"{normalized_statement_type_key}:{statement_type_key}"
    )

    if merge_group.normalized_statement_code:
        identity_key = f"{base_key}:{merge_group.normalized_statement_code}"

        # Determine if the code alone is not unique or an audit flag requires
        # preservation.
        needs_disambiguator = (
            _SAME_CODE_DIFFERENT_CONTENT_AUDIT_FLAG in merge_group.audit_flags
            or code_group_counts.get(_build_code_group_key(merge_group), 0) > 1
        )

        if needs_disambiguator:
            # Build a deterministic source/text/provenance disambiguator for same-code
            # groups.
            disambiguator_parts = [
                *merge_group.candidate_descriptions,
                *merge_group.candidate_source_texts,
                *merge_group.source_segment_ids,
                *(str(index) for index in merge_group.source_window_indexes),
                *_source_context_keys(merge_group),
            ]
            disambiguator = _hash_text(n_hex=20, value="\n".join(disambiguator_parts))
            identity_key = f"{identity_key}:{disambiguator}"

        return identity_key, needs_disambiguator

    source_context_key = _hash_text(
        n_hex=20,
        value="\n".join(_source_context_keys(merge_group))
        or "\n".join(merge_group.source_segment_ids)
        or merge_group.merge_group_id,
    )
    source_text_key = _hash_text(
        n_hex=20,
        value="\n".join(merge_group.candidate_source_texts)
        or "\n".join(merge_group.candidate_descriptions)
        or merge_group.merge_group_id,
    )
    return f"{base_key}:{source_context_key}:{source_text_key}", False


def _build_segment_page_index_lookup(document_ir: DocumentIR) -> dict[str, list[int]]:
    """Build page-index provenance for each DocumentIR segment.

    Parameters
    ----------
    document_ir
        Source DocumentIR.

    Returns
    -------
    dict[str, list[int]]
        Sorted page indexes keyed by segment ID.
    """

    page_indexes_by_id: dict[str, list[int]] = {}

    for segment in document_ir.segments:
        page_indexes = sorted(
            {
                provenance.page_index
                for provenance in getattr(segment, "segment_provenance", []) or []
            }
        )
        page_indexes_by_id[segment.segment_id] = page_indexes

    return page_indexes_by_id


def _choose_final_description(merge_group: SFIMergeGroup) -> str:
    """Choose deterministic final source-backed text for one merge group.

    Parameters
    ----------
    merge_group
        Merge group whose candidate descriptions and evidence quotes should be used.

    Returns
    -------
    str
        Chosen final description.

    Raises
    ------
    ValueError
        If no description or source text is available.
    """

    candidates = _unique_nonempty(merge_group.candidate_descriptions)

    if not candidates:
        candidates = _unique_nonempty(merge_group.candidate_source_texts)

    if not candidates:
        raise ValueError(
            f"Merge group {merge_group.merge_group_id!r} has no final description "
            f"or source text to use for Step 8."
        )

    normalized_to_values: dict[str, list[str]] = {}

    for value in candidates:
        normalized_to_values.setdefault(_normalize_text(value), []).append(value)

    if len(normalized_to_values) == 1:
        return _preferred_surface_form(candidates)

    return _preferred_surface_form(candidates)


def _choose_language(
    *, group_candidates: Sequence[SFIRegistryCandidate], kg_config: CreateKGConfig
) -> str:
    """Choose a deterministic language tag for a final SFI.

    Parameters
    ----------
    group_candidates
        Registry candidates represented by the final SFI.
    kg_config
        Runtime KG config carrying framework language metadata.

    Returns
    -------
    str
        Final language tag.
    """

    languages = _unique_nonempty(candidate.language for candidate in group_candidates)

    if len(languages) == 1:
        return languages[0]

    if kg_config.metadata.primary_language:
        return kg_config.metadata.primary_language

    return languages[0] if languages else kg_config.metadata.languages[0]


def _hash_text(*, n_hex: int, value: str) -> str:
    """Hash normalized text with a stable SHA-256 digest.

    Parameters
    ----------
    n_hex
        Number of hexadecimal digest characters to return.
    value
        Raw text to hash.

    Returns
    -------
    str
        Truncated hexadecimal digest.
    """

    normalized = _normalize_text(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:n_hex]


def _normalize_text(value: str) -> str:
    """Normalize text for deterministic identity material.

    Parameters
    ----------
    value
        Raw text.

    Returns
    -------
    str
        Unicode-normalized, casefolded text with collapsed whitespace.
    """

    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _preferred_surface_form(values: Sequence[str]) -> str:
    """Choose a stable human-readable representative from equivalent strings.

    Parameters
    ----------
    values
        Candidate surface forms.

    Returns
    -------
    str
        Preferred surface form.
    """

    cleaned = _unique_nonempty(values)

    if not cleaned:
        raise ValueError("Cannot choose a surface form from an empty sequence.")

    return sorted(
        cleaned,
        key=lambda value: (
            str(value).isupper(),
            -len(str(value).strip()),
            str(value).casefold(),
            str(value),
        ),
    )[0]


def _shared_normalized_statement_type(merge_group: SFIMergeGroup) -> str:
    """Return the single normalized statement type for an eligible merge group.

    Parameters
    ----------
    merge_group
        Merge group to inspect.

    Returns
    -------
    str
        Shared normalized statement type.

    Raises
    ------
    ValueError
        If the merge group does not have exactly one normalized statement type.
    """

    values = _unique_nonempty(
        [merge_group.normalized_statement_type, *merge_group.normalized_statement_types]
    )

    if len(values) != 1:
        raise ValueError(
            f"Merge group {merge_group.merge_group_id!r} must have exactly one "
            f"normalized statement type for final SFI minting; got {values!r}."
        )

    return values[0]


def _shared_statement_type(merge_group: SFIMergeGroup) -> str:
    """Return the single source-facing statement type for an eligible merge group.

    Parameters
    ----------
    merge_group
        Merge group to inspect.

    Returns
    -------
    str
        Shared source-facing statement type.

    Raises
    ------
    ValueError
        If the merge group does not have exactly one statement type.
    """

    values = _unique_nonempty(
        [merge_group.statement_type, *merge_group.statement_types]
    )

    if len(values) != 1:
        raise ValueError(
            f"Merge group {merge_group.merge_group_id!r} must have exactly one "
            f"statement type for final SFI minting; got {values!r}."
        )

    return values[0]


def _slug(value: str) -> str:
    """Convert text to a compact deterministic identity-key component.

    Parameters
    ----------
    value
        Raw text value.

    Returns
    -------
    str
        Lowercase slug.
    """

    slug = re.sub(r"[^0-9a-z]+", "-", _normalize_text(value)).strip("-")
    return slug or "unknown"


def _source_context_keys(merge_group: SFIMergeGroup) -> list[str]:
    """Extract source-context keys from merge-group candidate source refs.

    Parameters
    ----------
    merge_group
        Merge group whose source refs should be inspected.

    Returns
    -------
    list[str]
        Unique source-context keys in stable order.
    """

    return _unique_nonempty(
        source_ref.get("source_context_key")
        for source_ref in merge_group.candidate_source_refs
        if isinstance(source_ref, dict)
    )


def _unique_nonempty(values: Iterable[Any]) -> list[str]:
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


def _validate_final_sfi_records(final_sfi_records: Sequence[FinalSFIRecord]) -> None:
    """Validate final SFI records for ID and identity-key uniqueness.

    Parameters
    ----------
    final_sfi_records
        Final SFI records to validate.

    Raises
    ------
    ValueError
        If final UUIDs or identity keys collide.
    """

    uuid_values = [str(record.final_sfi_uuid) for record in final_sfi_records]
    identity_keys = [record.identity_key for record in final_sfi_records]

    duplicate_uuids = sorted(
        {value for value in uuid_values if uuid_values.count(value) > 1}
    )
    duplicate_identity_keys = sorted(
        {value for value in identity_keys if identity_keys.count(value) > 1}
    )

    if duplicate_uuids:
        raise ValueError(f"Final SFI UUID collisions detected: {duplicate_uuids}.")

    if duplicate_identity_keys:
        raise ValueError(
            f"Final SFI identity-key collisions detected: {duplicate_identity_keys}."
        )


def _validate_merge_group_coverage(
    *, sfi_candidate_registry: SFIRegistryArtifact, sfi_merge_report: SFIMergeReport
) -> None:
    """Validate that SFI merge groups cover registry candidates exactly once.

    Parameters
    ----------
    sfi_candidate_registry
        SFI candidate registry.
    sfi_merge_report
        SFI merge report.

    Raises
    ------
    ValueError
        If candidates are omitted, duplicated, or unknown in the merge groups.
    """

    expected_candidate_ids = {
        candidate.registry_candidate_id
        for candidate in sfi_candidate_registry.candidates
    }
    assigned_candidate_ids = [
        candidate_id
        for merge_group in sfi_merge_report.merge_groups
        for candidate_id in merge_group.registry_candidate_ids
    ]
    assigned_candidate_id_set = set(assigned_candidate_ids)
    duplicate_candidate_ids = sorted(
        {
            candidate_id
            for candidate_id in assigned_candidate_ids
            if assigned_candidate_ids.count(candidate_id) > 1
        }
    )
    omitted_candidate_ids = sorted(expected_candidate_ids - assigned_candidate_id_set)
    unknown_candidate_ids = sorted(assigned_candidate_id_set - expected_candidate_ids)

    if duplicate_candidate_ids:
        raise ValueError(
            f"SFI merge report assigns registry candidates more than once: "
            f"{duplicate_candidate_ids}."
        )

    if omitted_candidate_ids:
        raise ValueError(
            f"SFI merge report omits registry candidates: {omitted_candidate_ids}."
        )

    if unknown_candidate_ids:
        raise ValueError(
            f"SFI merge report references unknown registry candidates: "
            f"{unknown_candidate_ids}."
        )


def mint_final_sfi_ids(
    *,
    document_ir: DocumentIR,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
    sfi_candidate_registry: SFIRegistryArtifact,
    sfi_merge_report: SFIMergeReport,
) -> list[FinalSFIRecord]:
    """Mint deterministic final SFI records from SFI merge groups.

    This function consumes deduplicated merge groups, validates candidate coverage,
    mints deterministic UUIDv5 identifiers, preserves source and audit provenance, and
    writes `final_sfi_records.json` and `final_sfi_summary.json`. It does not infer
    hasChild relationships or build final exported KG objects.

    Parameters
    ----------
    document_ir
        Source DocumentIR used for document keys and page provenance recovery.
    kg_config
        Runtime KG creation config.
    kg_dirs
        KG artifact directory wrapper.
    sfi_candidate_registry
        SFI candidate registry.
    sfi_merge_report
        SFI merge report.

    Returns
    -------
    list[FinalSFIRecord]
        Deterministic final SFI records eligible for downstream relationship work.

    Raises
    ------
    ValueError
        If merge coverage is invalid, no final SFIs are produced, or IDs collide.
    """

    _validate_merge_group_coverage(
        sfi_candidate_registry=sfi_candidate_registry, sfi_merge_report=sfi_merge_report
    )

    eligible_merge_groups = [
        merge_group
        for merge_group in sfi_merge_report.merge_groups
        if merge_group.merge_decision in {"merged", "singleton"}
    ]
    code_group_counts = _build_code_group_counts(eligible_merge_groups)
    segment_page_indexes_by_id = _build_segment_page_index_lookup(document_ir)
    sfi_candidates_by_id = {
        candidate.registry_candidate_id: candidate
        for candidate in sfi_candidate_registry.candidates
    }

    final_sfi_records = [
        _build_final_sfi_record(
            code_group_counts=code_group_counts,
            document_ir=document_ir,
            kg_config=kg_config,
            merge_group=merge_group,
            segment_page_indexes_by_id=segment_page_indexes_by_id,
            sfi_candidates_by_id=sfi_candidates_by_id,
        )
        for merge_group in eligible_merge_groups
    ]

    if not final_sfi_records:
        raise ValueError("Step 8 produced zero final SFI records.")

    _validate_final_sfi_records(final_sfi_records)

    final_sfi_summary = _build_final_sfi_summary(
        eligible_merge_group_count=len(eligible_merge_groups),
        excluded_conflict_group_count=len(sfi_merge_report.conflict_groups),
        excluded_needs_review_group_count=len(sfi_merge_report.needs_review_groups),
        final_sfi_records=final_sfi_records,
    )

    make_dir(kg_dirs.root)
    write_to_json(
        fp=kg_dirs.root / "final_sfi_records.json", json_info=final_sfi_records
    )
    write_to_json(
        fp=kg_dirs.root / "final_sfi_summary.json", json_info=final_sfi_summary
    )

    logger.success(
        f"Minted final SFI records: final_sfis={len(final_sfi_records)}; "
        f"records={kg_dirs.root / 'final_sfi_records.json'}."
    )

    return final_sfi_records
