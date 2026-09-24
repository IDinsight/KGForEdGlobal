"""Named, non-embedding evidence and bounded nomination for LP endpoint pairs.

Evidence is weak nomination support, never a semantic judgment or a published edge. The
extractor applies the authoritative pair filter before inspecting any features. It does
not rank, persist, or adjudicate a candidate population. Its nomination index
contributes a bounded pair stream from each fixed evidence family without constructing
a large complete pair matrix. Weakly overlapping concepts can be missed; candidate
recall is unmeasured and these lexical features have not been validated for
multilingual progression judgments.
"""

# Future Library
from __future__ import annotations

# Standard Library
import re
import unicodedata

from collections import defaultdict, deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from uuid import UUID

# Third Party Library
from pydantic import JsonValue

# Package Library
from kgfeg.kgs.lp_admissibility import (
    LPCandidateFilter,
    LPPairAdmissibility,
    build_lp_pair_filter,
)
from kgfeg.kgs.lp_index import LPGraphIndex, build_lp_graph_index
from kgfeg.kgs.lp_selection import LPSFIEligibility
from kgfeg.kgs.schemas import (
    AcademicStandardsLCKGBundle,
    LearningComponent,
    LPCandidateEvidence,
    LPRelationshipType,
)
from kgfeg.schemas import CreateKGConfig

_NOMINATION_EVIDENCE_TYPES = (
    "shared_learning_components",
    "hierarchy_context",
    "lc_text_token_overlap",
    "lc_tag_token_overlap",
    "sfi_text_token_overlap",
    "sfi_text_trigram_overlap",
    "source_code_prefix",
    "local_rank_proximity",
    "source_page_proximity",
)
_NominationBuckets = dict[str, dict[str, set[UUID]]]


@dataclass(frozen=True, slots=True)
class LPEvidenceExtractor:
    """Run-scoped snapshot for filtered, on-demand pair evidence.

    Construct with ``build_lp_evidence_extractor``. Private graph and policy snapshots
    isolate feature evaluation from later input mutation. Returned records are fresh
    per call, including the filter's complete endpoint warning and provenance context.
    """

    _graph_index: LPGraphIndex
    _pair_filter: LPCandidateFilter

    @property
    def evidence_types(self) -> tuple[str, ...]:
        """Return the fixed nomination and ranking evidence order.

        Returns
        -------
        tuple[str, ...]
            Code-owned evidence names from strongest precedence to weakest.
        """

        return _NOMINATION_EVIDENCE_TYPES

    def extract_pair(
        self, *, first_sfi_uuid: UUID | str, second_sfi_uuid: UUID | str
    ) -> LPPairEvidence | None:
        """Evaluate every built-in feature for one admissible logical pair.

        Parameters
        ----------
        first_sfi_uuid
            One final SFI CASE UUID in this run, in any valid UUID spelling.
        second_sfi_uuid
            The other final SFI CASE UUID; encounter order has no semantic meaning.

        Returns
        -------
        LPPairEvidence | None
            Canonical permission and named matches, possibly with no matching feature.
            Self-pairs and policy-disallowed pairs return ``None``.

        Raises
        ------
        ValueError
            If either endpoint is malformed or outside this run's final SFIs.
        """

        pair = self._pair_filter.filter_pair(
            first_sfi_uuid=first_sfi_uuid, second_sfi_uuid=second_sfi_uuid
        )

        if pair is None:
            return None

        evidence = [
            *_code_evidence(pair),
            *_hierarchy_evidence(graph_index=self._graph_index, pair=pair),
            *_lc_evidence(graph_index=self._graph_index, pair=pair),
            *_rank_evidence(pair),
            *_source_evidence(pair),
            *_text_evidence(pair),
        ]
        return LPPairEvidence(
            admissibility=pair,
            evidence=tuple(sorted(evidence, key=lambda item: item.evidence_type)),
        )

    def nominate_pair_endpoints(self, pair_limit: int) -> tuple[tuple[UUID, UUID], ...]:
        """Build a bounded union of endpoint pairs indexed by fixed evidence signals.

        Each evidence family contributes at most ``pair_limit`` deterministic endpoint
        pairs. Duplicate pairs across buckets and signals are retained once. Every
        population follows these evidence-indexed streams; no complete-pair shortcut
        bypasses nomination when a matrix happens to fit the aggregate ceiling.

        Parameters
        ----------
        pair_limit
            Maximum retained candidate population implied by configured budgets.

        Returns
        -------
        tuple[tuple[UUID, UUID], ...]
            Canonical unordered endpoint pairs requiring hard-filtered evidence
            extraction, bounded by evidence-family count times ``pair_limit``.

        Raises
        ------
        ValueError
            If the pair limit is not a nonnegative integer.
        """

        if (
            isinstance(pair_limit, bool)
            or not isinstance(pair_limit, int)
            or pair_limit < 0
        ):
            raise ValueError("LP nomination pair_limit must be a nonnegative integer.")

        eligible_sfis = self._pair_filter.eligible_sfis
        eligible_sfi_uuids = tuple(
            record.sfi.case_identifier_uuid for record in eligible_sfis
        )
        statement_type_by_sfi_uuid = {
            record.sfi.case_identifier_uuid: record.statement_type
            for record in eligible_sfis
        }
        if pair_limit == 0 or len(eligible_sfi_uuids) < 2:
            return ()

        buckets_by_evidence_type = _evidence_nomination_buckets(
            eligible_sfis=eligible_sfis, graph_index=self._graph_index
        )
        nominated_pairs: dict[tuple[UUID, UUID], None] = {}

        for proposal_offset, evidence_type in enumerate(_NOMINATION_EVIDENCE_TYPES):
            for pair in _bounded_pairs_from_buckets(
                admissible_statement_type_pairs=(
                    self._pair_filter.admissible_statement_type_pairs
                ),
                buckets=buckets_by_evidence_type[evidence_type],
                pair_limit=pair_limit,
                proposal_offset=proposal_offset,
                statement_type_by_sfi_uuid=statement_type_by_sfi_uuid,
            ):
                nominated_pairs.setdefault(pair, None)

        return tuple(nominated_pairs)


@dataclass(frozen=True, slots=True)
class LPPairEvidence:
    """Filtered pair context and feature matches without population selection.

    Attributes
    ----------
    admissibility
        Unchanged canonical pair identity, allowed outcomes, complete endpoint records,
        warnings, unresolved status, fallback audit IDs, and upstream provenance.
    evidence
        Named positive matches in name order, not relevance order. An empty tuple means
        the pair is permitted but no feature matched. Audit flags and missing values
        are retained in endpoint context; they never become positive feature matches.
    """

    admissibility: LPPairAdmissibility
    evidence: tuple[LPCandidateEvidence, ...]


def _add_nomination_bucket(
    *, buckets: dict[str, set[UUID]], key: str, sfi_uuid: UUID
) -> None:
    """Add one SFI to one deterministic evidence-value bucket.

    Parameters
    ----------
    buckets
        Mutable evidence-value index under construction.
    key
        Canonical value whose shared presence can nominate a pair.
    sfi_uuid
        Eligible endpoint carrying the value.
    """

    buckets.setdefault(key, set()).add(sfi_uuid)


def _add_nomination_values(
    *,
    buckets_by_evidence_type: _NominationBuckets,
    evidence_type: str,
    sfi_uuid: UUID,
    values: Iterable[str],
) -> None:
    """Add unique nomination values to evidence buckets for one endpoint.

    Values are deduplicated and processed in deterministic sorted order. Each value is
    used unchanged as a bucket key, and the endpoint UUID is added to that bucket's set.

    Parameters
    ----------
    buckets_by_evidence_type
        Mutable nomination buckets organized by evidence type and evidence value.
    evidence_type
        Evidence category whose buckets receive the endpoint.
    sfi_uuid
        Endpoint UUID to add to each selected bucket.
    values
        Evidence values to deduplicate, sort, and index.
    """

    for value in sorted(set(values)):
        _add_nomination_bucket(
            buckets=buckets_by_evidence_type[evidence_type],
            key=value,
            sfi_uuid=sfi_uuid,
        )


def _ancestor_distances(
    *, graph_index: LPGraphIndex, sfi_uuid: UUID
) -> dict[UUID, int]:
    """Visit all positive DAG branches without enumerating their path combinations.

    Parameters
    ----------
    graph_index
        Validated graph whose SFI adjacency excludes every framework attachment.
    sfi_uuid
        Endpoint whose ancestors are sought.

    Returns
    -------
    dict[UUID, int]
        Every reachable SFI ancestor with its minimum positive edge distance. Minimum
        distance summarizes proximity without selecting one canonical ancestry path.
    """

    distances: dict[UUID, int] = {}
    pending = deque(
        (parent, 1) for parent in graph_index.parent_sfi_uuids_by_sfi_uuid[sfi_uuid]
    )

    while pending:
        ancestor, distance = pending.popleft()

        if ancestor in distances:
            continue

        distances[ancestor] = distance
        pending.extend(
            (parent, distance + 1)
            for parent in graph_index.parent_sfi_uuids_by_sfi_uuid[ancestor]
        )

    return distances


def _bounded_pair_stream_window(
    *,
    pair_limit: int,
    pair_streams: deque[Iterator[tuple[UUID, UUID]]],
    proposal_offset: int,
) -> tuple[tuple[UUID, UUID], ...]:
    """Select a bounded, offset window from lazy pair streams.

    The streams are advanced in round-robin order, with each produced pair considered
    at most once after deduplication. The first ``proposal_offset`` unique pairs are
    deferred so different evidence families can begin from different positions.
    Deferred pairs are used as fallback values when too few later pairs are available,
    preventing the offset from eliminating an otherwise valid proposal population.

    Enumeration stops after at most ``pair_limit + proposal_offset`` stream advances,
    so the complete pair population is never materialized. The supplied deque and its
    iterators are consumed during selection.

    Parameters
    ----------
    pair_limit
        Maximum number of unique pairs to return.
    pair_streams
        Ordered lazy pair iterators to consume in round-robin order.
    proposal_offset
        Number of initial unique pairs to defer before retaining later pairs.

    Returns
    -------
    tuple[tuple[UUID, UUID], ...]
        Deterministically ordered unique pairs, never exceeding ``pair_limit``.
    """

    pairs: list[tuple[UUID, UUID]] = []
    skipped_pairs: list[tuple[UUID, UUID]] = []
    seen: set[tuple[UUID, UUID]] = set()
    pair_stream_advances = 0
    pair_stream_advance_limit = pair_limit + proposal_offset

    while pair_streams and pair_stream_advances < pair_stream_advance_limit:
        pair_stream = pair_streams.popleft()

        try:
            pair = next(pair_stream)
        except StopIteration:
            continue

        pair_streams.append(pair_stream)
        pair_stream_advances += 1

        if pair in seen:
            continue

        seen.add(pair)

        if len(skipped_pairs) < proposal_offset:
            skipped_pairs.append(pair)
        else:
            pairs.append(pair)

    pairs.extend(skipped_pairs[: pair_limit - len(pairs)])

    return tuple(pairs)


def _bounded_pairs_from_buckets(
    *,
    admissible_statement_type_pairs: tuple[tuple[str, str], ...],
    buckets: dict[str, set[UUID]],
    pair_limit: int,
    proposal_offset: int,
    statement_type_by_sfi_uuid: dict[UUID, str],
) -> tuple[tuple[UUID, UUID], ...]:
    """Round-robin bounded proposals inside D1-admissible type cohorts.

    Parameters
    ----------
    admissible_statement_type_pairs
        Canonical unordered statement-type cohorts permitted by D1.
    buckets
        Evidence values mapped to eligible endpoints carrying each value.
    pair_limit
        Maximum retained pair proposals for this evidence family.
    proposal_offset
        Fixed evidence-family offset used to diversify otherwise identical bounded
        streams. Skipped proposals wrap to the end when the stream is smaller than the
        requested window, so offsetting cannot erase a family's only evidence pairs.
    statement_type_by_sfi_uuid
        Validated statement type for every eligible endpoint.

    Returns
    -------
    tuple[tuple[UUID, UUID], ...]
        Stable unique endpoint proposals, never exceeding ``pair_limit``.
    """

    pair_group_specs = _nomination_pair_group_specs(
        admissible_statement_type_pairs=admissible_statement_type_pairs,
        buckets=buckets,
        statement_type_by_sfi_uuid=statement_type_by_sfi_uuid,
    )
    return _bounded_pair_stream_window(
        pair_limit=pair_limit,
        pair_streams=_nomination_pair_streams(pair_group_specs=pair_group_specs),
        proposal_offset=proposal_offset,
    )


def _code_evidence(pair: LPPairAdmissibility) -> list[LPCandidateEvidence]:
    """Compare source-authoritative codes as generic leading alphanumeric segments.

    Parameters
    ----------
    pair
        Filtered pair retaining the original codes and all AS code/merge audit context.

    Returns
    -------
    list[LPCandidateEvidence]
        A weak prefix match, or no match. Normalized-code metadata and invented local
        code grammars are not consulted; warnings do not certify a code as correct.
    """

    first_code = pair.first_sfi.sfi.statement_code
    second_code = pair.second_sfi.sfi.statement_code

    if not first_code or not second_code:
        return []

    first_parts = _word_tokens(first_code)
    second_parts = _word_tokens(second_code)
    common: list[JsonValue] = []

    for first, second in zip(first_parts, second_parts):
        if first != second:
            break

        common.append(first)

    return (
        []
        if not common
        else [
            _nomination(
                evidence_type="source_code_prefix",
                pair=pair,
                references=_sfi_references(pair),
                triggering_values={
                    "common_segments": common,
                    "first_code": first_code,
                    "second_code": second_code,
                    "strength": "weak",
                },
            )
        ]
    )


def _cross_type_pairs(
    *, first_sfi_uuids: tuple[UUID, ...], second_sfi_uuids: tuple[UUID, ...]
) -> Iterator[tuple[UUID, UUID]]:
    """Yield a balanced Cartesian stream for two distinct statement types.

    Parameters
    ----------
    first_sfi_uuids
        Canonically ordered endpoints from the first statement type.
    second_sfi_uuids
        Canonically ordered endpoints from the second statement type.

    Yields
    ------
    tuple[UUID, UUID]
        Every cross-type pair once, in balanced cyclic order and canonical UUID order.
    """

    for distance in range(len(second_sfi_uuids)):
        for first_index, first_sfi_uuid in enumerate(first_sfi_uuids):
            second_sfi_uuid = second_sfi_uuids[
                (first_index + distance) % len(second_sfi_uuids)
            ]
            first_endpoint, second_endpoint = sorted(
                (first_sfi_uuid, second_sfi_uuid), key=str
            )
            yield first_endpoint, second_endpoint


def _cyclic_pairs(sfi_uuids: tuple[UUID, ...]) -> Iterator[tuple[UUID, UUID]]:
    """Yield balanced canonical unordered pairs without eager matrix construction.

    Parameters
    ----------
    sfi_uuids
        Unique endpoint UUIDs in canonical order.

    Yields
    ------
    tuple[UUID, UUID]
        Each logical pair at most once, visiting increasing cyclic distance.
    """

    seen: set[tuple[UUID, UUID]] = set()

    for distance in range(1, len(sfi_uuids)):
        for first_index, first_sfi_uuid in enumerate(sfi_uuids):
            second_sfi_uuid = sfi_uuids[(first_index + distance) % len(sfi_uuids)]
            first_endpoint, second_endpoint = sorted(
                (first_sfi_uuid, second_sfi_uuid), key=str
            )
            pair = (first_endpoint, second_endpoint)

            if pair in seen:
                continue

            seen.add(pair)
            yield pair


def _evidence_nomination_buckets(
    *, eligible_sfis: tuple[LPSFIEligibility, ...], graph_index: LPGraphIndex
) -> dict[str, dict[str, set[UUID]]]:
    """Index eligible endpoints by values used by every fixed evidence family.

    Parameters
    ----------
    eligible_sfis
        Canonical participation records with coordinates and source context.
    graph_index
        Validated DAG and LC indexes for the same immutable upstream bundle.

    Returns
    -------
    dict[str, dict[str, set[UUID]]]
        Evidence type, then canonical triggering value, then matching endpoints.
        Framework-root fallback placement and audit warnings are never indexed.
    """

    buckets_by_evidence_type: _NominationBuckets = {
        evidence_type: defaultdict(set) for evidence_type in _NOMINATION_EVIDENCE_TYPES
    }

    for record in eligible_sfis:
        _index_hierarchy_nomination_values(
            buckets_by_evidence_type=buckets_by_evidence_type,
            graph_index=graph_index,
            record=record,
        )
        _index_lc_nomination_values(
            buckets_by_evidence_type=buckets_by_evidence_type,
            graph_index=graph_index,
            record=record,
        )
        _index_sfi_nomination_values(
            buckets_by_evidence_type=buckets_by_evidence_type, record=record
        )
        _index_proximity_nomination_values(
            buckets_by_evidence_type=buckets_by_evidence_type, record=record
        )

    return {
        evidence_type: dict(buckets)
        for evidence_type, buckets in buckets_by_evidence_type.items()
    }


def _hierarchy_evidence(
    *, graph_index: LPGraphIndex, pair: LPPairAdmissibility
) -> list[LPCandidateEvidence]:
    """Capture shared context and ancestry across every positive SFI branch.

    Parameters
    ----------
    graph_index
        Validated DAG index with fallback/root placement outside positive adjacency.
    pair
        Canonical pair whose complete direct-parent lists remain in endpoint context.

    Returns
    -------
    list[LPCandidateEvidence]
        Shared ancestor and/or endpoint ancestry values. A shared framework alone,
        including unresolved fallback placement, never produces a match.
    """

    first = _ancestor_distances(graph_index=graph_index, sfi_uuid=pair.first_sfi_uuid)
    second = _ancestor_distances(graph_index=graph_index, sfi_uuid=pair.second_sfi_uuid)
    common = sorted(first.keys() & second.keys(), key=str)
    values: dict[str, JsonValue] = {}

    if common:
        values["shared_ancestors"] = [
            {
                "first_distance": first[ancestor],
                "second_distance": second[ancestor],
                "sfi_uuid": str(ancestor),
                "statement_type": graph_index.sfi_by_uuid[ancestor].statement_type,
            }
            for ancestor in common
        ]

    if pair.first_sfi_uuid in second:
        values["first_is_ancestor_of_second_distance"] = second[pair.first_sfi_uuid]

    if pair.second_sfi_uuid in first:
        values["second_is_ancestor_of_first_distance"] = first[pair.second_sfi_uuid]

    return (
        []
        if not values
        else [
            _nomination(
                evidence_type="hierarchy_context",
                pair=pair,
                references=[
                    *_sfi_references(pair),
                    *(f"sfi:{ancestor}" for ancestor in common),
                ],
                triggering_values=values,
            )
        ]
    )


def _index_hierarchy_nomination_values(
    *,
    buckets_by_evidence_type: _NominationBuckets,
    graph_index: LPGraphIndex,
    record: LPSFIEligibility,
) -> None:
    """Index one endpoint under its own and reachable ancestor identifiers.

    The endpoint is added to the ``hierarchy_context`` bucket for its own UUID and for
    every SFI ancestor reachable through the graph index. Only ancestor identity is
    indexed; path and distance information are not retained.

    Parameters
    ----------
    buckets_by_evidence_type
        Mutable nomination buckets organized by evidence type and evidence value.
    graph_index
        Graph index containing the endpoint's parent and ancestor relationships.
    record
        Eligibility record for the endpoint being indexed.
    """

    sfi_uuid = record.sfi.case_identifier_uuid
    ancestor_uuids = _ancestor_distances(graph_index=graph_index, sfi_uuid=sfi_uuid)
    _add_nomination_values(
        buckets_by_evidence_type=buckets_by_evidence_type,
        evidence_type="hierarchy_context",
        sfi_uuid=sfi_uuid,
        values=(str(ancestor_uuid) for ancestor_uuid in {sfi_uuid, *ancestor_uuids}),
    )


def _index_lc_nomination_values(
    *,
    buckets_by_evidence_type: _NominationBuckets,
    graph_index: LPGraphIndex,
    record: LPSFIEligibility,
) -> None:
    """Index Learning Component evidence values for one endpoint.

    The endpoint is indexed by the identifiers of its supporting Learning Components,
    tokens from their descriptions, and tokens from well-formed optional tags. These
    values allow endpoints sharing an exact component or lexical component evidence to
    enter the same nomination bucket.

    Parameters
    ----------
    buckets_by_evidence_type
        Mutable nomination buckets organized by evidence type and evidence value.
    graph_index
        Graph index containing the Learning Components associated with the
        endpoint.
    record
        Eligibility record for the endpoint being indexed.
    """

    sfi_uuid = record.sfi.case_identifier_uuid
    components = graph_index.learning_components_by_sfi_uuid[sfi_uuid]
    _add_nomination_values(
        buckets_by_evidence_type=buckets_by_evidence_type,
        evidence_type="shared_learning_components",
        sfi_uuid=sfi_uuid,
        values=(str(component.identifier) for component in components),
    )
    _add_nomination_values(
        buckets_by_evidence_type=buckets_by_evidence_type,
        evidence_type="lc_text_token_overlap",
        sfi_uuid=sfi_uuid,
        values=(
            token
            for component in components
            for token in _word_tokens(component.description)
        ),
    )
    _add_nomination_values(
        buckets_by_evidence_type=buckets_by_evidence_type,
        evidence_type="lc_tag_token_overlap",
        sfi_uuid=sfi_uuid,
        values=(token for component in components for token in _tag_tokens(component)),
    )


def _index_proximity_nomination_values(
    *, buckets_by_evidence_type: _NominationBuckets, record: LPSFIEligibility
) -> None:
    """Index local-rank and source-page proximity values for one endpoint.

    A populated local rank is indexed under its current and immediately preceding
    integer values, allowing equal or adjacent ranks to share a bucket. Each validated
    source-page index is handled the same way, allowing endpoints on equal or adjacent
    pages to share a bucket. Missing ranks and unavailable, malformed, or conflicting
    page indexes contribute no values.

    Parameters
    ----------
    buckets_by_evidence_type
        Mutable nomination buckets organized by evidence type and evidence value.
    record
        Eligibility record containing the endpoint's coordinate and source-page
        provenance.
    """

    sfi_uuid = record.sfi.case_identifier_uuid

    if record.coordinate.rank is not None:
        _add_nomination_values(
            buckets_by_evidence_type=buckets_by_evidence_type,
            evidence_type="local_rank_proximity",
            sfi_uuid=sfi_uuid,
            values=(
                str(record.coordinate.rank - 1),
                str(record.coordinate.rank),
            ),
        )

    _add_nomination_values(
        buckets_by_evidence_type=buckets_by_evidence_type,
        evidence_type="source_page_proximity",
        sfi_uuid=sfi_uuid,
        values=(
            str(page_bucket)
            for page_index in _source_pages(record)
            for page_bucket in (page_index - 1, page_index)
        ),
    )


def _index_sfi_nomination_values(
    *, buckets_by_evidence_type: _NominationBuckets, record: LPSFIEligibility
) -> None:
    """Index statement text and source-code evidence values for one endpoint.

    The endpoint is indexed by normalized word tokens and character trigrams from its
    description. When the statement code contains at least one normalized word token,
    its leading token is also indexed as the source-code prefix.

    Parameters
    ----------
    buckets_by_evidence_type
        Mutable nomination buckets organized by evidence type and evidence value.
    record
        Eligibility record containing the statement text, source code, and
        endpoint UUID to index.
    """

    sfi_uuid = record.sfi.case_identifier_uuid
    _add_nomination_values(
        buckets_by_evidence_type=buckets_by_evidence_type,
        evidence_type="sfi_text_token_overlap",
        sfi_uuid=sfi_uuid,
        values=_word_tokens(record.sfi.description),
    )
    _add_nomination_values(
        buckets_by_evidence_type=buckets_by_evidence_type,
        evidence_type="sfi_text_trigram_overlap",
        sfi_uuid=sfi_uuid,
        values=_trigrams(record.sfi.description),
    )
    code_tokens = _word_tokens(record.sfi.statement_code or "")

    if code_tokens:
        _add_nomination_bucket(
            buckets=buckets_by_evidence_type["source_code_prefix"],
            key=code_tokens[0],
            sfi_uuid=sfi_uuid,
        )


def _lc_evidence(
    *, graph_index: LPGraphIndex, pair: LPPairAdmissibility
) -> list[LPCandidateEvidence]:
    """Compare exact supporting LC identities and their text/tag token bags.

    Parameters
    ----------
    graph_index
        Validated support adjacency; LCs are evidence only, never pair endpoints.
    pair
        Canonical pair to inspect.

    Returns
    -------
    list[LPCandidateEvidence]
        Independent exact, description-token, and tag-token overlap matches. Repeated
        generic LCs may match but cannot establish pedagogical correctness or an edge.
    """

    first = graph_index.learning_components_by_sfi_uuid[pair.first_sfi_uuid]
    second = graph_index.learning_components_by_sfi_uuid[pair.second_sfi_uuid]
    first_ids = frozenset(str(component.identifier) for component in first)
    second_ids = frozenset(str(component.identifier) for component in second)
    references = [f"lc:{identifier}" for identifier in sorted(first_ids | second_ids)]
    return [
        *_overlap_evidence(
            evidence_type="shared_learning_components",
            first=first_ids,
            pair=pair,
            references=[
                f"lc:{identifier}" for identifier in sorted(first_ids & second_ids)
            ],
            second=second_ids,
        ),
        *_overlap_evidence(
            evidence_type="lc_text_token_overlap",
            first=frozenset(
                token
                for component in first
                for token in _word_tokens(component.description)
            ),
            pair=pair,
            references=references,
            second=frozenset(
                token
                for component in second
                for token in _word_tokens(component.description)
            ),
        ),
        *_overlap_evidence(
            evidence_type="lc_tag_token_overlap",
            first=frozenset(
                token for component in first for token in _tag_tokens(component)
            ),
            pair=pair,
            references=references,
            second=frozenset(
                token for component in second for token in _tag_tokens(component)
            ),
        ),
    ]


def _nomination(
    *,
    evidence_type: str,
    pair: LPPairAdmissibility,
    references: list[str],
    triggering_values: dict[str, JsonValue],
) -> LPCandidateEvidence:
    """Attach feature values only to relationship types allowed by the hard filter.

    Parameters
    ----------
    evidence_type
        Fixed code-owned name for this feature.
    pair
        Filtered canonical pair supplying authoritative admissible outcomes.
    references
        Source SFI or LC identifiers supporting the concrete values.
    triggering_values
        Nonempty JSON values explaining the feature match.

    Returns
    -------
    LPCandidateEvidence
        Intrinsically validated evidence without a chosen relation or direction.
    """

    relationships: list[LPRelationshipType] = []

    for option in pair.admissible_decisions:
        if (
            option.decision in ("buildsTowards", "relatesTo")
            and option.decision not in relationships
        ):
            relationships.append(option.decision)

    return LPCandidateEvidence(
        evidence_type=evidence_type,
        nominated_relationships=relationships,
        references=sorted(set(references)),
        triggering_values=triggering_values,
    )


def _nomination_pair_group_specs(
    *,
    admissible_statement_type_pairs: tuple[tuple[str, str], ...],
    buckets: dict[str, set[UUID]],
    statement_type_by_sfi_uuid: dict[UUID, str],
) -> tuple[tuple[tuple[UUID, ...], tuple[UUID, ...]], ...]:
    """Build deterministic endpoint-group specifications for lazy nomination.

    Each evidence bucket is partitioned by statement type. For every admissible type
    pairing, this function records a group capable of producing candidate pairs.
    Same-type groups place all endpoints in the first tuple and use an empty second
    tuple; cross-type groups keep the two endpoint cohorts separate.

    Groups repeated across evidence buckets are deduplicated. Endpoints and the
    returned groups are sorted deterministically by UUID string. This function does not
    materialize the combinatorial pair population; downstream code enumerates each
    group lazily under the configured nomination bound.

    Parameters
    ----------
    admissible_statement_type_pairs
        Canonical unordered statement-type pairings eligible for nomination.
    buckets
        Evidence values mapped to the eligible SFI endpoints carrying each value.
    statement_type_by_sfi_uuid
        Validated statement type for every endpoint present in ``buckets``.

    Returns
    -------
    tuple[tuple[tuple[UUID, ...], tuple[UUID, ...]], ...]
        Stable, unique endpoint-group specifications. Same-type groups contain at least
        two endpoints in the first tuple and an empty second tuple. Cross-type groups
        contain at least one endpoint in each tuple.
    """

    pair_group_specs: set[tuple[tuple[UUID, ...], tuple[UUID, ...]]] = set()

    for _, sfi_uuids in sorted(buckets.items()):
        sfi_uuids_by_type: dict[str, set[UUID]] = defaultdict(set)

        for sfi_uuid in sfi_uuids:
            sfi_uuids_by_type[statement_type_by_sfi_uuid[sfi_uuid]].add(sfi_uuid)

        for first_type, second_type in admissible_statement_type_pairs:
            first_sfi_uuids = tuple(sorted(sfi_uuids_by_type[first_type], key=str))

            if first_type == second_type:
                if len(first_sfi_uuids) > 1:
                    pair_group_specs.add((first_sfi_uuids, ()))

                continue

            second_sfi_uuids = tuple(sorted(sfi_uuids_by_type[second_type], key=str))

            if first_sfi_uuids and second_sfi_uuids:
                pair_group_specs.add((first_sfi_uuids, second_sfi_uuids))

    return tuple(
        sorted(
            pair_group_specs,
            key=lambda group: (tuple(map(str, group[0])), tuple(map(str, group[1]))),
        )
    )


def _nomination_pair_streams(
    *, pair_group_specs: tuple[tuple[tuple[UUID, ...], tuple[UUID, ...]], ...]
) -> deque[Iterator[tuple[UUID, UUID]]]:
    """Create lazy pair streams from deterministic endpoint-group specifications.

    Each group produces one iterator. Cross-type groups enumerate pairs between their
    two endpoint cohorts, while same-type groups use the single populated cohort to
    produce distinct endpoint pairs. The group order is preserved in a deque so
    downstream code can consume the streams in round-robin order without materializing
    every possible pair.

    Parameters
    ----------
    pair_group_specs
        Ordered endpoint-group specifications. An empty second endpoint tuple
        identifies a same-type group; a populated second tuple identifies a cross-type
        group.

    Returns
    -------
    deque[Iterator[tuple[UUID, UUID]]]
        Lazy pair iterators in the same deterministic order as the input groups.
    """

    pair_streams: deque[Iterator[tuple[UUID, UUID]]] = deque()

    for first_sfi_uuids, second_sfi_uuids in pair_group_specs:
        pair_streams.append(
            _cross_type_pairs(
                first_sfi_uuids=first_sfi_uuids, second_sfi_uuids=second_sfi_uuids
            )
            if second_sfi_uuids
            else _cyclic_pairs(first_sfi_uuids)
        )

    return pair_streams


def _overlap_evidence(
    *,
    evidence_type: str,
    first: frozenset[str],
    pair: LPPairAdmissibility,
    references: list[str],
    second: frozenset[str],
) -> list[LPCandidateEvidence]:
    """Record a nonempty set intersection and its exact Jaccard inputs.

    Parameters
    ----------
    evidence_type
        Stable name of the concrete feature family.
    first
        Lower-UUID endpoint's tokens, character trigrams, or LC identifiers.
    pair
        Filtered pair supplying relation permissions.
    references
        Identifiers for the source records used to build the compared sets.
    second
        Higher-UUID endpoint's corresponding values.

    Returns
    -------
    list[LPCandidateEvidence]
        A single explainable match when at least one value is shared, otherwise empty.
        Jaccard is descriptive evidence, not confidence or an acceptance threshold.
    """

    shared = first & second

    if not shared:
        return []

    union_count = len(first | second)
    return [
        _nomination(
            evidence_type=evidence_type,
            pair=pair,
            references=references,
            triggering_values={
                "first_count": len(first),
                "jaccard": len(shared) / union_count,
                "second_count": len(second),
                "shared_count": len(shared),
                "shared_values": list(sorted(shared)),
                "union_count": union_count,
            },
        )
    ]


def _rank_evidence(pair: LPPairAdmissibility) -> list[LPCandidateEvidence]:
    """Record same or adjacent configured local ranks as weak proximity evidence.

    Parameters
    ----------
    pair
        Filtered pair whose coordinates were resolved from canonical identity scope.

    Returns
    -------
    list[LPCandidateEvidence]
        A proximity match when both ranks exist and differ by at most one. Larger gaps
        remain admissible to other signals; a missing coordinate never becomes a rank.
    """

    first = pair.first_sfi.coordinate
    second = pair.second_sfi.coordinate

    if first.rank is None or second.rank is None:
        return []

    gap = abs(first.rank - second.rank)
    return (
        []
        if gap > 1
        else [
            _nomination(
                evidence_type="local_rank_proximity",
                pair=pair,
                references=_sfi_references(pair),
                triggering_values={
                    "coordinate_statement_type": first.statement_type,
                    "first_rank": first.rank,
                    "first_value": first.canonical_value,
                    "rank_gap": gap,
                    "second_rank": second.rank,
                    "second_value": second.canonical_value,
                    "strength": "weak",
                },
            )
        ]
    )


def _sfi_references(pair: LPPairAdmissibility) -> list[str]:
    """Return canonical endpoint references for evidence and retained audit context.

    Parameters
    ----------
    pair
        Filtered canonical pair.

    Returns
    -------
    list[str]
        Endpoint SFI references in canonical UUID order.
    """

    return [f"sfi:{pair.first_sfi_uuid}", f"sfi:{pair.second_sfi_uuid}"]


def _source_evidence(pair: LPPairAdmissibility) -> list[LPCandidateEvidence]:
    """Compare preserved page positions without interpreting source order as rank.

    Parameters
    ----------
    pair
        Filtered pair with original source provenance and metadata.

    Returns
    -------
    list[LPCandidateEvidence]
        Weak same/adjacent-page evidence with the nearest page pair. Missing, invalid,
        or conflicting positions supply no feature; original audit context is retained.
    """

    first_pages = _source_pages(pair.first_sfi)
    second_pages = _source_pages(pair.second_sfi)

    if not first_pages or not second_pages:
        return []

    first_index = second_index = 0
    closest = (abs(first_pages[0] - second_pages[0]), first_pages[0], second_pages[0])

    while first_index < len(first_pages) and second_index < len(second_pages):
        first = first_pages[first_index]
        second = second_pages[second_index]
        closest = min(closest, (abs(first - second), first, second))

        if first <= second:
            first_index += 1
        else:
            second_index += 1

    gap, first_page, second_page = closest
    return (
        []
        if gap > 1
        else [
            _nomination(
                evidence_type="source_page_proximity",
                pair=pair,
                references=_sfi_references(pair),
                triggering_values={
                    "first_page_index": first_page,
                    "first_source_page_indexes": list(first_pages),
                    "page_gap": gap,
                    "second_page_index": second_page,
                    "second_source_page_indexes": list(second_pages),
                    "strength": "weak",
                },
            )
        ]
    )


def _source_pages(record: LPSFIEligibility) -> tuple[int, ...]:
    """Read valid page indexes only when populated upstream copies agree.

    Parameters
    ----------
    record
        Endpoint with unmodified exported metadata and entity provenance.

    Returns
    -------
    tuple[int, ...]
        Sorted unique nonnegative integer indexes, or empty when unavailable,
        malformed, or conflicting. Arbitrary metadata keys and source-window IDs are
        not positions.
    """

    populations: list[tuple[int, ...]] = []

    for source in (record.sfi.metadata, record.source_provenance):
        if "source_page_indexes" not in source:
            continue

        pages = source["source_page_indexes"]

        if not isinstance(pages, list) or any(
            isinstance(page, bool) or not isinstance(page, int) or page < 0
            for page in pages
        ):
            return ()

        populations.append(tuple(sorted(set(pages))))

    if not populations or any(pages != populations[0] for pages in populations):
        return ()

    return populations[0]


def _tag_tokens(component: LearningComponent) -> frozenset[str]:
    """Tokenize a well-formed optional LC tag list without stringifying metadata.

    Parameters
    ----------
    component
        Final supporting LC whose tags are optional metadata.

    Returns
    -------
    frozenset[str]
        Unicode tag tokens, or an empty set when tags are absent or malformed.
    """

    tags = component.metadata.get("tags", [])

    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        return frozenset()

    return frozenset(token for tag in tags for token in _word_tokens(tag))


def _text_evidence(pair: LPPairAdmissibility) -> list[LPCandidateEvidence]:
    """Compare SFI descriptions with independent token and character-trigram overlap.

    Parameters
    ----------
    pair
        Filtered pair containing authoritative final SFI descriptions.

    Returns
    -------
    list[LPCandidateEvidence]
        Nonempty lexical matches with concrete shared values and counts. No stemming,
        language-specific vocabulary, embeddings, or semantic interpretation is used.
    """

    first = pair.first_sfi.sfi.description
    second = pair.second_sfi.sfi.description
    references = _sfi_references(pair)
    return [
        *_overlap_evidence(
            evidence_type="sfi_text_token_overlap",
            first=frozenset(_word_tokens(first)),
            pair=pair,
            references=references,
            second=frozenset(_word_tokens(second)),
        ),
        *_overlap_evidence(
            evidence_type="sfi_text_trigram_overlap",
            first=_trigrams(first),
            pair=pair,
            references=references,
            second=_trigrams(second),
        ),
    ]


def _trigrams(text: str) -> frozenset[str]:
    """Extract character trigrams from normalized alphanumeric words.

    Parameters
    ----------
    text
        Authoritative description text.

    Returns
    -------
    frozenset[str]
        Unique trigrams without boundary whitespace; texts shorter than three
        characters supply no trigram evidence, but may still share word tokens.
    """

    normalized = " ".join(_word_tokens(text))
    return frozenset(
        normalized[index : index + 3]
        for index in range(len(normalized) - 2)
        if normalized[index : index + 3] == normalized[index : index + 3].strip()
    )


def _word_tokens(text: str) -> tuple[str, ...]:
    """Normalize Unicode compatibility forms and case into alphanumeric word tokens.

    Parameters
    ----------
    text
        Text or source code; normalization affects evidence only, never identity.

    Returns
    -------
    tuple[str, ...]
        Tokens in original text order with punctuation and underscores as separators.
    """

    return tuple(
        re.findall(
            pattern=r"[^\W_]+", string=unicodedata.normalize("NFKC", text).casefold()
        )
    )


def build_lp_evidence_extractor(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
) -> LPEvidenceExtractor:
    """Snapshot authoritative inputs and build a filtered evidence extractor.

    Parameters
    ----------
    as_lc_bundle
        Final passed, error-free AS+LC bundle with contained endpoints and provenance.
    doc_key
        Document identity checked against the authoritative framework by the filter.
    kg_config
        Validated curriculum configuration supplying participation and local order.

    Returns
    -------
    LPEvidenceExtractor
        On-demand feature evaluator with no pair-population processing or file I/O.

    Raises
    ------
    ValueError
        If upstream validation, document identity, containment, or coordinates fail.
    """

    bundle = as_lc_bundle.model_copy(deep=True)
    pair_filter = build_lp_pair_filter(
        as_lc_bundle=bundle, doc_key=doc_key, kg_config=kg_config
    )
    return LPEvidenceExtractor(
        _graph_index=build_lp_graph_index(bundle), _pair_filter=pair_filter
    )
