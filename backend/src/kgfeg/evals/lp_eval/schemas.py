"""Typed input and configuration records for Learning Progressions evaluation."""

# Future Library
from __future__ import annotations

# Standard Library
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Self
from uuid import UUID

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field, model_validator


@dataclass(frozen=True, slots=True)
class DiscoveredRun:
    """One run's recorded completion state, not a validated evaluation snapshot.

    Attributes
    ----------
    aliases
        Additional discovered paths resolving to this physical run directory.
    completed_at
        Recorded completion timestamp, when present.
    doc_key
        Claimed document identity for a completed candidate, pending validation.
    fingerprints
        Byte identities of the metadata read during discovery.
    framework_uuid
        Claimed framework identity, pending full artifact validation.
    kgs_directory
        Resolved physical run directory.
    reason
        Explanation of candidate inclusion or run exclusion.
    run_id
        Identity from the recorded KG execution metadata.
    started_at
        Recorded execution start.
    status
        Preliminary completion classification; never evaluation readiness.
    """

    aliases: tuple[Path, ...]
    completed_at: datetime | None
    doc_key: str | None
    fingerprints: tuple[FileFingerprint, ...]
    framework_uuid: UUID | None
    kgs_directory: Path
    reason: str
    run_id: UUID
    started_at: datetime
    status: Literal["completed_candidate", "failed", "unfinished"]


@dataclass(frozen=True, slots=True)
class DiscoveryAlias:
    """A directory alias observed without repeatedly traversing its target.

    Attributes
    ----------
    discovered_path
        Path encountered during traversal.
    resolved_path
        Physical path used for traversal and containment checks.
    """

    discovered_path: Path
    resolved_path: Path


@dataclass(frozen=True, slots=True)
class DiscoveryInventory:
    """Discovery evidence to pass to full snapshot validation.

    Attributes
    ----------
    aliases
        Observed directory aliases, including cycles that were not traversed again.
    evaluation_root
        Resolved evaluator output subtree excluded from traversal.
    results_root
        Resolved starting directory.
    runs
        One entry per physical run, sorted by resolved path.
    skipped_paths
        Output or external-symlink subtrees excluded from traversal.
    """

    aliases: tuple[DiscoveryAlias, ...]
    evaluation_root: Path
    results_root: Path
    runs: tuple[DiscoveredRun, ...]
    skipped_paths: tuple[DiscoverySkip, ...]


@dataclass(frozen=True, slots=True)
class DiscoverySkip:
    """A traversal exclusion, distinct from an incomplete curriculum run.

    Attributes
    ----------
    path
        Encountered path that was not traversed.
    reason
        Explicit output-subtree or external-symlink exclusion reason.
    """

    path: Path
    reason: Literal["evaluation_output", "outside_results_root"]


@dataclass(frozen=True, slots=True)
class EvaluationPair:
    """Canonical framework-contained pair identity without a semantic label.

    Attributes
    ----------
    endpoint_uuids
        Lower and higher canonical SFI CASE UUIDs.
    pair_id
        Document-scoped unordered pair identifier.
    """

    endpoint_uuids: tuple[UUID, UUID]
    pair_id: str


class EvaluationSettings(BaseModel):
    """Validated invocation-wide sampling and repetition controls.

    All counts apply uniformly across selected curricula. Empty populations produce
    recorded shortfalls; settings never disable a required component. Explicitly
    supplied values can equal defaults and remain recorded as overrides.

    Attributes
    ----------
    additional_diagnostic_replicates
        Additional fresh judgments with identical base presentation; base plus
        additional must be at least two.
    base_blind_replicates
        Blind judgments per real pair in each component's required base condition.
    critique_replicates
        Separate operative-rationale critiques per selected production pair.
    diagnostic_pairs_per_cohort
        Target pairs for repetition and all five variants in each cohort per curriculum.
    independent_pairs_per_tag
        Independent without-replacement target per upstream tag per curriculum.
    independent_uniform_pairs
        Uniform admissible upstream pair target per curriculum.
    lexical_baseline_top_k
        Lexical ranking target per cohort, capped by its available sampled population.
    production_examples_per_tag
        Minimum examples per production tag, counting already-selected pairs.
    production_pairs_per_outcome
        Uniform without-replacement target for each production outcome per curriculum.
    sampling_seed
        Integer seed for all deterministic selection and ordering.
    synthetic_cases_per_family
        Constructed cases per required control family per curriculum.
    synthetic_control_replicates
        Fresh judgments per constructed synthetic control.
    variant_replicates
        Fresh judgments per diagnostic pair for each of the five required variants.
    """

    additional_diagnostic_replicates: int = Field(
        default=2,
        description="Additional fresh judgments with identical base presentation; base plus additional must be at least two.",
        ge=0,
    )
    base_blind_replicates: int = Field(
        default=1,
        description="Blind judgments per real pair in each component's required base condition.",
        ge=1,
    )
    critique_replicates: int = Field(
        default=1,
        description="Separate operative-rationale critiques per selected production pair.",
        ge=1,
    )
    diagnostic_pairs_per_cohort: int = Field(
        default=12,
        description="Target pairs for repetition and all five variants in each cohort per curriculum.",
        ge=1,
    )
    independent_pairs_per_tag: int = Field(
        default=3,
        description="Independent without-replacement target per upstream tag per curriculum.",
        ge=1,
    )
    independent_uniform_pairs: int = Field(
        default=36,
        description="Uniform admissible upstream pair target per curriculum.",
        ge=1,
    )
    lexical_baseline_top_k: int = Field(
        default=10,
        description="Lexical ranking target per cohort, capped by its available sampled population.",
        ge=1,
    )
    production_examples_per_tag: int = Field(
        default=2,
        description="Minimum examples per production tag, counting already-selected pairs.",
        ge=1,
    )
    production_pairs_per_outcome: int = Field(
        default=15,
        description="Uniform without-replacement target for each production outcome per curriculum.",
        ge=1,
    )
    sampling_seed: int = Field(
        default=20260911,
        description="Integer seed for all deterministic selection and ordering.",
    )
    synthetic_cases_per_family: int = Field(
        default=5,
        description="Constructed cases per required control family per curriculum.",
        ge=1,
    )
    synthetic_control_replicates: int = Field(
        default=3,
        description="Fresh judgments per constructed synthetic control.",
        ge=1,
    )
    variant_replicates: int = Field(
        default=1,
        description="Fresh judgments per diagnostic pair for each of the five required variants.",
        ge=1,
    )

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, validate_default=True
    )

    @model_validator(mode="after")
    def _validate_diagnostic_replicates(self) -> Self:
        """Require repeated judgments for every diagnostic pair.

        Returns
        -------
        Self
            The validated settings.

        Raises
        ------
        ValueError
            If base and additional counts cannot produce at least two judgments.
        """

        if self.base_blind_replicates + self.additional_diagnostic_replicates < 2:
            raise ValueError(
                "Base blind plus additional diagnostic replicates must be at least 2."
            )

        return self


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    """Actual input bytes observed at a resolved path.

    Attributes
    ----------
    path
        Resolved input file path.
    sha256
        SHA-256 digest of the exact bytes read.
    size_bytes
        Length of the bytes hashed.
    """

    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class FrozenArtifact:
    """One preserved artifact's original location and content-addressed copy identity.

    Attributes
    ----------
    fingerprint
        Original resolved path, SHA-256 and byte length.
    name
        Original run-relative artifact name; never used as an output filename.
    """

    fingerprint: FileFingerprint
    name: str


@dataclass(frozen=True, slots=True)
class FrozenInputManifest:
    """Fixed input selection, independent of subsequent discovery or evaluation calls.

    Attributes
    ----------
    inventory
        Original discovery inventory, including excluded runs and directory aliases.
    kind
        Identifies this input artifact without claiming evaluation completion.
    snapshots
        Validated curricula and their exact preserved material bindings.
    """

    inventory: DiscoveryInventory
    kind: Literal["lp_evaluation_inputs"]
    snapshots: tuple[FrozenSnapshot, ...]


@dataclass(frozen=True, slots=True)
class FrozenInputs:
    """Pinned reference used to reject edited or substituted input manifests.

    Attributes
    ----------
    content_hash
        SHA-256 of the complete canonical manifest bytes.
    manifest_path
        Exact absolute path of the published manifest.
    """

    content_hash: str
    manifest_path: Path


@dataclass(frozen=True, slots=True)
class FrozenSnapshot:
    """A completed curriculum's exact material bindings without mutable payloads.

    Attributes
    ----------
    absent_artifacts
        Optional and transaction artifacts recorded absent during validation.
    artifacts
        Original names and byte identities, in deterministic name order.
    checkpoint_format
        Validated historical-prefix or journal-bearing interpretation.
    config_json
        Captured effective configuration in its original serialization shape.
    run
        Original completion and framework identity.
    source_artifact
        Original run-relative source DocumentIR path.
    """

    absent_artifacts: tuple[str, ...]
    artifacts: tuple[FrozenArtifact, ...]
    checkpoint_format: str
    config_json: str
    run: DiscoveredRun
    source_artifact: str


@dataclass(frozen=True, slots=True)
class PairSamplePlan:
    """Immutable selection and evidence binding for one framework and component.

    Attributes
    ----------
    algorithm
        Recorded canonical reservoir algorithm and random stream derivation.
    base_evidence
        Independent common bounded views, in selected-pair order; empty for production.
    cells
        Ordered uniform cells followed by diagnostic cells, including empty cells.
    component
        Independent upstream or production assessment; overlaps remain separate.
    doc_key
        Input document identity.
    framework_uuid
        Framework containing every selected pair.
    input_content_hash
        Upstream material identity.
    material_content_hash
        Hash of the complete plan excluding this field.
    pairs
        Canonical unique pairs with all routes retained.
    population_content_hash
        Identity of the complete population from which selection was made.
    settings_json
        Canonical effective evaluator controls, including the seed.
    """

    algorithm: str
    base_evidence: tuple[UpstreamEvidenceView, ...]
    cells: tuple[SampleCell, ...]
    component: Literal["independent", "production"]
    doc_key: str
    framework_uuid: UUID
    input_content_hash: str
    material_content_hash: str
    pairs: tuple[SampledPair, ...]
    population_content_hash: str
    settings_json: str


@dataclass(frozen=True, slots=True)
class ProductionPair:
    """Production comparison metadata, never an independent truth label.

    Attributes
    ----------
    checker_outcome
        Original accepted or corrected checker status.
    direction
        Final production direction, when applicable.
    outcome
        Published relationship type or final nonpublishing production outcome.
    pair
        Canonical pair identity.
    producer_direction
        Original producer direction, when applicable.
    producer_outcome
        Original producer decision.
    producer_to_final_changes
        Names of judgment fields changed by reconciliation, including rationale changes.
    published_relationship_uuid
        Actual published relationship identifier; absent for nonpublishing outcomes.
    request_content_hash
        Original bounded request material hash.
    request_id
        Original request identifier.
    tags
        Applicable production diagnostic tags in the prescribed order.
    """

    checker_outcome: Literal["accepted", "corrected"]
    direction: str | None
    outcome: Literal["buildsTowards", "relatesTo", "no_relation", "needs_review"]
    pair: EvaluationPair
    producer_direction: str | None
    producer_outcome: str
    producer_to_final_changes: tuple[str, ...]
    published_relationship_uuid: UUID | None
    request_content_hash: str
    request_id: UUID
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProductionPopulation:
    """Complete production comparison inventory bound to exact frozen artifacts.

    Attributes
    ----------
    artifact_fingerprints
        Exact material read to derive production outcomes and truncation tags.
    doc_key
        Validated document identity.
    framework_uuid
        Framework containing every pair.
    material_content_hash
        Content identity covering inputs and the complete ordered inventory.
    outcome_counts
        Counts for all four outcomes, retaining zero cells.
    pairs
        Unique production pairs ordered by canonical endpoint UUIDs.
    tag_counts
        Counts for all twelve production tags, retaining zero cells.
    upstream_input_content_hash
        Exact upstream/configuration binding shared with the admissible population.
    """

    artifact_fingerprints: tuple[FileFingerprint, ...]
    doc_key: str
    framework_uuid: UUID
    material_content_hash: str
    outcome_counts: tuple[tuple[str, int], ...]
    pairs: tuple[ProductionPair, ...]
    tag_counts: tuple[tuple[str, int], ...]
    upstream_input_content_hash: str


@dataclass(frozen=True, slots=True)
class ResolvedEvaluationSettings:
    """Effective controls and the names explicitly overridden by the caller.

    Attributes
    ----------
    overrides
        Sorted explicitly supplied field names, including values equal to defaults.
        Their effective values are retained in settings.
    settings
        Complete immutable validated settings, including all defaults.
    """

    overrides: tuple[str, ...]
    settings: EvaluationSettings


@dataclass(frozen=True, slots=True)
class ResolvedJudgeSettings:
    """Non-secret model and execution material captured without creating a client.

    Attributes
    ----------
    execution_json
        Canonical fixed concurrency, timeout, retry and retry-wait settings.
    model
        Exact resolved provider-prefixed model identifier.
    model_config_json
        Complete canonical shared model configuration for reproducible resolution.
    model_settings_json
        Canonical effective learning-progressions settings from the shared registry.
    provider
        Provider resolved from the explicit model identifier.
    """

    execution_json: str
    model: str
    model_config_json: str
    model_settings_json: str
    provider: str


@dataclass(frozen=True, slots=True)
class SampleCell:
    """One sampling route with explicit quota and population accounting.

    Attributes
    ----------
    drawn_pair_ids
        Actual without-replacement draws, in canonical endpoint order.
    inclusion_probability
        Reduced numerator/denominator for a uniform cell; None for diagnostics or an
        empty population. Never a probability for the deduplicated union.
    population_count
        Complete number of matching pairs before selection.
    route
        Component-qualified outcome, uniform or diagnostic route.
    selected_pair_ids
        Cell members: draws for independent cells; prior members plus additions for
        production supplements, counted when that supplement was processed.
    shortfall
        Unfilled target after exhausting available candidates, without reallocation.
    target
        Configured target; production supplements count previously selected pairs.
    """

    drawn_pair_ids: tuple[str, ...]
    inclusion_probability: tuple[int, int] | None
    population_count: int
    route: str
    selected_pair_ids: tuple[str, ...]
    shortfall: int
    target: int


@dataclass(frozen=True, slots=True)
class SampledPair:
    """One selected pair and all of its sampling memberships.

    Attributes
    ----------
    pair
        Canonical endpoint identities without a pedagogical truth label.
    routes
        All selecting or supplement-credit routes in cell processing order.
    tags
        All diagnostic tags, including tags that did not select this pair.
    """

    pair: EvaluationPair
    routes: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SnapshotArtifact:
    """Exact input bytes retained separately from production and evaluator outputs.

    Attributes
    ----------
    fingerprint
        Actual resolved path, byte length and SHA-256.
    name
        Path relative to the run directory, including contained source evidence.
    payload
        Original bytes, without normalization or historical schema migration.
    """

    fingerprint: FileFingerprint
    name: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class UpstreamEvidenceSource:
    """Isolated upstream inputs with no production nomination or judgment artifacts.

    Attributes
    ----------
    config_json
        Captured effective configuration, preserving its historical field shape.
    doc_key
        Validated document identity.
    framework_uuid
        Validated framework identity.
    upstream_artifact
        Exact frozen AS+LC bundle bytes and their original fingerprint.
    """

    config_json: str
    doc_key: str
    framework_uuid: UUID
    upstream_artifact: SnapshotArtifact


@dataclass(frozen=True, slots=True)
class UpstreamEvidenceView:
    """An immutable pair evidence view, separate from prompts and production answers.

    Attributes
    ----------
    audit_json
        Canonical construction, omission and removal audit; never judge evidence.
    condition
        Common reconstructed base or a separately identified diagnostic condition.
    doc_key
        Framework-contained document identity.
    endpoint_uuids
        Canonical lower and higher SFI CASE UUIDs.
    framework_uuid
        Framework containing both endpoints.
    input_content_hash
        Identity of the exact upstream bytes and captured configuration.
    material_content_hash
        Identity of the payload, condition, references, limits and construction audit.
    pair_id
        Stable unordered pair identity, without a production nomination claim.
    payload_json
        Canonical bounded judge-visible facts and policy, without the audit.
    payload_sha256
        SHA-256 of the exact UTF-8 payload.
    references
        JSON pointers into shown evidence, usable for contained judge citations.
    """

    audit_json: str
    condition: Literal[
        "reconstructed_bounded_upstream",
        "expanded_upstream",
        "lc_removed",
        "hierarchy_removed",
    ]
    doc_key: str
    endpoint_uuids: tuple[UUID, UUID]
    framework_uuid: UUID
    input_content_hash: str
    material_content_hash: str
    pair_id: str
    payload_json: str
    payload_sha256: str
    references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ValidatedSnapshot:
    """Validated material awaiting a separate immutable snapshot-freezing operation.

    Attributes
    ----------
    absent_artifacts
        Optional or transaction artifacts whose absence was verified.
    artifacts
        Exact captured material, sorted by run-relative name.
    checkpoint_format
        Explicit historical-prefix or journal-bearing interpretation.
    config_json
        Validated effective configuration in its original serialization shape.
    run
        Original discovery identity and completion evidence.
    source_artifact
        Run-relative source DocumentIR path, retaining relocated-path provenance.
    """

    absent_artifacts: tuple[str, ...]
    artifacts: tuple[SnapshotArtifact, ...]
    checkpoint_format: str
    config_json: str
    run: DiscoveredRun
    source_artifact: str


def resolve_evaluation_settings(
    overrides: Mapping[str, int] | None = None,
) -> ResolvedEvaluationSettings:
    """Resolve defaults and supplied overrides without reading curriculum settings.

    Parameters
    ----------
    overrides
        Explicit integer controls keyed by EvaluationSettings field name. Omit fields
        to use defaults; boolean, string and fractional values are invalid.

    Returns
    -------
    ResolvedEvaluationSettings
        Complete validated controls and sorted explicit override names.
    """

    supplied = {} if overrides is None else dict(overrides)
    settings = EvaluationSettings.model_validate(supplied)
    return ResolvedEvaluationSettings(
        overrides=tuple(sorted(supplied)), settings=settings
    )
