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
3. Within-grade relatesTo (cross-subject within a grade; subject-pair sampling)
4. Cross-grade relatesTo (adjacent grades within the same subject, excluding
    buildsTowards pairs)
"""

# Standard Library
import re

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from typing import Any, DefaultDict, Optional
from uuid import UUID, uuid5

# Third Party Library
from loguru import logger

# Package Library
from skg.kgs.export_academic_standards import AcademicStandardsExport
from skg.kgs.llm import infer_progression_edges
from skg.kgs.prompts import (
    cross_grade_builds_towards,
    cross_grade_relates_to,
    cross_stage_builds_towards,
    cross_stage_relates_to,
    within_grade_builds_towards,
    within_grade_relates_to,
)
from skg.kgs.schemas import (
    ProgressionEdgesResponse,
    Relationship,
    StandardsFrameworkItem,
)
from skg.kgs.utils import ExportContext, KGDirs
from skg.kgs.validators import (
    validate_cross_grade_builds_towards,
    validate_cross_grade_relates_to,
    validate_within_grade_builds_towards,
    validate_within_grade_relates_to,
)
from skg.schemas import CreateKGConfig
from skg.utils.general import write_to_json

# Compiled regexes.
GRADE_INT_RE = re.compile(r"\b(\d+)\b")


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


def _allow_within_grade_inference(
    *, bucket: dict[str, Any], config: CreateKGConfig
) -> bool:
    """Return True if Phase 1/3 within-grade inference should consider this bucket.

    By default, we only run within-grade inference for single-grade buckets. If
    progressions_within_grade_allow_banded_levels=True, banded/stage buckets are
    allowed.

    Parameters
    ----------
    bucket
        The bucket dictionary containing information about a thread of standards within
        a grade, which may include grade bounds and other contextual information.
    config
        The knowledge graph run configuration.

    Returns
    -------
    bool
        True if within-grade inference should be allowed for this bucket based on the
        configuration and whether it represents a single grade or a banded level.
    """

    if config.progressions_within_grade_allow_banded_levels:
        return True

    return _is_single_grade_bucket(bucket)


def _best_map(
    resp: ProgressionEdgesResponse,
) -> dict[tuple[UUID, UUID], tuple[float, str]]:
    """Extract the best confidence and rationale for each canonicalized pair of UUIDs
    regardless of edge direction, to facilitate bidirectional confirmation of relatesTo
    edges between the two levels.

    Parameters
    ----------
    resp
        The response from the infer_progression_edges call, containing a list of edges
        with source and target UUIDs, confidence scores, and rationales.

    Returns
    -------
    dict[tuple[UUID, UUID], tuple[float, str]]
        A dictionary mapping canonicalized pairs of UUIDs (as tuples) to their best
        confidence score and corresponding rationale found in the response edges,
        regardless of the direction of the edge (source -> target or target -> source).
        This allows for easy comparison of confidence scores for the same pair of SFIs
        across the lo -> hi and hi -> lo runs to confirm bidirectional relatesTo
        relationships.
    """

    best: dict[tuple[UUID, UUID], tuple[float, str]] = {}

    for ee in resp.edges:
        u1 = _uuid(ee.source_sfi_uuid)
        u2 = _uuid(ee.target_sfi_uuid)
        a, b = _canon_uuid_pair(u1, u2)
        c = float(ee.confidence)
        r = str(ee.rationale or "")

        if (a, b) not in best or c > best[(a, b)][0]:
            best[(a, b)] = (c, r)

    return best


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


def _build_sfi_index(
    *, by_grade: dict[str, list[dict[str, Any]]]
) -> dict[str, dict[str, Any]]:
    """Build a lookup table of SFI UUID -> context/provenance hints.

    This is used to enrich emitted Relationship.metadata so downstream consumers can
    reason about edges without having to join back to the source node payloads.

    Parameters
    ----------
    by_grade
        Dictionary mapping grade labels to lists of bucket dictionaries, where each
        bucket contains information about a thread of standards within that grade, and
        each thread contains items that represent individual StandardsFrameworkItems
        with their UUIDs and other contextual information.

    Returns
    -------
    dict[str, dict[str, Any]]
        A dictionary mapping SFI UUIDs (as strings) to dictionaries of context and
        provenance hints, such as grade label, subject label, topic path, statement
        code, and page index. This index allows for quick lookup of relevant
        information about an SFI when processing inferred relationships, enabling the
        enrichment of Relationship.metadata with details about the source and target
        SFIs without needing to reference the full node payloads.
    """

    index: dict[str, dict[str, Any]] = {}

    for grade_label, grade_buckets in (by_grade or {}).items():
        for b in grade_buckets or []:
            for it in b.get("items") or []:
                u = str(it.get("sfi_uuid") or "").strip()

                if not u or u in index:
                    continue

                index[u] = {
                    "grade_label": grade_label,
                    "subject_label": b.get("subject_label"),
                    "topic_path_key": b.get("topic_path_key"),
                    "normalized_topic_path_key": b.get("normalized_topic_path_key"),
                    "thread_key": b.get("thread_key"),
                    "topic_path": b.get("topic_path"),
                    "statement_code": it.get("statement_code"),
                    "page_index": it.get("page_index"),
                    "order_index_within_parent": it.get("order_index_within_parent"),
                }

    return index


def _build_thread_map(
    by_grade: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Organize buckets by thread key and level-range order.

    For cross-level inference we need to handle both:
      - single-grade buckets (low==high) for true cross-grade adjacency, and
      - banded buckets (low!=high) for cross-stage adjacency.

    The returned mapping groups buckets by thread key and sorts them by
    (grade_ordinal_low, grade_ordinal_high).

    Parameters
    ----------
    by_grade
        Dictionary mapping grade labels to lists of bucket dictionaries, where each
        bucket contains information about a thread of standards within that grade.

    Returns
    -------
    dict[str, list[dict[str, Any]]]
        A dictionary mapping thread keys to lists of bucket dictionaries, where each
        list is sorted by (grade_ordinal_low, grade_ordinal_high) to facilitate
        cross-grade and cross-stage buildsTowards inference. Buckets without integer
        grade bounds are skipped and counted for logging purposes.

    Raises
    ------
    ValueError
        If any bucket is missing a valid thread key in its progression_context, since
        the thread key is essential for grouping buckets for cross-grade inference.
    """

    thread_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skipped_no_bounds = 0
    missing_thread_key = 0
    missing_thread_key_examples: list[str] = []

    for grade_buckets in by_grade.values():
        for b in grade_buckets:
            lo, hi = _level_bounds(b)

            if not isinstance(lo, int) or not isinstance(hi, int):
                skipped_no_bounds += 1
                continue

            thread_key = b.get("thread_key")

            if not isinstance(thread_key, str) or not thread_key.strip():
                missing_thread_key += 1

                if len(missing_thread_key_examples) < 3:
                    missing_thread_key_examples.append(
                        str(b.get("bucket_key") or b.get("topic_path_key") or "")
                    )

                continue

            thread_key = thread_key.strip()
            thread_map[thread_key].append(b)

    for k in list(thread_map.keys()):
        thread_map[k] = sorted(
            thread_map[k],
            key=lambda b: (
                int(_level_bounds(b)[0] or 10**9),
                int(_level_bounds(b)[1] or 10**9),
                str(b.get("topic_path") or ""),
                str(b.get("topic_path_key") or ""),
            ),
        )

    if skipped_no_bounds > 0:
        logger.warning(
            f"Skipped {skipped_no_bounds} buckets without integer grade bounds "
            f"(missing grade/stage ordinal data)."
        )

    if missing_thread_key > 0:
        raise ValueError(
            f"Missing progression_context.thread_key in "
            f"{missing_thread_key} bucket(s). Re-export Academic Standards so each "
            f"SFI has metadata.progression_context.thread_key. "
            f"Examples: {missing_thread_key_examples}"
        )

    return thread_map


def _canon_uuid_pair(a: UUID, b: UUID) -> tuple[UUID, UUID]:
    """Return (min_uuid, max_uuid) using UUID.int ordering.

    Parameters
    ----------
    a
        The first UUID to compare and order.
    b
        The second UUID to compare and order.

    Returns
    -------
    tuple[UUID, UUID]
        A tuple containing the two UUIDs ordered by their integer value, with the
        smaller (earlier) UUID first and the larger (later) UUID second. This is used
        for canonicalizing pairs of UUIDs in undirected relationships like relatesTo,
        where the order of source and target does not matter.
    """

    return (a, b) if a.int <= b.int else (b, a)


def _compute_expected_phase1_calls(
    *, by_grade: dict[str, list[dict[str, Any]]], config: CreateKGConfig
) -> int:
    """Count and log expected LLM calls for phase 1.

    Parameters
    ----------
    by_grade
        Dictionary mapping grade labels to lists of bucket dictionaries.
    config
        The knowledge graph run configuration.

    Returns
    -------
    int
        The total number of buckets that contain 2 or more items, representing
        the number of LLM calls required.
    """

    phase1_calls = sum(
        1
        for grade_buckets in by_grade.values()
        for b in grade_buckets
        if _allow_within_grade_inference(bucket=b, config=config)
        and len(b.get("items", [])) >= 2
    )

    logger.info(
        f"{phase1_calls} buckets with 2+ items for within-grade buildsTowards inference."
    )

    return phase1_calls


def _compute_expected_phase2_calls(
    *, config: CreateKGConfig, thread_map: dict[str, list[dict[str, Any]]]
) -> int:
    """Count and log expected LLM calls for phase 2 based on the normalized thread map.

    Parameters
    ----------
    config
        The knowledge graph run configuration.
    thread_map
        A nested dictionary mapping normalized thread keys to dictionaries that map
        grade ordinals to their corresponding bucket dictionaries. This structure is
        used to determine how many adjacent grade pairs exist for each thread, which in
        turn indicates how many LLM calls will be made for cross-grade buildsTowards
        inference in phase 2.

    Returns
    -------
    int
        The total number of expected LLM calls for phase 2, which corresponds to the
        number of adjacent grade pairs with items for each normalized thread key.
    """

    cross_grade_calls = 0
    cross_stage_calls = 0

    for buckets in thread_map.values():
        for lower, upper in zip(buckets, buckets[1:]):
            if not _levels_adjacent(lower, upper):
                continue

            both_single = _is_single_grade_bucket(lower) and _is_single_grade_bucket(
                upper
            )

            if both_single and config.progressions_cross_grade_builds_towards:
                cross_grade_calls += 1
            elif (not both_single) and config.progressions_cross_stage_builds_towards:
                cross_stage_calls += 1

    total = cross_grade_calls + cross_stage_calls

    if config.progressions_cross_grade_builds_towards:
        logger.info(
            f"{cross_grade_calls} adjacent single-grade pairs for cross-grade buildsTowards inference."
        )
    if config.progressions_cross_stage_builds_towards:
        logger.info(
            f"{cross_stage_calls} adjacent level pairs for cross-stage buildsTowards inference."
        )

    return total


def _compute_expected_phase3_calls(
    *, grade_subject_threads: dict[str, dict[str, list[dict[str, Any]]]], max_items: int
) -> int:
    """Count and log expected LLM calls for phase 3.

    Parameters
    ----------
    grade_subject_threads
        A nested dictionary mapping grade labels to subject labels to lists of bucket
        dictionaries, representing the organization of standards by grade and subject.
    max_items
        The maximum number of items to sample per subject for relatesTo inference,
        which affects the number of LLM calls since pairs without enough items are
        skipped.

    Returns
    -------
    int
        The total number of expected LLM calls for phase 3, which corresponds to the
        number of cross-subject (subject_a, subject_b) pairs within each grade that
        have enough sampled items to populate the LLM prompt.
    """

    def _sort_key(b: dict[str, Any]) -> tuple[str, str]:
        """Sorting key for threads within a subject, to ensure deterministic sampling
        of items for the LLM prompt. Sort by topic path, then topic path key as a
        tiebreaker.

        Parameters
        ----------
        b
            A bucket dictionary containing information about a thread of standards,
            which may include "topic_path" and "topic_path_key" keys.

        Returns
        -------
        tuple[str, str]
            A tuple containing the topic path and topic path key as strings, used for
            sorting threads in a deterministic order for sampling.
        """

        return str(b.get("topic_path") or ""), str(b.get("topic_path_key") or "")

    phase3_calls = 0

    for by_subject in grade_subject_threads.values():
        # Match Phase 3 runtime filtering in _infer_within_grade_relates_to().
        # Otherwise the "expected calls" log over-counts by including placeholders.
        subject_keys = [
            s
            for s in sorted(by_subject.keys())
            if s not in {"UNSPECIFIED_SUBJECT", "UNKNOWN", ""}
        ]

        if len(subject_keys) < 2:
            continue

        for i, s1 in enumerate(subject_keys):
            for s2 in subject_keys[i + 1 :]:
                # Sample from each subject, then pair across.
                sampled_a = _sample_items_across_threads(
                    max_items=max_items,
                    thread_buckets=sorted(by_subject[s1], key=_sort_key),
                )
                sampled_b = _sample_items_across_threads(
                    max_items=max_items,
                    thread_buckets=sorted(by_subject[s2], key=_sort_key),
                )

                if sampled_a and sampled_b:
                    phase3_calls += 1

    logger.info(
        f"{phase3_calls} within-grade cross-subject pairs for relatesTo inference "
        f"(bidirectional confirmation => {phase3_calls * 2} LLM calls)."
    )

    return phase3_calls * 2


def _compute_expected_phase4_calls(
    *,
    config: CreateKGConfig,
    subject_level_samples: dict[str, dict[tuple[int, int], dict[str, Any]]],
) -> int:
    """Count and log expected LLM calls for phase 4 (cross-grade and optional
    cross-stage).

    Parameters
    ----------
    config
        The knowledge graph run configuration, containing flags for which types of
        inference are enabled (cross-grade relatesTo and/or cross-stage relatesTo).
    subject_level_samples
        A nested dictionary mapping subject labels to dictionaries that map (grade_low,
        grade_high) tuples to their corresponding bucket dictionaries, which include
        the sampled items for each grade and subject. This structure is used to
        determine how many adjacent grade pairs exist within each subject that have
        enough items to be sampled for the LLM prompt, which in turn indicates how many
        LLM calls will be made for cross-grade and cross-stage relatesTo inference in
        phase 4.

    Returns
    -------
    int
        The total number of expected LLM calls for phase 4, which corresponds to the
        number of adjacent grade pairs with sampled items for each subject label,
        taking into account whether the pairs represent single-grade adjacency (for
        cross-grade relatesTo) or banded-level adjacency (for cross-stage relatesTo),
        and whether the respective inference types are enabled in the config.
    """

    cross_grade_calls = 0
    cross_stage_calls = 0

    for by_level in subject_level_samples.values():
        keys = sorted(by_level.keys(), key=lambda k: (k[0], k[1]))

        for k_lo, k_hi in zip(keys, keys[1:]):
            lo_low, lo_high = k_lo
            hi_low, hi_high = k_hi

            if lo_high + 1 != hi_low:
                continue

            both_single = (lo_low == lo_high) and (hi_low == hi_high)

            if both_single and config.progressions_cross_grade_relates_to:
                cross_grade_calls += 1
            elif (not both_single) and config.progressions_cross_stage_relates_to:
                cross_stage_calls += 1

    total = cross_grade_calls + cross_stage_calls

    # Bidirectional confirmation doubles the number of LLM calls per (subject,
    # adjacent-level) pair.
    total_calls = total * 2

    if config.progressions_cross_grade_relates_to:
        logger.info(
            f"{cross_grade_calls} adjacent single-grade pairs for cross-grade relatesTo inference."
        )
    if config.progressions_cross_stage_relates_to:
        logger.info(
            f"{cross_stage_calls} adjacent level pairs for cross-stage relatesTo inference."
        )

    return total_calls


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

        if e.rel_type == "relatesTo" and s.int > t.int:
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
    grade_ordinal_high = progression_context.get("grade_ordinal_high")

    # If this SFI belongs to a banded level (low != high), label it as a band/stage.
    if (
        isinstance(grade_ordinal_low, int)
        and isinstance(grade_ordinal_high, int)
        and grade_ordinal_high != grade_ordinal_low
    ):
        parts = progression_context.get("topic_path_parts") or []
        stage_label = ""

        if isinstance(parts, list):
            stage_label = next(
                (
                    str(p.get("label") or "").strip()
                    for p in parts
                    if p.get("role") == "stage" and p.get("label")
                ),
                "",
            )

        label = stage_label or f"GRADES {grade_ordinal_low}–{grade_ordinal_high}"
        return label, grade_ordinal_low

    if isinstance(grade_ordinal_low, int):
        return f"GRADE {grade_ordinal_low}", grade_ordinal_low

    grade_level = sfi.grade_level or []

    if grade_level:
        label = str(grade_level[0]).strip().upper()
        m = GRADE_INT_RE.search(label)
        return label, int(m.group(1)) if m else None

    return "UNSPECIFIED_GRADE", None


def _group_threads_by_grade_and_subject(
    *, by_grade: dict[str, list[dict[str, Any]]], config: CreateKGConfig
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Group threads by grade and subject, filtering invalid items.

    Parameters
    ----------
    by_grade
        Dictionary mapping grade labels to lists of bucket dictionaries, where each
        bucket contains information about a thread of standards within that grade, and
        may include a "subject_label" key indicating the subject of the thread.
    config
        The knowledge graph run configuration.

    Returns
    -------
    dict[str, dict[str, list[dict[str, Any]]]]
        A nested dictionary mapping grade labels to subject labels to lists of bucket
        dictionaries, representing the organization of threads by grade and subject for
        within-grade relatesTo inference. Only buckets that are allowed for
        within-grade inference based on the configuration (e.g., single-grade buckets
        if progressions_within_grade_allow_banded_levels=False) are included in the
        output.
    """

    grade_subject_threads: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for grade_label, grade_buckets in by_grade.items():
        for b in grade_buckets:
            if not _allow_within_grade_inference(bucket=b, config=config):
                continue

            subject = str(b.get("subject_label") or "UNSPECIFIED_SUBJECT")
            grade_subject_threads[grade_label][subject].append(b)

    return grade_subject_threads


def _infer_cross_grade_builds_towards(
    *, by_grade: dict[str, list[dict[str, Any]]], config: CreateKGConfig
) -> tuple[list[CandidateEdge], list[dict[str, Any]], set[tuple[UUID, UUID]]]:
    """Perform Phase 2 inference: Cross-grade buildsTowards relationships with optional
    cross-stage fallback.

    1. If BOTH adjacent buckets represent single grades (low == high), run true
        cross-grade.
    2. If EITHER side is banded (low != high) and cross-stage is enabled, run
        cross-stage.

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
    cross_level_build_pairs: set[tuple[UUID, UUID]] = set()

    if not (
        config.progressions_cross_grade_builds_towards
        or config.progressions_cross_stage_builds_towards
    ):
        return candidates, provenance_rows, cross_level_build_pairs

    thread_map = _build_thread_map(by_grade)
    total_calls = _compute_expected_phase2_calls(config=config, thread_map=thread_map)
    current_call = 0

    for thread_key, buckets in thread_map.items():
        for b_lo, b_hi in zip(buckets, buckets[1:]):
            if not _levels_adjacent(b_lo, b_hi):
                continue

            lower_items = b_lo.get("items") or []
            upper_items = b_hi.get("items") or []

            if not lower_items or not upper_items:
                continue

            both_single = _is_single_grade_bucket(b_lo) and _is_single_grade_bucket(
                b_hi
            )

            if both_single:
                if not config.progressions_cross_grade_builds_towards:
                    continue

                inference_type = "cross_grade_builds_towards"
                prompt_builder = cross_grade_builds_towards
            else:
                if not config.progressions_cross_stage_builds_towards:
                    continue

                inference_type = "cross_stage_builds_towards"
                prompt_builder = cross_stage_builds_towards

            lo_label = _level_label(b_lo)
            hi_label = _level_label(b_hi)
            lo_lo, lo_hi = _level_bounds(b_lo)
            hi_lo, hi_hi = _level_bounds(b_hi)

            current_call += 1
            logger.info(
                f"Phase 2 Progress: {current_call}/{total_calls} "
                f"({lo_label} -> {hi_label} | {thread_key} | {inference_type})"
            )

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

            prompt = prompt_builder(
                lower_items=lower_payload,
                lower_grade_label=lo_label,
                thread_key=thread_key,
                thread_path=str(b_hi.get("topic_path") or b_hi.get("topic_path_key")),
                upper_grade_label=hi_label,
                upper_items=upper_payload,
            )

            allowed_lo = {str(it["sfi_uuid"]) for it in lower_payload}
            allowed_hi = {str(it["sfi_uuid"]) for it in upper_payload}

            response = infer_progression_edges(
                always_double_check_first_attempt=config.always_double_check_first_attempt,
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
                    inference_type=inference_type,
                    metadata={
                        "phase": 2,
                        "lower_level_label": lo_label,
                        "upper_level_label": hi_label,
                        "lower_level_low": lo_lo,
                        "lower_level_high": lo_hi,
                        "upper_level_low": hi_lo,
                        "upper_level_high": hi_hi,
                        "thread_key": thread_key,
                        "topic_path_key_upper": b_hi.get("topic_path_key"),
                        "topic_path": b_hi.get("topic_path"),
                        "subject_label": b_hi.get("subject_label"),
                    },
                    rel_type="buildsTowards",
                    source_sfi_uuid=_uuid(e.source_sfi_uuid),
                    target_sfi_uuid=_uuid(e.target_sfi_uuid),
                )
                candidates.append(ce)
                cross_level_build_pairs.add((ce.source_sfi_uuid, ce.target_sfi_uuid))
                provenance_rows.append(
                    {
                        "confidence": ce.confidence,
                        "lower_level": lo_label,
                        "thread_key": thread_key,
                        "rationale": e.rationale,
                        "rel_type": "buildsTowards",
                        "source": str(ce.source_sfi_uuid),
                        "target": str(ce.target_sfi_uuid),
                        "upper_level": hi_label,
                        "inference_type": inference_type,
                    }
                )

    return candidates, provenance_rows, cross_level_build_pairs


def _infer_cross_grade_relates_to(
    *,
    by_grade: dict[str, list[dict[str, Any]]],
    config: CreateKGConfig,
    forbidden_builds_pairs: set[tuple[UUID, UUID]],
) -> tuple[list[CandidateEdge], list[dict[str, Any]]]:
    """Perform Phase 4 inference: Cross-grade relatesTo relationships with optional
    cross-stage fallback.

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

    if not (
        config.progressions_cross_grade_relates_to
        or config.progressions_cross_stage_relates_to
    ):
        return candidates, provenance_rows

    max_items = int(config.progressions_cross_grade_relates_to_max_items_per_subject)
    max_edges_per_sfi = int(config.progressions_relates_to_max_edges_per_sfi)

    subject_level_samples = _prepare_subject_grade_samples(
        by_grade=by_grade, max_items=max_items
    )

    total_calls = _compute_expected_phase4_calls(
        config=config, subject_level_samples=subject_level_samples
    )
    current_call = 0

    for subject_label, by_level in subject_level_samples.items():
        level_keys = sorted(by_level.keys(), key=lambda k: (k[0], k[1]))

        for k_lo, k_hi in zip(level_keys, level_keys[1:]):
            lo_low, lo_high = k_lo
            hi_low, hi_high = k_hi

            if lo_high + 1 != hi_low:
                continue

            lower = by_level[k_lo]
            upper = by_level[k_hi]
            lower_items = lower["items"]
            upper_items = upper["items"]

            if not lower_items or not upper_items:
                continue

            both_single = (lo_low == lo_high) and (hi_low == hi_high)

            if both_single:
                if not config.progressions_cross_grade_relates_to:
                    continue

                inference_type = "cross_grade_relates_to"
                prompt_builder = cross_grade_relates_to
            else:
                if not config.progressions_cross_stage_relates_to:
                    continue

                inference_type = "cross_stage_relates_to"
                prompt_builder = cross_stage_relates_to

            forbidden_pairs_set, forbidden_pairs = _resolve_forbidden_pairs(
                forbidden_builds_pairs=forbidden_builds_pairs,
                lower_items=lower_items,
                upper_items=upper_items,
            )

            # Bidirectional confirmation: run lo→hi and hi→lo (swapped prompt inputs),
            # then keep only edges that appear in both runs (canonicalized by UUID
            # order).
            prompt_lo_hi = prompt_builder(
                forbidden_pairs=forbidden_pairs,
                lower_grade_label=str(lower["level_label"]),
                lower_items=lower_items,
                max_edges_per_sfi=max_edges_per_sfi,
                subject_label=subject_label,
                upper_grade_label=str(upper["level_label"]),
                upper_items=upper_items,
            )

            current_call += 1
            logger.info(
                f"Phase 4 Progress: {current_call}/{total_calls} "
                f"({subject_label}: {lower['level_label']} -> {upper['level_label']} | {inference_type} | lo→hi)"
            )

            resp_lo_hi = infer_progression_edges(
                always_double_check_first_attempt=config.always_double_check_first_attempt,
                instructions=prompt_lo_hi.system_message,
                model=config.model,
                user_message=prompt_lo_hi.user_message,
                validator=partial(
                    validate_cross_grade_relates_to,
                    allowed_lo={str(it["sfi_uuid"]) for it in lower_items},
                    allowed_hi={str(it["sfi_uuid"]) for it in upper_items},
                    forbidden_pairs=forbidden_pairs_set,
                ),
            )

            prompt_hi_lo = prompt_builder(
                forbidden_pairs=forbidden_pairs,
                lower_grade_label=str(upper["level_label"]),
                lower_items=upper_items,
                max_edges_per_sfi=max_edges_per_sfi,
                subject_label=subject_label,
                upper_grade_label=str(lower["level_label"]),
                upper_items=lower_items,
            )

            current_call += 1
            logger.info(
                f"Phase 4 Progress: {current_call}/{total_calls} "
                f"({subject_label}: {lower['level_label']} -> {upper['level_label']} | {inference_type} | hi→lo)"
            )

            resp_hi_lo = infer_progression_edges(
                always_double_check_first_attempt=config.always_double_check_first_attempt,
                instructions=prompt_hi_lo.system_message,
                model=config.model,
                user_message=prompt_hi_lo.user_message,
                validator=partial(
                    validate_cross_grade_relates_to,
                    allowed_lo={str(it["sfi_uuid"]) for it in upper_items},
                    allowed_hi={str(it["sfi_uuid"]) for it in lower_items},
                    forbidden_pairs=forbidden_pairs_set,
                ),
            )

            m_lo_hi = _best_map(resp_lo_hi)
            m_hi_lo = _best_map(resp_hi_lo)
            common_pairs = sorted(
                (set(m_lo_hi.keys()) & set(m_hi_lo.keys())),
                key=lambda p: (p[0].int, p[1].int),
            )

            for a, b in common_pairs:
                conf_lo_hi, rat_lo_hi = m_lo_hi[(a, b)]
                conf_hi_lo, rat_hi_lo = m_hi_lo[(a, b)]
                conf = min(conf_lo_hi, conf_hi_lo)

                ce = CandidateEdge(
                    confidence=float(conf),
                    evidence={
                        "rationale_lo_hi": rat_lo_hi,
                        "rationale_hi_lo": rat_hi_lo,
                        "confidence_lo_hi": float(conf_lo_hi),
                        "confidence_hi_lo": float(conf_hi_lo),
                        "bidirectional_confirmed": True,
                    },
                    inference_source="llm",
                    llm_confidence=float(conf),
                    inference_type=inference_type,
                    metadata={
                        "phase": 4,
                        "bidirectional_confirmed": True,
                        "subject_label": subject_label,
                        "lower_level_label": lower["level_label"],
                        "upper_level_label": upper["level_label"],
                        "lower_level_low": lo_low,
                        "lower_level_high": lo_high,
                        "upper_level_low": hi_low,
                        "upper_level_high": hi_high,
                    },
                    rel_type="relatesTo",
                    source_sfi_uuid=a,
                    target_sfi_uuid=b,
                )
                candidates.append(ce)
                provenance_rows.append(
                    {
                        "phase": 4,
                        "bidirectional_confirmed": True,
                        "confidence": ce.confidence,
                        "confidence_lo_hi": float(conf_lo_hi),
                        "confidence_hi_lo": float(conf_hi_lo),
                        "subject_label": subject_label,
                        "lower_level": lower["level_label"],
                        "upper_level": upper["level_label"],
                        "rel_type": "relatesTo",
                        "source": str(ce.source_sfi_uuid),
                        "target": str(ce.target_sfi_uuid),
                        "inference_type": inference_type,
                        "rationale_lo_hi": rat_lo_hi,
                        "rationale_hi_lo": rat_hi_lo,
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

    total_calls = _compute_expected_phase1_calls(by_grade=by_grade, config=config)
    current_call = 0

    for grade_label, grade_buckets in by_grade.items():
        for bucket in grade_buckets:
            if not _allow_within_grade_inference(bucket=bucket, config=config):
                continue

            items = bucket.get("items") or []

            if len(items) < 2:
                continue

            current_call += 1
            logger.info(
                f"Phase 1 Progress: {current_call}/{total_calls} "
                f"({grade_label} - {bucket.get('topic_path_key')})"
            )

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
                        "phase": 1,
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
    """Perform Phase 3 inference: Within-grade cross-subject relatesTo relationships.

    For each grade, compare *subjects* (not within-subject threads) to find
    cross-curricular connections (i.e., the Coherence Map pattern). Within-subject
    relatesTo is skipped---those connections are lower value and more likely to produce
    noise.

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

    # Group threads by grade -> subject.
    grade_subject_threads = _group_threads_by_grade_and_subject(
        by_grade=by_grade, config=config
    )

    total_calls = _compute_expected_phase3_calls(
        grade_subject_threads=grade_subject_threads, max_items=max_items
    )
    current_call = 0

    for grade_label, by_subject in grade_subject_threads.items():
        subject_keys = [
            s
            for s in sorted(by_subject.keys())
            if s not in {"UNSPECIFIED_SUBJECT", "UNKNOWN", ""}
        ]

        if len(subject_keys) < 2:
            continue

        for i, subject_a in enumerate(subject_keys):
            for subject_b in subject_keys[i + 1 :]:
                threads_a = sorted(
                    by_subject[subject_a],
                    key=lambda b: (
                        str(b.get("topic_path") or ""),
                        str(b.get("topic_path_key") or ""),
                    ),
                )
                threads_b = sorted(
                    by_subject[subject_b],
                    key=lambda b: (
                        str(b.get("topic_path") or ""),
                        str(b.get("topic_path_key") or ""),
                    ),
                )
                thread_a_path = " | ".join(
                    str(b.get("topic_path") or b.get("topic_path_key") or "").strip()
                    for b in threads_a[:3]
                    if (b.get("topic_path") or b.get("topic_path_key"))
                )
                thread_b_path = " | ".join(
                    str(b.get("topic_path") or b.get("topic_path_key") or "").strip()
                    for b in threads_b[:3]
                    if (b.get("topic_path") or b.get("topic_path_key"))
                )

                sampled_a = _sample_items_across_threads(
                    max_items=max_items, thread_buckets=threads_a
                )
                sampled_b = _sample_items_across_threads(
                    max_items=max_items, thread_buckets=threads_b
                )

                if not sampled_a or not sampled_b:
                    continue

                # NB: Do not increment current_call here. Phase 3 progress should count
                # only actual LLM calls (A -> B and B -> A), which are logged below.
                logger.info(f"Phase 3 Pair: ({grade_label}: {subject_a} × {subject_b})")

                items_a = [
                    {
                        "sfi_uuid": it["sfi_uuid"],
                        "statement_code": it.get("statement_code"),
                        "description": it.get("description"),
                        "notes": it.get("notes"),
                        "page_index": it.get("page_index"),
                    }
                    for it in sampled_a
                ]
                items_b = [
                    {
                        "sfi_uuid": it["sfi_uuid"],
                        "statement_code": it.get("statement_code"),
                        "description": it.get("description"),
                        "notes": it.get("notes"),
                        "page_index": it.get("page_index"),
                    }
                    for it in sampled_b
                ]

                allowed_a = {str(it["sfi_uuid"]) for it in items_a}
                allowed_b = {str(it["sfi_uuid"]) for it in items_b}

                # Bidirectional confirmation: run A ×B and B×A, then keep only edges
                # that appear in both runs (canonicalized by UUID order).
                prompt_ab = within_grade_relates_to(
                    grade_label=str(grade_label),
                    items_a=items_a,
                    items_b=items_b,
                    max_edges_per_sfi=max_edges_per_sfi,
                    subject_label=f"{subject_a} × {subject_b}",
                    thread_a_key=f"subject:{subject_a}",
                    thread_b_key=f"subject:{subject_b}",
                    thread_a_path=thread_a_path or subject_a,
                    thread_b_path=thread_b_path or subject_b,
                )

                current_call += 1
                logger.info(
                    f"Phase 3 Progress: {current_call}/{total_calls} "
                    f"({grade_label}: {subject_a} × {subject_b} | relatesTo | A -> B)"
                )

                resp_ab = infer_progression_edges(
                    always_double_check_first_attempt=config.always_double_check_first_attempt,
                    instructions=prompt_ab.system_message,
                    model=config.model,
                    user_message=prompt_ab.user_message,
                    validator=partial(
                        validate_within_grade_relates_to,
                        allowed_uuids_a=allowed_a,
                        allowed_uuids_b=allowed_b,
                    ),
                )

                prompt_ba = within_grade_relates_to(
                    grade_label=str(grade_label),
                    items_a=items_b,
                    items_b=items_a,
                    max_edges_per_sfi=max_edges_per_sfi,
                    subject_label=f"{subject_a} × {subject_b}",
                    thread_a_key=f"subject:{subject_b}",
                    thread_b_key=f"subject:{subject_a}",
                    thread_a_path=thread_b_path or subject_b,
                    thread_b_path=thread_a_path or subject_a,
                )

                current_call += 1
                logger.info(
                    f"Phase 3 Progress: {current_call}/{total_calls} "
                    f"({grade_label}: {subject_a} × {subject_b} | relatesTo | B -> A)"
                )

                resp_ba = infer_progression_edges(
                    always_double_check_first_attempt=config.always_double_check_first_attempt,
                    instructions=prompt_ba.system_message,
                    model=config.model,
                    user_message=prompt_ba.user_message,
                    validator=partial(
                        validate_within_grade_relates_to,
                        allowed_uuids_a=allowed_b,
                        allowed_uuids_b=allowed_a,
                    ),
                )

                m_ab = _best_map(resp_ab)
                m_ba = _best_map(resp_ba)
                common_pairs = sorted(
                    (set(m_ab.keys()) & set(m_ba.keys())),
                    key=lambda p: (p[0].int, p[1].int),
                )

                for u_a, u_b in common_pairs:
                    conf_ab, rat_ab = m_ab[(u_a, u_b)]
                    conf_ba, rat_ba = m_ba[(u_a, u_b)]
                    conf = min(conf_ab, conf_ba)

                    ce = CandidateEdge(
                        confidence=float(conf),
                        evidence={
                            "rationale_ab": rat_ab,
                            "rationale_ba": rat_ba,
                            "confidence_ab": float(conf_ab),
                            "confidence_ba": float(conf_ba),
                            "bidirectional_confirmed": True,
                        },
                        inference_source="llm",
                        inference_type="within_grade_cross_subject_relates_to",
                        llm_confidence=float(conf),
                        metadata={
                            "phase": 3,
                            "grade_label": grade_label,
                            "subject_a": subject_a,
                            "subject_b": subject_b,
                            "bidirectional_confirmed": True,
                        },
                        rel_type="relatesTo",
                        source_sfi_uuid=u_a,
                        target_sfi_uuid=u_b,
                    )
                    candidates.append(ce)
                    provenance_rows.append(
                        {
                            "phase": 3,
                            "bidirectional_confirmed": True,
                            "confidence": ce.confidence,
                            "confidence_ab": float(conf_ab),
                            "confidence_ba": float(conf_ba),
                            "grade_label": grade_label,
                            "rel_type": "relatesTo",
                            "source": str(ce.source_sfi_uuid),
                            "target": str(ce.target_sfi_uuid),
                            "subject_a": subject_a,
                            "subject_b": subject_b,
                            "rationale_ab": rat_ab,
                            "rationale_ba": rat_ba,
                        }
                    )

    return candidates, provenance_rows


def _is_single_grade_bucket(b: dict[str, Any]) -> bool:
    """Determine if a bucket corresponds to a single grade level based on its grade
    ordinal.

    Parameters
    ----------
    b
        A bucket dictionary that may contain grade ordinal information.

    Returns
    -------
    bool
        True if the bucket corresponds to a single grade level (i.e., low and high
        ordinals are both integers and equal), False otherwise.
    """

    lo, hi = _level_bounds(b)
    return isinstance(lo, int) and isinstance(hi, int) and lo == hi


def _level_bounds(b: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    """Return (low, high) ordinals for a bucket when available.

    Parameters
    ----------
    b
        A bucket dictionary that may contain "grade_ordinal_low", "grade_ordinal_high",
        or "grade_ordinal" keys representing the grade level information for the
        standards contained in the bucket.

    Returns
    -------
    tuple[Optional[int], Optional[int]]
        A tuple containing the low and high grade ordinals for the bucket. If both
        "grade_ordinal_low" and "grade_ordinal_high" are present and valid integers,
        those values are returned. If only "grade_ordinal" is present and valid, it is
        returned as both the low and high ordinal. If neither is available or valid,
        (None, None) is returned, indicating that the grade level information is not
        available for this bucket.
    """

    lo = b.get("grade_ordinal_low")
    hi = b.get("grade_ordinal_high")

    if isinstance(lo, int) and isinstance(hi, int):
        return lo, hi

    ord_ = b.get("grade_ordinal")

    if isinstance(ord_, int):
        return int(ord_), int(ord_)

    return None, None


def _level_label(b: dict[str, Any]) -> str:
    """Human-readable label for grade or banded stage buckets.

    Parameters
    ----------
    b
        A bucket dictionary that may contain grade level information, including
        "grade_ordinal_low", "grade_ordinal_high", "grade_level", and
        "topic_path_parts" keys.

    Returns
    -------
    str
        A human-readable label for the grade or banded stage represented by the bucket.
    """

    lo, hi = _level_bounds(b)

    if isinstance(lo, int) and isinstance(hi, int) and hi != lo:
        parts = b.get("topic_path_parts") or []

        if isinstance(parts, list):
            stage = next(
                (
                    str(p.get("label") or "")
                    for p in parts
                    if p.get("role") == "stage" and p.get("label")
                ),
                "",
            ).strip()

            if stage:
                return stage

        return f"GRADES {lo}–{hi}"

    return str(
        b.get("grade_level")
        or (f"GRADE {lo}" if isinstance(lo, int) else "UNSPECIFIED_GRADE")
    )


def _levels_adjacent(lower: dict[str, Any], upper: dict[str, Any]) -> bool:
    """Determine if the grade levels of two buckets are adjacent based on their
    ordinals.

    Parameters
    ----------
    lower
        A bucket dictionary representing the lower grade level, which may contain grade
        ordinal information.
    upper
        A bucket dictionary representing the upper grade level, which may contain grade
        ordinal information.

    Returns
    -------
    bool
        True if the grade levels of the two buckets are adjacent (i.e., the high
        ordinal of the lower bucket is exactly one less than the low ordinal of the
        upper bucket), False otherwise. If the necessary ordinal information is not
        available or valid in either bucket, the function returns False, indicating
        that adjacency cannot be determined.
    """

    lo_lo, lo_hi = _level_bounds(lower)
    hi_lo, hi_hi = _level_bounds(upper)

    if not isinstance(lo_lo, int) or not isinstance(lo_hi, int):
        return False

    if not isinstance(hi_lo, int) or not isinstance(hi_hi, int):
        return False

    return lo_hi + 1 == hi_lo


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
        The maximum number of relatesTo edges to allow per SFI (undirected cap). Must
        be >= 1. To disable relatesTo inference, use the phase toggles
        (progressions_within_grade_relates_to/progressions_cross_grade_relates_to).

    Returns
    -------
    tuple[list[CandidateEdge], list[CandidateEdge]]
        A tuple containing two lists of CandidateEdge instances: the first list
        includes the edges that are kept after applying the limit, and the second list
        includes the edges that are dropped due to exceeding the maximum allowed edges
        per SFI.
    """

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
) -> dict[str, dict[tuple[int, int], dict[str, Any]]]:
    """Group and sample items by subject and level range for Phase 4.

    Instead of keying by a single grade ordinal, we key by (low, high) so stage-banded
    buckets (e.g., III–VI) remain truthful in cross-level inference.

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
    dict[str, dict[tuple[int, int], dict[str, Any]]]
        A nested dictionary structured as follows:
        {
            subject_label: {
                (level_low, level_high): {
                    "grade_label": str,
                    "level_label": str,
                    "level_low": int,
                    "level_high": int,
                    "items": list[dict[str, Any]],  # Sampled items for this subject and level range
                },
                ...
    """

    subject_level_samples: dict[str, dict[tuple[int, int], dict[str, Any]]] = (
        defaultdict(dict)
    )

    for grade_label, grade_buckets in by_grade.items():
        buckets_by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
        bounds: list[tuple[int, int]] = []
        exemplar_bucket: Optional[dict[str, Any]] = None

        for b in grade_buckets:
            lo, hi = _level_bounds(b)

            if isinstance(lo, int) and isinstance(hi, int):
                bounds.append((lo, hi))
                exemplar_bucket = exemplar_bucket or b

            buckets_by_subject[
                str(b.get("subject_label") or "UNSPECIFIED_SUBJECT")
            ].append(b)

        if not bounds:
            continue

        level_low = min(lo for lo, _ in bounds)
        level_high = max(hi for _, hi in bounds)
        level_key = (level_low, level_high)

        if any((lo, hi) != level_key for lo, hi in bounds):
            logger.warning(
                f"Inconsistent grade bounds within '{grade_label}'. Using aggregated {level_key}."
            )

        level_label = _level_label(
            exemplar_bucket
            or {
                "grade_level": grade_label,
                "grade_ordinal_low": level_low,
                "grade_ordinal_high": level_high,
            }
        )

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
            subject_level_samples[subject_label][level_key] = {
                "grade_label": grade_label,
                "level_label": level_label,
                "level_low": level_low,
                "level_high": level_high,
                "items": prompt_items,
            }

    return subject_level_samples


def _process_and_filter_candidates(
    *,
    candidates: list[CandidateEdge],
    config: CreateKGConfig,
    sfi_index: Optional[dict[str, dict[str, Any]]] = None,
) -> tuple[list[Relationship], list[Relationship], dict[str, int]]:
    """Process candidates: dedupe, filter by confidence, limit, and convert.

    Parameters
    ----------
    candidates
        The complete list of raw candidate edges from all inference phases.
    config
        The knowledge graph run configuration.
    sfi_index
        An optional index mapping SFI UUIDs to their corresponding data, which can be
        used to enrich the metadata of the final relationships if needed. The structure
        is expected to be {sfi_uuid: {"description": str, "statement_code": str, ...}}.

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

        if sfi_index:
            metadata["source_sfi_context"] = sfi_index.get(str(e.source_sfi_uuid))
            metadata["target_sfi_context"] = sfi_index.get(str(e.target_sfi_uuid))

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

        if sfi_index:
            metadata["source_sfi_context"] = sfi_index.get(str(e.source_sfi_uuid))
            metadata["target_sfi_context"] = sfi_index.get(str(e.target_sfi_uuid))

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


def _process_single_standard(
    *,
    buckets: DefaultDict[str, DefaultDict[str, dict[str, Any]]],
    drops: dict[str, list[dict[str, Any]]],
    include_provenance: bool,
    sfi: Any,
    strict_single_grade: bool,
) -> None:
    """Process a single standard item and sort it into buckets or drops.

    Parameters
    ----------
    buckets
        A nested dictionary for organizing standards into buckets based on grade and
        topic path.
    drops
        A dictionary for collecting standards that are dropped due to validation issues,
        categorized by the reason for dropping.
    include_provenance
        Whether to include provenance information (e.g., page index) in the payload for
        LLM inference.
    sfi
        The standard item to process, which is expected to have attributes such as
        description, normalized_statement_type, grade_level, and metadata containing
        progression context.
    strict_single_grade
        Whether to enforce that each standard item must be associated with exactly one
        grade level. If True, items with multiple grade levels will be dropped; if
        False, items with multiple grade levels will be processed but may be
        categorized under a special "UNSPECIFIED_GRADE" label if their grade levels
        cannot be clearly determined.
    """

    metadata = sfi.metadata or {}
    progression_context = metadata.get("progression_context") or {}
    sfi_uuid = str(sfi.case_identifier_uuid or sfi.identifier)

    # 1. Validation: We only want endpoints for buildsTowards (Standard).
    if sfi.normalized_statement_type != "Standard":
        drops["non_standard_item"].append(
            {
                "description": sfi.description,
                "normalized_statement_type": sfi.normalized_statement_type,
                "sfi_uuid": sfi_uuid,
                "statement_type": sfi.statement_type,
            }
        )
        return

    # 2. Validation: grade placement.
    grade_label, grade_ord = _grade_label_and_ordinal(sfi)

    if grade_label == "UNSPECIFIED_GRADE":
        drops["unassigned_grade"].append(
            {"description": sfi.description, "sfi_uuid": sfi_uuid}
        )

        if strict_single_grade:
            return

    grade_level = sfi.grade_level or []

    if strict_single_grade and len(grade_level) != 1:
        drops["multi_grade_item"].append(
            {
                "description": sfi.description,
                "grade_level": grade_level,
                "sfi_uuid": sfi_uuid,
            }
        )
        return

    # 3. Validation: topic path key.
    topic_path_key = progression_context.get("topic_path_key") or ""

    if not isinstance(topic_path_key, str) or not topic_path_key.strip():
        drops["missing_topic_path_key"].append(
            {
                "description": sfi.description,
                "grade": grade_label,
                "sfi_uuid": sfi_uuid,
            }
        )
        return

    # 4. Bucket management: get or create bucket.
    topic_path_parts = progression_context.get("topic_path_parts") or []

    if not isinstance(topic_path_parts, list):
        topic_path_parts = []

    b = buckets[grade_label].get(topic_path_key)

    if not b:
        # Prefer explicit subject over broader learning_area when both exist.
        subject_label = next(
            (
                str(p.get("label") or "")
                for p in topic_path_parts
                if p.get("role") == "subject" and p.get("label")
            ),
            None,
        ) or next(
            (
                str(p.get("label") or "")
                for p in topic_path_parts
                if p.get("role") == "learning_area" and p.get("label")
            ),
            "UNSPECIFIED_SUBJECT",
        )
        b = {
            "bucket_key": f"{grade_label}::{topic_path_key}",
            "grade_level": grade_label,
            "grade_ordinal": grade_ord,
            "grade_ordinal_low": (
                progression_context.get("grade_ordinal_low")
                if isinstance(progression_context.get("grade_ordinal_low"), int)
                else grade_ord
            ),
            "grade_ordinal_high": (
                progression_context.get("grade_ordinal_high")
                if isinstance(progression_context.get("grade_ordinal_high"), int)
                else (
                    progression_context.get("grade_ordinal_low")
                    if isinstance(progression_context.get("grade_ordinal_low"), int)
                    else grade_ord
                )
            ),
            "subject_label": subject_label,
            "thread_key": progression_context.get("thread_key"),
            "topic_path_key": topic_path_key,
            "topic_path": _path_string(topic_path_parts),
            "topic_path_parts": topic_path_parts,
            "items": [],
        }
        buckets[grade_label][topic_path_key] = b

    # 5. Build payload.
    payload = {
        "description": sfi.description,
        "notes": sfi.notes,
        "order_index_within_parent": progression_context.get(
            "order_index_within_parent"
        ),
        "code_tuple": progression_context.get("code_tuple"),
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


def _resolve_forbidden_pairs(
    *,
    forbidden_builds_pairs: set[tuple[UUID, UUID]],
    lower_items: list[dict[str, Any]],
    upper_items: list[dict[str, Any]],
) -> tuple[set[tuple[str, str]], list[dict[str, Any]]]:
    """Calculate forbidden pairs for the current level iteration.

    Parameters
    ----------
    forbidden_builds_pairs
        A set of tuples representing pairs of SFI UUIDs that are forbidden from being
        connected by a buildsTowards relationship, based on prior iterations of the
        inference process. Each tuple contains two UUIDs corresponding to Standards
        Framework Items (SFIs) that should not be connected in the current inference
        step.
    lower_items
        A list of dictionaries representing the Standards Framework Items (SFIs) in the
        lower grade level bucket for the current iteration. Each dictionary contains
        information about an SFI, including its UUID and other relevant fields.
    upper_items
        A list of dictionaries representing the Standards Framework Items (SFIs) in the
        upper grade level bucket for the current iteration. Each dictionary contains
        information about an SFI, including its UUID and other relevant fields.

    Returns
    -------
    tuple[set[tuple[str, str]], list[dict[str, Any]]]
        A tuple containing:
        1. A set of tuples, where each tuple consists of two strings representing the
           UUIDs of SFIs that are forbidden from being connected by a buildsTowards
           relationship in the current inference step. The UUIDs in each tuple are
           ordered lexicographically to ensure consistency.
        2. A list of dictionaries, where each dictionary represents a forbidden pair of
           SFIs with keys "a_sfi_uuid" and "b_sfi_uuid" corresponding to the UUIDs of
           the two SFIs in the pair. This list is sorted by the UUID pairs for stable
           output and can be used for reporting or debugging purposes.
    """

    allowed_lo = {str(it["sfi_uuid"]) for it in lower_items}
    allowed_hi = {str(it["sfi_uuid"]) for it in upper_items}
    forbidden_pairs_set: set[tuple[str, str]] = set()

    for s, t in forbidden_builds_pairs:
        ss, tt = str(s), str(t)

        # Record forbidden pairs as *undirected* canonicalized UUID string tuples.
        # Canonicalization is by string sort to match validator behavior.
        if (ss in allowed_lo and tt in allowed_hi) or (
            ss in allowed_hi and tt in allowed_lo
        ):
            a, b = sorted([ss, tt])
            forbidden_pairs_set.add((a, b))

    forbidden_pairs_list = [
        {"a_sfi_uuid": a, "b_sfi_uuid": b} for a, b in sorted(forbidden_pairs_set)
    ]

    return forbidden_pairs_set, forbidden_pairs_list


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
        A flat list of sampled StandardsFrameworkItem dictionaries drawn across the
        provided thread buckets. Each returned item preserves SFI fields and may
        include helper keys like "_thread_key" and "_thread_path".
    """

    # Thread buckets should already be stable-sorted by caller.
    per_thread = [(b["topic_path_key"], list(b["items"])) for b in thread_buckets]
    path_by_key = {b["topic_path_key"]: b.get("topic_path", "") for b in thread_buckets}

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
                sampled_item["_thread_path"] = str(path_by_key.get(tkey, ""))
                sampled.append(sampled_item)
                idxs[tkey] = i + 1
                progressed = True

                if len(sampled) >= max_items:
                    break

        if not progressed:
            break

    return sampled


def _sort_key_for_bucket_sfi(
    s: dict[str, Any],
) -> tuple[int, int, tuple[int, ...], str, str]:
    """Stable ordering inside a bucket. Prefer explicit order_index; fall back to
    numeric code tuple (if available), then statement_code, then uuid.

    Parameters
    ----------
    s
        The StandardsFrameworkItem dictionary to generate a sort key for.

    Returns
    -------
    tuple[int, int, tuple[int, ...], str, str]
        A tuple representing the sort key for the given StandardsFrameworkItem,
        structured as follows:
        1. An integer representing the order index within the parent context. If the
           order index is not available or not an integer, a large default value (10^9)
           is used to ensure it sorts last.
        2. An integer indicating whether the code tuple is missing (1) or present (0).
        3. A tuple of integers representing the code tuple extracted from the item. If
           the code tuple is not available or not valid, a default tuple (10^9,) is
           used to ensure it sorts last.
        4. A string representing the statement code, stripped of leading and trailing
           whitespace.
        5. A string representing the SFI UUID or case identifier UUID, used as a
           tiebreaker in sorting.
    """

    order_index = s.get("order_index_within_parent")
    order_index = order_index if isinstance(order_index, int) else 10**9
    code = (s.get("statement_code") or "").strip()

    # Prefer numeric tuple ordering over lexicographic string ordering.
    ct = s.get("code_tuple")
    code_tuple: tuple[int, ...] | None = None

    if isinstance(ct, list) and ct and all(isinstance(x, int) for x in ct):
        code_tuple = tuple(ct)
    elif isinstance(ct, list) and ct and all(isinstance(x, str) for x in ct):
        # Defensive: if upstream ever stores numeric segments as strings.
        try:
            code_tuple = tuple(int(x) for x in ct)
        except Exception:  # pylint: disable=broad-except
            code_tuple = None

    if code_tuple is None and code:
        # Fallback: parse any digits from statement_code (e.g., "3.9.4.1" -> (3,9,4,1)).
        nums = [int(x) for x in re.findall(r"\d+", code)]
        code_tuple = tuple(nums) if nums else None

    missing_code_tuple = 1 if code_tuple is None else 0
    code_tuple_key = code_tuple if code_tuple is not None else (10**9,)
    uuid_key = s.get("sfi_uuid") or s.get("case_identifier_uuid") or ""

    return order_index, missing_code_tuple, code_tuple_key, code, uuid_key


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
    sfi_index = _build_sfi_index(by_grade=by_grade)

    # Phase 1: Within-grade buildsTowards.
    p1_candidates, p1_prov = _infer_within_grade_builds_towards(
        by_grade=by_grade, config=config
    )
    candidates.extend(p1_candidates)
    provenance_rows.extend(p1_prov)

    # Phase 2: Cross-grade buildsTowards.
    p2_candidates, p2_prov, cross_level_build_pairs = _infer_cross_grade_builds_towards(
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
        by_grade=by_grade, config=config, forbidden_builds_pairs=cross_level_build_pairs
    )
    candidates.extend(p4_candidates)
    provenance_rows.extend(p4_prov)

    # Dedupe, filter, limit, and emit final relationships, and gather stats for the report.
    builds_rels, relates_rels, stats = _process_and_filter_candidates(
        candidates=candidates, config=config, sfi_index=sfi_index
    )

    # Write artifacts.
    write_to_json(
        fp=kg_dirs.learning_progressions
        / "learning_progressions_builds_towards_relationships.json",
        json_info=[r.model_dump(mode="json") for r in builds_rels],
    )
    write_to_json(
        fp=kg_dirs.learning_progressions
        / "learning_progressions_relates_to_relationships.json",
        json_info=[r.model_dump(mode="json") for r in relates_rels],
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
        _process_single_standard(
            buckets=buckets,
            drops=drops,
            include_provenance=include_provenance,
            sfi=sfi,
            strict_single_grade=strict_single_grade,
        )

    return _format_learning_progressions_dict(buckets=buckets, drops=drops)
