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
import uuid

from collections import Counter
from typing import Sequence

# Third Party Library
from loguru import logger

# Package Library
from skg.config import Settings
from skg.document_ir.schemas import DocumentIR
from skg.kgs.schemas import (
    SFIFinalRecord,
    SFIFinalSummary,
    SFIMergeGroup,
    SFIMergeReport,
    SFIRegistryArtifact,
    SFIRegistryCandidate,
)
from skg.kgs.utils import KGDirs, normalize_text, unique_nonempty
from skg.schemas import CreateKGConfig
from skg.utils.general import make_dir, write_to_json

_SAME_CODE_DIFFERENT_CONTENT_AUDIT_FLAG = "same_code_different_content"
_SUSPICIOUS_CONTROLLED_SCOPE_SPLIT_AUDIT_FLAG = "suspicious_controlled_scope_split"


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

    source_context_key = _build_no_code_source_context_key(merge_group)
    source_text_key = _build_no_code_statement_text_key(merge_group)
    return f"{base_key}:{source_context_key}:{source_text_key}", False


def _build_no_code_source_context_key(merge_group: SFIMergeGroup) -> str:
    """Build the source/scope identity component for a no-code SFI group.

    Controlled organizer values use their configured scope key so source-visible
    punctuation variants in the same curriculum scope mint the same final identity.
    Uncontrolled no-code statements keep the previous source-context-derived key so
    recurring visible text in different source locations remains distinct.

    Parameters
    ----------
    merge_group
        Merge group whose no-code source/scope should become an identity component.

    Returns
    -------
    str
        Compact deterministic hash or configured canonical scope key for final no-code
        identity construction.
    """

    if (
        merge_group.canonical_statement_scope_key
        and merge_group.canonical_statement_value_key
    ):
        return _hash_text(n_hex=20, value=merge_group.canonical_statement_scope_key)

    return _hash_text(
        n_hex=20,
        value="\n".join(_source_context_keys(merge_group))
        or "\n".join(merge_group.source_segment_ids)
        or merge_group.merge_group_id,
    )


def _build_no_code_statement_text_key(merge_group: SFIMergeGroup) -> str:
    """Build the statement-text identity component for a no-code SFI group.

    No-code curriculum statements can share the same table cell evidence quote while
    representing distinct numbered statements. The final SFI identity therefore uses
    candidate descriptions before source evidence quotes so sibling list items remain
    distinct when they were deduplicated as separate final statements. Source evidence
    quotes remain part of the identity component after descriptions to preserve
    provenance-sensitive separation when descriptions alone are not enough.

    Parameters
    ----------
    merge_group
        Merge group whose no-code statement text should be converted into a compact
        deterministic identity component.

    Returns
    -------
    str
        Compact deterministic hash for the no-code statement text identity component.
    """

    if merge_group.canonical_statement_value_key:
        return _hash_text(n_hex=20, value=merge_group.canonical_statement_value_key)

    identity_parts = [
        *merge_group.candidate_descriptions,
        *merge_group.candidate_source_texts,
    ]
    return _hash_text(
        n_hex=20, value="\n".join(identity_parts) or merge_group.merge_group_id
    )


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


def _build_sfi_final_record(
    *,
    code_group_counts: dict[tuple[str, str, str], int],
    document_ir: DocumentIR,
    kg_config: CreateKGConfig,
    merge_group: SFIMergeGroup,
    segment_page_indexes_by_id: dict[str, list[int]],
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
) -> SFIFinalRecord:
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
    SFIFinalRecord
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
    return SFIFinalRecord(
        academic_subject=kg_config.metadata.subject,
        attribution_statement=kg_config.metadata.attribution_statement,
        audit_flags=merge_group.audit_flags,
        audit_notes=merge_group.audit_notes,
        audit_peer_merge_group_ids=merge_group.audit_peer_merge_group_ids,
        author=kg_config.metadata.author,
        candidate_descriptions=merge_group.candidate_descriptions,
        candidate_source_refs=merge_group.candidate_source_refs,
        candidate_source_texts=merge_group.candidate_source_texts,
        canonical_statement_scope_key=merge_group.canonical_statement_scope_key,
        canonical_statement_value=merge_group.canonical_statement_value,
        canonical_statement_value_key=merge_group.canonical_statement_value_key,
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
            "statement_value_canonicalization": {
                "canonical_statement_scope_key": merge_group.canonical_statement_scope_key,
                "canonical_statement_value": merge_group.canonical_statement_value,
                "canonical_statement_value_key": merge_group.canonical_statement_value_key,
            },
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


def _build_sfi_final_summary(
    *,
    eligible_merge_group_count: int,
    excluded_conflict_group_count: int,
    excluded_needs_review_group_count: int,
    sfi_final_records: Sequence[SFIFinalRecord],
) -> SFIFinalSummary:
    """Build aggregate counts for final SFI records.

    Parameters
    ----------
    eligible_merge_group_count
        Number of merge groups eligible for final SFI minting.
    excluded_conflict_group_count
        Number of conflict groups excluded from automatic final SFI minting.
    excluded_needs_review_group_count
        Number of needs-review groups excluded from automatic final SFI minting.
    sfi_final_records
        Final SFI records.

    Returns
    -------
    SFIFinalSummary
        Aggregate final SFI summary.
    """

    audit_flag_counts: Counter[str] = Counter(
        audit_flag
        for final_sfi_record in sfi_final_records
        for audit_flag in final_sfi_record.audit_flags
    )
    normalized_statement_type_counts: Counter[str] = Counter(
        final_sfi_record.normalized_statement_type
        for final_sfi_record in sfi_final_records
    )
    statement_type_counts: Counter[str] = Counter(
        final_sfi_record.statement_type for final_sfi_record in sfi_final_records
    )
    return SFIFinalSummary(
        audit_flag_count_by_type=dict(sorted(audit_flag_counts.items())),
        eligible_merge_group_count=eligible_merge_group_count,
        excluded_conflict_group_count=excluded_conflict_group_count,
        excluded_needs_review_group_count=excluded_needs_review_group_count,
        final_sfi_count=len(sfi_final_records),
        final_sfi_count_by_normalized_statement_type=dict(
            sorted(normalized_statement_type_counts.items())
        ),
        final_sfi_count_by_statement_type=dict(sorted(statement_type_counts.items())),
        final_sfis_with_statement_code=sum(
            1 for record in sfi_final_records if record.statement_code is not None
        ),
        final_sfis_without_statement_code=sum(
            1 for record in sfi_final_records if record.statement_code is None
        ),
        same_code_disambiguated_final_sfi_count=sum(
            1
            for record in sfi_final_records
            if record.metadata.get("identity", {}).get("uses_code_disambiguator")
        ),
        source_registry_candidate_count=sum(
            len(record.source_registry_candidate_ids) for record in sfi_final_records
        ),
    )


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

    candidates = unique_nonempty(merge_group.candidate_descriptions)

    if not candidates:
        candidates = unique_nonempty(merge_group.candidate_source_texts)

    if not candidates:
        raise ValueError(
            f"Merge group {merge_group.merge_group_id!r} has no final description "
            f"or source text to use for SFI finalization."
        )

    normalized_to_values: dict[str, list[str]] = {}

    for value in candidates:
        normalized_to_values.setdefault(normalize_text(value), []).append(value)

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

    languages = unique_nonempty(candidate.language for candidate in group_candidates)

    if len(languages) == 1:
        return languages[0]

    if kg_config.metadata.primary_language:
        return kg_config.metadata.primary_language

    return languages[0] if languages else kg_config.metadata.languages[0]


def _format_controlled_scope_parts(scope_parts: Sequence[tuple[str, str]]) -> str:
    """Format ordered controlled-scope parts into a stable string.

    Parameters
    ----------
    scope_parts
        Ordered `(scope_label, scope_value)` pairs parsed from a scope key.

    Returns
    -------
    str
        Stable pipe-delimited scope string.
    """

    return "|".join(f"{label}:{value}" for label, value in scope_parts)


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

    normalized = normalize_text(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:n_hex]


def _parse_controlled_scope_parts(scope_key: str | None) -> list[tuple[str, str]]:
    """Parse a controlled-value scope key into ordered scope parts.

    Parameters
    ----------
    scope_key
        Scope key such as `level:grade 4|strand:number`.

    Returns
    -------
    list[tuple[str, str]]
        Parsed non-empty scope parts in source-key order.
    """

    parts: list[tuple[str, str]] = []

    for raw_part in str(scope_key or "").split("|"):
        if ":" not in raw_part:
            continue

        key, value = raw_part.split(":", 1)
        key_clean = key.strip()
        value_clean = value.strip()

        if key_clean and value_clean:
            parts.append((key_clean, value_clean))

    return parts


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

    cleaned = unique_nonempty(values)

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

    values = unique_nonempty(
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

    values = unique_nonempty([merge_group.statement_type, *merge_group.statement_types])

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

    slug = re.sub(r"[^0-9a-z]+", "-", normalize_text(value)).strip("-")
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

    return unique_nonempty(
        source_ref.get("source_context_key")
        for source_ref in merge_group.candidate_source_refs
        if isinstance(source_ref, dict)
    )


def _validate_final_sfi_records(final_sfi_records: Sequence[SFIFinalRecord]) -> None:
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


def _with_controlled_scope_split_audits(
    sfi_final_records: Sequence[SFIFinalRecord],
) -> list[SFIFinalRecord]:
    """Annotate suspicious controlled-organizer scope splits.

    Controlled organizer values should not silently split into multiple final SFIs
    solely because an upstream source-context path was noisy. This audit flags cases
    where the same canonical controlled value and statement type appears under the same
    ancestor scope but across multiple immediate parent scope values. It does not merge
    or block records; it preserves deterministic finalization while making suspicious
    scope fragmentation visible before relationship resolution.

    Parameters
    ----------
    sfi_final_records
        Final SFI records minted from eligible merge groups.

    Returns
    -------
    list[SFIFinalRecord]
        Final records with added audit flags/notes where suspicious scope splits are
        detected.
    """

    records_by_scope_family: dict[tuple[str, str, str], list[SFIFinalRecord]] = {}

    for record in sfi_final_records:
        if not (
            record.canonical_statement_scope_key
            and record.canonical_statement_value_key
        ):
            continue

        scope_parts = _parse_controlled_scope_parts(
            record.canonical_statement_scope_key
        )

        if len(scope_parts) < 2:
            continue

        ancestor_scope_key = _format_controlled_scope_parts(scope_parts[:-1])
        family_key = (
            record.statement_type,
            record.canonical_statement_value_key,
            ancestor_scope_key,
        )
        records_by_scope_family.setdefault(family_key, []).append(record)

    flagged_record_ids: set[uuid.UUID] = set()
    notes_by_record_id: dict[uuid.UUID, str] = {}

    for family_records in records_by_scope_family.values():
        full_scope_keys = {
            _format_controlled_scope_parts(
                _parse_controlled_scope_parts(record.canonical_statement_scope_key)
            )
            for record in family_records
        }
        immediate_parent_scope_keys = {
            _format_controlled_scope_parts(
                [
                    _parse_controlled_scope_parts(record.canonical_statement_scope_key)[
                        -1
                    ]
                ]
            )
            for record in family_records
        }

        if len(full_scope_keys) <= 1 or len(immediate_parent_scope_keys) <= 1:
            continue

        family_record_ids = sorted(
            str(record.final_sfi_uuid) for record in family_records
        )
        audit_note = (
            f"Same controlled statement value and ancestor scope appears across "
            f"multiple immediate parent scope keys; review upstream source-context "
            f"recovery before accepting these as distinct organizer SFIs. Peer "
            f"final_sfi_uuid values: {family_record_ids}."
        )

        for record in family_records:
            flagged_record_ids.add(record.final_sfi_uuid)
            notes_by_record_id[record.final_sfi_uuid] = audit_note

    audited_records: list[SFIFinalRecord] = []

    for record in sfi_final_records:
        if record.final_sfi_uuid not in flagged_record_ids:
            audited_records.append(record)
            continue

        audit_flags = unique_nonempty(
            [*record.audit_flags, _SUSPICIOUS_CONTROLLED_SCOPE_SPLIT_AUDIT_FLAG]
        )
        audit_notes = unique_nonempty(
            [*record.audit_notes, notes_by_record_id[record.final_sfi_uuid]]
        )
        metadata = dict(record.metadata)
        metadata["suspicious_controlled_scope_split"] = True
        audited_records.append(
            record.model_copy(
                update={
                    "audit_flags": audit_flags,
                    "audit_notes": audit_notes,
                    "metadata": metadata,
                }
            )
        )

    return audited_records


def mint_final_sfi_ids(
    *,
    document_ir: DocumentIR,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
    sfi_candidate_registry: SFIRegistryArtifact,
    sfi_merge_report: SFIMergeReport,
) -> list[SFIFinalRecord]:
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
    list[SFIFinalRecord]
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

    sfi_final_records = [
        _build_sfi_final_record(
            code_group_counts=code_group_counts,
            document_ir=document_ir,
            kg_config=kg_config,
            merge_group=merge_group,
            segment_page_indexes_by_id=segment_page_indexes_by_id,
            sfi_candidates_by_id=sfi_candidates_by_id,
        )
        for merge_group in eligible_merge_groups
    ]

    if not sfi_final_records:
        raise ValueError("Produced zero final SFI records.")

    sfi_final_records = _with_controlled_scope_split_audits(sfi_final_records)

    _validate_final_sfi_records(sfi_final_records)

    sfi_final_summary = _build_sfi_final_summary(
        eligible_merge_group_count=len(eligible_merge_groups),
        excluded_conflict_group_count=len(sfi_merge_report.conflict_groups),
        excluded_needs_review_group_count=len(sfi_merge_report.needs_review_groups),
        sfi_final_records=sfi_final_records,
    )

    make_dir(kg_dirs.root)
    write_to_json(
        fp=kg_dirs.root / "sfi_final_records.json",
        json_info=[record.model_dump(mode="json") for record in sfi_final_records],
    )
    write_to_json(
        fp=kg_dirs.root / "sfi_final_summary.json",
        json_info=sfi_final_summary.model_dump(mode="json"),
    )

    logger.success(
        f"Minted final SFI records: final_sfi_records={len(sfi_final_records)}."
    )

    return sfi_final_records
