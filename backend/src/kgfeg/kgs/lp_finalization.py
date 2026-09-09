"""Reconcile direct LP claims against complete material-aligned adjudication.

Claims preserve the unified checker-selected judgment, including negative and ambiguous
outcomes. Whole-graph diagnostics establish acyclicity only; neither processing
completion nor acyclicity establishes pedagogical correctness.
"""

# Future Library
from __future__ import annotations

# Standard Library
import fcntl
import hashlib
import os
import tempfile

from collections import deque
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

# Third Party Library
from pydantic import Field, model_validator

# Package Library
from kgfeg.kgs.lp_checkpoints import LPGenerationCheckpoints, content_hash
from kgfeg.kgs.lp_generation import LPGenerationFailed, lp_execution_material
from kgfeg.kgs.lp_requests import (
    LPRequestManifest,
    LPRequestPopulation,
    canonical_lp_json,
    read_lp_request_population,
    validate_lp_request_artifacts,
)
from kgfeg.kgs.schemas import (
    AcademicStandardsLCKGBundle,
    LPCandidatePair,
    LPGenerationResponse,
    LPGenerationValidationVerdict,
    LPPairJudgment,
)
from kgfeg.kgs.utils import KGDirs
from kgfeg.schemas import BaseSchema, CreateKGConfig

_FINAL_CLAIMS = "lp_final_claims.json"
_RECEIPT = "lp_generation_checkpoint_manifest.json"
_TRANSACTION = "lp_generation_checkpoint_transaction.json"


class LPCycleComponent(BaseSchema):
    """One cyclic strongly connected component and a deterministic closed path."""

    edge_count: int = Field(ge=1, strict=True)
    edges: tuple[LPCycleEdge, ...]
    node_count: int = Field(ge=1, strict=True)
    node_uuids: tuple[UUID, ...]
    representative_cycle: tuple[UUID, ...]
    representative_pair_ids: tuple[str, ...]


class LPCycleDiagnostics(BaseSchema):
    """Complete participating direct edges and reconciled whole-graph counts."""

    components: tuple[LPCycleComponent, ...]
    cyclic_component_count: int = Field(ge=0, strict=True)
    cyclic_edge_count: int = Field(ge=0, strict=True)
    cyclic_node_count: int = Field(ge=0, strict=True)
    graph_edge_count: int = Field(ge=0, strict=True)
    graph_node_count: int = Field(ge=0, strict=True)


class LPCycleEdge(BaseSchema):
    """A participating directed claim with stable adjudication provenance links."""

    checker_checkpoint_content_hash: str
    judgment_content_hash: str
    pair_id: str
    producer_checkpoint_content_hash: str
    request_id: UUID
    response_checkpoint_content_hash: str
    source_sfi_uuid: UUID
    target_sfi_uuid: UUID


class LPFinalClaim(BaseSchema):
    """One direct semantic judgment with its candidate and execution references.

    Negative and ambiguous claims have no relationship endpoints. The candidate is
    retained as nomination evidence and permissions, never as a semantic override.
    """

    candidate: LPCandidatePair
    judgment: LPPairJudgment
    provenance: LPFinalClaimProvenance
    source_sfi_uuid: UUID | None
    target_sfi_uuid: UUID | None

    @model_validator(mode="after")
    def _validate_claim(self) -> LPFinalClaim:
        """Require candidate containment, allowed decisions, and exact endpoints.

        Returns
        -------
        LPFinalClaim
            Intrinsically consistent claim; checkpoint validation is still required.

        Raises
        ------
        ValueError
            If a claim changes its candidate, permissions, warnings, or endpoints.
        """

        candidate = self.candidate
        judgment = self.judgment

        if (
            judgment.pair_id != candidate.pair_id
            or judgment.first_sfi_uuid != candidate.first_sfi_uuid
            or judgment.second_sfi_uuid != candidate.second_sfi_uuid
            or (judgment.decision, judgment.direction)
            not in {
                (item.decision, item.direction)
                for item in candidate.admissible_decisions
            }
            or not set(candidate.warnings).issubset(judgment.warnings)
            or (self.source_sfi_uuid, self.target_sfi_uuid)
            != _claim_endpoints(judgment)
        ):
            raise ValueError("LP final claim violates its candidate or judgment.")

        return self


class LPFinalClaimProvenance(BaseSchema):
    """Actual material references connecting one claim to both model stages."""

    checker_checkpoint_content_hash: str
    checker_outcome: Literal["accepted", "corrected"]
    checker_prompt_content_hash: str
    checker_verdict_content_hash: str
    execution_content_hash: str
    judgment_content_hash: str
    producer_checkpoint_content_hash: str
    producer_judgment: LPPairJudgment
    producer_prompt_content_hash: str
    producer_response_content_hash: str
    request_content_hash: str
    request_id: UUID
    response_checkpoint_content_hash: str
    response_content_hash: str


class LPFinalClaims(BaseSchema):
    """Complete claim population and graph diagnostics, never a release verdict.

    The content hash covers every other field. Artifact validation additionally
    reconstructs this record from current inputs and validated checkpoint bytes.
    """

    checkpoint_receipt_byte_hash: str
    claims: tuple[LPFinalClaim, ...]
    content_hash: str
    cycle_diagnostics: LPCycleDiagnostics
    decision_counts: dict[str, int]
    execution_material: dict[str, Any]
    finalization_source_content_hash: str
    graph_status: Literal["acyclic", "cyclic"]
    request_manifest: LPRequestManifest
    total_claims: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def _validate_population(self) -> LPFinalClaims:
        """Require unique exact coverage, actual counts, diagnostics, and content.

        Returns
        -------
        LPFinalClaims
            Internally reconciled artifact, pending current-input comparison.

        Raises
        ------
        ValueError
            If coverage, counts, diagnostics, or the material hash disagree.
        """

        pair_ids = tuple(claim.judgment.pair_id for claim in self.claims)
        pairs = {
            (claim.judgment.first_sfi_uuid, claim.judgment.second_sfi_uuid)
            for claim in self.claims
        }
        diagnostics = _cycle_diagnostics(self.claims)

        if (
            len(set(pair_ids)) != len(pair_ids)
            or len(pairs) != len(pair_ids)
            or pair_ids != self.request_manifest.pair_ids
            or self.total_claims != len(self.claims)
            or self.total_claims != self.request_manifest.total_candidate_pairs
            or self.decision_counts != _decision_counts(self.claims)
            or self.cycle_diagnostics != diagnostics
            or self.graph_status != ("cyclic" if diagnostics.components else "acyclic")
            or self.content_hash
            != content_hash(self.model_dump(exclude={"content_hash"}, mode="json"))
        ):
            raise ValueError(
                "LP final claims have inconsistent coverage, counts, or material."
            )

        return self


class LPFinalizationCycleError(ValueError):
    """Final direct claims contain cycles and cannot proceed to relationships."""

    def __init__(self, artifact: LPFinalClaims) -> None:
        """Retain the complete failed claim population and deterministic diagnostics.

        Parameters
        ----------
        artifact
            Reconciled claims with every cyclic component and provenance reference.
        """

        self.artifact = artifact
        self.diagnostics = artifact.cycle_diagnostics
        super().__init__(
            f"LP buildsTowards graph contains {self.diagnostics.cyclic_component_count} "
            f"cyclic components, {self.diagnostics.cyclic_node_count} participating SFIs, "
            f"and {self.diagnostics.cyclic_edge_count} participating edges."
        )


def _claim_endpoints(judgment: LPPairJudgment) -> tuple[UUID | None, UUID | None]:
    """Resolve only the relationship explicitly selected by a unified judgment.

    Parameters
    ----------
    judgment
        Complete canonical pair decision, with semantic direction when required.

    Returns
    -------
    tuple[UUID or None, UUID or None]
        Directed or UUID-canonical endpoints; absent for nonpublishing decisions.
    """

    if judgment.decision in {"no_relation", "needs_review"}:
        return None, None

    if judgment.direction == "second_to_first":
        return judgment.second_sfi_uuid, judgment.first_sfi_uuid

    return judgment.first_sfi_uuid, judgment.second_sfi_uuid


def _cycle_diagnostics(claims: tuple[LPFinalClaim, ...]) -> LPCycleDiagnostics:
    """Find every cyclic component of the complete direct developmental graph.

    Iterative traversal avoids recursion limits. Internal SCC edges all participate in
    cycles; bridges between components do not. No simple-cycle enumeration, transitive
    closure, reduction, edge conversion, or semantic reinterpretation occurs.

    Parameters
    ----------
    claims
        Complete reconciled claim population across every request.

    Returns
    -------
    LPCycleDiagnostics
        Stable components, participating edges, closed paths, and actual counts.

    Raises
    ------
    ValueError
        If any direct claim lacks endpoints or if the graph contains duplicate edges.
    """

    edges: dict[tuple[UUID, UUID], LPCycleEdge] = {}

    for claim in claims:
        if claim.judgment.decision != "buildsTowards":
            continue

        source, target = _claim_endpoints(claim.judgment)

        if source is None or target is None:
            raise ValueError("LP developmental claim lacks endpoints.")

        provenance = claim.provenance
        key = (source, target)

        if key in edges:
            raise ValueError(
                "LP graph contains duplicate or conflicting direct claims."
            )

        edges[key] = LPCycleEdge(
            checker_checkpoint_content_hash=provenance.checker_checkpoint_content_hash,
            judgment_content_hash=provenance.judgment_content_hash,
            pair_id=claim.judgment.pair_id,
            producer_checkpoint_content_hash=provenance.producer_checkpoint_content_hash,
            request_id=provenance.request_id,
            response_checkpoint_content_hash=provenance.response_checkpoint_content_hash,
            source_sfi_uuid=source,
            target_sfi_uuid=target,
        )

    nodes = sorted({node for edge in edges for node in edge}, key=str)
    forward: dict[UUID, list[UUID]] = {node: [] for node in nodes}
    reverse: dict[UUID, list[UUID]] = {node: [] for node in nodes}

    for source, target in sorted(edges, key=lambda edge: (str(edge[0]), str(edge[1]))):
        forward[source].append(target)
        reverse[target].append(source)

    components = []

    for members in _strong_components(forward=forward, reverse=reverse):
        if len(members) == 1 and (members[0], members[0]) not in edges:
            continue

        member_set = set(members)
        component_edges = tuple(
            edges[(source, target)]
            for source in members
            for target in forward[source]
            if target in member_set
        )
        path = _representative_cycle(forward=forward, members=members)
        components.append(
            LPCycleComponent(
                edge_count=len(component_edges),
                edges=component_edges,
                node_count=len(members),
                node_uuids=members,
                representative_cycle=path,
                representative_pair_ids=tuple(
                    edges[(source, target)].pair_id
                    for source, target in zip(path, path[1:])
                ),
            )
        )

    return LPCycleDiagnostics(
        components=tuple(components),
        cyclic_component_count=len(components),
        cyclic_edge_count=sum(component.edge_count for component in components),
        cyclic_node_count=sum(component.node_count for component in components),
        graph_edge_count=len(edges),
        graph_node_count=len(nodes),
    )


def _decision_counts(claims: tuple[LPFinalClaim, ...]) -> dict[str, int]:
    """Count every semantic disposition, including explicit zero populations.

    Parameters
    ----------
    claims
        One reconciled record per canonical candidate pair.

    Returns
    -------
    dict[str, int]
        Exact accepted, negative, and ambiguous claim counts.
    """

    counts = dict.fromkeys(
        ("buildsTowards", "relatesTo", "no_relation", "needs_review"), 0
    )

    for claim in claims:
        counts[claim.judgment.decision] += 1

    return counts


def _finishing_order(forward: dict[UUID, list[UUID]]) -> list[UUID]:
    """Compute iterative depth-first postorder for the component partition.

    Parameters
    ----------
    forward
        Deterministically ordered outgoing adjacency.

    Returns
    -------
    list[UUID]
        Each graph node once, in depth-first finishing order.
    """

    seen: set[UUID] = set()
    finished: list[UUID] = []

    for start in forward:
        if start in seen:
            continue

        stack = [(start, False)]

        while stack:
            node, expanded = stack.pop()

            if expanded:
                finished.append(node)
            elif node not in seen:
                seen.add(node)
                stack.append((node, True))
                stack.extend(
                    (target, False)
                    for target in reversed(forward[node])
                    if target not in seen
                )

    return finished


def _load_final_claims(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
) -> LPFinalClaims:
    """Rebuild expected inputs and consume only completed validated checkpoints.

    Parameters
    ----------
    as_lc_bundle
        Current authoritative validated upstream bundle.
    doc_key
        Document identity for the one framework.
    kg_config
        Current curriculum policy and operational settings.
    kg_dirs
        Generation directory; the caller holds its exclusive generation lock.

    Returns
    -------
    LPFinalClaims
        Complete direct claims, including inspectable cycle failures.

    Raises
    ------
    LPGenerationFailed
        If requests or processing-failure dispositions remain unfinished.
    ValueError
        If inputs or execution evidence are stale, incomplete, or inconsistent.
    """

    config = CreateKGConfig.model_validate_json(
        kg_config.model_dump_json(by_alias=True)
    )
    population = validate_lp_request_artifacts(
        as_lc_bundle=as_lc_bundle, doc_key=doc_key, kg_config=config, kg_dirs=kg_dirs
    )

    # Reconciliation never initializes or recovers generation. Only the generation
    # owner may resume an interrupted transaction or create missing checkpoints.
    if (
        not (kg_dirs.root / _RECEIPT).is_file()
        or (kg_dirs.root / _TRANSACTION).exists()
    ):
        raise ValueError(
            "LP generation must have a committed checkpoint receipt; resume generation first."
        )

    store = LPGenerationCheckpoints(
        material=lp_execution_material(kg_config=config, population=population),
        population=population,
        root=kg_dirs.root,
    )

    if (
        store.run_number < 1
        or any(len(rows) != len(population.requests) for rows in store.rows.values())
        or any(failure.resolved_run_number is None for failure in store.failures)
    ):
        raise LPGenerationFailed(
            "LP finalization requires complete successful adjudication."
        )

    claims = _reconcile_claims(population=population, store=store)
    diagnostics = _cycle_diagnostics(claims)
    material = {
        "checkpoint_receipt_byte_hash": hashlib.sha256(
            (kg_dirs.root / _RECEIPT).read_bytes()
        ).hexdigest(),
        "claims": [claim.model_dump(mode="json") for claim in claims],
        "cycle_diagnostics": diagnostics.model_dump(mode="json"),
        "decision_counts": _decision_counts(claims),
        "execution_material": store.material,
        "finalization_source_content_hash": hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
        "graph_status": "cyclic" if diagnostics.components else "acyclic",
        "request_manifest": population.manifest.model_dump(mode="json"),
        "total_claims": len(claims),
    }
    artifact = LPFinalClaims.model_validate(
        {**material, "content_hash": content_hash(material)}
    )
    read_lp_request_population(expected=population, root=kg_dirs.root)
    store.verify_bytes()

    if store.material != lp_execution_material(kg_config=config, population=population):
        raise ValueError("LP execution material changed during finalization.")

    return artifact


def _reconcile_claims(
    *, population: LPRequestPopulation, store: LPGenerationCheckpoints
) -> tuple[LPFinalClaim, ...]:
    """Preserve the complete selected judgment without manufacturing interpretations.

    Parameters
    ----------
    population
        Material-validated candidate and request population.
    store
        Complete validated drafts, checker verdicts, and selected responses.

    Returns
    -------
    tuple[LPFinalClaim, ...]
        Candidate-ordered claims with direct evidence and execution references.
    """

    candidates = {
        candidate.pair_id: candidate for candidate in population.candidates.candidates
    }
    claims = []

    for index, request in enumerate(population.requests):
        draft_row = store.rows["draft"][index]
        verdict_row = store.rows["verdict"][index]
        response_row = store.rows["response"][index]
        draft = LPGenerationResponse.model_validate(draft_row.payload)
        verdict = LPGenerationValidationVerdict.model_validate(verdict_row.payload)
        response = LPGenerationResponse.model_validate(response_row.payload)
        producers = {judgment.pair_id: judgment for judgment in draft.judgments}
        judgments = {judgment.pair_id: judgment for judgment in response.judgments}

        for pair in request.pairs:
            judgment = judgments[pair.pair_id]
            source, target = _claim_endpoints(judgment)
            claims.append(
                LPFinalClaim(
                    candidate=candidates[pair.pair_id],
                    judgment=judgment,
                    provenance=LPFinalClaimProvenance(
                        checker_checkpoint_content_hash=content_hash(
                            verdict_row.model_dump(mode="json")
                        ),
                        checker_outcome="accepted" if verdict.passed else "corrected",
                        checker_prompt_content_hash=verdict_row.prompt_content_hash,
                        checker_verdict_content_hash=verdict_row.payload_content_hash,
                        execution_content_hash=store.execution_hash,
                        judgment_content_hash=content_hash(
                            judgment.model_dump(mode="json")
                        ),
                        producer_checkpoint_content_hash=content_hash(
                            draft_row.model_dump(mode="json")
                        ),
                        producer_judgment=producers[pair.pair_id],
                        producer_prompt_content_hash=draft_row.prompt_content_hash,
                        producer_response_content_hash=draft_row.payload_content_hash,
                        request_content_hash=request.request_content_hash,
                        request_id=request.request_id,
                        response_checkpoint_content_hash=content_hash(
                            response_row.model_dump(mode="json")
                        ),
                        response_content_hash=response_row.payload_content_hash,
                    ),
                    source_sfi_uuid=source,
                    target_sfi_uuid=target,
                )
            )

    return tuple(claims)


def _representative_cycle(
    *, forward: dict[UUID, list[UUID]], members: tuple[UUID, ...]
) -> tuple[UUID, ...]:
    """Choose the smallest component node and a deterministic shortest return path.

    Parameters
    ----------
    forward
        UUID-sorted outgoing adjacency for the whole direct graph.
    members
        Sorted nodes in one cyclic strongly connected component.

    Returns
    -------
    tuple[UUID, ...]
        A closed directed path starting and ending at the smallest component UUID.

    Raises
    ------
    ValueError
        If the LP cyclic component has no representative return path.
    """

    allowed = set(members)
    start = members[0]
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


def _strong_components(
    *, forward: dict[UUID, list[UUID]], reverse: dict[UUID, list[UUID]]
) -> tuple[tuple[UUID, ...], ...]:
    """Partition the direct graph with iterative two-pass depth-first traversal.

    Parameters
    ----------
    forward
        Deterministically ordered outgoing adjacency.
    reverse
        Deterministically ordered incoming adjacency over the same nodes.

    Returns
    -------
    tuple[tuple[UUID, ...], ...]
        Every strongly connected component in canonical UUID order.
    """

    finished = _finishing_order(forward)
    seen: set[UUID] = set()
    components = []

    for start in reversed(finished):
        if start in seen:
            continue

        members = []
        pending = [start]
        seen.add(start)

        while pending:
            node = pending.pop()
            members.append(node)

            for target in reverse[node]:
                if target not in seen:
                    seen.add(target)
                    pending.append(target)

        components.append(tuple(sorted(members, key=str)))

    return tuple(sorted(components, key=lambda members: tuple(map(str, members))))


def _write_claims(*, artifact: LPFinalClaims, path: Path) -> None:
    """Atomically replace one generated artifact using canonical complete JSON.

    Parameters
    ----------
    artifact
        Reconstructed complete claims, including any cycle diagnostics.
    path
        Final-claims destination in the locked generation directory.
    """

    payload = (canonical_lp_json(artifact.model_dump(mode="json")) + "\n").encode(
        "utf-8"
    )
    temporary = None

    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, mode="wb", prefix=".lp_final_claims-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(dst=path, src=temporary)
        descriptor = os.open(flags=os.O_RDONLY, path=path.parent)

        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def finalize_learning_progressions(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
) -> LPFinalClaims:
    """Reconcile completed adjudication and write the complete direct claim artifact.

    Existing final claims are never semantic input: every call reconstructs them from
    validated upstream evidence. Cyclic claims are written for diagnosis before
    raising; no edge is deleted or converted, and no relationship or release record is
    emitted.

    Parameters
    ----------
    as_lc_bundle
        Current authoritative validated upstream bundle.
    doc_key
        Document key for the one framework.
    kg_config
        Current effective curriculum configuration.
    kg_dirs
        Directory containing complete candidate, request, and execution artifacts.

    Returns
    -------
    LPFinalClaims
        Complete acyclic claims, including nonpublishing negative and review outcomes.

    Raises
    ------
    LPFinalizationCycleError
        If the whole direct developmental graph contains any cycle.
    """

    with (kg_dirs.root / ".lp_generation.lock").open("rb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        artifact = _load_final_claims(
            as_lc_bundle=as_lc_bundle,
            doc_key=doc_key,
            kg_config=kg_config,
            kg_dirs=kg_dirs,
        )
        _write_claims(artifact=artifact, path=kg_dirs.root / _FINAL_CLAIMS)

        if artifact.cycle_diagnostics.components:
            raise LPFinalizationCycleError(artifact)

        return artifact


def validate_lp_final_claims_artifact(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
) -> LPFinalClaims:
    """Reject edited, stale, incomplete, or cyclic final claims by reconstruction.

    This read-only boundary revalidates all underlying adjudication. An artifact's own
    content hash is insufficient authority, even when recomputed after an edit.

    Parameters
    ----------
    as_lc_bundle
        Current authoritative validated upstream bundle.
    doc_key
        Document key for the one framework.
    kg_config
        Current effective curriculum configuration.
    kg_dirs
        Directory containing the final claims and their complete upstream evidence.

    Returns
    -------
    LPFinalClaims
        Exact current artifact after independent material and whole-graph checks.

    Raises
    ------
    LPFinalizationCycleError
        If matching persisted claims contain a directed cycle.
    ValueError
        If claims or upstream artifacts are inconsistent, edited, or stale.
    """

    with (kg_dirs.root / ".lp_generation.lock").open("rb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        artifact = _load_final_claims(
            as_lc_bundle=as_lc_bundle,
            doc_key=doc_key,
            kg_config=kg_config,
            kg_dirs=kg_dirs,
        )
        expected = (canonical_lp_json(artifact.model_dump(mode="json")) + "\n").encode(
            "utf-8"
        )

        if (kg_dirs.root / _FINAL_CLAIMS).read_bytes() != expected:
            raise ValueError(
                "LP final claims differ from current validated adjudication."
            )

        if artifact.cycle_diagnostics.components:
            raise LPFinalizationCycleError(artifact)

        return artifact
