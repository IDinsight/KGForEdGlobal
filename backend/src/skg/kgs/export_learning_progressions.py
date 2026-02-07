"""This module contains functionalities related to exporting the Learning Progressions
knowledge graph. It exports relationships between *exported* StandardsFrameworkItems
(SFIs) from an Academic Standards export.

- Relationships:
  - buildsTowards (SFI -> SFI), directional
  - relatesTo (SFI -- SFI), associative (canonicalized to a single directed edge)

The export is *shape-preserving* for the LC Knowledge Graph ontology and is designed to
work for non-US curriculum documents mapped into the LC "academic standards" shape.

Design principles
-----------------

1. Deterministic by default (stable ordering + UUIDv5 IDs).
2. Bounded candidate generation (top-K per source) to avoid dense graphs.
3. Optional LLM judging hook is non-blocking (skipped if disabled/unavailable).
4. Strong provenance on every emitted edge (heuristics + evidence pointers).

NB
--

1. Endpoints must reference exported SFI `case_identifier_uuid`.
2. This exporter assumes Academic Standards export has populated
    `sfi.metadata["progression_context"]` where possible (recommended).
"""

# Standard Library
import re

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import UUID, uuid5

# Third Party Library
from loguru import logger

# Package Library
from skg.kgs.export_academic_standards import (
    AcademicStandardsExport,
    _parse_code_features,
)
from skg.kgs.schemas import Relationship, StandardsFrameworkItem
from skg.kgs.utils import ExportContext, KGDirs, normalize_ws
from skg.schemas import CreateKGConfig
from skg.utils.constants import NodeRole, StatementRole
from skg.utils.general import write_to_json


@dataclass(frozen=True)
class CandidateEdge:
    """Internal candidate edge representation (pre-Relationship emission)."""

    confidence: float  # 0..1 heuristic or final
    evidence: dict[str, Any]
    inference_source: str  # "inferred" | "llm"
    inference_type: str  # Module name, e.g. "grade_order"
    metadata: dict[str, Any]
    rel_type: str  # "buildsTowards" | "relatesTo"
    source_sfi_uuid: UUID
    target_sfi_uuid: UUID
    heuristic_confidence: Optional[float] = None
    llm_confidence: Optional[float] = None


@dataclass
class LearningProgressionsExport:
    """The output of exporting Learning Progressions KG artifacts."""

    builds_towards_relationships: list[Relationship]
    graph_bundle: dict[str, Any]
    relates_to_relationships: list[Relationship]
    report: dict[str, Any]


def _bound_candidate_pool(
    *, candidates: list[CandidateEdge], per_source: int
) -> list[CandidateEdge]:
    """Bound the candidate pool to top-K per source SFI, sorted by confidence then
    target UUID.

    Parameters
    ----------
    candidates
        List of candidate edges to filter.
    per_source
        Maximum number of outgoing edges to keep per source SFI. If <= 0, no filtering
        is applied.

    Returns
    -------
    list[CandidateEdge]
        Filtered list of candidate edges.
    """

    if per_source <= 0:
        return candidates

    grouped: dict[UUID, list[CandidateEdge]] = {}

    for c in candidates:
        grouped.setdefault(c.source_sfi_uuid, []).append(c)

    output: list[CandidateEdge] = []

    for src in sorted(grouped.keys(), key=str):
        cs = grouped[src]
        cs_sorted = sorted(cs, key=lambda x: (-x.confidence, str(x.target_sfi_uuid)))
        output.extend(cs_sorted[:per_source])

    return output


def _build_learning_progressions_graph_bundle(
    *, ctx: ExportContext, export_dialect: str, relationships: list[Relationship]
) -> dict[str, Any]:
    """Build a shape-preserving graph bundle for Learning Progressions export.

    Parameters
    ----------
    ctx
        ExportContext (doc_key, framework metadata, indexes).
    export_dialect
        Export dialect string to include in the bundle metadata.
    relationships
        List of Relationship objects to include in the bundle.

    Returns
    -------
    dict[str, Any]
        Graph bundle dictionary ready for JSON serialization.
    """

    generated_at = datetime.now(timezone.utc).isoformat()
    nodes: list[dict[str, Any]] = []
    rels: list[dict[str, Any]] = []

    for rel in relationships:
        rels.append(
            {
                "id": str(rel.identifier),
                "type": (
                    "BUILDS_TOWARDS"
                    if rel.relationship_type == "buildsTowards"
                    else "RELATES_TO"
                ),
                "start": str(rel.source_entity_value),
                "end": str(rel.target_entity_value),
                "properties": rel.model_dump(mode="json"),
            }
        )

    return {
        "doc_key": ctx.doc_key,
        "export_dialect": export_dialect,
        "generated_at": generated_at,
        "graph_type": "learning_progressions",
        "included_graph_types": ["learning_progressions"],
        "nodes": nodes,
        "relationships": rels,
    }


def _canonicalize_edge(candidate: CandidateEdge) -> CandidateEdge:
    """Ensure 'relatesTo' edges are stored as an undirected pair by enforcing a
    deterministic (lexicographical) order of UUIDs.

    Parameters
    ----------
    candidate
        The CandidateEdge to canonicalize if it's a 'relatesTo' edge.

    Returns
    -------
    CandidateEdge
        A CandidateEdge with 'relatesTo' edges ordered by source and target UUIDs to
        ensure undirected canonical form; other edges are returned unchanged.
    """

    if candidate.rel_type != "relatesTo":
        return candidate

    src = candidate.source_sfi_uuid
    tgt = candidate.target_sfi_uuid

    # If already in order, return original.
    if str(src) <= str(tgt):
        return candidate

    # Swap to enforce undirected canonical form.
    return CandidateEdge(
        confidence=candidate.confidence,
        evidence=candidate.evidence,
        heuristic_confidence=candidate.heuristic_confidence,
        inference_source=candidate.inference_source,
        inference_type=candidate.inference_type,
        llm_confidence=candidate.llm_confidence,
        metadata=candidate.metadata,
        rel_type="relatesTo",
        source_sfi_uuid=tgt,  # Swapped
        target_sfi_uuid=src,  # Swapped
    )


def _check_level_constraints(
    *,
    candidate: CandidateEdge,
    config: CreateKGConfig,
    features_by_uuid: dict[UUID, dict[str, Any]],
) -> str | None:
    """Check if the edge violates adjacent level constraints.

    Parameters
    ----------
    candidate
        The CandidateEdge to check for level constraints.
    config
        The CreateKGConfig containing settings for progression constraints.
    features_by_uuid
        A dictionary mapping SFI UUIDs to their extracted features, used to determine
        level ordinals for the source and target SFIs.

    Returns
    -------
    str | None
        A string indicating the reason for dropping the edge if it violates level
        constraints (e.g., "non_adjacent_levels", "within_level_blocked",
        "unknown_level_ordinal"), or None if the edge satisfies the constraints.
    """

    if not (
        config.progression_only_adjacent_levels
        and candidate.rel_type == "buildsTowards"
    ):
        return None

    fs = features_by_uuid[candidate.source_sfi_uuid]
    ft = features_by_uuid[candidate.target_sfi_uuid]

    # For stage_order candidates, enforce adjacency using stage ordinals even if a
    # grade axis is present (level_ordinal_* prefers grade when available).
    level_field = (
        "stage_ordinal_low"
        if candidate.inference_type == "stage_order"
        else "level_ordinal_low"
    )
    ls = fs.get(level_field)
    lt = ft.get(level_field)

    if not (isinstance(ls, int) and isinstance(lt, int)):
        return "unknown_level_ordinal"

    delta = lt - ls

    if delta == 0:
        if candidate.inference_type != "scope_sequence":
            return "within_level_blocked"
    elif delta != 1:
        return "non_adjacent_levels"

    return None


def _check_subject_constraints(
    *,
    candidate: CandidateEdge,
    config: CreateKGConfig,
    features_by_uuid: dict[UUID, dict[str, Any]],
) -> str | None:
    """Check if the edge violates subject constraints.

    Parameters
    ----------
    candidate
        The CandidateEdge to check for subject constraints.
    config
        The CreateKGConfig containing settings for progression constraints.
    features_by_uuid
        A dictionary mapping SFI UUIDs to their extracted features, used to determine
        the subjects for the source and target SFIs.

    Returns
    -------
    str | None
        A string indicating the reason for dropping the edge if it violates subject
        constraints (e.g., "cross_subject_blocked"), or None if the edge satisfies the
        constraints.
    """

    if config.progression_allow_cross_subject:
        return None

    def _get_subject_feature(uuid: UUID) -> str:
        """Helper to extract the subject string for a given UUID.

        Parameters
        ----------
        uuid
            The UUID of the SFI for which to extract the subject.

        Returns
        -------
        str
            The subject string for the given UUID, extracted from features; defaults to
            empty string if not found.
        """

        feats = features_by_uuid.get(uuid, {})
        return feats.get("local_subject_key") or feats.get("academic_subject", "")

    s_subj = _get_subject_feature(candidate.source_sfi_uuid)
    t_subj = _get_subject_feature(candidate.target_sfi_uuid)

    if normalize_ws(s_subj) != normalize_ws(t_subj):
        return "cross_subject_blocked"

    return None


def _choose_granularity(
    *,
    configured: str,
    features_by_uuid: dict[UUID, dict[str, Any]],
    sfis: list[StandardsFrameworkItem],
) -> str:
    """Choose coarse/fine/auto granularity in a deterministic, simple way.

    Parameters
    ----------
    configured
        The granularity setting from the config ("coarse", "fine", or "auto").
    features_by_uuid
        Precomputed features for each SFI, keyed by case_identifier_uuid.
    sfis
        List of StandardsFrameworkItems to consider for auto heuristic.

    Returns
    -------
    str
        The chosen granularity ("coarse" or "fine").
    """

    if configured in {"coarse", "fine"}:
        return configured

    # `auto` heuristic: If we have multiple grade ordinals across expectation SFIs,
    # prefer fine.
    exp = [s for s in sfis if s.normalized_statement_type == "Standard"]
    ords = []

    for s in exp:
        f = features_by_uuid.get(s.case_identifier_uuid) or {}
        o = f.get("level_ordinal_low")

        if isinstance(o, int):
            ords.append(o)

    distinct = sorted(set(ords))

    if len(exp) >= 10 and len(distinct) >= 2:
        return "fine"

    return "coarse"


def _choose_level_axis(
    *,
    grade_high: Optional[int],
    grade_key: Optional[str],
    grade_low: Optional[int],
    stage_high: Optional[int],
    stage_key: Optional[str],
    stage_low: Optional[int],
) -> tuple[str, str, Optional[int], Optional[int]]:
    """Choose the primary level axis (grade vs. stage) for threading and reporting,
    based on presence of grade vs. stage features. Prefer grade when available, else
    stage, else none.

    Parameters
    ----------
    grade_high
        The high end of the grade ordinal range, if available.
    grade_key
        The grade key string, if available.
    grade_low
        The low end of the grade ordinal range, if available.
    stage_high
        The high end of the stage ordinal range, if available.
    stage_key
        The stage key string, if available.
    stage_low
        The low end of the stage ordinal range, if available.

    Returns
    -------
    tuple[str, str, Optional[int], Optional[int]]
        A tuple of (level_type, level_key, level_ordinal_low, level_ordinal_high).
    """

    if grade_low is not None:
        return "grade", str(grade_key or ""), grade_low, grade_high

    if stage_low is not None:
        return "stage", str(stage_key or ""), stage_low, stage_high

    return "none", "", None, None


def _code_key_without_grade(
    *, code_features: dict[str, Any], grade_low: Optional[int]
) -> str:
    """Exact code key with grade stripped when the first segment matches grade.

    Parameters
    ----------
    code_features
        The pre-parsed code features dictionary for an SFI.
    grade_low
        The low end of the grade ordinal range, if available.

    Returns
    -------
    str
        A string key representing the code without the grade segment, if it can be
        determined; otherwise the full code key.
    """

    segs = code_features.get("code_segments") or []

    if not isinstance(segs, list) or not segs:
        return ""

    if grade_low is None:
        return ".".join([str(x) for x in segs])

    # Best-effort: match numeric first segment.
    try:
        first = int(str(segs[0]))
    except Exception:  # pylint: disable=broad-except
        first = None

    if first == grade_low and len(segs) >= 2:
        return ".".join([str(x) for x in segs[1:]])

    return ".".join([str(x) for x in segs])


def _code_sort_key(f: dict[str, Any]) -> tuple[Any, ...]:
    """Stable ordering by (level, code_tuple).

    Parameters
    ----------
    f
        The features dictionary for an SFI.

    Returns
    -------
    tuple[Any, ...]
        A tuple key for sorting SFIs by level ordinal (with missing as very high) and
        then code tuple (normalized to all strings, with missing as empty).
    """

    cf = f.get("code_features") or {}

    # Level ordinal (defaulting to a very high value if missing).
    lvl = f.get("level_ordinal_low")
    lvl_key = int(lvl) if isinstance(lvl, int) else 10**9

    # code_tuple normalization.
    ct = cf.get("code_tuple")
    if isinstance(ct, (list, tuple)):
        # Normalize to (int_priority, value) to ensure ints and strings are comparable.
        ct_key = tuple((0, x) if isinstance(x, int) else (1, str(x)) for x in ct)
    else:
        ct_key = ()

    # Final key including UUID for absolute stability.
    return lvl_key, ct_key, str(f.get("sfi_uuid"))


def _compute_local_subject_key(*, ctx: ExportContext, node_id: str) -> str:
    """Compute a local subject key for an SFI based on its nearest subject and learning
    area ancestors.

    Parameters
    ----------
    ctx
        The ExportContext containing the node and edge indexes.
    node_id
        The node ID for which to compute the local subject key.

    Returns
    -------
    str
        A string representing the local subject key for the given node ID, constructed
        by traversing up the node's ancestry to find the nearest subject and learning
        area, and building a key based on their labels or IDs.
    """

    cur: Optional[str] = node_id
    subject_id: Optional[str] = None
    learning_area_id: Optional[str] = None
    seen: set[str] = set()

    # Walk upward from the node toward root; capture nearest subject, and (optionally)
    # learning area.
    while cur and cur != ctx.root_id and cur not in seen:
        seen.add(cur)
        n = ctx.nodes_by_id.get(cur) or {}
        role = str(n.get("role") or "")

        if role == NodeRole.SUBJECT.value and subject_id is None:
            subject_id = cur  # Nearest subject
        elif role == NodeRole.LEARNING_AREA.value and learning_area_id is None:
            learning_area_id = cur

        # If we have both, we can stop early.
        if subject_id and learning_area_id:
            break

        nxt = ctx.parent_by_child.get(cur)
        if nxt == cur:  # Self-loop guard
            break
        cur = nxt

    # Build label-based key parts (grade-insensitive).
    if subject_id:
        subj_node = ctx.nodes_by_id.get(subject_id) or {}
        subj_label = _node_label_for_path(subj_node) or ""
        subj_part = (
            f"{NodeRole.SUBJECT.value}:{_slugify(subj_label)}"
            if subj_label
            else f"{NodeRole.SUBJECT.value}:{subject_id}"
        )

        if learning_area_id:
            la_node = ctx.nodes_by_id.get(learning_area_id) or {}
            la_label = _node_label_for_path(la_node) or ""
            la_part = (
                f"{NodeRole.LEARNING_AREA.value}:{_slugify(la_label)}"
                if la_label
                else f"{NodeRole.LEARNING_AREA.value}:{learning_area_id}"
            )
            return f"{la_part}|{subj_part}"

        return subj_part

    if learning_area_id:
        la_node = ctx.nodes_by_id.get(learning_area_id) or {}
        la_label = _node_label_for_path(la_node) or ""
        return (
            f"{NodeRole.LEARNING_AREA.value}:{_slugify(la_label)}"
            if la_label
            else f"{NodeRole.LEARNING_AREA.value}:{learning_area_id}"
        )

    # Fallback: keep it deterministic and non-empty.
    return f"node:{node_id}"


def _compute_module_stats(
    *,
    features_by_uuid: dict[UUID, dict[str, Any]],
    module_name: str,
    sfis_by_subject: dict[str, list[UUID]],
) -> dict[str, Any]:
    """Compute statistics about the presence of key features for a given inference
    module, to help understand the coverage and potential impact of each module on the
    candidate pool. The specific stats computed depend on the module type.

    Parameters
    ----------
    features_by_uuid
        A dictionary mapping SFI UUIDs to their extracted features.
    module_name
        The name of the inference module (e.g., "grade_order", "scope_sequence",
        "code_pattern").
    sfis_by_subject
        A dictionary mapping academic subjects to lists of SFI UUIDs in that subject.

    Returns
    -------
    dict[str, Any]
        A dictionary of computed statistics relevant to the specified module.
    """

    total_sfis = sum(len(v) for v in sfis_by_subject.values())

    # Grade/stage modules need to be explicit about which ordinal axis they are
    # measuring.
    if module_name in {"grade_order", "stage_order"}:
        level_field = (
            "stage_ordinal_low" if module_name == "stage_order" else "level_ordinal_low"
        )
        stats = _stats_grade_order(
            features_by_uuid=features_by_uuid,
            level_field=level_field,
            sfis_by_subject=sfis_by_subject,
        )
        stats["sfis_total"] = total_sfis
        return stats

    # Map module names to their specific handler functions.
    handlers: dict[str, Callable[..., dict[str, Any]]] = {
        "scope_sequence": _stats_scope_sequence,
        "code_pattern": _stats_code_pattern,
    }

    handler = handlers.get(module_name)

    if handler:
        stats = handler(features_by_uuid, sfis_by_subject)
        stats["sfis_total"] = total_sfis
        return stats

    return {"sfis_total": total_sfis, "note": "unimplemented_or_unknown_module"}


def _compute_topic_path_key(*, ctx: ExportContext, node_id: str) -> str:
    """Compute a topic path key that excludes grade/stage and statement roles.

    Parameters
    ----------
    ctx
        The ExportContext containing the node and edge indexes.
    node_id
        The node ID for which to compute the topic path key.

    Returns
    -------
    str
        A string representing the topic path key for the given node ID, constructed by
        traversing up the node's ancestry and concatenating role=slug(label) pairs,
        while excluding certain roles.
    """

    exclude_roles = {
        NodeRole.FRAMEWORK.value,
        NodeRole.GRADE_LEVEL.value,
        NodeRole.STAGE.value,
        NodeRole.PROSE.value,
        NodeRole.UNRESOLVED.value,
        StatementRole.EXPECTATION.value,
        StatementRole.DESCRIPTOR.value,
        StatementRole.GUIDANCE.value,
    }

    chain: list[str] = []
    cur: Optional[str] = node_id
    seen: set[str] = set()

    while cur and cur != ctx.root_id and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        nxt = ctx.parent_by_child.get(cur)
        if nxt == cur:  # self-loop guard
            break
        cur = nxt

    chain.reverse()  # root -> ... -> node

    parts: list[str] = []

    for nid in chain:
        n = ctx.nodes_by_id.get(nid) or {}
        role = str(n.get("role") or "")

        if not role or role in exclude_roles:
            continue

        label = _node_label_for_path(n)

        if not label:
            continue

        parts.append(f"{role}={_slugify(label)}")

    return "|".join(parts)


def _create_edge(
    *,
    conf: float,
    extra_evidence: dict[str, Any],
    inference_type: str,
    src_uid: UUID,
    subject_key: str,
    tgt_uid: UUID,
    thread_key: str,
    thread_type: str,
) -> CandidateEdge:
    """Factory for creating CandidateEdge objects.

    Parameters
    ----------
    conf
        The confidence score for the edge (0..1).
    extra_evidence
        A dictionary of additional evidence to include in the edge's evidence field.
    inference_type
        A string indicating the type of inference (e.g., "grade_order", "code_pattern").
    src_uid
        The UUID of the source SFI.
    subject_key
        The local subject key associated with the edge, used for evidence.
    tgt_uid
        The UUID of the target SFI.
    thread_key
        The key of the thread (e.g., grade_key or stage_key) that this edge is part of,
        used for evidence.
    thread_type
        The type of thread (e.g., "grade", "stage") that this edge is part of, used for
        evidence.

    Returns
    -------
    CandidateEdge
        A CandidateEdge object constructed with the provided parameters and evidence.
    """

    return CandidateEdge(
        confidence=conf,
        evidence={
            "thread_type": thread_type,
            "thread_key": thread_key,
            "adjacent_level": True,
            "local_subject_key": subject_key,
            **extra_evidence,
        },
        heuristic_confidence=conf,
        inference_source="inferred",
        inference_type=inference_type,
        metadata={},
        rel_type="buildsTowards",
        source_sfi_uuid=src_uid,
        target_sfi_uuid=tgt_uid,
    )


def _dedupe_candidates_pre_pool(candidates: list[CandidateEdge]) -> list[CandidateEdge]:
    """Deterministically dedupe candidates *before* pool bounding so duplicates don't
    consume top-K slots.

    Dedupe key is (source_uuid, target_uuid, rel_type) after canonicalizing relatesTo.
    We keep the highest-confidence candidate; ties are broken deterministically by
    (inference_source, inference_type).

    Parameters
    ----------
    candidates
        List of CandidateEdge objects to deduplicate.

    Returns
    -------
    list[CandidateEdge]
        A deduplicated list of CandidateEdge objects, where duplicates (same source,
        target, and relationship type) have been removed in favor of the highest
        confidence edge, with ties broken by inference source and type.
    """

    best: dict[tuple[str, str, str], CandidateEdge] = {}

    for c in candidates:
        c = _canonicalize_edge(c)
        key = (str(c.source_sfi_uuid), str(c.target_sfi_uuid), c.rel_type)
        prev = best.get(key)

        if prev is None:
            best[key] = c
            continue
        if c.confidence > prev.confidence:
            best[key] = c
            continue
        if c.confidence == prev.confidence:
            if (c.inference_source, c.inference_type) < (
                prev.inference_source,
                prev.inference_type,
            ):
                best[key] = c

    return sorted(
        best.values(),
        key=lambda x: (
            str(x.source_sfi_uuid),
            -x.confidence,
            x.rel_type,
            x.inference_source,
            x.inference_type,
            str(x.target_sfi_uuid),
        ),
    )


def _emit_progression_relationship(
    *,
    candidate: CandidateEdge,
    config: CreateKGConfig,
    doc_key: str,
    features_by_uuid: dict[UUID, dict[str, Any]],
    fw_metadata: dict[str, Any],
    granularity: str,
) -> Relationship:
    """Emit a Relationship object for a candidate edge with deterministic UUIDv5.

    Parameters
    ----------
    candidate
        The CandidateEdge for which to emit a Relationship.
    config
        The CreateKGConfig containing attribution and namespace information.
    doc_key
        The document key for the export, used in UUID generation.
    features_by_uuid
        A dictionary mapping SFI UUIDs to their extracted features, used for provenance.
    fw_metadata
        The metadata dictionary for the framework, to include in the relationship
        metadata.
    granularity
        The chosen granularity ("coarse" or "fine") to include in the relationship
        metadata.

    Returns
    -------
    Relationship
        A Relationship object representing the candidate edge, with a deterministic
        UUID and rich metadata for provenance.
    """

    rel_type = candidate.rel_type
    src_uuid = candidate.source_sfi_uuid
    tgt_uuid = candidate.target_sfi_uuid

    # Canonicalize relatesTo ordering for deterministic IDs.
    if rel_type == "relatesTo" and str(tgt_uuid) < str(src_uuid):
        src_uuid, tgt_uuid = tgt_uuid, src_uuid

    edge_id = uuid5(
        config.namespace_uuid,
        f"lc:curriculum:{doc_key}:rel:{rel_type}:{src_uuid}:{tgt_uuid}",
    )

    metadata: dict[str, Any] = {
        "source_kg": "learning_progressions",
        "framework": fw_metadata,
        "learning_progression_provenance": {
            "inference_source": candidate.inference_source,
            "inference_type": candidate.inference_type,
            "confidence": candidate.confidence,
            "heuristic_confidence": candidate.heuristic_confidence,
            "llm_confidence": candidate.llm_confidence,
            "evidence": candidate.evidence,
            "granularity": granularity,
        },
    }

    # Attach canonical pointers when available.
    fs = features_by_uuid.get(candidate.source_sfi_uuid, {})
    ft = features_by_uuid.get(candidate.target_sfi_uuid, {})
    metadata["learning_progression_provenance"]["canonical_pointers"] = {
        "source": {
            "canonical_node_id": fs.get("canonical_node_id"),
            "topic_path_key": fs.get("topic_path_key"),
            "grade_key": fs.get("grade_key"),
            "stage_key": fs.get("stage_key"),
        },
        "target": {
            "canonical_node_id": ft.get("canonical_node_id"),
            "topic_path_key": ft.get("topic_path_key"),
            "grade_key": ft.get("grade_key"),
            "stage_key": ft.get("stage_key"),
        },
    }

    return Relationship(
        attribution_statement=config.attribution_statement,
        author=config.author,
        description="",
        identifier=edge_id,
        license=config.license,
        metadata=metadata,
        provider=config.provider,
        relationship_type=rel_type,
        source_entity="StandardsFrameworkItem",
        source_entity_key="case_identifier_uuid",
        source_entity_value=str(src_uuid),
        target_entity="StandardsFrameworkItem",
        target_entity_key="case_identifier_uuid",
        target_entity_value=str(tgt_uuid),
    )


def _enforce_dag_builds_towards(
    *, candidates: list[CandidateEdge], report: dict[str, Any]
) -> list[CandidateEdge]:
    """Remove the lowest-confidence edge in each detected cycle until acyclic.

    Parameters
    ----------
    candidates
        List of CandidateEdge objects representing the candidate progression edges.
    report
        A dictionary to which information about removed cycles will be added for
        reporting.

    Returns
    -------
    list[CandidateEdge]
        A list of CandidateEdge objects with cycles removed from the "buildsTowards"
        edges.
    """

    builds = [c for c in candidates if c.rel_type == "buildsTowards"]
    others = [c for c in candidates if c.rel_type != "buildsTowards"]
    edge_map: dict[tuple[str, str], CandidateEdge] = {
        (str(c.source_sfi_uuid), str(c.target_sfi_uuid)): c for c in builds
    }

    def _adj_list() -> dict[str, list[str]]:
        """Build adjacency list for current edges.

        Returns
        -------
        dict[str, list[str]]
            A dictionary representing the adjacency list of the current "buildsTowards"
            edges, where keys are source SFI UUIDs as strings and values are lists of
            target SFI UUIDs as strings.
        """

        a: dict[str, list[str]] = {}

        for s, t in edge_map.keys():
            a.setdefault(s, []).append(t)

        # Stable ordering.
        for k in a:
            a[k] = sorted(a[k])

        return a

    removed: list[dict[str, Any]] = []

    while True:
        cycle = _find_cycle(_adj_list())

        if not cycle:
            break

        # Cycle is list of (src, tgt) string pairs -> remove the lowest-confidence edge
        # among them.
        worst = None
        worst_conf = 10.0

        for s, t in cycle:
            e = edge_map.get((s, t))

            if e and e.confidence < worst_conf:
                worst = (s, t)
                worst_conf = e.confidence

        if worst is None:
            break

        e = edge_map.pop(worst)
        removed.append(
            {
                "removed_edge": {
                    "source": str(e.source_sfi_uuid),
                    "target": str(e.target_sfi_uuid),
                    "confidence": e.confidence,
                    "inference_type": e.inference_type,
                },
                "cycle_edges": cycle,
            }
        )

    if removed:
        report["cycles_removed"] = removed

    return list(edge_map.values()) + others


def _extract_progression_features(
    *, ctx: ExportContext, sfi: StandardsFrameworkItem
) -> dict[str, Any]:
    """Extract deterministic progression features for an SFI. Prefer
    `sfi.metadata["progression_context"]` if present.

    Parameters
    ----------
    ctx
        The ExportContext containing indexes for canonical pointers.
    sfi
        The StandardsFrameworkItem for which to extract features.

    Returns
    -------
    dict[str, Any]
        A dictionary of extracted features for the given SFI, including canonical
        pointers, grade/stage information, topic path key, code features, and ordering
        helpers.
    """

    metadata = sfi.metadata or {}
    pc = metadata.get("progression_context") or {}

    # Resolve identity and provenance.
    canonical_node_id = metadata.get("canonical_node_id") or metadata.get(
        "canonical_node"
    )
    canonical_node_role = (
        metadata.get("canonical_node_role") or metadata.get("role") or ""
    )
    provenance = _extract_provenance_fields(metadata)

    # Resolve grades and stages. NB: Stage keys come directly from PC; grade keys may
    # require parsing SFI.
    grade_info = _resolve_grade_info(pc=pc, sfi=sfi)

    stage_key = pc.get("stage_key")
    stage_low = pc.get("stage_ordinal_low")
    stage_high = pc.get("stage_ordinal_high")

    level_type, level_key, level_low, level_high = _choose_level_axis(
        grade_key=grade_info["key"],
        grade_low=grade_info["low"],
        grade_high=grade_info["high"],
        stage_key=stage_key,
        stage_low=stage_low,
        stage_high=stage_high,
    )

    # Resolve content keys.
    topic_path_key = _resolve_topic_path(ctx=ctx, node_id=canonical_node_id, pc=pc)
    local_subject_key = _resolve_subject_key(
        ctx=ctx, node_id=canonical_node_id, pc=pc, sfi=sfi
    )

    # Resolve code features.
    code_features = (
        _resolve_code_features(grade_low=grade_info["low"], pc=pc, sfi=sfi) or {}
    )
    code_key_wo_grade = _code_key_without_grade(
        code_features=code_features, grade_low=grade_info["low"]
    )

    # Resolve ordering.
    canon_order_path, order_index = _resolve_ordering(
        ctx=ctx, node_id=canonical_node_id, pc=pc
    )

    return {
        "academic_subject": sfi.academic_subject or "",
        "bbox": provenance["bbox"],
        "canon_order_path": canon_order_path,
        "canonical_node_id": canonical_node_id,
        "canonical_node_role": canonical_node_role,
        "code_features": code_features,
        "code_key_without_grade": code_key_wo_grade or "",
        "description": sfi.description,
        "grade_key": grade_info["key"],
        "grade_ordinal_high": grade_info["high"],
        "grade_ordinal_low": grade_info["low"],
        "level_key": level_key,
        "level_ordinal_high": level_high,
        "level_ordinal_low": level_low,
        "level_type": level_type,
        "local_subject_key": local_subject_key or "",
        "normalized_statement_type": sfi.normalized_statement_type,
        "order_index_within_parent": order_index,
        "page_indices": provenance["page_indices"],
        "sfi_uuid": sfi.case_identifier_uuid,
        "statement_code": sfi.statement_code or "",
        "source_decision_ids": provenance["source_decision_ids"],
        "source_segment_ids": provenance["source_segment_ids"],
        "stage_key": stage_key,
        "stage_ordinal_high": stage_high,
        "stage_ordinal_low": stage_low,
        "topic_path_key": topic_path_key or "",
    }


def _extract_provenance_fields(metadata: dict[str, Any]) -> dict[str, Any]:
    """Extract list-based provenance fields.

    Parameters
    ----------
    metadata
        The metadata dictionary from which to extract provenance fields.

    Returns
    -------
    dict[str, Any]
        A dictionary containing the extracted provenance fields, including "bbox",
        "page_indices", "source_segment_ids", and "source_decision_ids".
    """

    _raw_indices = metadata.get("page_indices")
    page_indices = (
        _raw_indices
        if isinstance(_raw_indices, list)
        and all(isinstance(x, int) for x in _raw_indices)
        else []
    )

    def _get_list(key: str) -> list[str]:
        """Helper to extract list-based fields.

        Parameters
        ----------
        key
            The key in the metadata dictionary to extract as a list of strings.

        Returns
        -------
        list[str]
            The value associated with the key if it is a list of strings; otherwise, an
            empty list.
        """

        val = metadata.get(key)
        return val if isinstance(val, list) else []

    return {
        "bbox": metadata.get("bbox"),
        "page_indices": page_indices,
        "source_segment_ids": _get_list("source_segment_ids"),
        "source_decision_ids": _get_list("source_decision_ids"),
    }


def _find_cycle(adj_list: dict[str, list[str]]) -> Optional[list[tuple[str, str]]]:
    """Return one directed cycle as list of edges (src,tgt) if any.

    Parameters
    ----------
    adj_list
        Adjacency list representing the directed graph, where keys are node identifiers
        and values are lists of adjacent node identifiers.

    Returns
    -------
    Optional[list[tuple[str, str]]]
        A list of edges (src, tgt) representing a detected cycle, or None if no cycle
        exists.
    """

    parent: dict[str, str] = {}
    stack: set[str] = set()
    visited: set[str] = set()

    def dfs(u: str) -> Optional[list[tuple[str, str]]]:
        """Depth-first search to detect cycles starting from node u.

        Parameters
        ----------
        u
            The current node identifier being visited.

        Returns
        -------
        Optional[list[tuple[str, str]]]
            A list of edges (src, tgt) representing a detected cycle, or None if no
            cycle is found starting from node u.
        """

        visited.add(u)
        stack.add(u)

        for v in adj_list.get(u, []):
            if v not in visited:
                parent[v] = u
                cyc = dfs(v)

                if cyc:
                    return cyc
            elif v in stack:
                # Back edge u -> v forms a cycle.
                cycle_edges: list[tuple[str, str]] = [(u, v)]
                cur = u

                while cur != v and cur in parent:
                    p = parent[cur]
                    cycle_edges.append((p, cur))
                    cur = p

                cycle_edges.reverse()
                return cycle_edges

        stack.remove(u)
        return None

    for u in sorted(adj_list.keys()):
        if u in visited:
            continue

        cyc = dfs(u)

        if cyc:
            return cyc

    return None


def _infer_code_pattern(
    *,
    config: CreateKGConfig,
    features_by_uuid: dict[UUID, dict[str, Any]],
    sfis_by_subject: dict[str, list[UUID]],
) -> list[CandidateEdge]:
    """Progression edges inferred from increasing code tuples within a thread.

    Parameters
    ----------
    config
        The CreateKGConfig containing configuration options.
    features_by_uuid
        A dictionary mapping SFI UUIDs to their extracted features.
    sfis_by_subject
        A dictionary mapping academic subjects to lists of SFI UUIDs in that subject.

    Returns
    -------
    list[CandidateEdge]
        A list of CandidateEdge objects representing inferred progression edges based
        on code patterns.
    """

    output: list[CandidateEdge] = []

    for subj, uuids in sfis_by_subject.items():
        threads: dict[str, list[UUID]] = {}

        for uid in uuids:
            f = features_by_uuid[uid]
            cf = f.get("code_features") or {}
            stem = normalize_ws(
                str(cf.get("code_stem_without_grade") or cf.get("code_stem") or "")
            )

            if not stem or cf.get("code_tuple") is None:
                continue

            threads.setdefault(stem, []).append(uid)

        for stem, tids in threads.items():
            ordered = sorted(tids, key=lambda u: _code_sort_key(features_by_uuid[u]))

            if len(ordered) < 2:
                continue

            for a, b in zip(ordered, ordered[1:]):
                fa = features_by_uuid[a]
                fb = features_by_uuid[b]

                # Adjacent levels rule (best-effort).
                la = fa.get("level_ordinal_low")
                lb = fb.get("level_ordinal_low")

                is_adjacent: Optional[bool] = None

                if isinstance(la, int) and isinstance(lb, int):
                    is_adjacent = (lb - la) == 1

                if config.progression_only_adjacent_levels is True:
                    # If we can't prove adjacency, don't emit.
                    if is_adjacent is not True:
                        continue

                    conf = 0.95
                else:
                    # Confidence: reserve 0.95 for truly-adjacent levels; otherwise
                    # 0.88.
                    conf = 0.95 if is_adjacent is True else 0.88

                output.append(
                    CandidateEdge(
                        confidence=conf,
                        evidence={
                            "code_stem": stem,
                            "local_subject_key": subj,
                            "adjacent_level_enforced": bool(
                                config.progression_only_adjacent_levels
                            ),
                            "is_adjacent_level": (
                                bool(is_adjacent) if is_adjacent is not None else None
                            ),
                            "level_delta": (
                                (lb - la)
                                if isinstance(la, int) and isinstance(lb, int)
                                else None
                            ),
                        },
                        heuristic_confidence=conf,
                        inference_source="inferred",
                        inference_type="code_pattern",
                        metadata={},
                        rel_type="buildsTowards",
                        source_sfi_uuid=a,
                        target_sfi_uuid=b,
                    )
                )

    return output


def _infer_grade_order(
    *,
    features_by_uuid: dict[UUID, dict[str, Any]],
    inference_type: str = "grade_order",
    sfis_by_subject: dict[str, list[UUID]],
) -> list[CandidateEdge]:
    """Threaded grade/stage adjacency progression.

    Parameters
    ----------
    features_by_uuid
        A dictionary mapping SFI UUIDs to their extracted features.
    inference_type
        The type of inference being performed ("grade_order" or "stage_order").
    sfis_by_subject
        A dictionary mapping academic subjects to lists of SFI UUIDs in that subject.

    Returns
    -------
    list[CandidateEdge]
        A list of CandidateEdge objects representing inferred progression edges based
        on grade or stage order.
    """

    output: list[CandidateEdge] = []

    for subj, uuids in sfis_by_subject.items():
        # Build threads keyed by (thread_type, thread_key).
        threads: dict[tuple[str, str], list[UUID]] = {}

        for uid in uuids:
            f = features_by_uuid[uid]
            thread = _thread_key_for_grade_order(f)

            if thread:
                threads.setdefault(thread, []).append(uid)

        # Process each thread.
        for (thread_type, thread_key), tids in threads.items():
            edges = _process_thread_levels(
                features_by_uuid=features_by_uuid,
                inference_type=inference_type,
                subject=subj,
                thread_key=thread_key,
                thread_type=thread_type,
                tids=tids,
            )
            output.extend(edges)

    return output


def _infer_scope_sequence(
    *,
    features_by_uuid: dict[UUID, dict[str, Any]],
    sfis_by_subject: dict[str, list[UUID]],
) -> list[CandidateEdge]:
    """Within-level sequential progressions based on canonical order.

    Parameters
    ----------
    features_by_uuid
        A dictionary mapping SFI UUIDs to their extracted features.
    sfis_by_subject
        A dictionary mapping academic subjects to lists of SFI UUIDs in that subject.

    Returns
    -------
    list[CandidateEdge]
        A list of CandidateEdge objects representing inferred progression edges based
        on scope sequencing.
    """

    output: list[CandidateEdge] = []

    for subj, uuids in sfis_by_subject.items():
        # Group by level ordinal and thread key to avoid dense edges.
        buckets: dict[tuple[int, str], list[UUID]] = {}

        for uid in uuids:
            f = features_by_uuid[uid]
            lvl = f.get("level_ordinal_low")

            if not isinstance(lvl, int):
                continue

            thread = _thread_key_for_scope_sequence(f)

            if not thread:
                continue

            buckets.setdefault((lvl, thread), []).append(uid)

        for (lvl, thread), tids in buckets.items():
            # Require explicit ordering signals (otherwise we'd be sequencing by
            # fallback keys).

            def _has_order_signal(f: dict[str, Any]) -> bool:
                """Check if the features indicate explicit ordering.

                Parameters
                ----------
                f
                    The features dictionary for an SFI.

                Returns
                -------
                bool
                    True if the SFI has explicit ordering signals; False otherwise.
                """

                canon_order_path = f.get("canon_order_path")
                has_canon_order = (
                    isinstance(canon_order_path, list)
                    and len(canon_order_path) > 0
                    and all(isinstance(x, int) for x in canon_order_path)
                )
                return f.get("order_index_within_parent") is not None or has_canon_order

            tids_with_signal = [
                u for u in tids if _has_order_signal(features_by_uuid[u])
            ]

            if len(tids_with_signal) < 2:
                continue

            ordered = sorted(
                tids_with_signal,
                key=lambda u: _within_level_sort_key(features_by_uuid[u]),
            )

            for a, b in zip(ordered, ordered[1:]):
                # Base confidence only when we have ordering evidence.
                conf = 0.85

                # Strongest signal: both endpoints have explicit
                # order_index_within_parent.
                if (
                    features_by_uuid[a].get("order_index_within_parent") is not None
                    and features_by_uuid[b].get("order_index_within_parent") is not None
                ):
                    conf = 0.90

                output.append(
                    CandidateEdge(
                        confidence=conf,
                        evidence={
                            "level_ordinal": lvl,
                            "thread": thread,
                            "local_subject_key": subj,
                            "sequence_type": "canon_order",
                        },
                        heuristic_confidence=conf,
                        inference_source="inferred",
                        inference_type="scope_sequence",
                        metadata={},
                        rel_type="buildsTowards",
                        source_sfi_uuid=a,
                        target_sfi_uuid=b,
                    )
                )

    return output


def _match_by_code_key(
    *,
    base_args: dict[str, Any],
    features_by_uuid: dict[UUID, dict[str, Any]],
    srcs: list[UUID],
    tgts: list[UUID],
) -> list[CandidateEdge]:
    """Handle 1:1 matching logic for 'code' threads.

    Parameters
    ----------
    base_args
        A dictionary of base arguments to include in each CandidateEdge, such as
        inference_type, thread_type, thread_key, and subject_key.
    features_by_uuid
        A dictionary mapping SFI UUIDs to their extracted features, used for keying and
        evidence.
    srcs
        A list of source SFI UUIDs, sorted by the appropriate within-thread ordering
        key.
    tgts
        A list of target SFI UUIDs, sorted by the appropriate within-thread ordering
        key.

    Returns
    -------
    list[CandidateEdge]
        A list of CandidateEdge objects representing inferred progression edges based
        on 1:1 matching of code keys within the thread.
    """

    edges = []
    src_by_code: dict[str, list[UUID]] = {}
    tgt_by_code: dict[str, list[UUID]] = {}

    def _group_by_code(
        *, uuids: list[UUID], target_dict: dict[str, list[UUID]]
    ) -> None:
        """Helper to group UUIDs by their code key without grade.

        Parameters
        ----------
        target_dict
            The dictionary in which to store the grouping, mapping code keys to lists of
            UUIDs.
        uuids
            A list of SFI UUIDs to group by code key.
        """

        for u in uuids:
            # Assumes normalize_ws is available in scope.
            k = normalize_ws(
                str(features_by_uuid[u].get("code_key_without_grade") or "")
            )
            if k:
                target_dict.setdefault(k, []).append(u)

    _group_by_code(target_dict=src_by_code, uuids=srcs)
    _group_by_code(target_dict=tgt_by_code, uuids=tgts)

    # Intersect keys.
    common_keys = set(src_by_code.keys()) & set(tgt_by_code.keys())

    for code_key in sorted(common_keys):
        # Sort within the specific code bucket.
        src_list = sorted(
            src_by_code[code_key],
            key=lambda u: _within_level_sort_key(features_by_uuid[u]),
        )
        tgt_list = sorted(
            tgt_by_code[code_key],
            key=lambda u: _within_level_sort_key(features_by_uuid[u]),
        )

        # 1:1 stable pairing.
        for src_uid, tgt_uid in zip(src_list, tgt_list):
            edges.append(
                _create_edge(
                    conf=0.95,
                    extra_evidence={
                        "match_policy": "code_key_1_to_1",
                        "code_key": code_key,
                    },
                    src_uid=src_uid,
                    tgt_uid=tgt_uid,
                    **base_args,
                )
            )

    return edges


def _match_positionally(
    *, base_args: dict[str, Any], srcs: list[UUID], tgts: list[UUID], thread_type: str
) -> list[CandidateEdge]:
    """Handle positional pairing for non-code threads.

    Parameters
    ----------
    base_args
        A dictionary of base arguments to include in each CandidateEdge, such as
        inference_type, thread_type, thread_key, and subject_key.
    srcs
        A list of source SFI UUIDs, sorted by the appropriate within-thread ordering
        key.
    tgts
        A list of target SFI UUIDs, sorted by the appropriate within-thread ordering
        key.
    thread_type
        The type of thread (e.g., "grade_order" or "stage_order") being processed, used
        to determine confidence levels.

    Returns
    -------
    list[CandidateEdge]
        A list of CandidateEdge objects representing inferred progression edges based
        on positional pairing of sources and targets within the thread.
    """

    edges = []
    conf = 0.90 if thread_type in {"code_stem"} else 0.85

    for src_uid, tgt_uid in zip(srcs, tgts):
        edges.append(
            _create_edge(
                conf=conf,
                extra_evidence={"match_policy": "positional_zip"},
                src_uid=src_uid,
                tgt_uid=tgt_uid,
                **base_args,
            )
        )

    return edges


def _node_label_for_path(node: dict[str, Any]) -> str:
    """Pick a display label for keying paths.

    Parameters
    ----------
    node
        The node dictionary from which to extract the label.

    Returns
    -------
    str
        The extracted label text for the node, or an empty string if none found.
    """

    # Canonical nodes store text units under title/body; fall back to normalized_text.
    for k in ("normalized_text", "title", "body"):
        v = node.get(k)
        txt = v.get("text") or "" if isinstance(v, dict) else str(v or "")
        txt = normalize_ws(txt)

        if txt:
            return txt

    return ""


def _parse_level_ordinal(s: str) -> Optional[int]:
    """Best-effort parse of grade/stage ordinals from human tags.

    Parameters
    ----------
    s
        The input string from which to parse the level ordinal, typically a grade or
        stage tag.

    Returns
    -------
    Optional[int]
        The parsed level ordinal as an integer if successful, or None if parsing fails.
    """

    s0 = normalize_ws(str(s)).lower()
    m = re.search(r"(\d+)", s0)

    if m:
        try:
            return int(m.group(1))
        except Exception:  # pylint: disable=broad-except
            return None

    # Roman numerals (I, II, III, IV, V, VI, etc).
    roman = {
        "i": 1,
        "ii": 2,
        "iii": 3,
        "iv": 4,
        "v": 5,
        "vi": 6,
        "vii": 7,
        "viii": 8,
        "ix": 9,
        "x": 10,
        "xi": 11,
        "xii": 12,
    }
    for k, v in roman.items():
        if re.search(rf"\b{k}\b", s0):
            return v

    return None


def _process_thread_levels(
    *,
    features_by_uuid: dict[UUID, dict[str, Any]],
    inference_type: str,
    subject: str,
    thread_key: str,
    thread_type: str,
    tids: list[UUID],
) -> list[CandidateEdge]:
    """Group SFIs by level and generate edges between adjacent levels.

    Parameters
    ----------
    features_by_uuid
        A dictionary mapping SFI UUIDs to their extracted features.
    inference_type
        The type of inference being performed (e.g., "grade_order" or "stage_order
        thread_type").
    subject
        The academic subject for which the thread is being processed, used for evidence
        in the generated edges.
    thread_key
        The thread key (e.g., "Math|Algebra") for which to process levels, used for
        evidence in the generated edges.
    thread_type
        The type of thread (e.g., "grade_order" or "stage_order") being processed, used
        for evidence in the generated edges.
    tids
        A list of SFI UUIDs that belong to the thread being processed.

    Returns
    -------
    list[CandidateEdge]
        A list of CandidateEdge objects representing inferred progression edges between
        adjacent levels within the thread.
    """

    by_level: dict[int, list[UUID]] = {}
    level_field = (
        "stage_ordinal_low" if inference_type == "stage_order" else "level_ordinal_low"
    )

    # Group by level.
    for uid in tids:
        f = features_by_uuid[uid]
        lvl = f.get(level_field)

        if isinstance(lvl, int):
            by_level.setdefault(lvl, []).append(uid)

    if len(by_level) < 2:
        return []

    edges = []

    # Iterate adjacent levels.
    for lvl in sorted(by_level.keys()):
        nxt = lvl + 1

        if nxt not in by_level:
            continue

        # Sort sources and targets.
        srcs = sorted(
            by_level[lvl],
            key=lambda u: _within_level_sort_key(features_by_uuid[u]),
        )
        tgts = sorted(
            by_level[nxt],
            key=lambda u: _within_level_sort_key(features_by_uuid[u]),
        )

        # Common arguments for edge creation.
        base_args = {
            "inference_type": inference_type,
            "thread_type": thread_type,
            "thread_key": thread_key,
            "subject_key": subject,
            "level_field": level_field,
        }

        # Dispatch to matching strategy.
        if thread_type == "code":
            edges.extend(
                _match_by_code_key(
                    base_args=base_args,
                    features_by_uuid=features_by_uuid,
                    srcs=srcs,
                    tgts=tgts,
                )
            )
        else:
            edges.extend(
                _match_positionally(
                    base_args=base_args, srcs=srcs, tgts=tgts, thread_type=thread_type
                )
            )

    return edges


def _resolve_code_features(
    *, grade_low: int | None, pc: dict[str, Any], sfi: StandardsFrameworkItem
) -> dict[str, Any] | None:
    """Extract code-related features from the progression context if available,
    otherwise parse from the SFI's statement_code and grade information.

    Parameters
    ----------
    grade_low
        The low ordinal of the grade level, used for parsing code features when not
        explicitly provided in the progression context.
    pc
        The progression context dictionary extracted from the SFI metadata, which may
        contain explicit code-related features.
    sfi
        The StandardsFrameworkItem for which to resolve code features, used as a source
        for the statement_code when parsing is needed.

    Returns
    -------
    dict[str, Any] | None
        A dictionary containing code-related features such as "code", "code_segments",
        "code_tuple", "code_stem", "code_ordinal", and "code_stem_without_grade" if
        they can be resolved from the progression context or parsed from the SFI;
        otherwise, None if no code features can be determined.
    """

    if pc:
        # Check specific keys relevant to code features.
        keys = (
            "code",
            "code_segments",
            "code_tuple",
            "code_stem",
            "code_ordinal",
            "code_stem_without_grade",
        )

        # Only construct if at least one key is present to avoid empty dict vs. None
        # ambiguity.
        features = {k: pc[k] for k in keys if k in pc}

        if features:
            return features

    code = pc.get("code") or sfi.statement_code or ""
    return _parse_code_features(code=str(code), grade_ordinal_low=grade_low)


def _resolve_grade_info(
    *, pc: dict[str, Any], sfi: StandardsFrameworkItem
) -> dict[str, Any]:
    """Determine grade key and ordinals from progression context or SFI tags.

    Parameters
    ----------
    pc
        The progression context dictionary extracted from the SFI metadata, which may
        contain explicit grade key and ordinal information.
    sfi
        The StandardsFrameworkItem for which to resolve grade information.

    Returns
    -------
    dict[str, Any]
        A dictionary containing the resolved grade key, low ordinal, and high ordinal,
        determined from the progression context if available, or parsed from the SFI's
        grade_level tags as a fallback.
    """

    key = pc.get("grade_key")
    low = pc.get("grade_ordinal_low")
    high = pc.get("grade_ordinal_high")

    # If explicit progression context exists, prefer it.
    if low is not None:
        return {"key": key, "low": low, "high": high}

    # Fallback: Parse from grade_level tags.
    if sfi.grade_level:
        parsed_ints = [
            p for x in sfi.grade_level if (p := _parse_level_ordinal(x)) is not None
        ]

        if parsed_ints:
            low = min(parsed_ints)
            high = max(parsed_ints)

            if not key:
                cleaned = [
                    normalize_ws(t).lower() for t in sfi.grade_level if normalize_ws(t)
                ]
                key = "|".join(sorted(set(cleaned)))

    return {"key": key, "low": low, "high": high}


def _resolve_ordering(
    *, ctx: ExportContext, node_id: Any, pc: dict[str, Any]
) -> tuple[list, int | None]:
    """Resolve canonical ordering information for the SFI, preferring explicit
    progression context and falling back to graph-based inference using the
    ExportContext's indexes.

    Parameters
    ----------
    ctx
        The ExportContext containing indexes for canonical pointers and graph structure.
    node_id
        The node identifier for the SFI, used to look up ordering information in the
        graph if not explicitly provided in the progression context.
    pc
        The progression context dictionary extracted from the SFI metadata, which may
        contain explicit "canon_order_path" and "order_index_within_parent" values.

    Returns
    -------
    tuple[list, int | None]
        A tuple containing the canonical order path (a list of ancestor node
        identifiers) and the order index within the parent, resolved from the
        progression context if available or inferred from the graph structure using the
        ExportContext's indexes.
    """

    canon_order_path = pc.get("canon_order_path") or []
    order_index = pc.get("order_index_within_parent")

    if order_index is None and node_id:
        pid = ctx.parent_by_child.get(str(node_id))
        if pid:
            order_index = ctx.edge_order_index.get((pid, str(node_id)))

    return canon_order_path, order_index


def _resolve_subject_key(
    *, ctx: ExportContext, node_id: Any, pc: dict[str, Any], sfi: StandardsFrameworkItem
) -> str | None:
    """Resolve a local subject key for the SFI, preferring explicit progression
    context, then falling back to a computed key based on the node's position in the
    framework graph, and finally normalizing the SFI's academic_subject.

    Parameters
    ----------
    ctx
        The ExportContext containing indexes for canonical pointers and graph structure.
    node_id
        The node identifier for the SFI, used to compute the subject key if not
        explicitly provided in the progression context.
    pc
        The progression context dictionary extracted from the SFI metadata, which may
        contain an explicit "local_subject_key".
    sfi
        The StandardsFrameworkItem for which to resolve the subject key, used as a
        final fallback if no explicit key is provided and computation fails.

    Returns
    -------
    str | None
        The resolved local subject key, either from the progression context, computed
        from the node's position in the graph, or normalized from the SFI's academic
        subject, or None if it cannot be resolved.
    """

    if key := pc.get("local_subject_key"):
        return key

    if node_id:
        if computed := _compute_local_subject_key(ctx=ctx, node_id=str(node_id)):
            return computed

    return normalize_ws(str(sfi.academic_subject or ""))


def _resolve_topic_path(
    *, ctx: ExportContext, node_id: Any, pc: dict[str, Any]
) -> str | None:
    """Resolve a topic path key for the SFI, preferring explicit progression context
    and falling back to a computed path based on the node's position in the framework
    graph.

    Parameters
    ----------
    ctx
        The ExportContext containing indexes for canonical pointers and graph structure.
    node_id
        The node identifier for the SFI, used to compute the topic path if not
        explicitly provided in the progression context.
    pc
        The progression context dictionary extracted from the SFI metadata, which may
        contain an explicit "topic_path_key".
    Returns
    -------
    str | None
        The resolved topic path key, either from the progression context or computed
        from the node's position in the graph, or None if it cannot be resolved.
    """

    if key := pc.get("topic_path_key"):
        return key

    if node_id:
        return _compute_topic_path_key(ctx=ctx, node_id=str(node_id))

    return None


def _slugify(s: str) -> str:
    """Normalize a string to a slug suitable for keys.

    Parameters
    ----------
    s
        The input string to normalize into a slug.

    Returns
    -------
    str
        A normalized slug string derived from the input, with non-alphanumeric
        characters replaced by underscores and multiple underscores collapsed.
    """

    s0 = normalize_ws(s).lower()
    s0 = re.sub(r"[^a-z0-9]+", "_", s0)
    s0 = re.sub(r"_+", "_", s0).strip("_")
    return s0


def _stats_code_pattern(
    features_by_uuid: dict[UUID, dict[str, Any]],
    sfis_by_subject: dict[str, list[UUID]],
) -> dict[str, Any]:
    """Calculate statistics about the presence of code features relevant to code
    pattern progression inference.

    Parameters
    ----------
    features_by_uuid
        A dictionary mapping SFI UUIDs to their extracted features, used to check for
        the presence of code stems and code tuples.
    sfis_by_subject
        A dictionary mapping academic subjects to lists of SFI UUIDs in that subject,
        used to group SFIs by subject for code pattern analysis.

    Returns
    -------
    dict[str, Any]
        A dictionary containing statistics about the number of SFIs with code stems and
        code tuples, which are relevant for code pattern progression inference.
    """

    with_stem = 0
    with_tuple = 0

    for uuids in sfis_by_subject.values():
        for uid in uuids:
            f = features_by_uuid.get(uid) or {}
            cf = f.get("code_features") or {}

            raw_stem = cf.get("code_stem_without_grade") or cf.get("code_stem") or ""
            stem = normalize_ws(str(raw_stem))

            if stem:
                with_stem += 1
            if cf.get("code_tuple") is not None:
                with_tuple += 1

    return {"sfis_with_code_stem": with_stem, "sfis_with_code_tuple": with_tuple}


def _stats_grade_order(
    features_by_uuid: dict[UUID, dict[str, Any]],
    sfis_by_subject: dict[str, list[UUID]],
    level_field: str = "level_ordinal_low",
) -> dict[str, Any]:
    """Calculate statistics about the presence of grade/stage ordinals and threading
    keys for grade order inference, as well as the potential adjacency of levels within
    threads.

    Parameters
    ----------
    features_by_uuid
        A dictionary mapping SFI UUIDs to their extracted features, used to check for
        the presence of level ordinals and threading keys.
    sfis_by_subject
        A dictionary mapping academic subjects to lists of SFI UUIDs in that subject,
        used to group SFIs by subject for thread aggregation.
    level_field
        The specific feature field to check for level ordinals, either
        "level_ordinal_low" for grade order or "stage_ordinal_low" for stage order.

    Returns
    -------
    dict[str, Any]
        A dictionary containing statistics about the number of SFIs with level
        ordinals, the number of SFIs with threading keys for grade order inference, the
        number of threads identified, the number of threads with 2 or more levels, and
        the number of adjacent level pairs possible within threads.
    """

    with_level = 0
    with_thread = 0
    levels_by_thread: dict[str, set[int]] = {}

    for subj, uuids in sfis_by_subject.items():
        for uid in uuids:
            f = features_by_uuid.get(uid) or {}
            lo = f.get(level_field)
            tk = _thread_key_for_grade_order(f)

            if isinstance(lo, int):
                with_level += 1
            if tk:
                with_thread += 1
            if isinstance(lo, int) and tk:
                key = f"{subj}||{tk}"
                levels_by_thread.setdefault(key, set()).add(lo)

    # Calculate adjacency statistics.
    adjacent_pairs = 0
    threads_2plus = 0

    for s in levels_by_thread.values():
        if len(s) >= 2:
            threads_2plus += 1

        for level in s:
            if level + 1 in s:
                adjacent_pairs += 1

    return {
        "level_field_used": level_field,
        "sfis_with_level_ordinal": with_level,
        "sfis_with_thread_key": with_thread,
        "threads": len(levels_by_thread),
        "threads_with_2plus_levels": threads_2plus,
        "adjacent_level_pairs_possible": adjacent_pairs,
    }


def _stats_scope_sequence(
    features_by_uuid: dict[UUID, dict[str, Any]],
    sfis_by_subject: dict[str, list[UUID]],
) -> dict[str, Any]:
    """Calculate statistics about the presence of level ordinals and threading keys for
    scope sequence inference, as well as the potential adjacency of levels within
    threads.

    Parameters
    ----------
    features_by_uuid
        A dictionary mapping SFI UUIDs to their extracted features, used to check for
        the presence of level ordinals and threading keys.
    sfis_by_subject
        A dictionary mapping academic subjects to lists of SFI UUIDs in that subject,
        used to group SFIs by subject for thread aggregation.

    Returns
    -------
    dict[str, Any]
        A dictionary containing statistics about the number of SFIs with level
        ordinals, the number of SFIs with threading keys for scope sequence inference,
        the number of threads identified, the number of threads with 2 or more levels,
        and the number of adjacent level pairs possible within threads.
    """

    buckets: dict[str, int] = {}
    with_level = 0
    with_thread = 0

    for subj, uuids in sfis_by_subject.items():
        for uid in uuids:
            f = features_by_uuid.get(uid) or {}
            lo = f.get("level_ordinal_low")
            scope_tk = _thread_key_for_scope_sequence(f)

            is_valid_lo = isinstance(lo, int)

            if is_valid_lo:
                with_level += 1
            if scope_tk:
                with_thread += 1

            if is_valid_lo and scope_tk:
                key = f"{subj}||{lo}||{scope_tk}"
                buckets[key] = buckets.get(key, 0) + 1

    return {
        "sfis_with_level_ordinal": with_level,
        "sfis_with_thread_key": with_thread,
        "buckets": len(buckets),
        "buckets_with_2plus": sum(1 for n in buckets.values() if n >= 2),
    }


def _thread_key_for_grade_order(f: dict[str, Any]) -> Optional[tuple[str, str]]:
    """Prefer code-key threading; fall back to topic path.

    Parameters
    ----------
    f
        The features dictionary for an SFI, from which to extract threading keys.

    Returns
    -------
    Optional[tuple[str, str]]
        A tuple of (thread_type, thread_key) for grade order threading, or None if no
        suitable keys are found.
    """

    code_key = normalize_ws(str(f.get("code_key_without_grade") or ""))

    if code_key:
        return "code", code_key

    cf = f.get("code_features") or {}
    stem = normalize_ws(
        str(cf.get("code_stem_without_grade") or cf.get("code_stem") or "")
    )

    if stem:
        return "code_stem", stem

    tp = normalize_ws(str(f.get("topic_path_key") or ""))

    if tp:
        return "topic", tp

    return None


def _thread_key_for_scope_sequence(f: dict[str, Any]) -> str:
    """Thread key for within-level sequencing: prefer code stem, else topic path.

    Parameters
    ----------
    f
        The features dictionary for an SFI, from which to extract threading keys for
        scope sequencing.

    Returns
    -------
    str
        A thread key for scope sequencing, preferring code stem if available, else
        topic path, or an empty string if neither is available.
    """

    cf = f.get("code_features") or {}
    stem = normalize_ws(
        str(cf.get("code_stem_without_grade") or cf.get("code_stem") or "")
    )

    if stem:
        return f"code_stem:{stem}"

    tp = normalize_ws(str(f.get("topic_path_key") or ""))

    if tp:
        return f"topic:{tp}"

    return ""


def _with_endpoint_pointers(
    *, edge: CandidateEdge, features_by_uuid: dict[UUID, dict[str, Any]]
) -> CandidateEdge:
    """Attach canonical pointers for source and target SFIs to the edge's evidence.

    Parameters
    ----------
    edge
        The CandidateEdge for which to attach endpoint pointers.
    features_by_uuid
        A dictionary mapping SFI UUIDs to their extracted features, used to retrieve
        canonical pointers for the source and target SFIs.

    Returns
    -------
    CandidateEdge
        A new CandidateEdge with canonical pointers for the source and target SFIs
        added to the evidence under the "endpoint_pointers" key.
    """

    sf = features_by_uuid.get(edge.source_sfi_uuid) or {}
    tf = features_by_uuid.get(edge.target_sfi_uuid) or {}

    evidence = dict(edge.evidence or {})
    evidence.setdefault("endpoint_pointers", {})

    evidence["endpoint_pointers"]["source"] = {
        "canonical_node_id": sf.get("canonical_node_id"),
        "canonical_node_role": sf.get("canonical_node_role"),
        "page_indices": sf.get("page_indices") or [],
        "bbox": sf.get("bbox"),
        "source_segment_ids": sf.get("source_segment_ids") or [],
        "source_decision_ids": sf.get("source_decision_ids") or [],
    }
    evidence["endpoint_pointers"]["target"] = {
        "canonical_node_id": tf.get("canonical_node_id"),
        "canonical_node_role": tf.get("canonical_node_role"),
        "page_indices": tf.get("page_indices") or [],
        "bbox": tf.get("bbox"),
        "source_segment_ids": tf.get("source_segment_ids") or [],
        "source_decision_ids": tf.get("source_decision_ids") or [],
    }

    return replace(edge, evidence=evidence)


def _within_level_sort_key(f: dict[str, Any]) -> tuple[Any, ...]:
    """Stable within-level sort key used for threading.

    Parameters
    ----------
    f
        The features dictionary for an SFI, from which to extract components for the
        sort key.

    Returns
    -------
    tuple[Any, ...]
        A tuple representing the sort key for within-level ordering, preferring
        canonical order path, then order index within parent, then statement code, then
        finally SFI UUID for tie-breaking.
    """

    # Prefer canonical order, then statement code, then UUID.
    canon_order_path = f.get("canon_order_path") or []

    # Inlined check: must be a list and contain only integers.
    is_valid_path = isinstance(canon_order_path, list) and all(
        isinstance(x, int) for x in canon_order_path
    )

    canon_order_key = (
        tuple(int(x) for x in canon_order_path) if is_valid_path else tuple()
    )

    code = normalize_ws(str(f.get("statement_code") or ""))

    return (
        canon_order_key,
        (
            f.get("order_index_within_parent")
            if f.get("order_index_within_parent") is not None
            else 10**9
        ),
        code,
        str(f.get("sfi_uuid")),
    )


def export_learning_progressions(
    *,
    academic_standards: AcademicStandardsExport,
    config: CreateKGConfig,
    ctx: ExportContext,
    kg_dirs: KGDirs,
) -> LearningProgressionsExport:
    """Export Learning Progressions KG artifacts.

    Parameters
    ----------
    academic_standards
        Exported Academic Standards artifacts. Endpoints MUST be emitted SFIs.
    config
        CreateKGConfig (progression settings + namespace UUID).
    ctx
        ExportContext (doc_key, framework metadata, indexes).
    kg_dirs
        Output directories.

    Returns
    -------
    LearningProgressionsExport
        Emitted buildsTowards + relatesTo relationships and a report dict.

    Raises
    ------
    ValueError
        If an unexpected relationship type is encountered during processing.
    """

    sfis = list(academic_standards.items)
    sfi_by_uuid: dict[UUID, StandardsFrameworkItem] = {
        s.case_identifier_uuid: s for s in sfis
    }

    # Gate LLM judging on both the boolean flag and progression_sources membership (the
    # config validator enforces consistency, but this keeps the exporter robust if
    # config objects are constructed programmatically).
    llm_enabled = bool(config.progression_llm_enabled) and (
        "llm" in list(getattr(config, "progression_sources", []))
    )

    report: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "doc_key": ctx.doc_key,
        "pdf_name": ctx.pdf_name,
        "total_sfis": len(sfis),
        "modules_enabled": list(config.progression_inference_modules),
        "candidates": {
            "by_module": {},
            "module_stats": {},
            "pre_pool_total": 0,
            "post_pre_pool_dedupe_total": 0,
            "post_pool_total": 0,
            "post_dedupe_total": 0,
            "post_filter_total": 0,
        },
        "drops": {},
        "truncations": {"per_node": {}, "total_nodes_truncated": 0},
        "cycles_removed": [],
        "llm": {"enabled": bool(llm_enabled), "failures": 0},
        "granularity": {"configured": config.progression_granularity, "chosen": None},
    }

    # Precompute features.
    features_by_uuid: dict[UUID, dict[str, Any]] = {}
    for sfi in sfis:
        features_by_uuid[sfi.case_identifier_uuid] = _extract_progression_features(
            sfi=sfi, ctx=ctx
        )

    # Candidate generation (deterministic modules).
    candidates = generate_candidate_edges(
        config=config,
        features_by_uuid=features_by_uuid,
        report=report,
        standards_export=academic_standards,
    )
    report["candidates"]["pre_pool_total"] = len(candidates)

    # Pre-pool dedupe so duplicates (e.g., from multiple modules) don't consume top-k
    # slots.
    candidates = _dedupe_candidates_pre_pool(candidates=candidates)
    report["candidates"]["post_pre_pool_dedupe_total"] = len(candidates)

    # Pool bounding per source.
    candidates = _bound_candidate_pool(
        candidates=candidates,
        per_source=config.progression_candidate_pool_size_per_node,
    )
    report["candidates"]["post_pool_total"] = len(candidates)

    # Optional LLM judging.
    if llm_enabled is True:
        candidates = judge_candidates_with_llm(
            candidates=candidates,
            config=config,
            features_by_uuid=features_by_uuid,
            report=report,
            sfi_by_uuid=sfi_by_uuid,
        )

    # Normalize + dedupe + stable ordering.
    candidates = normalize_and_dedupe(
        candidates=candidates,
        config=config,
        features_by_uuid=features_by_uuid,
        report=report,
        sfi_by_uuid=sfi_by_uuid,
    )
    report["candidates"]["post_dedupe_total"] = len(candidates)

    # Granularity + thresholds + top-K caps.
    chosen_granularity = _choose_granularity(
        configured=config.progression_granularity,
        features_by_uuid=features_by_uuid,
        sfis=sfis,
    )
    report["granularity"]["chosen"] = chosen_granularity

    candidates = filter_edges(
        candidates=candidates,
        chosen_granularity=chosen_granularity,
        config=config,
        report=report,
        sfi_by_uuid=sfi_by_uuid,
    )
    report["candidates"]["post_filter_total"] = len(candidates)

    # Optional: cycle protection for buildsTowards.
    if config.progression_enforce_dag_builds_towards is True:
        candidates = _enforce_dag_builds_towards(candidates=candidates, report=report)

    # Emit relationships + provenance metadata.
    fw_metadata = ctx.get_framework_metadata()

    builds_towards: list[Relationship] = []
    relates_to: list[Relationship] = []

    for c in candidates:
        rel = _emit_progression_relationship(
            candidate=c,
            config=config,
            doc_key=ctx.doc_key,
            features_by_uuid=features_by_uuid,
            fw_metadata=fw_metadata,
            granularity=chosen_granularity,
        )

        if rel.relationship_type == "buildsTowards":
            builds_towards.append(rel)
        elif rel.relationship_type == "relatesTo":
            relates_to.append(rel)
        else:
            raise ValueError(
                f"Unexpected progression rel type: {rel.relationship_type}"
            )

    # Deterministic ordering for output stability.
    builds_towards = sorted(
        builds_towards,
        key=lambda r: (
            str(r.source_entity_value),
            str(r.target_entity_value),
            str(r.identifier),
        ),
    )

    relates_to = sorted(
        relates_to,
        key=lambda r: (
            str(r.source_entity_value),
            str(r.target_entity_value),
            str(r.identifier),
        ),
    )

    # Write artifacts.
    write_to_json(
        fp=kg_dirs.learning_progressions
        / "learning_progressions_builds_towards_relationships.json",
        json_info=[r.model_dump(mode="json") for r in builds_towards],
    )
    write_to_json(
        fp=kg_dirs.learning_progressions
        / "learning_progressions_relates_to_relationships.json",
        json_info=[r.model_dump(mode="json") for r in relates_to],
    )

    graph_bundle = _build_learning_progressions_graph_bundle(
        ctx=ctx,
        export_dialect=str(config.export_dialect),
        relationships=(builds_towards + relates_to),
    )
    write_to_json(
        fp=kg_dirs.learning_progressions / "learning_progressions_kg.json",
        json_info=graph_bundle,
    )

    write_to_json(
        fp=kg_dirs.learning_progressions / "learning_progressions_report.json",
        json_info=report,
    )

    return LearningProgressionsExport(
        builds_towards_relationships=builds_towards,
        graph_bundle=graph_bundle,
        relates_to_relationships=relates_to,
        report=report,
    )


def filter_edges(
    *,
    candidates: list[CandidateEdge],
    chosen_granularity: str,
    config: CreateKGConfig,
    report: dict[str, Any],
    sfi_by_uuid: dict[UUID, StandardsFrameworkItem],
) -> list[CandidateEdge]:
    """Apply granularity, confidence threshold, and max edges per node caps.

    Parameters
    ----------
    candidates
        The list of CandidateEdge objects to filter.
    chosen_granularity
        The granularity level chosen for filtering ("fine", "coarse", or "all").
    config
        The CreateKGConfig containing configuration options, including confidence
        thresholds and max edges per node.
    report
        The report dictionary to update with drop and truncation statistics during
        filtering.
    sfi_by_uuid
        A dictionary mapping SFI UUIDs to their corresponding StandardsFrameworkItem
        objects, used for accessing SFI features needed for filtering decisions.

    Returns
    -------
    list[CandidateEdge]
        The list of CandidateEdge objects that passed the filtering criteria.
    """

    drops = report.setdefault("drops", {})

    def _drop(reason: str) -> None:
        """Helper function to record a dropped edge with a specific reason.

        Parameters
        ----------
        reason
            A string indicating the reason for dropping the edge, used for reporting
            purposes.
        """

        drops[reason] = int(drops.get(reason, 0)) + 1

    # Granularity filter.
    filtered: list[CandidateEdge] = []

    for c in candidates:
        s = sfi_by_uuid[c.source_sfi_uuid]
        t = sfi_by_uuid[c.target_sfi_uuid]

        if chosen_granularity == "fine" and not (
            s.normalized_statement_type == "Standard"
            and t.normalized_statement_type == "Standard"
        ):
            _drop("granularity_non_standard")
            continue

        if chosen_granularity == "coarse" and not (
            s.normalized_statement_type == "Standard Grouping"
            and t.normalized_statement_type == "Standard Grouping"
        ):
            _drop("granularity_non_grouping")
            continue

        filtered.append(c)

    # Confidence threshold.
    filtered2: list[CandidateEdge] = []

    for c in filtered:
        if c.confidence < config.progression_min_confidence:
            _drop("below_min_confidence")
            continue

        filtered2.append(c)

    # Top-k per source.
    grouped: dict[UUID, list[CandidateEdge]] = {}

    for c in filtered2:
        grouped.setdefault(c.source_sfi_uuid, []).append(c)

    output: list[CandidateEdge] = []
    trunc = report.setdefault("truncations", {}).setdefault("per_node", {})

    for src in sorted(grouped.keys(), key=str):
        cs = grouped[src]
        cs_sorted = sorted(
            cs, key=lambda x: (-x.confidence, x.rel_type, str(x.target_sfi_uuid))
        )
        kept = cs_sorted[: config.max_progression_edges_per_node]
        output.extend(kept)

        if len(cs_sorted) > len(kept):
            trunc[str(src)] = len(cs_sorted) - len(kept)

    report["truncations"]["total_nodes_truncated"] = len(trunc)

    return output


def generate_candidate_edges(
    *,
    config: CreateKGConfig,
    features_by_uuid: dict[UUID, dict[str, Any]],
    report: dict[str, Any],
    standards_export: AcademicStandardsExport,
) -> list[CandidateEdge]:
    """Generate candidate progression edges via deterministic inference modules.

    Parameters
    ----------
    config
        The CreateKGConfig containing configuration options, including which inference
        modules are enabled.
    features_by_uuid
        A dictionary mapping SFI UUIDs to their extracted features, used by inference
        modules to generate candidate edges.
    report
        The report dictionary to update with statistics about candidate generation,
        such as module-specific stats and counts of generated edges.
    standards_export
        The AcademicStandardsExport containing the SFIs for which to generate candidate
        edges, used for partitioning and feature access during inference.

    Returns
    -------
    list[CandidateEdge]
        A list of CandidateEdge objects representing inferred progression edges based
        on the enabled deterministic inference modules.
    """

    candidates: list[CandidateEdge] = []

    # Partition by subject for cheap blocking and nicer reports.
    sfis_by_subject: dict[str, list[UUID]] = {}  # NB: actually "local subject" buckets

    for sfi in standards_export.items:
        f = features_by_uuid.get(sfi.case_identifier_uuid) or {}
        subj = normalize_ws(str(f.get("local_subject_key") or "")) or normalize_ws(
            sfi.academic_subject or ""
        )
        sfis_by_subject.setdefault(subj, []).append(sfi.case_identifier_uuid)

    enabled = list(config.progression_inference_modules)
    module_stats = report["candidates"].setdefault("module_stats", {})

    def _add(*, edges: list[CandidateEdge], module_name: str) -> None:
        """Helper to add generated edges for a specific module and record stats.

        Parameters
        ----------
        edges
            The list of CandidateEdge objects generated by the module, which will be
            added to the overall candidates list and counted in the report.
        module_name
            The name of the inference module that generated the edges, used for
            reporting.
        """

        # Always record the module, even if it produced 0 edges.
        stats = _compute_module_stats(
            features_by_uuid=features_by_uuid,
            module_name=module_name,
            sfis_by_subject=sfis_by_subject,
        )
        stats["generated_edges"] = len(edges)
        module_stats[module_name] = stats

        # Attach endpoint pointers into evidence.
        edges2 = [
            _with_endpoint_pointers(edge=e, features_by_uuid=features_by_uuid)
            for e in edges
        ]

        report["candidates"]["by_module"][module_name] = len(edges2)
        candidates.extend(edges2)

    for module in enabled:
        if module in {"grade_order", "stage_order"}:
            _add(
                edges=_infer_grade_order(
                    features_by_uuid=features_by_uuid,
                    inference_type=module,
                    sfis_by_subject=sfis_by_subject,
                ),
                module_name=module,
            )
        elif module == "scope_sequence":
            _add(
                edges=_infer_scope_sequence(
                    features_by_uuid=features_by_uuid, sfis_by_subject=sfis_by_subject
                ),
                module_name="scope_sequence",
            )
        elif module == "code_pattern":
            _add(
                edges=_infer_code_pattern(
                    config=config,
                    features_by_uuid=features_by_uuid,
                    sfis_by_subject=sfis_by_subject,
                ),
                module_name="code_pattern",
            )
        else:
            logger.warning(
                f"Unknown progression inference module '{module}'; skipping."
            )
            _add(edges=[], module_name=str(module))
            report["candidates"]["by_module"][str(module)] = 0

    return candidates


def judge_candidates_with_llm(
    *,
    candidates: list[CandidateEdge],
    config: CreateKGConfig,
    features_by_uuid: dict[UUID, dict[str, Any]],
    report: dict[str, Any],
    sfi_by_uuid: dict[UUID, StandardsFrameworkItem],
) -> list[CandidateEdge]:
    """Optional LLM judging hook.

    Parameters
    ----------
    candidates
        The list of CandidateEdge objects to judge with the LLM.
    config
        The CreateKGConfig containing configuration options, including LLM-related
        settings.
    features_by_uuid
        A dictionary mapping SFI UUIDs to their extracted features, which may be used
        to construct prompts for the LLM.
    report
        The report dictionary to update with statistics about LLM judging, such as
        counts of failures or errors during LLM calls.
    sfi_by_uuid
        A dictionary mapping SFI UUIDs to their corresponding StandardsFrameworkItem
        objects, which may be used to access SFI details needed for LLM judging.

    Returns
    -------
    list[CandidateEdge]
        The list of CandidateEdge objects after LLM judging, which may be filtered or
        modified based on the LLM's assessments.
    """

    # Just return inferred candidates.
    logger.debug(f"{config = }")
    logger.debug(f"{features_by_uuid = }")
    logger.debug(f"{sfi_by_uuid = }")
    logger.warning(
        "progression_llm_enabled=True but no LLM client is wired in this codebase; "
        "skipping LLM judging and exporting heuristic candidates only."
    )
    report["llm"]["failures"] = report["llm"].get("failures", 0) + 1
    return candidates


def normalize_and_dedupe(
    *,
    candidates: list[CandidateEdge],
    config: CreateKGConfig,
    features_by_uuid: dict[UUID, dict[str, Any]],
    report: dict[str, Any],
    sfi_by_uuid: dict[UUID, StandardsFrameworkItem],
) -> list[CandidateEdge]:
    """Apply global constraints, canonicalize relatesTo, and dedupe deterministically.

    Parameters
    ----------
    candidates
        The list of CandidateEdge objects to normalize and deduplicate.
    config
        The CreateKGConfig containing configuration options, including global
        constraints such as whether to allow cross-subject edges and whether to only
        allow adjacent levels.
    features_by_uuid
        A dictionary mapping SFI UUIDs to their extracted features, used for enforcing
        constraints that depend on SFI attributes (e.g., subject keys, level ordinals).
    report
        The report dictionary to update with statistics about dropped edges during
        normalization and deduplication, such as counts of drops for self-loops,
        missing endpoints, cross-subject edges, non-adjacent levels, etc.
    sfi_by_uuid
        A dictionary mapping SFI UUIDs to their corresponding StandardsFrameworkItem
        objects, used for validating the existence of endpoints and accessing features
        needed for constraint enforcement during normalization and deduplication.

    Returns
    -------
    list[CandidateEdge]
        A list of CandidateEdge objects that have been normalized and deduplicated,
        with global constraints enforced and relatesTo edges canonicalized as
        undirected pairs.
    """

    drops = report.setdefault("drops", {})

    def _drop(reason: str) -> None:
        """Helper function to record a dropped edge with a specific reason during
        normalization and deduplication.

        Parameters
        ----------
        reason
            A string indicating the reason for dropping the edge, used for reporting
            purposes (e.g., "self_loop", "missing_endpoint", "cross_subject_blocked
            ", "non_adjacent_levels", etc.).
        """

        drops[reason] = int(drops.get(reason, 0)) + 1

    dedup: dict[tuple[str, str, str], CandidateEdge] = {}

    for c in candidates:
        # Basic validity checks.
        if c.source_sfi_uuid == c.target_sfi_uuid:
            _drop("self_loop")
            continue

        if c.source_sfi_uuid not in sfi_by_uuid or c.target_sfi_uuid not in sfi_by_uuid:
            _drop("missing_endpoint")
            continue

        # Configurable logic checks.
        if reason := _check_subject_constraints(
            candidate=c, config=config, features_by_uuid=features_by_uuid
        ):
            _drop(reason)
            continue

        if reason := _check_level_constraints(
            candidate=c, config=config, features_by_uuid=features_by_uuid
        ):
            _drop(reason)
            continue

        # Edge canonicalization.
        c = _canonicalize_edge(c)

        # Dedupe.
        key = (str(c.source_sfi_uuid), str(c.target_sfi_uuid), c.rel_type)
        prev = dedup.get(key)

        if prev is None or c.confidence > prev.confidence:
            dedup[key] = c

    output = sorted(
        dedup.values(),
        key=lambda x: (
            -x.confidence,
            x.rel_type,
            x.inference_source,
            x.inference_type,
            str(x.source_sfi_uuid),
            str(x.target_sfi_uuid),
        ),
    )

    return output
