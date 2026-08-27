"""This module contains functionalities for finalizing deduplicated SFI merge groups
into deterministic final SFI records.

This module consumes the SFI merge report and candidate registry. It mints stable,
CASE-compatible final StandardsFrameworkItem identifiers and writes the finalized
source-context handoff artifact needed for later relationship resolution. It does not
infer hierarchy, compile final KG objects, or create relationships.
"""

# Standard Library
import hashlib
import json
import uuid

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Literal, Sequence

# Third Party Library
from loguru import logger

# Package Library
from kgfeg.config import Settings
from kgfeg.document_ir.schemas import BlockSegment, DocumentIR, TableSegment
from kgfeg.kgs.schemas import (
    SFIFinalContext,
    SFIFinalRecord,
    SFIFinalSummary,
    SFIMergeGroup,
    SFIMergeReport,
    SFIRegistryArtifact,
    SFIRegistryCandidate,
)
from kgfeg.kgs.sfi_source_anchors import source_anchor_set_signature
from kgfeg.kgs.utils import KGDirs, normalize_text, unique_nonempty
from kgfeg.schemas import CreateKGConfig, normalize_controlled_value_key
from kgfeg.utils.general import make_dir, write_to_json

_CODE_DISAMBIGUATION_METHOD_PARTITION: Literal["dedup_partition_membership"] = (
    "dedup_partition_membership"
)
_CODE_DISAMBIGUATION_METHOD_SOURCE: Literal["source_semantic_basis"] = (
    "source_semantic_basis"
)
_IDENTITY_SECTION_PATH_FALLBACK_LIMIT = 8
_SAME_CODE_DIFFERENT_CONTENT_AUDIT_FLAG = "same_code_different_content"
_SUPPORTED_SYNTHETIC_IDENTITY_FIELDS = frozenset(
    {
        "author",
        "canonical_statement_value",
        "code_scope",
        "country",
        "doc_key",
        "framework_title",
        "grade_level",
        "hierarchy_context",
        "identity_scope",
        "jurisdiction",
        "language",
        "normalized_statement_code",
        "normalized_statement_type",
        "normalized_text",
        "primary_language",
        "provider",
        "statement_type",
        "subject",
    }
)
_CodedIdentityDisambiguationMethod = Literal[
    "dedup_partition_membership", "source_semantic_basis"
]


@dataclass(frozen=True)
class _CodedIdentityDisambiguation:
    """Resolved identity disambiguation for one coded merge group.

    Attributes
    ----------
    disambiguation_method
        Method used to distinguish the group within its coded identity family, or
        `None` when the family contains only one mintable group.
    disambiguator
        Full SHA-256 digest appended to the coded identity key, or `None` when no
        disambiguation is required.
    family_group_count
        Number of mintable merge groups sharing the coded identity family.
    family_key
        Canonical serialized coded identity family key.
    source_semantic_basis_key
        Full SHA-256 digest of the group's source-semantic basis. This is retained for
        audit even when partition membership is required as the final discriminator.
    """

    disambiguation_method: _CodedIdentityDisambiguationMethod | None
    disambiguator: str | None
    family_group_count: int
    family_key: str
    source_semantic_basis_key: str


@dataclass(frozen=True)
class _IdentityBuildResult:
    """Deterministic identity construction details for one final SFI record.

    Attributes
    ----------
    code_identity_disambiguation_method
        Method used to distinguish this coded group from other mintable groups in the
        same coded identity family.
    code_identity_disambiguation_source_basis_key
        SHA-256 key for the source-semantic basis considered before any partition
        membership fallback.
    code_identity_disambiguator
        Stable group discriminator appended when multiple mintable groups share one
        coded identity family.
    code_identity_family_group_count
        Number of mintable groups in the coded identity family, when coded.
    code_identity_family_key
        Serialized configured identity material for a coded SFI, when applicable.
    identity_key
        Complete deterministic identity key used for UUIDv5 minting.
    no_code_identity_disambiguator
        Reserved no-code disambiguator metadata. No-code collisions are rejected rather
        than resolved from incidental provenance.
    no_code_identity_family_key
        Serialized configured identity material for a no-code SFI, when applicable.
    synthetic_key_fields
        Configured synthetic identity field names in their authoritative order.
    synthetic_key_values
        Normalized identity values resolved for the configured fields.
    uses_code_disambiguator
        Whether a stable dedup-group discriminator was appended.
    uses_no_code_disambiguator
        Always false; no-code identity collisions must be fixed through configuration
        or deduplication rather than provenance-derived suffixes.
    """

    code_identity_disambiguation_method: _CodedIdentityDisambiguationMethod | None
    code_identity_disambiguation_source_basis_key: str | None
    code_identity_disambiguator: str | None
    code_identity_family_group_count: int | None
    code_identity_family_key: str | None
    identity_key: str
    no_code_identity_disambiguator: str | None
    no_code_identity_family_key: str | None
    synthetic_key_fields: tuple[str, ...]
    synthetic_key_values: dict[str, str]
    uses_code_disambiguator: bool
    uses_no_code_disambiguator: bool


def _build_coded_identity_disambiguations(
    *,
    eligible_merge_groups: Sequence[SFIMergeGroup],
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
) -> dict[str, _CodedIdentityDisambiguation]:
    """Resolve collision-free identity discriminators for coded merge groups.

    The dedup partition is authoritative: distinct mintable merge groups must remain
    distinct even when a curriculum reuses the same scoped code and visible wording. A
    coded family containing one mintable group keeps its base coded identity. Every
    group in a multi-group family receives a discriminator derived first from stable
    source-semantic evidence. When two groups have the same source-semantic basis, the
    group's exact dedup partition membership is added as the guaranteed fallback.

    Parameters
    ----------
    eligible_merge_groups
        Merge groups eligible for final SFI minting.
    sfi_candidates_by_id
        Current registry candidates keyed by registry candidate ID.

    Returns
    -------
    dict[str, _CodedIdentityDisambiguation]
        Coded merge-group IDs mapped to their resolved family and discriminator data.

    Raises
    ------
    ValueError
        If merge-group IDs are duplicated, source candidates cannot be resolved, or the
        resolved discriminators are not unique within a multi-group coded family.
    """

    duplicate_ids = sorted(
        group_id
        for group_id, count in Counter(
            group.merge_group_id for group in eligible_merge_groups
        ).items()
        if count > 1
    )

    if duplicate_ids:
        raise ValueError(
            f"Eligible SFI merge groups contain duplicate merge_group_id values: "
            f"{duplicate_ids}."
        )

    groups_by_family_key: dict[str, list[SFIMergeGroup]] = defaultdict(list)

    for merge_group in eligible_merge_groups:
        family_key = _build_coded_identity_family_key(merge_group)

        if family_key is not None:
            groups_by_family_key[family_key].append(merge_group)

    resolved: dict[str, _CodedIdentityDisambiguation] = {}

    for family_key, family_groups in sorted(groups_by_family_key.items()):
        ordered_groups = sorted(family_groups, key=lambda group: group.merge_group_id)
        family_group_count = len(ordered_groups)
        source_bases = {
            group.merge_group_id: _build_coded_source_semantic_basis(
                merge_group=group, sfi_candidates_by_id=sfi_candidates_by_id
            )
            for group in ordered_groups
        }
        source_basis_counts = Counter(source_bases.values())

        for merge_group in ordered_groups:
            source_semantic_basis = source_bases[merge_group.merge_group_id]
            source_semantic_basis_key = _hash_identity_basis(source_semantic_basis)
            unique_source_basis = source_basis_counts[source_semantic_basis] == 1

            disambiguation_method: Any = (
                None
                if family_group_count == 1
                else (
                    _CODE_DISAMBIGUATION_METHOD_SOURCE
                    if unique_source_basis
                    else _CODE_DISAMBIGUATION_METHOD_PARTITION
                )
            )
            disambiguator = (
                None
                if family_group_count == 1
                else (
                    source_semantic_basis_key
                    if unique_source_basis
                    else _hash_identity_basis(
                        _build_partition_disambiguation_basis(
                            merge_group=merge_group,
                            source_semantic_basis=source_semantic_basis,
                        )
                    )
                )
            )

            resolved[merge_group.merge_group_id] = _CodedIdentityDisambiguation(
                disambiguation_method=disambiguation_method,
                disambiguator=disambiguator,
                family_group_count=family_group_count,
                family_key=family_key,
                source_semantic_basis_key=source_semantic_basis_key,
            )

        if family_group_count <= 1:
            continue

        family_disambiguation_keys = [
            (
                resolved[group.merge_group_id].disambiguation_method,
                resolved[group.merge_group_id].disambiguator,
            )
            for group in ordered_groups
        ]
        collision_groups = [
            {
                "disambiguation_method": (
                    resolved[group.merge_group_id].disambiguation_method
                ),
                "disambiguator": resolved[group.merge_group_id].disambiguator,
                "merge_group_id": group.merge_group_id,
                "registry_candidate_ids": group.registry_candidate_ids,
            }
            for group in ordered_groups
        ]

        if None in {value for pair in family_disambiguation_keys for value in pair}:
            raise ValueError(
                f"Every mintable merge group in a multi-group coded identity family "
                f"must receive a complete disambiguation method and value. "
                f"Family={family_key!r}; groups={collision_groups}."
            )

        if len(family_disambiguation_keys) != len(set(family_disambiguation_keys)):
            raise ValueError(
                f"Coded identity disambiguators are not unique within one mintable "
                f"coded family. Family={family_key!r}; groups={collision_groups}."
            )

    return resolved


def _build_coded_identity_family_key(merge_group: SFIMergeGroup) -> str | None:
    """Build the canonical identity-family key for one coded merge group.

    Parameters
    ----------
    merge_group
        Merge group whose canonical code identity should be serialized.

    Returns
    -------
    str | None
        Canonical JSON family key, or `None` when the group is uncoded.

    Raises
    ------
    ValueError
        If a coded merge group lacks a canonical code type.
    """

    canonical_normalized_code = merge_group.canonical_normalized_statement_code

    if canonical_normalized_code is None:
        return None

    canonical_code_type = merge_group.canonical_code_type

    if canonical_code_type is None:
        raise ValueError(
            f"Coded merge group {merge_group.merge_group_id!r} has no canonical_code_type."
        )

    return json.dumps(
        {
            "code_scope": [
                [
                    normalize_controlled_value_key(statement_type),
                    normalize_controlled_value_key(value),
                ]
                for statement_type, value in sorted(
                    merge_group.code_scope_values.items()
                )
            ],
            "code_type": normalize_text(canonical_code_type),
            "identity_kind": "coded",
            "normalized_statement_code": normalize_text(canonical_normalized_code),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _build_coded_source_semantic_basis(
    *, merge_group: SFIMergeGroup, sfi_candidates_by_id: dict[str, SFIRegistryCandidate]
) -> str:
    """Build stable source-semantic identity evidence for one coded merge group.

    The basis excludes extraction-window overlap and representative selection. Exact
    description-anchor sets are deduplicated so repeated extraction of one physical
    occurrence does not alter identity. The basis is intended to distinguish separate
    coded groups whenever the source itself exposes a stable semantic difference.

    Parameters
    ----------
    merge_group
        Coded merge group whose source-semantic basis should be built.
    sfi_candidates_by_id
        Current registry candidates keyed by registry candidate ID.

    Returns
    -------
    str
        Canonical JSON source-semantic basis.

    Raises
    ------
    ValueError
        If the group references an unknown registry candidate or contains no exact
        description-anchor evidence.
    """

    source_occurrences: set[tuple[tuple[str, int, str], ...]] = set()

    for candidate_id in merge_group.registry_candidate_ids:
        candidate = sfi_candidates_by_id.get(candidate_id)

        if candidate is None:
            raise ValueError(
                f"Merge group {merge_group.merge_group_id!r} references unknown "
                f"registry candidate {candidate_id!r} while building coded identity."
            )

        occurrence_signature = source_anchor_set_signature(
            candidate.description_source_anchors
        )

        if not occurrence_signature:
            raise ValueError(
                f"Registry candidate {candidate_id!r} has no exact description "
                f"anchors for coded identity disambiguation."
            )

        source_occurrences.add(occurrence_signature)

    return json.dumps(
        {
            "canonical_normalized_statement_type": normalize_text(
                _canonical_normalized_statement_type(merge_group)
            ),
            "canonical_statement_type": normalize_text(
                _canonical_statement_type(merge_group)
            ),
            "canonical_statement_value": normalize_controlled_value_key(
                merge_group.canonical_statement_value_key
                or merge_group.canonical_statement_value
                or ""
            ),
            "identity_scope": [
                [
                    normalize_controlled_value_key(statement_type),
                    normalize_controlled_value_key(value),
                ]
                for statement_type, value in sorted(
                    merge_group.identity_scope_values.items()
                )
            ],
            "source_occurrences": sorted(source_occurrences),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _build_controlled_value_alias_maps(
    kg_config: CreateKGConfig,
) -> dict[str, dict[str, str]]:
    """Build normalized controlled-value alias maps by statement type.

    Parameters
    ----------
    kg_config
        Runtime KG configuration containing statement-type controlled values.

    Returns
    -------
    dict[str, dict[str, str]]
        Statement types mapped to normalized aliases and canonical source values.
    """

    alias_maps: dict[str, dict[str, str]] = {}

    for policy_item in kg_config.academic_standards.statement_type_policy:
        alias_to_canonical: dict[str, str] = {}

        for controlled_value in policy_item.controlled_values:
            for alias in [controlled_value.canonical_value, *controlled_value.aliases]:
                alias_key = normalize_controlled_value_key(alias)

                if alias_key:
                    alias_to_canonical[alias_key] = controlled_value.canonical_value

        if alias_to_canonical:
            alias_maps[policy_item.statement_type] = alias_to_canonical

    return alias_maps


def _build_final_sfi_collision_details(
    *, key_name: str, records: Sequence[SFIFinalRecord], values: Sequence[str]
) -> list[dict[str, Any]]:
    """Build compact diagnostics for duplicate final SFI identity values.

    Parameters
    ----------
    key_name
        Name of the duplicated identity field.
    records
        Final SFI records being validated.
    values
        Identity field values aligned positionally with `records`.

    Returns
    -------
    list[dict[str, Any]]
        Collision diagnostics keyed by duplicate identity field value.
    """

    duplicate_values = sorted({value for value in values if values.count(value) > 1})
    collision_details: list[dict[str, Any]] = []

    for duplicate_value in duplicate_values:
        duplicate_records = [
            record for record, value in zip(records, values) if value == duplicate_value
        ]
        collision_details.append(
            {
                key_name: duplicate_value,
                "records": [
                    {
                        "canonical_statement_value_key": record.canonical_statement_value_key,
                        "description": record.description,
                        "identity_key": record.identity_key,
                        "merge_group_id": record.merge_group_id,
                        "source_registry_candidate_ids": record.source_registry_candidate_ids,
                        "statement_type": record.statement_type,
                    }
                    for record in duplicate_records
                ],
            }
        )

    return collision_details


def _build_grade_level_identity_value(
    *,
    kg_config: CreateKGConfig,
    merge_group: SFIMergeGroup,
    section_paths: Sequence[Sequence[str]],
) -> str:
    """Resolve configured identity-scope values for the `grade_level` field.

    The conventional field name remains curriculum-neutral: any configured semantic
    identity-scope statement type may contribute. Resolved registry scope is
    authoritative; section paths are used only as a deterministic source-visible
    fallback for configured scope types.

    Parameters
    ----------
    kg_config
        Runtime KG configuration containing identity-scope and controlled-value policy.
    merge_group
        Merge group whose stable semantic scope should be resolved.
    section_paths
        Source section paths recovered from the group's DocumentIR segments.

    Returns
    -------
    str
        Sorted normalized canonical scope assignments joined by `|`.
    """

    scope_statement_types = _configured_identity_scope_statement_types(
        kg_config=kg_config, merge_group=merge_group
    )
    resolved_values_by_type: dict[str, set[str]] = {
        statement_type: set() for statement_type in scope_statement_types
    }

    for statement_type, value in merge_group.identity_scope_values.items():
        if statement_type not in resolved_values_by_type:
            continue

        value_key = normalize_controlled_value_key(value)

        if value_key:
            resolved_values_by_type[statement_type].add(value_key)

    alias_maps = _build_controlled_value_alias_maps(kg_config)

    for statement_type in scope_statement_types:
        if resolved_values_by_type[statement_type]:
            continue

        alias_to_canonical = alias_maps.get(statement_type, {})

        for section_path in section_paths:
            canonical_value = next(
                (
                    matched_value
                    for section_label in reversed(section_path)
                    if (
                        matched_value := alias_to_canonical.get(
                            normalize_controlled_value_key(section_label)
                        )
                    )
                ),
                None,
            )

            if canonical_value:
                resolved_values_by_type[statement_type].add(
                    normalize_controlled_value_key(canonical_value)
                )

    return "|".join(
        f"{normalize_controlled_value_key(statement_type)}={value_key}"
        for statement_type in scope_statement_types
        for value_key in sorted(resolved_values_by_type[statement_type])
    )


def _build_hierarchy_context_identity_value(
    *,
    kg_config: CreateKGConfig,
    merge_group: SFIMergeGroup,
    section_paths: Sequence[Sequence[str]],
) -> str:
    """Build stable hierarchy context from semantic identity scope and sections.

    Configured semantic identity-scope assignments are authoritative. Local DocumentIR
    section-path suffixes provide additional stable source context. Segment IDs,
    windows, row indexes, and overlap-copy membership are excluded from identity.

    Parameters
    ----------
    kg_config
        Runtime KG configuration defining semantic identity scope.
    merge_group
        Merge group containing resolved semantic identity-scope values.
    section_paths
        Source section paths recovered from the group's DocumentIR segments.

    Returns
    -------
    str
        Sorted unique normalized hierarchy-context components.
    """

    components = {
        f"scope:{normalize_controlled_value_key(statement_type)}="
        f"{normalize_controlled_value_key(value)}"
        for statement_type, value in merge_group.identity_scope_values.items()
        if normalize_controlled_value_key(statement_type)
        and normalize_controlled_value_key(value)
    }
    scope_statement_types = _configured_identity_scope_statement_types(
        kg_config=kg_config, merge_group=merge_group
    )
    alias_maps = _build_controlled_value_alias_maps(kg_config)
    scope_alias_keys = {
        alias_key
        for statement_type in scope_statement_types
        for alias_key in alias_maps.get(statement_type, {})
    }

    for section_path in section_paths:
        normalized_labels = [
            normalize_controlled_value_key(label) for label in section_path
        ]
        anchor_index = next(
            (
                index
                for index in range(len(normalized_labels) - 1, -1, -1)
                if normalized_labels[index] in scope_alias_keys
            ),
            None,
        )
        local_labels = (
            normalized_labels[anchor_index:]
            if anchor_index is not None
            else normalized_labels[-_IDENTITY_SECTION_PATH_FALLBACK_LIMIT:]
        )
        local_labels = [label for label in local_labels if label]

        if local_labels:
            components.add("section_path:" + ">".join(local_labels))

    return "||".join(sorted(components))


def _build_identity_key(
    *,
    coded_identity_disambiguation: _CodedIdentityDisambiguation | None,
    document_ir: DocumentIR,
    kg_config: CreateKGConfig,
    merge_group: SFIMergeGroup,
    representative_candidate: SFIRegistryCandidate,
    segments_by_id: dict[str, BlockSegment | TableSegment],
) -> _IdentityBuildResult:
    """Build a stable coded or synthetic identity string for UUIDv5 minting.

    Coded SFIs use a structural identity family built from the document key, canonical
    code type, canonical configured scope, and canonical normalized code. When more
    than one mintable merge group shares that family, the precomputed dedup-group
    discriminator is appended. Uncoded SFIs use the configured synthetic fields.

    Parameters
    ----------
    coded_identity_disambiguation
        Precomputed coded-family and dedup-group identity details, or `None` for an
        uncoded merge group.
    document_ir
        Source DocumentIR whose document key scopes the identity namespace.
    kg_config
        Runtime KG configuration carrying synthetic identity policy for uncoded SFIs.
    merge_group
        Merge group containing resolved canonical type, code, and scope fields.
    representative_candidate
        Source-backed candidate supplying final text and language.
    segments_by_id
        DocumentIR block/table segments keyed by segment ID.

    Returns
    -------
    _IdentityBuildResult
        Complete identity key and auditable identity metadata.

    Raises
    ------
    ValueError
        If configured synthetic fields are unsupported or coded-family identity data is
        missing or inconsistent with the merge group.
    """

    synthetic_key_fields = tuple(
        kg_config.academic_standards.synthetic_merge_key_fields
    )
    representative_normalized_text = (
        representative_candidate.normalized_description
        or normalize_text(representative_candidate.description)
    )
    normalized_text_value = (
        merge_group.canonical_statement_value_key or representative_normalized_text
    )
    canonical_normalized_code = merge_group.canonical_normalized_statement_code

    if canonical_normalized_code is not None:
        identity_family_key = _build_coded_identity_family_key(merge_group)

        if identity_family_key is None:
            raise ValueError(
                f"Coded merge group {merge_group.merge_group_id!r} did not produce "
                f"a coded identity family key."
            )

        if coded_identity_disambiguation is None:
            raise ValueError(
                f"Coded merge group {merge_group.merge_group_id!r} has no resolved "
                f"coded identity disambiguation record."
            )

        if coded_identity_disambiguation.family_key != identity_family_key:
            raise ValueError(
                f"Coded merge group {merge_group.merge_group_id!r} identity family "
                f"does not match its precomputed disambiguation record."
            )

        uses_code_disambiguator = (
            coded_identity_disambiguation.disambiguator is not None
        )

        if uses_code_disambiguator != (
            coded_identity_disambiguation.family_group_count > 1
        ):
            raise ValueError(
                f"Coded merge group {merge_group.merge_group_id!r} has inconsistent "
                f"coded-family size and disambiguator presence."
            )

        identity_key = f"lc:curriculum:{document_ir.doc_key}:sfi:{identity_family_key}"

        if uses_code_disambiguator:
            disambiguation_method = coded_identity_disambiguation.disambiguation_method
            disambiguator = coded_identity_disambiguation.disambiguator

            if not disambiguation_method or not disambiguator:
                raise ValueError(
                    f"Coded merge group {merge_group.merge_group_id!r} requires a "
                    f"complete coded identity disambiguation method and value."
                )

            identity_key = (
                f"{identity_key}:dedup-group:{disambiguation_method}:{disambiguator}"
            )

        return _IdentityBuildResult(
            code_identity_disambiguation_method=(
                coded_identity_disambiguation.disambiguation_method
            ),
            code_identity_disambiguation_source_basis_key=(
                coded_identity_disambiguation.source_semantic_basis_key
            ),
            code_identity_disambiguator=(coded_identity_disambiguation.disambiguator),
            code_identity_family_group_count=(
                coded_identity_disambiguation.family_group_count
            ),
            code_identity_family_key=identity_family_key,
            identity_key=identity_key,
            no_code_identity_disambiguator=None,
            no_code_identity_family_key=None,
            synthetic_key_fields=(),
            synthetic_key_values={},
            uses_code_disambiguator=uses_code_disambiguator,
            uses_no_code_disambiguator=False,
        )

    if coded_identity_disambiguation is not None:
        raise ValueError(
            f"Uncoded merge group {merge_group.merge_group_id!r} unexpectedly has "
            f"coded identity disambiguation data."
        )

    unsupported_fields = sorted(
        set(synthetic_key_fields) - _SUPPORTED_SYNTHETIC_IDENTITY_FIELDS
    )

    if unsupported_fields:
        raise ValueError(
            f"Unsupported synthetic_merge_key_fields for no-code SFI finalization: "
            f"{unsupported_fields}. Supported fields are "
            f"{sorted(_SUPPORTED_SYNTHETIC_IDENTITY_FIELDS)}."
        )

    section_paths = _recover_merge_group_section_paths(
        merge_group=merge_group, segments_by_id=segments_by_id
    )
    available_values = {
        "author": normalize_text(kg_config.metadata.author),
        "canonical_statement_value": normalize_controlled_value_key(
            merge_group.canonical_statement_value_key
            or merge_group.canonical_statement_value
            or ""
        ),
        "code_scope": "|".join(
            f"{normalize_controlled_value_key(statement_type)}="
            f"{normalize_controlled_value_key(value)}"
            for statement_type, value in sorted(merge_group.code_scope_values.items())
        ),
        "country": normalize_text(kg_config.metadata.country),
        "doc_key": normalize_text(document_ir.doc_key),
        "framework_title": normalize_text(kg_config.metadata.framework_title),
        "grade_level": _build_grade_level_identity_value(
            kg_config=kg_config,
            merge_group=merge_group,
            section_paths=section_paths,
        ),
        "hierarchy_context": _build_hierarchy_context_identity_value(
            kg_config=kg_config,
            merge_group=merge_group,
            section_paths=section_paths,
        ),
        "identity_scope": "|".join(
            f"{normalize_controlled_value_key(statement_type)}="
            f"{normalize_controlled_value_key(value)}"
            for statement_type, value in merge_group.identity_scope_values.items()
        ),
        "jurisdiction": normalize_text(kg_config.metadata.jurisdiction),
        "language": normalize_text(representative_candidate.language),
        "normalized_statement_code": "",
        "normalized_statement_type": normalize_text(
            _canonical_normalized_statement_type(merge_group)
        ),
        "normalized_text": normalize_text(normalized_text_value),
        "primary_language": normalize_text(kg_config.metadata.primary_language),
        "provider": normalize_text(kg_config.metadata.provider),
        "statement_type": normalize_text(_canonical_statement_type(merge_group)),
        "subject": normalize_text(kg_config.metadata.subject),
    }
    synthetic_key_values = {
        field_name: available_values[field_name] for field_name in synthetic_key_fields
    }
    identity_family_key = json.dumps(
        {
            "fields": [
                [field_name, synthetic_key_values[field_name]]
                for field_name in synthetic_key_fields
            ],
            "identity_kind": "synthetic",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    identity_key = f"lc:curriculum:{document_ir.doc_key}:sfi:{identity_family_key}"

    return _IdentityBuildResult(
        code_identity_disambiguation_method=None,
        code_identity_disambiguation_source_basis_key=None,
        code_identity_disambiguator=None,
        code_identity_family_group_count=None,
        code_identity_family_key=None,
        identity_key=identity_key,
        no_code_identity_disambiguator=None,
        no_code_identity_family_key=identity_family_key,
        synthetic_key_fields=synthetic_key_fields,
        synthetic_key_values=synthetic_key_values,
        uses_code_disambiguator=False,
        uses_no_code_disambiguator=False,
    )


def _build_partition_disambiguation_basis(
    *, merge_group: SFIMergeGroup, source_semantic_basis: str
) -> str:
    """Add exact dedup partition membership to a source-semantic basis.

    This fallback is used only when separate mintable groups in one coded family have
    identical source-semantic evidence. Exact merge-group membership is then the only
    structured signal that expresses the dedup stage's decision to keep them separate.

    Parameters
    ----------
    merge_group
        Merge group whose exact candidate membership should be included.
    source_semantic_basis
        Canonical source-semantic basis shared with another group.

    Returns
    -------
    str
        Canonical JSON basis containing source evidence and partition membership.

    Raises
    ------
    ValueError
        If the merge group has no registry candidate membership.
    """

    registry_candidate_ids = sorted(set(merge_group.registry_candidate_ids))

    if not registry_candidate_ids:
        raise ValueError(
            f"Merge group {merge_group.merge_group_id!r} has no registry candidate "
            f"membership for coded identity disambiguation."
        )

    return json.dumps(
        {
            "dedup_partition_members": registry_candidate_ids,
            "source_semantic_basis": source_semantic_basis,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _build_sfi_final_contexts(
    *,
    document_ir: DocumentIR,
    kg_config: CreateKGConfig,
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
    sfi_final_records: Sequence[SFIFinalRecord],
) -> list[SFIFinalContext]:
    """Recover and package row-aware source context for finalized SFI records.

    The returned context artifact is the handoff to SFI hasChild resolution. Source
    order is assigned from the earliest actual source occurrence using DocumentIR
    segment order, cited table row/header position, extraction-window order, and the
    candidate's source-order position within that window. This prevents all records in
    one stitched table from collapsing to the same source order.

    Parameters
    ----------
    document_ir
        Source DocumentIR used to recover segment, row, and section-path evidence.
    kg_config
        Runtime KG configuration containing the recent section-path label bound.
    sfi_candidates_by_id
        Registry candidates keyed by candidate ID for row-aware source ordering.
    sfi_final_records
        Finalized SFI records to convert into relationship-resolution contexts.

    Returns
    -------
    list[SFIFinalContext]
        Final SFI contexts in deterministic source order.
    """

    contexts: list[SFIFinalContext] = []
    segment_order_by_id = {
        segment.segment_id: index for index, segment in enumerate(document_ir.segments)
    }
    segments_by_id = {segment.segment_id: segment for segment in document_ir.segments}
    ordered_records = sorted(
        sfi_final_records,
        key=lambda record: _final_record_source_sort_key(
            record=record,
            segment_order_by_id=segment_order_by_id,
            segments_by_id=segments_by_id,
            sfi_candidates_by_id=sfi_candidates_by_id,
        ),
    )

    for source_order, record in enumerate(ordered_records):
        section_path_labels = unique_nonempty(
            list(
                reversed(
                    _recover_section_path_labels(
                        record=record, segments_by_id=segments_by_id
                    )
                )
            )
        )[: kg_config.academic_standards.max_has_child_section_path_labels]
        table_header_indexes = _source_ref_int_values(
            key="table_header_indexes", record=record
        )
        table_row_indexes = _source_ref_int_values(
            key="table_row_indexes", record=record
        )
        source_context_labels = _source_ref_text_values(
            key="source_context_labels", record=record
        )

        if not source_context_labels:
            raise ValueError(
                f"Final SFI {record.final_sfi_uuid} has no source_context_labels "
                f"in candidate_source_refs; rerun SFI registry and dedup with "
                f"source-context-label support before SFI finalization."
            )

        contexts.append(
            SFIFinalContext(
                audit_flags=record.audit_flags,
                candidate_source_texts=record.candidate_source_texts,
                canonical_statement_value=record.canonical_statement_value,
                canonical_statement_value_key=record.canonical_statement_value_key,
                description=record.description,
                final_sfi_uuid=record.final_sfi_uuid,
                identity_scope_key=record.identity_scope_key,
                identity_scope_values=record.identity_scope_values,
                normalized_statement_code=record.normalized_statement_code,
                normalized_statement_type=record.normalized_statement_type,
                section_path_labels=section_path_labels,
                source_context_keys=record.source_context_keys,
                source_context_labels=source_context_labels,
                source_order=source_order,
                source_page_indexes=record.source_page_indexes,
                source_registry_candidate_ids=record.source_registry_candidate_ids,
                source_segment_ids=record.source_segment_ids,
                source_window_ids=record.source_window_ids,
                source_window_indexes=record.source_window_indexes,
                statement_code=record.statement_code,
                statement_type=record.statement_type,
                table_header_indexes=table_header_indexes,
                table_row_indexes=table_row_indexes,
            )
        )

    return contexts


def _build_sfi_final_record(
    *,
    coded_identity_disambiguation: _CodedIdentityDisambiguation | None,
    document_ir: DocumentIR,
    kg_config: CreateKGConfig,
    merge_group: SFIMergeGroup,
    segments_by_id: dict[str, BlockSegment | TableSegment],
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
) -> SFIFinalRecord:
    """Build one deterministic final SFI record from one eligible merge group.

    Parameters
    ----------
    coded_identity_disambiguation
        Precomputed coded-family discriminator details, or `None` for an uncoded merge
        group.
    document_ir
        Source DocumentIR used for identity and source provenance recovery.
    kg_config
        Runtime KG configuration with framework metadata and identity policy.
    merge_group
        Eligible SFI merge group to mint as a final SFI.
    segments_by_id
        DocumentIR block/table segments keyed by segment ID.
    sfi_candidates_by_id
        Registry candidates keyed by registry candidate ID.

    Returns
    -------
    SFIFinalRecord
        Deterministic final SFI record.
    """

    representative_candidate = _get_representative_candidate(
        merge_group=merge_group, sfi_candidates_by_id=sfi_candidates_by_id
    )
    identity_result = _build_identity_key(
        coded_identity_disambiguation=coded_identity_disambiguation,
        document_ir=document_ir,
        kg_config=kg_config,
        merge_group=merge_group,
        representative_candidate=representative_candidate,
        segments_by_id=segments_by_id,
    )
    final_sfi_uuid = uuid.uuid5(
        Settings.LC_CANONICAL_NAMESPACE_UUID, identity_result.identity_key
    )
    source_context_keys = _source_context_keys(merge_group)
    source_page_indexes = _build_source_page_indexes(
        merge_group=merge_group, segments_by_id=segments_by_id
    )
    statement_type = _canonical_statement_type(merge_group)
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
        canonical_code_source_candidate_id=(
            merge_group.canonical_code_source_candidate_id
        ),
        canonical_code_type=merge_group.canonical_code_type,
        canonical_normalized_statement_code=(
            merge_group.canonical_normalized_statement_code
        ),
        canonical_statement_code=merge_group.canonical_statement_code,
        canonical_statement_value=merge_group.canonical_statement_value,
        canonical_statement_value_key=merge_group.canonical_statement_value_key,
        canonical_type_selection_reason=(merge_group.canonical_type_selection_reason),
        canonical_type_source_candidate_id=(
            merge_group.canonical_type_source_candidate_id
        ),
        case_identifier_uri=f"urn:uuid:{final_sfi_uuid}",
        case_identifier_uuid=final_sfi_uuid,
        code_resolution_method=merge_group.code_resolution_method,
        code_resolution_reason=merge_group.code_resolution_reason,
        confidence_max=merge_group.confidence_max,
        confidence_min=merge_group.confidence_min,
        description=representative_candidate.description,
        final_sfi_uuid=final_sfi_uuid,
        identifier=final_sfi_uuid,
        identity_key=identity_result.identity_key,
        identity_scope_key=merge_group.identity_scope_key,
        identity_scope_values=merge_group.identity_scope_values,
        in_language=representative_candidate.language,
        jurisdiction=kg_config.metadata.jurisdiction,
        language=representative_candidate.language,
        license=kg_config.metadata.license,
        merge_decision=merge_group.merge_decision,
        merge_group_id=merge_group.merge_group_id,
        merge_reason=merge_group.merge_reason,
        metadata={
            "code_resolution": {
                "canonical_code_source_candidate_id": (
                    merge_group.canonical_code_source_candidate_id
                ),
                "canonical_code_type": merge_group.canonical_code_type,
                "canonical_normalized_statement_code": (
                    merge_group.canonical_normalized_statement_code
                ),
                "canonical_statement_code": merge_group.canonical_statement_code,
                "method": merge_group.code_resolution_method,
                "reason": merge_group.code_resolution_reason,
                "source_normalized_statement_codes": (
                    merge_group.normalized_statement_codes
                ),
                "source_statement_codes": merge_group.statement_codes,
            },
            "country": kg_config.metadata.country,
            "doc_key": document_ir.doc_key,
            "framework_title": kg_config.metadata.framework_title,
            "identity": {
                "code_identity_disambiguation_method": (
                    identity_result.code_identity_disambiguation_method
                ),
                "code_identity_disambiguation_source_basis_key": (
                    identity_result.code_identity_disambiguation_source_basis_key
                ),
                "code_identity_disambiguator": (
                    identity_result.code_identity_disambiguator
                ),
                "code_identity_family_group_count": (
                    identity_result.code_identity_family_group_count
                ),
                "code_identity_family_key": identity_result.code_identity_family_key,
                "namespace_uuid": str(Settings.LC_CANONICAL_NAMESPACE_UUID),
                "no_code_identity_disambiguator": (
                    identity_result.no_code_identity_disambiguator
                ),
                "no_code_identity_family_key": (
                    identity_result.no_code_identity_family_key
                ),
                "synthetic_merge_key_fields": list(
                    identity_result.synthetic_key_fields
                ),
                "synthetic_merge_key_values": identity_result.synthetic_key_values,
                "uses_code_disambiguator": identity_result.uses_code_disambiguator,
                "uses_no_code_disambiguator": (
                    identity_result.uses_no_code_disambiguator
                ),
            },
            "identity_scope": {
                "key": merge_group.identity_scope_key,
                "values": merge_group.identity_scope_values,
            },
            "pdf_name": document_ir.pdf_name,
            "primary_language": kg_config.metadata.primary_language,
            "same_code_different_content": (
                _SAME_CODE_DIFFERENT_CONTENT_AUDIT_FLAG in merge_group.audit_flags
            ),
            "statement_value_canonicalization": {
                "canonical_statement_value": merge_group.canonical_statement_value,
                "canonical_statement_value_key": (
                    merge_group.canonical_statement_value_key
                ),
            },
            "type_resolution": {
                "canonical_normalized_statement_type": (
                    merge_group.canonical_normalized_statement_type
                ),
                "canonical_statement_type": merge_group.canonical_statement_type,
                "canonical_type_selection_reason": (
                    merge_group.canonical_type_selection_reason
                ),
                "canonical_type_source_candidate_id": (
                    merge_group.canonical_type_source_candidate_id
                ),
                "source_normalized_statement_types": (
                    merge_group.normalized_statement_types
                ),
                "source_statement_types": merge_group.statement_types,
            },
        },
        normalized_statement_code=merge_group.canonical_normalized_statement_code,
        normalized_statement_type=_canonical_normalized_statement_type(merge_group),
        provider=kg_config.metadata.provider,
        representative_candidate_id=representative_candidate.registry_candidate_id,
        source_context_keys=source_context_keys,
        source_normalized_statement_codes=merge_group.normalized_statement_codes,
        source_page_indexes=source_page_indexes,
        source_registry_candidate_ids=merge_group.registry_candidate_ids,
        source_segment_ids=merge_group.source_segment_ids,
        source_statement_codes=merge_group.statement_codes,
        source_window_ids=merge_group.source_window_ids,
        source_window_indexes=merge_group.source_window_indexes,
        statement_code=merge_group.canonical_statement_code,
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


def _build_source_page_indexes(
    *,
    merge_group: SFIMergeGroup,
    segments_by_id: dict[str, BlockSegment | TableSegment],
) -> list[int]:
    """Recover the narrowest available page provenance for one merge group.

    Table candidates use their cited raw header/body row indexes and aligned
    `TableSegment.row_provenance`. Block candidates use segment provenance. When an
    external DocumentIR lacks optional table row provenance, the function falls back to
    the table segment's page set rather than inventing an exact page.

    Parameters
    ----------
    merge_group
        Merge group preserving per-candidate source references.
    segments_by_id
        DocumentIR block/table segments keyed by segment ID.

    Returns
    -------
    list[int]
        Sorted unique zero-based source page indexes.

    Raises
    ------
    ValueError
        If a source reference points to a missing segment or an invalid table row.
    """

    page_indexes: set[int] = set()
    referenced_segment_ids: set[str] = set()

    for source_ref in merge_group.candidate_source_refs:
        if not isinstance(source_ref, dict):
            continue

        header_indexes = _source_ref_index_values(
            key="table_header_indexes", source_ref=source_ref
        )
        row_indexes = _source_ref_index_values(
            key="table_row_indexes", source_ref=source_ref
        )
        source_segment_ids = [
            str(value).strip()
            for value in source_ref.get("source_segment_ids") or []
            if str(value).strip()
        ]

        for source_segment_id in source_segment_ids:
            referenced_segment_ids.add(source_segment_id)
            segment = segments_by_id.get(source_segment_id)

            if not isinstance(segment, (BlockSegment, TableSegment)):
                raise ValueError(
                    f"Merge group {merge_group.merge_group_id!r} references missing "
                    f"or unsupported source segment {source_segment_id!r}."
                )

            if isinstance(segment, TableSegment) and (header_indexes or row_indexes):
                page_indexes.update(
                    _table_reference_page_indexes(
                        header_indexes=header_indexes,
                        row_indexes=row_indexes,
                        table_segment=segment,
                    )
                )
            else:
                page_indexes.update(_segment_page_indexes(segment))

    for source_segment_id in merge_group.source_segment_ids:
        if source_segment_id in referenced_segment_ids:
            continue

        segment = segments_by_id.get(source_segment_id)

        if not isinstance(segment, (BlockSegment, TableSegment)):
            raise ValueError(
                f"Merge group {merge_group.merge_group_id!r} references missing or "
                f"unsupported source segment {source_segment_id!r}."
            )

        page_indexes.update(_segment_page_indexes(segment))

    return sorted(page_indexes)


def _candidate_source_sort_key(
    *,
    candidate: SFIRegistryCandidate,
    segment_order_by_id: dict[str, int],
    segments_by_id: dict[str, BlockSegment | TableSegment],
) -> tuple[int, int, int, int, str]:
    """Build a row-aware source-order key for one registry candidate.

    Parameters
    ----------
    candidate
        Registry candidate carrying source segment and table-row references.
    segment_order_by_id
        DocumentIR segment indexes keyed by segment ID.
    segments_by_id
        DocumentIR segments keyed by segment ID.

    Returns
    -------
    tuple[int, int, int, int, str]
        Segment order, local row position, window index, candidate position, and stable
        candidate ID.

    Raises
    ------
    ValueError
        If the candidate references no known source segment.
    """

    source_positions: list[tuple[int, int]] = []

    for source_segment_id in candidate.source_segment_ids:
        segment = segments_by_id.get(source_segment_id)
        segment_order = segment_order_by_id.get(source_segment_id)

        if (
            not isinstance(segment, (BlockSegment, TableSegment))
            or segment_order is None
        ):
            continue

        local_positions = [
            *candidate.table_header_indexes,
            *candidate.table_row_indexes,
        ]
        local_position = min(local_positions) if local_positions else 0
        source_positions.append((segment_order, local_position))

    if not source_positions:
        raise ValueError(
            f"Registry candidate {candidate.registry_candidate_id!r} references no "
            "known DocumentIR source segment."
        )

    segment_order, local_position = min(source_positions)
    return (
        segment_order,
        local_position,
        candidate.window_index,
        candidate.source_window_candidate_index,
        candidate.registry_candidate_id,
    )


def _canonical_normalized_statement_type(merge_group: SFIMergeGroup) -> str:
    """Return the resolved canonical normalized type for a mintable merge group.

    Parameters
    ----------
    merge_group
        Merge group to inspect.

    Returns
    -------
    str
        Canonical normalized statement type.

    Raises
    ------
    ValueError
        If the merge group lacks a resolved canonical normalized statement type.
    """

    value = merge_group.canonical_normalized_statement_type

    if not value:
        raise ValueError(
            f"Merge group {merge_group.merge_group_id!r} has no resolved canonical "
            f"normalized statement type for final SFI minting."
        )

    return value


def _canonical_statement_type(merge_group: SFIMergeGroup) -> str:
    """Return the resolved canonical source-facing type for a mintable group.

    Parameters
    ----------
    merge_group
        Merge group to inspect.

    Returns
    -------
    str
        Canonical source-facing statement type.

    Raises
    ------
    ValueError
        If the merge group lacks a resolved canonical statement type.
    """

    value = merge_group.canonical_statement_type

    if not value:
        raise ValueError(
            f"Merge group {merge_group.merge_group_id!r} has no resolved canonical "
            f"statement type for final SFI minting."
        )

    return value


def _configured_identity_scope_statement_types(
    *, kg_config: CreateKGConfig, merge_group: SFIMergeGroup
) -> list[str]:
    """Return configured semantic identity-scope types for one merge group.

    Parameters
    ----------
    kg_config
        Runtime KG configuration containing identity-scope policy.
    merge_group
        Merge group whose canonical statement type selects the applicable policy.

    Returns
    -------
    list[str]
        Ordered configured identity-scope statement types.
    """

    statement_type = _canonical_statement_type(merge_group)
    return list(
        kg_config.academic_standards.identity_scope_statement_types.get(
            statement_type, []
        )
    )


def _final_record_source_sort_key(
    *,
    record: SFIFinalRecord,
    segment_order_by_id: dict[str, int],
    segments_by_id: dict[str, BlockSegment | TableSegment],
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
) -> tuple[int, int, int, int, str]:
    """Build the earliest row-aware source-order key for one final SFI record.

    Parameters
    ----------
    record
        Final SFI record preserving registry candidate IDs.
    segment_order_by_id
        DocumentIR segment indexes keyed by segment ID.
    segments_by_id
        DocumentIR segments keyed by segment ID.
    sfi_candidates_by_id
        Current registry candidates keyed by candidate ID.

    Returns
    -------
    tuple[int, int, int, int, str]
        Earliest source-order key among all source occurrences in the final SFI.

    Raises
    ------
    ValueError
        If none of the final record's source candidates can be resolved.
    """

    candidate_keys = [
        _candidate_source_sort_key(
            candidate=sfi_candidates_by_id[candidate_id],
            segment_order_by_id=segment_order_by_id,
            segments_by_id=segments_by_id,
        )
        for candidate_id in record.source_registry_candidate_ids
        if candidate_id in sfi_candidates_by_id
    ]

    if not candidate_keys:
        raise ValueError(
            f"Final SFI {record.final_sfi_uuid} has no resolvable registry candidate "
            "for row-aware source ordering."
        )

    return min(candidate_keys)


def _get_representative_candidate(
    *, merge_group: SFIMergeGroup, sfi_candidates_by_id: dict[str, SFIRegistryCandidate]
) -> SFIRegistryCandidate:
    """Return the validated representative candidate for a mintable merge group.

    Parameters
    ----------
    merge_group
        Merge group whose representative source-facing candidate should be resolved.
    sfi_candidates_by_id
        Registry candidates keyed by registry candidate ID.

    Returns
    -------
    SFIRegistryCandidate
        Existing registry candidate whose description and language should be copied to
        the final SFI record.

    Raises
    ------
    ValueError
        If the representative ID is missing, outside the merge group, or unknown to
        the current candidate registry.
    """

    representative_candidate_id = merge_group.representative_candidate_id

    if representative_candidate_id is None:
        raise ValueError(
            f"Eligible merge group {merge_group.merge_group_id!r} has no "
            f"representative_candidate_id."
        )

    if representative_candidate_id not in merge_group.registry_candidate_ids:
        raise ValueError(
            f"Eligible merge group {merge_group.merge_group_id!r} selected "
            f"representative candidate {representative_candidate_id!r}, which is "
            f"outside the group."
        )

    representative_candidate = sfi_candidates_by_id.get(representative_candidate_id)

    if representative_candidate is None:
        raise ValueError(
            f"Eligible merge group {merge_group.merge_group_id!r} selected unknown "
            f"representative candidate {representative_candidate_id!r}."
        )

    return representative_candidate


def _hash_identity_basis(value: str) -> str:
    """Hash one canonical identity basis with a full SHA-256 digest.

    Parameters
    ----------
    value
        Canonical serialized identity basis to hash exactly as supplied.

    Returns
    -------
    str
        Full hexadecimal SHA-256 digest.
    """

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _recover_merge_group_section_paths(
    *,
    merge_group: SFIMergeGroup,
    segments_by_id: dict[str, BlockSegment | TableSegment],
) -> list[list[str]]:
    """Recover unique source section paths for one merge group.

    Parameters
    ----------
    merge_group
        Merge group whose source segments should be inspected.
    segments_by_id
        DocumentIR segments keyed by segment ID.

    Returns
    -------
    list[list[str]]
        Unique non-empty section paths in source-reference order.

    Raises
    ------
    ValueError
        If a source segment is missing or contains an empty section-path label.
    """

    section_paths: list[list[str]] = []
    seen_paths: set[tuple[str, ...]] = set()

    for source_segment_id in merge_group.source_segment_ids:
        segment = segments_by_id.get(source_segment_id)

        if not isinstance(segment, (BlockSegment, TableSegment)):
            raise ValueError(
                f"Merge group {merge_group.merge_group_id!r} references source "
                f"segment {source_segment_id!r}, but that segment is missing or "
                f"unsupported for identity context recovery."
            )

        path: list[str] = []

        for section_ref in segment.section_path:
            label = section_ref.text.strip()

            if not label:
                raise ValueError(
                    f"Merge group {merge_group.merge_group_id!r} references source "
                    f"segment {source_segment_id!r} with an empty section-path label."
                )

            path.append(label)

        path_key = tuple(path)

        if path and path_key not in seen_paths:
            section_paths.append(path)
            seen_paths.add(path_key)

    return section_paths


def _recover_section_path_labels(
    *, record: SFIFinalRecord, segments_by_id: dict[str, BlockSegment | TableSegment]
) -> list[str]:
    """Recover section-path labels from DocumentIR source segments.

    Parameters
    ----------
    record
        Final SFI record whose source segment IDs should be inspected.
    segments_by_id
        DocumentIR block/table segments keyed by segment ID.

    Returns
    -------
    list[str]
        Non-empty section-path labels in source-segment order, preserving repeated
        labels so later recent-first selection can prefer the latest occurrence.

    Raises
    ------
    ValueError
        If a final SFI source segment is missing from the DocumentIR or is not a
        block/table segment with non-empty section-path labels.
    """

    section_ref_labels: list[str] = []

    for source_segment_id in record.source_segment_ids:
        segment = segments_by_id.get(source_segment_id)

        if not isinstance(segment, (BlockSegment, TableSegment)):
            raise ValueError(
                f"Final SFI {record.final_sfi_uuid} references source segment "
                f"{source_segment_id!r}, but that segment is missing or unsupported "
                f"for final-context recovery."
            )

        for section_ref in segment.section_path:
            section_ref_label = section_ref.text.strip()

            if not section_ref_label:
                raise ValueError(
                    f"Final SFI {record.final_sfi_uuid} references source segment "
                    f"{source_segment_id!r}, which contains an empty section-path "
                    f"label."
                )

            section_ref_labels.append(section_ref_label)

    return section_ref_labels


def _segment_page_indexes(segment: BlockSegment | TableSegment) -> list[int]:
    """Return sorted page indexes represented by one DocumentIR segment.

    Parameters
    ----------
    segment
        Block or table segment.

    Returns
    -------
    list[int]
        Sorted unique zero-based page indexes.
    """

    return sorted({provenance.page_index for provenance in segment.segment_provenance})


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


def _source_ref_index_values(*, key: str, source_ref: dict[str, Any]) -> list[int]:
    """Read sorted integer index values from one candidate source reference.

    Parameters
    ----------
    key
        Source-reference key containing row/header indexes.
    source_ref
        Candidate source-reference dictionary.

    Returns
    -------
    list[int]
        Sorted unique integer indexes.

    Raises
    ------
    ValueError
        If an index cannot be converted to a non-negative integer.
    """

    values: set[int] = set()

    for value in source_ref.get(key) or []:
        try:
            index = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Candidate source reference {key!r} contains invalid index "
                f"{value!r}."
            ) from exc

        if index < 0:
            raise ValueError(
                f"Candidate source reference {key!r} contains negative index "
                f"{index}."
            )

        values.add(index)

    return sorted(values)


def _source_ref_int_values(*, key: str, record: SFIFinalRecord) -> list[int]:
    """Collect integer table-reference values from final-record source refs.

    Parameters
    ----------
    key
        Candidate source-ref key to collect, such as `table_row_indexes` or
        `table_header_indexes`.
    record
        Final SFI record whose candidate source refs should be inspected.

    Returns
    -------
    list[int]
        Sorted unique integer values. Invalid or empty values are ignored.
    """

    values: set[int] = set()

    for source_ref in record.candidate_source_refs:
        if not isinstance(source_ref, dict):
            continue

        for value in source_ref.get(key) or []:
            try:
                values.add(int(value))
            except Exception:  # pylint: disable=W0718
                continue

    return sorted(values)


def _source_ref_text_values(*, key: str, record: SFIFinalRecord) -> list[str]:
    """Collect unique string values from final-record source refs.

    Parameters
    ----------
    key
        Candidate source-ref key to collect, such as `source_context_labels`.
    record
        Final SFI record whose candidate source refs should be inspected.

    Returns
    -------
    list[str]
        Unique non-empty string values in first-seen source-ref order. Invalid or empty
        values are ignored.
    """

    values: list[str] = []

    for source_ref in record.candidate_source_refs:
        if not isinstance(source_ref, dict):
            continue

        raw_values = source_ref.get(key) or []

        if isinstance(raw_values, (str, bytes)) or not isinstance(raw_values, Sequence):
            values_iterable: Any = [raw_values]
        else:
            values_iterable = raw_values

        for value in values_iterable:
            text = str(value).strip()

            if text and text not in values:
                values.append(text)

    return values


def _table_reference_page_indexes(
    *,
    header_indexes: Sequence[int],
    row_indexes: Sequence[int],
    table_segment: TableSegment,
) -> list[int]:
    """Recover pages for specifically cited rows in a stitched table segment.

    Parameters
    ----------
    header_indexes
        Cited raw table header indexes.
    row_indexes
        Cited raw table body-row indexes.
    table_segment
        Stitched table segment containing the cited rows.

    Returns
    -------
    list[int]
        Sorted unique row-level page indexes. Falls back to segment pages when optional
        row provenance is unavailable.

    Raises
    ------
    ValueError
        If a cited row index is outside the source table.
    """

    cited_indexes = sorted(set(header_indexes).union(row_indexes))

    for row_index in cited_indexes:
        if row_index >= len(table_segment.rows):
            raise ValueError(
                f"Table segment {table_segment.segment_id!r} has "
                f"{len(table_segment.rows)} "
                f"rows but a final SFI cites row index {row_index}."
            )

    if not cited_indexes or table_segment.row_provenance is None:
        return _segment_page_indexes(table_segment)

    return sorted(
        {
            table_segment.row_provenance[row_index].page_index
            for row_index in cited_indexes
        }
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
        collision_details = _build_final_sfi_collision_details(
            key_name="final_sfi_uuid", records=final_sfi_records, values=uuid_values
        )
        raise ValueError(
            f"Final SFI UUID collisions detected: {duplicate_uuids}. "
            f"Collision details: {collision_details}."
        )

    if duplicate_identity_keys:
        collision_details = _build_final_sfi_collision_details(
            key_name="identity_key", records=final_sfi_records, values=identity_keys
        )
        raise ValueError(
            f"Final SFI identity-key collisions detected: {duplicate_identity_keys}. "
            f"Collision details: {collision_details}."
        )


def _validate_merge_group_canonical_code_presence(merge_group: SFIMergeGroup) -> None:
    """Validate canonical-code presence and source-backing for one merge group.

    Checks that a merge group carrying source-visible codes has a resolved canonical
    code, that a canonical code is never asserted without preserved source evidence,
    and that any canonical code is drawn from that source evidence.

    Parameters
    ----------
    merge_group
        Merge group eligible for final SFI minting.

    Raises
    ------
    ValueError
        If a coded group lacks a canonical code, a canonical code is absent from source
        evidence, or a canonical code is not present in its source-code collection.
    """

    source_normalized_codes = unique_nonempty(merge_group.normalized_statement_codes)
    source_statement_codes = unique_nonempty(merge_group.statement_codes)
    canonical_code_type = merge_group.canonical_code_type
    canonical_normalized_code = merge_group.canonical_normalized_statement_code
    canonical_statement_code = merge_group.canonical_statement_code

    if source_normalized_codes and canonical_normalized_code is None:
        raise ValueError(
            f"Eligible merge group {merge_group.merge_group_id!r} contains "
            f"source-visible normalized codes but has no resolved canonical "
            f"normalized statement code."
        )

    if source_normalized_codes and canonical_code_type is None:
        raise ValueError(
            f"Eligible merge group {merge_group.merge_group_id!r} contains "
            f"source-visible normalized codes but has no canonical code type."
        )

    if not source_normalized_codes and canonical_normalized_code is not None:
        raise ValueError(
            f"Eligible merge group {merge_group.merge_group_id!r} defines a "
            f"canonical normalized statement code without preserved normalized "
            f"source-code evidence."
        )

    if not source_normalized_codes and canonical_code_type is not None:
        raise ValueError(
            f"Eligible merge group {merge_group.merge_group_id!r} defines "
            f"canonical_code_type without preserved source-code evidence."
        )

    if source_statement_codes and canonical_statement_code is None:
        raise ValueError(
            f"Eligible merge group {merge_group.merge_group_id!r} contains "
            f"source-visible statement codes but has no resolved canonical "
            f"statement code."
        )

    if not source_statement_codes and canonical_statement_code is not None:
        raise ValueError(
            f"Eligible merge group {merge_group.merge_group_id!r} defines a "
            f"canonical statement code without preserved source-code evidence."
        )

    if (
        canonical_normalized_code is not None
        and canonical_normalized_code not in source_normalized_codes
    ):
        raise ValueError(
            f"Eligible merge group {merge_group.merge_group_id!r} has canonical "
            f"normalized code {canonical_normalized_code!r}, which is not present "
            f"in normalized_statement_codes."
        )

    if (
        canonical_statement_code is not None
        and canonical_statement_code not in source_statement_codes
    ):
        raise ValueError(
            f"Eligible merge group {merge_group.merge_group_id!r} has canonical "
            f"statement code {canonical_statement_code!r}, which is not present "
            f"in statement_codes."
        )


def _validate_merge_group_canonical_type_pair(
    *,
    canonical_pair: tuple[str | None, str | None],
    merge_group: SFIMergeGroup,
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
) -> set[tuple[str | None, str | None]]:
    """Validate the canonical statement-type pair against source candidates.

    Verifies that the canonical type pair is fully populated, that every registry
    candidate referenced by the group is known, and that the canonical pair is
    preserved by at least one source candidate.

    Parameters
    ----------
    canonical_pair
        The group's `(statement_type, normalized_statement_type)` canonical pair.
    merge_group
        Merge group eligible for final SFI minting.
    sfi_candidates_by_id
        Registry candidates keyed by candidate ID.

    Returns
    -------
    set[tuple[str | None, str | None]]
        The distinct `(statement_type, normalized_statement_type)` pairs observed
        across the group's source candidates.

    Raises
    ------
    ValueError
        If the canonical type pair is incomplete, a referenced registry candidate is
        unknown, or the canonical pair is not preserved by any source candidate.
    """

    if not all(canonical_pair):
        raise ValueError(
            f"Eligible merge group {merge_group.merge_group_id!r} lacks a "
            f"canonical statement-type pair."
        )

    group_candidates = [
        sfi_candidates_by_id[candidate_id]
        for candidate_id in merge_group.registry_candidate_ids
        if candidate_id in sfi_candidates_by_id
    ]

    if len(group_candidates) != len(merge_group.registry_candidate_ids):
        raise ValueError(
            f"Eligible merge group {merge_group.merge_group_id!r} references an "
            f"unknown registry candidate during type validation."
        )

    observed_pairs = {
        (candidate.statement_type, candidate.normalized_statement_type)
        for candidate in group_candidates
    }

    if canonical_pair not in observed_pairs:
        raise ValueError(
            f"Eligible merge group {merge_group.merge_group_id!r} has a "
            f"canonical type pair not preserved by any source candidate."
        )

    return observed_pairs


def _validate_merge_group_code_resolutions(
    *,
    eligible_merge_groups: Sequence[SFIMergeGroup],
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
) -> None:
    """Validate canonical-code resolution for finalizable merge groups.

    This is a defensive finalization check. Deduplication owns the semantic choice of
    the canonical source candidate for a mixed-code merge; finalization verifies that
    the persisted choice is complete, source-backed, and internally consistent.

    Parameters
    ----------
    eligible_merge_groups
        Merge groups eligible for final SFI minting.
    sfi_candidates_by_id
        Registry candidates keyed by registry candidate ID.

    Raises
    ------
    ValueError
        If a coded group lacks a canonical code, a canonical code is absent from source
        evidence, or a review-selected source candidate does not support the persisted
        canonical code.
    """

    for merge_group in eligible_merge_groups:
        _validate_merge_group_canonical_code_presence(merge_group)
        _validate_merge_group_selected_source_candidate(
            merge_group=merge_group, sfi_candidates_by_id=sfi_candidates_by_id
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


def _validate_merge_group_selected_source_candidate(
    *, merge_group: SFIMergeGroup, sfi_candidates_by_id: dict[str, SFIRegistryCandidate]
) -> None:
    """Validate the review-selected source candidate for one merge group.

    For groups resolved via `review_selected_source_code`, verifies that a selected
    source candidate exists, belongs to the group, is a known registry candidate, and
    supports the persisted canonical code. For all other resolution methods, verifies
    that no source-candidate id is asserted.

    Parameters
    ----------
    merge_group
        Merge group eligible for final SFI minting.
    sfi_candidates_by_id
        Registry candidates keyed by registry candidate ID.

    Raises
    ------
    ValueError
        If a review-selected source candidate is missing, out of group, unknown, or
        does not support the persisted canonical code, or if a non-review method
        asserts a source-candidate id.
    """

    selected_candidate_id = merge_group.canonical_code_source_candidate_id

    if merge_group.code_resolution_method != "review_selected_source_code":
        if selected_candidate_id is not None:
            raise ValueError(
                f"Eligible merge group {merge_group.merge_group_id!r} defines "
                f"canonical_code_source_candidate_id without a review-selected "
                f"code-resolution method."
            )

        return

    if selected_candidate_id is None:
        raise ValueError(
            f"Eligible merge group {merge_group.merge_group_id!r} uses "
            f"review_selected_source_code without a selected source candidate."
        )

    if selected_candidate_id not in merge_group.registry_candidate_ids:
        raise ValueError(
            f"Eligible merge group {merge_group.merge_group_id!r} selected "
            f"candidate {selected_candidate_id!r}, which is outside the group."
        )

    selected_candidate = sfi_candidates_by_id.get(selected_candidate_id)

    if selected_candidate is None:
        raise ValueError(
            f"Eligible merge group {merge_group.merge_group_id!r} selected "
            f"unknown registry candidate {selected_candidate_id!r}."
        )

    if (
        selected_candidate.normalized_statement_code
        != merge_group.canonical_normalized_statement_code
        or selected_candidate.resolved_code_type != merge_group.canonical_code_type
        or selected_candidate.statement_code != merge_group.canonical_statement_code
    ):
        raise ValueError(
            f"Eligible merge group {merge_group.merge_group_id!r} canonical code "
            f"does not match selected source candidate "
            f"{selected_candidate_id!r}."
        )


def _validate_merge_group_type_resolutions(
    *,
    eligible_merge_groups: Sequence[SFIMergeGroup],
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
) -> None:
    """Validate canonical statement-type resolution for finalizable groups.

    Parameters
    ----------
    eligible_merge_groups
        Merge groups eligible for final SFI minting.
    sfi_candidates_by_id
        Registry candidates keyed by candidate ID.

    Raises
    ------
    ValueError
        If canonical type fields are absent, not source-backed, or inconsistent with
        mixed-type selection metadata.
    """

    for merge_group in eligible_merge_groups:
        canonical_pair = (
            merge_group.canonical_statement_type,
            merge_group.canonical_normalized_statement_type,
        )
        observed_pairs = _validate_merge_group_canonical_type_pair(
            canonical_pair=canonical_pair,
            merge_group=merge_group,
            sfi_candidates_by_id=sfi_candidates_by_id,
        )
        _validate_merge_group_type_selection(
            canonical_pair=canonical_pair,
            merge_group=merge_group,
            observed_pairs=observed_pairs,
            sfi_candidates_by_id=sfi_candidates_by_id,
        )


def _validate_merge_group_type_selection(
    *,
    canonical_pair: tuple[str | None, str | None],
    merge_group: SFIMergeGroup,
    observed_pairs: set[tuple[str | None, str | None]],
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
) -> None:
    """Validate mixed-type selection metadata for one merge group.

    For single-type groups (one observed pair), verifies that no mixed-type selection
    metadata is asserted. For mixed-type groups, verifies that a selection source
    candidate and reason are present, that the selected candidate is known and in
    group, and that it supports the canonical type pair.

    Parameters
    ----------
    canonical_pair
        The group's `(statement_type, normalized_statement_type)` canonical pair.
    merge_group
        Merge group eligible for final SFI minting.
    observed_pairs
        The distinct `(statement_type, normalized_statement_type)` pairs observed
        across the group's source candidates.
    sfi_candidates_by_id
        Registry candidates keyed by candidate ID.

    Raises
    ------
    ValueError
        If a single-type group defines mixed-type selection metadata, or a mixed-type
        group has a missing reason, or a missing, unknown, out-of-group, or
        non-matching canonical type source candidate.
    """

    selected_candidate_id = merge_group.canonical_type_source_candidate_id

    if len(observed_pairs) == 1:
        if (
            selected_candidate_id is not None
            or merge_group.canonical_type_selection_reason is not None
        ):
            raise ValueError(
                f"Eligible merge group {merge_group.merge_group_id!r} has one "
                f"observed type pair but defines mixed-type selection metadata."
            )

        return

    if selected_candidate_id is None:
        raise ValueError(
            f"Eligible mixed-type merge group {merge_group.merge_group_id!r} "
            f"has no canonical type source candidate."
        )

    if not merge_group.canonical_type_selection_reason:
        raise ValueError(
            f"Eligible mixed-type merge group {merge_group.merge_group_id!r} "
            f"has no canonical type selection reason."
        )

    selected_candidate = sfi_candidates_by_id.get(selected_candidate_id)

    if (
        selected_candidate is None
        or selected_candidate_id not in merge_group.registry_candidate_ids
    ):
        raise ValueError(
            f"Eligible mixed-type merge group {merge_group.merge_group_id!r} "
            f"selected an unknown or out-of-group canonical type source."
        )

    selected_pair = (
        selected_candidate.statement_type,
        selected_candidate.normalized_statement_type,
    )

    if canonical_pair != selected_pair:
        raise ValueError(
            f"Eligible mixed-type merge group {merge_group.merge_group_id!r} "
            f"canonical type does not match the selected source candidate."
        )


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
    sfi_candidates_by_id = {
        candidate.registry_candidate_id: candidate
        for candidate in sfi_candidate_registry.candidates
    }
    _validate_merge_group_code_resolutions(
        eligible_merge_groups=eligible_merge_groups,
        sfi_candidates_by_id=sfi_candidates_by_id,
    )
    _validate_merge_group_type_resolutions(
        eligible_merge_groups=eligible_merge_groups,
        sfi_candidates_by_id=sfi_candidates_by_id,
    )
    coded_identity_disambiguations = _build_coded_identity_disambiguations(
        eligible_merge_groups=eligible_merge_groups,
        sfi_candidates_by_id=sfi_candidates_by_id,
    )
    segments_by_id = {segment.segment_id: segment for segment in document_ir.segments}

    sfi_final_records = [
        _build_sfi_final_record(
            coded_identity_disambiguation=coded_identity_disambiguations.get(
                merge_group.merge_group_id
            ),
            document_ir=document_ir,
            kg_config=kg_config,
            merge_group=merge_group,
            segments_by_id=segments_by_id,
            sfi_candidates_by_id=sfi_candidates_by_id,
        )
        for merge_group in eligible_merge_groups
    ]

    if not sfi_final_records:
        raise ValueError("Produced zero final SFI records.")

    _validate_final_sfi_records(sfi_final_records)

    sfi_final_contexts = _build_sfi_final_contexts(
        document_ir=document_ir,
        kg_config=kg_config,
        sfi_candidates_by_id=sfi_candidates_by_id,
        sfi_final_records=sfi_final_records,
    )
    sfi_final_summary = _build_sfi_final_summary(
        eligible_merge_group_count=len(eligible_merge_groups),
        excluded_conflict_group_count=len(sfi_merge_report.conflict_groups),
        excluded_needs_review_group_count=len(sfi_merge_report.needs_review_groups),
        sfi_final_records=sfi_final_records,
    )

    make_dir(kg_dirs.root)
    write_to_json(
        fp=kg_dirs.root / "sfi_final_contexts.json",
        json_info=[context.model_dump(mode="json") for context in sfi_final_contexts],
    )
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
