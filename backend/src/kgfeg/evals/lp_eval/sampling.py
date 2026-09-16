"""Discover and validate completed curriculum evidence for LP evaluation.

Discovery reads completion metadata. Snapshot validation reconstructs bounded requests
and reconciles persisted judgments, provenance and graph projections. Shared pure
artifact helpers and the read-only journal reader are used without invoking production
generation, recovery, writes or model calls. Frozen copies and selection manifests are
stored separately and checked against their original material before use.
"""

# Standard Library
import fcntl
import hashlib
import heapq
import json
import os
import re
import stat
import unicodedata

from bisect import bisect_right
from collections import Counter, deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from fractions import Fraction
from itertools import islice
from operator import itemgetter
from pathlib import Path
from random import Random
from typing import Any, Literal
from uuid import UUID, uuid4

# Third Party Library
from pydantic import (
    SerializationInfo,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    model_serializer,
)

# Package Library
from kgfeg.evals.lp_eval.prompts import (
    build_synthetic_controls,
    production_blind_payload,
    render_classification_prompt,
    render_critique_prompt,
)
from kgfeg.evals.lp_eval.schemas import (
    CurriculumSchedule,
    DiscoveredRun,
    DiscoveryAlias,
    DiscoveryInventory,
    DiscoverySkip,
    EvaluationPair,
    EvaluationSchedule,
    EvaluationSettings,
    FileFingerprint,
    FrozenArtifact,
    FrozenInputManifest,
    FrozenInputs,
    FrozenSnapshot,
    PairSamplePlan,
    ProductionEvidenceView,
    ProductionEvidenceViews,
    ProductionPair,
    ProductionPopulation,
    ResolvedEvaluationSettings,
    ResolvedJudgeSettings,
    SampleCell,
    SampledPair,
    ScheduledEvidence,
    ScheduledRequest,
    SnapshotArtifact,
    SyntheticControl,
    UpstreamEvidenceSource,
    UpstreamEvidenceView,
    ValidatedSnapshot,
)
from kgfeg.kgs.lc_export import _build_learning_component_nodes, _validate_merged_graph
from kgfeg.kgs.lp_admissibility import (
    LPCandidateFilter,
    LPPairAdmissibility,
    build_lp_pair_filter,
    build_lp_pair_id,
)
from kgfeg.kgs.lp_artifacts import (
    LPGenerationSummary,
    LPStandaloneArtifacts,
    LPUnresolvedItems,
    _reconciled_counts,
)
from kgfeg.kgs.lp_checkpoints import (
    LPGenerationCheckpoints,
    _LPCheckpoint,
    _LPFailure,
    content_hash,
    reconciled_response,
)
from kgfeg.kgs.lp_export import (
    _artifact_byte_hashes,
    _combined_counts,
    _merge_material,
    _validate_combined_material,
)
from kgfeg.kgs.lp_finalization import LPFinalClaim, LPFinalClaims, LPRelationships
from kgfeg.kgs.lp_index import LPGraphIndex, build_lp_graph_index
from kgfeg.kgs.lp_requests import (
    LPGenerationRequest,
    LPRequestPopulation,
    LPRequestSFI,
    _artifact_payloads,
    _bounded_text,
    _request_sfi,
    build_lp_generation_requests,
    build_lp_request_id,
    canonical_lp_json,
    lp_material_content_hash,
)
from kgfeg.kgs.lp_selection import LPSFIEligibility, build_lp_selection
from kgfeg.kgs.lp_validation import LPValidationReport
from kgfeg.kgs.lp_validation import _cycle_diagnostics as _lp_cycle_diagnostics
from kgfeg.kgs.lp_validation import _expected_relationship
from kgfeg.kgs.prompts import (
    build_lp_generation_prompt,
    validate_lp_generation_response,
)
from kgfeg.kgs.schemas import (
    AcademicStandardsKGBundle,
    AcademicStandardsLCKGBundle,
    AcademicStandardsLCLPKGBundle,
    LCGenerationSummary,
    LPGenerationResponse,
    LPGenerationValidationVerdict,
    Relationship,
)
from kgfeg.kgs.sfi_export import (
    _build_learning_commons_nodes,
    _build_learning_commons_relationships,
    _fingerprint_jsonable,
    _validate_graph_export,
)
from kgfeg.kgs.validators import verify_lp_generation_validation_integrity
from kgfeg.page_ir_extraction.validators import QualityError
from kgfeg.schemas import CreateKGConfig

_STAGE_FILES = {
    "draft": "lp_generation_draft_responses.jsonl",
    "verdict": "lp_generation_validation_verdicts.jsonl",
    "response": "lp_generation_responses.jsonl",
}
PRODUCTION_DIAGNOSTIC_TAGS = (
    "checker_correction",
    "same_rank",
    "different_valid_ranks",
    "missing_coordinate",
    "equal_normalized_sfi_text",
    "unequal_text_high_jaccard",
    "endpoint_without_lcs",
    "shared_exact_lc",
    "nonshared_lcs",
    "broadly_reused_lc",
    "unresolved_self_or_ancestry",
    "production_evidence_truncation",
)
PRODUCTION_OUTCOMES = ("buildsTowards", "relatesTo", "no_relation", "needs_review")
UPSTREAM_DIAGNOSTIC_TAGS = (
    "multi_parent_dag_context",
    *PRODUCTION_DIAGNOSTIC_TAGS[1:-1],
    "reconstructed_evidence_truncation",
)


class _CapturedKGConfig(CreateKGConfig):
    """Interpret a captured configuration without serializing later operational defaults."""

    @model_serializer(mode="wrap")
    def _serialize_captured(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> dict[str, Any]:
        """Preserve the original configuration's capacity-field presence.

        Parameters
        ----------
        handler
            Ordinary schema serialization.
        info
            Requested alias and exclusion settings.

        Returns
        -------
        dict[str, Any]
            Configuration in its captured historical shape.
        """

        material = handler(self)
        key = "lp" if info.by_alias is not False else "learning_progressions"

        if "max_concurrent_requests" not in self.learning_progressions.model_fields_set:
            material.get(key, {}).pop("max_concurrent_requests", None)

        return material


@dataclass(frozen=True, slots=True)
class _EndpointFeatures:
    """Compact upstream facts used only for diagnostic population membership."""

    broadly_reused_lc: bool
    lc_uuids: frozenset[UUID]
    multi_parent_context: bool
    normalized_text: str
    rank: int | None
    reconstructed_truncation: bool
    tokens: frozenset[str]
    unresolved_ancestry: bool


@dataclass(frozen=True, slots=True)
class _ProductionRequestBinding:
    """Original bounded batch identity and its pair's endpoint binding."""

    endpoint_uuids: tuple[UUID, UUID]
    request_content_hash: str
    request_id: UUID
    truncated: bool


@dataclass
class _SampleReservoir:
    """Bounded uniform reservoir over a canonical stream, isolated by route.

    Attributes
    ----------
    count
        Number of matching pairs considered, including discarded pairs.
    pairs
        Current without-replacement sample, bounded by the target.
    random
        Private seeded random stream for this route alone.
    target
        Number of desired new draws; zero for an already-satisfied supplement.
    """

    count: int
    pairs: list[EvaluationPair]
    random: Random
    target: int

    def offer(self, pair: EvaluationPair) -> None:
        """Consider one unique canonical pair with uniform replacement probability.

        Parameters
        ----------
        pair
            Next pair from the canonical, duplicate-free population stream.
        """

        self.count += 1

        if len(self.pairs) < self.target:
            self.pairs.append(pair)
        elif self.target:
            position = self.random.randrange(self.count)

            if position < self.target:
                self.pairs[position] = pair


@dataclass(frozen=True, slots=True)
class _ScheduleContext:
    """Invocation-wide immutable identity for individual scheduled requests."""

    implementation_hash: str
    judge: ResolvedJudgeSettings
    settings: EvaluationSettings


class _SnapshotReader:
    """Read stable input bytes once and retain exact path bindings for later freezing."""

    def __init__(self, root: Path) -> None:
        """Initialize a read-only run material collection.

        Parameters
        ----------
        root
            Resolved run directory.
        """

        self.root = root
        self.artifacts: dict[str, SnapshotArtifact] = {}
        self.absent: set[str] = set()

    def check_hashes(self, hashes: dict[str, str]) -> None:
        """Resolve recorded artifact digests against actual contained run files.

        Parameters
        ----------
        hashes
            Recorded basename-to-SHA-256 mapping.

        Raises
        ------
        ValueError
            A name escapes the run, a file is missing or its digest differs.
        """

        for name, expected in hashes.items():
            if Path(name).name != name or name in {"", ".", ".."}:
                raise ValueError(f"Invalid artifact binding name: {name!r}")

            self.read(name)

            if self.artifacts[name].fingerprint.sha256 != expected:
                raise ValueError(f"Artifact byte hash differs: {name}")

    def check_unchanged(self) -> None:
        """Recheck captured bytes and optional absences before returning evidence.

        Raises
        ------
        ValueError
            A material file changed or a previously absent artifact appeared.
        """

        for name, artifact in self.artifacts.items():
            raw, fingerprint = _read_snapshot_bytes(
                boundary=self.root.parent, path=self.root / name
            )

            if raw != artifact.payload or fingerprint != artifact.fingerprint:
                raise ValueError(f"Snapshot material changed: {name}")

        for name in self.absent:
            if os.path.lexists(self.root / name):
                raise ValueError(f"Snapshot absence changed: {name}")

    def optional(self, name: str) -> Any:
        """Read optional evidence while retaining absence as part of the input state.

        Parameters
        ----------
        name
            Optional artifact basename.

        Returns
        -------
        Any
            Decoded content, or None when absent.

        Raises
        ------
        ValueError
            Present evidence is invalid.
        """

        if not os.path.lexists(self.root / name):
            self.absent.add(name)
            return None

        return self.read(name)

    def read(self, name: str) -> Any:
        """Read strict JSON or JSONL without modifying the input directory.

        Parameters
        ----------
        name
            Run-relative artifact path, including a contained source-document path.

        Returns
        -------
        Any
            Strictly decoded material.

        Raises
        ------
        ValueError
            Evidence is unavailable, unstable or malformed.
        """

        if name not in self.artifacts:
            raw, fingerprint = _read_snapshot_bytes(
                boundary=self.root.parent, path=self.root / name
            )
            self.artifacts[name] = SnapshotArtifact(
                fingerprint=fingerprint, name=name, payload=raw
            )

        raw = self.artifacts[name].payload

        if name.endswith(".jsonl"):
            return [_decode_snapshot_json(line) for line in raw.splitlines()]

        return _decode_snapshot_json(raw)


class LPDiscoveryError(ValueError):
    """Invalid or unavailable discovery evidence.

    Attributes
    ----------
    inventory
        Completed discovery inventory for a no-candidate failure, when available.
    """

    def __init__(
        self, *, inventory: DiscoveryInventory | None = None, message: str
    ) -> None:
        """Retain diagnostic inventory without representing it as valid input.

        Parameters
        ----------
        inventory
            Discovery results available before failure.
        message
            Path-specific failure or no-completed-candidate explanation.
        """

        super().__init__(message)
        self.inventory = inventory


class LPSnapshotError(ValueError):
    """A selected completed run has unavailable, changed or inconsistent evidence."""


@dataclass(frozen=True, slots=True)
class ProductionEvidenceBuilder:
    """Construct two separate views from immutable original production material.

    Attributes
    ----------
    _claims_json
        Canonical final claims by pair; drafts and corrections stay outside blind input.
    _input_content_hash
        Exact original artifact and captured configuration identity.
    _policy_json
        Captured curriculum semantics and instructions.
    _requests_json
        Original bounded request records by request UUID.
    """

    _claims_json: dict[str, str]
    _input_content_hash: str
    _policy_json: str
    _requests_json: dict[UUID, str]

    def build_pair(self, pair: EvaluationPair) -> ProductionEvidenceViews:
        """Freeze blind facts and original-production critique evidence separately.

        Both payloads can be prepared before execution. A later execution dependency
        must require a validated, frozen blind response before critique dispatch. This
        constructor accepts no evaluator answer and cannot feed it to the critic.

        Parameters
        ----------
        pair
            Selected canonical production pair.

        Returns
        -------
        ProductionEvidenceViews
            Separate material-bound views and hidden original correction diagnostics.

        Raises
        ------
        ValueError
            If original pair, batch, policy or prompt bindings disagree.
        """

        if pair.pair_id not in self._claims_json:
            raise ValueError("Selected pair has no original production claim.")

        claim = LPFinalClaim.model_validate_json(self._claims_json[pair.pair_id])

        if pair.endpoint_uuids != (
            claim.judgment.first_sfi_uuid,
            claim.judgment.second_sfi_uuid,
        ):
            raise ValueError("Selected pair endpoints differ from its original claim.")

        request = LPGenerationRequest.model_validate_json(
            self._requests_json[claim.provenance.request_id]
        )
        policy = json.loads(self._policy_json)
        blind, mapping = production_blind_payload(
            policy=policy, request=request, target_pair_id=pair.pair_id
        )
        prompt_material = _production_prompt_material(
            builder=self, claim=claim, request=request
        )
        critique = {
            "original_request": request.model_dump(mode="json"),
            "original_producer_system_message": prompt_material["producer_system"],
            "original_checker_system_message": prompt_material["checker_system"],
            "operative_judgment": claim.judgment.model_dump(mode="json"),
            "target_pair_id": pair.pair_id,
        }
        common_audit = {
            "original_request_content_hash": request.request_content_hash,
            "original_request_id": str(request.request_id),
            "producer_prompt_content_hash": claim.provenance.producer_prompt_content_hash,
            "checker_prompt_content_hash": claim.provenance.checker_prompt_content_hash,
        }
        return ProductionEvidenceViews(
            blind=_production_freeze_view(
                audit={**common_audit, "field_mapping": mapping},
                builder=self,
                pair=pair,
                payload=blind,
                request=request,
                view="production_blind",
            ),
            correction_audit_json=canonical_lp_json(
                {
                    "checker_outcome": claim.provenance.checker_outcome,
                    "operative_judgment": claim.judgment.model_dump(mode="json"),
                    "producer_judgment": claim.provenance.producer_judgment.model_dump(
                        mode="json"
                    ),
                    "provenance": claim.provenance.model_dump(mode="json"),
                }
            ),
            critique=_production_freeze_view(
                audit={
                    **common_audit,
                    "field_mapping": [
                        {
                            "source_path": "/",
                            "target_path": "/original_request",
                            "action": "original_request_fields_unchanged",
                        },
                        {
                            "source_path": "/claim/judgment",
                            "target_path": "/operative_judgment",
                            "action": "operative_final_judgment",
                        },
                    ],
                },
                builder=self,
                pair=pair,
                payload=critique,
                request=request,
                view="original_production_critique",
            ),
        )


@dataclass(frozen=True, slots=True)
class UpstreamEvidenceBuilder:
    """Construct bounded pair views from a private upstream and policy snapshot.

    Use build_upstream_evidence_builder to validate and isolate the inputs. No
    production candidates, requests, judgments or nomination state are accessible.
    """

    _config: _CapturedKGConfig
    _framework_title: str
    _graph_index: LPGraphIndex
    _input_content_hash: str
    _pair_filter: LPCandidateFilter
    _records: dict[UUID, LPSFIEligibility]
    _source: UpstreamEvidenceSource

    def build_pair(
        self,
        *,
        condition: Literal[
            "reconstructed_bounded_upstream",
            "expanded_upstream",
            "lc_removed",
            "hierarchy_removed",
        ] = "reconstructed_bounded_upstream",
        first_sfi_uuid: UUID | str,
        second_sfi_uuid: UUID | str,
    ) -> UpstreamEvidenceView:
        """Freeze one pair's evidence under an explicitly identified condition.

        Parameters
        ----------
        condition
            Common base, doubled upstream limits, or removal of one evidence family.
        first_sfi_uuid
            One final eligible SFI within this framework.
        second_sfi_uuid
            The other endpoint; presentation order does not affect construction.

        Returns
        -------
        UpstreamEvidenceView
            Canonical evidence bytes, contained references and a separate audit.

        Raises
        ------
        ValueError
            If a condition, endpoint, permission or evidence bound is invalid.
        """

        if condition not in {
            "reconstructed_bounded_upstream",
            "expanded_upstream",
            "lc_removed",
            "hierarchy_removed",
        }:
            raise ValueError("Unsupported upstream evidence condition.")

        pair = self._pair_filter.filter_pair(
            first_sfi_uuid=first_sfi_uuid, second_sfi_uuid=second_sfi_uuid
        )

        if pair is None:
            raise ValueError("Upstream evidence requires a distinct admissible pair.")

        base = _upstream_pair_payload(builder=self, expanded=False, pair=pair)
        payload = base
        removals: list[dict[str, Any]] = []

        if condition == "expanded_upstream":
            payload = _upstream_pair_payload(builder=self, expanded=True, pair=pair)
        elif condition == "lc_removed" or condition == "hierarchy_removed":
            payload, removals = _upstream_remove(condition=condition, payload=base)

        limits = self._config.learning_progressions.evidence_limits.model_dump()
        effective_limits = {
            key: value * (2 if condition == "expanded_upstream" else 1)
            for key, value in limits.items()
        }
        audit = {
            "base_omissions": _upstream_omissions(value=base),
            "base_payload_sha256": _upstream_payload_hash(base),
            "base_unavailable_evidence": _upstream_unavailable(base),
            "changed_paths": _upstream_changed_paths(base=base, value=payload),
            "effective_limits": effective_limits,
            "omissions": _upstream_omissions(value=payload),
            "production_limits": limits,
            "removals": removals,
            "selection_order": {
                "ancestors_and_learning_components": "canonical_uuid",
                "paths": "canonical_uuid_depth_first",
                "source_values": "preserved_source_text_first_then_origin_key",
                "text": "unicode_character_prefix",
            },
            "unchanged_from_base": payload == base,
            "unavailable_evidence": _upstream_unavailable(payload),
        }
        return _upstream_freeze_view(
            audit=audit, builder=self, condition=condition, pair=pair, payload=payload
        )


@dataclass(frozen=True, slots=True)
class AdmissiblePairPopulation:
    """Exact canonical pair indexing without retaining a quadratic pair table.

    Type, rank and endpoint permissions determine compatible groups. Row cumulative
    counts support direct uniform-sample indexing; diagnostic scans stream pairs in
    canonical UUID order. No production nomination or judgment material is consumed.
    """

    _builder: UpstreamEvidenceBuilder
    _endpoint_groups: dict[UUID, int]
    _features: dict[UUID, _EndpointFeatures]
    _groups: tuple[tuple[UUID, ...], ...]
    _row_ends: tuple[int, ...]
    doc_key: str
    endpoint_uuids: tuple[UUID, ...]
    framework_uuid: UUID
    input_content_hash: str
    material_content_hash: str

    def iter_pairs(self) -> Iterator[EvaluationPair]:
        """Stream the complete population in canonical endpoint order.

        Yields
        ------
        EvaluationPair
            Distinct admissible pairs without an all-pairs in-memory table.
        """

        for first in self.endpoint_uuids:
            allowed = _population_compatible_groups(
                builder=self._builder, first=first, groups=self._groups
            )
            seconds = heapq.merge(
                *(
                    islice(group, bisect_right(group, first), None)
                    for group in (self._groups[index] for index in allowed)
                )
            )

            for second in seconds:
                yield _evaluation_pair(doc_key=self.doc_key, first=first, second=second)

    def pair_at(self, index: int) -> EvaluationPair:
        """Resolve a zero-based index in the exact canonical pair population.

        Parameters
        ----------
        index
            Integer between zero and total_pairs minus one.

        Returns
        -------
        EvaluationPair
            The same pair as canonical streaming at this index.

        Raises
        ------
        ValueError
            If the index is not an integer inside the population.
        """

        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < self.total_pairs
        ):
            raise ValueError("Pair index must be an integer inside the population.")

        row = bisect_right(self._row_ends, index)
        offset = index - (self._row_ends[row - 1] if row else 0)
        first = self.endpoint_uuids[row]
        allowed = _population_compatible_groups(
            builder=self._builder, first=first, groups=self._groups
        )
        starts = tuple(bisect_right(self._groups[cell], first) for cell in allowed)
        low, high = row + 1, len(self.endpoint_uuids) - 1

        while low < high:
            middle = (low + high) // 2
            count = sum(
                bisect_right(self._groups[cell], self.endpoint_uuids[middle]) - start
                for cell, start in zip(allowed, starts)
            )

            if count > offset:
                high = middle
            else:
                low = middle + 1

        return _evaluation_pair(
            doc_key=self.doc_key, first=first, second=self.endpoint_uuids[low]
        )

    def pair_for_endpoints(
        self, *, first_sfi_uuid: UUID | str, second_sfi_uuid: UUID | str
    ) -> EvaluationPair:
        """Require a distinct admitted pair without consulting production metadata.

        Parameters
        ----------
        first_sfi_uuid
            One endpoint from this framework.
        second_sfi_uuid
            The other endpoint, in either encounter order.

        Returns
        -------
        EvaluationPair
            Canonical pair identity.

        Raises
        ------
        ValueError
            If either endpoint is malformed, unknown, excluded or incompatible.
        """

        first, second = sorted((UUID(str(first_sfi_uuid)), UUID(str(second_sfi_uuid))))

        if first == second or any(
            item not in self._endpoint_groups for item in (first, second)
        ):
            raise ValueError("Pair endpoints must be distinct eligible framework SFIs.")

        if not self._builder._pair_filter._admissible_decisions(
            first=self._builder._records[first], second=self._builder._records[second]
        ):
            raise ValueError("Pair is excluded by the captured curriculum policy.")

        return _evaluation_pair(doc_key=self.doc_key, first=first, second=second)

    def tags_for_pair(self, pair: EvaluationPair) -> tuple[str, ...]:
        """Return all upstream tags in their fixed diagnostic order.

        Parameters
        ----------
        pair
            Canonical identity belonging to this population.

        Returns
        -------
        tuple[str, ...]
            Upstream-only tags, never production nomination or semantic labels.

        Raises
        ------
        ValueError
            If the pair is noncanonical, excluded or bound to different input identity.
        """

        expected = self.pair_for_endpoints(
            first_sfi_uuid=pair.endpoint_uuids[0],
            second_sfi_uuid=pair.endpoint_uuids[1],
        )

        if pair != expected:
            raise ValueError("Pair identity does not match this population.")

        first, second = (self._features[item] for item in pair.endpoint_uuids)
        return _upstream_pair_tags(first=first, second=second)

    @property
    def total_pairs(self) -> int:
        """Return the exact uniform-cohort denominator.

        Returns
        -------
        int
            Total distinct admissible pairs, including low-signal pairs.
        """

        return self._row_ends[-1] if self._row_ends else 0


def _bounded_endpoint_truncated(endpoint: LPRequestSFI) -> bool:
    """Inspect typed evidence bounds without interpreting arbitrary audit metadata.

    Parameters
    ----------
    endpoint
        Original or reconstructed bounded endpoint.

    Returns
    -------
    bool
        Whether any shown text, source, LC, coordinate-origin or ancestor depth was
        limited by its evidence bound.
    """

    contexts = (endpoint.context, *endpoint.ancestors)
    texts = [
        *endpoint.coordinate.source_fields,
        *(
            text
            for context in contexts
            for text in (
                context.description,
                context.statement_code,
                context.alternate_statement_code,
            )
            if text is not None
        ),
        *(
            text
            for component in endpoint.learning_components
            for text in (component.description, component.metadata)
        ),
        *(
            text
            for item in endpoint.source_evidence
            for text in (item.excerpt, item.reference)
        ),
    ]
    return any(
        (
            endpoint.omitted_learning_component_count,
            endpoint.omitted_source_evidence_count,
            endpoint.coordinate.omitted_source_field_count,
            any(path.depth_truncated for path in endpoint.ancestor_paths),
            any(text.truncated for text in texts),
        )
    )


def _bounded_request_truncated(request: LPGenerationRequest) -> bool:
    """Inspect every typed truncation signal in the original bounded request batch.

    Parameters
    ----------
    request
        Unchanged original production request.

    Returns
    -------
    bool
        Whether the framework, any endpoint or any nomination context was truncated.
    """

    return (
        request.framework_title.truncated
        or any(_bounded_endpoint_truncated(endpoint) for endpoint in request.sfis)
        or any(
            pair.nomination.evidence.truncated or pair.nomination.references_truncated
            for pair in request.pairs
        )
    )


def _check_checkpoint_row(
    *,
    material: dict[str, Any],
    prerequisite: str,
    prompt_hash: str,
    request_index: int,
    row: _LPCheckpoint,
    stage: str,
) -> None:
    """Verify a stage's payload and exact request, prompt and prerequisite bindings.

    Parameters
    ----------
    material
        Captured execution material.
    prerequisite
        Expected prior-stage or bounded-request digest.
    prompt_hash
        Digest of the original stage messages.
    request_index
        Position in the original complete request schedule.
    row
        Parsed persisted checkpoint.
    stage
        Expected producer, checker or reconciled stage.

    Raises
    ------
    ValueError
        Any checkpoint binding differs.
    """

    if (
        row.execution_content_hash != content_hash(material)
        or row.payload_content_hash != content_hash(row.payload)
        or row.prerequisite_content_hash != prerequisite
        or row.prompt_content_hash != prompt_hash
        or row.request_index != request_index
        or row.stage != stage
    ):
        raise ValueError(f"Checkpoint material differs: {stage}[{request_index}]")


def _checkpoint_claims(
    *,
    claims: LPFinalClaims,
    population: LPRequestPopulation,
    reader: _SnapshotReader,
    rows: dict[str, list[_LPCheckpoint]],
) -> None:
    """Reconcile each final claim with its original request and both model stages.

    Parameters
    ----------
    claims
        Complete self-consistent claim artifact.
    population
        Upstream-reconstructed bounded requests and candidates.
    reader
        Captured run artifacts.
    rows
        Persisted stage rows with complete ordered coverage.

    Raises
    ------
    ValueError
        Selected responses, provenance or material dependencies differ.
    """

    material = reader.read("lp_generation_checkpoint_manifest.json")["material"]
    expected_claims = []
    candidates = {row.pair_id: row for row in population.candidates.candidates}

    for index, request in enumerate(population.requests):
        draft_row, verdict_row, response_row = (
            rows[stage][index] for stage in ("draft", "verdict", "response")
        )
        draft = LPGenerationResponse.model_validate(draft_row.payload)
        verdict = LPGenerationValidationVerdict.model_validate(verdict_row.payload)
        verify_lp_generation_validation_integrity(
            draft_response=draft,
            lp_generation_request=request,
            validation_verdict=verdict,
        )
        response = reconciled_response(draft=draft, verdict=verdict)
        _equal(
            actual=response_row.payload,
            expected=response.model_dump(mode="json"),
            label=f"Reconciled response {index}",
        )
        producer_prompt = build_lp_generation_prompt(
            lp_generation_request=request,
            producer_instructions=material["producer_instructions"],
        )
        checker_prompt = validate_lp_generation_response(
            checker_instructions=material["checker_instructions"],
            draft_response=draft,
            lp_generation_request=request,
            producer_instructions=material["producer_instructions"],
        )

        for stage, row, prerequisite, prompt in (
            ("draft", draft_row, request.request_content_hash, producer_prompt),
            (
                "verdict",
                verdict_row,
                content_hash(draft_row.model_dump(mode="json")),
                checker_prompt,
            ),
            (
                "response",
                response_row,
                content_hash(verdict_row.model_dump(mode="json")),
                checker_prompt,
            ),
        ):
            _check_checkpoint_row(
                material=material,
                prerequisite=prerequisite,
                prompt_hash=content_hash(
                    {
                        "system_message": prompt.system_message,
                        "user_message": prompt.user_message,
                    }
                ),
                request_index=index,
                row=row,
                stage=stage,
            )

        producers = {judgment.pair_id: judgment for judgment in draft.judgments}
        judgments = {judgment.pair_id: judgment for judgment in response.judgments}

        for pair in request.pairs:
            judgment = judgments[pair.pair_id]
            source, target = _judgment_endpoints(judgment.model_dump(mode="json"))
            expected_claims.append(
                {
                    "candidate": candidates[pair.pair_id].model_dump(mode="json"),
                    "judgment": judgment.model_dump(mode="json"),
                    "source_sfi_uuid": source,
                    "target_sfi_uuid": target,
                    "provenance": {
                        "checker_checkpoint_content_hash": content_hash(
                            verdict_row.model_dump(mode="json")
                        ),
                        "checker_outcome": (
                            "accepted" if verdict.passed else "corrected"
                        ),
                        "checker_prompt_content_hash": verdict_row.prompt_content_hash,
                        "checker_verdict_content_hash": verdict_row.payload_content_hash,
                        "execution_content_hash": content_hash(material),
                        "judgment_content_hash": content_hash(
                            judgment.model_dump(mode="json")
                        ),
                        "producer_checkpoint_content_hash": content_hash(
                            draft_row.model_dump(mode="json")
                        ),
                        "producer_judgment": producers[pair.pair_id].model_dump(
                            mode="json"
                        ),
                        "producer_prompt_content_hash": draft_row.prompt_content_hash,
                        "producer_response_content_hash": draft_row.payload_content_hash,
                        "request_content_hash": request.request_content_hash,
                        "request_id": str(request.request_id),
                        "response_checkpoint_content_hash": content_hash(
                            response_row.model_dump(mode="json")
                        ),
                        "response_content_hash": response_row.payload_content_hash,
                    },
                }
            )

    _equal(
        actual=[claim.model_dump(mode="json") for claim in claims.claims],
        expected=expected_claims,
        label="Final claim reconciliation",
    )


def _classify_run(*, kgs_directory: Path, results_root: Path) -> DiscoveredRun:
    """Read one run's status and completed-candidate identity claims.

    Parameters
    ----------
    kgs_directory
        Resolved directory containing the execution metadata.
    results_root
        Containment boundary for all metadata reads.

    Returns
    -------
    DiscoveredRun
        Preliminary status without graph, checkpoint or snapshot certification.

    Raises
    ------
    LPDiscoveryError
        Invalid or unavailable discovery evidence.
    ValueError
        Invalid metadata or contradictory status/timestamp fields.
    """

    metadata, fingerprint = _read_metadata(
        path=kgs_directory / "kg_run.json", results_root=results_root
    )

    try:
        run_id = UUID(_required_text(key="run_id", record=metadata))
        started_at = _timestamp(metadata.get("started_at"))
        completed_at = (
            None
            if metadata.get("completed_at") is None
            else _timestamp(metadata["completed_at"])
        )
        classification, reason = _completion_status(
            completed_at=completed_at,
            extra=metadata.get("extra"),
            started_at=started_at,
        )
    except (TypeError, ValueError) as exc:
        raise LPDiscoveryError(
            message=f"Invalid completion metadata in {fingerprint.path}: {exc}"
        ) from exc

    doc_key = None
    framework_uuid = None
    fingerprints = [fingerprint]

    if classification == "completed_candidate":
        manifest, manifest_fingerprint = _read_metadata(
            path=kgs_directory / "kg_run_manifest.json", results_root=results_root
        )
        requests, requests_fingerprint = _read_metadata(
            path=kgs_directory / "lp_generation_requests_manifest.json",
            results_root=results_root,
        )

        try:
            doc_key = _required_text(key="doc_key", record=manifest)

            if doc_key != _required_text(key="doc_key", record=requests):
                raise ValueError("KG and LP request manifests disagree on doc_key")

            framework_uuid = UUID(_required_text(key="framework_uuid", record=requests))
        except (TypeError, ValueError) as exc:
            raise LPDiscoveryError(
                message=f"Invalid completed-candidate identity in {kgs_directory}: {exc}"
            ) from exc

        fingerprints.extend([manifest_fingerprint, requests_fingerprint])

    return DiscoveredRun(
        aliases=(),
        completed_at=completed_at,
        doc_key=doc_key,
        fingerprints=tuple(fingerprints),
        framework_uuid=framework_uuid,
        kgs_directory=kgs_directory,
        reason=reason,
        run_id=run_id,
        started_at=started_at,
        status=classification,
    )


def _completion_status(
    *, completed_at: datetime | None, extra: Any, started_at: datetime
) -> tuple[Literal["completed_candidate", "failed", "unfinished"], str]:
    """Classify only internally consistent execution status and timestamps.

    Parameters
    ----------
    completed_at
        Optional recorded completion timestamp.
    extra
        Parsed execution metadata containing the terminal status.
    started_at
        Recorded execution start.

    Returns
    -------
    tuple[Literal["completed_candidate", "failed", "unfinished"], str]
        Preliminary state and its inclusion or exclusion explanation.

    Raises
    ------
    ValueError
        Status metadata is malformed or contradicts the recorded timestamps.
    """

    if not isinstance(extra, dict):
        raise ValueError("extra must be a JSON object")

    if completed_at is not None and completed_at < started_at:
        raise ValueError("completion precedes execution start")

    status = extra.get("status")

    if status is None and completed_at is None:
        return "unfinished", "Execution started without a recorded terminal status."

    if status == "error" and completed_at is not None:
        return "failed", "Execution recorded a terminal error."

    if status == "success" and completed_at is not None:
        return (
            "completed_candidate",
            "Recorded success; full snapshot validation is still required.",
        )

    raise ValueError("unrecognized or contradictory status/timestamp fields")


def _decode_snapshot_json(payload: bytes) -> Any:
    """Decode finite JSON with unique object keys.

    Parameters
    ----------
    payload
        Complete UTF-8 JSON bytes.

    Returns
    -------
    Any
        Decoded material.

    Raises
    ------
    ValueError
        JSON is malformed, contains duplicate keys or nonfinite values.
    """

    return json.loads(
        payload, object_pairs_hook=_metadata_object, parse_constant=_nonfinite_number
    )


def _equal(*, actual: Any, expected: Any, label: str) -> None:
    """Reject material differences without dumping potentially large source records.

    Parameters
    ----------
    actual
        Observed material.
    expected
        Independently derived required material.
    label
        Artifact or binding name for a concise failure.

    Raises
    ------
    ValueError
        Material differs.
    """

    if actual != expected:
        raise ValueError(f"{label} differs from its source material")


def _evaluation_artifact(*, name: str, snapshot: ValidatedSnapshot) -> SnapshotArtifact:
    """Require exactly one unchanged captured artifact without reading a live file.

    Parameters
    ----------
    name
        Required original artifact name.
    snapshot
        Copy-backed validated snapshot.

    Returns
    -------
    SnapshotArtifact
        Exact retained bytes.

    Raises
    ------
    ValueError
        If the artifact is absent, duplicated or inconsistent with its fingerprint.
    """

    matches = [artifact for artifact in snapshot.artifacts if artifact.name == name]

    if len(matches) != 1:
        raise ValueError(f"Evaluation requires exactly one artifact: {name}.")

    artifact = matches[0]

    if (
        len(artifact.payload) != artifact.fingerprint.size_bytes
        or hashlib.sha256(artifact.payload).hexdigest() != artifact.fingerprint.sha256
    ):
        raise ValueError(f"Evaluation artifact bytes changed: {name}.")

    return artifact


def _evaluation_pair(*, doc_key: str, first: UUID, second: UUID) -> EvaluationPair:
    """Bind canonical endpoints to their document-scoped identity.

    Parameters
    ----------
    doc_key
        Validated document identity.
    first
        Lower canonical endpoint.
    second
        Higher canonical endpoint.

    Returns
    -------
    EvaluationPair
        Pair identity without evidence or production labels.
    """

    return EvaluationPair(
        endpoint_uuids=(first, second),
        pair_id=build_lp_pair_id(
            doc_key=doc_key, first_sfi_uuid=first, second_sfi_uuid=second
        ),
    )


def _frozen_artifact_layout(*, artifact: FrozenArtifact, run: DiscoveredRun) -> None:
    """Check a material name and its original path without resolving output names.

    Parameters
    ----------
    artifact
        Original name and fingerprint.
    run
        Owning completed run.

    Raises
    ------
    ValueError
        Artifact identity or containment is invalid.
    """

    name = Path(artifact.name)
    fingerprint = artifact.fingerprint

    if name.is_absolute() or name.as_posix() != artifact.name:
        raise ValueError("Frozen artifact name must be a canonical relative path")

    original = Path(os.path.abspath(run.kgs_directory / name))

    if not original.is_relative_to(run.kgs_directory.parent):
        raise ValueError("Frozen artifact name escapes its run container")

    if not fingerprint.path.is_absolute() or not fingerprint.path.is_relative_to(
        run.kgs_directory.parent
    ):
        raise ValueError("Frozen original path escapes its run container")

    if len(fingerprint.sha256) != 64 or any(
        char not in "0123456789abcdef" for char in fingerprint.sha256
    ):
        raise ValueError("Invalid frozen SHA-256")

    if isinstance(fingerprint.size_bytes, bool) or fingerprint.size_bytes < 0:
        raise ValueError("Invalid frozen artifact length")


def _frozen_bytes(manifest: FrozenInputManifest) -> bytes:
    """Serialize the complete input selection with stable ordering and finite JSON.

    Parameters
    ----------
    manifest
        Fixed discovery and validated material bindings.

    Returns
    -------
    bytes
        Canonical UTF-8 manifest, including its terminal newline.
    """

    return (
        canonical_lp_json(
            TypeAdapter(FrozenInputManifest).dump_python(manifest, mode="json")
        )
        + "\n"
    ).encode("utf-8")


def _frozen_copy_read(*, artifact: FrozenArtifact, directory: Path) -> bytes:
    """Read a separate stored copy and validate its actual length, digest and inode.

    Parameters
    ----------
    artifact
        Expected original byte identity.
    directory
        Frozen input directory containing SHA-256-named copies.

    Returns
    -------
    bytes
        Exact stored input payload.

    Raises
    ------
    ValueError
        A copy is aliased, linked to another file or fails its byte identity.
    """

    path = directory / artifact.fingerprint.sha256

    if path.resolve(strict=True) != path or path.stat().st_nlink != 1:
        raise ValueError(f"Frozen copy is aliased or hard-linked: {path}")

    raw, observed = _read_snapshot_bytes(boundary=directory, path=path)
    _equal(
        actual=(observed.sha256, observed.size_bytes),
        expected=(artifact.fingerprint.sha256, artifact.fingerprint.size_bytes),
        label=f"Frozen copy {artifact.name}",
    )
    return raw


@contextmanager
def _frozen_directory(path: Path) -> Iterator[int]:
    """Hold an output directory descriptor and detect directory substitution.

    Parameters
    ----------
    path
        Existing physical output directory.

    Yields
    ------
    int
        Directory descriptor for relative output operations.

    Raises
    ------
    ValueError
        The directory changes identity or is aliased.
    """

    if path.resolve(strict=True) != path:
        raise ValueError("Frozen directory is aliased")

    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    try:
        before = os.fstat(descriptor)
        yield descriptor
        after = path.stat()

        if (before.st_dev, before.st_ino) != (
            after.st_dev,
            after.st_ino,
        ) or path.resolve(strict=True) != path:
            raise ValueError("Frozen directory changed identity")
    finally:
        os.close(descriptor)


def _frozen_layout(manifest: FrozenInputManifest) -> None:
    """Check unique selection, safe material locations and manifest coverage.

    Parameters
    ----------
    manifest
        Parsed frozen selection.

    Raises
    ------
    ValueError
        Identities repeat, material names escape their run container, or metadata does
        not reconcile with the selected inventory.
    """

    selected = tuple(
        run for run in manifest.inventory.runs if run.status == "completed_candidate"
    )

    if (
        not selected
        or tuple(snapshot.run for snapshot in manifest.snapshots) != selected
    ):
        raise ValueError("Frozen inputs do not exactly cover selected completed runs")

    identities: dict[tuple[str, str], Path] = {}

    for snapshot in manifest.snapshots:
        _register_run_identity(identities=identities, run=snapshot.run)
        _frozen_snapshot_layout(snapshot)

    _frozen_output_boundary(
        inventory=manifest.inventory, output=manifest.inventory.evaluation_root
    )


def _frozen_manifest(reference: FrozenInputs) -> FrozenInputManifest:
    """Read a manifest only at its pinned path and exact byte identity.

    Parameters
    ----------
    reference
        Expected manifest path and digest, retained by the consuming invocation.

    Returns
    -------
    FrozenInputManifest
        Canonically parsed, structurally reconciled fixed selection.

    Raises
    ------
    ValueError
        Manifest bytes, shape, location or identity differ.
    """

    path = reference.manifest_path

    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("Frozen manifest path must be absolute and unaliased")

    raw, fingerprint = _read_snapshot_bytes(boundary=path.parent, path=path)
    _equal(
        actual=fingerprint.sha256,
        expected=reference.content_hash,
        label="Frozen manifest hash",
    )
    manifest = TypeAdapter(FrozenInputManifest).validate_python(
        _decode_snapshot_json(raw)
    )
    _equal(
        actual=raw,
        expected=_frozen_bytes(manifest),
        label="Frozen manifest schema round trip",
    )
    _frozen_layout(manifest)
    _equal(
        actual=path,
        expected=manifest.inventory.evaluation_root
        / "inputs"
        / reference.content_hash
        / "manifest.json",
        label="Frozen manifest location",
    )
    expected = {
        "manifest.json",
        *(
            artifact.fingerprint.sha256
            for snapshot in manifest.snapshots
            for artifact in snapshot.artifacts
        ),
    }
    _equal(
        actual={entry.name for entry in path.parent.iterdir()},
        expected=expected,
        label="Frozen directory contents",
    )
    return manifest


def _frozen_output_boundary(*, inventory: DiscoveryInventory, output: Path) -> None:
    """Reject output aliases or overlap with any discovered curriculum run container.

    Parameters
    ----------
    inventory
        Original discovery paths, including unfinished runs.
    output
        Repository evaluation output root.

    Raises
    ------
    ValueError
        Output is aliased or overlaps a run's source/material container.
    """

    if not output.is_absolute() or output.resolve(strict=False) != output:
        raise ValueError("Evaluation output must be an absolute unaliased directory")

    for run in inventory.runs:
        container = run.kgs_directory.parent

        if output.is_relative_to(container) or container.is_relative_to(output):
            raise ValueError(
                f"Evaluation output overlaps curriculum inputs: {container}"
            )


def _frozen_publish(*, directory: Path, payload: bytes) -> None:
    """Publish the completed manifest atomically without replacing prior evidence.

    Parameters
    ----------
    directory
        Evaluator-owned directory whose copies have already passed verification.
    payload
        Canonical complete manifest.
    """

    name = f".manifest-pending-{uuid4().hex}"
    _frozen_write(directory=directory, name=name, payload=payload)

    with _frozen_directory(directory) as descriptor:
        os.link(
            name,
            "manifest.json",
            src_dir_fd=descriptor,
            dst_dir_fd=descriptor,
            follow_symlinks=False,
        )
        os.unlink(name, dir_fd=descriptor)
        os.fsync(descriptor)


def _frozen_snapshot_layout(snapshot: FrozenSnapshot) -> None:
    """Require original metadata and safe, unique artifact/absence bindings.

    Parameters
    ----------
    snapshot
        One curriculum's fixed material index.

    Raises
    ------
    ValueError
        Names, digests, source paths, metadata or absences are inconsistent.
    """

    names = tuple(artifact.name for artifact in snapshot.artifacts)

    if not names or names != tuple(sorted(set(names))):
        raise ValueError("Frozen artifact names must be nonempty, sorted and unique")

    if snapshot.absent_artifacts != tuple(sorted(set(snapshot.absent_artifacts))):
        raise ValueError("Frozen absence names must be sorted and unique")

    if (
        set(names).intersection(snapshot.absent_artifacts)
        or snapshot.source_artifact not in names
    ):
        raise ValueError("Frozen source or absence coverage is inconsistent")

    if snapshot.checkpoint_format not in {"historical_prefix", "journal_bearing"}:
        raise ValueError("Unsupported frozen checkpoint interpretation")

    for artifact in snapshot.artifacts:
        _frozen_artifact_layout(artifact=artifact, run=snapshot.run)

    for name in snapshot.absent_artifacts:
        if Path(name).name != name or name in {"", ".", ".."}:
            raise ValueError("Invalid frozen absence name")

    fingerprints = {
        artifact.fingerprint.path: artifact.fingerprint
        for artifact in snapshot.artifacts
    }

    for fingerprint in snapshot.run.fingerprints:
        _equal(
            actual=fingerprints.get(fingerprint.path),
            expected=fingerprint,
            label="Frozen discovery metadata",
        )

    config = _decode_snapshot_json(snapshot.config_json.encode("utf-8"))
    _equal(
        actual=canonical_lp_json(config),
        expected=snapshot.config_json,
        label="Frozen configuration encoding",
    )


def _frozen_source_check(snapshot: FrozenSnapshot) -> None:
    """Verify original selected material remains complete, stable and byte-identical.

    Parameters
    ----------
    snapshot
        Pinned original run, material and absence identities.

    Raises
    ------
    ValueError
        Original material or completion metadata changed.
    """

    reader = _SnapshotReader(snapshot.run.kgs_directory)

    with _snapshot_ownership(reader):
        current = _classify_run(
            kgs_directory=snapshot.run.kgs_directory,
            results_root=snapshot.run.kgs_directory,
        )
        _equal(
            actual=replace(current, aliases=snapshot.run.aliases),
            expected=snapshot.run,
            label="Frozen source completion",
        )

        for artifact in snapshot.artifacts:
            _, observed = _read_snapshot_bytes(
                boundary=snapshot.run.kgs_directory.parent,
                path=snapshot.run.kgs_directory / artifact.name,
            )
            _equal(
                actual=observed,
                expected=artifact.fingerprint,
                label=f"Frozen original {artifact.name}",
            )

        for name in snapshot.absent_artifacts:
            if os.path.lexists(snapshot.run.kgs_directory / name):
                raise ValueError(f"Previously absent source artifact appeared: {name}")

        reader.check_unchanged()


def _frozen_store_copies(
    *, directory: Path, snapshots: tuple[ValidatedSnapshot, ...]
) -> None:
    """Copy validated payloads without source hard links, verifying each saved object.

    Parameters
    ----------
    directory
        Newly created evaluator input directory.
    snapshots
        Fully validated source bytes.
    """

    written: set[str] = set()

    for snapshot in snapshots:
        for artifact in snapshot.artifacts:
            digest = artifact.fingerprint.sha256
            _equal(
                actual=(
                    hashlib.sha256(artifact.payload).hexdigest(),
                    len(artifact.payload),
                ),
                expected=(digest, artifact.fingerprint.size_bytes),
                label="Captured snapshot payload",
            )

            if digest not in written:
                _frozen_write(
                    directory=directory, name=digest, payload=artifact.payload
                )
                written.add(digest)

            _frozen_copy_read(
                artifact=FrozenArtifact(
                    fingerprint=artifact.fingerprint, name=artifact.name
                ),
                directory=directory,
            )


def _frozen_write(*, directory: Path, name: str, payload: bytes) -> None:
    """Write one new evaluator-owned file durably without following aliases.

    Parameters
    ----------
    directory
        Existing unaliased evaluator directory.
    name
        Safe generated basename.
    payload
        Complete bytes to preserve.

    Raises
    ------
    ValueError
        The output path is unsafe or its directory changes.
    """

    if directory.resolve(strict=True) != directory or Path(name).name != name:
        raise ValueError("Unsafe frozen output location")

    with _frozen_directory(directory) as descriptor:
        file_descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=descriptor,
        )

        with os.fdopen(file_descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o444)

        os.fsync(descriptor)


def _is_run_directory(*, evaluation_root: Path, path: Path, resolved: Path) -> bool:
    """Recognize run boundaries while preventing nested evaluator outputs.

    Parameters
    ----------
    evaluation_root
        Resolved evaluator output subtree.
    path
        Discovered directory path.
    resolved
        Physical directory path.

    Returns
    -------
    bool
        Whether the discovered or resolved directory is named kgs.

    Raises
    ------
    ValueError
        A recognized production run contains the evaluator output location.
    """

    if resolved.name != "kgs" and path.name != "kgs":
        return False

    if evaluation_root.is_relative_to(resolved):
        raise ValueError("evaluator output lies inside a production run")

    return True


def _judgment_endpoints(judgment: dict[str, Any]) -> tuple[str | None, str | None]:
    """Resolve publishing endpoints from a structurally valid final judgment.

    Parameters
    ----------
    judgment
        Validated judgment with canonical pair order.

    Returns
    -------
    tuple[str | None, str | None]
        Directed or canonical symmetric endpoints; absent for nonpublishing outcomes.
    """

    if judgment["decision"] in {"no_relation", "needs_review"}:
        return None, None

    first, second = judgment["first_sfi_uuid"], judgment["second_sfi_uuid"]

    if judgment["direction"] == "second_to_first":
        return second, first

    return first, second


def _metadata_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate object keys instead of accepting a last-value overwrite.

    Parameters
    ----------
    pairs
        JSON object members in their encoded order.

    Returns
    -------
    dict[str, Any]
        Object retaining each key exactly once.

    Raises
    ------
    ValueError
        Duplicate key encountered in the JSON object.
    """

    result: dict[str, Any] = {}

    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")

        result[key] = value

    return result


def _nonfinite_number(value: str) -> None:
    """Reject non-JSON numeric constants in completion metadata.

    Parameters
    ----------
    value
        Nonfinite constant encountered by the JSON decoder.

    Raises
    ------
    ValueError
        Always, because these constants are not valid JSON.
    """

    raise ValueError(f"invalid JSON numeric constant: {value}")


def _path_exclusion(
    *, evaluation_root: Path, path: Path, resolved: Path, results_root: Path
) -> DiscoverySkip | None:
    """Identify subtrees outside the permitted discovery population.

    Parameters
    ----------
    evaluation_root
        Resolved output subtree excluded from discovery.
    path
        Encountered path retained in the exclusion record.
    resolved
        Physical path used for containment checks.
    results_root
        Resolved input discovery root.

    Returns
    -------
    DiscoverySkip | None
        An explicit exclusion, or None when traversal is permitted.
    """

    if not resolved.is_relative_to(results_root):
        return DiscoverySkip(path=path, reason="outside_results_root")

    if resolved.is_relative_to(evaluation_root):
        return DiscoverySkip(path=path, reason="evaluation_output")

    return None


def _population_compatible_groups(
    *,
    builder: UpstreamEvidenceBuilder,
    first: UUID,
    groups: tuple[tuple[UUID, ...], ...],
) -> tuple[int, ...]:
    """Find compatible groups for one row using the authoritative permission rule.

    Group members share type, rank and relation participation. Evaluate one
    representative without copying provenance or retaining a group-pair matrix.
    Distinctness and canonical orientation are enforced by the row's UUID suffix.

    Parameters
    ----------
    builder
        Validated upstream and policy indexes.
    first
        Canonical first endpoint for the row.
    groups
        Canonical members of identical-permission groups.

    Returns
    -------
    tuple[int, ...]
        Compatible group indices for this row only.
    """

    return tuple(
        index
        for index, group in enumerate(groups)
        if builder._pair_filter._admissible_decisions(
            first=builder._records[first], second=builder._records[group[0]]
        )
    )


def _population_features(
    *, builder: UpstreamEvidenceBuilder, records: tuple[LPSFIEligibility, ...]
) -> dict[UUID, _EndpointFeatures]:
    """Derive compact diagnostic facts from full authoritative upstream material.

    Parameters
    ----------
    builder
        Upstream-only graph, policy and evidence constructor.
    records
        Eligible endpoints in canonical order.

    Returns
    -------
    dict[UUID, _EndpointFeatures]
        Features with LC reuse counted only over eligible SFIs.

    Raises
    ------
    ValueError
        If mandatory reconstructed evidence cannot fit its captured bounds.
    """

    graph = builder._graph_index
    eligible = {record.sfi.case_identifier_uuid for record in records}
    broad_lcs = {
        identifier
        for identifier, sfis in graph.sfis_by_learning_component_uuid.items()
        if len({sfi.case_identifier_uuid for sfi in sfis} & eligible) >= 10
    }
    multi_parent = {
        identifier
        for identifier, parents in graph.parent_sfi_uuids_by_sfi_uuid.items()
        if len(parents) > 1
    }
    pending = deque(multi_parent)

    while pending:
        current = pending.popleft()

        for child in graph.child_sfi_uuids_by_parent_sfi_uuid[current]:
            if child not in multi_parent:
                multi_parent.add(child)
                pending.append(child)

    maximum = (
        builder._config.learning_progressions.evidence_limits.max_source_evidence_characters_per_sfi
    )
    title_truncated = len(builder._framework_title) > maximum
    features: dict[UUID, _EndpointFeatures] = {}

    for record in records:
        identifier = record.sfi.case_identifier_uuid
        context = _request_sfi(
            graph_index=graph,
            kg_config=builder._config,
            records=builder._records,
            sfi_uuid=identifier,
        )
        lc_ids = frozenset(
            component.identifier
            for component in graph.learning_components_by_sfi_uuid[identifier]
        )
        features[identifier] = _EndpointFeatures(
            broadly_reused_lc=bool(lc_ids & broad_lcs),
            lc_uuids=lc_ids,
            multi_parent_context=identifier in multi_parent,
            normalized_text=normalize_evaluation_text(record.sfi.description),
            rank=record.coordinate.rank,
            reconstructed_truncation=title_truncated
            or _bounded_endpoint_truncated(context),
            tokens=evaluation_token_set(record.sfi.description),
            unresolved_ancestry=record.unresolved_ancestry,
        )

    return features


def _production_freeze_view(
    *,
    audit: dict[str, Any],
    builder: ProductionEvidenceBuilder,
    pair: EvaluationPair,
    payload: dict[str, Any],
    request: LPGenerationRequest,
    view: Literal["production_blind", "original_production_critique"],
) -> ProductionEvidenceView:
    """Bind original evidence, separate audit and actual rendered view bytes.

    Parameters
    ----------
    audit
        Non-prompt construction and source mapping.
    builder
        Original material owner.
    pair
        Canonical assessed pair.
    payload
        Judge-visible view, without correction or sampling audit.
    request
        Original bounded batch.
    view
        Distinct classification or critique condition.

    Returns
    -------
    ProductionEvidenceView
        Immutable JSON and hashes with contained evidence references.
    """

    payload_json = canonical_lp_json(payload)

    # A rationale cannot cite itself as evidence that its factual claims are grounded.
    evidence_references = (
        _upstream_references(
            path="/original_request", value=payload["original_request"]
        )
        if view == "original_production_critique"
        else _upstream_references(path="", value=payload)
    )
    result = ProductionEvidenceView(
        audit_json=canonical_lp_json(audit),
        endpoint_uuids=pair.endpoint_uuids,
        input_content_hash=builder._input_content_hash,
        material_content_hash="",
        pair_id=pair.pair_id,
        payload_json=payload_json,
        payload_sha256=hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
        references=tuple(evidence_references),
        request_content_hash=request.request_content_hash,
        request_id=request.request_id,
        view=view,
    )
    material = TypeAdapter(ProductionEvidenceView).dump_python(result, mode="json")
    del material["material_content_hash"]
    return replace(result, material_content_hash=lp_material_content_hash(material))


def _production_pair(
    *,
    claim: LPFinalClaim,
    population: AdmissiblePairPopulation,
    publication: UUID | None,
    request: _ProductionRequestBinding,
) -> ProductionPair:
    """Build production comparison metadata with separately scoped diagnostic tags.

    Parameters
    ----------
    claim
        Validated original final claim and producer/checker provenance.
    population
        Independent upstream population, used only to derive shared feature tags.
    publication
        Matching actual published edge, if any.
    request
        Original batch evidence binding.

    Returns
    -------
    ProductionPair
        Production outcome and change audit, never an independent truth label.

    Raises
    ------
    ValueError
        If the claim, request, pair or outcome does not reconcile.
    """

    judgment = claim.judgment
    pair = population.pair_for_endpoints(
        first_sfi_uuid=judgment.first_sfi_uuid,
        second_sfi_uuid=judgment.second_sfi_uuid,
    )

    if (
        pair.pair_id != judgment.pair_id
        or pair.endpoint_uuids != request.endpoint_uuids
        or claim.provenance.request_id != request.request_id
        or claim.provenance.request_content_hash != request.request_content_hash
        or (publication is not None) != (judgment.decision in PRODUCTION_OUTCOMES[:2])
    ):
        raise ValueError("Production pair does not reconcile with request/publication.")

    tags = set(population.tags_for_pair(pair)) & set(PRODUCTION_DIAGNOSTIC_TAGS[1:-1])

    if claim.provenance.checker_outcome == "corrected":
        tags.add("checker_correction")

    if request.truncated:
        tags.add("production_evidence_truncation")

    producer = claim.provenance.producer_judgment.model_dump(mode="json")
    final = judgment.model_dump(mode="json")
    return ProductionPair(
        checker_outcome=claim.provenance.checker_outcome,
        direction=judgment.direction,
        outcome=judgment.decision,
        pair=pair,
        producer_direction=claim.provenance.producer_judgment.direction,
        producer_outcome=claim.provenance.producer_judgment.decision,
        producer_to_final_changes=tuple(
            field for field in sorted(final) if producer[field] != final[field]
        ),
        published_relationship_uuid=publication,
        request_content_hash=request.request_content_hash,
        request_id=request.request_id,
        tags=tuple(tag for tag in PRODUCTION_DIAGNOSTIC_TAGS if tag in tags),
    )


def _production_prompt_material(
    *,
    builder: ProductionEvidenceBuilder,
    claim: LPFinalClaim,
    request: LPGenerationRequest,
) -> dict[str, str]:
    """Recover original policy messages only when both recorded prompt hashes match.

    This uses the already validated compatible renderer and original bounded request.
    It neither invokes production generation nor reconstructs evidence from a graph.

    Parameters
    ----------
    builder
        Original claim/request owner.
    claim
        Assessed claim with recorded prompt bindings.
    request
        Exact original batch.

    Returns
    -------
    dict[str, str]
        Original producer/checker system messages for the critique evidence.

    Raises
    ------
    ValueError
        If compatible rendering does not reproduce the original prompt identities.
    """

    policy = json.loads(builder._policy_json)
    draft = LPGenerationResponse(
        judgments=[
            LPFinalClaim.model_validate_json(
                builder._claims_json[pair.pair_id]
            ).provenance.producer_judgment
            for pair in request.pairs
        ],
        request_content_hash=request.request_content_hash,
        request_id=request.request_id,
    )
    producer = build_lp_generation_prompt(
        lp_generation_request=request,
        producer_instructions=policy["producer_instructions"],
    )
    checker = validate_lp_generation_response(
        checker_instructions=policy["checker_instructions"],
        draft_response=draft,
        lp_generation_request=request,
        producer_instructions=policy["producer_instructions"],
    )

    for prompt, expected in (
        (producer, claim.provenance.producer_prompt_content_hash),
        (checker, claim.provenance.checker_prompt_content_hash),
    ):
        _equal(
            actual=content_hash(
                {
                    "system_message": prompt.system_message,
                    "user_message": prompt.user_message,
                }
            ),
            expected=expected,
            label="Original production prompt",
        )

    return {
        "producer_system": producer.system_message,
        "checker_system": checker.system_message,
    }


def _production_publications(
    *, artifacts: tuple[SnapshotArtifact, ...], claims: LPFinalClaims
) -> dict[str, UUID]:
    """Reconcile actual published edges with positive final claims.

    Parameters
    ----------
    artifacts
        Captured standalone relationship projections in outcome order.
    claims
        Complete original claims.

    Returns
    -------
    dict[str, UUID]
        One real relationship UUID per positive pair.

    Raises
    ------
    ValueError
        If publication identity, endpoints, provenance or coverage disagrees.
    """

    expected = {
        claim.judgment.pair_id: claim
        for claim in claims.claims
        if claim.judgment.decision in PRODUCTION_OUTCOMES[:2]
    }
    result: dict[str, UUID] = {}
    identifiers: set[UUID] = set()

    for artifact, outcome in zip(artifacts, PRODUCTION_OUTCOMES[:2]):
        for line in artifact.payload.splitlines():
            edge = Relationship.model_validate(_decode_snapshot_json(line))
            raw_claim = edge.metadata.get("claim")

            if not isinstance(raw_claim, dict):
                raise ValueError("Published edge is missing its original claim.")

            claim = LPFinalClaim.model_validate(raw_claim)
            pair_id = claim.judgment.pair_id

            if (
                pair_id not in expected
                or claim != expected[pair_id]
                or pair_id in result
                or edge.identifier in identifiers
                or edge.relationship_type != outcome
                or claim.judgment.decision != outcome
                or edge.source_entity_value != str(claim.source_sfi_uuid)
                or edge.target_entity_value != str(claim.target_sfi_uuid)
            ):
                raise ValueError("Published relationship disagrees with final claims.")

            result[pair_id] = edge.identifier
            identifiers.add(edge.identifier)

    if set(result) != set(expected):
        raise ValueError("Published relationship coverage is incomplete.")

    return result


def _production_requests(
    *, artifact: SnapshotArtifact, claims: LPFinalClaims
) -> dict[str, _ProductionRequestBinding]:
    """Read original request/batch truncation without reconstructing production
    evidence.

    Parameters
    ----------
    artifact
        Exact original bounded request JSONL.
    claims
        Original claim manifest defining complete request and pair coverage.

    Returns
    -------
    dict[str, _ProductionRequestBinding]
        Pair bindings; truncation applies to the complete original bounded batch.

    Raises
    ------
    ValueError
        If original requests have duplicate pairs or mismatched manifest coverage.
    """

    result: dict[str, _ProductionRequestBinding] = {}
    request_ids: list[UUID] = []

    for line in artifact.payload.splitlines():
        request = LPGenerationRequest.model_validate(_decode_snapshot_json(line))
        request_ids.append(request.request_id)
        truncated = _bounded_request_truncated(request)

        for pair in request.pairs:
            if pair.pair_id in result:
                raise ValueError("Original request population repeats a pair.")

            result[pair.pair_id] = _ProductionRequestBinding(
                endpoint_uuids=(pair.first_sfi_uuid, pair.second_sfi_uuid),
                request_content_hash=request.request_content_hash,
                request_id=request.request_id,
                truncated=truncated,
            )

    if (
        tuple(request_ids) != claims.request_manifest.request_ids
        or tuple(result) != claims.request_manifest.pair_ids
    ):
        raise ValueError("Original request population disagrees with its manifest.")

    return result


def _read_metadata(
    *, path: Path, results_root: Path
) -> tuple[dict[str, Any], FileFingerprint]:
    """Read a stable regular JSON file contained inside the selected root.

    Parameters
    ----------
    path
        Required metadata file.
    results_root
        Resolved starting directory constraining file access.

    Returns
    -------
    tuple[dict[str, Any], FileFingerprint]
        Parsed object and identity of the actual bytes read.

    Raises
    ------
    LPDiscoveryError
        Invalid or unavailable discovery evidence.
    ValueError
        Metadata is not a regular file, resolves outside the root or changes while
        being read.
    """

    try:
        resolved = path.resolve(strict=True)

        if not resolved.is_relative_to(results_root):
            raise ValueError("metadata resolves outside the starting directory")

        before = resolved.stat()

        if not stat.S_ISREG(before.st_mode):
            raise ValueError("metadata is not a regular file")

        with resolved.open(mode="rb") as stream:
            opened = os.fstat(stream.fileno())
            raw = stream.read()
            after = os.fstat(stream.fileno())

        if (
            not (
                _stat_identity(before)
                == _stat_identity(opened)
                == _stat_identity(after)
                == _stat_identity(resolved.stat())
            )
            or path.resolve(strict=True) != resolved
        ):
            raise ValueError("metadata changed while being read")

        decoded = json.loads(
            object_pairs_hook=_metadata_object, parse_constant=_nonfinite_number, s=raw
        )

        if not isinstance(decoded, dict):
            raise ValueError("metadata must be a JSON object")
    except (OSError, RuntimeError, ValueError) as exc:
        raise LPDiscoveryError(
            message=f"Cannot inspect required metadata {path}: {exc}"
        ) from exc

    return decoded, FileFingerprint(
        path=resolved, sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw)
    )


def _read_snapshot_bytes(
    *, boundary: Path, path: Path
) -> tuple[bytes, FileFingerprint]:
    """Capture stable regular-file bytes inside the physical run/source boundary.

    Parameters
    ----------
    boundary
        Physical directory containing the run and its source-document artifacts.
    path
        Required input path.

    Returns
    -------
    tuple[bytes, FileFingerprint]
        Exact payload and resolved path/byte identity.

    Raises
    ------
    ValueError
        The file is missing, escapes containment or changes during reading.
    """

    resolved = path.resolve(strict=True)
    before = resolved.stat()

    if not resolved.is_relative_to(boundary) or not stat.S_ISREG(before.st_mode):
        raise ValueError(f"Input must be a contained regular file: {path}")

    with resolved.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read()
        after = os.fstat(stream.fileno())

    if (
        len({_stat_identity(item) for item in (before, opened, after, resolved.stat())})
        != 1
        or path.resolve(strict=True) != resolved
    ):
        raise ValueError(f"Input changed while reading: {path}")

    return raw, FileFingerprint(
        path=resolved, sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw)
    )


def _register_run_identity(
    *, identities: dict[tuple[str, str], Path], run: DiscoveredRun
) -> None:
    """Register completed identity claims without merging different snapshots.

    Parameters
    ----------
    identities
        Invocation-local index of previously observed identity claims.
    run
        Classified run whose completed identity claims should be registered.

    Raises
    ------
    LPDiscoveryError
        A completed candidate lacks identity or conflicts with a previous directory.
    """

    if run.status != "completed_candidate":
        return

    directory = run.kgs_directory

    if run.doc_key is None or run.framework_uuid is None:
        raise LPDiscoveryError(
            message=f"Completed candidate lacks identity: {directory}"
        )

    for identity in (
        ("doc_key", run.doc_key),
        ("framework_uuid", str(run.framework_uuid)),
    ):
        if identity in identities:
            raise LPDiscoveryError(
                message=(
                    f"Conflicting completed snapshots: {identities[identity]} and "
                    f"{directory} claim the same identity {identity}."
                )
            )

        identities[identity] = directory


def _required_text(*, key: str, record: dict[str, Any]) -> str:
    """Read an exact nonblank text field without normalizing its identity.

    Parameters
    ----------
    key
        Required object member.
    record
        Parsed metadata object.

    Returns
    -------
    str
        Exact recorded text.

    Raises
    ------
    ValueError
        Required key is missing, not a string or contains surrounding whitespace.
    """

    value = record.get(key)

    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{key} must be nonblank text without surrounding whitespace")

    return value


def _sample_cell(
    *,
    population_count: int,
    prior: tuple[EvaluationPair, ...] = (),
    reservoir: _SampleReservoir,
    route: str,
    target: int,
    uniform: bool = False,
) -> SampleCell:
    """Freeze cell accounting, distinguishing new draws from supplement credit.

    Parameters
    ----------
    population_count
        Complete matching population before any sampling.
    prior
        Already selected matching pairs credited toward a supplement.
    reservoir
        Bounded new draws.
    route
        Stable component-qualified route.
    target
        Configured quota before supplement credit.
    uniform
        Whether this cell supports an exact uniform inclusion probability.

    Returns
    -------
    SampleCell
        Canonical draw and membership identities and honest exhausted shortfall.
    """

    drawn = sorted(reservoir.pairs, key=lambda pair: pair.endpoint_uuids)
    selected = sorted((*prior, *drawn), key=lambda pair: pair.endpoint_uuids)
    probability = Fraction(len(drawn), population_count) if population_count else None
    return SampleCell(
        drawn_pair_ids=tuple(pair.pair_id for pair in drawn),
        inclusion_probability=(
            (probability.numerator, probability.denominator)
            if uniform and probability is not None
            else None
        ),
        population_count=population_count,
        route=route,
        selected_pair_ids=tuple(pair.pair_id for pair in selected),
        shortfall=max(0, target - len(selected)),
        target=target,
    )


def _sample_plan(
    *,
    base_evidence: tuple[UpstreamEvidenceView, ...],
    cells: tuple[SampleCell, ...],
    component: Literal["independent", "production"],
    doc_key: str,
    framework_uuid: UUID,
    input_content_hash: str,
    pairs: tuple[SampledPair, ...],
    population_content_hash: str,
    settings: EvaluationSettings,
) -> PairSamplePlan:
    """Bind canonical selections, routes, settings and evidence into one identity.

    Parameters
    ----------
    base_evidence
        Common upstream evidence, or an empty tuple for production selection.
    cells
        Ordered cell accounting with selection routes and shortfalls.
    component
        Assessment component; prevents cross-component deduplication.
    doc_key
        Validated document identity.
    framework_uuid
        Shared endpoint framework.
    input_content_hash
        Exact upstream material identity.
    pairs
        Canonical selected pairs with every route retained.
    population_content_hash
        Complete source population identity.
    settings
        Validated effective controls, including the seed.

    Returns
    -------
    PairSamplePlan
        Immutable plan with a content hash covering all of its remaining fields.
    """

    plan = PairSamplePlan(
        algorithm="canonical-reservoir-v1/sha256-route-seed/python-mt19937",
        base_evidence=base_evidence,
        cells=cells,
        component=component,
        doc_key=doc_key,
        framework_uuid=framework_uuid,
        input_content_hash=input_content_hash,
        material_content_hash="",
        pairs=pairs,
        population_content_hash=population_content_hash,
        settings_json=canonical_lp_json(settings.model_dump(mode="json")),
    )
    material = TypeAdapter(PairSamplePlan).dump_python(plan, mode="json")
    del material["material_content_hash"]
    return replace(plan, material_content_hash=lp_material_content_hash(material))


def _sample_reservoir(
    *, doc_key: str, framework_uuid: UUID, route: str, seed: int, target: int
) -> _SampleReservoir:
    """Create an isolated deterministic random stream for one sampling cell.

    Parameters
    ----------
    doc_key
        Validated input document identity.
    framework_uuid
        Framework containing the source population.
    route
        Component-qualified outcome or tag route.
    seed
        Explicit integer sampling seed.
    target
        Number of desired new draws.

    Returns
    -------
    _SampleReservoir
        Empty bounded reservoir seeded from canonical identity, route and seed.
    """

    identity = canonical_lp_json(
        {
            "doc_key": doc_key,
            "framework_uuid": str(framework_uuid),
            "route": route,
            "seed": seed,
        }
    )
    digest = hashlib.sha256(identity.encode("utf-8")).digest()
    return _SampleReservoir(
        count=0, pairs=[], random=Random(int.from_bytes(digest, "big")), target=target
    )


def _sample_routes(*, cells: tuple[SampleCell, ...], pair_id: str) -> tuple[str, ...]:
    """Return all ordered cell memberships for a deduplicated pair.

    Parameters
    ----------
    cells
        Frozen cells in processing order.
    pair_id
        Pair whose memberships are requested.

    Returns
    -------
    tuple[str, ...]
        Every route that selected or credited the pair.
    """

    return tuple(cell.route for cell in cells if pair_id in cell.selected_pair_ids)


def _schedule_call(
    *,
    canonical_endpoints: tuple[UUID, UUID],
    component: Literal["production", "independent", "controls"],
    context: _ScheduleContext,
    dependencies: tuple[str, ...] = (),
    evidence: ScheduledEvidence,
    replicate: int,
    role: Literal["base", "identical_repeat", "variant", "critique", "control"],
) -> ScheduledRequest:
    """Bind one exact request to its condition, replicate, model and implementation.

    Parameters
    ----------
    canonical_endpoints
        Comparison orientation independent of displayed order.
    component
        Required assessment component.
    context
        Effective invocation settings and actual implementation identity.
    dependencies
        Blind results which must freeze before dispatch.
    evidence
        Complete evidence/condition/presentation payload.
    replicate
        One-based replicate index.
    role
        Scheduled purpose, kept outside judge evidence.

    Returns
    -------
    ScheduledRequest
        Material-bound prompt and dependency record.

    Raises
    ------
    ValueError
        If the evidence cannot be rendered under the required response schema.
    """

    request_id = lp_material_content_hash(
        {
            "canonical_endpoint_uuids": [str(item) for item in canonical_endpoints],
            "component": component,
            "evidence_content_hash": evidence.material_content_hash,
            "implementation_hash": context.implementation_hash,
            "judge": TypeAdapter(ResolvedJudgeSettings).dump_python(
                context.judge, mode="json"
            ),
            "replicate": replicate,
            "role": role,
            "settings": context.settings.model_dump(mode="json"),
        }
    )
    renderer = (
        render_critique_prompt
        if evidence.view == "critique"
        else render_classification_prompt
    )
    prompt = renderer(evidence=evidence, request_id=request_id)
    result = ScheduledRequest(
        canonical_endpoint_uuids=canonical_endpoints,
        component=component,
        dependencies=dependencies,
        evidence=evidence,
        material_content_hash="",
        prompt=prompt,
        replicate=replicate,
        role=role,
    )
    material = TypeAdapter(ScheduledRequest).dump_python(result, mode="json")
    del material["material_content_hash"]
    return replace(result, material_content_hash=lp_material_content_hash(material))


def _schedule_cohort(
    *,
    bases: tuple[ProductionEvidenceView | UpstreamEvidenceView, ...],
    component: Literal["production", "independent"],
    context: _ScheduleContext,
    diagnostic: SampleCell,
    upstream: UpstreamEvidenceBuilder,
) -> tuple[ScheduledRequest, ...]:
    """Schedule every real base, identical repeat and preselected diagnostic variant.

    Parameters
    ----------
    bases
        All selected cohort base views in canonical pair order.
    component
        Real assessment component.
    context
        Resolved controls and identities.
    diagnostic
        Independently seeded within-cohort diagnostic selection.
    upstream
        Frozen upstream constructor for expanded/common evidence comparisons.

    Returns
    -------
    tuple[ScheduledRequest, ...]
        Complete calls, with no model-result-driven selection or sample shrinkage.

    Raises
    ------
    ValueError
        If required evidence cannot be constructed or rendered.
    """

    calls: list[ScheduledRequest] = []
    condition = (
        "original_production_blind"
        if component == "production"
        else "reconstructed_bounded_upstream"
    )

    for base in bases:
        evidence = _schedule_evidence(base=base, condition=condition)

        for replicate in range(1, context.settings.base_blind_replicates + 1):
            calls.append(
                _schedule_call(
                    canonical_endpoints=base.endpoint_uuids,
                    component=component,
                    context=context,
                    evidence=evidence,
                    replicate=replicate,
                    role="base",
                )
            )

        if base.pair_id not in diagnostic.selected_pair_ids:
            continue

        start = context.settings.base_blind_replicates + 1

        for replicate in range(
            start, start + context.settings.additional_diagnostic_replicates
        ):
            calls.append(
                _schedule_call(
                    canonical_endpoints=base.endpoint_uuids,
                    component=component,
                    context=context,
                    evidence=evidence,
                    replicate=replicate,
                    role="identical_repeat",
                )
            )

        for variant in _schedule_variants(
            base=base, condition=condition, upstream=upstream
        ):
            for replicate in range(1, context.settings.variant_replicates + 1):
                calls.append(
                    _schedule_call(
                        canonical_endpoints=base.endpoint_uuids,
                        component=component,
                        context=context,
                        evidence=variant,
                        replicate=replicate,
                        role="variant",
                    )
                )

    return tuple(calls)


def _schedule_curriculum(
    *, context: _ScheduleContext, snapshot: ValidatedSnapshot
) -> CurriculumSchedule:
    """Freeze independent selection/evidence before inspecting production outcomes.

    Parameters
    ----------
    context
        Effective settings and material identity.
    snapshot
        One complete validated frozen curriculum.

    Returns
    -------
    CurriculumSchedule
        Every component and required call, retaining all shortfalls.

    Raises
    ------
    ValueError
        If any selected population or required view fails validation.
    """

    source = upstream_evidence_source(snapshot)
    population = build_admissible_population(source)
    independent = sample_independent_pairs(
        population=population, settings=context.settings
    )
    independent_diagnostic = _schedule_diagnostic(
        plan=independent, settings=context.settings
    )

    # This is the first production metadata access after independent freezing.
    production_population = build_production_population(
        population=population, snapshot=snapshot
    )
    production = sample_production_pairs(
        population=production_population, settings=context.settings
    )
    production_diagnostic = _schedule_diagnostic(
        plan=production, settings=context.settings
    )
    builder = build_production_evidence_builder(snapshot)
    views = tuple(builder.build_pair(selected.pair) for selected in production.pairs)
    production_calls = _schedule_cohort(
        bases=tuple(view.blind for view in views),
        component="production",
        context=context,
        diagnostic=production_diagnostic,
        upstream=population._builder,
    )
    independent_calls = _schedule_cohort(
        bases=independent.base_evidence,
        component="independent",
        context=context,
        diagnostic=independent_diagnostic,
        upstream=population._builder,
    )
    critique_calls = []

    for view in views:
        dependencies = tuple(
            call.prompt.request_id
            for call in production_calls
            if call.evidence.pair_id == view.blind.pair_id
            and call.role in {"base", "identical_repeat"}
        )

        for replicate in range(1, context.settings.critique_replicates + 1):
            critique_calls.append(
                _schedule_call(
                    canonical_endpoints=view.critique.endpoint_uuids,
                    component="production",
                    context=context,
                    dependencies=dependencies,
                    evidence=_schedule_evidence(
                        base=view.critique, condition="original_production_critique"
                    ),
                    replicate=replicate,
                    role="critique",
                )
            )

    controls = build_synthetic_controls(settings=context.settings, source=source)
    control_calls = tuple(
        _schedule_call(
            canonical_endpoints=control.endpoint_uuids,
            component="controls",
            context=context,
            evidence=_schedule_evidence(base=control, condition=control.view),
            replicate=replicate,
            role="control",
        )
        for control in controls
        for replicate in range(1, context.settings.synthetic_control_replicates + 1)
    )
    return CurriculumSchedule(
        controls=controls,
        correction_audits=tuple(
            (view.blind.pair_id, view.correction_audit_json) for view in views
        ),
        diagnostic_cells=(production_diagnostic, independent_diagnostic),
        doc_key=source.doc_key,
        framework_uuid=source.framework_uuid,
        independent_sample=independent,
        production_population=production_population,
        production_sample=production,
        requests=production_calls
        + independent_calls
        + tuple(critique_calls)
        + control_calls,
    )


def _schedule_diagnostic(
    *, plan: PairSamplePlan, settings: EvaluationSettings
) -> SampleCell:
    """Select uniform diagnostic pairs within a frozen cohort without judge results.

    Parameters
    ----------
    plan
        Frozen canonical selected cohort.
    settings
        Invocation-wide count and seed.

    Returns
    -------
    SampleCell
        Exact inclusion fraction, selected identities and exhausted shortfall.
    """

    route = plan.component + "/repetition_and_evidence_diagnostics"
    reservoir = _sample_reservoir(
        doc_key=plan.doc_key,
        framework_uuid=plan.framework_uuid,
        route=route,
        seed=settings.sampling_seed,
        target=settings.diagnostic_pairs_per_cohort,
    )

    for selected in plan.pairs:
        reservoir.offer(selected.pair)

    return _sample_cell(
        population_count=len(plan.pairs),
        reservoir=reservoir,
        route=route,
        target=settings.diagnostic_pairs_per_cohort,
        uniform=True,
    )


def _schedule_evidence(
    *,
    base: ProductionEvidenceView | UpstreamEvidenceView | SyntheticControl,
    comparison_base: ProductionEvidenceView | UpstreamEvidenceView | None = None,
    condition: str,
    payload: dict[str, Any] | None = None,
    presentation: Literal[
        "canonical", "endpoint_swapped", "evidence_lists_reversed"
    ] = "canonical",
    removal_audit: list[dict[str, Any]] | None = None,
) -> ScheduledEvidence:
    """Freeze a condition/presentation without changing the underlying evidence owner.

    Parameters
    ----------
    base
        Source view whose audit and material identity remain available.
    comparison_base
        Original cohort base for an expanded comparison; omitted for other variants.
    condition
        Explicit evidence condition.
    payload
        Optional separately constructed removal payload.
    presentation
        Display-only transformation; original orientation-sensitive facts stay intact.
    removal_audit
        Recorded field removals for production evidence comparisons.

    Returns
    -------
    ScheduledEvidence
        Presented bytes, regenerated pointers and explicit change/omission audit.

    Raises
    ------
    ValueError
        If source bytes or presentation are inconsistent.
    """

    if hashlib.sha256(base.payload_json.encode()).hexdigest() != base.payload_sha256:
        raise ValueError("Schedule evidence differs from its source bytes.")

    original = json.loads(base.payload_json)
    comparison = (
        original
        if comparison_base is None
        else json.loads(comparison_base.payload_json)
    )
    shown = json.loads(canonical_lp_json(original if payload is None else payload))
    endpoints = base.endpoint_uuids
    presentation_changes: list[str] = []

    if presentation == "endpoint_swapped":
        shown["sfis"] = list(reversed(shown["sfis"]))
        endpoints = (endpoints[1], endpoints[0])
        presentation_changes.append("/sfis")
    elif presentation == "evidence_lists_reversed":
        _schedule_reverse_lists(changes=presentation_changes, path="", value=shown)
    elif presentation != "canonical":
        raise ValueError("Unsupported evidence presentation.")

    task: Literal["classification", "critique"] = (
        "critique"
        if isinstance(base, (ProductionEvidenceView, SyntheticControl))
        and base.view in {"original_production_critique", "synthetic_critique"}
        else "classification"
    )
    references = (
        _upstream_references(path="/original_request", value=shown["original_request"])
        if task == "critique"
        else _upstream_references(path="", value=shown)
    )
    audit = {
        "base_audit": (
            json.loads(base.audit_json)
            if not isinstance(base, SyntheticControl)
            else {}
        ),
        "canonical_endpoint_uuids": [str(item) for item in base.endpoint_uuids],
        "changed_paths": _upstream_changed_paths(base=original, value=shown),
        "comparison_base_content_hash": (
            base.material_content_hash
            if comparison_base is None
            else comparison_base.material_content_hash
        ),
        "comparison_changed_paths": _upstream_changed_paths(
            base=comparison, value=shown
        ),
        "comparison_unchanged": shown == comparison,
        "presentation_changed_lists": presentation_changes,
        "removals": removal_audit or [],
        "source_payload_sha256": base.payload_sha256,
        "unchanged_from_source": shown == original,
    }
    result = ScheduledEvidence(
        audit_json=canonical_lp_json(audit),
        condition=condition,
        endpoint_uuids=endpoints,
        material_content_hash="",
        pair_id=base.pair_id,
        payload_json=canonical_lp_json(shown),
        payload_sha256=_upstream_payload_hash(shown),
        presentation=presentation,
        references=tuple(references),
        source_content_hash=base.material_content_hash,
        view=task,
    )
    material = TypeAdapter(ScheduledEvidence).dump_python(result, mode="json")
    del material["material_content_hash"]
    return replace(result, material_content_hash=lp_material_content_hash(material))


def _schedule_implementation() -> tuple[FileFingerprint, ...]:
    """Capture actual package Python bytes without Git mutations or secret reads.

    Returns
    -------
    tuple[FileFingerprint, ...]
        Stable ordered source identities, including shared evidence/rendering helpers.

    Raises
    ------
    ValueError
        If a source file changes while its bytes are captured.
    """

    root = Path(__file__).resolve().parents[2]
    fingerprints = []

    for path in sorted(root.rglob("*.py")):
        # Report-only scoring changes do not invalidate identical judge requests.
        if path == root / "evals" / "lp_eval" / "scoring.py":
            continue

        before = path.stat()
        payload = path.read_bytes()
        after = path.stat()

        if _stat_identity(before) != _stat_identity(after):
            raise ValueError("Implementation changed during schedule preparation.")

        fingerprints.append(
            FileFingerprint(
                path=path,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
            )
        )

    return tuple(fingerprints)


def _schedule_nomination_removal(
    *, condition: Literal["lc_removed", "hierarchy_removed"], payload: dict[str, Any]
) -> list[dict[str, Any]]:
    """Remove derived nomination facts and identifiers for the selected evidence family.

    Parameters
    ----------
    condition
        Evidence family to remove.
    payload
        Fresh production blind payload with original bounded nomination projections.

    Returns
    -------
    list[dict[str, Any]]
        Removed field identities; incomplete unidentifiable records are also withheld.
    """

    forbidden = (
        {"shared_learning_components", "lc_text_token_overlap", "lc_tag_token_overlap"}
        if condition == "lc_removed"
        else {"hierarchy_context"}
    )
    removals: list[dict[str, Any]] = []

    for index, pair in enumerate(payload["pairs"]):
        nomination = pair["nomination_facts"]
        records: dict[str, str] = {}

        for fact in nomination["facts"]:
            if fact["field"].endswith("/evidence_type") and not fact["truncated"]:
                records[fact["field"].split("/")[1]] = json.loads(fact["json_excerpt"])

        keep = []

        for fact in nomination["facts"]:
            family = records.get(fact["field"].split("/")[1])

            if family is None or family in forbidden:
                removals.append(
                    {
                        "path": f"/pairs/{index}/nomination_facts" + fact["field"],
                        "content_hash": lp_material_content_hash(fact),
                        "reason": "selected_family_or_incomplete_family_identity",
                    }
                )
            else:
                keep.append(fact)

        nomination["facts"] = keep
        nomination["evidence_types"] = [
            family for family in nomination["evidence_types"] if family not in forbidden
        ]
        _upstream_drop_field(
            key="original_characters",
            parent=nomination,
            path=f"/pairs/{index}/nomination_facts",
            removals=removals,
        )

    return removals


def _schedule_production_removal(
    *,
    base: ProductionEvidenceView,
    condition: Literal["lc_removed", "hierarchy_removed"],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Ablate original bounded production evidence, including derived nomination facts.

    Parameters
    ----------
    base
        Frozen blind production view, never its original critique payload.
    condition
        Selected family.

    Returns
    -------
    tuple[dict[str, Any], list[dict[str, Any]]]
        Fresh comparison payload and explicit removals/collateral omissions.
    """

    payload = json.loads(base.payload_json)
    payload["pair"] = next(
        pair for pair in payload["pairs"] if pair["pair_id"] == base.pair_id
    )
    payload, removals = _upstream_remove(condition=condition, payload=payload)
    del payload["pair"]
    removals.extend(_schedule_nomination_removal(condition=condition, payload=payload))
    return payload, removals


def _schedule_reverse_fact(
    *, changes: list[str], path: str, value: dict[str, Any]
) -> None:
    """Reverse complete unordered nomination lists without completing partial excerpts.

    Parameters
    ----------
    changes
        Audit paths for actual reordered factual excerpts.
    path
        Current factual-record pointer.
    value
        Mutable nomination fact record or another payload object.
    """

    if value.get("truncated") is False and str(value.get("field", "")).rsplit("/", 1)[
        -1
    ] in {"references", "shared_values", "shared_ancestors"}:
        excerpt = json.loads(value["json_excerpt"])

        if isinstance(excerpt, list) and len(excerpt) > 1:
            value["json_excerpt"] = canonical_lp_json(list(reversed(excerpt)))
            changes.append(path + "/json_excerpt")


def _schedule_reverse_lists(*, changes: list[str], path: str, value: Any) -> None:
    """Reverse evidence container lists while preserving path steps and policy order.

    Parameters
    ----------
    changes
        Audit paths for nontrivial reversed lists.
    path
        Current payload pointer.
    value
        Mutable copy of shown evidence.
    """

    reversible = {
        "sfis",
        "ancestors",
        "ancestor_paths",
        "learning_components",
        "source_evidence",
        "parent_sfi_uuids",
        "facts",
        "warnings",
    }

    if isinstance(value, dict):
        _schedule_reverse_fact(changes=changes, path=path, value=value)

        for key, child in value.items():
            child_path = path + "/" + _upstream_pointer_token(key)

            if key in reversible and isinstance(child, list) and len(child) > 1:
                child.reverse()
                changes.append(child_path)

            if key not in {"policy", "audit_context", "metadata", "coordinate"}:
                _schedule_reverse_lists(changes=changes, path=child_path, value=child)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _schedule_reverse_lists(
                changes=changes, path=f"{path}/{index}", value=child
            )


def _schedule_variants(
    *,
    base: ProductionEvidenceView | UpstreamEvidenceView,
    condition: str,
    upstream: UpstreamEvidenceBuilder,
) -> tuple[ScheduledEvidence, ...]:
    """Materialize all five variants, preserving independent common-base semantics.

    Parameters
    ----------
    base
        Production blind or common reconstructed base.
    condition
        Base condition retained by presentation-only variants.
    upstream
        Frozen upstream evidence source.

    Returns
    -------
    tuple[ScheduledEvidence, ...]
        Swap, list reversal, expanded upstream, LC removal and hierarchy removal.

    Raises
    ------
    ValueError
        If mandatory expanded or removed evidence cannot be constructed.
    """

    variants = [
        _schedule_evidence(
            base=base, condition=condition, presentation="endpoint_swapped"
        ),
        _schedule_evidence(
            base=base, condition=condition, presentation="evidence_lists_reversed"
        ),
    ]
    expanded = upstream.build_pair(
        condition="expanded_upstream",
        first_sfi_uuid=base.endpoint_uuids[0],
        second_sfi_uuid=base.endpoint_uuids[1],
    )
    variants.append(
        _schedule_evidence(
            base=expanded, comparison_base=base, condition="expanded_upstream"
        )
    )

    for removal in ("lc_removed", "hierarchy_removed"):
        if isinstance(base, ProductionEvidenceView):
            payload, audit = _schedule_production_removal(base=base, condition=removal)
            variants.append(
                _schedule_evidence(
                    base=base, condition=removal, payload=payload, removal_audit=audit
                )
            )
        else:
            removed = upstream.build_pair(
                condition=removal,
                first_sfi_uuid=base.endpoint_uuids[0],
                second_sfi_uuid=base.endpoint_uuids[1],
            )
            variants.append(_schedule_evidence(base=removed, condition=removal))

    return tuple(variants)


def _snapshot_config(reader: _SnapshotReader) -> _CapturedKGConfig:
    """Recover the captured effective configuration and verify both hash conventions.

    The execution metadata omits the overwrite flag. Its value is recovered only
    when exactly one boolean choice matches the recorded effective-config digest.
    A later concurrency default is never included in historical serialized material.

    Parameters
    ----------
    reader
        Captured execution metadata and original configuration hashes.

    Returns
    -------
    _CapturedKGConfig
        Validated policy whose serialized bytes retain their original shape.

    Raises
    ------
    ValueError
        Required namespaces are absent, defaults alter captured fields, or no unique
        effective configuration matches the recorded digest.
    """

    extra = reader.read("kg_run.json")["extra"]
    raw = {key: extra[key] for key in ("as", "lc", "lp", "metadata")}
    expected = reader.read("lp_generation_checkpoint_manifest.json")["material"][
        "config_content_hash"
    ]
    matches = []

    for overwrite in (False, True):
        config = _CapturedKGConfig.model_validate({**raw, "overwrite": overwrite})
        dumped = config.model_dump(mode="json")
        _equal(
            actual={key: dumped[key] for key in raw},
            expected=raw,
            label="Captured configuration namespaces",
        )

        if content_hash(dumped) == expected:
            matches.append(config)

    if len(matches) != 1:
        raise ValueError("Captured effective configuration has no unique hash binding")

    return matches[0]


def _snapshot_execution(
    *,
    config: _CapturedKGConfig,
    population: LPRequestPopulation,
    reader: _SnapshotReader,
) -> tuple[str, dict[str, list[_LPCheckpoint]]]:
    """Validate completed historical prefixes or complete journal-bearing execution.

    Parameters
    ----------
    config
        Captured effective configuration.
    population
        Fully reconstructed original requests.
    reader
        Stable input reader.

    Returns
    -------
    tuple[str, dict[str, list[_LPCheckpoint]]]
        Explicit format interpretation and ordered producer/checker/final rows.

    Raises
    ------
    ValueError
        Execution is incomplete, incompatible, stale or has unresolved failures.
    """

    receipt = reader.read("lp_generation_checkpoint_manifest.json")
    _snapshot_receipt_fields(config=config, receipt=receipt)
    material = receipt["material"]

    for name in ("producer_instructions", "checker_instructions"):
        _equal(
            actual=material[name],
            expected=config.model_dump(mode="json")["lp"][name],
            label=name,
        )

    _equal(
        actual=material["request_manifest"],
        expected=population.manifest.model_dump(mode="json"),
        label="Execution request manifest",
    )
    _equal(
        actual=receipt["execution_content_hash"],
        expected=content_hash(material),
        label="Execution material hash",
    )
    _equal(actual=receipt["status"], expected="completed", label="Execution status")
    _equal(actual=receipt["failed_pair_ids"], expected=[], label="Unresolved failures")
    _equal(
        actual=material["retry_limits"],
        expected={
            "draft": config.learning_progressions.retry.producer_max_retries,
            "verdict": config.learning_progressions.retry.checker_max_retries,
        },
        label="Captured retry limits",
    )

    if (
        isinstance(receipt["run_number"], bool)
        or not isinstance(receipt["run_number"], int)
    ) or receipt["run_number"] < 1:
        raise ValueError("Invalid completed execution run number")

    reader.optional("lp_generation_checkpoint_transaction.json")

    if "lp_generation_checkpoint_transaction.json" not in reader.absent:
        raise ValueError("Completed run has an unfinished checkpoint transaction")

    rows = {
        stage: [_LPCheckpoint.model_validate(row) for row in reader.read(name)]
        for stage, name in _STAGE_FILES.items()
    }
    expected_counts = {stage: len(population.requests) for stage in _STAGE_FILES}
    _equal(
        actual=receipt["stage_counts"],
        expected=expected_counts,
        label="Execution stage counts",
    )
    _equal(
        actual={stage: len(values) for stage, values in rows.items()},
        expected=expected_counts,
        label="Actual stage coverage",
    )
    reader.check_hashes(receipt["artifact_byte_hashes"])
    _snapshot_failures(population=population, reader=reader, rows=rows)

    if "max_concurrent_requests" in material:
        _equal(
            actual=material["max_concurrent_requests"],
            expected=config.model_dump(mode="json")["lp"]["max_concurrent_requests"],
            label="Captured execution capacity",
        )
        store = LPGenerationCheckpoints(
            material=material, population=population, read_only=True, root=reader.root
        )
        store.verify_bytes()
        return "journal_bearing", rows

    expected_files = {*_STAGE_FILES.values(), "lp_generation_failures.json"}
    _equal(
        actual=set(receipt["artifact_byte_hashes"]),
        expected=expected_files,
        label="Historical prefix receipt coverage",
    )

    for name in ("lp_generation_usage.json", "lp_generation_pending_completions.json"):
        reader.optional(name)

        if name not in reader.absent:
            raise ValueError(
                "Historical prefix receipt has unauthenticated journal material"
            )

    return "historical_prefix", rows


def _snapshot_failures(
    *,
    population: LPRequestPopulation,
    reader: _SnapshotReader,
    rows: dict[str, list[_LPCheckpoint]],
) -> None:
    """Validate preserved failed attempts and their successful recovery bindings.

    Parameters
    ----------
    population
        Original bounded requests.
    reader
        Captured failure artifact and receipt.
    rows
        Complete successful checkpoints.

    Raises
    ------
    ValueError
        A failure is unresolved or references inconsistent requests or recovery.
    """

    receipt = reader.read("lp_generation_checkpoint_manifest.json")

    for value in reader.read("lp_generation_failures.json"):
        failure = _LPFailure.model_validate(value)
        request = population.requests[failure.request_index]
        expected = {
            "pair_ids": [pair.pair_id for pair in request.pairs],
            "request_content_hash": request.request_content_hash,
            "request_id": str(request.request_id),
            "resolved_response_content_hash": rows["response"][
                failure.request_index
            ].payload_content_hash,
        }
        _equal(
            actual={key: value[key] for key in expected},
            expected=expected,
            label="Failure recovery binding",
        )

        if (
            failure.resolved_run_number is None
            or not failure.run_number
            <= failure.resolved_run_number
            <= receipt["run_number"]
        ):
            raise ValueError("Unresolved or invalid failure recovery")


def _snapshot_graphs(
    *,
    artifacts: LPStandaloneArtifacts,
    config: _CapturedKGConfig,
    reader: _SnapshotReader,
    upstream: AcademicStandardsLCKGBundle,
) -> None:
    """Reconcile the additive combined graph, reports and exact internal projections.

    Parameters
    ----------
    artifacts
        Reconciled standalone LP artifacts.
    config
        Captured effective configuration.
    reader
        Stable input reader.
    upstream
        Validated upstream bundle.

    Raises
    ------
    ValueError
        Combined material, reports or projections disagree.
    """

    combined_raw = reader.read("as_lc_lp_kg_bundle.json")
    combined = AcademicStandardsLCLPKGBundle.model_validate(combined_raw)
    material = _merge_material(
        artifacts=artifacts, upstream=upstream.model_dump(mode="json")
    )
    _validate_combined_material(
        artifacts=artifacts,
        material=material,
        upstream=upstream.model_dump(mode="json"),
    )
    _equal(
        actual={
            key: value
            for key, value in combined_raw.items()
            if key != "validation_report"
        },
        expected=material,
        label="Additive combined graph",
    )
    report = combined_raw["validation_report"]
    _equal(
        actual=report["as_lc_validation_report"],
        expected=upstream.validation_report.model_dump(mode="json"),
        label="Combined upstream report",
    )
    _equal(
        actual=report["lp_validation_report"],
        expected=artifacts.validation_report.model_dump(mode="json"),
        label="Combined LP report",
    )
    hashes = {
        **artifacts.summary.input_content_hashes,
        "combined_graph": content_hash(material),
        "lp_generation_summary": content_hash(
            artifacts.summary.model_dump(mode="json")
        ),
        "lp_unresolved_items": content_hash(
            artifacts.unresolved_items.model_dump(mode="json")
        ),
        "lp_validation_report": content_hash(
            artifacts.validation_report.model_dump(mode="json")
        ),
    }
    _equal(
        actual=report["input_content_hashes"],
        expected=hashes,
        label="Combined material hashes",
    )
    _equal(
        actual=report["artifact_byte_hashes"],
        expected=_artifact_byte_hashes(artifacts),
        label="Combined artifact hashes",
    )
    _equal(
        actual=report["object_counts"],
        expected=_combined_counts(material),
        label="Combined object counts",
    )
    _equal(
        actual=hashes["effective_config"],
        expected=content_hash(config.model_dump(mode="json")),
        label="Combined configuration",
    )
    _equal(
        actual=combined.model_dump(mode="json"),
        expected=combined_raw,
        label="Combined schema round trip",
    )

    if not report["passed"] or report["errors"]:
        raise ValueError("Combined graph reports failed validation")

    nodes = [{**material["framework"], "entity_type": "StandardsFramework"}]

    for field, identity, entity in (
        ("items", "case_identifier_uuid", "StandardsFrameworkItem"),
        ("learning_components", "identifier", "LearningComponent"),
    ):
        nodes.extend(
            {**row, "entity_type": entity}
            for row in sorted(material[field], key=itemgetter(identity))
        )

    edges = [
        row
        for field in (
            "relationships_has_child",
            "relationships_supports",
            "relationships_builds_towards",
            "relationships_relates_to",
        )
        for row in sorted(material[field], key=lambda row: row["identifier"])
    ]
    _equal(
        actual=reader.read("as_lc_lp_nodes.jsonl"),
        expected=nodes,
        label="Combined node projection",
    )
    _equal(
        actual=reader.read("as_lc_lp_relationships.jsonl"),
        expected=edges,
        label="Combined edge projection",
    )


@contextmanager
def _snapshot_ownership(reader: _SnapshotReader) -> Iterator[None]:
    """Observe an existing writer lock without creating one for a historical copy.

    Parameters
    ----------
    reader
        Run whose material is being validated.

    Yields
    ------
    None
        Read-only validation scope.

    Raises
    ------
    ValueError
        The lock is replaced or lies outside the run.
    OSError
        A writer holds the lock or it cannot be read.
    """

    path = reader.root / ".lp_generation.lock"

    if not os.path.lexists(path):
        reader.absent.add(path.name)
        yield
        return

    if path.resolve(strict=True).parent != reader.root:
        raise ValueError("Generation lock resolves outside the run")

    with path.open("rb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        before = _stat_identity(os.fstat(lock.fileno()))
        yield

        if before != _stat_identity(path.stat()):
            raise ValueError("Generation lock changed during validation")


def _snapshot_population(
    *,
    config: _CapturedKGConfig,
    doc_key: str,
    reader: _SnapshotReader,
    upstream: AcademicStandardsLCKGBundle,
) -> LPRequestPopulation:
    """Reconstruct requests with the captured configuration's historical hash shape.

    The shared pure builder copies configuration through the current schema. Its
    evidence construction is unchanged; only newly computed configuration-dependent
    identities are rebound here when that copy adds a later capacity default. Saved
    requests, receipts and their hashes are never changed.

    Parameters
    ----------
    config
        Captured policy with original field presence.
    doc_key
        Source document identity.
    reader
        Read-only original artifact collection.
    upstream
        Validated fixed upstream graph.

    Returns
    -------
    LPRequestPopulation
        Independently reconstructed and byte-matched original requests.

    Raises
    ------
    ValueError
        Reconstructed candidates, bounded evidence or material identities differ.
    """

    population = build_lp_generation_requests(
        as_lc_bundle=upstream, doc_key=doc_key, kg_config=config
    )
    config_hash = lp_material_content_hash(config.model_dump(mode="json"))
    summary = population.candidates.summary.model_copy(
        update={"config_content_hash": config_hash}
    )
    candidates = replace(population.candidates, summary=summary)
    summary_hash = lp_material_content_hash(summary.model_dump(mode="json"))
    requests = []

    for request in population.requests:
        material = request.model_dump(
            exclude={"request_content_hash", "request_id"}, mode="json"
        )
        material.update(
            config_content_hash=config_hash, candidate_summary_content_hash=summary_hash
        )
        digest = lp_material_content_hash(material)
        requests.append(
            LPGenerationRequest.model_validate(
                {
                    **material,
                    "request_content_hash": digest,
                    "request_id": build_lp_request_id(digest),
                }
            )
        )

    payloads = _artifact_payloads(candidates=candidates, requests=tuple(requests))
    manifest = population.manifest.model_copy(
        update={
            "artifact_byte_hashes": {
                name: hashlib.sha256(raw).hexdigest() for name, raw in payloads.items()
            },
            "candidate_summary_content_hash": summary_hash,
            "config_content_hash": config_hash,
            "request_ids": tuple(request.request_id for request in requests),
            "requests_content_hash": lp_material_content_hash(
                [request.model_dump(mode="json") for request in requests]
            ),
        }
    )
    payloads["lp_generation_requests_manifest.json"] = (
        canonical_lp_json(manifest.model_dump(mode="json")) + "\n"
    ).encode()

    for name, expected in payloads.items():
        reader.read(name)
        _equal(actual=reader.artifacts[name].payload, expected=expected, label=name)

    return LPRequestPopulation(
        candidates=candidates, manifest=manifest, requests=tuple(requests)
    )


def _snapshot_receipt_fields(
    *, config: _CapturedKGConfig, receipt: dict[str, Any]
) -> None:
    """Require one recognized complete execution format without adding missing fields.

    Parameters
    ----------
    config
        Captured configuration and original capacity-field presence.
    receipt
        Recorded completed checkpoint receipt.

    Raises
    ------
    ValueError
        Receipt or execution fields are incomplete or unknown.
    """

    _equal(
        actual=set(receipt),
        expected={
            "artifact_byte_hashes",
            "execution_content_hash",
            "failed_pair_ids",
            "material",
            "run_number",
            "stage_counts",
            "status",
        },
        label="Checkpoint receipt fields",
    )
    expected = {
        "checker_instructions",
        "config_content_hash",
        "model_config",
        "model_settings",
        "producer_instructions",
        "prompt_definitions_content_hash",
        "request_manifest",
        "response_schema_content_hash",
        "retry_limits",
        "verdict_schema_content_hash",
    }

    if "max_concurrent_requests" in config.model_dump(mode="json")["lp"]:
        expected.add("max_concurrent_requests")

    _equal(
        actual=set(receipt["material"]),
        expected=expected,
        label="Captured execution fields",
    )


def _snapshot_relationships(
    *,
    claims: LPFinalClaims,
    config: _CapturedKGConfig,
    reader: _SnapshotReader,
    upstream: AcademicStandardsLCKGBundle,
) -> LPRelationships:
    """Reconstruct direct published rows and provenance from reconciled final claims.

    Parameters
    ----------
    claims
        Complete reconciled final judgments.
    config
        Captured policy and attribution.
    reader
        Stable input reader.
    upstream
        Source framework and permitted endpoint authority.

    Returns
    -------
    LPRelationships
        Exact standalone relationship population.

    Raises
    ------
    ValueError
        Relationships, source attribution or provenance disagree with direct claims.
    """

    edges = []
    provenance = {}

    for claim in claims.claims:
        if claim.source_sfi_uuid is None:
            continue

        edge, proof = _expected_relationship(
            as_lc_bundle=upstream,
            claim=claim,
            doc_key=claims.request_manifest.doc_key,
            final_claims=claims,
            kg_config=config,
        )

        # Preserve the original LP configuration hash, without adding a later capacity.
        proof.effective_lp_config_content_hash = content_hash(
            config.model_dump(mode="json")["lp"]
        )
        edge.metadata = proof.model_dump(mode="json")
        edges.append(edge)
        provenance[str(edge.identifier)] = proof

    edges.sort(key=lambda edge: str(edge.identifier))
    relationships = LPRelationships(
        final_claims_content_hash=claims.content_hash,
        relationship_provenance=provenance,
        relationships_builds_towards=tuple(
            edge for edge in edges if edge.relationship_type == "buildsTowards"
        ),
        relationships_relates_to=tuple(
            edge for edge in edges if edge.relationship_type == "relatesTo"
        ),
    )

    for field, filename in (
        ("relationships_builds_towards", "lp_relationships_builds_towards.jsonl"),
        ("relationships_relates_to", "lp_relationships_relates_to.jsonl"),
    ):
        _equal(
            actual=reader.read(filename),
            expected=[
                edge.model_dump(mode="json") for edge in getattr(relationships, field)
            ],
            label=filename,
        )

    _equal(
        actual=reader.read("lp_relationship_provenance.json"),
        expected={
            key: proof.model_dump(mode="json") for key, proof in provenance.items()
        },
        label="Relationship provenance",
    )
    return relationships


def _snapshot_source(
    *, config: _CapturedKGConfig, reader: _SnapshotReader, run: DiscoveredRun
) -> str:
    """Bind original source identity while supporting relocated result directories.

    Parameters
    ----------
    config
        Captured framework metadata.
    reader
        Stable input reader.
    run
        Discovered completed candidate.

    Returns
    -------
    str
        Actual run-relative source DocumentIR path.

    Raises
    ------
    ValueError
        Source location escapes the run container or identities disagree.
    """

    manifest = reader.read("kg_run_manifest.json")
    captured_root = Path(manifest["kg_run_dir"])
    source = Path(manifest["document_ir_fp"])
    relative = source.relative_to(captured_root.parent)
    name = (Path("..") / relative).as_posix()
    document = reader.read(name)
    framework = reader.read("as_lc_kg_bundle.json")["framework"]
    identities = [
        manifest["doc_key"],
        document["doc_key"],
        framework["metadata"]["doc_key"],
    ]
    _equal(
        actual=identities,
        expected=[run.doc_key] * len(identities),
        label="Source document identity",
    )
    _equal(
        actual=framework["case_identifier_uuid"],
        expected=str(run.framework_uuid),
        label="Framework identity",
    )
    _equal(
        actual=document["pdf_name"],
        expected=manifest["pdf_name"],
        label="Source document name",
    )
    _equal(
        actual=document["page_count"],
        expected=manifest["page_count"],
        label="Source page count",
    )
    _equal(
        actual=manifest["framework_title"],
        expected=config.metadata.framework_title,
        label="Captured framework title",
    )
    return name


def _snapshot_standalone(
    *,
    claims: LPFinalClaims,
    config: _CapturedKGConfig,
    reader: _SnapshotReader,
    relationships: LPRelationships,
    upstream: AcademicStandardsLCKGBundle,
) -> LPStandaloneArtifacts:
    """Reconcile recorded validation reports, counts, warnings and ambiguity artifacts.

    Parameters
    ----------
    claims
        Fully reconciled direct claims.
    config
        Captured configuration.
    reader
        Stable input reader.
    relationships
        Reconstructed direct relationships.
    upstream
        Validated upstream graph.

    Returns
    -------
    LPStandaloneArtifacts
        Standalone artifacts with actual counts and material links checked.

    Raises
    ------
    ValueError
        Summary, report, ambiguity or source material does not reconcile.
    """

    summary = LPGenerationSummary.model_validate(
        reader.read("lp_generation_summary.json")
    )
    report = LPValidationReport.model_validate(reader.read("lp_validation_report.json"))
    unresolved = LPUnresolvedItems.model_validate(
        reader.read("lp_unresolved_items.json")
    )
    failures = reader.read("lp_generation_failures.json")
    _equal(
        actual=report.cycle_diagnostics,
        expected=_lp_cycle_diagnostics(
            provenance=relationships.relationship_provenance,
            relationships=relationships.relationships_builds_towards,
        ),
        label="LP cycle diagnostics",
    )
    _equal(
        actual=report.object_counts["identifier_collisions"],
        expected=0,
        label="LP identifier collisions",
    )

    if not report.passed or report.errors or claims.graph_status != "acyclic":
        raise ValueError("LP graph lacks successful structural validation")

    _equal(
        actual=[claim.model_dump(mode="json") for claim in unresolved.claims],
        expected=[
            claim.model_dump(mode="json")
            for claim in claims.claims
            if claim.judgment.decision == "needs_review"
        ],
        label="Ambiguous claims",
    )
    _equal(
        actual=unresolved.final_claims_content_hash,
        expected=claims.content_hash,
        label="Ambiguity claim binding",
    )
    counts = _reconciled_counts(
        claims=claims, failures=failures, relationships=relationships, report=report
    )
    _equal(actual=summary.object_counts, expected=counts, label="Summary object counts")
    selection = build_lp_selection(as_lc_bundle=upstream, kg_config=config)

    for name, expected in (
        ("lp_eligibility_report.json", selection.model_dump(mode="json")),
        (
            "lp_eligible_sfis.json",
            [row.model_dump(mode="json") for row in selection.eligible_sfis],
        ),
    ):
        actual = reader.optional(name)

        if name not in reader.absent:
            _equal(actual=actual, expected=expected, label=name)

    hashes = {
        "as_lc_bundle": content_hash(upstream.model_dump(mode="json")),
        "checkpoint_receipt_bytes": reader.artifacts[
            "lp_generation_checkpoint_manifest.json"
        ].fingerprint.sha256,
        "effective_config": content_hash(config.model_dump(mode="json")),
        "final_claims": claims.content_hash,
        "relationships": content_hash(relationships.model_dump(mode="json")),
    }
    _equal(
        actual=report.input_content_hashes,
        expected=hashes,
        label="LP validation source hashes",
    )
    _equal(
        actual=summary.input_content_hashes,
        expected={
            **hashes,
            "eligibility_report": content_hash(selection.model_dump(mode="json")),
            "request_manifest": content_hash(
                claims.request_manifest.model_dump(mode="json")
            ),
        },
        label="Summary material hashes",
    )
    _equal(
        actual=summary.eligibility,
        expected=selection.model_dump(exclude={"sfis"}, mode="json"),
        label="Summary eligibility",
    )
    _equal(
        actual=summary.decision_counts,
        expected=claims.decision_counts,
        label="Summary decisions",
    )
    _equal(
        actual=summary.distributions,
        expected={
            "checker_outcome": dict(
                Counter(claim.provenance.checker_outcome for claim in claims.claims)
            ),
            "eligible_coordinate": dict(
                Counter(
                    row.coordinate.canonical_value
                    for row in selection.eligible_sfis
                    if row.coordinate.canonical_value is not None
                )
            ),
            "eligible_statement_type": dict(
                Counter(row.statement_type for row in selection.eligible_sfis)
            ),
        },
        label="Summary distributions",
    )
    _equal(
        actual=summary.warning_counts,
        expected=dict(
            Counter(
                warning
                for claim in claims.claims
                for warning in claim.judgment.warnings
            )
        ),
        label="Summary warning counts",
    )
    _equal(
        actual=summary.validation_report_content_hash,
        expected=report.content_hash,
        label="Summary validation report",
    )
    _equal(
        actual=summary.relationships_final_claims_content_hash,
        expected=claims.content_hash,
        label="Summary direct claims",
    )

    if not summary.validation_report_passed or counts["unresolved_failed_pairs"]:
        raise ValueError("Summary is not structurally complete")

    reader.check_hashes(summary.artifact_byte_hashes)
    reader.check_hashes(summary.input_artifact_byte_hashes)
    return LPStandaloneArtifacts(
        failures=tuple(failures),
        relationships=relationships,
        summary=summary,
        unresolved_items=unresolved,
        validation_report=report,
    )


def _snapshot_upstream(
    *, config: _CapturedKGConfig, reader: _SnapshotReader
) -> AcademicStandardsLCKGBundle:
    """Validate AS+LC graph structure, source preservation, provenance and wire
    projections.

    Parameters
    ----------
    config
        Captured curriculum policy and wire-grade mapping.
    reader
        Stable input reader.

    Returns
    -------
    AcademicStandardsLCKGBundle
        Reconciled upstream evidence, without a semantic truth interpretation.

    Raises
    ------
    ValueError
        Upstream bundles, provenance, reports or projections disagree.
    """

    academic = AcademicStandardsKGBundle.model_validate(
        reader.read("as_kg_bundle.json")
    )
    upstream = AcademicStandardsLCKGBundle.model_validate(
        reader.read("as_lc_kg_bundle.json")
    )

    for bundle, filename in (
        (academic, "as_validation_report.json"),
        (upstream, "as_lc_validation_report.json"),
    ):
        if not bundle.validation_report.passed or bundle.validation_report.errors:
            raise ValueError(f"Failed upstream validation: {filename}")

        _equal(
            actual=reader.read(filename),
            expected=(
                _upstream_report(academic=academic, upstream=upstream)
                if filename == "as_lc_validation_report.json"
                else bundle.validation_report.model_dump(mode="json")
            ),
            label=filename,
        )

    for field in ("framework", "items", "relationships_has_child"):
        _equal(
            actual=upstream.model_dump(mode="json")[field],
            expected=academic.model_dump(mode="json")[field],
            label=f"Upstream preservation: {field}",
        )

    errors = _validate_graph_export(
        relationships=academic.relationships_has_child,
        sf=academic.framework,
        sfis=academic.items,
    )
    errors.extend(
        _validate_merged_graph(
            academic_standards_bundle=academic,
            lc_generation_summary=LCGenerationSummary.model_validate(
                reader.read("lc_generation_summary.json")
            ),
            learning_components=upstream.learning_components,
            merged_entity_provenance=upstream.entity_provenance,
            supports_edges=upstream.relationships_supports,
        )
    )

    if errors:
        raise ValueError(f"Invalid upstream structure: {errors[0]}")

    _equal(
        actual=reader.read("as_entity_provenance.json"),
        expected=academic.entity_provenance,
        label="AS provenance",
    )
    _source_manifest_binding(
        actual=reader.read("kg_run_manifest.json"),
        original=academic.entity_provenance["kg_run_manifest"],
    )
    _equal(
        actual={
            key: value
            for key, value in upstream.entity_provenance.items()
            if key != "learning_components"
        },
        expected=academic.entity_provenance,
        label="Merged AS provenance",
    )
    _equal(
        actual=reader.read("lc_entity_provenance.json")["learning_components"],
        expected=upstream.entity_provenance["learning_components"],
        label="LC provenance",
    )
    nodes = _build_learning_commons_nodes(
        grade_level_mapping=config.academic_standards.grade_level_mapping,
        sf=academic.framework,
        sfis=academic.items,
    )
    edges = _build_learning_commons_relationships(
        nodes=nodes, relationships=academic.relationships_has_child
    )

    for prefix, node_rows, edge_rows in (
        ("as", nodes, edges),
        (
            "as_lc",
            [
                *nodes,
                *_build_learning_component_nodes(
                    learning_components=upstream.learning_components
                ),
            ],
            None,
        ),
    ):
        if edge_rows is None:
            edge_rows = [
                *edges,
                *_build_learning_commons_relationships(
                    nodes=node_rows, relationships=upstream.relationships_supports
                ),
            ]

        _equal(
            actual=reader.read(f"{prefix}_nodes.jsonl"),
            expected=[
                row.model_dump(by_alias=True, exclude_none=True, mode="json")
                for row in node_rows
            ],
            label=f"{prefix} wire nodes",
        )
        _equal(
            actual=reader.read(f"{prefix}_relationships.jsonl"),
            expected=[
                row.model_dump(by_alias=True, exclude_none=True, mode="json")
                for row in edge_rows
            ],
            label=f"{prefix} wire relationships",
        )

    _snapshot_upstream_bindings(
        academic=academic, config=config, reader=reader, upstream=upstream
    )
    return upstream


def _snapshot_upstream_bindings(
    *,
    academic: AcademicStandardsKGBundle,
    config: _CapturedKGConfig,
    reader: _SnapshotReader,
    upstream: AcademicStandardsLCKGBundle,
) -> None:
    """Check every upstream report fingerprint against preserved source artifacts.

    Parameters
    ----------
    academic
        Original Academic Standards bundle.
    config
        Captured configuration.
    reader
        Read-only source artifact collection.
    upstream
        Combined AS+LC bundle.

    Raises
    ------
    ValueError
        A recorded fingerprint or internal projection differs from its source.
    """

    as_material = {
        key: reader.read(key + ".json")
        for key in (
            "has_child_edges_final",
            "has_child_resolution_summary",
            "has_child_unresolved_edges",
            "sfi_final_records",
            "sfi_final_summary",
        )
    }
    as_material.update(
        {
            "grade_level_mapping": config.academic_standards.model_dump(mode="json")[
                "grade_level_mapping"
            ],
            "grade_level_statement_types": config.academic_standards.grade_level_statement_types,
            "is_current": config.metadata.is_current,
            "kg_run_manifest": academic.entity_provenance["kg_run_manifest"],
            "learning_commons_export_schema_version": academic.validation_report.learning_commons_export_schema_version,
            "learning_commons_nodes": reader.read("as_nodes.jsonl"),
            "learning_commons_relationships": reader.read("as_relationships.jsonl"),
        }
    )
    _equal(
        actual=academic.validation_report.input_fingerprints,
        expected={
            key: _fingerprint_jsonable(value) for key, value in as_material.items()
        },
        label="AS report source fingerprints",
    )
    lc_material = {
        key: reader.read(key + ".json")
        for key in (
            "lc_entity_provenance",
            "lc_generation_failures",
            "lc_generation_summary",
            "lc_supports_edges",
        )
    }
    lc_material.update(
        {
            "academic_standards_kg_bundle": academic.model_dump(mode="json"),
            "learning_components": reader.read("learning_components.jsonl"),
        }
    )
    _equal(
        actual=upstream.validation_report.input_fingerprints,
        expected={
            key: _fingerprint_jsonable(value) for key, value in lc_material.items()
        },
        label="AS+LC report source fingerprints",
    )
    _equal(
        actual=lc_material["lc_supports_edges"],
        expected=[
            edge.model_dump(mode="json") for edge in upstream.relationships_supports
        ],
        label="LC supports source",
    )
    _equal(
        actual=lc_material["learning_components"],
        expected=[
            node.model_dump(mode="json") for node in upstream.learning_components
        ],
        label="LC node source",
    )

    for name, expected in (
        ("as_standards_framework.json", academic.framework.model_dump(mode="json")),
        (
            "as_standards_framework_items.jsonl",
            [node.model_dump(mode="json") for node in academic.items],
        ),
        (
            "as_relationships_has_child.jsonl",
            [edge.model_dump(mode="json") for edge in academic.relationships_has_child],
        ),
        ("as_unresolved_items.json", academic.unresolved_items.model_dump(mode="json")),
    ):
        _equal(actual=reader.read(name), expected=expected, label=name)


def _source_manifest_binding(
    *, actual: dict[str, Any], original: dict[str, Any]
) -> None:
    """Permit relocation of absolute locations while requiring identical source material.

    Parameters
    ----------
    actual
        Current run manifest, potentially relocated with the result directory.
    original
        Manifest captured inside the original graph provenance.

    Raises
    ------
    ValueError
        Nonlocation material or the relative source layout changed.
    """

    locations = {"kg_run_dir", "document_ir_fp"}
    _equal(
        actual={key: value for key, value in actual.items() if key not in locations},
        expected={
            key: value for key, value in original.items() if key not in locations
        },
        label="Source manifest nonlocation material",
    )
    relative_paths = []

    for manifest in (actual, original):
        root = Path(manifest["kg_run_dir"])
        relative_paths.append(Path(manifest["document_ir_fp"]).relative_to(root.parent))

        if root.name != "kgs":
            raise ValueError("Captured run path does not identify a kgs directory")

    _equal(
        actual=relative_paths[0],
        expected=relative_paths[1],
        label="Relocated source layout",
    )


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    """Return physical identity and mutation markers for an opened file.

    Parameters
    ----------
    value
        Filesystem stat result.

    Returns
    -------
    tuple[int, int, int, int, int]
        Device, inode, size and nanosecond modification/change timestamps.
    """

    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _timestamp(value: Any) -> datetime:
    """Require a timezone-aware execution timestamp.

    Parameters
    ----------
    value
        Recorded timestamp.

    Returns
    -------
    datetime
        Parsed timestamp preserving its supplied timezone.

    Raises
    ------
    ValueError
        Timestamp is not a string or lacks a timezone.
    """

    if not isinstance(value, str):
        raise ValueError("execution timestamps must be strings")

    result = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("execution timestamps must include a timezone")

    return result


def _upstream_changed_paths(*, base: Any, path: str = "", value: Any) -> list[str]:
    """Identify changed JSON locations without conflating limit and evidence changes.

    Parameters
    ----------
    base
        Original bounded value.
    path
        Current JSON pointer prefix.
    value
        Diagnostic-condition value.

    Returns
    -------
    list[str]
        Deterministically ordered changed locations.
    """

    if base == value:
        return []

    if not isinstance(base, dict) or not isinstance(value, dict):
        return [path]

    paths: list[str] = []

    for key in sorted(base.keys() | value.keys()):
        child = path + "/" + _upstream_pointer_token(key)

        if key not in base or key not in value:
            paths.append(child)
        else:
            paths.extend(
                _upstream_changed_paths(base=base[key], path=child, value=value[key])
            )

    return paths


def _upstream_drop_field(
    *, key: str, parent: dict[str, Any], path: str, removals: list[dict[str, Any]]
) -> None:
    """Remove a representation while retaining its exact hash outside judge evidence.

    Parameters
    ----------
    key
        Field to remove when present.
    parent
        Mutable diagnostic copy.
    path
        JSON pointer to the parent.
    removals
        Separate construction audit receiving the removed-value identity.
    """

    if key in parent:
        value = parent.pop(key)
        removals.append(
            {
                "content_hash": lp_material_content_hash(value),
                "path": path + "/" + _upstream_pointer_token(key),
                "reason": "selected_family_or_opaque_copy_cannot_be_separated",
            }
        )


def _upstream_freeze_view(
    *,
    audit: dict[str, Any],
    builder: UpstreamEvidenceBuilder,
    condition: Literal[
        "reconstructed_bounded_upstream",
        "expanded_upstream",
        "lc_removed",
        "hierarchy_removed",
    ],
    pair: LPPairAdmissibility,
    payload: dict[str, Any],
) -> UpstreamEvidenceView:
    """Bind one immutable payload to its input, condition and construction audit.

    Parameters
    ----------
    audit
        Construction and omission records, excluded from judge-visible evidence.
    builder
        Isolated source and policy owner.
    condition
        Explicit evidence condition.
    pair
        Canonical admissibility result.
    payload
        Bounded evidence and policy, independent of production nomination.

    Returns
    -------
    UpstreamEvidenceView
        Exact canonical strings and hashes suitable for later schedule materialization.
    """

    payload_json = canonical_lp_json(payload)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    references = tuple(_upstream_references(path="/sfis", value=payload["sfis"]))
    audit_json = canonical_lp_json(audit)
    material = {
        "audit": audit,
        "condition": condition,
        "doc_key": builder._source.doc_key,
        "endpoint_uuids": [str(pair.first_sfi_uuid), str(pair.second_sfi_uuid)],
        "framework_uuid": str(builder._source.framework_uuid),
        "input_content_hash": builder._input_content_hash,
        "pair_id": pair.pair_id,
        "payload_sha256": payload_hash,
        "references": references,
    }
    return UpstreamEvidenceView(
        audit_json=audit_json,
        condition=condition,
        doc_key=builder._source.doc_key,
        endpoint_uuids=(pair.first_sfi_uuid, pair.second_sfi_uuid),
        framework_uuid=builder._source.framework_uuid,
        input_content_hash=builder._input_content_hash,
        material_content_hash=lp_material_content_hash(material),
        pair_id=pair.pair_id,
        payload_json=payload_json,
        payload_sha256=payload_hash,
        references=references,
    )


def _upstream_omissions(*, path: str = "", value: Any) -> dict[str, Any]:
    """Collect explicit excerpt and population omissions from bounded evidence.

    Parameters
    ----------
    value
        Bounded JSON value.
    path
        Current JSON pointer.

    Returns
    -------
    dict[str, Any]
        Omission counts and truncation flags, including truthful zero values.
    """

    result: dict[str, Any] = {}

    if isinstance(value, dict):
        for key, item in sorted(value.items()):
            child = path + "/" + _upstream_pointer_token(key)

            if key.startswith("omitted_") or key.endswith("truncated"):
                result[child] = item
            else:
                result.update(_upstream_omissions(path=child, value=item))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.update(_upstream_omissions(path=f"{path}/{index}", value=item))

    return result


def _upstream_pair_payload(
    *, builder: UpstreamEvidenceBuilder, expanded: bool, pair: LPPairAdmissibility
) -> dict[str, Any]:
    """Project upstream endpoints under the captured bounds without nomination.

    Parameters
    ----------
    builder
        Isolated source, graph and policy snapshot.
    expanded
        Whether to double every positive count, text and depth limit.
    pair
        Admissible pair containing canonical endpoint identities.

    Returns
    -------
    dict[str, Any]
        Same deterministic evidence procedure for every admitted pair.

    Raises
    ------
    ValueError
        If mandatory audit or all bounded DAG branches cannot fit the evidence limits.
    """

    config = builder._config.model_copy(deep=True)

    if expanded:
        limits = config.learning_progressions.evidence_limits
        config.learning_progressions.evidence_limits = type(limits).model_validate(
            {key: value * 2 for key, value in limits.model_dump().items()}
        )

    lp_config = config.learning_progressions
    max_characters = lp_config.evidence_limits.max_source_evidence_characters_per_sfi
    permissions = [
        {
            "decision": (
                "ambiguous" if item.decision == "needs_review" else item.decision
            ),
            "direction": item.direction,
        }
        for item in pair.admissible_decisions
    ]
    return {
        "framework_title": _bounded_text(
            max_characters=max_characters, text=builder._framework_title
        ).model_dump(mode="json"),
        "pair": {
            "admissible_decisions": permissions,
            "first_sfi_uuid": str(pair.first_sfi_uuid),
            "second_sfi_uuid": str(pair.second_sfi_uuid),
        },
        "policy": {
            "builds_towards": lp_config.builds_towards.model_dump(mode="json"),
            "checker_instructions": lp_config.checker_instructions,
            "developmental_coordinate": lp_config.developmental_coordinate.model_dump(
                mode="json"
            ),
            "producer_instructions": lp_config.producer_instructions,
            "relates_to": lp_config.relates_to.model_dump(mode="json"),
            "unresolved_participation": lp_config.unresolved_participation,
        },
        "sfis": [
            _request_sfi(
                graph_index=builder._graph_index,
                kg_config=config,
                records=builder._records,
                sfi_uuid=sfi_uuid,
            ).model_dump(mode="json")
            for sfi_uuid in (pair.first_sfi_uuid, pair.second_sfi_uuid)
        ],
    }


def _upstream_pair_tags(
    *, first: _EndpointFeatures, second: _EndpointFeatures
) -> tuple[str, ...]:
    """Classify upstream diagnostic membership without production or judge outcomes.

    Parameters
    ----------
    first
        First endpoint's source-derived facts.
    second
        Second endpoint's source-derived facts.

    Returns
    -------
    tuple[str, ...]
        Every matching tag in the prescribed stable order.
    """

    valid_ranks = first.rank is not None and second.rank is not None
    shared = first.lc_uuids & second.lc_uuids
    union = first.tokens | second.tokens
    high_jaccard = bool(union) and 2 * len(first.tokens & second.tokens) >= len(union)
    flags = (
        first.multi_parent_context or second.multi_parent_context,
        valid_ranks and first.rank == second.rank,
        valid_ranks and first.rank != second.rank,
        not valid_ranks,
        first.normalized_text == second.normalized_text,
        first.normalized_text != second.normalized_text and high_jaccard,
        not first.lc_uuids or not second.lc_uuids,
        bool(shared),
        bool(first.lc_uuids and second.lc_uuids) and not shared,
        first.broadly_reused_lc or second.broadly_reused_lc,
        first.unresolved_ancestry or second.unresolved_ancestry,
        first.reconstructed_truncation or second.reconstructed_truncation,
    )
    return tuple(
        tag for tag, applies in zip(UPSTREAM_DIAGNOSTIC_TAGS, flags) if applies
    )


def _upstream_payload_hash(payload: dict[str, Any]) -> str:
    """Hash the exact canonical UTF-8 judge-visible evidence bytes.

    Parameters
    ----------
    payload
        Evidence payload.

    Returns
    -------
    str
        SHA-256 of canonical JSON without an added newline.
    """

    return hashlib.sha256(canonical_lp_json(payload).encode("utf-8")).hexdigest()


def _upstream_pointer_token(value: str) -> str:
    """Escape a JSON object key as one JSON pointer token.

    Parameters
    ----------
    value
        Original object key.

    Returns
    -------
    str
        Escaped token preserving slash and tilde characters.
    """

    return value.replace("~", "~0").replace("/", "~1")


def _upstream_references(*, path: str, value: Any) -> list[str]:
    """Enumerate shown evidence locations without treating hashes as evidence.

    Parameters
    ----------
    path
        JSON pointer to the value.
    value
        Bounded evidence value.

    Returns
    -------
    list[str]
        Ordered pointers to excerpts, scalar facts, warnings and explicit absences.
    """

    if isinstance(value, dict):
        if {"text", "truncated", "content_hash"} <= value.keys():
            return [path]

        return [
            reference
            for key, item in sorted(value.items())
            if not key.endswith("content_hash")
            for reference in _upstream_references(
                path=path + "/" + _upstream_pointer_token(key), value=item
            )
        ]

    if isinstance(value, list):
        return [
            reference
            for index, item in enumerate(value)
            for reference in _upstream_references(path=f"{path}/{index}", value=item)
        ] or [path]

    return [path]


def _upstream_remove(
    *, condition: Literal["lc_removed", "hierarchy_removed"], payload: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Remove the selected family and opaque alternate copies from a fresh payload.

    Source/audit metadata and coordinate-origin strings are untyped and may duplicate
    LC or hierarchy evidence. Withhold those fields in either ablation and record the
    collateral omissions. Intrinsic SFI text, resolved coordinate semantics, policy
    permissions and uncertainty warnings remain. Hierarchy removal also withholds LC
    metadata, which can copy hierarchy context, while retaining intrinsic LC text.

    Parameters
    ----------
    condition
        Selected evidence family to remove.
    payload
        Common reconstructed base evidence.

    Returns
    -------
    tuple[dict[str, Any], list[dict[str, Any]]]
        Independent payload copy and field-level removed-value hashes.
    """

    result = json.loads(canonical_lp_json(payload))
    removals: list[dict[str, Any]] = []

    for index, endpoint in enumerate(result["sfis"]):
        path = f"/sfis/{index}"

        for key in (
            "source_evidence",
            "source_evidence_content_hash",
            "omitted_source_evidence_count",
        ):
            _upstream_drop_field(key=key, parent=endpoint, path=path, removals=removals)

        for key in (
            "source_fields",
            "source_fields_content_hash",
            "omitted_source_field_count",
        ):
            _upstream_drop_field(
                key=key,
                parent=endpoint["coordinate"],
                path=path + "/coordinate",
                removals=removals,
            )

        contexts = [(path + "/context", endpoint["context"])]
        contexts.extend(
            (f"{path}/ancestors/{offset}", ancestor)
            for offset, ancestor in enumerate(endpoint["ancestors"])
        )

        for context_path, context in contexts:
            for key in ("audit_context", "upstream_sfi_content_hash"):
                _upstream_drop_field(
                    key=key, parent=context, path=context_path, removals=removals
                )

        _upstream_remove_family(
            condition=condition, endpoint=endpoint, path=path, removals=removals
        )
        endpoint["warnings"].append(
            "Evidence has been deliberately withheld. Opaque source/audit metadata "
            "and coordinate origins were also withheld to prevent duplicate evidence; "
            "this comparison can therefore remove additional context."
        )

    if condition == "hierarchy_removed":
        _upstream_withhold_warning_identifiers(
            endpoint_uuids={
                result["pair"]["first_sfi_uuid"],
                result["pair"]["second_sfi_uuid"],
            },
            path="",
            removals=removals,
            value=result,
        )

    return result, sorted(removals, key=lambda item: item["path"])


def _upstream_remove_family(
    *,
    condition: Literal["lc_removed", "hierarchy_removed"],
    endpoint: dict[str, Any],
    path: str,
    removals: list[dict[str, Any]],
) -> None:
    """Remove explicit family records and every associated aggregate representation.

    Parameters
    ----------
    condition
        Family being withheld.
    endpoint
        Mutable endpoint evidence.
    path
        Endpoint JSON pointer.
    removals
        Separate field-level audit.
    """

    keys = (
        (
            "learning_components",
            "learning_components_content_hash",
            "omitted_learning_component_count",
        )
        if condition == "lc_removed"
        else ("ancestor_paths", "ancestors", "parent_sfi_uuids")
    )

    for key in keys:
        _upstream_drop_field(key=key, parent=endpoint, path=path, removals=removals)

    if condition == "hierarchy_removed":
        _upstream_drop_field(
            key="root_fallback_relationship_uuids",
            parent=endpoint["context"],
            path=path + "/context",
            removals=removals,
        )

        for index, component in enumerate(endpoint["learning_components"]):
            for key in ("metadata", "upstream_content_hash"):
                _upstream_drop_field(
                    key=key,
                    parent=component,
                    path=f"{path}/learning_components/{index}",
                    removals=removals,
                )


def _upstream_report(
    *, academic: AcademicStandardsKGBundle, upstream: AcademicStandardsLCKGBundle
) -> dict[str, Any]:
    """Reconstruct the standalone merged report's additional delivery counts.

    Parameters
    ----------
    academic
        Source framework and fallback count.
    upstream
        Combined bundle and its internal report.

    Returns
    -------
    dict[str, Any]
        Original standalone report shape.
    """

    report = upstream.validation_report.model_dump(mode="json")
    counts = report["object_counts"]
    counts.update(
        {
            "learning_commons_framework_nodes": 1,
            "learning_commons_item_nodes": len(upstream.items),
            "learning_commons_nodes": 1
            + len(upstream.items)
            + len(upstream.learning_components),
            "learning_commons_relationships": len(upstream.relationships_has_child)
            + len(upstream.relationships_supports),
            "learning_commons_unresolved_fallback_relationships": academic.validation_report.object_counts[
                "learning_commons_unresolved_fallback_relationships"
            ],
        }
    )
    return report


def _upstream_unavailable(payload: dict[str, Any]) -> list[str]:
    """Record unavailable evidence separately from deliberate family removals.

    Parameters
    ----------
    payload
        Bounded evidence view.

    Returns
    -------
    list[str]
        Paths to unavailable coordinate or empty evidence populations.
    """

    unavailable: list[str] = []

    for index, endpoint in enumerate(payload["sfis"]):
        for key in ("ancestor_paths", "learning_components", "source_evidence"):
            if key in endpoint and not endpoint[key]:
                unavailable.append(f"/sfis/{index}/{key}")

        if endpoint["coordinate"]["status"] == "missing":
            unavailable.append(f"/sfis/{index}/coordinate")

    return unavailable


def _upstream_warning_values(
    *,
    endpoint_uuids: set[str],
    path: str,
    removals: list[dict[str, Any]],
    warnings: list[str],
) -> None:
    """Mask non-endpoint identities while retaining each warning's meaning.

    Parameters
    ----------
    endpoint_uuids
        Canonical endpoints still shown to the judge.
    path
        JSON pointer to the warning list.
    removals
        Audit retaining original warnings whenever their identifiers change.
    warnings
        Mutable warning list from the fresh diagnostic copy.
    """

    for index, warning in enumerate(warnings):
        changed = warning
        for identifier in re.findall(
            r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", warning
        ):
            if identifier not in endpoint_uuids:
                changed = changed.replace(identifier, "[withheld context]")

        if changed != warning:
            removals.append(
                {
                    "original_warning": warning,
                    "path": f"{path}/{index}",
                    "reason": "hide_context_identity_preserving_warning",
                }
            )
            warnings[index] = changed


def _upstream_withhold_warning_identifiers(
    *, endpoint_uuids: set[str], path: str, removals: list[dict[str, Any]], value: Any
) -> None:
    """Keep warning meaning while hiding positive-context identities in an ablation.

    Parameters
    ----------
    endpoint_uuids
        Endpoint identities which remain visible.
    path
        Current JSON pointer.
    removals
        Audit retaining original warning values and their locations.
    value
        Mutable payload tree.
    """

    if isinstance(value, dict):
        for key, item in value.items():
            child = path + "/" + _upstream_pointer_token(key)

            if key == "warnings":
                _upstream_warning_values(
                    endpoint_uuids=endpoint_uuids,
                    path=child,
                    removals=removals,
                    warnings=item,
                )
            else:
                _upstream_withhold_warning_identifiers(
                    endpoint_uuids=endpoint_uuids,
                    path=child,
                    removals=removals,
                    value=item,
                )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _upstream_withhold_warning_identifiers(
                endpoint_uuids=endpoint_uuids,
                path=f"{path}/{index}",
                removals=removals,
                value=item,
            )


def _walk_runs(
    *, evaluation_root: Path, results_root: Path
) -> tuple[list[Path], list[DiscoveryAlias], list[DiscoverySkip]]:
    """Traverse arbitrary parent layouts once per physical directory.

    Parameters
    ----------
    evaluation_root
        Resolved evaluator output subtree to exclude.
    results_root
        Resolved traversal root.

    Returns
    -------
    tuple[list[Path], list[DiscoveryAlias], list[DiscoverySkip]]
        Run boundaries, observed aliases and explicit traversal exclusions.

    Raises
    ------
    LPDiscoveryError
        Invalid or unavailable discovery evidence.
    ValueError
        Traversal root is not a directory or lies inside the evaluator output.
    """

    aliases: list[DiscoveryAlias] = []
    directories: list[Path] = []
    pending = [results_root]
    skipped: list[DiscoverySkip] = []
    visited: set[tuple[int, int]] = set()

    while pending:
        path = pending.pop()

        try:
            resolved = path.resolve(strict=True)

            exclusion = _path_exclusion(
                evaluation_root=evaluation_root,
                path=path,
                resolved=resolved,
                results_root=results_root,
            )

            if exclusion is not None:
                skipped.append(exclusion)
                continue

            info = resolved.stat()

            if not stat.S_ISDIR(info.st_mode):
                continue

            if path != resolved:
                aliases.append(
                    DiscoveryAlias(discovered_path=path, resolved_path=resolved)
                )

            identity = (info.st_dev, info.st_ino)

            if identity in visited:
                continue

            visited.add(identity)

            if _is_run_directory(
                evaluation_root=evaluation_root, path=path, resolved=resolved
            ):
                directories.append(resolved)
                continue

            with os.scandir(resolved) as entries:
                children = sorted(Path(entry.path) for entry in entries)

            pending.extend(reversed(children))
        except (OSError, RuntimeError, ValueError) as exc:
            raise LPDiscoveryError(
                message=f"Cannot safely discover runs at {path}: {exc}"
            ) from exc

    return sorted(directories), aliases, skipped


def build_admissible_population(
    source: UpstreamEvidenceSource,
) -> AdmissiblePairPopulation:
    """Build exact pair counts and indexing from upstream evidence and policy alone.

    No candidate population is read or generated. Storage scales with endpoints and
    compatible type/rank groups, rather than the complete pair population.

    Parameters
    ----------
    source
        Isolated frozen upstream bytes and captured curriculum configuration.

    Returns
    -------
    AdmissiblePairPopulation
        Canonical random-access and streaming population, with upstream-only tags.

    Raises
    ------
    ValueError
        If the upstream binding, policy or bounded evidence is invalid.
    """

    builder = build_upstream_evidence_builder(source)
    records = builder._pair_filter.eligible_sfis
    cells: dict[tuple[str, int | None, tuple[str, ...]], list[UUID]] = {}

    for record in records:
        key = (
            record.statement_type,
            record.coordinate.rank,
            tuple(sorted(record.eligibility_reasons)),
        )
        cells.setdefault(key, []).append(record.sfi.case_identifier_uuid)

    groups = tuple(tuple(members) for members in cells.values())
    endpoint_groups = {
        identifier: index
        for index, members in enumerate(groups)
        for identifier in members
    }
    endpoints = tuple(record.sfi.case_identifier_uuid for record in records)
    row_ends: list[int] = []
    count = 0

    for first in endpoints:
        allowed = _population_compatible_groups(
            builder=builder, first=first, groups=groups
        )
        count += sum(
            len(groups[cell]) - bisect_right(groups[cell], first) for cell in allowed
        )
        row_ends.append(count)

    features = _population_features(builder=builder, records=records)
    identity = {
        "endpoints": [str(item) for item in endpoints],
        "features": {
            str(identifier): {
                **asdict(feature),
                "lc_uuids": sorted(str(item) for item in feature.lc_uuids),
                "tokens": sorted(feature.tokens),
            }
            for identifier, feature in features.items()
        },
        "groups": [[str(item) for item in group] for group in groups],
        "input_content_hash": builder._input_content_hash,
        "row_ends": row_ends,
        "tag_order": UPSTREAM_DIAGNOSTIC_TAGS,
    }
    return AdmissiblePairPopulation(
        _builder=builder,
        _endpoint_groups=endpoint_groups,
        _features=features,
        _groups=groups,
        _row_ends=tuple(row_ends),
        doc_key=source.doc_key,
        endpoint_uuids=endpoints,
        framework_uuid=source.framework_uuid,
        input_content_hash=builder._input_content_hash,
        material_content_hash=lp_material_content_hash(identity),
    )


def build_production_evidence_builder(
    snapshot: ValidatedSnapshot,
) -> ProductionEvidenceBuilder:
    """Index exact original requests, claims and policy without upstream retrieval.

    Use a snapshot from load_frozen_lp_inputs. Original source bytes are rechecked
    here, and historical receipts are interpreted read-only without production
    checkpoint loading, migration, recovery or generation.

    Parameters
    ----------
    snapshot
        Complete validated frozen production snapshot.

    Returns
    -------
    ProductionEvidenceBuilder
        Reusable original-material owner for selected production pairs.

    Raises
    ------
    ValueError
        If original bytes, receipt, claims, requests, framework or policy disagree.
    """

    requests_artifact = _evaluation_artifact(
        name="lp_generation_requests.jsonl", snapshot=snapshot
    )
    claims_artifact = _evaluation_artifact(
        name="lp_final_claims.json", snapshot=snapshot
    )
    receipt_artifact = _evaluation_artifact(
        name="lp_generation_checkpoint_manifest.json", snapshot=snapshot
    )
    claims = LPFinalClaims.model_validate(
        _decode_snapshot_json(claims_artifact.payload)
    )
    receipt = _decode_snapshot_json(receipt_artifact.payload)
    _equal(
        actual=claims.execution_material,
        expected=receipt["material"],
        label="Original execution material",
    )
    _equal(
        actual=claims.checkpoint_receipt_byte_hash,
        expected=receipt_artifact.fingerprint.sha256,
        label="Original receipt",
    )
    if (
        claims.request_manifest.doc_key != snapshot.run.doc_key
        or claims.request_manifest.framework_uuid != snapshot.run.framework_uuid
    ):
        raise ValueError("Original production evidence has a different framework.")

    bindings = _production_requests(artifact=requests_artifact, claims=claims)

    for claim in claims.claims:
        binding = bindings[claim.judgment.pair_id]

        if (
            binding.request_id != claim.provenance.request_id
            or binding.request_content_hash != claim.provenance.request_content_hash
            or binding.endpoint_uuids
            != (claim.judgment.first_sfi_uuid, claim.judgment.second_sfi_uuid)
        ):
            raise ValueError("Original claim and bounded request bindings disagree.")

    config = _CapturedKGConfig.model_validate_json(snapshot.config_json)
    lp = config.learning_progressions
    policy = {
        "builds_towards": lp.builds_towards.model_dump(mode="json"),
        "checker_instructions": lp.checker_instructions,
        "developmental_coordinate": lp.developmental_coordinate.model_dump(mode="json"),
        "producer_instructions": lp.producer_instructions,
        "relates_to": lp.relates_to.model_dump(mode="json"),
        "unresolved_participation": lp.unresolved_participation,
    }

    for name in ("producer_instructions", "checker_instructions"):
        _equal(
            actual=policy[name],
            expected=claims.execution_material[name],
            label="Original curriculum instructions",
        )

    requests = [
        LPGenerationRequest.model_validate(_decode_snapshot_json(line))
        for line in requests_artifact.payload.splitlines()
    ]
    return ProductionEvidenceBuilder(
        _claims_json={
            claim.judgment.pair_id: canonical_lp_json(claim.model_dump(mode="json"))
            for claim in claims.claims
        },
        _input_content_hash=lp_material_content_hash(
            {
                "artifacts": TypeAdapter(tuple[FileFingerprint, ...]).dump_python(
                    tuple(
                        artifact.fingerprint
                        for artifact in (
                            claims_artifact,
                            requests_artifact,
                            receipt_artifact,
                        )
                    ),
                    mode="json",
                ),
                "config_json": snapshot.config_json,
                "doc_key": snapshot.run.doc_key,
                "framework_uuid": str(snapshot.run.framework_uuid),
            }
        ),
        _policy_json=canonical_lp_json(policy),
        _requests_json={
            request.request_id: canonical_lp_json(request.model_dump(mode="json"))
            for request in requests
        },
    )


def build_production_population(
    *, population: AdmissiblePairPopulation, snapshot: ValidatedSnapshot
) -> ProductionPopulation:
    """Build a separate production inventory without altering upstream selection.

    Call only for the production component or after independent selection/evidence have
    been frozen. The returned inventory must not guide independent sampling. Use
    load_frozen_lp_inputs before this operation to validate original material.

    Parameters
    ----------
    population
        Independently constructed admissible population and upstream features.
    snapshot
        Matching frozen snapshot with the original production artifact bytes.

    Returns
    -------
    ProductionPopulation
        Complete canonical outcomes, twelve diagnostic counts and provenance.

    Raises
    ------
    ValueError
        If snapshot identity, artifact bindings or original populations disagree.
    """

    if upstream_evidence_source(snapshot) != population._builder._source:
        raise ValueError("Production and upstream population bindings differ.")

    names = (
        "lp_final_claims.json",
        "lp_generation_requests.jsonl",
        "lp_relationships_builds_towards.jsonl",
        "lp_relationships_relates_to.jsonl",
    )
    artifacts = tuple(
        _evaluation_artifact(name=name, snapshot=snapshot) for name in names
    )
    claims = LPFinalClaims.model_validate(_decode_snapshot_json(artifacts[0].payload))

    if (
        claims.request_manifest.doc_key != population.doc_key
        or claims.request_manifest.framework_uuid != population.framework_uuid
    ):
        raise ValueError("Production claims have a different framework identity.")

    requests = _production_requests(artifact=artifacts[1], claims=claims)
    publications = _production_publications(artifacts=artifacts[2:], claims=claims)
    pairs = tuple(
        sorted(
            (
                _production_pair(
                    claim=claim,
                    population=population,
                    publication=publications.get(claim.judgment.pair_id),
                    request=requests[claim.judgment.pair_id],
                )
                for claim in claims.claims
            ),
            key=lambda pair: pair.pair.endpoint_uuids,
        )
    )
    outcome_counts = tuple(
        (outcome, sum(pair.outcome == outcome for pair in pairs))
        for outcome in PRODUCTION_OUTCOMES
    )
    tag_counts = tuple(
        (tag, sum(tag in pair.tags for pair in pairs))
        for tag in PRODUCTION_DIAGNOSTIC_TAGS
    )
    material = {
        "artifact_hashes": {
            artifact.name: artifact.fingerprint.sha256 for artifact in artifacts
        },
        "outcome_counts": outcome_counts,
        "pairs": TypeAdapter(tuple[ProductionPair, ...]).dump_python(
            pairs, mode="json"
        ),
        "tag_counts": tag_counts,
        "upstream_population_content_hash": population.material_content_hash,
    }
    return ProductionPopulation(
        artifact_fingerprints=tuple(artifact.fingerprint for artifact in artifacts),
        doc_key=population.doc_key,
        framework_uuid=population.framework_uuid,
        material_content_hash=lp_material_content_hash(material),
        outcome_counts=outcome_counts,
        pairs=pairs,
        tag_counts=tag_counts,
        upstream_input_content_hash=population.input_content_hash,
    )


def build_upstream_evidence_builder(
    source: UpstreamEvidenceSource,
) -> UpstreamEvidenceBuilder:
    """Validate and isolate an upstream-only evidence capability without file access.

    Parameters
    ----------
    source
        Exact upstream bytes and captured configuration from a validated frozen input.

    Returns
    -------
    UpstreamEvidenceBuilder
        Run-local graph and policy indexes for on-demand bounded pair construction.

    Raises
    ------
    ValueError
        If source bytes, framework identity, policy or upstream structure are invalid.
    """

    artifact = source.upstream_artifact

    if (
        artifact.name != "as_lc_kg_bundle.json"
        or hashlib.sha256(artifact.payload).hexdigest() != artifact.fingerprint.sha256
        or len(artifact.payload) != artifact.fingerprint.size_bytes
    ):
        raise ValueError("Upstream evidence artifact does not match its byte binding.")

    bundle = AcademicStandardsLCKGBundle.model_validate(
        _decode_snapshot_json(artifact.payload)
    )
    config = _CapturedKGConfig.model_validate(
        _decode_snapshot_json(source.config_json.encode("utf-8"))
    )
    if bundle.framework.case_identifier_uuid != source.framework_uuid:
        raise ValueError("Upstream evidence framework does not match its binding.")

    selection = build_lp_selection(as_lc_bundle=bundle, kg_config=config)
    pair_filter = build_lp_pair_filter(
        as_lc_bundle=bundle, doc_key=source.doc_key, kg_config=config
    )
    return UpstreamEvidenceBuilder(
        _config=config,
        _framework_title=bundle.framework.name,
        _graph_index=build_lp_graph_index(bundle),
        _input_content_hash=lp_material_content_hash(
            {
                "config_json": source.config_json,
                "doc_key": source.doc_key,
                "framework_uuid": str(source.framework_uuid),
                "upstream_sha256": artifact.fingerprint.sha256,
            }
        ),
        _pair_filter=pair_filter,
        _records={record.sfi.case_identifier_uuid: record for record in selection.sfis},
        _source=source,
    )


def discover_lp_runs(
    *, evaluation_root: Path, results_root: Path
) -> DiscoveryInventory:
    """Inventory recorded KG executions without consuming graph or judgment data.

    Completed candidates are not validated snapshots. The caller must validate their
    full material bindings, absence of active mutation and frozen evidence before
    preparing any evaluation request. Discovery never invokes a production pipeline,
    modifies a file, resolves a judge model or creates an evaluation directory.

    Parameters
    ----------
    evaluation_root
        Evaluator output subtree, supplied by the entry point from repository paths.
    results_root
        Starting directory; arbitrary parent layouts and a kgs root are accepted.

    Returns
    -------
    DiscoveryInventory
        Deterministically ordered preliminary candidates and excluded runs.

    Raises
    ------
    LPDiscoveryError
        Invalid root, ambiguous/unreadable metadata, conflicting candidate identities,
        changing metadata, aliased outputs or absence of completed candidates.
    ValueError
        Starting path is not a directory or lies inside the evaluator output.
    """

    try:
        resolved_root = results_root.resolve(strict=True)
        resolved_output = evaluation_root.resolve(strict=False)

        if not resolved_root.is_dir():
            raise ValueError("starting path is not a directory")

        if resolved_root.is_relative_to(resolved_output):
            raise ValueError("starting directory lies inside evaluator output")
    except (OSError, RuntimeError, ValueError) as exc:
        raise LPDiscoveryError(message=f"Invalid discovery root: {exc}") from exc

    directories, aliases, skipped = _walk_runs(
        evaluation_root=resolved_output, results_root=resolved_root
    )
    runs: list[DiscoveredRun] = []
    identities: dict[tuple[str, str], Path] = {}

    for directory in directories:
        run = _classify_run(kgs_directory=directory, results_root=resolved_root)
        run_aliases = {
            alias.discovered_path / directory.relative_to(alias.resolved_path)
            for alias in aliases
            if directory.is_relative_to(alias.resolved_path)
        }
        run = replace(run, aliases=tuple(sorted(run_aliases - {directory})))
        _register_run_identity(identities=identities, run=run)
        runs.append(run)

    # Recheck only previously read metadata; growing judgment files remain unopened.
    for run in runs:
        for fingerprint in run.fingerprints:
            _, observed = _read_metadata(
                path=fingerprint.path, results_root=resolved_root
            )

            if observed != fingerprint:
                raise LPDiscoveryError(
                    message=f"Run metadata changed during discovery: {fingerprint.path}"
                )

    inventory = DiscoveryInventory(
        aliases=tuple(aliases),
        evaluation_root=resolved_output,
        results_root=resolved_root,
        runs=tuple(runs),
        skipped_paths=tuple(skipped),
    )

    if not any(run.status == "completed_candidate" for run in runs):
        raise LPDiscoveryError(
            inventory=inventory,
            message="No completed curriculum candidates were discovered.",
        )

    return inventory


def evaluation_token_set(text: str) -> frozenset[str]:
    """Tokenize normalized text into Unicode alphanumeric runs without stopwords.

    Parameters
    ----------
    text
        Source SFI text, including unfamiliar languages and scripts.

    Returns
    -------
    frozenset[str]
        Unique alphanumeric runs; punctuation and underscores separate tokens.
    """

    return frozenset(re.findall(r"[^\W_]+", normalize_evaluation_text(text)))


def freeze_lp_inputs(
    *, inventory: DiscoveryInventory, repository_root: Path
) -> FrozenInputs:
    """Validate and freeze exactly the discovery selection into separate input copies.

    The content-addressed directory is reused only when its complete manifest and
    copies match. Interrupted or invalid existing output fails without repair or
    overwrite. The manifest is the final commit marker; partial copies are never
    returned as frozen inputs. This operation prepares inputs only and makes no calls.

    Parameters
    ----------
    inventory
        Fixed discovery selection, including excluded unfinished runs.
    repository_root
        Repository whose results/lp_evals directory owns evaluator outputs.

    Returns
    -------
    FrozenInputs
        Exact path and digest to retain in the later evaluation invocation manifest.

    Raises
    ------
    LPSnapshotError
        Selected inputs fail validation, output aliases inputs, or publication fails.
    """

    try:
        output = repository_root.resolve(strict=True) / "results" / "lp_evals"
        _equal(
            actual=inventory.evaluation_root,
            expected=output,
            label="Repository evaluation root",
        )
        _frozen_output_boundary(inventory=inventory, output=output)
        snapshots = validate_lp_snapshots(inventory)
        manifest = FrozenInputManifest(
            inventory=inventory,
            kind="lp_evaluation_inputs",
            snapshots=tuple(
                FrozenSnapshot(
                    absent_artifacts=snapshot.absent_artifacts,
                    artifacts=tuple(
                        FrozenArtifact(
                            fingerprint=artifact.fingerprint, name=artifact.name
                        )
                        for artifact in snapshot.artifacts
                    ),
                    checkpoint_format=snapshot.checkpoint_format,
                    config_json=snapshot.config_json,
                    run=snapshot.run,
                    source_artifact=snapshot.source_artifact,
                )
                for snapshot in snapshots
            ),
        )
        _frozen_layout(manifest)
        payload = _frozen_bytes(manifest)
        digest = hashlib.sha256(payload).hexdigest()
        directory = output / "inputs" / digest
        reference = FrozenInputs(
            content_hash=digest, manifest_path=directory / "manifest.json"
        )

        for snapshot in manifest.snapshots:
            _frozen_source_check(snapshot)

        _frozen_output_boundary(inventory=inventory, output=output)

        if os.path.lexists(directory):
            load_frozen_lp_inputs(reference)
            return reference

        directory.parent.mkdir(parents=True, exist_ok=True)

        if directory.parent.resolve(strict=True) != directory.parent:
            raise ValueError("Frozen output parent is aliased")

        directory.mkdir()
        _frozen_store_copies(directory=directory, snapshots=snapshots)

        for snapshot in manifest.snapshots:
            _frozen_source_check(snapshot)

        _frozen_publish(directory=directory, payload=payload)
        load_frozen_lp_inputs(reference)
        return reference
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        raise LPSnapshotError(f"Cannot freeze LP inputs: {exc}") from exc


def load_frozen_lp_inputs(reference: FrozenInputs) -> tuple[ValidatedSnapshot, ...]:
    """Read only the pinned selection, verifying original inputs and separate copies.

    This function never discovers runs, adds newly completed curricula, rewrites
    evidence or interprets the input manifest as evaluation completion. Returned
    payloads come from the preserved copies. The caller retains the pinned reference
    and revalidates before subsequent use.

    Parameters
    ----------
    reference
        Manifest path and digest from the consuming invocation.

    Returns
    -------
    tuple[ValidatedSnapshot, ...]
        Copy-backed payloads with their original identities and configuration.

    Raises
    ------
    LPSnapshotError
        Manifest, selected originals, stored copies or required absences changed.
    """

    try:
        manifest = _frozen_manifest(reference)
        snapshots = []

        for snapshot in manifest.snapshots:
            _frozen_source_check(snapshot)
            artifacts = tuple(
                SnapshotArtifact(
                    fingerprint=artifact.fingerprint,
                    name=artifact.name,
                    payload=_frozen_copy_read(
                        artifact=artifact, directory=reference.manifest_path.parent
                    ),
                )
                for artifact in snapshot.artifacts
            )
            snapshots.append(
                ValidatedSnapshot(
                    absent_artifacts=snapshot.absent_artifacts,
                    artifacts=artifacts,
                    checkpoint_format=snapshot.checkpoint_format,
                    config_json=snapshot.config_json,
                    run=snapshot.run,
                    source_artifact=snapshot.source_artifact,
                )
            )

        for snapshot in manifest.snapshots:
            _frozen_source_check(snapshot)

        _equal(
            actual=_frozen_manifest(reference),
            expected=manifest,
            label="Frozen manifest stability",
        )
        return tuple(snapshots)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        raise LPSnapshotError(f"Invalid frozen LP inputs: {exc}") from exc


def normalize_evaluation_text(text: str) -> str:
    """Apply Unicode NFKC, case folding and whitespace collapse in that order.

    Parameters
    ----------
    text
        Original source text.

    Returns
    -------
    str
        Deterministic normalized text used only for evaluation diagnostics.
    """

    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def prepare_evaluation_schedule(
    *,
    inputs: FrozenInputs,
    judge: ResolvedJudgeSettings,
    settings: ResolvedEvaluationSettings,
) -> EvaluationSchedule:
    """Prepare the entire frozen-input schedule without model calls or file writes.

    All settings apply uniformly to every selected curriculum. The returned plan
    contains every required call and its dependencies, ready for separate persistence
    and execution.

    Parameters
    ----------
    inputs
        Exact validated frozen selection; newly completed runs cannot be absorbed.
    judge
        Resolved evaluator-only model and finite operational settings.
    settings
        Effective controls and recorded explicit overrides.

    Returns
    -------
    EvaluationSchedule
        Complete immutable calls, conditions, replicas, mappings and dependencies.

    Raises
    ------
    ValueError
        If selected inputs, settings, required evidence, identities or source stability
        fail. No component is silently dropped or shrunk on failure.
    """

    effective = EvaluationSettings.model_validate(settings.settings.model_dump())
    expected_execution = {
        "attempt_timeout_seconds": 180,
        "concurrency": 4,
        "max_retries": 2,
        "retry_waits_seconds": [5, 20],
        "sdk_max_retries": 0,
    }
    _equal(
        actual=json.loads(judge.execution_json),
        expected=expected_execution,
        label="Judge execution controls",
    )
    snapshots = load_frozen_lp_inputs(inputs)

    if not snapshots:
        raise ValueError("Evaluation requires at least one frozen selected curriculum.")

    implementation = _schedule_implementation()
    context = _ScheduleContext(
        implementation_hash=lp_material_content_hash(
            TypeAdapter(tuple[FileFingerprint, ...]).dump_python(
                implementation, mode="json"
            )
        ),
        judge=judge,
        settings=effective,
    )
    curricula = tuple(
        _schedule_curriculum(context=context, snapshot=snapshot)
        for snapshot in sorted(
            snapshots,
            key=lambda item: (str(item.run.framework_uuid), str(item.run.doc_key)),
        )
    )
    seen: set[str] = set()
    counts: Counter[str] = Counter()

    for curriculum in curricula:
        for call in curriculum.requests:
            if call.prompt.request_id in seen or not set(call.dependencies) <= seen:
                raise ValueError(
                    "Schedule has duplicate identities or unresolved dependencies."
                )

            seen.add(call.prompt.request_id)
            counts[
                canonical_lp_json(
                    {
                        "component": call.component,
                        "condition": call.evidence.condition,
                        "presentation": call.evidence.presentation,
                        "task": call.prompt.task,
                    }
                )
            ] += 1

    if _schedule_implementation() != implementation:
        raise ValueError("Implementation changed while the schedule was prepared.")

    load_frozen_lp_inputs(inputs)
    schedule = EvaluationSchedule(
        curricula=curricula,
        implementation_fingerprints=implementation,
        inputs=inputs,
        judge=judge,
        material_content_hash="",
        request_counts=tuple(sorted(counts.items())),
        settings=settings,
        total_requests=len(seen),
    )
    material = TypeAdapter(EvaluationSchedule).dump_python(schedule, mode="json")
    del material["material_content_hash"]
    return replace(schedule, material_content_hash=lp_material_content_hash(material))


def sample_independent_pairs(
    *, population: AdmissiblePairPopulation, settings: EvaluationSettings
) -> PairSamplePlan:
    """Select upstream pairs and freeze common evidence without production access.

    Selection makes one canonical pass with bounded reservoirs. Every diagnostic tag
    has an independent quota, including when its pairs overlap the uniform cohort. The
    exact uniform inclusion fraction applies only to the uniform route. Diagnostic
    draws and the union have no asserted population-estimation probability.

    Call this before any production metadata join. The returned evidence is the common
    reconstructed bounded base for every selected pair, regardless of nomination.

    Parameters
    ----------
    population
        Complete upstream-only admissible pair universe.
    settings
        Effective validated evaluator settings shared by all selected curricula.

    Returns
    -------
    PairSamplePlan
        Frozen upstream selection, all routes, shortfalls and bounded evidence.

    Raises
    ------
    ValueError
        If settings are invalid, streamed population counts disagree, or mandatory
        bounded evidence cannot be constructed for a selected pair.
    """

    settings = EvaluationSettings.model_validate(settings.model_dump())
    routes = ("independent/uniform",) + tuple(
        f"independent/tag/{tag}" for tag in UPSTREAM_DIAGNOSTIC_TAGS
    )
    targets = (settings.independent_uniform_pairs,) + (
        settings.independent_pairs_per_tag,
    ) * len(UPSTREAM_DIAGNOSTIC_TAGS)
    reservoirs = tuple(
        _sample_reservoir(
            doc_key=population.doc_key,
            framework_uuid=population.framework_uuid,
            route=route,
            seed=settings.sampling_seed,
            target=target,
        )
        for route, target in zip(routes, targets, strict=True)
    )

    for pair in population.iter_pairs():
        reservoirs[0].offer(pair)
        tags = population.tags_for_pair(pair)

        for tag, reservoir in zip(
            UPSTREAM_DIAGNOSTIC_TAGS, reservoirs[1:], strict=True
        ):
            if tag in tags:
                reservoir.offer(pair)

    if reservoirs[0].count != population.total_pairs:
        raise ValueError("Streamed admissible population count differs from its index.")

    cells = tuple(
        _sample_cell(
            population_count=reservoir.count,
            reservoir=reservoir,
            route=route,
            target=target,
            uniform=index == 0,
        )
        for index, (route, target, reservoir) in enumerate(
            zip(routes, targets, reservoirs, strict=True)
        )
    )
    unique = {
        pair.pair_id: pair for reservoir in reservoirs for pair in reservoir.pairs
    }
    pairs = tuple(
        SampledPair(
            pair=pair,
            routes=_sample_routes(cells=cells, pair_id=pair.pair_id),
            tags=population.tags_for_pair(pair),
        )
        for pair in sorted(unique.values(), key=lambda pair: pair.endpoint_uuids)
    )
    evidence = tuple(
        population._builder.build_pair(
            first_sfi_uuid=entry.pair.endpoint_uuids[0],
            second_sfi_uuid=entry.pair.endpoint_uuids[1],
        )
        for entry in pairs
    )
    return _sample_plan(
        base_evidence=evidence,
        cells=cells,
        component="independent",
        doc_key=population.doc_key,
        framework_uuid=population.framework_uuid,
        input_content_hash=population.input_content_hash,
        pairs=pairs,
        population_content_hash=population.material_content_hash,
        settings=settings,
    )


def sample_production_pairs(
    *, population: ProductionPopulation, settings: EvaluationSettings
) -> PairSamplePlan:
    """Select uniform outcomes followed by ordered deficit-only tag supplements.

    Existing matches count toward each tag target when it is processed. Additions can
    satisfy later tags; exhausted quotas are never transferred. Route credit remains
    separate from actual draws, and later incidental tag matches remain in pair tags.
    Uniform probabilities describe only the corresponding outcome cell, never the
    diagnostic supplement or combined selected population.

    Parameters
    ----------
    population
        Validated canonical production inventory bound to its original artifacts.
    settings
        Effective validated evaluator settings shared by all selected curricula.

    Returns
    -------
    PairSamplePlan
        Frozen production selection with all routes, denominators and shortfalls.

    Raises
    ------
    ValueError
        If evaluator settings are invalid.
    """

    settings = EvaluationSettings.model_validate(settings.model_dump())
    ordered = tuple(sorted(population.pairs, key=lambda item: item.pair.endpoint_uuids))
    selected: dict[str, ProductionPair] = {}
    cells: list[SampleCell] = []

    for outcome in PRODUCTION_OUTCOMES:
        route = f"production/outcome/{outcome}"
        reservoir = _sample_reservoir(
            doc_key=population.doc_key,
            framework_uuid=population.framework_uuid,
            route=route,
            seed=settings.sampling_seed,
            target=settings.production_pairs_per_outcome,
        )
        candidates = tuple(item for item in ordered if item.outcome == outcome)

        for item in candidates:
            reservoir.offer(item.pair)

        cell = _sample_cell(
            population_count=len(candidates),
            reservoir=reservoir,
            route=route,
            target=settings.production_pairs_per_outcome,
            uniform=True,
        )
        cells.append(cell)
        selected.update(
            (item.pair.pair_id, item)
            for item in candidates
            if item.pair.pair_id in cell.drawn_pair_ids
        )

    for tag in PRODUCTION_DIAGNOSTIC_TAGS:
        route = f"production/tag/{tag}"
        candidates = tuple(item for item in ordered if tag in item.tags)
        prior = tuple(item.pair for item in candidates if item.pair.pair_id in selected)
        reservoir = _sample_reservoir(
            doc_key=population.doc_key,
            framework_uuid=population.framework_uuid,
            route=route,
            seed=settings.sampling_seed,
            target=max(0, settings.production_examples_per_tag - len(prior)),
        )

        for item in candidates:
            if item.pair.pair_id not in selected:
                reservoir.offer(item.pair)

        cell = _sample_cell(
            population_count=len(candidates),
            prior=prior,
            reservoir=reservoir,
            route=route,
            target=settings.production_examples_per_tag,
        )
        cells.append(cell)
        selected.update(
            (item.pair.pair_id, item)
            for item in candidates
            if item.pair.pair_id in cell.drawn_pair_ids
        )

    frozen_cells = tuple(cells)
    pairs = tuple(
        SampledPair(
            pair=item.pair,
            routes=_sample_routes(cells=frozen_cells, pair_id=item.pair.pair_id),
            tags=item.tags,
        )
        for item in ordered
        if item.pair.pair_id in selected
    )
    return _sample_plan(
        base_evidence=(),
        cells=frozen_cells,
        component="production",
        doc_key=population.doc_key,
        framework_uuid=population.framework_uuid,
        input_content_hash=population.upstream_input_content_hash,
        pairs=pairs,
        population_content_hash=population.material_content_hash,
        settings=settings,
    )


def text_token_jaccard(*, first: str, second: str) -> Fraction:
    """Compute exact token Jaccard similarity, assigning empty unions zero.

    Parameters
    ----------
    first
        First SFI text.
    second
        Second SFI text.

    Returns
    -------
    Fraction
        Intersection over union as an exact rational diagnostic, never a truth label.
    """

    first_tokens = evaluation_token_set(first)
    second_tokens = evaluation_token_set(second)
    union = first_tokens | second_tokens

    return (
        Fraction(len(first_tokens & second_tokens), len(union))
        if union
        else Fraction(0)
    )


def upstream_evidence_source(snapshot: ValidatedSnapshot) -> UpstreamEvidenceSource:
    """Extract only upstream evidence from an already validated frozen snapshot.

    Call load_frozen_lp_inputs before use to check original and frozen material. This
    adapter neither reads nor decodes production requests or judgment payloads.

    Parameters
    ----------
    snapshot
        Copy-backed snapshot returned by frozen input loading.

    Returns
    -------
    UpstreamEvidenceSource
        Immutable upstream-only input for independent evidence construction.

    Raises
    ------
    ValueError
        If the selected snapshot lacks one upstream bundle or its framework identity.
    """

    artifacts = [
        artifact
        for artifact in snapshot.artifacts
        if artifact.name == "as_lc_kg_bundle.json"
    ]

    if (
        len(artifacts) != 1
        or snapshot.run.doc_key is None
        or snapshot.run.framework_uuid is None
    ):
        raise ValueError("Snapshot has no unique bound upstream evidence source.")

    return UpstreamEvidenceSource(
        config_json=snapshot.config_json,
        doc_key=snapshot.run.doc_key,
        framework_uuid=snapshot.run.framework_uuid,
        upstream_artifact=artifacts[0],
    )


def validate_lp_snapshot(run: DiscoveredRun) -> ValidatedSnapshot:
    """Validate one completed run for evaluation without writing or invoking production.

    Original byte payloads, relative names, resolved paths and absences are returned
    for a separate freezing boundary. Passing this function establishes input
    consistency; freezing and pedagogical assessment are separate operations.
    Producing Git provenance is retained if recorded and is never required.

    Parameters
    ----------
    run
        Completed candidate from recursive discovery.

    Returns
    -------
    ValidatedSnapshot
        Captured material and configuration suitable for subsequent snapshot freezing.

    Raises
    ------
    LPSnapshotError
        A selected run is active, incomplete, changed, unavailable or inconsistent.
    ValueError
        Run is not a completed candidate or lacks a document identity.
    """

    try:
        if run.status != "completed_candidate":
            raise ValueError("Only completed candidates may be validated")

        reader = _SnapshotReader(run.kgs_directory)

        for fingerprint in run.fingerprints:
            name = fingerprint.path.relative_to(run.kgs_directory).as_posix()
            reader.read(name)
            _equal(
                actual=reader.artifacts[name].fingerprint,
                expected=fingerprint,
                label=f"Discovery metadata {name}",
            )

        with _snapshot_ownership(reader):
            current = _classify_run(
                kgs_directory=run.kgs_directory, results_root=run.kgs_directory
            )
            _equal(
                actual=replace(current, aliases=run.aliases),
                expected=run,
                label="Selected completion state",
            )
            config = _snapshot_config(reader)
            source_name = _snapshot_source(config=config, reader=reader, run=run)
            upstream = _snapshot_upstream(config=config, reader=reader)

            if run.doc_key is None:
                raise ValueError("Missing document identity")

            population = _snapshot_population(
                config=config, doc_key=run.doc_key, reader=reader, upstream=upstream
            )

            for name in (
                *population.manifest.artifact_byte_hashes,
                "lp_generation_requests_manifest.json",
            ):
                reader.read(name)

            format_name, rows = _snapshot_execution(
                config=config, population=population, reader=reader
            )
            claims = LPFinalClaims.model_validate(reader.read("lp_final_claims.json"))
            _equal(
                actual=claims.request_manifest,
                expected=population.manifest,
                label="Final request manifest",
            )
            _equal(
                actual=claims.execution_material,
                expected=reader.read("lp_generation_checkpoint_manifest.json")[
                    "material"
                ],
                label="Final execution material",
            )
            _equal(
                actual=claims.checkpoint_receipt_byte_hash,
                expected=reader.artifacts[
                    "lp_generation_checkpoint_manifest.json"
                ].fingerprint.sha256,
                label="Final receipt binding",
            )
            _checkpoint_claims(
                claims=claims, population=population, reader=reader, rows=rows
            )
            relationships = _snapshot_relationships(
                claims=claims, config=config, reader=reader, upstream=upstream
            )
            artifacts = _snapshot_standalone(
                claims=claims,
                config=config,
                reader=reader,
                relationships=relationships,
                upstream=upstream,
            )
            _snapshot_graphs(
                artifacts=artifacts, config=config, reader=reader, upstream=upstream
            )
            reader.check_unchanged()

        return ValidatedSnapshot(
            absent_artifacts=tuple(sorted(reader.absent)),
            artifacts=tuple(
                reader.artifacts[name] for name in sorted(reader.artifacts)
            ),
            checkpoint_format=format_name,
            config_json=canonical_lp_json(config.model_dump(mode="json")),
            run=run,
            source_artifact=source_name,
        )
    except (
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        QualityError,
    ) as exc:
        raise LPSnapshotError(
            f"Invalid completed LP snapshot {run.kgs_directory}: {exc}"
        ) from exc


def validate_lp_snapshots(
    inventory: DiscoveryInventory,
) -> tuple[ValidatedSnapshot, ...]:
    """Validate every selected completed candidate, failing the invocation on any error.

    Parameters
    ----------
    inventory
        Recursive discovery inventory, including explicitly excluded unfinished runs.

    Returns
    -------
    tuple[ValidatedSnapshot, ...]
        Validated material in deterministic discovery order, pending freezing.

    Raises
    ------
    LPSnapshotError
        No completed candidate exists, identities conflict, or any selected input
        fails validation. Partial success is never returned.
    """

    selected = tuple(
        run for run in inventory.runs if run.status == "completed_candidate"
    )

    if not selected:
        raise LPSnapshotError("No completed LP snapshot was selected")

    identities: dict[tuple[str, str], Path] = {}

    try:
        for run in selected:
            _register_run_identity(identities=identities, run=run)
    except LPDiscoveryError as exc:
        raise LPSnapshotError(str(exc)) from exc

    return tuple(validate_lp_snapshot(run) for run in selected)
