"""Select LP participants and report coordinate and unresolved-context exclusions.

Selection grants relation-specific SFI participation only. The configured type-pair
matrices and coordinate directions still constrain individual pairs. No hierarchy, LC
support, warning, or participation decision nominates or publishes an edge.
"""

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import json
import re

from collections import Counter
from copy import deepcopy
from operator import itemgetter
from typing import Any, Literal
from uuid import UUID

# Package Library
from kgfeg.kgs.lp_coordinates import (
    LPDevelopmentalCoordinate,
    build_lp_coordinate_index,
)
from kgfeg.kgs.lp_index import LPGraphIndex, build_lp_graph_index
from kgfeg.kgs.schemas import (
    AcademicStandardsLCKGBundle,
    LPRelationshipType,
    StandardsFrameworkItem,
)
from kgfeg.kgs.utils import KGDirs
from kgfeg.schemas import BaseSchema, CreateKGConfig
from kgfeg.utils.general import make_dir, write_to_json

_EligibilityReason = Literal[
    "configured_statement_type_with_resolved_coordinate",
    "configured_statement_type_coordinate_optional",
]
_ExclusionReason = Literal[
    "missing_coordinate", "statement_type_not_configured", "unresolved_context_excluded"
]
_RELATIONSHIP_TYPES: tuple[LPRelationshipType, ...] = ("buildsTowards", "relatesTo")
_UnresolvedParticipation = Literal[
    "exclude_unresolved", "include_unresolved_with_warnings"
]


class LPSFIEligibility(BaseSchema):
    """Participation decisions and authoritative context for one final SFI.

    Attributes
    ----------
    coordinate
        Resolved or explicitly missing coordinate from the coordinate-only resolver.
    eligibility_reasons
        Permitted relationships and their policy reasons. These are endpoint
        permissions, not permissions for arbitrary pairs of participating types.
    exclusion_reasons
        Every nonpermitted relationship and its controlling exclusion reason.
    parent_sfi_uuids
        All direct positive-hierarchy parents; framework-root fallbacks are absent.
    root_fallback_relationship_uuids
        Direct unresolved fallback relationship identifiers, retained only for audit.
    sfi
        Unmodified final upstream SFI, including its source and audit metadata.
    source_provenance
        Complete upstream item provenance, retained without interpreting audit flags as
        positive evidence or independent eligibility rules.
    statement_type
        Canonical AS statement type used to consult the configured LP matrices.
    unresolved_ancestry
        Whether the SFI itself or any ancestor has an unresolved root fallback.
    warnings
        Explicit unresolved-context and missing-coordinate warnings for downstream use.
    """

    coordinate: LPDevelopmentalCoordinate
    eligibility_reasons: dict[LPRelationshipType, _EligibilityReason]
    exclusion_reasons: dict[LPRelationshipType, _ExclusionReason]
    parent_sfi_uuids: tuple[UUID, ...]
    root_fallback_relationship_uuids: tuple[UUID, ...]
    sfi: StandardsFrameworkItem
    source_provenance: dict[str, Any]
    statement_type: str
    unresolved_ancestry: bool
    warnings: tuple[str, ...]


class LPSelectionReport(BaseSchema):
    """Complete deterministic eligibility audit for one AS+LC bundle and config.

    Attributes
    ----------
    config_content_hash
        SHA-256 of the complete effective KG configuration, including unresolved policy.
    eligible_sfis_content_hash
        SHA-256 of the exact eligible-record population serialized as canonical JSON.
    eligible_sfis_per_relationship
        Endpoint participation counts for each relationship, not candidate counts.
    exclusion_reason_counts_per_relationship
        Counts of controlling exclusion reasons for each relationship separately.
    framework_uuid
        CASE UUID of the authoritative upstream framework.
    sfis
        Exactly one decision record per upstream SFI, ordered by CASE UUID.
    total_sfis_considered
        Total number of upstream SFIs assessed.
    total_sfis_eligible
        SFIs permitted to participate in at least one relationship.
    total_sfis_excluded
        SFIs excluded from both relationships. Empty selections are valid outcomes.
    unresolved_participation
        Explicit profile-wide policy applied to unresolved self or ancestry.
    unresolved_sfis_considered
        All SFIs with unresolved self or ancestry, including omitted statement types.
    unresolved_sfis_eligible
        Unresolved SFIs permitted in at least one relationship, with explicit warnings.
    unresolved_sfis_excluded
        Unresolved SFIs excluded from both relationships for any policy reason.
    unresolved_sfis_policy_excluded
        Unresolved SFIs excluded specifically by the profile's exclusion state.
    upstream_content_hash
        SHA-256 of the complete upstream bundle with graph populations in UUID order.
    """

    config_content_hash: str
    eligible_sfis_content_hash: str
    eligible_sfis_per_relationship: dict[LPRelationshipType, int]
    exclusion_reason_counts_per_relationship: dict[LPRelationshipType, dict[str, int]]
    framework_uuid: UUID
    sfis: list[LPSFIEligibility]
    total_sfis_considered: int
    total_sfis_eligible: int
    total_sfis_excluded: int
    unresolved_participation: _UnresolvedParticipation
    unresolved_sfis_considered: int
    unresolved_sfis_eligible: int
    unresolved_sfis_excluded: int
    unresolved_sfis_policy_excluded: int
    upstream_content_hash: str

    @property
    def eligible_sfis(self) -> list[LPSFIEligibility]:
        """Return the UUID-ordered SFIs permitted in at least one relationship.

        Returns
        -------
        list[LPSFIEligibility]
            The exact population written to the eligible-SFI artifact.
        """

        return [record for record in self.sfis if record.eligibility_reasons]


def _canonical_json(value: Any) -> str:
    """Serialize JSON material with stable keys and no non-finite numbers.

    Parameters
    ----------
    value
        JSON-compatible material to serialize.

    Returns
    -------
    str
        Canonical JSON used for content hashes and deterministic artifact payloads.
    """

    return json.dumps(
        allow_nan=False,
        ensure_ascii=False,
        obj=value,
        separators=(",", ":"),
        sort_keys=True,
    )


def _canonical_statement_types(kg_config: CreateKGConfig) -> dict[str, str]:
    """Map AS labels and aliases to the canonical types used by LP policy.

    Parameters
    ----------
    kg_config
        Validated configuration whose aliases were checked by coordinate resolution.

    Returns
    -------
    dict[str, str]
        Punctuation-insensitive AS label keys mapped to canonical statement types.
    """

    return {
        _statement_type_key(label): policy.statement_type
        for policy in kg_config.academic_standards.statement_type_policy
        for label in [policy.statement_type, *policy.aliases]
    }


def _classify_sfi(
    *,
    coordinate: LPDevelopmentalCoordinate,
    graph_index: LPGraphIndex,
    participating_types: dict[LPRelationshipType, set[str]],
    statement_type: str,
    unresolved_participation: _UnresolvedParticipation,
) -> LPSFIEligibility:
    """Apply profile participation and coordinate rules to one indexed SFI.

    Parameters
    ----------
    coordinate
        Validated coordinate for this SFI; invalid coordinates never reach selection.
    graph_index
        Validated upstream graph with all unresolved DAG descendants marked.
    participating_types
        Relation-specific endpoint type sets derived from the configured pair matrices.
    statement_type
        Canonical local AS type for this SFI.
    unresolved_participation
        Required profile-wide inclusion or exclusion state.

    Returns
    -------
    LPSFIEligibility
        Complete decisions, warnings, and unmodified upstream context.
    """

    sfi_uuid = coordinate.sfi_uuid
    unresolved = sfi_uuid in graph_index.unresolved_ancestry_sfi_uuids
    eligibility_reasons: dict[LPRelationshipType, _EligibilityReason] = {}
    exclusion_reasons: dict[LPRelationshipType, _ExclusionReason] = {}
    warnings: list[str] = []

    if unresolved:
        warnings.append(
            f"SFI {sfi_uuid} has unresolved self or ancestry. Framework-root fallback "
            f"must not be used as positive hierarchy, topology, domain, or placement "
            f"evidence."
        )

    if coordinate.status == "missing":
        warnings.append(
            f"SFI {sfi_uuid} has no developmental coordinate. It cannot participate "
            f"in buildsTowards; relatesTo still requires statement-type and "
            f"unresolved-context permission. Missing rank is not positive evidence."
        )

    for relationship_type in _RELATIONSHIP_TYPES:
        if unresolved and unresolved_participation == "exclude_unresolved":
            exclusion_reasons[relationship_type] = "unresolved_context_excluded"
        elif statement_type not in participating_types[relationship_type]:
            exclusion_reasons[relationship_type] = "statement_type_not_configured"
        elif relationship_type == "buildsTowards":
            if coordinate.status == "missing":
                exclusion_reasons[relationship_type] = "missing_coordinate"
            else:
                eligibility_reasons[relationship_type] = (
                    "configured_statement_type_with_resolved_coordinate"
                )
        else:
            eligibility_reasons[relationship_type] = (
                "configured_statement_type_coordinate_optional"
            )

    return LPSFIEligibility(
        coordinate=coordinate,
        eligibility_reasons=eligibility_reasons,
        exclusion_reasons=exclusion_reasons,
        parent_sfi_uuids=graph_index.parent_sfi_uuids_by_sfi_uuid[sfi_uuid],
        root_fallback_relationship_uuids=tuple(
            edge.identifier
            for edge in graph_index.root_fallback_relationships_by_sfi_uuid[sfi_uuid]
        ),
        sfi=graph_index.sfi_by_uuid[sfi_uuid].model_copy(deep=True),
        source_provenance=deepcopy(dict(graph_index.sfi_provenance_by_uuid[sfi_uuid])),
        statement_type=statement_type,
        unresolved_ancestry=unresolved,
        warnings=tuple(warnings),
    )


def _content_hash(value: Any) -> str:
    """Hash actual JSON material with stable keys and no non-finite numbers.

    Parameters
    ----------
    value
        JSON-compatible material to identify.

    Returns
    -------
    str
        SHA-256 hex digest of canonical UTF-8 JSON.
    """

    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _statement_type_key(value: str | None) -> str:
    """Normalize an AS statement-type label using the existing AS label convention.

    Parameters
    ----------
    value
        Canonical label or configured alias.

    Returns
    -------
    str
        Casefolded label with punctuation runs collapsed to spaces.

    Raises
    ------
    ValueError
        If the final SFI has no statement-type label.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError("LP participation requires a configured statement type.")

    return re.sub(pattern=r"[^0-9a-z]+", repl=" ", string=value.casefold()).strip()


def _upstream_content_hash(as_lc_bundle: AcademicStandardsLCKGBundle) -> str:
    """Hash the full upstream bundle independently of graph encounter order.

    Parameters
    ----------
    as_lc_bundle
        Validated authoritative bundle; its original populations are not modified.

    Returns
    -------
    str
        Canonical bundle content digest retaining provenance, summaries, and audit data.
    """

    material = as_lc_bundle.model_dump(mode="json")

    for field, identifier in (
        ("items", "case_identifier_uuid"),
        ("learning_components", "identifier"),
        ("relationships_has_child", "identifier"),
        ("relationships_supports", "identifier"),
    ):
        material[field] = sorted(material[field], key=itemgetter(identifier))

    return _content_hash(material)


def build_lp_selection(
    *, as_lc_bundle: AcademicStandardsLCKGBundle, kg_config: CreateKGConfig
) -> LPSelectionReport:
    """Assess every SFI and return a complete eligibility report without file I/O.

    Graph and coordinate validation finish before any classification is returned.
    Unknown, ambiguous, or conflicting coordinates remain hard errors even for an
    omitted type or an unresolved SFI excluded by policy. Missing coordinates only
    remove buildsTowards permission. Normalized type, leafness, LC eligibility, and LC
    reuse are never participation gates.

    Parameters
    ----------
    as_lc_bundle
        Final passed, error-free AS+LC bundle for the current run.
    kg_config
        The same run's validated AS and LP configuration.

    Returns
    -------
    LPSelectionReport
        UUID-ordered decisions, exact population counts, and material input hashes.

    Raises
    ------
    ValueError
        If upstream graph or coordinate validation fails.
    """

    graph_index = build_lp_graph_index(as_lc_bundle)
    coordinate_index = build_lp_coordinate_index(
        graph_index=graph_index, kg_config=kg_config
    )
    lp_config = kg_config.learning_progressions
    statement_types = _canonical_statement_types(kg_config)
    participating_types: dict[LPRelationshipType, set[str]] = {
        "buildsTowards": {
            statement_type
            for pair in lp_config.builds_towards.allowed_statement_type_pairs
            for statement_type in (
                pair.source_statement_type,
                pair.target_statement_type,
            )
        },
        "relatesTo": {
            statement_type
            for pair in lp_config.relates_to.allowed_statement_type_pairs
            for statement_type in (
                pair.first_statement_type,
                pair.second_statement_type,
            )
        },
    }
    records = [
        _classify_sfi(
            coordinate=coordinate_index.coordinate_by_sfi_uuid[sfi_uuid],
            graph_index=graph_index,
            participating_types=participating_types,
            statement_type=statement_types[_statement_type_key(sfi.statement_type)],
            unresolved_participation=lp_config.unresolved_participation,
        )
        for sfi_uuid, sfi in graph_index.sfi_by_uuid.items()
    ]
    eligible = [record for record in records if record.eligibility_reasons]
    unresolved = [record for record in records if record.unresolved_ancestry]
    unresolved_eligible_count = sum(
        bool(record.eligibility_reasons) for record in unresolved
    )
    return LPSelectionReport(
        config_content_hash=_content_hash(kg_config.model_dump(mode="json")),
        eligible_sfis_content_hash=_content_hash(
            [record.model_dump(mode="json") for record in eligible]
        ),
        eligible_sfis_per_relationship={
            relationship_type: sum(
                relationship_type in record.eligibility_reasons for record in records
            )
            for relationship_type in _RELATIONSHIP_TYPES
        },
        exclusion_reason_counts_per_relationship={
            relationship_type: dict(
                sorted(
                    Counter(
                        record.exclusion_reasons[relationship_type]
                        for record in records
                        if relationship_type in record.exclusion_reasons
                    ).items()
                )
            )
            for relationship_type in _RELATIONSHIP_TYPES
        },
        framework_uuid=as_lc_bundle.framework.case_identifier_uuid,
        sfis=records,
        total_sfis_considered=len(records),
        total_sfis_eligible=len(eligible),
        total_sfis_excluded=len(records) - len(eligible),
        unresolved_participation=lp_config.unresolved_participation,
        unresolved_sfis_considered=len(unresolved),
        unresolved_sfis_eligible=unresolved_eligible_count,
        unresolved_sfis_excluded=len(unresolved) - unresolved_eligible_count,
        unresolved_sfis_policy_excluded=sum(
            "unresolved_context_excluded" in record.exclusion_reasons.values()
            for record in records
        ),
        upstream_content_hash=_upstream_content_hash(as_lc_bundle),
    )


def select_lp_sfis(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
) -> tuple[list[LPSFIEligibility], LPSelectionReport]:
    """Build and write the complete eligible population and eligibility report.

    Both payloads are validated and serialized from current inputs before any output is
    written. This operation recomputes selection; it does not reuse stale artifacts.
    Policy exclusions remain in the eligibility report, separate from adjudication
    ambiguity or processing failures.

    Parameters
    ----------
    as_lc_bundle
        Final validated AS+LC bundle.
    kg_config
        Validated KG runtime configuration for that bundle.
    kg_dirs
        Directory receiving the two LP eligibility JSON artifacts.

    Returns
    -------
    tuple[list[LPSFIEligibility], LPSelectionReport]
        Eligible SFI records and the complete report used to write both artifacts.

    Raises
    ------
    ValueError
        If graph, coordinate, or serialization validation fails before writing.
    OSError
        If the artifact directory or files cannot be written.
    """

    report = build_lp_selection(as_lc_bundle=as_lc_bundle, kg_config=kg_config)
    eligible = report.eligible_sfis
    eligible_payload = json.loads(
        _canonical_json([record.model_dump(mode="json") for record in eligible])
    )
    report_payload = json.loads(_canonical_json(report.model_dump(mode="json")))
    make_dir(kg_dirs.root)
    write_to_json(fp=kg_dirs.root / "lp_eligible_sfis.json", json_info=eligible_payload)
    write_to_json(
        fp=kg_dirs.root / "lp_eligibility_report.json", json_info=report_payload
    )
    return eligible, report
