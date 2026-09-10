"""Validate the standalone Learning Progressions graph and its audit trail.

The validator consumes persisted, material-aligned final claims and the relationship
records minted from them. It checks structural and process integrity only. A passing
report does not establish that any progression is pedagogically correct.
"""

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import json

from collections import Counter, deque
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence
from uuid import UUID, uuid5

# Third Party Library
from pydantic import Field, model_validator

# Package Library
from kgfeg.config import Settings
from kgfeg.kgs.lp_admissibility import build_lp_pair_filter
from kgfeg.kgs.lp_checkpoints import content_hash
from kgfeg.kgs.lp_finalization import (
    LPFinalClaim,
    LPFinalClaims,
    LPFinalizationCycleError,
    LPRelationshipProvenance,
    LPRelationships,
    strong_components,
    validate_lp_final_claims_artifact,
)
from kgfeg.kgs.schemas import AcademicStandardsLCKGBundle, Relationship
from kgfeg.kgs.utils import KGDirs
from kgfeg.schemas import BaseSchema, CreateKGConfig

_CHECKPOINT_FAILURES = "lp_generation_failures.json"
_CHECKPOINT_RECEIPT = "lp_generation_checkpoint_manifest.json"
_CHECKPOINT_TRANSACTION = "lp_generation_checkpoint_transaction.json"
_SEMANTIC_SCOPE_NOTICE = (
    "Validation covers structural and process integrity only; it does not establish "
    "pedagogical correctness."
)
_VALIDATION_CHECKS = (
    "upstream_validation_gate",
    "final_claim_and_checkpoint_integrity",
    "generation_zero_unresolved_failures",
    "relationship_schema_and_endpoints",
    "relationship_identity",
    "pair_policy_and_direct_claim_alignment",
    "canonical_relates_to_and_pair_exclusivity",
    "builds_towards_cycle_diagnostics",
    "relationship_metadata_and_provenance",
    "unresolved_warning_consistency",
    "count_reconciliation",
    "identifier_collision_absence",
    "structural_scope_only",
)


class LPValidationCycleComponent(BaseSchema):
    """One complete cyclic component and one deterministic representative path."""

    edge_count: int = Field(ge=1, strict=True)
    edges: tuple[LPValidationCycleEdge, ...]
    node_count: int = Field(ge=1, strict=True)
    node_uuids: tuple[UUID, ...]
    representative_cycle: tuple[UUID, ...]
    representative_relationship_ids: tuple[UUID, ...]

    @model_validator(mode="after")
    def _validate_counts(self) -> LPValidationCycleComponent:
        """Require component counts and representative links to be exact.

        Returns
        -------
        LPValidationCycleComponent
            The count-reconciled component.

        Raises
        ------
        ValueError
            If nodes, edges, or the representative path disagree with their counts.
        """

        if (
            self.edge_count != len(self.edges)
            or self.node_count != len(self.node_uuids)
            or len(set(self.node_uuids)) != self.node_count
            or len(self.representative_cycle) < 2
            or self.representative_cycle[0] != self.representative_cycle[-1]
            or len(self.representative_relationship_ids)
            != len(self.representative_cycle) - 1
        ):
            raise ValueError("LP validation cycle component counts are inconsistent.")

        return self


class LPValidationCycleDiagnostics(BaseSchema):
    """Every cyclic component and all participating direct relationship rows."""

    components: tuple[LPValidationCycleComponent, ...]
    cyclic_component_count: int = Field(ge=0, strict=True)
    cyclic_edge_count: int = Field(ge=0, strict=True)
    cyclic_node_count: int = Field(ge=0, strict=True)
    graph_edge_count: int = Field(ge=0, strict=True)
    graph_node_count: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def _validate_counts(self) -> LPValidationCycleDiagnostics:
        """Require aggregate diagnostic counts to match the listed components.

        Returns
        -------
        LPValidationCycleDiagnostics
            The count-reconciled diagnostics.

        Raises
        ------
        ValueError
            If any aggregate count disagrees with the component population.
        """

        if (
            self.cyclic_component_count != len(self.components)
            or self.cyclic_edge_count
            != sum(component.edge_count for component in self.components)
            or self.cyclic_node_count
            != sum(component.node_count for component in self.components)
        ):
            raise ValueError("LP validation cycle diagnostic counts are inconsistent.")

        return self


class LPValidationCycleEdge(BaseSchema):
    """One cyclic relationship with available claim and model-stage references."""

    checker_checkpoint_content_hash: str | None
    pair_id: str | None
    producer_checkpoint_content_hash: str | None
    relationship_id: UUID
    request_id: UUID | None
    response_checkpoint_content_hash: str | None
    source_sfi_uuid: UUID
    target_sfi_uuid: UUID


class LPValidationReport(BaseSchema):
    """Standalone LP structural/process verdict with explicit semantic limits."""

    content_hash: str
    cycle_diagnostics: LPValidationCycleDiagnostics
    errors: tuple[str, ...]
    input_content_hashes: dict[str, str]
    object_counts: dict[str, int]
    passed: bool = Field(strict=True)
    pedagogical_correctness_established: Literal[False]
    semantic_scope_notice: Literal[
        "Validation covers structural and process integrity only; it does not establish pedagogical correctness."
    ]
    semantic_validation_performed: Literal[False]
    validation_checks: tuple[str, ...]
    warnings: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_report(self) -> LPValidationReport:
        """Bind the verdict and digest to the report's actual material.

        Returns
        -------
        LPValidationReport
            The internally reconciled report.

        Raises
        ------
        ValueError
            If the verdict, checks, messages, or content hash are inconsistent.
        """

        if self.passed != (not self.errors):
            raise ValueError("LP validation passed must equal the absence of errors.")

        if (
            self.validation_checks != _VALIDATION_CHECKS
            or len(set(self.errors)) != len(self.errors)
            or len(set(self.warnings)) != len(self.warnings)
            or self.content_hash
            != content_hash(self.model_dump(exclude={"content_hash"}, mode="json"))
        ):
            raise ValueError("LP validation report material is inconsistent.")

        return self


def _add_error(*, errors: list[str], message: str) -> None:
    """Append one deterministic validation error without duplicating it.

    Parameters
    ----------
    errors
        Ordered validation errors accumulated so far.
    message
        Complete domain-level failure message.
    """

    if message not in errors:
        errors.append(message)


def _add_warning(*, message: str, warnings: list[str]) -> None:
    """Append one deterministic nonblocking warning without duplicating it.

    Parameters
    ----------
    message
        Complete domain-level warning message.
    warnings
        Ordered validation warnings accumulated so far.
    """

    if message not in warnings:
        warnings.append(message)


def _checkpoint_state(
    *, final_claims: LPFinalClaims, root: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read checkpoint status already authenticated by final-claim reconstruction.

    Parameters
    ----------
    final_claims
        Reconstructed final claims carrying the committed receipt byte hash.
    root
        Generation directory containing checkpoint and failure artifacts.

    Returns
    -------
    tuple[list[dict[str, Any]], dict[str, Any]]
        Ordered failure history and committed processing receipt.

    Raises
    ------
    ValueError
        If a transaction remains, files are missing, hashes differ, or JSON shape is
        invalid.
    """

    if (root / _CHECKPOINT_TRANSACTION).exists():
        raise ValueError("LP checkpoint transaction is unfinished.")

    receipt_bytes = (root / _CHECKPOINT_RECEIPT).read_bytes()

    if hashlib.sha256(receipt_bytes).hexdigest() != (
        final_claims.checkpoint_receipt_byte_hash
    ):
        raise ValueError("LP checkpoint receipt differs from final claims.")

    receipt = json.loads(receipt_bytes)
    failure_bytes = (root / _CHECKPOINT_FAILURES).read_bytes()
    failures = json.loads(failure_bytes)

    if not isinstance(receipt, dict) or not isinstance(failures, list):
        raise ValueError("LP checkpoint receipt or failure artifact has invalid shape.")

    artifact_hashes = receipt.get("artifact_byte_hashes")

    if (
        not isinstance(artifact_hashes, dict)
        or artifact_hashes.get(_CHECKPOINT_FAILURES)
        != hashlib.sha256(failure_bytes).hexdigest()
    ):
        raise ValueError("LP failure artifact differs from the checkpoint receipt.")

    return failures, receipt


def _claim_by_relationship_identity(
    *, doc_key: str, final_claims: LPFinalClaims
) -> dict[UUID, LPFinalClaim]:
    """Index every publishing claim by its required deterministic relationship UUID.

    Parameters
    ----------
    doc_key
        Document identity used by the relationship namespace.
    final_claims
        Complete direct claim population.

    Returns
    -------
    dict[UUID, LPFinalClaim]
        Publishing claims keyed by expected relationship identifier.

    Raises
    ------
    ValueError
        If a publishing claim lacks endpoints or expected identities collide.
    """

    claims: dict[UUID, LPFinalClaim] = {}

    for claim in final_claims.claims:
        if claim.judgment.decision in {"no_relation", "needs_review"}:
            continue

        source = claim.source_sfi_uuid
        target = claim.target_sfi_uuid

        if source is None or target is None:
            raise ValueError("Publishing LP claim lacks final endpoints.")

        identity_key = (
            f"lc:curriculum:{doc_key}:relationship:{claim.judgment.decision}:"
            f"{source}:{target}"
        )
        identifier = uuid5(
            name=identity_key, namespace=Settings.LC_CANONICAL_NAMESPACE_UUID
        )

        if identifier in claims:
            raise ValueError("Publishing LP claims collide on relationship identity.")

        claims[identifier] = claim

    return claims


def _cycle_diagnostics(
    *,
    provenance: Mapping[str, LPRelationshipProvenance],
    relationships: Sequence[Relationship],
) -> LPValidationCycleDiagnostics:
    """Find every cyclic component in the supplied direct developmental graph.

    Parameters
    ----------
    provenance
        Relationship provenance keyed by relationship UUID string.
    relationships
        All supplied ``buildsTowards`` rows, including malformed duplicates.

    Returns
    -------
    LPValidationCycleDiagnostics
        Stable SCC, edge, node, path, and provenance-link diagnostics.
    """

    endpoint_edges: dict[tuple[UUID, UUID], list[Relationship]] = {}

    for edge in relationships:
        try:
            source = UUID(edge.source_entity_value)
            target = UUID(edge.target_entity_value)
        except (TypeError, ValueError):
            continue

        endpoint_edges.setdefault((source, target), []).append(edge)

    nodes = sorted({node for edge in endpoint_edges for node in edge}, key=str)
    forward: dict[UUID, list[UUID]] = {node: [] for node in nodes}
    reverse: dict[UUID, list[UUID]] = {node: [] for node in nodes}

    for source, target in sorted(
        endpoint_edges, key=lambda endpoints: tuple(map(str, endpoints))
    ):
        forward[source].append(target)
        reverse[target].append(source)

    components = []

    for members in strong_components(forward=forward, reverse=reverse):
        if len(members) == 1 and (members[0], members[0]) not in endpoint_edges:
            continue

        member_set = set(members)
        component_edges_rows = tuple(
            edge
            for source in members
            for target in forward[source]
            if target in member_set
            for edge in sorted(
                endpoint_edges[(source, target)], key=lambda item: str(item.identifier)
            )
        )
        cycle = _representative_cycle(forward=forward, members=members)
        representative_relationship_ids = tuple(
            sorted(
                endpoint_edges[(source, target)], key=lambda item: str(item.identifier)
            )[0].identifier
            for source, target in zip(cycle, cycle[1:])
        )
        component_edges = []

        for edge in component_edges_rows:
            record = provenance.get(str(edge.identifier))
            claim = None if record is None else record.claim
            claim_provenance = None if claim is None else claim.provenance
            component_edges.append(
                LPValidationCycleEdge(
                    checker_checkpoint_content_hash=(
                        None
                        if claim_provenance is None
                        else claim_provenance.checker_checkpoint_content_hash
                    ),
                    pair_id=None if claim is None else claim.judgment.pair_id,
                    producer_checkpoint_content_hash=(
                        None
                        if claim_provenance is None
                        else claim_provenance.producer_checkpoint_content_hash
                    ),
                    relationship_id=edge.identifier,
                    request_id=(
                        None
                        if claim_provenance is None
                        else claim_provenance.request_id
                    ),
                    response_checkpoint_content_hash=(
                        None
                        if claim_provenance is None
                        else claim_provenance.response_checkpoint_content_hash
                    ),
                    source_sfi_uuid=UUID(edge.source_entity_value),
                    target_sfi_uuid=UUID(edge.target_entity_value),
                )
            )

        components.append(
            LPValidationCycleComponent(
                edge_count=len(component_edges),
                edges=tuple(component_edges),
                node_count=len(members),
                node_uuids=members,
                representative_cycle=cycle,
                representative_relationship_ids=representative_relationship_ids,
            )
        )

    return LPValidationCycleDiagnostics(
        components=tuple(components),
        cyclic_component_count=len(components),
        cyclic_edge_count=sum(component.edge_count for component in components),
        cyclic_node_count=sum(component.node_count for component in components),
        graph_edge_count=len(relationships),
        graph_node_count=len(nodes),
    )


def _expected_relationship(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    claim: LPFinalClaim,
    doc_key: str,
    final_claims: LPFinalClaims,
    kg_config: CreateKGConfig,
) -> tuple[Relationship, LPRelationshipProvenance]:
    """Reconstruct one required row and provenance record from material inputs.

    Parameters
    ----------
    as_lc_bundle
        Authoritative upstream graph and framework ownership metadata.
    claim
        Direct accepted claim selected by producer/checker reconciliation.
    doc_key
        Current document identity.
    final_claims
        Complete material-aligned final claim population.
    kg_config
        Current effective curriculum and operational configuration.

    Returns
    -------
    tuple[Relationship, LPRelationshipProvenance]
        Exact expected relationship and its complete provenance.

    Raises
    ------
    ValueError
        If the claim lacks final endpoints.
    """

    source = claim.source_sfi_uuid
    target = claim.target_sfi_uuid

    if source is None or target is None:
        raise ValueError("Publishing LP claim lacks final endpoints.")

    framework = as_lc_bundle.framework
    policy = kg_config.learning_progressions.relationship_metadata
    relationship_type = claim.judgment.decision
    identity_key = (
        f"lc:curriculum:{doc_key}:relationship:{relationship_type}:{source}:{target}"
    )
    identifier = uuid5(
        name=identity_key, namespace=Settings.LC_CANONICAL_NAMESPACE_UUID
    )
    model_configuration = final_claims.execution_material["model_config"]
    model_settings = final_claims.execution_material["model_settings"]
    provenance = LPRelationshipProvenance(
        approved_by=policy.approved_by,
        attribution_statement_template=policy.attribution_statement_template,
        candidate_artifact_byte_hashes=(
            final_claims.request_manifest.artifact_byte_hashes
        ),
        candidate_pairs_content_hash=(
            final_claims.request_manifest.candidate_pairs_content_hash
        ),
        candidate_summary_content_hash=(
            final_claims.request_manifest.candidate_summary_content_hash
        ),
        claim=claim,
        config_content_hash=final_claims.request_manifest.config_content_hash,
        doc_key=doc_key,
        effective_lp_config_content_hash=content_hash(
            kg_config.learning_progressions.model_dump(mode="json")
        ),
        final_claims_content_hash=final_claims.content_hash,
        model_configuration=model_configuration,
        model_configuration_content_hash=content_hash(model_configuration),
        model_settings=model_settings,
        model_settings_content_hash=content_hash(model_settings),
        relationship_identity_key=identity_key,
        requests_content_hash=final_claims.request_manifest.requests_content_hash,
        source_framework={
            "attribution_statement": framework.attribution_statement,
            "author": framework.author,
            "case_identifier_uuid": framework.case_identifier_uuid,
            "license": framework.license,
            "provider": framework.provider,
            "title": framework.name,
        },
        upstream_content_hash=final_claims.request_manifest.upstream_content_hash,
    )
    attribution = policy.attribution_statement_template.replace(
        "{source_attribution_statement}", framework.attribution_statement
    )
    relationship = Relationship(
        attribution_statement=attribution,
        author=policy.author,
        identifier=identifier,
        license=framework.license,
        metadata=provenance.model_dump(mode="json"),
        provider=policy.provider,
        relationship_type=relationship_type,
        source_entity="StandardsFrameworkItem",
        source_entity_key="case_identifier_uuid",
        source_entity_value=str(source),
        target_entity="StandardsFrameworkItem",
        target_entity_key="case_identifier_uuid",
        target_entity_value=str(target),
    )
    return relationship, provenance


def _identifier_collisions(
    *, as_lc_bundle: AcademicStandardsLCKGBundle, relationships: Sequence[Relationship]
) -> tuple[str, ...]:
    """Find identifiers repeated across every upstream and LP entity category.

    Parameters
    ----------
    as_lc_bundle
        Complete upstream framework, nodes, and AS/LC relationships.
    relationships
        Both standalone LP relationship populations.

    Returns
    -------
    tuple[str, ...]
        Canonically sorted identifiers occurring more than once.
    """

    counts = Counter(
        [
            str(as_lc_bundle.framework.case_identifier_uuid),
            *(str(item.case_identifier_uuid) for item in as_lc_bundle.items),
            *(
                str(component.identifier)
                for component in as_lc_bundle.learning_components
            ),
            *(
                str(relationship.identifier)
                for relationship in as_lc_bundle.relationships_has_child
            ),
            *(
                str(relationship.identifier)
                for relationship in as_lc_bundle.relationships_supports
            ),
            *(str(relationship.identifier) for relationship in relationships),
        ]
    )
    return tuple(
        sorted(identifier for identifier, count in counts.items() if count > 1)
    )


def _relationship_rows(relationships: LPRelationships) -> tuple[Relationship, ...]:
    """Return both LP relationship populations in deterministic field order.

    Parameters
    ----------
    relationships
        Standalone rows grouped by public relationship type.

    Returns
    -------
    tuple[Relationship, ...]
        ``buildsTowards`` rows followed by ``relatesTo`` rows.
    """

    return (
        *relationships.relationships_builds_towards,
        *relationships.relationships_relates_to,
    )


def _representative_cycle(
    *, forward: Mapping[UUID, Sequence[UUID]], members: tuple[UUID, ...]
) -> tuple[UUID, ...]:
    """Choose a deterministic shortest closed path through the smallest member.

    Parameters
    ----------
    forward
        UUID-ordered directed adjacency for the whole graph.
    members
        Canonically sorted nodes in one cyclic component.

    Returns
    -------
    tuple[UUID, ...]
        Closed directed path beginning and ending at the smallest component node.

    Raises
    ------
    ValueError
        If no return path exists inside a purported cyclic component.
    """

    allowed = set(members)
    start = members[0]

    if start in forward[start]:
        return start, start

    successor = next(node for node in forward[start] if node in allowed)
    parents: dict[UUID, UUID | None] = {successor: None}
    queue = deque([successor])

    while queue:
        node = queue.popleft()

        if node == start:
            path = [node]

            while parents[node] is not None:
                parent = parents[node]

                if parent is None:
                    break

                path.append(parent)
                node = parent

            return start, *reversed(path)

        for target in forward[node]:
            if target in allowed and target not in parents:
                parents[target] = node
                queue.append(target)

    raise ValueError("LP cyclic component has no representative return path.")


def _validate_checkpoint_completion(
    *,
    errors: list[str],
    failures: list[dict[str, Any]],
    final_claims: LPFinalClaims,
    receipt: dict[str, Any],
) -> tuple[int, int]:
    """Require complete successful prefixes and no unresolved processing failures.

    Parameters
    ----------
    errors
        Ordered validation errors.
    failures
        Authenticated generation failure history.
    final_claims
        Complete reconciled claim population.
    receipt
        Authenticated checkpoint receipt.

    Returns
    -------
    tuple[int, int]
        Historical failure-attempt count and unresolved failed-pair count.
    """

    unresolved = sorted(
        {
            pair_id
            for failure in failures
            if failure.get("resolved_run_number") is None
            for pair_id in failure.get("pair_ids", [])
        }
    )
    expected_requests = final_claims.request_manifest.total_requests
    stage_counts = receipt.get("stage_counts")

    if receipt.get("status") != "completed" or receipt.get("failed_pair_ids"):
        _add_error(
            errors=errors,
            message="LP generation is not completed with zero failed pairs.",
        )

    if not isinstance(stage_counts, dict) or any(
        stage_counts.get(stage) != expected_requests
        for stage in ("draft", "response", "verdict")
    ):
        _add_error(
            errors=errors,
            message="LP generation checkpoint counts do not cover every request.",
        )

    if unresolved:
        _add_error(
            errors=errors,
            message=f"LP generation has unresolved failed pairs: {unresolved}.",
        )

    return len(failures), len(unresolved)


def _validate_claim_policy(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    errors: list[str],
    final_claims: LPFinalClaims,
    kg_config: CreateKGConfig,
    warnings: list[str],
) -> None:
    """Recompute pair permissions and warning propagation from current policy.

    Parameters
    ----------
    as_lc_bundle
        Current authoritative AS+LC graph.
    doc_key
        Current document identity.
    errors
        Ordered validation errors.
    final_claims
        Complete reconciled pair judgments.
    kg_config
        Current effective AS and LP configuration.
    warnings
        Ordered nonblocking unresolved-context warnings.
    """

    pair_filter = build_lp_pair_filter(
        as_lc_bundle=as_lc_bundle, doc_key=doc_key, kg_config=kg_config
    )

    for claim in final_claims.claims:
        judgment = claim.judgment
        admissibility = pair_filter.filter_pair(
            first_sfi_uuid=judgment.first_sfi_uuid,
            second_sfi_uuid=judgment.second_sfi_uuid,
        )

        if admissibility is None:
            _add_error(
                errors=errors,
                message=f"LP claim {judgment.pair_id} is excluded by current policy.",
            )
            continue

        expected_decisions = tuple(
            decision.model_dump(mode="json")
            for decision in admissibility.admissible_decisions
        )
        actual_decisions = tuple(
            decision.model_dump(mode="json")
            for decision in claim.candidate.admissible_decisions
        )

        if (
            judgment.pair_id != admissibility.pair_id
            or claim.candidate.pair_id != admissibility.pair_id
            or actual_decisions != expected_decisions
            or tuple(claim.candidate.warnings) != admissibility.warnings
            or not set(admissibility.warnings).issubset(judgment.warnings)
        ):
            _add_error(
                errors=errors,
                message=(
                    f"LP claim {judgment.pair_id} disagrees with pair permissions "
                    f"or required warnings."
                ),
            )

        if admissibility.warnings:
            _add_warning(
                message=(
                    f"LP pair {judgment.pair_id} retains unresolved or incomplete "
                    f"upstream context warnings."
                ),
                warnings=warnings,
            )


def _validate_counts_and_collisions(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    errors: list[str],
    final_claims: LPFinalClaims,
    relationships: LPRelationships,
) -> tuple[tuple[str, ...], dict[str, int]]:
    """Reconcile decision, edge, provenance, and graph-wide identifier counts.

    Parameters
    ----------
    as_lc_bundle
        Complete upstream graph.
    errors
        Ordered validation errors.
    final_claims
        Complete pair-decision population.
    relationships
        Standalone LP rows and keyed provenance.

    Returns
    -------
    tuple[tuple[str, ...], dict[str, int]]
        Identifier collisions and actual object counts.
    """

    rows = _relationship_rows(relationships)
    decisions = Counter(claim.judgment.decision for claim in final_claims.claims)
    collisions = _identifier_collisions(as_lc_bundle=as_lc_bundle, relationships=rows)
    counts = {
        "builds_towards_claims": decisions["buildsTowards"],
        "builds_towards_relationships": len(relationships.relationships_builds_towards),
        "candidate_pairs": final_claims.request_manifest.total_candidate_pairs,
        "final_claims": len(final_claims.claims),
        "identifier_collisions": len(collisions),
        "needs_review_claims": decisions["needs_review"],
        "no_relation_claims": decisions["no_relation"],
        "relationship_provenance": len(relationships.relationship_provenance),
        "relates_to_claims": decisions["relatesTo"],
        "relates_to_relationships": len(relationships.relationships_relates_to),
        "relationships": len(rows),
        "requests": final_claims.request_manifest.total_requests,
    }

    if counts["final_claims"] != counts["candidate_pairs"]:
        _add_error(
            errors=errors,
            message="LP final-claim count does not match the candidate population.",
        )

    if (
        counts["builds_towards_claims"] != counts["builds_towards_relationships"]
        or counts["relates_to_claims"] != counts["relates_to_relationships"]
        or counts["relationships"] != counts["relationship_provenance"]
    ):
        _add_error(
            errors=errors,
            message="LP accepted-claim, relationship, or provenance counts disagree.",
        )

    if collisions:
        _add_error(
            errors=errors,
            message=f"LP graph contains identifier collisions: {list(collisions)}.",
        )

    return collisions, counts


def _validate_relationship_material(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    errors: list[str],
    final_claims: LPFinalClaims,
    kg_config: CreateKGConfig,
    relationships: LPRelationships,
    rows_by_id: Mapping[UUID, Relationship],
) -> None:
    """Compare every supplied row and provenance entry with its direct claim.

    Parameters
    ----------
    as_lc_bundle
        Current authoritative upstream graph.
    doc_key
        Current document identity.
    errors
        Ordered validation errors.
    final_claims
        Material-aligned direct claim population.
    kg_config
        Current effective policy and ownership configuration.
    relationships
        Standalone keyed provenance to validate.
    rows_by_id
        Supplied LP rows keyed by relationship UUID.
    """

    try:
        expected_claims = _claim_by_relationship_identity(
            doc_key=doc_key, final_claims=final_claims
        )
    except ValueError as exc:
        _add_error(errors=errors, message=str(exc))
        expected_claims = {}

    if relationships.final_claims_content_hash != final_claims.content_hash:
        _add_error(
            errors=errors,
            message="LP relationships reference stale or different final claims.",
        )

    if set(rows_by_id) != set(expected_claims):
        _add_error(
            errors=errors,
            message=(
                "LP relationships do not equal the complete directly accepted "
                "claim population."
            ),
        )

    if set(relationships.relationship_provenance) != {
        str(identifier) for identifier in rows_by_id
    }:
        _add_error(
            errors=errors,
            message="LP relationship provenance keys do not equal relationship IDs.",
        )

    for identifier in sorted(set(rows_by_id) & set(expected_claims), key=str):
        actual = rows_by_id[identifier]
        claim = expected_claims[identifier]
        expected, expected_provenance = _expected_relationship(
            as_lc_bundle=as_lc_bundle,
            claim=claim,
            doc_key=doc_key,
            final_claims=final_claims,
            kg_config=kg_config,
        )
        actual_provenance = relationships.relationship_provenance.get(str(identifier))

        if actual != expected:
            _add_error(
                errors=errors,
                message=(
                    f"LP relationship {identifier} differs from its deterministic "
                    f"identity, endpoints, description, or exact ownership metadata."
                ),
            )

        if (
            actual_provenance != expected_provenance
            or actual.metadata != expected_provenance.model_dump(mode="json")
        ):
            _add_error(
                errors=errors,
                message=(
                    f"LP relationship {identifier} has incomplete or inconsistent "
                    f"direct-claim provenance."
                ),
            )


def _validate_relationship_row(
    *, endpoints: set[str], errors: list[str], row: Relationship
) -> tuple[str, str]:
    """Validate one LP row's intrinsic shape, endpoints, and canonical ordering.

    Parameters
    ----------
    endpoints
        Authoritative final SFI CASE UUID strings.
    errors
        Ordered validation errors.
    row
        One supplied LP relationship.

    Returns
    -------
    tuple[str, str]
        Canonically unordered endpoint pair used for exclusivity checks.
    """

    try:
        Relationship.model_validate(row.model_dump(mode="python"))
    except ValueError as exc:
        _add_error(
            errors=errors,
            message=f"LP relationship {row.identifier} is invalid: {exc}",
        )

    if (
        row.source_entity_value not in endpoints
        or row.target_entity_value not in endpoints
    ):
        _add_error(
            errors=errors,
            message=f"LP relationship {row.identifier} has an unknown SFI endpoint.",
        )

    if row.source_entity_value == row.target_entity_value:
        _add_error(
            errors=errors, message=f"LP relationship {row.identifier} is a self-loop."
        )

    if (
        row.relationship_type == "relatesTo"
        and row.source_entity_value >= row.target_entity_value
    ):
        _add_error(
            errors=errors,
            message=(
                f"LP relatesTo relationship {row.identifier} is not in canonical "
                f"endpoint order."
            ),
        )

    return tuple(sorted((row.source_entity_value, row.target_entity_value)))


def _validate_relationships(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    errors: list[str],
    final_claims: LPFinalClaims,
    kg_config: CreateKGConfig,
    relationships: LPRelationships,
) -> None:
    """Validate exact rows, pair cardinality, endpoints, identity, and provenance.

    Parameters
    ----------
    as_lc_bundle
        Current authoritative upstream graph.
    doc_key
        Current document identity.
    errors
        Ordered validation errors.
    final_claims
        Material-aligned direct claim population.
    kg_config
        Current effective policy and ownership configuration.
    relationships
        Standalone LP rows and keyed provenance to validate.
    """

    rows = _relationship_rows(relationships)
    endpoints = {str(item.case_identifier_uuid) for item in as_lc_bundle.items}
    identifiers = [str(row.identifier) for row in rows]
    endpoint_pairs = [
        _validate_relationship_row(endpoints=endpoints, errors=errors, row=row)
        for row in rows
    ]

    if len(identifiers) != len(set(identifiers)):
        _add_error(errors=errors, message="LP relationship identifiers are duplicated.")

    if len(endpoint_pairs) != len(set(endpoint_pairs)):
        _add_error(
            errors=errors,
            message="LP logical pairs publish duplicate or mutually exclusive rows.",
        )

    rows_by_id = {row.identifier: row for row in rows}
    _validate_relationship_material(
        as_lc_bundle=as_lc_bundle,
        doc_key=doc_key,
        errors=errors,
        final_claims=final_claims,
        kg_config=kg_config,
        relationships=relationships,
        rows_by_id=rows_by_id,
    )


def validate_lp_graph(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
    relationships: LPRelationships,
) -> LPValidationReport:
    """Validate one standalone LP graph without writing or semantically patching it.

    Parameters
    ----------
    as_lc_bundle
        Authoritative passed AS+LC bundle for the current framework.
    doc_key
        Current source document identity.
    kg_config
        Current effective AS and LP policy.
    kg_dirs
        Directory containing materialized requests, checkpoints, and final claims.
    relationships
        Step 17 relationship rows and separately keyed provenance.

    Returns
    -------
    LPValidationReport
        Deterministic structural/process verdict, counts, warnings, hashes, and full
        directed-cycle diagnostics.

    Raises
    ------
    ValueError
        If authoritative material cannot be loaded or reconstructed safely. Graph
        policy violations in otherwise loadable material are returned as report errors.
    """

    bundle = AcademicStandardsLCKGBundle.model_validate_json(
        as_lc_bundle.model_dump_json()
    )
    config = CreateKGConfig.model_validate_json(
        kg_config.model_dump_json(by_alias=True)
    )
    errors: list[str] = []
    warnings: list[str] = []

    if not bundle.validation_report.passed or bundle.validation_report.errors:
        _add_error(
            errors=errors,
            message="LP validation requires a passed, error-free AS+LC bundle.",
        )

    try:
        final_claims = validate_lp_final_claims_artifact(
            as_lc_bundle=bundle,
            doc_key=doc_key,
            kg_config=config,
            kg_dirs=kg_dirs,
        )
    except LPFinalizationCycleError as exc:
        final_claims = exc.artifact
        _add_error(
            errors=errors,
            message="LP final claims contain a directed buildsTowards cycle.",
        )

    failures, receipt = _checkpoint_state(final_claims=final_claims, root=kg_dirs.root)
    material_doc_key = final_claims.request_manifest.doc_key
    historical_failures, unresolved_failures = _validate_checkpoint_completion(
        errors=errors,
        failures=failures,
        final_claims=final_claims,
        receipt=receipt,
    )
    normalized_relationships = LPRelationships.model_validate_json(
        relationships.model_dump_json()
    )
    _validate_claim_policy(
        as_lc_bundle=bundle,
        doc_key=material_doc_key,
        errors=errors,
        final_claims=final_claims,
        kg_config=config,
        warnings=warnings,
    )
    _validate_relationships(
        as_lc_bundle=bundle,
        doc_key=material_doc_key,
        errors=errors,
        final_claims=final_claims,
        kg_config=config,
        relationships=normalized_relationships,
    )
    _, object_counts = _validate_counts_and_collisions(
        as_lc_bundle=bundle,
        errors=errors,
        final_claims=final_claims,
        relationships=normalized_relationships,
    )
    object_counts.update(
        {
            "generation_failure_attempts": historical_failures,
            "unresolved_failed_pairs": unresolved_failures,
            "unresolved_warning_pairs": len(warnings),
        }
    )
    diagnostics = _cycle_diagnostics(
        provenance=normalized_relationships.relationship_provenance,
        relationships=normalized_relationships.relationships_builds_towards,
    )

    if diagnostics.components:
        _add_error(
            errors=errors,
            message=(
                f"LP buildsTowards graph contains "
                f"{diagnostics.cyclic_component_count} cyclic components, "
                f"{diagnostics.cyclic_node_count} participating SFIs, and "
                f"{diagnostics.cyclic_edge_count} participating relationships."
            ),
        )

    input_hashes = {
        "as_lc_bundle": content_hash(bundle.model_dump(mode="json")),
        "checkpoint_receipt_bytes": final_claims.checkpoint_receipt_byte_hash,
        "effective_config": content_hash(config.model_dump(by_alias=True, mode="json")),
        "final_claims": final_claims.content_hash,
        "relationships": content_hash(normalized_relationships.model_dump(mode="json")),
    }
    material = {
        "cycle_diagnostics": diagnostics.model_dump(mode="json"),
        "errors": errors,
        "input_content_hashes": input_hashes,
        "object_counts": object_counts,
        "passed": not errors,
        "pedagogical_correctness_established": False,
        "semantic_scope_notice": _SEMANTIC_SCOPE_NOTICE,
        "semantic_validation_performed": False,
        "validation_checks": _VALIDATION_CHECKS,
        "warnings": warnings,
    }
    return LPValidationReport.model_validate(
        {**material, "content_hash": content_hash(material)}
    )
