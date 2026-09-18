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


class AttemptEvent(BaseModel):
    """Append-only start or terminal evidence for one scheduled API attempt.

    A durable start without a terminal event represents an interrupted, possibly billed
    attempt. It cannot be mistaken for an unattempted request or a success.
    """

    attempt_number: int = Field(ge=1, le=3)
    event: Literal["started", "succeeded", "failed"]
    failure_category: (
        Literal[
            "timeout",
            "rate_limit",
            "server_error",
            "invalid_output",
            "interrupted",
            "authentication",
            "configuration",
            "invalid_input",
            "transport",
        ]
        | None
    ) = None
    failure_message: str | None = None
    judgment_json: str | None = None
    raw_response: str | None = None
    request_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    timestamp: datetime
    usage: JudgeUsage | None = None

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="after")
    def _validate_event(self) -> Self:
        """Require timezone-aware times and disjoint start/success/failure payloads.

        Returns
        -------
        Self
            Structurally complete attempt event.

        Raises
        ------
        ValueError
            If the event mixes outcomes, omits accounting or has a naive timestamp.
        """

        if self.timestamp.utcoffset() is None:
            raise ValueError("Attempt timestamp must include a timezone.")

        if self.event == "started":
            if any(
                value is not None
                for value in (
                    self.failure_category,
                    self.failure_message,
                    self.judgment_json,
                    self.raw_response,
                    self.usage,
                )
            ):
                raise ValueError("A start event cannot contain an outcome.")
        elif self.usage is None:
            raise ValueError(
                "Every terminal attempt requires explicit usage accounting."
            )
        elif self.event == "succeeded":
            if (
                self.judgment_json is None
                or self.failure_category is not None
                or self.failure_message is not None
            ):
                raise ValueError("Successful attempts require only a valid judgment.")
        elif (
            self.judgment_json is not None
            or self.failure_category is None
            or not self.failure_message
            or not self.failure_message.strip()
        ):
            raise ValueError(
                "Failed attempts require a category and meaningful explanation."
            )

        return self


class ClassificationJudgment(BaseModel):
    """Independent relationship assessment, never a production truth label.

    Evidence references are JSON pointers into the shown payload. Scheduled identity,
    permissions and reference containment require request-relative validation.
    """

    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    decision: Literal["buildsTowards", "relatesTo", "no_relation", "ambiguous"]
    direction: Literal["first_to_second", "second_to_first"] | None
    evidence_references: tuple[str, ...] = Field(min_length=1)
    explanation: str = Field(min_length=1)
    first_sfi_uuid: UUID
    pair_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    second_sfi_uuid: UUID

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="after")
    def _validate_assessment(self) -> Self:
        """Require distinct endpoints, meaningful text and relation-direction shape.

        Returns
        -------
        Self
            Intrinsically consistent assessment, pending request-relative checks.

        Raises
        ------
        ValueError
            If endpoint identity, text, citations or direction shape is invalid.
        """

        if self.first_sfi_uuid == self.second_sfi_uuid:
            raise ValueError("Judgment endpoints must be distinct.")

        if (self.decision == "buildsTowards") != (self.direction is not None):
            raise ValueError("Only a developmental assessment has a direction.")

        _validate_judgment_text(
            references=self.evidence_references,
            texts=(self.explanation, self.pair_id, self.request_id),
        )
        return self


class ConcernDisposition(BaseModel):
    """User-recorded disposition bound to one immutable report and concern group."""

    authority: Literal["IDinsight project user"]
    concern_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    disposition: Literal["acknowledged", "investigate", "remediation_requested"]
    rationale: str = Field(min_length=1)
    report_json_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_markdown_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    timestamp: datetime

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="after")
    def _validate_record(self) -> Self:
        """Require accountable rationale and an unambiguous timestamp.

        Returns
        -------
        Self
            Structurally valid record, pending report-relative validation.

        Raises
        ------
        ValueError
            If the rationale is blank or the timestamp lacks a timezone.
        """

        if not self.rationale.strip() or self.timestamp.utcoffset() is None:
            raise ValueError("Disposition requires rationale and a zoned timestamp.")

        return self


class CritiqueJudgment(BaseModel):
    """Grounding of the operative rationale, separate from blind classification.

    Request-relative validation must check identity and every claim's references.
    Confidence is uncalibrated judge self-report, never a release threshold.
    """

    claims: tuple[RationaleClaimAssessment, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    explanation: str = Field(min_length=1)
    first_sfi_uuid: UUID
    grounding: Literal["grounded", "partially_grounded", "unsupported", "ambiguous"]
    pair_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    second_sfi_uuid: UUID

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="after")
    def _validate_assessment(self) -> Self:
        """Require distinct endpoints and grounding consistent with claim support.

        Returns
        -------
        Self
            Intrinsically valid critique, pending source/identity validation.

        Raises
        ------
        ValueError
            If identity, required text or aggregate grounding is inconsistent.
        """

        if self.first_sfi_uuid == self.second_sfi_uuid:
            raise ValueError("Critique endpoints must be distinct.")

        support_states = {claim.support for claim in self.claims}

        if self.grounding == "grounded" and support_states != {"supported"}:
            raise ValueError("A grounded rationale must support every material claim.")

        if self.grounding == "partially_grounded" and (
            "supported" not in support_states or support_states == {"supported"}
        ):
            raise ValueError(
                "A partially grounded rationale must support some but not all claims."
            )

        # A contradicted central justification can outweigh other supported claims;
        # whether it is central remains the critic's evidence-based assessment.
        if (
            self.grounding == "unsupported"
            and "supported" in support_states
            and "contradicted" not in support_states
        ):
            raise ValueError(
                "An unsupported rationale with supported claims needs a contradiction."
            )

        _validate_judgment_text(
            references=(), texts=(self.explanation, self.pair_id, self.request_id)
        )
        return self


@dataclass(frozen=True, slots=True)
class CurriculumSchedule:
    """Complete pre-call work and selection evidence for one framework.

    Attributes
    ----------
    controls
        Constructed controls with hidden expectations.
    correction_audits
        Original producer/final provenance keyed by production pair identity.
    diagnostic_cells
        Uniform within-cohort diagnostic selections and counted shortfalls.
    doc_key
        Selected document identity.
    framework_uuid
        Shared framework for this curriculum's real and constructed pairs.
    independent_sample
        Independently frozen selection and common evidence before production joins.
    production_population
        Original production metadata for later comparison, never prompt truth labels.
    production_sample
        Production selection with all routes and shortfalls.
    requests
        Ordered complete scheduled calls; no result-dependent expansion.
    """

    controls: tuple[SyntheticControl, ...]
    correction_audits: tuple[tuple[str, str], ...]
    diagnostic_cells: tuple[SampleCell, ...]
    doc_key: str
    framework_uuid: UUID
    independent_sample: PairSamplePlan
    production_population: ProductionPopulation
    production_sample: PairSamplePlan
    requests: tuple[ScheduledRequest, ...]


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
class EvaluationCache:
    """Validated outcomes and complete attempt history from a frozen invocation.

    Attributes
    ----------
    events
        Ordered, immutable start and terminal events, including every failed attempt.
    judgments
        Successful scheduled responses, retaining every replicate separately.
    unfinished
        Started attempts without a terminal event; their usage remains unknown.
    """

    events: tuple[AttemptEvent, ...]
    judgments: tuple[ClassificationJudgment | CritiqueJudgment, ...]
    unfinished: tuple[AttemptEvent, ...]


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


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Deterministic scoring outputs, retaining every request and attempt.

    Attributes
    ----------
    cache_content_hash
        Identity of the validated ledger and successful responses.
    failures_jsonl
        Failed, unfinished and unattempted work with later-resolution status.
    judgments_jsonl
        Every valid displayed and canonical assessment, separately identified.
    report_json
        Complete counts, comparisons, baseline rankings and concern memberships.
    schedule_content_hash
        Exact frozen schedule scored.
    scorer_sha256
        Actual scoring source byte identity.
    usage_json
        Every attempt and grouped known/unknown accounting.
    """

    cache_content_hash: str
    failures_jsonl: str
    judgments_jsonl: str
    report_json: str
    schedule_content_hash: str
    scorer_sha256: str
    usage_json: str


@dataclass(frozen=True, slots=True)
class EvaluationReportArtifacts:
    """Pinned immutable report generation, separate from the execution cache.

    Attributes
    ----------
    directory
        Exact content-addressed report directory under its invocation.
    manifest_sha256
        SHA-256 of the manifest listing every required report artifact.
    """

    directory: Path
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class EvaluationSchedule:
    """Complete immutable execution plan prepared without model calls.

    Attributes
    ----------
    curricula
        All selected curricula and every required component.
    implementation_fingerprints
        Actual local package source files used to construct this plan.
    inputs
        Exact frozen input manifest; no discovery or selection expansion on resume.
    judge
        Effective non-secret provider/model/settings and finite execution controls.
    material_content_hash
        Identity of the complete plan excluding this field.
    request_counts
        Counts by component, condition, presentation and task, encoded as JSON keys.
    settings
        Effective controls and explicit override names.
    total_requests
        Exact number of scheduled calls, excluding possible retry attempts.
    """

    curricula: tuple[CurriculumSchedule, ...]
    implementation_fingerprints: tuple[FileFingerprint, ...]
    inputs: FrozenInputs
    judge: ResolvedJudgeSettings
    material_content_hash: str
    request_counts: tuple[tuple[str, int], ...]
    settings: ResolvedEvaluationSettings
    total_requests: int


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
class EvaluationStore:
    """Pinned manifest reference for an invocation, independent of discovery.

    Attributes
    ----------
    content_hash
        SHA-256 of the canonical invocation manifest.
    manifest_path
        Absolute invocation manifest path under the evaluator output directory.
    """

    content_hash: str
    manifest_path: Path


@dataclass(frozen=True, slots=True)
class EvaluationStoreManifest:
    """Exact frozen schedule location and deterministic invocation lookup identity.

    Attributes
    ----------
    inputs
        Original frozen input selection.
    kind
        Explicit version of this storage contract.
    schedule_content_hash
        Material identity of the full schedule.
    schedule_sha256
        SHA-256 of the compressed schedule bytes.
    selector_hash
        Starting-directory and effective-settings identity, never a latest-run hint.
    """

    inputs: FrozenInputs
    kind: Literal["lp_evaluation_store_v1"]
    schedule_content_hash: str
    schedule_sha256: str
    selector_hash: str


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
        Validated current journal-bearing checkpoint contract.
    config_json
        Captured effective configuration in its original serialization shape.
    run
        Original completion and framework identity.
    source_artifact
        Original run-relative source DocumentIR path.
    """

    absent_artifacts: tuple[str, ...]
    artifacts: tuple[FrozenArtifact, ...]
    checkpoint_format: Literal["journal_bearing"]
    config_json: str
    run: DiscoveredRun
    source_artifact: str


@dataclass(frozen=True, slots=True)
class JudgePrompt:
    """Exact prompt and response schema, with audit identity outside message text.

    Attributes
    ----------
    evidence_content_hash
        Bound evidence-view identity; hidden from the response rubric.
    material_content_hash
        Hash covering both messages, schema and all binding fields.
    messages_content_hash
        Hash of the exact system and user messages, without hidden audit material.
    pair_id
        Assessed canonical pair.
    references
        Permitted pointers relative to the shown evidence object.
    request_id
        Opaque caller-supplied scheduled request identity.
    response_schema_json
        Exact canonical structured-output schema.
    response_schema_sha256
        SHA-256 of that schema.
    system_message
        Task-specific instructions without control expectations or earlier answers.
    task
        Classification or grounding critique.
    user_message
        Evidence, response identity and permitted citations only.
    """

    evidence_content_hash: str
    material_content_hash: str
    messages_content_hash: str
    pair_id: str
    references: tuple[str, ...]
    request_id: str
    response_schema_json: str
    response_schema_sha256: str
    system_message: str
    task: Literal["classification", "critique"]
    user_message: str


@dataclass(frozen=True, slots=True)
class JudgeReply:
    """One transport response before scheduled output validation.

    Attributes
    ----------
    response_json
        Returned native JSON text or output-tool arguments before scheduled validation.
    usage
        Available accounting from this attempt, including unknown values.
    """

    response_json: str
    usage: JudgeUsage


class JudgeUsage(BaseModel):
    """Observed per-attempt accounting; unavailable values remain explicitly null.

    Token categories are provider observations and need not be additive. Cost is
    recorded only when supplied, with its currency; no price estimate is required.
    """

    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cost_currency: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    provider_model: str | None = None
    provider_request_id: str | None = None
    reasoning_tokens: int | None = Field(default=None, ge=0)

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="after")
    def _validate_cost(self) -> Self:
        """Keep observed cost and its currency together.

        Returns
        -------
        Self
            Accounting with unknown values preserved.

        Raises
        ------
        ValueError
            If a cost lacks a currency or metadata contains blank strings.
        """

        if (self.cost is None) != (self.cost_currency is None):
            raise ValueError("Observed cost and currency must be supplied together.")

        for value in (
            self.cost_currency,
            self.provider_model,
            self.provider_request_id,
        ):
            if value is not None and not value.strip():
                raise ValueError("Usage metadata cannot contain blank strings.")

        return self


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
class ProductionEvidenceView:
    """One immutable judge-visible production view with a separate construction audit.

    Attributes
    ----------
    audit_json
        Field mappings and original material bindings, never judge-visible evidence.
    endpoint_uuids
        Canonical assessed endpoints.
    input_content_hash
        Complete source artifact and configuration identity.
    material_content_hash
        View identity covering payload, audit, pair and condition.
    pair_id
        Assessed production pair.
    payload_json
        Exact canonical evidence payload to render in a fresh judge context.
    payload_sha256
        SHA-256 of the payload's UTF-8 bytes.
    references
        JSON pointers to evidence available in this view.
    request_content_hash
        Original bounded request material identity.
    request_id
        Original batch identity.
    view
        Classification and critique remain distinct even for the same pair.
    """

    audit_json: str
    endpoint_uuids: tuple[UUID, UUID]
    input_content_hash: str
    material_content_hash: str
    pair_id: str
    payload_json: str
    payload_sha256: str
    references: tuple[str, ...]
    request_content_hash: str
    request_id: UUID
    view: Literal["production_blind", "original_production_critique"]


@dataclass(frozen=True, slots=True)
class ProductionEvidenceViews:
    """Separate classification/critique evidence and hidden correction diagnostics.

    Preparation may freeze both views before calls. Execution must freeze the validated
    blind response before dispatching critique, without supplying that response to it.

    Attributes
    ----------
    blind
        Facts and policy with production answers and nomination advice withheld.
    correction_audit_json
        Original producer judgment, operative judgment and change provenance. Retained
        for diagnostics, never appended to the blind or default critic input.
    critique
        Original bounded batch, original instructions and operative final judgment.
    """

    blind: ProductionEvidenceView
    correction_audit_json: str
    critique: ProductionEvidenceView


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


class RationaleClaimAssessment(BaseModel):
    """One material rationale claim and its grounding in shown evidence only."""

    claim: str = Field(min_length=1)
    evidence_references: tuple[str, ...]
    explanation: str = Field(min_length=1)
    support: Literal["supported", "unsupported", "contradicted", "unresolved"]

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="after")
    def _validate_claim(self) -> Self:
        """Require useful claim text and citations for assertions of support.

        Returns
        -------
        Self
            Claim assessment with valid text and citation shape.

        Raises
        ------
        ValueError
            If text is blank, citations repeat, or support lacks a reference.
        """

        _validate_judgment_text(
            references=self.evidence_references, texts=(self.claim, self.explanation)
        )

        if (
            self.support in {"supported", "contradicted"}
            and not self.evidence_references
        ):
            raise ValueError("Supported or contradicted claims need shown evidence.")

        return self


@dataclass(frozen=True, slots=True)
class ReportInputs:
    """Upstream-only population descriptions and bounded lexical comparisons.

    Attributes
    ----------
    lexical_scores
        Document, component, pair ID and exact Jaccard numerator/denominator.
    population_json
        Compact complete population membership, exclusions and source bindings.
    schedule_content_hash
        Frozen schedule whose selected pairs determine the baseline comparisons.
    """

    lexical_scores: tuple[tuple[str, str, str, int, int], ...]
    population_json: str
    schedule_content_hash: str


class ReportProvenance(BaseModel):
    """Explicit development/evaluation evidence kind without Git metadata."""

    evidence_kind: Literal["development", "evaluation"]

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


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
    output_contract_json
        Explicit output mode, effective provider schemas and SDK/retry binding.
    provider
        Provider resolved from the explicit model identifier.
    """

    execution_json: str
    model: str
    model_config_json: str
    model_settings_json: str
    output_contract_json: str
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
class ScheduledEvidence:
    """Exact presented payload and its construction audit for a scheduled call.

    Attributes
    ----------
    audit_json
        Source binding, removals, changed/unavailable evidence and presentation audit.
    condition
        Evidence condition; distinct from presentation order.
    endpoint_uuids
        Displayed first/second endpoints; canonical mapping is stored in the request.
    material_content_hash
        Hash covering all other fields.
    pair_id
        Stable assessed pair.
    payload_json
        Actual shown evidence; original first/second facts retain their orientation.
    payload_sha256
        SHA-256 of the shown evidence.
    presentation
        Canonical, swapped endpoints or reversed evidence lists.
    references
        Permitted pointers regenerated for the presented payload.
    source_content_hash
        Immutable source-view identity.
    view
        Assessment task, kept outside evidence text.
    """

    audit_json: str
    condition: str
    endpoint_uuids: tuple[UUID, UUID]
    material_content_hash: str
    pair_id: str
    payload_json: str
    payload_sha256: str
    presentation: Literal["canonical", "endpoint_swapped", "evidence_lists_reversed"]
    references: tuple[str, ...]
    source_content_hash: str
    view: Literal["classification", "critique"]


@dataclass(frozen=True, slots=True)
class ScheduledRequest:
    """One finite scheduled judgment, with immutable response and dependency identity.

    Attributes
    ----------
    canonical_endpoint_uuids
        Stable orientation used for comparisons after remapping displayed judgments.
    component
        Real production, independent upstream, or constructed controls.
    dependencies
        Blind request IDs that must validate and freeze before this call can dispatch.
    evidence
        Exact condition and presentation payload.
    material_content_hash
        Hash covering every field except itself.
    prompt
        Exact messages, response schema and opaque request ID.
    replicate
        One-based replicate within component/pair/condition/presentation/task.
    role
        Base, identical diagnostic repeat, variant, critique or control.
    """

    canonical_endpoint_uuids: tuple[UUID, UUID]
    component: Literal["production", "independent", "controls"]
    dependencies: tuple[str, ...]
    evidence: ScheduledEvidence
    material_content_hash: str
    prompt: JudgePrompt
    replicate: int
    role: Literal["base", "identical_repeat", "variant", "critique", "control"]


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
class SyntheticControl:
    """Constructed evaluator control with expectations separated from shown evidence.

    Attributes
    ----------
    construction_json
        Hidden toy-world assumptions and planted defect definition.
    doc_key
        Selected curriculum owning the control; never a real-population example.
    endpoint_uuids
        Distinct invented endpoint identities in canonical order.
    expectation_json
        Hidden explicit expected classification/direction or rationale grounding.
    family
        Hidden required control family.
    framework_uuid
        Owning framework; no cross-framework pair is constructed.
    input_content_hash
        Selected snapshot/configuration binding.
    material_content_hash
        Complete control identity including hidden oracle material.
    pair_id
        Opaque invented pair identity without a family label.
    payload_json
        Judge-visible bounded toy-world facts and optional operative rationale.
    payload_sha256
        SHA-256 of the exact shown evidence.
    references
        Permitted shown evidence pointers, excluding the rationale under critique.
    view
        Internal dispatch type, hidden from judge messages.
    """

    construction_json: str
    doc_key: str
    endpoint_uuids: tuple[UUID, UUID]
    expectation_json: str
    family: str
    framework_uuid: UUID
    input_content_hash: str
    material_content_hash: str
    pair_id: str
    payload_json: str
    payload_sha256: str
    references: tuple[str, ...]
    view: Literal["synthetic_classification", "synthetic_critique"]


@dataclass(frozen=True, slots=True)
class UpstreamEvidenceSource:
    """Isolated upstream inputs with no production nomination or judgment artifacts.

    Attributes
    ----------
    config_json
        Exact current effective configuration, including resolved concurrency capacity.
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
        Supported current journal-bearing checkpoint contract.
    config_json
        Validated effective configuration in its original serialization shape.
    run
        Original discovery identity and completion evidence.
    source_artifact
        Run-relative source DocumentIR path, retaining relocated-path provenance.
    """

    absent_artifacts: tuple[str, ...]
    artifacts: tuple[SnapshotArtifact, ...]
    checkpoint_format: Literal["journal_bearing"]
    config_json: str
    run: DiscoveredRun
    source_artifact: str


def _validate_judgment_text(
    *, references: tuple[str, ...], texts: tuple[str, ...]
) -> None:
    """Validate nonblank judgment strings and unique nonblank evidence pointers.

    Parameters
    ----------
    references
        Citations whose containment is checked separately against a request.
    texts
        Required explanatory and identity strings.

    Raises
    ------
    ValueError
        If a string is blank or a citation is duplicated.
    """

    if any(not text.strip() for text in (*texts, *references)):
        raise ValueError("Judgment text and evidence references cannot be blank.")

    if len(set(references)) != len(references):
        raise ValueError("Evidence references must not repeat.")


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
