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
from typing import Any, Callable, DefaultDict, Optional
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
from skg.kgs.utils import ExportContext, KGDirs, canon_str_pair, keyify
from skg.kgs.validators import (
    validate_cross_grade_builds_towards,
    validate_cross_grade_relates_to,
    validate_within_grade_builds_towards,
    validate_within_grade_relates_to,
)
from skg.schemas import CreateKGConfig
from skg.utils.general import write_to_json


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
) -> dict[tuple[str, str], tuple[float, str]]:
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
    dict[tuple[str, str], tuple[float, str]]
        A dictionary mapping canonicalized UUID-string pairs to their best confidence
        score and rationale, regardless of edge direction. Canonicalization uses
        `canon_str_pair` (lexicographic ordering)--the single source of truth for
        undirected pair ordering throughout the pipeline.
    """

    best: dict[tuple[str, str], tuple[float, str]] = {}

    for ee in resp.edges:
        a, b = canon_str_pair(ee.source_sfi_uuid, ee.target_sfi_uuid)
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
        The exported Academic Standards KG artifacts, containing the framework and
        items to include as nodes in the graph.
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
    doc_key: str,
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
    doc_key
        The document key for this export, included in the ID namespace string for
        consistency with the rest of the pipeline.
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

    rid = uuid5(
        config.namespace_uuid,
        f"lc:curriculum:{doc_key}:rel:{rel_type}:{source}:{target}",
    )

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


def _build_item_payload(
    *,
    include_order_index: bool = False,
    item: dict[str, Any],
    thread_key_field: Optional[str] = None,
) -> dict[str, Any]:
    """Build a compact item payload for the LLM prompt from a bucket item.

    This is the single source of truth for which fields are sent to the LLM for each
    StandardsFrameworkItem in the learning progressions inference prompts.

    Parameters
    ----------
    item
        A bucket item dictionary containing SFI fields.
    include_order_index
        Whether to include `order_index_within_parent` in the payload. Used by
        buildsTowards phases where sequence ordering matters.
    thread_key_field
        If provided, the item key to read for an additional `thread_key` field in the
        payload (e.g., `"_thread_key"`). Used by Phase 4 cross-grade relatesTo.

    Returns
    -------
    dict[str, Any]
        A dictionary containing the SFI fields to include in the LLM prompt payload.
    """

    payload: dict[str, Any] = {
        "sfi_uuid": item["sfi_uuid"],
        "statement_code": item.get("statement_code"),
        "description": item.get("description"),
        "notes": item.get("notes"),
        "page_index": item.get("page_index"),
    }

    if include_order_index:
        payload["order_index_within_parent"] = item.get("order_index_within_parent")

    if thread_key_field:
        payload["thread_key"] = item.get(thread_key_field)

    return payload


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
        If any bucket is missing a valid thread key, which is required for grouping and
        inference. The error message includes examples of bucket keys that are missing
        thread keys to aid in debugging the Academic Standards export data.
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

    # Check the error condition first (missing thread_key) since it is fatal and should
    # not be masked by the non-fatal warning about skipped bounds.
    if missing_thread_key > 0:
        # The check is deferred until after full iteration so we can collect all
        # examples for a more actionable error message. Clear the partial thread_map
        # before raising so callers that catch the exception cannot accidentally use
        # incomplete data.
        thread_map.clear()
        raise ValueError(
            f"Missing progression_context.thread_key in "
            f"{missing_thread_key} bucket(s). Re-export Academic Standards so each "
            f"SFI has metadata.progression_context.thread_key. "
            f"Examples: {missing_thread_key_examples}"
        )

    if skipped_no_bounds > 0:
        logger.warning(
            f"Skipped {skipped_no_bounds} buckets without integer grade bounds "
            f"(missing grade/stage ordinal data)."
        )

    return thread_map


def _canon_disposition_key(
    *, rel_type: str, source: str, target: str
) -> tuple[str, str, str]:
    """Build a canonicalized disposition-map key.

    For directed relationship types (buildsTowards) the key preserves the original
    (source, target) order. For undirected types (relatesTo) the two UUID strings are
    canonicalized via `canon_str_pair` so that keys match regardless of which direction
    the edge was originally emitted in.

    Parameters
    ----------
    rel_type
        The relationship type string (e.g., `"buildsTowards"` or `"relatesTo"`).
    source
        The source SFI UUID as a string.
    target
        The target SFI UUID as a string.

    Returns
    -------
    tuple[str, str, str]
        A 3-tuple `(rel_type, a, b)` suitable for use as a dictionary key where
        `(a, b)` is canonicalized for undirected relationship types.
    """

    if rel_type == "relatesTo":
        a, b = canon_str_pair(source, target)
        return rel_type, a, b

    return rel_type, source, target


def _collect_builds_towards_work_items(
    *, config: CreateKGConfig, thread_map: dict[str, list[dict[str, Any]]]
) -> list[tuple[str, dict[str, Any], dict[str, Any], str, Callable[..., Any]]]:
    """Collect and configure eligible adjacent pairs of buckets for inference.

    Parameters
    ----------
    config
        The knowledge graph run configuration.
    thread_map
        Dictionary mapping thread keys to lists of bucket dictionaries.

    Returns
    -------
    list[tuple[str, dict[str, Any], dict[str, Any], str, Callable[..., Any]]]
        A list of work items, where each item contains:
            1. The thread key.
            2. The lower-level bucket.
            3. The upper-level bucket.
            4. The inference type string.
            5. The prompt building function.
    """

    work_items: list[tuple[str, dict[str, Any], dict[str, Any], str, Any]] = []

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

            work_items.append((thread_key, b_lo, b_hi, inference_type, prompt_builder))

    return work_items


def _collect_relates_to_work_items(
    *, config: CreateKGConfig, subject_level_samples: dict[str, Any]
) -> list[dict[str, Any]]:
    """Collect and configure eligible adjacent-level pairs for relatesTo inference.

    Parameters
    ----------
    config
        The knowledge graph run configuration.
    subject_level_samples
        Dictionary mapping subjects to grade-level boundaries and bucket data.

    Returns
    -------
    list[dict[str, Any]]
        A list of work item dictionaries, each containing the parameters needed for a
        bidirectional inference run.
    """

    work_items: list[dict[str, Any]] = []

    for subject_label, by_level in subject_level_samples.items():
        level_keys = sorted(by_level.keys(), key=lambda k: (k[0], k[1]))

        for k_lo, k_hi in zip(level_keys, level_keys[1:]):
            lo_low, lo_high = k_lo
            hi_low, hi_high = k_hi

            if lo_high + 1 != hi_low:
                continue

            lower = by_level[k_lo]
            upper = by_level[k_hi]

            if not lower.get("items") or not upper.get("items"):
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

            work_items.append(
                {
                    "hi_high": hi_high,
                    "hi_low": hi_low,
                    "inference_type": inference_type,
                    "lo_high": lo_high,
                    "lo_low": lo_low,
                    "lower": lower,
                    "prompt_builder": prompt_builder,
                    "subject_label": subject_label,
                    "upper": upper,
                }
            )

    return work_items


def _compute_lp_thread_key(
    topic_path_parts: list[dict[str, Any]], roles: list[str]
) -> str | None:
    """Compute an LP thread key from topic_path_parts filtered to the given roles.

    Only entries in `topic_path_parts` whose `role` is in `roles` contribute to the
    key. Segments are emitted in the order specified by `roles` (not the order they
    appear in `topic_path_parts`), which makes the key stable across curricula with
    differing hierarchy depth.

    Parameters
    ----------
    topic_path_parts
        The topic_path_parts from progression_context (list of dicts with
        "role" and "label" keys).
    roles
        Ordered list of roles to include in the thread key.

    Returns
    -------
    str | None
        The computed thread key (e.g., "strand=activites_numeriques"), or None
        if no matching roles are found in topic_path_parts.
    """

    parts_by_role: dict[str, list[str]] = {}

    for entry in topic_path_parts:
        r = entry.get("role", "")
        label = entry.get("label", "")

        if r and label and r in set(roles):
            parts_by_role.setdefault(r, []).append(keyify(label))

    segments: list[str] = []

    for role in roles:  # Iterate in user-specified order
        for val in parts_by_role.get(role, []):
            segments.append(f"{role}={val}")

    return "|".join(segments) if segments else None


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

    best: dict[tuple[str, str, str], CandidateEdge] = {}

    for e in edges:
        s, t = e.source_sfi_uuid, e.target_sfi_uuid

        if e.rel_type == "relatesTo":
            cs, _ = canon_str_pair(str(s), str(t))

            if cs != str(s):  # Canonical order differs from original; swap UUIDs
                s, t = t, s

        k = (e.rel_type, str(s), str(t))

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


def _emit_relationship(
    *,
    candidate: CandidateEdge,
    config: CreateKGConfig,
    doc_key: str,
    sfi_index: Optional[dict[str, dict[str, Any]]] = None,
) -> Relationship:
    """Convert a CandidateEdge to a Relationship, enriching metadata as needed.

    Parameters
    ----------
    candidate
        The CandidateEdge instance to convert into a Relationship. This edge is
        expected to have attributes such as confidence, evidence, inference_source,
        inference_type, rel_type, source_sfi_uuid, target_sfi_uuid, and metadata
        containing any additional information from the inference process.
    config
        The knowledge graph run configuration.
    doc_key
        The document key for this export, included in the relationship ID namespace
        string for consistency with the rest of the pipeline.
    sfi_index
        An optional index mapping SFI UUIDs to their corresponding data, which can be
        used to enrich the metadata of the final relationships if needed.

    Returns
    -------
    Relationship
        A Relationship instance constructed from the CandidateEdge, with enriched
        metadata that includes the original metadata from the edge as well as
        additional fields such as confidence, evidence, inference source/type, and
        optionally source/target SFI context if an sfi_index is provided.
    """

    metadata = dict(candidate.metadata)
    metadata.update(
        {
            "confidence": candidate.confidence,
            "evidence": candidate.evidence,
            "inference_source": candidate.inference_source,
            "inference_type": candidate.inference_type,
        }
    )

    if sfi_index:
        metadata["source_sfi_context"] = sfi_index.get(str(candidate.source_sfi_uuid))
        metadata["target_sfi_context"] = sfi_index.get(str(candidate.target_sfi_uuid))

    return _build_relationship(
        config=config,
        doc_key=doc_key,
        metadata=metadata,
        rel_type=candidate.rel_type,
        source=candidate.source_sfi_uuid,
        target=candidate.target_sfi_uuid,
    )


def _extract_code_tuple(
    *, code_string: str, raw_code_tuple: Any
) -> tuple[int, ...] | None:
    """Extract a numeric tuple from a raw code tuple or falls back to the code string.

    Evaluates the raw code tuple to parse integer sequences from strings, mixed lists,
    or pure integer lists. If no valid integers are found, attempts to parse digits
    from the fallback code string.

    Parameters
    ----------
    code_string
        The stripped statement code string used as a fallback for digit extraction.
    raw_code_tuple
        The raw 'code_tuple' value retrieved from the item dictionary.

    Returns
    -------
    tuple[int, ...] | None
        A tuple of extracted integers, or None if no numeric values could be parsed.
    """

    nums: list[int] = []

    # Process all list types (pure int, pure str, or mixed) in a single pass.
    if isinstance(raw_code_tuple, list):
        for item in raw_code_tuple:
            if isinstance(item, (int, float)):
                nums.append(int(item))
            elif isinstance(item, str):
                # re.findall(r"\d+") guarantees digits, making int() conversion safe.
                nums.extend(int(match) for match in re.findall(r"\d+", item))

    # Fallback to code_string if no digits were extracted from the tuple.
    if not nums and code_string:
        nums = [int(match) for match in re.findall(r"\d+", code_string)]

    return tuple(nums) if nums else None


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
            # Sort items by canon_order_path (document position) to preserve the
            # intended pedagogical sequence across all weeks/substages within a strand,
            # with _sort_key_for_bucket_sfi as a tiebreaker.
            b["items"] = sorted(
                b["items"],
                key=lambda s: (
                    s.get("canon_order_path") or [],
                    _sort_key_for_bucket_sfi(s),
                ),
            )

            grade_buckets.append(b)
            by_thread[tkey][grade_label] = b

        by_grade[grade_label] = sorted(
            grade_buckets,
            key=lambda x: (x.get("topic_path") or "", x["topic_path_key"]),
        )

    return {"by_grade": by_grade, "by_thread": dict(by_thread), "drops": drops}


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

    # Debug: count unthreaded sentinel buckets that will be excluded from cross-grade
    # matching because their grade-specific sentinels prevent cross-grade pairing.
    unthreaded_count = sum(1 for tk in thread_map if tk.startswith("__unthreaded__::"))

    if unthreaded_count > 0:
        logger.info(
            f"Cross-grade matching: {unthreaded_count} unthreaded thread(s) "
            f"excluded (grade-specific sentinel prevents cross-grade pairing)"
        )

    work_items = _collect_builds_towards_work_items(
        config=config, thread_map=thread_map
    )

    total_calls = len(work_items)
    cross_grade_calls = sum(
        1 for _, _, _, it, _ in work_items if it == "cross_grade_builds_towards"
    )
    cross_stage_calls = total_calls - cross_grade_calls

    if config.progressions_cross_grade_builds_towards:
        logger.info(
            f"{cross_grade_calls} adjacent single-grade pairs for cross-grade buildsTowards inference."
        )
    if config.progressions_cross_stage_builds_towards:
        logger.info(
            f"{cross_stage_calls} adjacent level pairs for cross-stage buildsTowards inference."
        )

    for current_call, (
        thread_key,
        b_lo,
        b_hi,
        inference_type,
        prompt_builder,
    ) in enumerate(work_items, 1):
        logger.info(
            f"Phase 2 Progress: {current_call}/{total_calls} "
            f"({_level_label(b_lo)} -> {_level_label(b_hi)} | {thread_key} | {inference_type})"
        )

        item_candidates, item_provenance, item_pairs = (
            _process_builds_towards_work_item(
                b_hi=b_hi,
                b_lo=b_lo,
                config=config,
                inference_type=inference_type,
                prompt_builder=prompt_builder,
                thread_key=thread_key,
            )
        )

        candidates.extend(item_candidates)
        provenance_rows.extend(item_provenance)
        cross_level_build_pairs.update(item_pairs)

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
        by_grade=by_grade,
        excluded_subject_labels=set(config.progressions_excluded_subject_labels or []),
        max_items=max_items,
    )

    work_items = _collect_relates_to_work_items(
        config=config, subject_level_samples=subject_level_samples
    )

    cross_grade_calls = sum(
        1 for w in work_items if w["inference_type"] == "cross_grade_relates_to"
    )
    cross_stage_calls = len(work_items) - cross_grade_calls
    total_calls = len(work_items) * 2

    if config.progressions_cross_grade_relates_to:
        logger.info(
            f"{cross_grade_calls} adjacent single-grade pairs for cross-grade relatesTo inference."
        )

    if config.progressions_cross_stage_relates_to:
        logger.info(
            f"{cross_stage_calls} adjacent level pairs for cross-stage relatesTo inference."
        )

    current_call_base = 0

    for wi in work_items:
        item_candidates, item_provenance = _process_relates_to_work_item(
            config=config,
            current_call_base=current_call_base,
            forbidden_builds_pairs=forbidden_builds_pairs,
            hi_high=wi["hi_high"],
            hi_low=wi["hi_low"],
            inference_type=wi["inference_type"],
            lo_high=wi["lo_high"],
            lo_low=wi["lo_low"],
            lower=wi["lower"],
            max_edges_per_sfi=max_edges_per_sfi,
            prompt_builder=wi["prompt_builder"],
            subject_label=wi["subject_label"],
            total_calls=total_calls,
            upper=wi["upper"],
        )

        candidates.extend(item_candidates)
        provenance_rows.extend(item_provenance)
        current_call_base += 2

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

    # Collect eligible buckets so total_calls is exact.
    eligible: list[tuple[str, dict[str, Any]]] = [
        (grade_label, bucket)
        for grade_label, grade_buckets in by_grade.items()
        for bucket in grade_buckets
        if _allow_within_grade_inference(bucket=bucket, config=config)
        and len(bucket.get("items") or []) >= 2
    ]
    total_calls = len(eligible)
    logger.info(
        f"{total_calls} buckets with 2+ items for within-grade buildsTowards inference."
    )

    for current_call, (grade_label, bucket) in enumerate(eligible, 1):
        items = bucket.get("items") or []

        logger.info(
            f"Phase 1 Progress: {current_call}/{total_calls} "
            f"({grade_label} - {bucket.get('topic_path_key')})"
        )

        ordered_items = [
            _build_item_payload(include_order_index=True, item=item) for item in items
        ]

        prompt = within_grade_builds_towards(
            grade_label=str(grade_label),
            items=ordered_items,
            min_confidence=config.progressions_builds_towards_min_confidence,
            thread_path=str(bucket.get("topic_path") or bucket.get("topic_path_key")),
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
                _make_provenance_row(
                    candidate=candidate_edge,
                    inference_type="within_grade_builds_towards",
                    phase=1,
                    rationale=edge.rationale,
                    bucket_key=bucket.get("bucket_key"),
                )
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

    work_items: list[dict[str, Any]] = []
    excluded = set(config.progressions_excluded_subject_labels or []) | {
        "UNKNOWN",
        "",
    }
    phase3_excluded_count = 0

    for grade_label, by_subject in grade_subject_threads.items():
        subject_keys = [s for s in sorted(by_subject.keys()) if s not in excluded]
        phase3_excluded_count += sum(1 for s in by_subject if s in excluded)

        if len(subject_keys) < 2:
            continue

        for i, subject_a in enumerate(subject_keys):
            for subject_b in subject_keys[i + 1 :]:
                threads_a = sorted(by_subject[subject_a], key=_thread_sort_key)
                threads_b = sorted(by_subject[subject_b], key=_thread_sort_key)

                sampled_a = _sample_items_across_threads(
                    max_items=max_items, thread_buckets=threads_a
                )
                sampled_b = _sample_items_across_threads(
                    max_items=max_items, thread_buckets=threads_b
                )

                if not sampled_a or not sampled_b:
                    continue

                work_items.append(
                    {
                        "grade_label": grade_label,
                        "subject_a": subject_a,
                        "subject_b": subject_b,
                        "sampled_a": sampled_a,
                        "sampled_b": sampled_b,
                        "thread_a_path": " | ".join(
                            str(
                                b.get("topic_path") or b.get("topic_path_key") or ""
                            ).strip()
                            for b in threads_a[:3]
                            if (b.get("topic_path") or b.get("topic_path_key"))
                        ),
                        "thread_b_path": " | ".join(
                            str(
                                b.get("topic_path") or b.get("topic_path_key") or ""
                            ).strip()
                            for b in threads_b[:3]
                            if (b.get("topic_path") or b.get("topic_path_key"))
                        ),
                    }
                )

    total_pairs = len(work_items)
    total_calls = total_pairs * 2  # Bidirectional confirmation

    if phase3_excluded_count > 0:
        logger.info(
            f"Phase 3: excluded {phase3_excluded_count} subject bucket(s) with "
            f"subject_label in {sorted(excluded)}"
        )

    logger.info(
        f"{total_pairs} within-grade cross-subject pairs for relatesTo inference "
        f"(bidirectional confirmation => {total_calls} LLM calls)."
    )

    current_call = 0

    for wi in work_items:
        grade_label = wi["grade_label"]
        subject_a, subject_b = wi["subject_a"], wi["subject_b"]
        sampled_a, sampled_b = wi["sampled_a"], wi["sampled_b"]
        thread_a_path, thread_b_path = wi["thread_a_path"], wi["thread_b_path"]

        logger.info(f"Phase 3 Pair: ({grade_label}: {subject_a} × {subject_b})")

        items_a, items_b = [_build_item_payload(item=it) for it in sampled_a], [
            _build_item_payload(item=it) for it in sampled_b
        ]

        allowed_a, allowed_b = {str(it["sfi_uuid"]) for it in items_a}, {
            str(it["sfi_uuid"]) for it in items_b
        }

        # Bidirectional confirmation: run A x B and B x A, then keep only edges that
        # appear in both runs (canonicalized by UUID order).
        prompt_ab = within_grade_relates_to(
            grade_label=str(grade_label),
            items_a=items_a,
            items_b=items_b,
            max_edges_per_sfi=max_edges_per_sfi,
            min_confidence=config.progressions_relates_to_min_confidence,
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
            min_confidence=config.progressions_relates_to_min_confidence,
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

        m_ab, m_ba = _best_map(resp_ab), _best_map(resp_ba)
        common_pairs = sorted(set(m_ab.keys()) & set(m_ba.keys()))

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
                source_sfi_uuid=_uuid(u_a),
                target_sfi_uuid=_uuid(u_b),
            )
            candidates.append(ce)
            provenance_rows.append(
                _make_provenance_row(
                    candidate=ce,
                    inference_type="within_grade_cross_subject_relates_to",
                    phase=3,
                    bidirectional_confirmed=True,
                    confidence_fwd=float(conf_ab),
                    confidence_rev=float(conf_ba),
                    rationale_fwd=rat_ab,
                    rationale_rev=rat_ba,
                    grade_label=grade_label,
                    subject_a=subject_a,
                    subject_b=subject_b,
                )
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
        # Prefer stage_key stored directly on the bucket.
        stage_key = b.get("stage_key")

        if isinstance(stage_key, str) and stage_key.strip():
            return stage_key.strip()

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


def _make_provenance_row(
    *, candidate: CandidateEdge, inference_type: str, phase: int, **extra: Any
) -> dict[str, Any]:
    """Build a provenance row from a CandidateEdge plus phase-specific extras.

    This is the single source of truth for the common fields present in every
    provenance row. Phase-specific fields (e.g., rationale, grade labels, bidirectional
    confirmation flags) are passed as keyword arguments.

    Parameters
    ----------
    candidate
        The CandidateEdge that produced this row.
    inference_type
        The inference type string (e.g., `"within_grade_builds_towards"`).
    phase
        The numeric phase identifier (1–4).
    **extra
        Additional phase-specific fields to include in the row.

    Returns
    -------
    dict[str, Any]
        A provenance row dictionary ready for serialization.
    """

    row: dict[str, Any] = {
        "phase": phase,
        "inference_type": inference_type,
        "rel_type": candidate.rel_type,
        "source": str(candidate.source_sfi_uuid),
        "target": str(candidate.target_sfi_uuid),
        "confidence": candidate.confidence,
    }
    row.update(extra)
    return row


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
    *,
    by_grade: dict[str, list[dict[str, Any]]],
    excluded_subject_labels: set[str] | None = None,
    max_items: int,
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
    excluded_subject_labels
        Optional set of subject labels to skip during sampling. Buckets whose
        `subject_label` is in this set are excluded from the returned samples.
        Typically `{"UNSPECIFIED_SUBJECT"}` to avoid noise from unmapped items.
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

        excluded_count = 0

        for subject_label, thread_buckets in buckets_by_subject.items():
            if excluded_subject_labels and subject_label in excluded_subject_labels:
                excluded_count += 1
                continue

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
                _build_item_payload(item=it, thread_key_field="_thread_key")
                for it in sampled
            ]
            subject_level_samples[subject_label][level_key] = {
                "grade_label": grade_label,
                "level_label": level_label,
                "level_low": level_low,
                "level_high": level_high,
                "items": prompt_items,
            }

        if excluded_count > 0:
            logger.info(
                f"Phase 4 subject sampling: excluded {excluded_count} subject buckets "
                f"in grade '{grade_label}' with subject_label in "
                f"{sorted(excluded_subject_labels or [])}"
            )

    return subject_level_samples


def _process_and_filter_candidates(
    *,
    candidates: list[CandidateEdge],
    config: CreateKGConfig,
    doc_key: str,
    sfi_index: Optional[dict[str, dict[str, Any]]] = None,
) -> tuple[
    list[Relationship],
    list[Relationship],
    dict[str, int],
    dict[tuple[str, str, str], str],
]:
    """Process candidates: dedupe, filter by confidence, limit, and convert.

    Parameters
    ----------
    candidates
        The complete list of raw candidate edges from all inference phases.
    config
        The knowledge graph run configuration.
    doc_key
        The document key for this export, included in relationship ID namespace
        strings for consistency with the rest of the pipeline.
    sfi_index
        An optional index mapping SFI UUIDs to their corresponding data, which can be
        used to enrich the metadata of the final relationships if needed. The structure
        is expected to be {sfi_uuid: {"description": str, "statement_code": str, ...}}.

    Returns
    -------
    tuple
        A tuple containing:
        1. List of final buildsTowards relationships.
        2. List of final relatesTo relationships.
        3. A dictionary of counts/statistics for the report.
        4. A disposition map keyed by (rel_type, source_uuid, target_uuid) ->
            disposition string (kept, dropped_low_conf, dropped_cap, dropped_dedupe).
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

    builds_relationships: list[Relationship] = [
        _emit_relationship(
            candidate=e, config=config, doc_key=doc_key, sfi_index=sfi_index
        )
        for e in builds_kept
    ]

    relates_relationships: list[Relationship] = [
        _emit_relationship(
            candidate=e, config=config, doc_key=doc_key, sfi_index=sfi_index
        )
        for e in relates_kept
    ]

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

    # Build disposition map keyed by (rel_type, source_uuid, target_uuid) for enriching
    # provenance rows downstream.
    #
    # NB: relatesTo edges are canonicalized during dedup (source < target by UUID.int),
    # so keys here are already canonical for relatesTo. The *lookup* side (in
    # export_learning_progressions) must canonicalize provenance row keys the same way
    # (see _canon_disposition_key).
    disposition_map: dict[tuple[str, str, str], str] = {}

    for e in builds_kept:
        _set_disposition(candidate=e, disposition_map=disposition_map, value="kept")

    for e in builds_dropped_low:
        _set_disposition(
            candidate=e, disposition_map=disposition_map, value="dropped_low_conf"
        )

    for e in relates_kept:
        _set_disposition(candidate=e, disposition_map=disposition_map, value="kept")

    for e in relates_dropped_low:
        _set_disposition(
            candidate=e, disposition_map=disposition_map, value="dropped_low_conf"
        )

    for e in relates_dropped_cap:
        _set_disposition(
            candidate=e, disposition_map=disposition_map, value="dropped_cap"
        )

    return builds_relationships, relates_relationships, stats, disposition_map


def _process_single_standard(
    *,
    buckets: DefaultDict[str, DefaultDict[str, dict[str, Any]]],
    config: CreateKGConfig,
    drops: dict[str, list[dict[str, Any]]],
    include_provenance: bool,
    sfi: StandardsFrameworkItem,
) -> None:
    """Process a single standard item and sort it into buckets or drops.

    The bucketing logic computes three independent axes—grade ordinal, subject label,
    and thread key—using config-driven mappings:

    1. **Level bounds** are resolved primarily from ordinals in `progression_context`
        (`grade_ordinal_low/high` or `stage_ordinal_low/high`). If ordinals are absent,
        we fall back to `config.progressions_grade_label_map` using `grade_key` or
        `stage_key`.
    2. **Subject label** is resolved via `config.progressions_subject_role`. Items
        without a matching role get `UNSPECIFIED_SUBJECT`.
    3. **Thread key** is computed via `config.progressions_cross_grade_match_roles`.
        Items with no matching roles become "unthreaded" and receive per-level sentinel
        thread keys to prevent false cross-level matching.

    NB: Banded/stage-level curricula (e.g., Tanzania "Standard I–II",
        "Standard III–VI"): When `progression_context` includes a true range
        (low != high), the bucket stores `grade_ordinal_low != grade_ordinal_high`,
        enabling cross-stage inference phases. If ordinals are unavailable and we rely
        on the config map, the bucket is treated as a single representative level
        (low == high).

    Parameters
    ----------
    buckets
        A nested dictionary for organizing standards into buckets based on grade and
        effective bucket key.
    config
        The KG creation config with LP-specific fields.
    drops
        A dictionary for collecting standards that are dropped due to validation
        issues, categorized by the reason for dropping.
    include_provenance
        Whether to include provenance information (e.g., page index) in the payload for
        LLM inference.
    sfi
        The standard item to process.
    """

    metadata = sfi.metadata or {}
    progression_context = metadata.get("progression_context") or {}
    sfi_uuid = str(sfi.case_identifier_uuid or sfi.identifier)

    # Statement type validation.
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

    # Level validation (grade or stage).
    grade_key = progression_context.get("grade_key")
    stage_key = progression_context.get("stage_key")
    normalized_grade_key = str(grade_key or "").strip().lower()
    normalized_stage_key = str(stage_key or "").strip().lower()
    normalized_level_key = normalized_grade_key or normalized_stage_key

    # Prefer ordinals computed upstream in Academic Standards export.
    g_lo = progression_context.get("grade_ordinal_low")
    g_hi = progression_context.get("grade_ordinal_high")
    s_lo = progression_context.get("stage_ordinal_low")
    s_hi = progression_context.get("stage_ordinal_high")

    if isinstance(g_lo, int) and isinstance(g_hi, int):
        level_lo, level_hi = (min(g_lo, g_hi), max(g_lo, g_hi))
    elif isinstance(s_lo, int) and isinstance(s_hi, int):
        level_lo, level_hi = (min(s_lo, s_hi), max(s_lo, s_hi))
    else:
        # Fall back to config-driven mapping when ordinals are unavailable.
        if not normalized_level_key:
            drops.setdefault("missing_grade_key", []).append(
                {"description": sfi.description, "sfi_uuid": sfi_uuid}
            )
            return

        mapped = (config.progressions_grade_label_map or {}).get(normalized_level_key)

        if mapped is None:
            logger.warning(
                f"progressions_grade_label_map: level key {normalized_level_key!r} "
                f"(grade_key={grade_key!r}, stage_key={stage_key!r}) not found in map. "
                f"Excluding SFI {sfi_uuid} from LP inference."
            )
            drops.setdefault("unmapped_grade_key", []).append(
                {
                    "description": sfi.description,
                    "grade_key": grade_key,
                    "stage_key": stage_key,
                    "sfi_uuid": sfi_uuid,
                }
            )
            return

        level_lo = level_hi = int(mapped)

    if not (isinstance(level_lo, int) and isinstance(level_hi, int)):
        drops.setdefault("missing_grade_key", []).append(
            {
                "description": sfi.description,
                "grade_key": grade_key,
                "stage_key": stage_key,
                "sfi_uuid": sfi_uuid,
            }
        )
        return

    # Bucket label (used as the top-level key for grouping buckets).
    grade_label = (
        (
            stage_key.strip()
            if isinstance(stage_key, str) and stage_key.strip()
            else f"LEVEL {level_lo}-{level_hi}"
        )
        if level_hi != level_lo
        else f"LEVEL {level_lo}"
    )

    # Topic path validation.
    topic_key = progression_context.get("topic_path_key", "")

    if not (isinstance(topic_key, str) and topic_key.strip()):
        drops["missing_topic_path_key"].append(
            {"description": sfi.description, "grade": grade_label, "sfi_uuid": sfi_uuid}
        )
        return

    # Subject label and topic parts setup.
    raw_parts = progression_context.get("topic_path_parts")
    topic_path_parts = raw_parts if isinstance(raw_parts, list) else []
    subject_role = config.progressions_subject_role
    subject_label = (
        next(
            (
                str(p["label"])
                for p in topic_path_parts
                if p.get("role") == subject_role and p.get("label")
            ),
            "UNSPECIFIED_SUBJECT",
        )
        if subject_role
        else "UNSPECIFIED_SUBJECT"
    )

    # Threading and bucket keys.
    cross_roles = config.progressions_cross_grade_match_roles
    lp_thread_key = (
        _compute_lp_thread_key(topic_path_parts, cross_roles) if cross_roles else None
    )
    effective_bucket_key = (
        lp_thread_key if lp_thread_key is not None else "__unthreaded__"
    )
    thread_key = (
        lp_thread_key
        if lp_thread_key is not None
        else f"__unthreaded__::{normalized_level_key}"
    )

    # Bucket management.
    b = buckets[grade_label].get(effective_bucket_key)

    if not b:
        b = buckets[grade_label][effective_bucket_key] = {
            "bucket_key": f"{grade_label}::{effective_bucket_key}",
            "effective_bucket_key": effective_bucket_key,
            "grade_level": grade_label,
            "grade_ordinal": level_lo,
            "grade_ordinal_low": level_lo,
            "grade_ordinal_high": level_hi,
            "stage_key": (
                stage_key.strip()
                if isinstance(stage_key, str) and stage_key.strip()
                else None
            ),
            "subject_label": subject_label,
            "thread_key": thread_key,
            "normalized_topic_path_key": str(
                progression_context.get("thread_key") or ""
            ),
            "topic_path_key": effective_bucket_key,
            "topic_path": _path_string(topic_path_parts),
            "topic_path_parts": topic_path_parts,
            "items": [],
        }

    payload: dict[str, Any] = {
        "description": sfi.description,
        "notes": sfi.notes,
        "order_index_within_parent": progression_context.get(
            "order_index_within_parent"
        ),
        "code_tuple": progression_context.get("code_tuple"),
        "sfi_uuid": sfi_uuid,
        "statement_code": sfi.statement_code,
        "statement_type": sfi.statement_type,
        "canon_order_path": progression_context.get("canon_order_path", []),
    }

    if include_provenance:
        indices = metadata.get("page_indices")
        valid_indices = indices if isinstance(indices, list) else []
        payload["page_index"] = min(valid_indices) if valid_indices else None

    b["items"].append(payload)


def _process_builds_towards_work_item(
    *,
    b_hi: dict[str, Any],
    b_lo: dict[str, Any],
    config: CreateKGConfig,
    inference_type: str,
    prompt_builder: Callable[..., Any],
    thread_key: str,
) -> tuple[list[CandidateEdge], list[dict[str, Any]], set[tuple[UUID, UUID]]]:
    """Process a single pair of adjacent buckets to infer progression edges.

    Parameters
    ----------
    b_hi
        The upper-level bucket dictionary.
    b_lo
        The lower-level bucket dictionary.
    config
        The knowledge graph run configuration.
    inference_type
        The type of inference being executed (cross-grade or cross-stage).
    prompt_builder
        The function used to build the LLM prompt.
    thread_key
        The string identifier for the current thread.

    Returns
    -------
    tuple[list[CandidateEdge], list[dict[str, Any]], set[tuple[UUID, UUID]]]
        A tuple containing:
            1. Generated candidate edges for this pair.
            2. Provenance rows for the generated edges.
            3. Set of (source_uuid, target_uuid) tuples.
    """

    candidates: list[CandidateEdge] = []
    provenance_rows: list[dict[str, Any]] = []
    cross_level_build_pairs: set[tuple[UUID, UUID]] = set()

    lo_label = _level_label(b_lo)
    hi_label = _level_label(b_hi)
    lo_lo, lo_hi = _level_bounds(b_lo)
    hi_lo, hi_hi = _level_bounds(b_hi)

    lower_items = b_lo.get("items") or []
    upper_items = b_hi.get("items") or []

    lower_payload = [
        _build_item_payload(include_order_index=True, item=it) for it in lower_items
    ]
    upper_payload = [
        _build_item_payload(include_order_index=True, item=it) for it in upper_items
    ]

    prompt = prompt_builder(
        lower_items=lower_payload,
        lower_grade_label=lo_label,
        min_confidence=config.progressions_builds_towards_min_confidence,
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
            _make_provenance_row(
                candidate=ce,
                inference_type=inference_type,
                phase=2,
                rationale=e.rationale,
                lower_level=lo_label,
                upper_level=hi_label,
                thread_key=thread_key,
            )
        )

    return candidates, provenance_rows, cross_level_build_pairs


def _process_relates_to_work_item(
    *,
    config: CreateKGConfig,
    current_call_base: int,
    forbidden_builds_pairs: set[tuple[UUID, UUID]],
    hi_high: int,
    hi_low: int,
    inference_type: str,
    lo_high: int,
    lo_low: int,
    lower: dict[str, Any],
    max_edges_per_sfi: int,
    prompt_builder: Callable[..., Any],
    subject_label: str,
    total_calls: int,
    upper: dict[str, Any],
) -> tuple[list[CandidateEdge], list[dict[str, Any]]]:
    """Execute bidirectional relatesTo inference for a single level-pair.

    Parameters
    ----------
    config
        The knowledge graph run configuration.
    current_call_base
        The starting call index for logging progression (updated by 2 per work item).
    forbidden_builds_pairs
        A set of UUID tuples representing buildsTowards relationships to exclude.
    hi_high
        The upper bound integer of the higher grade level.
    hi_low
        The lower bound integer of the higher grade level.
    inference_type
        The type of inference being executed ("cross_grade_relates_to" or
        "cross_stage_relates_to").
    lo_high
        The upper bound integer of the lower grade level.
    lo_low
        The lower bound integer of the lower grade level.
    lower
        The lower-level bucket dictionary containing items and labels.
    max_edges_per_sfi
        The maximum number of relatesTo edges permitted per standard framework item.
    prompt_builder
        The function used to build the LLM prompt.
    subject_label
        The string identifier for the academic subject.
    total_calls
        The total number of planned LLM calls across all work items.
    upper
        The upper-level bucket dictionary containing items and labels.

    Returns
    -------
    tuple[list[CandidateEdge], list[dict[str, Any]]]
        A tuple containing:
            1. Bidirectionally confirmed candidate edges.
            2. Provenance rows for the generated edges.
    """

    candidates: list[CandidateEdge] = []
    provenance_rows: list[dict[str, Any]] = []

    lower_items = lower["items"]
    upper_items = upper["items"]

    forbidden_pairs_set, forbidden_pairs = _resolve_forbidden_pairs(
        forbidden_builds_pairs=forbidden_builds_pairs,
        lower_items=lower_items,
        upper_items=upper_items,
    )

    # Call 1: lower -> upper.
    prompt_lo_hi = prompt_builder(
        forbidden_pairs=forbidden_pairs,
        list_a_grade_label=str(lower["level_label"]),
        list_a_items=lower_items,
        list_b_grade_label=str(upper["level_label"]),
        list_b_items=upper_items,
        max_edges_per_sfi=max_edges_per_sfi,
        min_confidence=config.progressions_relates_to_min_confidence,
        subject_label=subject_label,
    )

    call_idx_1 = current_call_base + 1

    logger.info(
        f"Phase 4 Progress: {call_idx_1}/{total_calls} "
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

    # Call 2: upper -> lower (reverse presentation order for bidirectional
    # confirmation). We swap BOTH items and labels so the LLM sees a self-consistent
    # view. The neutral "List A"/"List B" names in the prompt avoid the semantic
    # confusion of calling upper-grade items "lower".
    prompt_hi_lo = prompt_builder(
        forbidden_pairs=forbidden_pairs,
        list_a_grade_label=str(upper["level_label"]),
        list_a_items=upper_items,
        list_b_grade_label=str(lower["level_label"]),
        list_b_items=lower_items,
        max_edges_per_sfi=max_edges_per_sfi,
        min_confidence=config.progressions_relates_to_min_confidence,
        subject_label=subject_label,
    )

    call_idx_2 = current_call_base + 2

    logger.info(
        f"Phase 4 Progress: {call_idx_2}/{total_calls} "
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
    common_pairs = sorted(set(m_lo_hi.keys()) & set(m_hi_lo.keys()))

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
            source_sfi_uuid=_uuid(a),
            target_sfi_uuid=_uuid(b),
        )
        candidates.append(ce)
        provenance_rows.append(
            _make_provenance_row(
                candidate=ce,
                inference_type=inference_type,
                phase=4,
                bidirectional_confirmed=True,
                confidence_fwd=float(conf_lo_hi),
                confidence_rev=float(conf_hi_lo),
                rationale_fwd=rat_lo_hi,
                rationale_rev=rat_hi_lo,
                subject_label=subject_label,
                lower_level=lower["level_label"],
                upper_level=upper["level_label"],
            )
        )

    return candidates, provenance_rows


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

        # Record forbidden pairs as *undirected* canonicalized UUID string tuples. Uses
        # canon_str_pair for consistency with validator canonicalization.
        if (ss in allowed_lo and tt in allowed_hi) or (
            ss in allowed_hi and tt in allowed_lo
        ):
            forbidden_pairs_set.add(canon_str_pair(ss, tt))

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


def _set_disposition(
    *,
    candidate: CandidateEdge,
    disposition_map: dict[tuple[str, str, str], str],
    value: str,
) -> None:
    """Set disposition with logging on overwrite.

    Parameters
    ----------
    candidate
        The CandidateEdge for which to set the disposition. This edge is expected to
        have attributes such as rel_type, source_sfi_uuid, and target_sfi_uuid, which
        are used to construct the key for the disposition map.
    disposition_map
        The dictionary tracking edge dispositions, to be updated in-place.
    value
        The disposition value to set for this edge, which should be one of the
        following strings: "kept", "dropped_low_conf", "dropped_cap", or
        "dropped_dedupe". This value indicates the final disposition of the edge after
        processing and filtering.
    """

    key = _canon_disposition_key(
        rel_type=candidate.rel_type,
        source=str(candidate.source_sfi_uuid),
        target=str(candidate.target_sfi_uuid),
    )
    prev = disposition_map.get(key)

    if prev is not None and prev != value:
        logger.warning(f"Disposition overwrite: {key} was '{prev}', now '{value}'.")

    disposition_map[key] = value


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

    code_tuple = _extract_code_tuple(
        code_string=code, raw_code_tuple=s.get("code_tuple")
    )

    missing_code_tuple = 1 if code_tuple is None else 0
    code_tuple_key = code_tuple if code_tuple is not None else (10**9,)
    uuid_key = s.get("sfi_uuid") or s.get("case_identifier_uuid") or ""

    return order_index, missing_code_tuple, code_tuple_key, code, uuid_key


def _thread_sort_key(b: dict[str, Any]) -> tuple[str, str]:
    """Sort threads by topic_path (with fallback to topic_path_key) to ensure
    deterministic ordering for sampling and pairing across runs, even if the input
    order changes.

    Parameters
    ----------
    b
        The bucket dictionary representing a thread, which may contain "topic_path"
        and/or "topic_path_key" for sorting.

    Returns
    -------
    tuple[str, str]
        A tuple used for sorting threads, where the first element is the
        "topic_path" (or an empty string if not present) and the second element is
        the "topic_path_key" (or an empty string if not present). This ensures
        consistent ordering of threads based on their topic paths, with a fallback
        to topic path keys when topic paths are missing.
    """

    return str(b.get("topic_path") or ""), str(b.get("topic_path_key") or "")


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
        academic_standards=academic_standards, config=config, include_provenance=True
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
    builds_rels, relates_rels, stats, disposition_map = _process_and_filter_candidates(
        candidates=candidates, config=config, doc_key=ctx.doc_key, sfi_index=sfi_index
    )

    # Enrich provenance rows with post-filtering disposition.
    #
    # NB: relatesTo edges are canonicalized during dedup (source < target by UUID.int),
    # so the disposition map keys for relatesTo are already canonical. Provenance rows,
    # however, carry the *original* (pre-dedup) source/target which may be in either
    # order. _canon_disposition_key normalises the lookup key so that the match
    # succeeds regardless of the original edge direction.
    for row in provenance_rows:
        key = _canon_disposition_key(
            rel_type=row.get("rel_type", ""),
            source=row.get("source", ""),
            target=row.get("target", ""),
        )
        row["disposition"] = disposition_map.get(key, "dropped_dedupe")

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
            "cross_stage_builds_towards": config.progressions_cross_stage_builds_towards,
            "within_grade_relates_to": config.progressions_within_grade_relates_to,
            "cross_grade_relates_to": config.progressions_cross_grade_relates_to,
            "cross_stage_relates_to": config.progressions_cross_stage_relates_to,
        },
        "thresholds": {
            "builds_towards_min_confidence": config.progressions_builds_towards_min_confidence,
            "relates_to_min_confidence": config.progressions_relates_to_min_confidence,
            "relates_to_max_edges_per_sfi": config.progressions_relates_to_max_edges_per_sfi,
            "within_grade_relates_to_max_items_per_subject": config.progressions_within_grade_relates_to_max_items_per_subject,
            "cross_grade_relates_to_max_items_per_subject": config.progressions_cross_grade_relates_to_max_items_per_subject,
        },
        "drops": buckets_info.get("drops") or {},
    }
    write_to_json(
        fp=kg_dirs.learning_progressions / "learning_progressions_report.json",
        json_info=report,
    )

    learning_progressions = LearningProgressionsExport(
        builds_towards_relationships=builds_rels,
        graph_bundle=graph_bundle,
        relates_to_relationships=relates_rels,
        report=report,
    )

    logger.info(
        f"Exported Learning Progressions KG: "
        f"{len(learning_progressions.builds_towards_relationships)} `buildsTowards` relationships, "
        f"{len(learning_progressions.relates_to_relationships)} `relatesTo` relationships"
    )

    return learning_progressions


def group_standards_for_learning_progressions(
    *,
    academic_standards: AcademicStandardsExport,
    config: CreateKGConfig,
    include_provenance: bool = True,
) -> dict[str, Any]:
    """Build learning progression buckets for the LLM.

    Uses config-driven three-axis bucketing:

    1. **Level bounds** resolved from `progression_context` ordinals when present, with
        a fallback to `config.progressions_grade_label_map`.
    2. **Subject label** resolved via `config.progressions_subject_role`.
    3. **Thread key** computed via `config.progressions_cross_grade_match_roles`.

    Parameters
    ----------
    academic_standards
        The exported Academic Standards KG artifacts.
    config
        The KG creation config with LP-specific fields that drive the three-axis
        bucketing logic in `_process_single_standard`.
    include_provenance
        Whether to include provenance metadata in the payload for each standard item,
        which the LLM can use as signals when deciding buildsTowards relationships.

    Returns
    -------
    dict[str, Any]
        A dictionary containing grouped standards by grade and thread, as well as any
        dropped items due to missing or non-standard data.
    """

    # grade -> effective_bucket_key -> bucket.
    buckets: DefaultDict[str, DefaultDict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    drops: dict[str, list[dict[str, Any]]] = {
        "missing_grade_key": [],
        "missing_topic_path_key": [],
        "non_standard_item": [],
        "unmapped_grade_key": [],
    }

    for sfi in academic_standards.items:
        _process_single_standard(
            buckets=buckets,
            config=config,
            drops=drops,
            include_provenance=include_provenance,
            sfi=sfi,
        )

    return _format_learning_progressions_dict(buckets=buckets, drops=drops)
