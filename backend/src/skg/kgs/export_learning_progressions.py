"""This module contains functionalities related to exporting the Learning Progressions
knowledge graph. It exports relationships between *exported* StandardsFrameworkItems
(SFIs) from an Academic Standards export.

- Relationships:
  - buildsTowards (SFI -> SFI), directional
  - relatesTo (SFI -- SFI), associative (canonicalized to a single directed edge)

The export is *shape-preserving* for the LC Knowledge Graph ontology and is designed to
work for non-US curriculum documents mapped into the LC "academic standards" shape.

Phases (toggleable via CreateKGConfig):

1. Within-grade buildsTowards
2. Cross-grade buildsTowards (adjacent grades, normalized thread matching)
3. Within-grade relatesTo (different threads within same grade and subject)
4. Cross-grade relatesTo (adjacent grades within same subject, excluding buildsTowards
    pairs)
"""

# Standard Library
import re

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from itertools import combinations
from typing import Any, DefaultDict, Optional
from uuid import UUID, uuid5

# Third Party Library
from loguru import logger

# Package Library
from skg.kgs.export_academic_standards import AcademicStandardsExport
from skg.kgs.llm import infer_progression_edges
from skg.kgs.schemas import Relationship, StandardsFrameworkItem
from skg.kgs.utils import ExportContext, KGDirs
from skg.kgs.validators import (
    validate_cross_grade_builds_towards,
    validate_cross_grade_relates_to,
    validate_within_grade_builds_towards,
    validate_within_grade_relates_to,
)
from skg.prompts.learning_progressions import (
    cross_grade_builds_towards,
    cross_grade_relates_to,
    within_grade_builds_towards,
    within_grade_relates_to,
)
from skg.schemas import CreateKGConfig
from skg.utils.general import write_to_json

# Compiled regexes.
GRADE_INT_RE = re.compile(r"\b(\d+)\b")
LEADING_GRADE_PREFIX_RE = re.compile(r"^(?:\d+_)+")  # Strips 1_ or 1_2_ etc.


@dataclass(frozen=True)
class CandidateEdge:
    """Internal candidate edge representation (pre-Relationship emission)."""

    confidence: float  # 0..1
    evidence: dict[str, Any]
    inference_source: str  # "llm"
    inference_type: str
    metadata: dict[str, Any]
    rel_type: str  # "buildsTowards" | "relatesTo"
    source_sfi_uuid: UUID
    target_sfi_uuid: UUID
    llm_confidence: Optional[float] = None


@dataclass
class LearningProgressionsExport:
    """The output of exporting Learning Progressions KG artifacts."""

    builds_towards_relationships: list[Relationship]
    graph_bundle: dict[str, Any]
    relates_to_relationships: list[Relationship]
    report: dict[str, Any]


def _build_learning_progressions_graph_bundle(
    *,
    academic_standards: AcademicStandardsExport,
    ctx: ExportContext,
    export_dialect: str,
    relationships: list[Relationship],
) -> dict[str, Any]:
    """Build a graph bundle for learning progressions.

    Parameters
    ----------
    academic_standards
        The exported Academic Standards KG artifacts, containing the framework and items
        to include as nodes in the graph.
    ctx
        The KG export context, providing information such as the document key for the
        graph bundle metadata.
    export_dialect
        A string indicating the export dialect or format of the graph bundle, to be
        included in the bundle metadata.
    relationships
        A list of Relationship objects representing the buildsTowards and relatesTo
        relationships to include in the graph bundle.

    Returns
    -------
    dict[str, Any]
        A dictionary representing the graph bundle for learning progressions,
        containing metadata such as the document key, export dialect, generation
        timestamp, graph type, and the lists of nodes and relationships to be included
        in the graph. The nodes include the StandardsFramework and
        StandardsFrameworkItem entities from the academic standards export, while the
        relationships include the inferred buildsTowards and relatesTo relationships
        between the StandardsFrameworkItems.
    """

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nodes: list[dict[str, Any]] = []

    # Include framework + SFIs for standalone graph use.
    fw = academic_standards.framework
    nodes.append(
        {
            "id": str(fw.case_identifier_uuid),
            "labels": ["StandardsFramework"],
            "properties": fw.model_dump(mode="json"),
        }
    )

    for sfi in academic_standards.items:
        nodes.append(
            {
                "id": str(sfi.case_identifier_uuid),
                "labels": ["StandardsFrameworkItem"],
                "properties": sfi.model_dump(mode="json"),
            }
        )

    rels: list[dict[str, Any]] = []

    for r in relationships:
        start_id = r.source_entity_value
        end_id = r.target_entity_value
        rel_type = (
            "BUILDS_TOWARDS" if r.relationship_type == "buildsTowards" else "RELATES_TO"
        )
        rels.append(
            {
                "id": str(r.identifier),
                "type": rel_type,
                "start": start_id,
                "end": end_id,
                "properties": r.model_dump(mode="json"),
            }
        )

    return {
        "doc_key": ctx.doc_key,
        "export_dialect": export_dialect,
        "generated_at": generated_at,
        "graph_type": "learning_progressions",
        "nodes": nodes,
        "relationships": rels,
    }


def _build_relationship(
    *,
    config: CreateKGConfig,
    metadata: dict[str, Any],
    rel_type: str,
    source: UUID,
    target: UUID,
) -> Relationship:
    """Helper to build a Relationship object from a CandidateEdge, using config for
    attribution and metadata from the CandidateEdge. The identifier is a UUID5 of the
    source and target UUIDs and the relationship type, within a namespace UUID from the
    config to ensure stability across runs. The relationship is always from source to
    target, and the source and target entities are both "StandardsFrameworkItem" with
    the key "case_identifier_uuid" and the value of the respective UUIDs as strings.

    Parameters
    ----------
    config
        The knowledge graph run configuration, containing attribution and namespace
        information.
    metadata
        A dictionary of metadata to include in the Relationship, typically derived from
        the CandidateEdge.
    rel_type
        The type of relationship to create (e.g., "buildsTowards" or "relatesTo").
    source
        The UUID of the source StandardsFrameworkItem in the relationship.
    target
        The UUID of the target StandardsFrameworkItem in the relationship.

    Returns
    -------
    Relationship
        A Relationship object representing the specified relationship between the
        source and target SFIs, with appropriate attribution, metadata, and a stable
        identifier.
    """

    rid = uuid5(config.namespace_uuid, f"lp:{rel_type}:{source}:{target}")

    return Relationship(
        attribution_statement=config.attribution_statement,
        author=config.author,
        date_created=None,
        date_modified=None,
        description="",
        identifier=rid,
        license=config.license,
        metadata=metadata,
        provider=config.provider,
        relationship_type=rel_type,
        source_entity="StandardsFrameworkItem",
        source_entity_key="case_identifier_uuid",
        source_entity_value=str(source),
        target_entity="StandardsFrameworkItem",
        target_entity_key="case_identifier_uuid",
        target_entity_value=str(target),
    )


def _dedupe_edges(edges: list[CandidateEdge]) -> list[CandidateEdge]:
    """Deduplicate by (rel_type, canonical endpoints). Keep highest confidence.

    Parameters
    ----------
    edges
        A list of CandidateEdge instances that may contain duplicates based on their
        relationship type and canonicalized endpoints.

    Returns
    -------
    list[CandidateEdge]
        A deduplicated list of CandidateEdge instances, where duplicates (edges with
        the same relationship type and canonical endpoints) are resolved by keeping the
        edge with the highest confidence score.
    """

    best: dict[tuple[str, UUID, UUID], CandidateEdge] = {}

    for e in edges:
        s, t = e.source_sfi_uuid, e.target_sfi_uuid

        if e.rel_type == "relatesTo" and str(s) > str(t):
            s, t = t, s

        k = (e.rel_type, s, t)

        # If the endpoints were swapped, create a new edge object; otherwise reuse
        # existing.
        e2 = (
            e
            if (s, t) == (e.source_sfi_uuid, e.target_sfi_uuid)
            else CandidateEdge(
                confidence=e.confidence,
                evidence=e.evidence,
                inference_source=e.inference_source,
                inference_type=e.inference_type,
                llm_confidence=e.llm_confidence,
                metadata=e.metadata,
                rel_type=e.rel_type,
                source_sfi_uuid=s,
                target_sfi_uuid=t,
            )
        )

        if k not in best or e2.confidence > best[k].confidence:
            best[k] = e2

    return list(best.values())


def _format_learning_progressions_dict(
    *,
    buckets: DefaultDict[str, DefaultDict[str, dict[str, Any]]],
    drops: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Sort and structure the raw buckets.

    Parameters
    ----------
    buckets
        The raw buckets of standards grouped by grade and thread, as built by
        group_standards_for_learning_progressions.
    drops
        The dropped items report, containing lists of items that were dropped due to
        various data issues (e.g., missing topic path key, multiple grade tags, etc.).

    Returns
    -------
    dict[str, Any]
        A dictionary containing the sorted and structured standards by grade and
        thread, as well as the drops report, ready for use in the LLM prompt or output
        artifacts.
    """

    by_grade: dict[str, list[dict[str, Any]]] = {}
    by_thread: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    for grade_label, per_thread in buckets.items():
        grade_buckets: list[dict[str, Any]] = []

        for tkey, b in per_thread.items():
            b["items"] = sorted(b["items"], key=_sort_key_for_bucket_sfi)
            grade_buckets.append(b)
            by_thread[tkey][grade_label] = b

        by_grade[grade_label] = sorted(
            grade_buckets,
            key=lambda x: (x.get("topic_path") or "", x["topic_path_key"]),
        )

    return {"by_grade": by_grade, "by_thread": dict(by_thread), "drops": drops}


def _grade_label_and_ordinal(sfi: StandardsFrameworkItem) -> tuple[str, int | None]:
    """Prefer progression_context grade ordinals when present; fall back to grade_level
    tags.

    Parameters
    ----------
    sfi
        The StandardsFrameworkItem to extract grade information from.

    Returns
    -------
    tuple[str, int | None]
        A tuple containing the grade label and its corresponding ordinal (if available).
    """

    metadata = sfi.metadata or {}
    progression_context = metadata.get("progression_context") or {}
    grade_ordinal_low = progression_context.get("grade_ordinal_low")

    if isinstance(grade_ordinal_low, int):
        return f"GRADE {grade_ordinal_low}", grade_ordinal_low

    grade_level = sfi.grade_level or []

    if grade_level:
        label = str(grade_level[0]).strip().upper()
        m = GRADE_INT_RE.search(label)
        return label, int(m.group(1)) if m else None

    return "UNSPECIFIED_GRADE", None


def _infer_cross_grade_builds_towards(
    *, by_grade: dict[str, list[dict[str, Any]]], config: CreateKGConfig
) -> tuple[list[CandidateEdge], list[dict[str, Any]], set[tuple[UUID, UUID]]]:
    """Perform Phase 2 inference: Cross-grade buildsTowards relationships.

    Parameters
    ----------
    by_grade
        Dictionary mapping grade labels to lists of bucket dictionaries.
    config
        The knowledge graph run configuration.

    Returns
    -------
    tuple[list[CandidateEdge], list[dict[str, Any]], set[tuple[UUID, UUID]]]
        A tuple containing:
            1. List of generated candidate edges.
            2. List of provenance dictionaries.
            3. Set of (source_uuid, target_uuid) tuples for use in exclusion logic in
                Phase 4.
    """

    candidates: list[CandidateEdge] = []
    provenance_rows: list[dict[str, Any]] = []
    cross_grade_build_pairs: set[tuple[UUID, UUID]] = set()

    if not config.progressions_cross_grade_builds_towards:
        return candidates, provenance_rows, cross_grade_build_pairs

    thread_grade = {
        (
            normalize_thread_key(b.get("topic_path_key", "")),
            int(b["grade_ordinal"]),
        ): b
        for grade_buckets in by_grade.values()
        for b in grade_buckets
        if isinstance(b.get("grade_ordinal"), int)
    }
    phase2_calls = sum(
        1
        for (nk, o), b in thread_grade.items()
        if (nk, o + 1) in thread_grade
        and len(b.get("items", [])) > 0
        and len(thread_grade[(nk, o + 1)].get("items", [])) > 0
    )

    norm_map: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)

    logger.info(
        f"{phase2_calls} adjacent grade pairs with overlapping threads for "
        f"cross-grade buildsTowards inference."
    )

    for grade_buckets in by_grade.values():
        for b in grade_buckets:
            ord_ = b.get("grade_ordinal")

            if not isinstance(ord_, int):
                continue

            norm_key = str(
                b.get("normalized_topic_path_key")
                or normalize_thread_key(str(b.get("topic_path_key") or ""))
            )
            norm_map[norm_key][ord_] = b

    for norm_key, by_ord in norm_map.items():
        ords = sorted(by_ord.keys())

        for lo, hi in zip(ords, ords[1:]):
            if hi != lo + 1:
                continue  # Adjacent only

            b_lo = by_ord[lo]
            b_hi = by_ord[hi]
            lower_items = b_lo.get("items") or []
            upper_items = b_hi.get("items") or []

            if not lower_items or not upper_items:
                continue

            lo_label = str(b_lo.get("grade_level") or f"GRADE {lo}")
            hi_label = str(b_hi.get("grade_level") or f"GRADE {hi}")

            lower_payload = [
                {
                    "sfi_uuid": it["sfi_uuid"],
                    "statement_code": it.get("statement_code"),
                    "description": it.get("description"),
                    "notes": it.get("notes"),
                    "page_index": it.get("page_index"),
                    "order_index_within_parent": it.get("order_index_within_parent"),
                }
                for it in lower_items
            ]
            upper_payload = [
                {
                    "sfi_uuid": it["sfi_uuid"],
                    "statement_code": it.get("statement_code"),
                    "description": it.get("description"),
                    "notes": it.get("notes"),
                    "page_index": it.get("page_index"),
                    "order_index_within_parent": it.get("order_index_within_parent"),
                }
                for it in upper_items
            ]

            prompt = cross_grade_builds_towards(
                lower_items=lower_payload,
                lower_grade_label=lo_label,
                normalized_thread_key=norm_key,
                thread_path=str(b_hi.get("topic_path") or b_hi.get("topic_path_key")),
                upper_grade_label=hi_label,
                upper_items=upper_payload,
            )

            allowed_lo = {str(it["sfi_uuid"]) for it in lower_payload}
            allowed_hi = {str(it["sfi_uuid"]) for it in upper_payload}

            response = infer_progression_edges(
                always_double_check_first_attempt=False,
                instructions=prompt.system_message,
                model=config.model,
                user_message=prompt.user_message,
                validator=partial(
                    validate_cross_grade_builds_towards,
                    allowed_lo=allowed_lo,
                    allowed_hi=allowed_hi,
                ),
            )

            for e in response.edges:
                ce = CandidateEdge(
                    confidence=float(e.confidence),
                    evidence={"rationale": e.rationale},
                    inference_source="llm",
                    llm_confidence=float(e.confidence),
                    inference_type="cross_grade_builds_towards",
                    metadata={
                        "lower_grade": lo_label,
                        "upper_grade": hi_label,
                        "normalized_thread_key": norm_key,
                        "topic_path_key_upper": b_hi.get("topic_path_key"),
                        "topic_path": b_hi.get("topic_path"),
                        "subject_label": b_hi.get("subject_label"),
                    },
                    rel_type="buildsTowards",
                    source_sfi_uuid=_uuid(e.source_sfi_uuid),
                    target_sfi_uuid=_uuid(e.target_sfi_uuid),
                )
                candidates.append(ce)
                cross_grade_build_pairs.add((ce.source_sfi_uuid, ce.target_sfi_uuid))
                provenance_rows.append(
                    {
                        "confidence": ce.confidence,
                        "lower_grade": lo_label,
                        "normalized_thread_key": norm_key,
                        "rationale": e.rationale,
                        "rel_type": "buildsTowards",
                        "source": str(ce.source_sfi_uuid),
                        "target": str(ce.target_sfi_uuid),
                        "upper_grade": hi_label,
                    }
                )

    return candidates, provenance_rows, cross_grade_build_pairs


def _infer_cross_grade_relates_to(
    *,
    by_grade: dict[str, list[dict[str, Any]]],
    config: CreateKGConfig,
    forbidden_builds_pairs: set[tuple[UUID, UUID]],
) -> tuple[list[CandidateEdge], list[dict[str, Any]]]:
    """Perform Phase 4 inference: Cross-grade relatesTo relationships.

    Parameters
    ----------
    by_grade
        Dictionary mapping grade labels to lists of bucket dictionaries.
    config
        The knowledge graph run configuration.
    forbidden_builds_pairs
        A set of (source, target) UUID tuples representing existing buildsTowards
        relationships which should be excluded from relatesTo inference.

    Returns
    -------
    tuple[list[CandidateEdge], list[dict[str, Any]]]
        A tuple containing the list of generated candidate edges and the list of
        provenance dictionaries.
    """

    candidates: list[CandidateEdge] = []
    provenance_rows: list[dict[str, Any]] = []

    if not config.progressions_cross_grade_relates_to:
        return candidates, provenance_rows

    max_items = int(config.progressions_within_grade_relates_to_max_items_per_subject)
    max_edges_per_sfi = int(config.progressions_relates_to_max_edges_per_sfi)

    subject_grade_has_items = {
        (
            str(b.get("subject_label") or "UNSPECIFIED_SUBJECT"),
            int(b["grade_ordinal"]),
        )
        for grade_buckets in by_grade.values()
        for b in grade_buckets
        if isinstance(b.get("grade_ordinal"), int) and len(b.get("items", [])) > 0
    }
    phase4_calls = (
        sum(
            1
            for subject in {s for (s, _) in subject_grade_has_items}
            for o in sorted({o for (s, o) in subject_grade_has_items if s == subject})
            if (subject, o + 1) in subject_grade_has_items
        )
        if max_items > 0
        else 0
    )
    subject_grade_samples = _prepare_subject_grade_samples(
        by_grade=by_grade, max_items=max_items
    )

    logger.info(
        f"{phase4_calls} adjacent grade pairs with 2+ items for cross-grade "
        f"relatesTo inference."
    )

    for subject_label, by_ord in subject_grade_samples.items():
        ords = sorted(by_ord.keys())

        for lo, hi in zip(ords, ords[1:]):
            if hi != lo + 1:
                continue

            lower = by_ord[lo]
            upper = by_ord[hi]
            lower_items = lower["items"]
            upper_items = upper["items"]
            assert (
                lower_items and upper_items
            ), f"Logic error in subject_grade_samples construction for {subject_label} {lo} vs {hi}"

            allowed_lo = {str(it["sfi_uuid"]) for it in lower_items}
            allowed_hi = {str(it["sfi_uuid"]) for it in upper_items}
            forbidden_pairs_set: set[tuple[str, str]] = set()

            for s, t in forbidden_builds_pairs:
                ss, tt = str(s), str(t)

                if ss in allowed_lo and tt in allowed_hi:
                    forbidden_pairs_set.add((ss, tt))
                    forbidden_pairs_set.add((tt, ss))

            forbidden_pairs = [
                {"a_sfi_uuid": a, "b_sfi_uuid": b}
                for a, b in sorted(forbidden_pairs_set)
            ]

            prompt = cross_grade_relates_to(
                forbidden_pairs=forbidden_pairs,
                lower_grade_label=str(lower["grade_label"]),
                lower_items=lower_items,
                max_edges_per_sfi=max_edges_per_sfi,
                subject_label=subject_label,
                upper_grade_label=str(upper["grade_label"]),
                upper_items=upper_items,
            )

            resp = infer_progression_edges(
                always_double_check_first_attempt=False,
                instructions=prompt.system_message,
                model=config.model,
                user_message=prompt.user_message,
                validator=partial(
                    validate_cross_grade_relates_to,
                    allowed_lo=allowed_lo,
                    allowed_hi=allowed_hi,
                    forbidden_pairs=forbidden_pairs_set,
                ),
            )

            for e in resp.edges:
                ce = CandidateEdge(
                    confidence=float(e.confidence),
                    evidence={"rationale": e.rationale},
                    inference_source="llm",
                    inference_type="phase4_cross_grade_relates_to",
                    llm_confidence=float(e.confidence),
                    metadata={
                        "lower_grade_label": str(lower["grade_label"]),
                        "subject_label": subject_label,
                        "upper_grade_label": str(upper["grade_label"]),
                    },
                    rel_type="relatesTo",
                    source_sfi_uuid=_uuid(e.source_sfi_uuid),
                    target_sfi_uuid=_uuid(e.target_sfi_uuid),
                )
                candidates.append(ce)
                provenance_rows.append(
                    {
                        "confidence": ce.confidence,
                        "lower_grade_label": str(lower["grade_label"]),
                        "rationale": e.rationale,
                        "rel_type": "relatesTo",
                        "source": str(ce.source_sfi_uuid),
                        "subject_label": subject_label,
                        "target": str(ce.target_sfi_uuid),
                        "upper_grade_label": str(upper["grade_label"]),
                    }
                )

    return candidates, provenance_rows


def _infer_within_grade_builds_towards(
    *, by_grade: dict[str, list[dict[str, Any]]], config: CreateKGConfig
) -> tuple[list[CandidateEdge], list[dict[str, Any]]]:
    """Perform Phase 1 inference: Within-grade buildsTowards relationships.

    Parameters
    ----------
    by_grade
        Dictionary mapping grade labels to lists of bucket dictionaries.
    config
        The knowledge graph run configuration.

    Returns
    -------
    tuple[list[CandidateEdge], list[dict[str, Any]]]
        A tuple containing the list of generated candidate edges and the list of
        provenance dictionaries.
    """

    candidates: list[CandidateEdge] = []
    provenance_rows: list[dict[str, Any]] = []

    if not config.progressions_within_grade_builds_towards:
        return candidates, provenance_rows

    phase1_calls = sum(
        1
        for grade_buckets in by_grade.values()
        for b in grade_buckets
        if len(b.get("items", [])) >= 2
    )

    logger.info(
        f"{phase1_calls} buckets with 2+ items for within-grade buildsTowards inference."
    )

    for grade_label, grade_buckets in by_grade.items():
        for bucket in grade_buckets:
            items = bucket.get("items") or []

            if len(items) < 2:
                continue

            ordered_items = []
            for item in items:
                ordered_items.append(
                    {
                        "sfi_uuid": item["sfi_uuid"],
                        "statement_code": item.get("statement_code"),
                        "description": item.get("description"),
                        "notes": item.get("notes"),
                        "page_index": item.get("page_index"),
                        "order_index_within_parent": item.get(
                            "order_index_within_parent"
                        ),
                    }
                )

            prompt = within_grade_builds_towards(
                grade_label=str(grade_label),
                items=ordered_items,
                thread_path=str(
                    bucket.get("topic_path") or bucket.get("topic_path_key")
                ),
            )

            pos = {str(it["sfi_uuid"]): idx for idx, it in enumerate(ordered_items)}
            allowed = set(pos.keys())

            response = infer_progression_edges(
                always_double_check_first_attempt=config.always_double_check_first_attempt,
                instructions=prompt.system_message,
                model=config.model,
                user_message=prompt.user_message,
                validator=partial(
                    validate_within_grade_builds_towards,
                    allowed_uuids=allowed,
                    uuid_positions=pos,
                ),
            )

            for edge in response.edges:
                candidate_edge = CandidateEdge(
                    confidence=float(edge.confidence),
                    evidence={"rationale": edge.rationale},
                    inference_source="llm",
                    inference_type="within_grade_builds_towards",
                    llm_confidence=float(edge.confidence),
                    metadata={
                        "grade_label": grade_label,
                        "subject_label": bucket.get("subject_label"),
                        "topic_path": bucket.get("topic_path"),
                        "topic_path_key": bucket.get("topic_path_key"),
                    },
                    rel_type="buildsTowards",
                    source_sfi_uuid=_uuid(edge.source_sfi_uuid),
                    target_sfi_uuid=_uuid(edge.target_sfi_uuid),
                )
                candidates.append(candidate_edge)
                provenance_rows.append(
                    {
                        "bucket_key": bucket.get("bucket_key"),
                        "confidence": candidate_edge.confidence,
                        "rationale": edge.rationale,
                        "rel_type": "buildsTowards",
                        "source": str(candidate_edge.source_sfi_uuid),
                        "target": str(candidate_edge.target_sfi_uuid),
                    }
                )

    return candidates, provenance_rows


def _infer_within_grade_relates_to(
    *, by_grade: dict[str, list[dict[str, Any]]], config: CreateKGConfig
) -> tuple[list[CandidateEdge], list[dict[str, Any]]]:
    """Perform Phase 3 inference: Within-grade relatesTo relationships.

    Parameters
    ----------
    by_grade
        Dictionary mapping grade labels to lists of bucket dictionaries.
    config
        The knowledge graph run configuration.

    Returns
    -------
    tuple[list[CandidateEdge], list[dict[str, Any]]]
        A tuple containing the list of generated candidate edges and the list of
        provenance dictionaries.
    """

    candidates: list[CandidateEdge] = []
    provenance_rows: list[dict[str, Any]] = []

    if not config.progressions_within_grade_relates_to:
        return candidates, provenance_rows

    max_items = int(config.progressions_within_grade_relates_to_max_items_per_subject)
    max_edges_per_sfi = int(config.progressions_relates_to_max_edges_per_sfi)
    phase3_calls = sum(
        (k * (k - 1)) // 2
        for grade_buckets in by_grade.values()
        for subject in {
            str(b.get("subject_label") or "UNSPECIFIED_SUBJECT") for b in grade_buckets
        }
        for nonempty_threads in [
            sum(
                1
                for b in grade_buckets
                if str(b.get("subject_label") or "UNSPECIFIED_SUBJECT") == subject
                and len(b.get("items", [])) > 0
            )
        ]
        for k in [min(nonempty_threads, max_items)]
        if k >= 2
    )

    grade_subject_threads: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for grade_label, grade_buckets in by_grade.items():
        for b in grade_buckets:
            subject = str(b.get("subject_label") or "UNSPECIFIED_SUBJECT")
            grade_subject_threads[grade_label][subject].append(b)

    logger.info(
        f"{phase3_calls} within-grade pairs of threads with 2+ items for relatesTo inference."
    )

    for grade_label, by_subject in grade_subject_threads.items():
        for subject_label, thread_buckets in by_subject.items():
            thread_buckets_sorted = sorted(
                thread_buckets,
                key=lambda b: (
                    str(b.get("topic_path") or ""),
                    str(b.get("topic_path_key") or ""),
                ),
            )
            sampled = _sample_items_across_threads(
                max_items=max_items, thread_buckets=thread_buckets_sorted
            )

            by_thread_sampled: dict[str, list[dict[str, Any]]] = defaultdict(list)
            thread_path_by_key: dict[str, str] = {}

            for it in sampled:
                tkey = it.get("_thread_key")

                if not tkey:
                    continue

                by_thread_sampled[tkey].append(it)
                thread_path_by_key[tkey] = str(it.get("_thread_path") or "")

            thread_keys = sorted(by_thread_sampled.keys())

            for t1, t2 in combinations(thread_keys, 2):
                items_a = [
                    {
                        "sfi_uuid": it["sfi_uuid"],
                        "statement_code": it.get("statement_code"),
                        "description": it.get("description"),
                        "notes": it.get("notes"),
                        "page_index": it.get("page_index"),
                    }
                    for it in by_thread_sampled[t1]
                ]
                items_b = [
                    {
                        "sfi_uuid": it["sfi_uuid"],
                        "statement_code": it.get("statement_code"),
                        "description": it.get("description"),
                        "notes": it.get("notes"),
                        "page_index": it.get("page_index"),
                    }
                    for it in by_thread_sampled[t2]
                ]

                allowed_a = {str(it["sfi_uuid"]) for it in items_a}
                allowed_b = {str(it["sfi_uuid"]) for it in items_b}

                prompt = within_grade_relates_to(
                    grade_label=str(grade_label),
                    items_a=items_a,
                    items_b=items_b,
                    max_edges_per_sfi=max_edges_per_sfi,
                    subject_label=subject_label,
                    thread_a_key=t1,
                    thread_b_key=t2,
                    thread_a_path=thread_path_by_key.get(t1, ""),
                    thread_b_path=thread_path_by_key.get(t2, ""),
                )

                response = infer_progression_edges(
                    always_double_check_first_attempt=False,
                    instructions=prompt.system_message,
                    model=config.model,
                    user_message=prompt.user_message,
                    validator=partial(
                        validate_within_grade_relates_to,
                        allowed_uuids_a=allowed_a,
                        allowed_uuids_b=allowed_b,
                    ),
                )

                for e in response.edges:
                    ce = CandidateEdge(
                        confidence=float(e.confidence),
                        evidence={"rationale": e.rationale},
                        inference_source="llm",
                        inference_type="within_grade_relates_to",
                        llm_confidence=float(e.confidence),
                        metadata={
                            "grade_label": grade_label,
                            "subject_label": subject_label,
                            "thread_a_key": t1,
                            "thread_b_key": t2,
                        },
                        rel_type="relatesTo",
                        source_sfi_uuid=_uuid(e.source_sfi_uuid),
                        target_sfi_uuid=_uuid(e.target_sfi_uuid),
                    )
                    candidates.append(ce)
                    provenance_rows.append(
                        {
                            "confidence": ce.confidence,
                            "grade_label": grade_label,
                            "rationale": e.rationale,
                            "rel_type": "relatesTo",
                            "source": str(ce.source_sfi_uuid),
                            "subject_label": subject_label,
                            "target": str(ce.target_sfi_uuid),
                        }
                    )

    return candidates, provenance_rows


def _limit_relates_to_edges_per_sfi(
    *, edges: list[CandidateEdge], max_edges_per_sfi: int
) -> tuple[list[CandidateEdge], list[CandidateEdge]]:
    """Greedy limit for undirected relatesTo edges per node.

    Parameters
    ----------
    edges
        A list of CandidateEdge instances representing proposed relatesTo relationships
        between StandardsFrameworkItems (SFIs), which may include multiple edges
        connected to the same SFI.
    max_edges_per_sfi
        The maximum number of relatesTo edges to allow per SFI. If set to 0 or a
        negative value, no edges will be dropped.

    Returns
    -------
    tuple[list[CandidateEdge], list[CandidateEdge]]
        A tuple containing two lists of CandidateEdge instances: the first list
        includes the edges that are kept after applying the limit, and the second list
        includes the edges that are dropped due to exceeding the maximum allowed edges
        per SFI.
    """

    if max_edges_per_sfi <= 0:
        return [], edges

    counts: dict[UUID, int] = defaultdict(int)
    kept: list[CandidateEdge] = []
    dropped: list[CandidateEdge] = []

    # Sort deterministically: highest confidence first, then UUID pair.
    edges_sorted = sorted(
        edges,
        key=lambda e: (-e.confidence, str(e.source_sfi_uuid), str(e.target_sfi_uuid)),
    )

    for e in edges_sorted:
        a, b = e.source_sfi_uuid, e.target_sfi_uuid

        if counts[a] >= max_edges_per_sfi or counts[b] >= max_edges_per_sfi:
            dropped.append(e)
            continue

        kept.append(e)
        counts[a] += 1
        counts[b] += 1

    return kept, dropped


def _path_string(topic_path_parts: list[dict[str, Any]]) -> str:
    """Convert a list of topic path parts (with optional "role" and label" keys) into a
    compact, stable-ish context string for the LLM.

    Parameters
    ----------
    topic_path_parts
        A list of dictionaries representing parts of a topic path, where each
        dictionary may contain optional "role" and "label" keys.

    Returns
    -------
    str
         A compact, stable-ish context string for the LLM, constructed by concatenating
         the role and label of each topic path part in a specific format.
    """

    chunks: list[str] = []

    for p in topic_path_parts:
        role = (p.get("role") or "").strip()
        label = (p.get("label") or "").strip()

        if role and label:
            chunks.append(f"{role}:{label}")
        elif label:
            chunks.append(label)

    return " -> ".join(chunks)


def _prepare_subject_grade_samples(
    *, by_grade: dict[str, list[dict[str, Any]]], max_items: int
) -> dict[str, dict[int, dict[str, Any]]]:
    """Group and sample items by subject and grade ordinal for Phase 4.

    Parameters
    ----------
    by_grade
        Dictionary mapping grade labels to lists of bucket dictionaries, where each
        bucket dictionary contains information about the subject, grade ordinal, topic
        path, and items (standards) within that bucket.
    max_items
        The maximum number of items to sample across threads for each subject and grade
        combination. If the total number of items across threads exceeds this limit, a
        sampling strategy will be applied to select a representative subset of items
        for use in the LLM prompt during Phase 4 inference.

    Returns
    -------
    dict[str, dict[int, dict[str, Any]]]
        A nested dictionary where the first key is the subject label, the second key is
        the grade ordinal, and the value is a dictionary containing the grade label and
        a list of sampled items (standards) for that subject and grade. This structured
        output is designed to facilitate the construction of LLM prompts for
        cross-grade relatesTo inference in Phase 4, ensuring that the LLM receives a
        manageable and representative set of standards to consider when generating
        candidate relationships.
    """

    subject_grade_samples: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)

    for grade_label, grade_buckets in by_grade.items():
        buckets_by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
        grade_ord: Optional[int] = None

        # Group buckets by subject within this grade.
        for b in grade_buckets:
            if grade_ord is None and isinstance(b.get("grade_ordinal"), int):
                grade_ord = int(b["grade_ordinal"])

            buckets_by_subject[
                str(b.get("subject_label") or "UNSPECIFIED_SUBJECT")
            ].append(b)

        if grade_ord is None:
            continue

        # Process each subject in this grade.
        for subject_label, thread_buckets in buckets_by_subject.items():
            thread_buckets_sorted = sorted(
                thread_buckets,
                key=lambda b: (
                    str(b.get("topic_path") or ""),
                    str(b.get("topic_path_key") or ""),
                ),
            )
            sampled = _sample_items_across_threads(
                max_items=max_items, thread_buckets=thread_buckets_sorted
            )

            if not sampled:
                continue

            prompt_items = [
                {
                    "sfi_uuid": it["sfi_uuid"],
                    "statement_code": it.get("statement_code"),
                    "description": it.get("description"),
                    "notes": it.get("notes"),
                    "page_index": it.get("page_index"),
                    "thread_key": it.get("_thread_key"),
                }
                for it in sampled
            ]

            subject_grade_samples[subject_label][grade_ord] = {
                "grade_label": grade_label,
                "items": prompt_items,
            }

    return subject_grade_samples


def _process_and_filter_candidates(
    *, candidates: list[CandidateEdge], config: CreateKGConfig
) -> tuple[list[Relationship], list[Relationship], dict[str, int]]:
    """Process candidates: dedupe, filter by confidence, limit, and convert.

    Parameters
    ----------
    candidates
        The complete list of raw candidate edges from all inference phases.
    config
        The knowledge graph run configuration.

    Returns
    -------
    tuple[list[Relationship], list[Relationship], dict[str, int]]
        A tuple containing:
        1. List of final buildsTowards relationships.
        2. List of final relatesTo relationships.
        3. A dictionary of counts/statistics for the report.
    """

    candidates = _dedupe_edges(candidates)

    builds_candidates = [e for e in candidates if e.rel_type == "buildsTowards"]
    relates_candidates = [e for e in candidates if e.rel_type == "relatesTo"]

    # Confidence thresholds.
    builds_kept = [
        e
        for e in builds_candidates
        if e.confidence >= config.progressions_builds_towards_min_confidence
    ]
    builds_dropped_low = [
        e
        for e in builds_candidates
        if e.confidence < config.progressions_builds_towards_min_confidence
    ]

    relates_kept_thr = [
        e
        for e in relates_candidates
        if e.confidence >= config.progressions_relates_to_min_confidence
    ]
    relates_dropped_low = [
        e
        for e in relates_candidates
        if e.confidence < config.progressions_relates_to_min_confidence
    ]

    # Limit relatesTo per SFI.
    relates_kept, relates_dropped_cap = _limit_relates_to_edges_per_sfi(
        edges=relates_kept_thr,
        max_edges_per_sfi=int(config.progressions_relates_to_max_edges_per_sfi),
    )

    builds_relationships: list[Relationship] = []
    relates_relationships: list[Relationship] = []

    for e in builds_kept:
        metadata = dict(e.metadata)
        metadata.update(
            {
                "confidence": e.confidence,
                "evidence": e.evidence,
                "inference_source": e.inference_source,
                "inference_type": e.inference_type,
            }
        )
        builds_relationships.append(
            _build_relationship(
                config=config,
                metadata=metadata,
                rel_type="buildsTowards",
                source=e.source_sfi_uuid,
                target=e.target_sfi_uuid,
            )
        )

    for e in relates_kept:
        metadata = dict(e.metadata)
        metadata.update(
            {
                "confidence": e.confidence,
                "evidence": e.evidence,
                "inference_source": e.inference_source,
                "inference_type": e.inference_type,
            }
        )
        relates_relationships.append(
            _build_relationship(
                config=config,
                metadata=metadata,
                rel_type="relatesTo",
                source=e.source_sfi_uuid,
                target=e.target_sfi_uuid,
            )
        )

    stats = {
        "candidate_edges_total_after_dedupe": len(candidates),
        "candidate_builds_towards": len(builds_candidates),
        "candidate_relates_to": len(relates_candidates),
        "builds_kept": len(builds_kept),
        "builds_dropped_low_conf": len(builds_dropped_low),
        "relates_kept_after_threshold": len(relates_kept_thr),
        "relates_dropped_low_conf": len(relates_dropped_low),
        "relates_kept_after_cap": len(relates_kept),
        "relates_dropped_cap": len(relates_dropped_cap),
    }

    return builds_relationships, relates_relationships, stats


def _sample_items_across_threads(
    *, max_items: int, thread_buckets: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Round-robin sample up to max_items across multiple thread buckets. This keeps
    calls bounded while retaining cross-thread diversity.

    Parameters
    ----------
    max_items
        The maximum number of items to sample across all threads.
    thread_buckets
        A list of thread buckets, where each bucket is a dictionary containing a
        "topic_path_key" and a list of "items" (standards) belonging to that thread.

    Returns
    -------
    list[dict[str, Any]]
        A list of sampled items across the threads, with a maximum length of max_items.
        Each item is a dictionary containing "topic_path_key" and "items" (standards)
        from the original thread buckets, along with additional metadata for LLM
        processing.
    """

    if max_items <= 0:
        return []

    # Thread buckets should already be stable-sorted by caller.
    per_thread = [(b["topic_path_key"], list(b["items"])) for b in thread_buckets]

    # Track per-thread index.
    idxs = {t: 0 for t, _ in per_thread}
    sampled: list[dict[str, Any]] = []

    while len(sampled) < max_items:
        progressed = False

        for tkey, items in per_thread:
            i = idxs[tkey]

            if i < len(items):
                sampled_item = dict(items[i])
                sampled_item["_thread_key"] = tkey
                sampled_item["_thread_path"] = str(
                    thread_buckets[
                        [tb["topic_path_key"] for tb in thread_buckets].index(tkey)
                    ].get("topic_path", "")
                )
                sampled.append(sampled_item)
                idxs[tkey] = i + 1
                progressed = True

                if len(sampled) >= max_items:
                    break

        if not progressed:
            break

    return sampled


def _sort_key_for_bucket_sfi(s: dict[str, Any]) -> tuple[int, str, str]:
    """Stable ordering inside a bucket. Prefer explicit order_index; fall back to
    statement_code then uuid.

    Parameters
    ----------
    s
        The StandardsFrameworkItem dictionary to generate a sort key for.

    Returns
    -------
    tuple[int, str, str]
        A tuple containing the order index (or a large default value if not present),
        the stripped statement code (or an empty string if not present), and the SFI
        UUID or case identifier UUID (or an empty string if neither is present).
    """

    order_index = s.get("order_index_within_parent")
    order_index = order_index if isinstance(order_index, int) else 10**9
    code = (s.get("statement_code") or "").strip()

    return order_index, code, s.get("sfi_uuid") or s.get("case_identifier_uuid") or ""


def _uuid(x: str) -> UUID:
    """Convert a string to a UUID, stripping whitespace. This is a helper function to
    ensure that any SFI UUIDs or case identifier UUIDs are properly formatted as UUID
    objects when creating CandidateEdge instances.

    Parameters
    ----------
    x
        The string to convert to a UUID.

    Returns
    -------
    UUID
        The UUID object corresponding to the input string.
    """

    return UUID(str(x).strip())


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
        The exported Academic Standards KG artifacts.
    config
        The knowledge graph run configuration.
    ctx
        The KG export context.
    kg_dirs
        The knowledge graph run directories.

    Returns
    -------
    LearningProgressionsExport
        Emitted buildsTowards and relatesTo relationships and a report of the export
        process.
    """

    buckets_info = group_standards_for_learning_progressions(
        academic_standards=academic_standards, include_provenance=True
    )

    # Write the buckets artifact for debugging.
    write_to_json(
        fp=kg_dirs.learning_progressions / "learning_progressions_buckets.json",
        json_info=buckets_info,
    )

    by_grade: dict[str, list[dict[str, Any]]] = buckets_info.get("by_grade") or {}
    candidates: list[CandidateEdge] = []
    provenance_rows: list[dict[str, Any]] = []

    # Phase 1: Within-grade buildsTowards.
    p1_candidates, p1_prov = _infer_within_grade_builds_towards(
        by_grade=by_grade, config=config
    )
    candidates.extend(p1_candidates)
    provenance_rows.extend(p1_prov)

    # Phase 2: Cross-grade buildsTowards.
    p2_candidates, p2_prov, cross_grade_pairs = _infer_cross_grade_builds_towards(
        by_grade=by_grade, config=config
    )
    candidates.extend(p2_candidates)
    provenance_rows.extend(p2_prov)

    # Phase 3: Within-grade relatesTo.
    p3_candidates, p3_prov = _infer_within_grade_relates_to(
        by_grade=by_grade, config=config
    )
    candidates.extend(p3_candidates)
    provenance_rows.extend(p3_prov)

    # Phase 4: Cross-grade relatesTo.
    p4_candidates, p4_prov = _infer_cross_grade_relates_to(
        by_grade=by_grade, config=config, forbidden_builds_pairs=cross_grade_pairs
    )
    candidates.extend(p4_candidates)
    provenance_rows.extend(p4_prov)

    # Dedupe, filter, limit, and emit final relationships, and gather stats for the report.
    builds_rels, relates_rels, stats = _process_and_filter_candidates(
        candidates=candidates, config=config
    )

    # Write artifacts.
    write_to_json(
        fp=kg_dirs.learning_progressions
        / "learning_progressions_builds_towards_relationships.json",
        json_info=builds_rels,
    )
    write_to_json(
        fp=kg_dirs.learning_progressions
        / "learning_progressions_relates_to_relationships.json",
        json_info=relates_rels,
    )
    write_to_json(
        fp=kg_dirs.learning_progressions
        / "learning_progressions_candidate_edges_provenance.json",
        json_info=provenance_rows,
    )

    # Include nodes for standalone use in graph bundle.
    graph_bundle = _build_learning_progressions_graph_bundle(
        academic_standards=academic_standards,
        ctx=ctx,
        export_dialect=str(config.export_dialect),
        relationships=(builds_rels + relates_rels),
    )
    write_to_json(
        fp=kg_dirs.learning_progressions / "learning_progressions_kg.json",
        json_info=graph_bundle,
    )

    report = {
        "doc_key": ctx.doc_key,
        "counts": stats,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "phase_toggles": {
            "within_grade_builds_towards": config.progressions_within_grade_builds_towards,
            "cross_grade_builds_towards": config.progressions_cross_grade_builds_towards,
            "within_grade_relates_to": config.progressions_within_grade_relates_to,
            "cross_grade_relates_to": config.progressions_cross_grade_relates_to,
        },
        "thresholds": {
            "builds_towards_min_confidence": config.progressions_builds_towards_min_confidence,
            "relates_to_min_confidence": config.progressions_relates_to_min_confidence,
            "relates_to_max_edges_per_sfi": config.progressions_relates_to_max_edges_per_sfi,
            "within_grade_relates_to_max_items_per_subject": config.progressions_within_grade_relates_to_max_items_per_subject,
        },
        "drops": buckets_info.get("drops") or {},
    }
    write_to_json(
        fp=kg_dirs.learning_progressions / "learning_progressions_report.json",
        json_info=report,
    )

    return LearningProgressionsExport(
        builds_towards_relationships=builds_rels,
        graph_bundle=graph_bundle,
        relates_to_relationships=relates_rels,
        report=report,
    )


def group_standards_for_learning_progressions(
    *,
    academic_standards: AcademicStandardsExport,
    include_provenance: bool = True,
    strict_single_grade: bool = False,
) -> dict[str, Any]:
    """Build learning progression buckets for the LLM.

    Parameters
    ----------
    academic_standards
        The exported Academic Standards KG artifacts.
    include_provenance
        Whether to include provenance metadata in the payload for each standard item,
        which the LLM can use as signals when deciding buildsTowards relationships.
        This will make the payload larger and may not be necessary if the standards
        export is already well-structured and clean.
    strict_single_grade
        Whether to enforce that each standard item has exactly one grade_level tag. If
        True, items with multiple grade_level tags will be dropped and recorded in the
        report. This can help catch data issues in exports that are expected to have a
        single grade tag per item, but may need to be relaxed for more complex or
        non-US curricula.

    Returns
    -------
    dict[str, Any]
        A dictionary containing grouped standards by grade and thread, as well as any
        dropped items due to missing or non-standard data.

    Raises
    ------
    ValueError
        If strict_single_grade is True and an item has multiple grade_level tags.
    """

    # grade -> topic_path_key -> bucket.
    buckets: DefaultDict[str, DefaultDict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    drops: dict[str, list[dict[str, Any]]] = {
        "missing_topic_path_key": [],
        "multi_grade_item": [],
        "non_standard_item": [],
        "unassigned_grade": [],
    }

    for sfi in academic_standards.items:
        metadata = sfi.metadata or {}
        progression_context = metadata.get("progression_context") or {}
        sfi_uuid = str(sfi.case_identifier_uuid or sfi.identifier)

        # We only want endpoints for buildsTowards: normative expectations.
        if sfi.normalized_statement_type != "Standard":
            drops["non_standard_item"].append(
                {
                    "description": sfi.description,
                    "normalized_statement_type": sfi.normalized_statement_type,
                    "sfi_uuid": sfi_uuid,
                    "statement_type": sfi.statement_type,
                }
            )
            continue

        # Grade placement (prefer progression_context ordinals).
        grade_label, grade_ord = _grade_label_and_ordinal(sfi)

        if grade_label == "UNSPECIFIED_GRADE":
            drops["unassigned_grade"].append(
                {"description": sfi.description, "sfi_uuid": sfi_uuid}
            )

            if strict_single_grade:
                continue

        grade_level = sfi.grade_level or []

        if strict_single_grade and len(grade_level) != 1:
            drops["multi_grade_item"].append(
                {
                    "description": sfi.description,
                    "grade_level": grade_level,
                    "sfi_uuid": sfi_uuid,
                }
            )
            continue

        topic_path_key = progression_context.get("topic_path_key") or ""

        if not isinstance(topic_path_key, str) or not topic_path_key.strip():
            drops["missing_topic_path_key"].append(
                {
                    "description": sfi.description,
                    "grade": grade_label,
                    "sfi_uuid": sfi_uuid,
                }
            )
            continue

        topic_path_parts = progression_context.get("topic_path_parts") or []

        if not isinstance(topic_path_parts, list):
            topic_path_parts = []

        b = buckets[grade_label].get(topic_path_key)

        if not b:
            b = {
                "bucket_key": f"{grade_label}::{topic_path_key}",
                "grade_level": grade_label,
                "grade_ordinal": grade_ord,
                "topic_path_key": topic_path_key,
                "topic_path": _path_string(topic_path_parts),
                "topic_path_parts": topic_path_parts,  # Structured context for the LLM
                "items": [],  # The standards in this (grade, thread)
            }
            buckets[grade_label][topic_path_key] = b

        # Minimal LLM payload for this SFI. NB: This is what the buildsTowards
        # relationships must reference.
        payload = {
            "description": sfi.description,
            "notes": sfi.notes,
            "order_index_within_parent": progression_context.get(
                "order_index_within_parent"
            ),
            "sfi_uuid": sfi_uuid,
            "statement_code": sfi.statement_code,
            "statement_type": sfi.statement_type,
        }

        if include_provenance:
            page_indices = (
                metadata.get("page_indices")
                if isinstance(metadata.get("page_indices"), list)
                else []
            )
            payload["page_index"] = min(page_indices) if page_indices else None

        b["items"].append(payload)

    return _format_learning_progressions_dict(buckets=buckets, drops=drops)


def normalize_thread_key(topic_path_key: str) -> str:
    """Normalize a topic_path_key by stripping leading digit_ prefixes from each
    segment value.

    Example:
      topic=1_1_exploring_my_world -> topic=exploring_my_world

    Parameters
    ----------
    topic_path_key
        The original topic_path_key string to normalize.

    Returns
    -------
    str
        The normalized topic_path_key string with leading digit_ prefixes removed from
         each segment value.
    """

    parts = []

    for seg in str(topic_path_key or "").split("|"):
        if "=" not in seg:
            parts.append(seg)
            continue

        k, v = seg.split("=", 1)
        v2 = str(v)

        if LEADING_GRADE_PREFIX_RE.match(v2):
            v2 = LEADING_GRADE_PREFIX_RE.sub("", v2)

        parts.append(f"{k}={v2}")

    return "|".join(parts)
