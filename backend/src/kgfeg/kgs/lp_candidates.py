"""Hard admissibility and canonical identities for Learning Progressions pairs.

The filter assesses only explicitly supplied endpoints. Admissibility is permission for
candidate consideration, not nomination evidence or an accepted relationship. It
neither enumerates the graph's pair population nor writes candidate artifacts.
"""

# Future Library
from __future__ import annotations

# Standard Library
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID, uuid5

# Package Library
from kgfeg.config import Settings
from kgfeg.kgs.lp_coordinates import LPDevelopmentalCoordinateIndex
from kgfeg.kgs.lp_selection import LPSFIEligibility, build_lp_selection
from kgfeg.kgs.schemas import (
    AcademicStandardsLCKGBundle,
    LPAdmissibleDecision,
    LPDirection,
)
from kgfeg.schemas import CreateKGConfig


@dataclass(frozen=True, slots=True)
class LPCandidateFilter:
    """Run-scoped pair filter built from validated participation and coordinates.

    Construct this snapshot with ``build_lp_pair_filter``. It keeps private endpoint
    records and immutable policy lookups so later changes to the input bundle or
    configuration do not change its decisions. Each result carries independent copies
    of the endpoint records, including all preserved upstream context.
    """

    _builds_towards_type_pairs: frozenset[tuple[str, str]]
    _coordinate_index: LPDevelopmentalCoordinateIndex
    _doc_key: str
    _relates_to_type_pairs: frozenset[frozenset[str]]
    _sfi_by_uuid: Mapping[UUID, LPSFIEligibility]

    def _admissible_decisions(
        self, *, first: LPSFIEligibility, second: LPSFIEligibility
    ) -> tuple[LPAdmissibleDecision, ...]:
        """Intersect endpoint, type-pair, and coordinate permissions.

        Parameters
        ----------
        first
            Participation record for the lower canonical endpoint UUID.
        second
            Participation record for the higher canonical endpoint UUID.

        Returns
        -------
        tuple[LPAdmissibleDecision, ...]
            Permitted relationship options followed by both nonpublishing outcomes, or
            an empty tuple when no relationship is admissible.
        """

        decisions: list[LPAdmissibleDecision] = []
        directions: tuple[
            tuple[LPSFIEligibility, LPSFIEligibility, LPDirection], ...
        ] = ((first, second, "first_to_second"), (second, first, "second_to_first"))

        for source, target, direction in directions:
            if (
                "buildsTowards" in source.eligibility_reasons
                and "buildsTowards" in target.eligibility_reasons
                and (source.statement_type, target.statement_type)
                in self._builds_towards_type_pairs
                and self._coordinate_index.allows_builds_towards(
                    source_sfi_uuid=source.sfi.case_identifier_uuid,
                    target_sfi_uuid=target.sfi.case_identifier_uuid,
                )
            ):
                decisions.append(
                    LPAdmissibleDecision(decision="buildsTowards", direction=direction)
                )

        if (
            "relatesTo" in first.eligibility_reasons
            and "relatesTo" in second.eligibility_reasons
            and frozenset((first.statement_type, second.statement_type))
            in self._relates_to_type_pairs
            and self._coordinate_index.allows_relates_to(
                first_sfi_uuid=first.sfi.case_identifier_uuid,
                second_sfi_uuid=second.sfi.case_identifier_uuid,
            )
        ):
            decisions.append(LPAdmissibleDecision(decision="relatesTo"))

        if decisions:
            # This is to include the possibility of a later negative or ambiguous
            # judgment.
            decisions.extend(
                (
                    LPAdmissibleDecision(decision="no_relation"),
                    LPAdmissibleDecision(decision="needs_review"),
                )
            )

        return tuple(decisions)

    def filter_pair(
        self, *, first_sfi_uuid: UUID | str, second_sfi_uuid: UUID | str
    ) -> LPPairAdmissibility | None:
        """Assess one unordered logical pair without nominating it.

        Endpoint encounter order never asserts developmental direction. A self-pair or
        a pair with no permitted relationship returns ``None``. Unknown endpoints raise
        rather than being mistaken for normal policy exclusions. Repeated or reversed
        calls return the same pair identity without accumulating candidates.

        Parameters
        ----------
        first_sfi_uuid
            One final SFI CASE UUID from this run, in any valid UUID spelling.
        second_sfi_uuid
            The other final SFI CASE UUID, in any valid UUID spelling.

        Returns
        -------
        LPPairAdmissibility | None
            Canonical identity, complete allowed outcomes, and preserved endpoint
            context; ``None`` for a self-pair or a policy-disallowed pair.

        Raises
        ------
        ValueError
            If either UUID is malformed or absent from the validated run's SFIs.
        """

        first_uuid, second_uuid = sorted(
            (UUID(str(first_sfi_uuid)), UUID(str(second_sfi_uuid))), key=str
        )

        for sfi_uuid in (first_uuid, second_uuid):
            if sfi_uuid not in self._sfi_by_uuid:
                raise ValueError(f"Unknown SFI CASE UUID: {sfi_uuid}.")

        if first_uuid == second_uuid:
            return None

        first = self._sfi_by_uuid[first_uuid]
        second = self._sfi_by_uuid[second_uuid]
        decisions = self._admissible_decisions(first=first, second=second)

        if not decisions:
            return None

        return LPPairAdmissibility(
            admissible_decisions=decisions,
            first_sfi=first.model_copy(deep=True),
            pair_id=build_lp_pair_id(
                doc_key=self._doc_key,
                first_sfi_uuid=first_uuid,
                second_sfi_uuid=second_uuid,
            ),
            second_sfi=second.model_copy(deep=True),
            warnings=tuple(sorted({*first.warnings, *second.warnings})),
        )


@dataclass(frozen=True, slots=True)
class LPPairAdmissibility:
    """Canonical pair permission without nomination evidence.

    Attributes
    ----------
    admissible_decisions
        Permitted directions and relationships plus ``no_relation`` and
        ``needs_review``. At least one relationship is permitted by the filter.
    first_sfi
        Lower-UUID endpoint's complete participation record, retaining unresolved
        status, warnings, positive parents, fallback audit IDs, and source provenance.
    pair_id
        Document-scoped deterministic UUIDv5 string for the unordered logical pair.
    second_sfi
        Higher-UUID endpoint's complete participation record.
    warnings
        Sorted unique union of both endpoints' warnings. These are audit context, never
        positive hierarchy or nomination evidence.
    """

    admissible_decisions: tuple[LPAdmissibleDecision, ...]
    first_sfi: LPSFIEligibility
    pair_id: str
    second_sfi: LPSFIEligibility
    warnings: tuple[str, ...]

    @property
    def first_sfi_uuid(self) -> UUID:
        """Return the lower canonical endpoint UUID.

        Returns
        -------
        UUID
            First endpoint key used by the intrinsic candidate schema.
        """

        return self.first_sfi.sfi.case_identifier_uuid

    @property
    def second_sfi_uuid(self) -> UUID:
        """Return the higher canonical endpoint UUID.

        Returns
        -------
        UUID
            Second endpoint key used by the intrinsic candidate schema.
        """

        return self.second_sfi.sfi.case_identifier_uuid


def _clean_doc_key(doc_key: str) -> str:
    """Require a nonblank document identity using the existing KG convention.

    Parameters
    ----------
    doc_key
        Source document key whose surrounding whitespace is not identity material.

    Returns
    -------
    str
        Nonblank stripped document key.

    Raises
    ------
    ValueError
        If the key is not a nonblank string.
    """

    if not isinstance(doc_key, str) or not doc_key.strip():
        raise ValueError("LP pair identity requires a nonblank doc_key.")

    return doc_key.strip()


def build_lp_pair_filter(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
) -> LPCandidateFilter:
    """Build a pair filter from the current validated bundle and configuration.

    Recompute endpoint participation from current material inputs instead of trusting a
    caller-supplied eligibility artifact. Coordinate validation completes for all SFIs
    before any filter is returned, including policy-excluded SFIs. The resulting
    coordinate snapshot supplies coordinate-only compatibility; the filter separately
    enforces relation-specific endpoint participation and exact type-pair matrices.

    Parameters
    ----------
    as_lc_bundle
        Final passed, error-free AS+LC bundle for the current run.
    doc_key
        Source document key, which must match the authoritative framework metadata and
        framework provenance when that provenance includes a document key.
    kg_config
        Validated AS and LP configuration for this curriculum.

    Returns
    -------
    LPCandidateFilter
        Run-scoped on-demand filter with no pair enumeration or file I/O.

    Raises
    ------
    ValueError
        If document identity, upstream validation, or coordinate resolution fails.
    """

    doc_key = _clean_doc_key(doc_key)

    if as_lc_bundle.framework.metadata.get("doc_key") != doc_key:
        raise ValueError("LP doc_key must match the upstream framework metadata.")

    framework_provenance = as_lc_bundle.entity_provenance.get("framework", {})

    if not isinstance(framework_provenance, Mapping):
        raise ValueError("Upstream framework provenance must be a mapping.")

    if "doc_key" in framework_provenance and framework_provenance["doc_key"] != doc_key:
        raise ValueError("LP doc_key must match the upstream framework provenance.")

    selection = build_lp_selection(as_lc_bundle=as_lc_bundle, kg_config=kg_config)
    lp_config = kg_config.learning_progressions
    coordinate_index = LPDevelopmentalCoordinateIndex(
        coordinate_by_sfi_uuid=MappingProxyType(
            {
                record.sfi.case_identifier_uuid: record.coordinate
                for record in selection.sfis
            }
        ),
        ordered_values=tuple(lp_config.developmental_coordinate.ordered_values),
        statement_type=lp_config.developmental_coordinate.statement_type,
    )
    return LPCandidateFilter(
        _builds_towards_type_pairs=frozenset(
            (pair.source_statement_type, pair.target_statement_type)
            for pair in lp_config.builds_towards.allowed_statement_type_pairs
        ),
        _coordinate_index=coordinate_index,
        _doc_key=doc_key,
        _relates_to_type_pairs=frozenset(
            frozenset((pair.first_statement_type, pair.second_statement_type))
            for pair in lp_config.relates_to.allowed_statement_type_pairs
        ),
        _sfi_by_uuid=MappingProxyType(
            {record.sfi.case_identifier_uuid: record for record in selection.sfis}
        ),
    )


def build_lp_pair_id(
    *, doc_key: str, first_sfi_uuid: UUID | str, second_sfi_uuid: UUID | str
) -> str:
    """Mint one document-scoped identity for a distinct unordered SFI pair.

    UUID spelling and endpoint encounter order do not affect identity. The name uses
    the existing canonical KG namespace and a pair-specific identity prefix. Relation
    permissions, nomination evidence, and model wording are not identity inputs. This
    helper mints identity only; use the filter to establish pair admissibility.

    Parameters
    ----------
    doc_key
        Stable source document key.
    first_sfi_uuid
        One final SFI CASE UUID.
    second_sfi_uuid
        The other final SFI CASE UUID.

    Returns
    -------
    str
        Canonical UUIDv5 string shared by every encounter of this logical pair.

    Raises
    ------
    ValueError
        If the document key is blank, an endpoint UUID is invalid, or endpoints match.
    """

    doc_key = _clean_doc_key(doc_key)
    first_uuid, second_uuid = sorted(
        (UUID(str(first_sfi_uuid)), UUID(str(second_sfi_uuid))), key=str
    )

    if first_uuid == second_uuid:
        raise ValueError("Cannot mint an LP pair identity for a self-pair.")

    return str(
        uuid5(
            name=f"lc:curriculum:{doc_key}:lp_pair:{first_uuid}:{second_uuid}",
            namespace=Settings.LC_CANONICAL_NAMESPACE_UUID,
        )
    )
