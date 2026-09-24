"""Resolve local developmental coordinates from authoritative final SFI metadata.

Coordinate permissions describe only developmental compatibility. They neither select
eligible SFIs nor nominate or publish relationships. Curriculum participation and
unresolved-context exclusions must still be applied by callers.
"""

# Future Library
from __future__ import annotations

# Standard Library
import re

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal
from uuid import UUID

# Package Library
from kgfeg.kgs.lp_index import LPGraphIndex
from kgfeg.kgs.schemas import StandardsFrameworkItem
from kgfeg.schemas import CreateKGConfig, normalize_controlled_value_key


@dataclass(frozen=True, slots=True)
class _CoordinatePolicy:
    """Canonical label lookups and configured local developmental ranks."""

    rank_by_value: Mapping[str, int]
    statement_type: str
    statement_type_by_alias: Mapping[str, str]
    value_by_alias: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class LPDevelopmentalCoordinate:
    """Resolved coordinate or explicit absence for one final SFI.

    Attributes
    ----------
    canonical_value
        Configured canonical value, or ``None`` when no coordinate was supplied.
    rank
        Zero-based position in configured order, or ``None`` when missing. Absence must
        never be substituted with a numeric rank or positive rank evidence.
    sfi_uuid
        Final SFI CASE UUID.
    source_fields
        Metadata fields supporting the value, in deterministic order. These remain
        empty for a missing coordinate; no hierarchy or display-text fallback is used.
    statement_type
        Configured canonical coordinate statement type.
    status
        Explicit resolved or missing state. Invalid values raise instead of producing a
        record that could be mistaken for an ordinary missing coordinate.
    """

    canonical_value: str | None
    rank: int | None
    sfi_uuid: UUID
    source_fields: tuple[str, ...]
    statement_type: str
    status: Literal["missing", "resolved"]


@dataclass(frozen=True, slots=True)
class LPDevelopmentalCoordinateIndex:
    """Immutable coordinate snapshot for one validated AS+LC graph and local order.

    Attributes
    ----------
    coordinate_by_sfi_uuid
        One coordinate record per indexed SFI in CASE UUID order.
    ordered_values
        Canonical values in the exact configured developmental order.
    statement_type
        Canonical local statement type defining the single developmental axis.
    """

    coordinate_by_sfi_uuid: Mapping[UUID, LPDevelopmentalCoordinate]
    ordered_values: tuple[str, ...]
    statement_type: str

    def _coordinate(self, sfi_uuid: UUID) -> LPDevelopmentalCoordinate:
        """Require an SFI from this coordinate snapshot.

        Parameters
        ----------
        sfi_uuid
            Final SFI CASE UUID to look up.

        Returns
        -------
        LPDevelopmentalCoordinate
            The resolved or explicitly missing coordinate.

        Raises
        ------
        ValueError
            If the SFI is absent from the indexed graph.
        """

        if sfi_uuid not in self.coordinate_by_sfi_uuid:
            raise ValueError(f"Unknown SFI CASE UUID: {sfi_uuid}.")

        return self.coordinate_by_sfi_uuid[sfi_uuid]

    def allows_builds_towards(
        self, *, source_sfi_uuid: UUID, target_sfi_uuid: UUID
    ) -> bool:
        """Check only the coordinate constraint for a directed progression.

        Both coordinates must exist. Equal ranks permit either direction; unequal ranks
        permit only lower-to-higher direction, without a maximum forward gap. This does
        not check distinctness, statement-type policy, unresolved-context policy, or
        semantic evidence and therefore cannot authorize an edge.

        Parameters
        ----------
        source_sfi_uuid
            Proposed source SFI CASE UUID.
        target_sfi_uuid
            Proposed target SFI CASE UUID.

        Returns
        -------
        bool
            Whether the coordinates permit this developmental direction.

        Raises
        ------
        ValueError
            If either endpoint is absent from the coordinate snapshot.
        """

        source_rank = self._coordinate(source_sfi_uuid).rank
        target_rank = self._coordinate(target_sfi_uuid).rank
        return (
            source_rank is not None
            and target_rank is not None
            and source_rank <= target_rank
        )

    def allows_relates_to(self, *, first_sfi_uuid: UUID, second_sfi_uuid: UUID) -> bool:
        """Check only coordinate compatibility for a conceptual relationship.

        Every pair of resolved or missing coordinates is compatible, regardless of rank
        gap. Other participation rules, including unresolved-context exclusion, still
        take precedence; this permission cannot select or publish an edge.

        Parameters
        ----------
        first_sfi_uuid
            First endpoint SFI CASE UUID.
        second_sfi_uuid
            Second endpoint SFI CASE UUID.

        Returns
        -------
        bool
            True for any two endpoints present in this validated snapshot.

        Raises
        ------
        ValueError
            If either endpoint is absent from the coordinate snapshot.
        """

        self._coordinate(first_sfi_uuid)
        self._coordinate(second_sfi_uuid)
        return True


def _build_coordinate_policy(kg_config: CreateKGConfig) -> _CoordinatePolicy:
    """Build canonical lookups using the existing AS label and value policies.

    Parameters
    ----------
    kg_config
        Validated KG configuration with cross-validated AS and LP policies.

    Returns
    -------
    _CoordinatePolicy
        Immutable alias lookups and ranks from the explicit local order.

    Raises
    ------
    ValueError
        If alias mappings or the coordinate vocabulary are ambiguous or inconsistent.
    """

    coordinate = kg_config.learning_progressions.developmental_coordinate
    statement_type_by_alias = _build_statement_type_aliases(kg_config)
    value_by_alias: dict[str, str] = {}

    for policy in kg_config.academic_standards.statement_type_policy:
        if policy.statement_type != coordinate.statement_type:
            continue

        for controlled_value in policy.controlled_values:
            for label in [controlled_value.canonical_value, *controlled_value.aliases]:
                key = normalize_controlled_value_key(label)

                if not key:
                    continue

                previous = value_by_alias.get(key)

                if (
                    previous is not None
                    and previous != controlled_value.canonical_value
                ):
                    raise ValueError(
                        f"Ambiguous developmental-coordinate alias: {label!r}."
                    )

                value_by_alias[key] = controlled_value.canonical_value

    rank_by_value = {
        value: rank for rank, value in enumerate(coordinate.ordered_values)
    }

    if (
        not rank_by_value
        or len(rank_by_value) != len(coordinate.ordered_values)
        or set(rank_by_value) != set(value_by_alias.values())
    ):
        raise ValueError(
            "Developmental order must contain exactly the AS canonical coordinate "
            "values without duplicates."
        )

    return _CoordinatePolicy(
        rank_by_value=MappingProxyType(rank_by_value),
        statement_type=coordinate.statement_type,
        statement_type_by_alias=MappingProxyType(statement_type_by_alias),
        value_by_alias=MappingProxyType(value_by_alias),
    )


def _build_statement_type_aliases(kg_config: CreateKGConfig) -> dict[str, str]:
    """Build an unambiguous lookup for canonical AS statement-type labels.

    Parameters
    ----------
    kg_config
        Validated KG configuration containing the AS statement-type policy.

    Returns
    -------
    dict[str, str]
        Normalized canonical labels and aliases mapped to canonical statement types.

    Raises
    ------
    ValueError
        If an alias names more than one canonical statement type.
    """

    statement_type_by_alias: dict[str, str] = {}

    for policy in kg_config.academic_standards.statement_type_policy:
        for label in [policy.statement_type, *policy.aliases]:
            key = _normalize_statement_type_key(label)

            if not key:
                continue

            previous = statement_type_by_alias.get(key)

            if previous is not None and previous != policy.statement_type:
                raise ValueError(f"Ambiguous AS statement-type alias: {label!r}.")

            statement_type_by_alias[key] = policy.statement_type

    return statement_type_by_alias


def _canonicalize_value(
    *, field: str, policy: _CoordinatePolicy, sfi_uuid: UUID, value: object
) -> str:
    """Require one exact canonical value or configured alias.

    Parameters
    ----------
    field
        Metadata field used for diagnostics.
    policy
        Canonical coordinate vocabulary and rank policy.
    sfi_uuid
        Final SFI CASE UUID used for diagnostics.
    value
        Supplied coordinate value; collections, blanks, and unknown labels are invalid.

    Returns
    -------
    str
        One configured canonical coordinate value.

    Raises
    ------
    ValueError
        If the value is malformed, ambiguous, or unrecognized.
    """

    if isinstance(value, str):
        canonical_value = policy.value_by_alias.get(
            normalize_controlled_value_key(value)
        )

        if canonical_value is not None:
            return canonical_value

    raise ValueError(
        f"SFI {sfi_uuid} has an invalid or unrecognized {policy.statement_type!r} "
        f"coordinate in {field}: {value!r}. Expected one canonical value or alias."
    )


def _collect_scope_values(
    *, policy: _CoordinatePolicy, sfi: StandardsFrameworkItem
) -> dict[str, object]:
    """Collect supplied coordinate values from canonical SFI identity scope.

    Parameters
    ----------
    policy
        Canonical statement-type lookups and configured coordinate type.
    sfi
        Final SFI containing authoritative identity-scope metadata.

    Returns
    -------
    dict[str, object]
        Metadata field references mapped to supplied coordinate values. Equivalent
        scope labels remain separate so later canonicalization can detect conflicts.

    Raises
    ------
    ValueError
        If the scope container or its statement-type labels are invalid.
    """

    sfi_uuid = sfi.case_identifier_uuid
    scope_values = sfi.metadata.get("identity_scope_values", {})

    if not isinstance(scope_values, Mapping):
        raise ValueError(f"SFI {sfi_uuid} identity_scope_values must be a mapping.")

    supplied_values: dict[str, object] = {}

    for label, value in scope_values.items():
        if not isinstance(label, str):
            raise ValueError(f"SFI {sfi_uuid} identity-scope labels must be strings.")

        scope_type = policy.statement_type_by_alias.get(
            _normalize_statement_type_key(label)
        )

        if scope_type is None:
            raise ValueError(
                f"SFI {sfi_uuid} has unknown identity-scope type {label!r}."
            )

        if scope_type == policy.statement_type:
            supplied_values[f"identity_scope_values[{label!r}]"] = value

    return supplied_values


def _normalize_statement_type_key(value: str) -> str:
    """Match the AS statement-type policy's punctuation-insensitive label keys.

    Parameters
    ----------
    value
        Statement-type label or configured alias.

    Returns
    -------
    str
        Casefolded label with non-alphanumeric runs collapsed to spaces.
    """

    return re.sub(pattern=r"[^0-9a-z]+", repl=" ", string=value.casefold()).strip()


def _resolve_coordinate(
    *, policy: _CoordinatePolicy, sfi: StandardsFrameworkItem
) -> LPDevelopmentalCoordinate:
    """Resolve one SFI's canonical scope and, where applicable, its own value.

    Parameters
    ----------
    policy
        Canonical AS lookups and explicit local developmental order.
    sfi
        Authoritative final SFI, including canonical identity-scope metadata.

    Returns
    -------
    LPDevelopmentalCoordinate
        One coordinate with source-field references, or explicit absence.

    Raises
    ------
    ValueError
        If the statement type or coordinate metadata is invalid, or supplied coordinate
        values disagree after canonicalization.
    """

    sfi_uuid = sfi.case_identifier_uuid
    statement_type = policy.statement_type_by_alias.get(
        _normalize_statement_type_key(sfi.statement_type)
    )

    if statement_type is None:
        raise ValueError(
            f"SFI {sfi_uuid} has unknown statement type {sfi.statement_type!r}."
        )

    supplied_values = _collect_scope_values(policy=policy, sfi=sfi)

    if statement_type == policy.statement_type:
        own_value = sfi.metadata.get("canonical_statement_value")

        if own_value is not None:
            supplied_values["canonical_statement_value"] = own_value

        canonicalization = sfi.metadata.get("statement_value_canonicalization", {})

        if not isinstance(canonicalization, Mapping):
            raise ValueError(
                f"SFI {sfi_uuid} statement_value_canonicalization must be a mapping."
            )

        own_value = canonicalization.get("canonical_statement_value")

        if own_value is not None:
            supplied_values[
                "statement_value_canonicalization.canonical_statement_value"
            ] = own_value

    canonical_values = {
        field: _canonicalize_value(
            field=field, policy=policy, sfi_uuid=sfi_uuid, value=value
        )
        for field, value in sorted(supplied_values.items())
    }

    if len(set(canonical_values.values())) > 1:
        raise ValueError(
            f"SFI {sfi_uuid} has conflicting {policy.statement_type!r} coordinates: "
            f"{canonical_values!r}."
        )

    canonical_value = next(iter(canonical_values.values()), None)
    return LPDevelopmentalCoordinate(
        canonical_value=canonical_value,
        rank=(
            policy.rank_by_value[canonical_value]
            if canonical_value is not None
            else None
        ),
        sfi_uuid=sfi_uuid,
        source_fields=tuple(canonical_values),
        statement_type=policy.statement_type,
        status="resolved" if canonical_value is not None else "missing",
    )


def build_lp_coordinate_index(
    *, graph_index: LPGraphIndex, kg_config: CreateKGConfig
) -> LPDevelopmentalCoordinateIndex:
    """Resolve every final SFI coordinate without modifying the upstream graph.

    Use a graph index produced from a passed, error-free AS+LC bundle and the same
    run's validated KG configuration. Only canonical identity-scope values and a
    coordinate-type SFI's own canonical statement value are read. Display text, US
    grade tags, ancestor placement, source order, and LC support do not supply values.
    Invalid input raises before any partial coordinate index can be returned.

    Parameters
    ----------
    graph_index
        Validated AS+LC graph index, preserving DAG and unresolved ancestry context.
    kg_config
        Validated AS and LP configuration for the graph's curriculum.

    Returns
    -------
    LPDevelopmentalCoordinateIndex
        Immutable, UUID-ordered coordinate records using the exact configured order.

    Raises
    ------
    ValueError
        If the policy or any SFI coordinate is unrecognized, ambiguous, or conflicting.
    """

    policy = _build_coordinate_policy(kg_config)
    coordinates = {
        sfi_uuid: _resolve_coordinate(
            policy=policy, sfi=graph_index.sfi_by_uuid[sfi_uuid]
        )
        for sfi_uuid in sorted(graph_index.sfi_by_uuid, key=str)
    }
    return LPDevelopmentalCoordinateIndex(
        coordinate_by_sfi_uuid=MappingProxyType(coordinates),
        ordered_values=tuple(policy.rank_by_value),
        statement_type=policy.statement_type,
    )
