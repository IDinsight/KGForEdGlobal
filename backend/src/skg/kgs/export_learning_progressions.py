"""This module contains functionalities related to exporting the Learning Progressions
knowledge graph. It exports relationships between *exported* StandardsFrameworkItems
(SFIs) from an Academic Standards export.

- Relationships:
  - buildsTowards (SFI -> SFI), directional
  - relatesTo (SFI -- SFI), associative (canonicalized to a single directed edge)

The export is *shape-preserving* for the LC Knowledge Graph ontology and is designed to
work for non-US curriculum documents mapped into the LC "academic standards" shape.

Phases (toggleable via CreateKGConfig):

1. Within-level buildsTowards
2. Cross-level/cross-stage buildsTowards (adjacent levels, normalized thread matching)
3. Within-level relatesTo (cross-subject/cross-strand within a level;
    subject-pair sampling)
4. Cross-level/cross-stage relatesTo (adjacent levels within the same subject,
    excluding buildsTowards pairs)
"""

# Standard Library
import hashlib
import itertools
import json
import re

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from typing import Any, Callable, DefaultDict, Literal, Optional
from uuid import UUID, uuid5

# Third Party Library
from loguru import logger

# Package Library
from skg.kgs.export_academic_standards import AcademicStandardsExport
from skg.kgs.llm import KGUsageTracker, infer_progression_edges
from skg.kgs.prompts import (
    cross_level_builds_towards,
    cross_level_relates_to,
    cross_stage_builds_towards,
    cross_stage_relates_to,
    within_level_builds_towards,
    within_level_relates_to,
)
from skg.kgs.schemas import (
    ProgressionEdgesResponse,
    Relationship,
    StandardsFrameworkItem,
)
from skg.kgs.utils import ExportContext, KGDirs, canon_str_pair, normalize_key_token
from skg.kgs.validators import (
    validate_cross_level_builds_towards,
    validate_cross_level_relates_to,
    validate_within_level_builds_towards,
    validate_within_level_relates_to,
)
from skg.schemas import CreateKGConfig
from skg.utils.constants import NodeRole
from skg.utils.general import open_json_type, write_to_json

BUILDS_TOWARDS = "buildsTowards"
CROSS_LEVEL_INFERENCE_TYPES: set[str] = {
    "cross_level_builds_towards",
    "cross_level_relates_to",
    "cross_stage_builds_towards",
    "cross_stage_relates_to",
}
RELATES_TO = "relatesTo"
SFIContextScope = Literal["cross_level", "within_level"]
WITHIN_LEVEL_INFERENCE_TYPES: set[str] = {
    "within_level_builds_towards",
    "within_level_cross_thread_relates_to",
    "within_level_relates_to",
}


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
    lp_kg: dict[str, Any]
    relates_to_relationships: list[Relationship]
    report: dict[str, Any]


def _allow_within_level_inference(
    *, bucket: dict[str, Any], config: CreateKGConfig
) -> bool:
    """Return True if Phase 1/3 within-level inference should consider this bucket.

    By default, we only run within-level inference for single-level buckets. If
    config.lp_within_level_allow_banded_levels=True, banded/stage buckets are allowed.

    Parameters
    ----------
    bucket
        The bucket dictionary containing information about a thread of standards within
        a level, which may include level bounds and other contextual information.
    config
        The knowledge graph run configuration.

    Returns
    -------
    bool
        True if within-level inference should be allowed for this bucket based on the
        configuration and whether it represents a single level or a banded level.
    """

    if config.lp_within_level_allow_banded_levels:
        return True

    return _is_single_level_bucket(bucket)


def _assign_candidate_uids(
    *, candidates: list[CandidateEdge], provenance_rows: list[dict[str, Any]]
) -> list[CandidateEdge]:
    """Build a deterministic identifier for this ordered raw-candidate occurrence
    within the current export run.

    Candidate inference functions append exactly one provenance row for each raw
    candidate edge, in the same order. This function validates that invariant, assigns
    a deterministic UID and order index to every candidate, mirrors the UID into the
    matching provenance row, and returns updated immutable CandidateEdge instances.

    Parameters
    ----------
    candidates
        Full raw candidate list before deduplication.
    provenance_rows
        Full raw provenance-row list before post-filter disposition enrichment. This
        list is updated in place with `candidate_uid` and `candidate_order_index`.

    Returns
    -------
    list[CandidateEdge]
        Candidate list with `candidate_uid` and `candidate_order_index` stored in each
        candidate metadata dictionary.

    Raises
    ------
    ValueError
        If the candidate and provenance-row counts differ.
    """

    if len(candidates) != len(provenance_rows):
        raise ValueError(
            f"Candidate/provenance cardinality mismatch before LP candidate processing: "
            f"{len(candidates)} candidate(s) vs {len(provenance_rows)} provenance "
            f"row(s). Each raw candidate must have exactly one provenance row so "
            f"dedupe audit disposition can be joined unambiguously."
        )

    updated_candidates: list[CandidateEdge] = []

    for occurrence_index, candidate in enumerate(candidates):
        metadata = (
            dict(candidate.metadata) if isinstance(candidate.metadata, dict) else {}
        )
        uid = str(metadata.get("candidate_uid") or "").strip()

        if not uid:
            # Build a deterministic per-run identifier for the raw candidate edge.
            stable_metadata = {
                str(key): value
                for key, value in metadata.items()
                if key not in {"candidate_order_index", "candidate_uid", "dedupe"}
            }
            payload = {
                "confidence": float(candidate.confidence),
                "evidence": candidate.evidence,
                "inference_source": candidate.inference_source,
                "inference_type": candidate.inference_type,
                "llm_confidence": candidate.llm_confidence,
                "metadata": stable_metadata,
                "occurrence_index": int(occurrence_index),
                "rel_type": candidate.rel_type,
                "source_sfi_uuid": str(candidate.source_sfi_uuid),
                "target_sfi_uuid": str(candidate.target_sfi_uuid),
            }
            encoded = json.dumps(
                payload, default=str, ensure_ascii=False, sort_keys=True
            ).encode("utf-8")
            uid = hashlib.sha256(encoded).hexdigest()[:24]

        metadata["candidate_order_index"] = occurrence_index
        metadata["candidate_uid"] = uid
        updated_candidate = _replace_candidate_metadata(
            candidate=candidate, metadata=metadata
        )
        provenance_rows[occurrence_index]["candidate_order_index"] = occurrence_index
        provenance_rows[occurrence_index]["candidate_uid"] = uid
        updated_candidates.append(updated_candidate)

    return updated_candidates


def _best_map(
    resp: ProgressionEdgesResponse,
) -> dict[tuple[str, str], tuple[float, str]]:
    """Extract the best confidence and rationale for each canonicalized pair of UUIDs
    regardless of edge direction, to facilitate bidirectional confirmation of relatesTo
    edges between the two levels.

    Examples
    --------
    1. Direction-insensitive pair canonicalization for relatesTo

        `relatesTo` is conceptually undirected, but the LLM response schema still uses
        `source_sfi_uuid` and `target_sfi_uuid`. The model may therefore return either
        direction for the same conceptual pair.

        Given edges:

            A -> B, confidence=0.91, rationale="Shared decoding skill..."
            B -> A, confidence=0.88, rationale="Both involve decoding..."

        `_best_map()` canonicalizes both with `canon_str_pair(A, B)`, so both edges map
        to the same key:

            (A, B)  # sorted canonical pair

        The returned map keeps the highest-confidence version:

            {
                (A, B): (0.91, "Shared decoding skill...")
            }

    2. Keeping the best duplicate pair

        If the LLM returns the same conceptual pair more than once, only the highest
        confidence/rationale is retained.

        Given edges:

            C -> D, confidence=0.86, rationale="Both require sentence construction..."
            C -> D, confidence=0.93, rationale="Both involve constructing coherent sentences..."

        `_best_map()` returns:

            {
                (C, D): (0.93, "Both involve constructing coherent sentences...")
            }

    3. Supporting bidirectional confirmation

        Phase 3 within-level relatesTo asks the model twice:
          - once with thread A as List A and thread B as List B
          - once with the lists swapped

        `_best_map()` converts both responses into canonical pair maps so they can be
        intersected reliably.

        First pass:

            A -> B, confidence=0.92

            best_ab = {
                (A, B): (0.92, "...")
            }

        Swapped pass:

            B -> A, confidence=0.89

            best_ba = {
                (A, B): (0.89, "...")
            }

        Because `(A, B)` appears in both maps, the pair is bidirectionally confirmed.
        Downstream code can then use `min(0.92, 0.89)` as the conservative confirmed
        confidence.

    4. Not preserving direction

        `_best_map()` should be used only where direction does not matter, such as
        `relatesTo` confirmation. It is not appropriate for directed `buildsTowards`
        edge logic, where A -> B and B -> A have different meanings.

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


def _bucket_topic_context(*, bucket: dict[str, Any], max_examples: int = 3) -> str:
    """Return topic context for prompts, reports, and sorting.

    Buckets can intentionally contain items from multiple finer-grained topic paths.
    For example, a within-level Senegal reading bucket keyed only by `strand` may
    include expectations from several paliers, weeks, and subtopics. In that case, a
    single bucket-level topic path would be misleading.

    This function prefers the aggregate `topic_path_examples` list maintained by
    `_get_or_create_bucket()`. If no examples exist, it falls back to topic path keys
    and then to the LP bucket key.

    Parameters
    ----------
    bucket
        The bucket dictionary created by `_get_or_create_bucket()`.
    max_examples
        Maximum number of path examples to include in the returned context string.

    Returns
    -------
    str
        A compact topic-context string suitable for LLM prompts, logs, reports, and
        deterministic sorting.
    """

    examples = bucket.get("topic_path_examples")

    if isinstance(examples, list):
        cleaned_examples = [
            str(example).strip() for example in examples if str(example or "").strip()
        ]

        if cleaned_examples:
            return " | ".join(cleaned_examples[:max_examples])

    tpks = bucket.get("topic_path_keys")

    if isinstance(tpks, list):
        cleaned_keys = [str(tpk).strip() for tpk in tpks if str(tpk or "").strip()]

        if cleaned_keys:
            return " | ".join(cleaned_keys[:max_examples])

    return str(bucket.get("lp_bucket_key") or bucket.get("bucket_key") or "").strip()


def _build_combined_sfi_context_index(
    *,
    cross_sfi_index: dict[str, dict[str, Any]],
    within_sfi_index: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Merge scoped SFI indexes into the emission-time metadata shape.

    Each scoped index describes the same SFI from one inference-bucket perspective. The
    combined index keeps scope-neutral source-item facts under `item_context` and
    preserves bucket-derived facts under `within_level_context` and
    `cross_level_context`. This separation prevents a within-level bucket fallback from
    being mistaken for source-item context when emitting cross-level edges, and vice
    versa.

    Parameters
    ----------
    cross_sfi_index
        SFI UUID -> context entries built from the cross-level bucket store.
    within_sfi_index
        SFI UUID -> context entries built from the within-level bucket store.

    Returns
    -------
    dict[str, dict[str, Any]]
        SFI UUID -> combined context with `item_context`, `within_level_context`,
        `cross_level_context`, and `available_context_scopes`.

    Raises
    ------
    ValueError
        If the same SFI UUID appears in both indexes with conflicting values for any
        scope-neutral fields (e.g., `description`, `statement_code`, etc.).
    """

    scope_neutral_check_fields = {
        "description",
        "doc_pos_page_index",
        "doc_pos_y0",
        "item_topic_path",
        "item_topic_path_key",
        "level_basis",
        "level_key",
        "level_label",
        "level_ordinal_high",
        "level_ordinal_low",
        "page_index",
        "sfi_uuid",
        "statement_code",
        "statement_type",
    }
    combined: dict[str, dict[str, Any]] = {}

    for sfi_uuid in sorted(set(within_sfi_index) | set(cross_sfi_index)):
        within_entry = within_sfi_index.get(sfi_uuid) or {}
        cross_entry = cross_sfi_index.get(sfi_uuid) or {}
        within_item_context = within_entry.get("item_context") or {}
        cross_item_context = cross_entry.get("item_context") or {}

        if within_item_context and cross_item_context:
            diffs = {
                field_name: {
                    "within_level": within_item_context.get(field_name),
                    "cross_level": cross_item_context.get(field_name),
                }
                for field_name in sorted(scope_neutral_check_fields)
                if within_item_context.get(field_name)
                != cross_item_context.get(field_name)
            }

            if diffs:
                raise ValueError(
                    f"Conflicting scope-neutral SFI context while combining LP SFI "
                    f"context indexes for SFI {sfi_uuid}: {diffs = }"
                )

        item_context = (
            within_item_context or cross_item_context or {"sfi_uuid": sfi_uuid}
        )
        available_context_scopes: list[str] = []

        if within_entry.get("within_level_context") is not None:
            available_context_scopes.append("within_level")

        if cross_entry.get("cross_level_context") is not None:
            available_context_scopes.append("cross_level")

        combined[sfi_uuid] = {
            "available_context_scopes": available_context_scopes,
            "item_context": item_context,
            "cross_level_context": cross_entry.get("cross_level_context"),
            "within_level_context": within_entry.get("within_level_context"),
        }

    return combined


def _build_fallback_segments(
    *, config: CreateKGConfig, metadata: dict[str, Any], sfi: StandardsFrameworkItem
) -> list[str]:
    """Build configured source-field fallback key segments for within-level bucketing.

    The returned segments are passed to `_compute_bucket_keys()` and are used only if
    the configured hierarchy roles do not produce a within-level bucket key. They let a
    curriculum-specific but still general source field, such as `statement_type`,
    define a within-level progression thread when the hierarchy path is too shallow.

    Examples
    --------
    1. Senegal reading: fallback to statement_type when no strand is present

        The Senegal reading config uses:

            lp_within_level_bucket_roles = ["strand"]
            lp_within_level_fallback_fields = ["statement_type"]

        Some written-language expectations may have only a substage in the topic path:

            progression_context = {
                "topic_path_parts": [
                    {
                        "role": "substage",
                        "label": "palier 1 - communication écrite",
                    }
                ]
            }

        but the exported SFI still has:

            sfi.statement_type = "Grammaire"

        Calling:

            _build_fallback_segments(
                config=config,
                metadata=sfi.metadata,
                sfi=sfi,
            )

        returns:

            ["statement_type=grammaire"]

        `_compute_bucket_keys()` can then use this fallback if no configured
        within-level role, such as "strand", is present.

    2. Fallback is built even when hierarchy roles later succeed

        If an item has both a strand and a statement type:

            progression_context = {
                "topic_path_parts": [
                    {"role": "strand", "label": "Lecture"},
                ]
            }
            sfi.statement_type = "Objectif spécifique"

        and the config contains:

            lp_within_level_fallback_fields = ["statement_type"]

        this function returns:

            ["statement_type=objectif_specifique"]

        However, `_compute_bucket_keys()` will ignore this fallback if the configured
        within-level bucket role "strand" successfully produces a key.

    3. Multiple fallback fields preserve configured order

        If the config contains:

            lp_within_level_fallback_fields = ["statement_type", "source_label"]

        and the item has:

            sfi.statement_type = "Écriture / Copie"
            sfi.metadata = {"source_label": "Copie"}

        the function returns:

            [
                "statement_type=ecriture_copie",
                "source_label=copie",
            ]

        The joined fallback key would be:

            "statement_type=ecriture_copie|source_label=copie"

    4. Blank configured fields are skipped

        If the config contains:

            lp_within_level_fallback_fields = ["statement_code", "statement_type"]

        and the item has:

            sfi.statement_code = None
            sfi.statement_type = "Orthographe"

        the function returns:

            ["statement_type=orthographe"]

    5. Academic subject fallback is usually broad

        If the config contains:

            lp_within_level_fallback_fields = ["academic_subject"]

        and the item has:

            sfi.academic_subject = "Langue et Communication"

        the function returns:

            ["academic_subject=langue_et_communication"]

        Use this carefully: for a single-subject PDF, it may collapse too many items
        into one broad fallback bucket.

    6. Unsupported fallback fields fail fast

        If a malformed config somehow contains:

            lp_within_level_fallback_fields = ["source_column"]

        the function raises:

            ValueError("Unsupported fallback field name: source_column")

    Parameters
    ----------
    config
        The knowledge graph run configuration, which may specify which source fields to
        use for fallback key segments via `lp_within_level_fallback_fields`.
    metadata
        The metadata dictionary for the current SFI, which may contain fields like
        `source_label` that can be used for fallback segments.
    sfi
        The StandardsFrameworkItem instance for the current item, which may contain
        fields like `statement_type`, `statement_code`, and `academic_subject` that can
        be used for fallback segments.

    Returns
    -------
    list[str]
        A list of strings representing fallback key segments derived from the specified
        source fields in the configuration, which can be used for within-level
        bucketing when hierarchy-based roles do not yield a key.

    Raises
    ------
    ValueError
        If `config.lp_within_level_fallback_fields` contains a field name that is not
        recognized or supported for fallback segment extraction.
    """

    segments: list[str] = []

    for field_name in config.lp_within_level_fallback_fields or []:
        if field_name == "statement_type":
            value = sfi.statement_type
        elif field_name == "statement_code":
            value = sfi.statement_code
        elif field_name == "source_label":
            value = metadata.get("source_label")
        elif field_name == "academic_subject":
            value = sfi.academic_subject
        else:
            raise ValueError(f"Unsupported fallback field name: {field_name}")

        value_s = str(value or "").strip()

        if not value_s:
            continue

        segments.append(
            f"{field_name}={normalize_key_token(label=value_s, separator='_')}"
        )

    return segments


def _build_item_payload(
    *,
    include_order_index: bool = False,
    item: dict[str, Any],
    sequence_index: Optional[int] = None,
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
    sequence_index
        Optional zero-based position of the item within the ordered prompt list. Used
        by within-level buildsTowards prompts to make sequence direction explicit.
    thread_key_field
        If provided, the item key to read for an additional `thread_key` field in the
        payload (e.g., `"_thread_key"`). Used by Phase 4 cross-level relatesTo.

    Returns
    -------
    dict[str, Any]
        A dictionary containing the SFI fields to include in the LLM prompt payload.
    """

    payload: dict[str, Any] = {
        "description": item.get("description"),
        "notes": item.get("notes"),
        "page_index": item.get("page_index"),
        "sfi_uuid": item["sfi_uuid"],
        "statement_code": item.get("statement_code"),
        "statement_type": item.get("statement_type"),
        "topic_path": item.get("topic_path"),
        "topic_path_key": item.get("topic_path_key"),
    }

    if sequence_index is not None:
        payload["sequence_index"] = sequence_index

    if include_order_index:
        payload["order_index_within_parent"] = item.get("order_index_within_parent")

    if thread_key_field:
        payload["thread_key"] = item.get(thread_key_field)

    return payload


def _build_order_index_lookup(
    academic_standards: AcademicStandardsExport,
) -> dict[str, int]:
    """Build canonical-node-id to sibling-order-index lookup.

    `progression_context.canon_order_path` stores canonical IR node IDs. Academic
    Standards `hasChild` relationship metadata is therefore the best source for
    resolving numeric order paths because it includes `canonical_child_id` plus
    export/canonical order metadata for grouping ancestors as well as leaf SFIs. SFI
    progression metadata is used only as a supplement so the relationship-derived
    ordering wins when both sources are available.

    Parameters
    ----------
    academic_standards
        The exported Academic Standards KG artifacts.

    Returns
    -------
    dict[str, int]
        A mapping from canonical IR node ID strings to sibling order indices.
    """

    order_index_lookup: dict[str, int] = {}

    for rel in academic_standards.relationships:
        if rel.relationship_type != "hasChild":
            continue

        metadata = rel.metadata if isinstance(rel.metadata, dict) else {}
        canonical_child_id = str(metadata.get("canonical_child_id") or "").strip()

        if not canonical_child_id:
            continue

        order_index = metadata.get("export_order_index")

        if not isinstance(order_index, int):
            order_index = metadata.get("canonical_order_index")

        if not isinstance(order_index, int):
            continue

        order_index_lookup[canonical_child_id] = order_index

    for sfi in academic_standards.items:
        metadata = sfi.metadata if isinstance(sfi.metadata, dict) else {}
        canonical_node_id = str(metadata.get("canonical_node_id") or "").strip()
        progression_context = metadata.get("progression_context")

        if not isinstance(progression_context, dict) or not canonical_node_id:
            continue

        order_index = progression_context.get("order_index_within_parent")

        if not isinstance(order_index, int):
            order_index = progression_context.get("canonical_order_index_within_parent")

        if not isinstance(order_index, int):
            continue

        order_index_lookup.setdefault(canonical_node_id, order_index)

    return order_index_lookup


def _build_relates_to_work_items(
    *,
    excluded: set[str],
    level_subject_threads: dict[str, dict[str, list[dict[str, Any]]]],
    max_items: int,
) -> list[dict[str, Any]]:
    """Build work items for Phase 3 within-level relatesTo inference.

    Iterates through levels and subjects, excludes specified subjects, pairs remaining
    subjects, samples prompt items, and compiles the payloads required for
    bidirectional LLM comparisons.

    Parameters
    ----------
    excluded
        A set of subject labels to exclude from comparison.
    level_subject_threads
        A dictionary mapping level labels to dictionaries of subject labels
        to lists of thread buckets.
    max_items
        The maximum number of items to sample per subject-like group.

    Returns
    -------
    list[dict[str, Any]]
        A list of work item dictionaries representing subject pairs to compare.
    """

    phase3_excluded_count = 0
    work_items: list[dict[str, Any]] = []

    for level_label, by_subject in level_subject_threads.items():
        phase3_excluded_count += sum(1 for s in by_subject if s in excluded)
        subject_keys = [s for s in sorted(by_subject.keys()) if s not in excluded]

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

                thread_a_path = " | ".join(
                    _bucket_topic_context(bucket=bucket).strip()
                    for bucket in threads_a[:3]
                    if _bucket_topic_context(bucket=bucket)
                )
                thread_b_path = " | ".join(
                    _bucket_topic_context(bucket=bucket).strip()
                    for bucket in threads_b[:3]
                    if _bucket_topic_context(bucket=bucket)
                )
                work_items.append(
                    {
                        "level_label": level_label,
                        "subject_a": subject_a,
                        "subject_b": subject_b,
                        "sampled_a": sampled_a,
                        "sampled_b": sampled_b,
                        "sampled_a_count": len(sampled_a),
                        "sampled_b_count": len(sampled_b),
                        "thread_a_path": thread_a_path,
                        "thread_b_path": thread_b_path,
                    }
                )

    total_pairs = len(work_items)
    total_calls = total_pairs * 2  # Bidirectional confirmation

    if phase3_excluded_count > 0:
        logger.info(
            f"Phase 3: excluded {phase3_excluded_count} subject-like bucket(s) with "
            f"subject_label in {sorted(excluded)}"
        )

    logger.info(
        f"{total_pairs} within-level cross-thread pairs for relatesTo inference "
        f"(bidirectional confirmation => {total_calls} LLM calls)."
    )

    return work_items


def _build_scoped_sfi_index(
    *, by_level: dict[str, list[dict[str, Any]]], scope: SFIContextScope
) -> dict[str, dict[str, Any]]:
    """Build an SFI UUID -> context index for one LP bucket semantics scope.

    Within-level and cross-level bucket stores answer different questions and can use
    different bucket keys. This function therefore builds only one scoped view at a
    time. Use `_build_combined_sfi_context_index()` to create the emission-time index
    that contains the scope-neutral item facts plus both bucket-derived views.

    Parameters
    ----------
    by_level
        Finalized bucket store view keyed by level label. Pass `by_within_level` when
        `scope="within_level"` and `by_cross_level` when `scope="cross_level"`.
    scope
        The bucket semantics represented by the index: `within_level` for Phase 1/3
        buckets or `cross_level` for Phase 2/4 buckets.

    Returns
    -------
    dict[str, dict[str, Any]]
        SFI UUID -> context dictionary containing `item_context` plus exactly one
        bucket-derived scoped context block (`within_level_context` or
        `cross_level_context`).

    Raises
    ------
    ValueError
        If an item is missing `sfi_uuid`, if a bucket's `bucket_scope` conflicts with
        the requested scope, if a duplicate SFI UUID appears within the same scoped
        bucket store, or if an unsupported scope is supplied.
    """

    if scope not in {"cross_level", "within_level"}:
        raise ValueError(f"Unsupported SFI context scope: {scope}")

    index: dict[str, dict[str, Any]] = {}

    for level_label, level_buckets in (by_level or {}).items():
        for bucket in level_buckets or []:
            bucket_scope = bucket.get("bucket_scope")

            if bucket_scope is not None and bucket_scope != scope:
                raise ValueError(
                    f"Bucket scope mismatch while building LP SFI context index. "
                    f"expected={scope!r}; actual={bucket_scope!r}; "
                    f"level={level_label!r}; bucket={bucket.get('lp_bucket_key')!r}."
                )

            bucket_topic_path = _bucket_topic_context(bucket=bucket)
            bucket_topic_path_key = _first_topic_path_key(bucket) or bucket.get(
                "lp_bucket_key"
            )

            for item in bucket.get("items") or []:
                sfi_uuid = str(item.get("sfi_uuid") or "").strip()

                if not sfi_uuid:
                    raise ValueError(
                        f"Missing sfi_uuid while building LP SFI context index. "
                        f"scope={scope!r}; level={level_label!r}; "
                        f"bucket={bucket.get('lp_bucket_key')!r}."
                    )

                item_topic_path = str(item.get("topic_path") or "").strip()
                item_topic_path_key = str(item.get("topic_path_key") or "").strip()
                item_context = {
                    "description": item.get("description"),
                    "doc_pos_page_index": item.get("doc_pos_page_index"),
                    "doc_pos_y0": item.get("doc_pos_y0"),
                    "item_topic_path": item_topic_path or None,
                    "item_topic_path_key": item_topic_path_key or None,
                    "level_basis": item.get("level_basis") or bucket.get("level_basis"),
                    "level_key": item.get("level_key") or bucket.get("level_key"),
                    "level_label": level_label,
                    "level_ordinal_high": bucket.get("level_ordinal_high"),
                    "level_ordinal_low": bucket.get("level_ordinal_low"),
                    "page_index": item.get("page_index"),
                    "sfi_uuid": sfi_uuid,
                    "statement_code": item.get("statement_code"),
                    "statement_type": item.get("statement_type"),
                }

                common_scoped_context = {
                    "subject_label": bucket.get("subject_label"),
                    "topic_path": item_topic_path or bucket_topic_path,
                    "topic_path_context_source": (
                        "item" if item_topic_path else "bucket_fallback"
                    ),
                    "topic_path_key": item_topic_path_key or bucket_topic_path_key,
                    "topic_path_key_context_source": (
                        "item" if item_topic_path_key else "bucket_fallback"
                    ),
                }

                if scope == "within_level":
                    scoped_context = {
                        **common_scoped_context,
                        "canon_order_path": item.get("canon_order_path"),
                        "numeric_order_missing_count": item.get(
                            "numeric_order_missing_count"
                        ),
                        "numeric_order_path": item.get("numeric_order_path"),
                        "order_index_within_parent": item.get(
                            "order_index_within_parent"
                        ),
                        "within_level_bucket_key": (
                            item.get("within_level_bucket_key")
                            or bucket.get("lp_bucket_key")
                        ),
                        "within_level_bucket_scope": bucket.get("bucket_scope"),
                        "within_level_bucket_used_fallback": bucket.get(
                            "within_level_bucket_used_fallback"
                        ),
                        "within_level_fallback_segments": item.get(
                            "within_level_fallback_segments"
                        )
                        or bucket.get("within_level_fallback_segments"),
                        "within_level_ordering_domain_key": (
                            item.get("within_level_bucket_key")
                            or bucket.get("lp_bucket_key")
                            or item.get("within_level_thread_key")
                            or bucket.get("lp_thread_key")
                            or item.get("topic_path_key")
                            or bucket_topic_path_key
                        ),
                        "within_level_thread_key": (
                            item.get("within_level_thread_key")
                            or bucket.get("lp_thread_key")
                        ),
                    }
                    entry = {
                        "item_context": item_context,
                        "within_level_context": scoped_context,
                    }
                else:
                    scoped_context = {
                        **common_scoped_context,
                        "cross_level_bucket_key": (
                            item.get("cross_level_bucket_key")
                            or bucket.get("lp_bucket_key")
                        ),
                        "cross_level_bucket_scope": bucket.get("bucket_scope"),
                        "cross_level_level_basis": (
                            item.get("level_basis") or bucket.get("level_basis")
                        ),
                        "cross_level_level_key": (
                            item.get("level_key") or bucket.get("level_key")
                        ),
                        "cross_level_level_label": level_label,
                        "cross_level_ordinal_high": bucket.get("level_ordinal_high"),
                        "cross_level_ordinal_low": bucket.get("level_ordinal_low"),
                        "cross_level_thread_key": (
                            item.get("cross_level_thread_key")
                            or bucket.get("lp_thread_key")
                        ),
                        "default_thread_key": (
                            item.get("default_thread_key")
                            or bucket.get("default_thread_key")
                        ),
                    }
                    entry = {
                        "item_context": item_context,
                        "cross_level_context": scoped_context,
                    }

                if sfi_uuid in index:
                    existing = index[sfi_uuid]
                    existing_context = existing.get(f"{scope}_context") or {}
                    raise ValueError(
                        f"Duplicate SFI UUID encountered while building LP SFI context "
                        f"index. Each SFI must appear at most once in a scoped bucket "
                        f"store. scope={scope!r}; sfi_uuid={sfi_uuid}; "
                        f"existing_level={existing.get('item_context', {}).get('level_label')!r}; "
                        f"existing_bucket={existing_context.get(f'{scope}_bucket_key')!r}; "
                        f"new_level={level_label!r}; "
                        f"new_bucket={scoped_context.get(f'{scope}_bucket_key')!r}."
                    )

                index[sfi_uuid] = entry

    return index


def _build_thread_map(
    by_level: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Organize buckets by thread key and level-range order.

    For cross-level inference we need to handle both:
      - single-level buckets (low==high) for true cross-level adjacency, and
      - banded buckets (low!=high) for cross-stage adjacency.

    The returned mapping groups buckets by thread key and sorts them by
    (level_ordinal_low, level_ordinal_high).

    Examples
    --------
    1. Same conceptual thread across adjacent single levels

        Given buckets shaped like:

            {
                "Grade 1": [
                    {
                        "level_ordinal_low": 1,
                        "level_ordinal_high": 1,
                        "lp_thread_key": "strand=reading",
                        "lp_bucket_key": "strand=reading",
                    }
                ],
                "Grade 2": [
                    {
                        "level_ordinal_low": 2,
                        "level_ordinal_high": 2,
                        "lp_thread_key": "strand=reading",
                        "lp_bucket_key": "strand=reading",
                    }
                ],
            }

        this function returns:

            {
                "strand=reading": [<Grade 1 bucket>, <Grade 2 bucket>]
            }

        `_collect_builds_towards_work_items()` can then decide whether to run a
        cross-level prompt for the Grade 1 -> Grade 2 pair.

    2. Banded stages remain in the same thread

        Buckets with ranges such as I-II (`level_ordinal_low=1`,
        `level_ordinal_high=2`) and III-VI (`level_ordinal_low=3`,
        `level_ordinal_high=6`) are grouped by the same `lp_thread_key` and sorted by
        their ordinal ranges. The next step decides whether to run cross-stage
        inference based on adjacency and config toggles.

    Parameters
    ----------
    by_level
        Dictionary mapping level labels to lists of bucket dictionaries, where each
        bucket contains information about a thread of standards within that level.

    Returns
    -------
    dict[str, list[dict[str, Any]]]
        A dictionary mapping thread keys to lists of bucket dictionaries, where each
        list is sorted by (level_ordinal_low, level_ordinal_high) to facilitate
        cross-level and cross-stage buildsTowards inference. Buckets without integer
        bounds are skipped with a warning. Bounded buckets without `lp_thread_key` are
        treated as malformed and raise ValueError.

    Raises
    ------
    ValueError
        If any bucket is missing a valid `lp_thread_key`, which is required for
        grouping buckets for cross-level inference. The error message includes examples
        of bucket keys that are missing the thread key to aid in debugging.
    """

    def _bound_or_last(value: Optional[int]) -> int:
        """Sort valid ordinal 0 before positive ordinals; place missing bounds last.

        Parameters
        ----------
        value
            The level ordinal bound to evaluate.

        Returns
        -------
        int
            The original value if it's a valid integer (including 0), or a large number
            (10^9) if the value is missing or invalid, to ensure that buckets with
            missing bounds are sorted after those with valid ordinals.
        """

        return value if isinstance(value, int) else 10**9

    missing_thread_key = 0
    missing_thread_key_examples: list[str] = []
    skipped_no_bounds = 0
    thread_map: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for level_buckets in by_level.values():
        for b in level_buckets:
            lo, hi = _level_bounds(b)

            if not isinstance(lo, int) or not isinstance(hi, int):
                skipped_no_bounds += 1
                continue

            thread_key = b.get("lp_thread_key")

            if not isinstance(thread_key, str) or not thread_key.strip():
                missing_thread_key += 1

                if len(missing_thread_key_examples) < 3:
                    missing_thread_key_examples.append(
                        str(b.get("bucket_key") or b.get("lp_bucket_key") or "")
                    )

                continue

            thread_key = thread_key.strip()
            thread_map[thread_key].append(b)

    for k in list(thread_map.keys()):
        thread_map[k] = sorted(
            thread_map[k],
            key=lambda b: (
                _bound_or_last(_level_bounds(b)[0]),
                _bound_or_last(_level_bounds(b)[1]),
                _bucket_topic_context(bucket=b),
                str(b.get("lp_bucket_key") or ""),
            ),
        )

    # Check the error condition first (missing thread_key) since it is fatal and should
    # not be masked by the non-fatal warning about skipped bounds.
    if missing_thread_key > 0:
        # The check is deferred until after full iteration so we can collect all
        # examples for a more actionable error message. Clear the partial `thread_map`
        # before raising so callers that catch the exception cannot accidentally use
        # incomplete data.
        thread_map.clear()
        raise ValueError(
            f"Missing or empty lp_thread_key in {missing_thread_key} bucket(s). "
            f"lp_thread_key is required to group buckets for cross-level inference and is "
            f"computed during bucketing (see _process_single_standard / "
            f"group_standards_for_learning_progressions). "
            f"If you're seeing this, the bucket objects were likely constructed without that "
            f"bucketing step or were mutated. "
            f"Examples: {missing_thread_key_examples}"
        )

    if skipped_no_bounds > 0:
        logger.warning(
            f"Skipped {skipped_no_bounds} buckets without integer level bounds "
            f"(missing level/stage ordinal data)."
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

    if rel_type == RELATES_TO:
        a, b = canon_str_pair(source, target)
        return rel_type, a, b

    return rel_type, source, target


def _canonicalize_candidate_for_dedupe(
    candidate: CandidateEdge,
) -> tuple[tuple[str, str, str], CandidateEdge]:
    """Canonicalize a candidate edge into the key used for deduplication.

    `buildsTowards` is directional, so endpoint order is preserved. `relatesTo` is
    associative, so endpoints are sorted using `canon_str_pair`. When a `relatesTo`
    candidate is reordered, the returned candidate preserves the original endpoint
    order in metadata for auditability.

    Parameters
    ----------
    candidate
        Raw candidate edge emitted by an LP inference phase.

    Returns
    -------
    tuple[tuple[str, str, str], CandidateEdge]
        The canonical dedupe key and a candidate whose endpoints match that key.
    """

    source = candidate.source_sfi_uuid
    target = candidate.target_sfi_uuid

    if candidate.rel_type == RELATES_TO:
        canonical_source, canonical_target = canon_str_pair(str(source), str(target))

        if canonical_source != str(source):
            metadata = dict(candidate.metadata)
            metadata.update(
                {
                    "dedupe_canonicalized_endpoints": True,
                    "dedupe_original_source_sfi_uuid": str(source),
                    "dedupe_original_target_sfi_uuid": str(target),
                }
            )
            source = UUID(canonical_source)
            target = UUID(canonical_target)
            candidate = CandidateEdge(
                confidence=candidate.confidence,
                evidence=candidate.evidence,
                inference_source=candidate.inference_source,
                inference_type=candidate.inference_type,
                llm_confidence=candidate.llm_confidence,
                metadata=metadata,
                rel_type=candidate.rel_type,
                source_sfi_uuid=source,
                target_sfi_uuid=target,
            )

    key = (candidate.rel_type, str(source), str(target))
    return key, candidate


def _collect_builds_towards_work_items(
    *, config: CreateKGConfig, thread_map: dict[str, list[dict[str, Any]]]
) -> list[tuple[str, dict[str, Any], dict[str, Any], str, Callable[..., Any]]]:
    """Collect and configure eligible adjacent pairs of buckets for inference.

    This function receives the thread-grouped output of `_build_thread_map()` and turns
    adjacent bucket pairs into concrete LLM work items. It does not call the LLM; it
    only decides which prompt type should be used for each adjacent pair.

    Examples
    --------
    1. Adjacent single levels

        If `thread_map["strand=reading"]` contains Grade 1 and Grade 2 buckets, and
        both buckets have `level_ordinal_low == level_ordinal_high`, the pair becomes
        a `cross_level_builds_towards` work item when
        `config.lp_cross_level_builds_towards` is enabled.

    2. Adjacent banded stages

        If the lower bucket covers Standards I-II (`low=1`, `high=2`) and the upper
        bucket covers Standards III-VI (`low=3`, `high=6`), the pair becomes a
        `cross_stage_builds_towards` work item when
        `config.lp_cross_stage_builds_towards` is enabled.

    3. Non-adjacent levels are skipped

        If the lower bucket ends at ordinal 1 and the next bucket starts at ordinal 3,
        the pair is skipped because the function only accepts
        `lower_high + 1 == upper_low`.

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

    def _levels_adjacent(*, lower: dict[str, Any], upper: dict[str, Any]) -> bool:
        """Determine if the levels of two buckets are adjacent based on their ordinals.

        Parameters
        ----------
        lower
            A bucket dictionary representing the lower level, which may contain level
            ordinal information.
        upper
            A bucket dictionary representing the upper level, which may contain level
            ordinal information.

        Returns
        -------
        bool
            True if the levels of the two buckets are adjacent (i.e., the high ordinal
            of the lower bucket is exactly one less than the low ordinal of the upper
            bucket), False otherwise. If the necessary ordinal information is not
            available or valid in either bucket, the function returns False, indicating
            that adjacency cannot be determined.
        """

        lo_lo, lo_hi = _level_bounds(lower)
        hi_lo, hi_hi = _level_bounds(upper)
        return (
            False
            if (not isinstance(lo_lo, int) or not isinstance(lo_hi, int))
            or (not isinstance(hi_lo, int) or not isinstance(hi_hi, int))
            else lo_hi + 1 == hi_lo
        )

    work_items: list[
        tuple[str, dict[str, Any], dict[str, Any], str, Callable[..., Any]]
    ] = []

    for thread_key, buckets in thread_map.items():
        seen_level_ranges: set[tuple[int, int]] = set()

        for bucket in buckets:
            low, high = _level_bounds(bucket)

            if isinstance(low, int) and isinstance(high, int):
                level_range = (low, high)

                if level_range in seen_level_ranges:
                    logger.warning(
                        f"Duplicate level bounds in cross-level buildsTowards thread "
                        f"{thread_key}: {level_range}. Only adjacent sorted pairs "
                        f"will be considered. bucket_key={bucket.get('lp_bucket_key')!r}"
                    )

                seen_level_ranges.add(level_range)

        for b_lo, b_hi in zip(buckets, buckets[1:]):
            lower_items = b_lo.get("items") or []
            upper_items = b_hi.get("items") or []

            if not _levels_adjacent(lower=b_lo, upper=b_hi) or (
                not lower_items or not upper_items
            ):
                continue

            both_single = _is_single_level_bucket(b_lo) and _is_single_level_bucket(
                b_hi
            )

            # Route to the correct prompt builder based on the config and bucket types.
            if both_single and config.lp_cross_level_builds_towards:
                inference_type = "cross_level_builds_towards"
                prompt_builder = cross_level_builds_towards
            elif not both_single and config.lp_cross_stage_builds_towards:
                inference_type = "cross_stage_builds_towards"
                prompt_builder = cross_stage_builds_towards
            else:
                # Feature is disabled in config for this specific bucket pairing.
                continue

            work_items.append((thread_key, b_lo, b_hi, inference_type, prompt_builder))

    return work_items


def _collect_relates_to_work_items(
    *, config: CreateKGConfig, subject_level_samples: dict[str, Any]
) -> list[dict[str, Any]]:
    """Collect eligible Phase 4 adjacent-level/adjacent-stage relatesTo work items.

    This function receives the output of `_prepare_subject_level_samples()`, which is
    organized as:

        subject_label -> (level_low, level_high) -> sampled prompt items + metadata

    It does not call the LLM. It only decides which same-subject adjacent level ranges
    should be compared, and which prompt builder should be used.

    A pair is eligible when:

      1. The two level ranges are adjacent (`lower_high + 1 == upper_low`);
      2. Both sides have non-empty sampled `items`;
      3. The relevant config toggle is enabled:
        - Adjacent single-level ranges use `cross_level_relates_to`;
        - Pairs where either side is banded use `cross_stage_relates_to`.

    Examples
    --------
    1. Adjacent single-grade levels produce a cross-level relatesTo work item

        Given sampled subject-level data:

            subject_level_samples = {
                "Reading": {
                    (1, 1): {
                        "level_label": "Grade 1",
                        "level_low": 1,
                        "level_high": 1,
                        "items": [
                            {
                                "sfi_uuid": "11111111-1111-1111-1111-111111111111",
                                "description": "Identify letter sounds.",
                                "thread_key": "strand=phonics",
                            }
                        ],
                        "sampled_count": 1,
                        "source_item_count": 5,
                        "source_bucket_count": 1,
                        "source_thread_keys": ["strand=phonics"],
                        "source_bucket_keys": ["strand=phonics"],
                        "sampled_sfi_uuids": [
                            "11111111-1111-1111-1111-111111111111"
                        ],
                        "max_items": 10,
                    },
                    (2, 2): {
                        "level_label": "Grade 2",
                        "level_low": 2,
                        "level_high": 2,
                        "items": [
                            {
                                "sfi_uuid": "22222222-2222-2222-2222-222222222222",
                                "description": "Read grade-level text fluently.",
                                "thread_key": "strand=fluency",
                            }
                        ],
                        "sampled_count": 1,
                        "source_item_count": 6,
                        "source_bucket_count": 1,
                        "source_thread_keys": ["strand=fluency"],
                        "source_bucket_keys": ["strand=fluency"],
                        "sampled_sfi_uuids": [
                            "22222222-2222-2222-2222-222222222222"
                        ],
                        "max_items": 10,
                    },
                }
            }

        If:

            config.lp_cross_level_relates_to = True

        then this function returns one work item:

            [
                {
                    "subject_label": "Reading",
                    "lo_low": 1,
                    "lo_high": 1,
                    "hi_low": 2,
                    "hi_high": 2,
                    "lower": subject_level_samples["Reading"][(1, 1)],
                    "upper": subject_level_samples["Reading"][(2, 2)],
                    "inference_type": "cross_level_relates_to",
                    "prompt_builder": cross_level_relates_to,
                }
            ]

        Later `_infer_cross_level_relates_to()` uses that work item to run
        bidirectional relatesTo inference between Grade 1 Reading and Grade 2 Reading.

    2. Adjacent banded stages produce a cross-stage relatesTo work item

        Given:

            subject_level_samples = {
                "Mathematics": {
                    (1, 2): {
                        "level_label": "Standards I-II",
                        "level_low": 1,
                        "level_high": 2,
                        "items": [...],
                    },
                    (3, 6): {
                        "level_label": "Standards III-VI",
                        "level_low": 3,
                        "level_high": 6,
                        "items": [...],
                    },
                }
            }

        The ranges are adjacent because `2 + 1 == 3`. Since at least one side is banded
        (`low != high`), this is not a single-level comparison.

        If:

            config.lp_cross_stage_relates_to = True

        the function returns a work item using:

            inference_type = "cross_stage_relates_to"
            prompt_builder = cross_stage_relates_to

        If `config.lp_cross_stage_relates_to = False`, the pair is skipped.

    3. Non-adjacent ranges are skipped

        Given:

            subject_level_samples = {
                "Science": {
                    (1, 1): {"level_label": "Grade 1", "items": [...]},
                    (3, 3): {"level_label": "Grade 3", "items": [...]},
                }
            }

        the function considers `(1, 1) -> (3, 3)` but skips it because:

            lo_high + 1 != hi_low
            1 + 1 != 3

        No work item is returned for this subject. This prevents Phase 4 from inventing
        cross-level associations across a missing Grade 2 level.

    4. Empty sampled sides are skipped

        Given:

            subject_level_samples = {
                "Reading": {
                    (1, 1): {"level_label": "Grade 1", "items": []},
                    (2, 2): {"level_label": "Grade 2", "items": [...]},
                }
            }

        the adjacent level pair is skipped because the lower side has no sampled prompt
        items. This avoids sending empty item lists to the LLM.

    5. Single-level pairs obey the cross-level toggle

        Given adjacent single-level ranges:

            (1, 1) -> (2, 2)

        the function only emits a work item when:

            config.lp_cross_level_relates_to = True

        If that flag is False, the pair is skipped even though the levels are adjacent.

    6. Banded pairs obey the cross-stage toggle

        Given adjacent ranges where either side is banded:

            (1, 2) -> (3, 3)
            (1, 1) -> (2, 4)
            (1, 2) -> (3, 6)

        the function only emits a work item when:

            config.lp_cross_stage_relates_to = True

        If that flag is False, the pair is skipped.

    7. More than two levels produce one work item per adjacent pair

        Given:

            subject_level_samples = {
                "Reading": {
                    (1, 1): {"level_label": "Grade 1", "items": [...]},
                    (2, 2): {"level_label": "Grade 2", "items": [...]},
                    (3, 3): {"level_label": "Grade 3", "items": [...]},
                }
            }

        and `config.lp_cross_level_relates_to = True`, the function returns two work
        items:

            Grade 1 -> Grade 2
            Grade 2 -> Grade 3

        It does not compare Grade 1 directly to Grade 3.

    8. Different subjects are never compared to each other in Phase 4

        Given:

            subject_level_samples = {
                "Reading": {
                    (1, 1): {"level_label": "Grade 1", "items": [...]},
                    (2, 2): {"level_label": "Grade 2", "items": [...]},
                },
                "Writing": {
                    (1, 1): {"level_label": "Grade 1", "items": [...]},
                    (2, 2): {"level_label": "Grade 2", "items": [...]},
                },
            }

        the function may create:

            Reading Grade 1 -> Reading Grade 2
            Writing Grade 1 -> Writing Grade 2

        It will not create:

            Reading Grade 1 -> Writing Grade 2

        Cross-subject or cross-strand relationships are handled by Phase 3 within-level
        relatesTo, not by Phase 4.

    Parameters
    ----------
    config
        The knowledge graph run configuration.
    subject_level_samples
        Dictionary mapping subjects to level boundaries and bucket data.

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
                if not config.lp_cross_level_relates_to:
                    continue

                inference_type = "cross_level_relates_to"
                prompt_builder = cross_level_relates_to
            else:
                if not config.lp_cross_stage_relates_to:
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


def _compute_bucket_keys(
    *,
    default_thread_key: str | None,
    fallback_segments: list[str] | None = None,
    normalized_level_key: str,
    roles: list[NodeRole] | None,
    subject_label: str,
    topic_path_parts: list[dict[str, Any]],
) -> tuple[str, str]:
    """Compute a bucket key and thread key from configured hierarchy roles.

    The caller decides whether `roles` represents within-level bucketing roles or
    cross-level thread roles. If roles are provided, only matching entries in
    `topic_path_parts` are used and `default_thread_key` is ignored. If roles are None,
    `default_thread_key` is used when non-empty.

    If no hierarchy/default key is available, configured `fallback_segments` are used
    next. If those are also empty, a level-specific unthreaded sentinel is returned.
    The sentinel includes the best available local context so unmatched items do not
    collapse into one broad same-subject/same-level bucket.

    Examples
    --------
    1. Within-level Senegal reading bucket from strand

        The Senegal reading config uses:

            lp_within_level_bucket_roles = ["strand"]
            lp_within_level_fallback_fields = ["statement_type"]

        Given:

            topic_path_parts = [
                {"role": "strand", "label": "Communication écrite - Production d'écrits"},
                {"role": "substage", "label": "Palier 1 - Production d'écrits"},
                {"role": "subtopic", "label": "Grammaire"},
            ]

        Calling:

            _compute_bucket_keys(
                default_thread_key="strand=communication_ecrite_production_d_ecrits|substage=palier_1",
                fallback_segments=["statement_type=grammaire"],
                normalized_level_key="ce1",
                roles=["strand"],
                subject_label="Communication écrite - Production d'écrits",
                topic_path_parts=topic_path_parts,
            )

        returns:

            (
                "strand=communication_ecrite_production_d_ecrits",
                "strand=communication_ecrite_production_d_ecrits",
            )

        The fallback is ignored because the configured hierarchy role produced a key.

    2. Within-level fallback when no configured role is present

        Given:

            topic_path_parts = [
                {"role": "substage", "label": "Palier 1 - Communication écrite"},
            ]

        and:

            fallback_segments = ["statement_type=grammaire"]

        Calling with:

            roles=["strand"]

        returns:

            (
                "statement_type=grammaire",
                "statement_type=grammaire",
            )

        This keeps no-strand items available for Phase 1 sequencing without placing
        them in an unspecified cross-subject bucket.

    3. Default thread key when roles is None

        Given:

            default_thread_key = "strand=lecture|substage=palier_1_lecture"

        Calling:

            _compute_bucket_keys(
                default_thread_key=default_thread_key,
                fallback_segments=["statement_type=objectif_specifique"],
                normalized_level_key="ce1",
                roles=None,
                subject_label="Lecture",
                topic_path_parts=[],
            )

        returns:

            (
                "strand=lecture|substage=palier_1_lecture",
                "strand=lecture|substage=palier_1_lecture",
            )

        The fallback is ignored because the default thread key is available.

    4. Cross-level key from multiple configured roles

        Given:

            topic_path_parts = [
                {"role": "strand", "label": "Lecture"},
                {"role": "substage", "label": "Palier 2 - Lecture"},
                {"role": "week", "label": "Semaine 12"},
            ]

        Calling with:

            roles=["strand", "substage"]

        returns:

            (
                "strand=lecture|substage=palier_2_lecture",
                "strand=lecture|substage=palier_2_lecture",
            )

        The output preserves the configured role order, not necessarily the order in
        `topic_path_parts`.

    5. Partial role match

        Given:

            topic_path_parts = [
                {"role": "substage", "label": "Palier 2 - Lecture"},
            ]

        Calling with:

            roles=["strand", "substage"]

        returns:

            (
                "substage=palier_2_lecture",
                "substage=palier_2_lecture",
            )

        The current function treats the role list as "use any matching roles in this
        order", not "require all roles".

    6. Unthreaded sentinel

        Given no matching roles, no fallback segments, and no default thread key:

            topic_path_parts = [
                {"role": "week", "label": "Semaine 12"},
            ]

        Calling with:

            normalized_level_key="ce1"
            subject_label="UNSPECIFIED_SUBJECT"
            roles=["strand"]

        returns something like:

            (
                "__unthreaded__::unspecified_subject::ce1::week=semaine_12",
                "__unthreaded__::unspecified_subject::ce1::week=semaine_12",
            )

        Including the level in the sentinel prevents these weakly threaded items from
        being matched across adjacent levels or stages.

    Parameters
    ----------
    default_thread_key
        The source/default thread key from `progression_context.thread_key`. Used
        directly only when `roles` is None. When `roles` is provided and no role-based
        key is found, this value may still be included as local context inside the
        unthreaded sentinel, but it is not used as the bucket key.
    fallback_segments
        A list of pre-computed fallback segments to use when `roles` is provided but
        does not yield any key segments. These are typically derived from
        curriculum-specific source fields (e.g., statement type) that can provide some
        signal for within-level bucketing when hierarchy-based roles are not available.
    normalized_level_key
        The normalized level key for the current bucket, used in the unthreaded
        sentinel when no other key can be computed.
    roles
        A list of hierarchy roles to look for in `topic_path_parts` to construct the
        key. If None, no role-based key segments are extracted and `default_thread_key`
        is used instead.
    subject_label
        The subject label for the current bucket, used in the unthreaded sentinel to
        help distinguish buckets across subjects when no other key can be computed.
    topic_path_parts
        A list of topic path part dictionaries, each potentially containing a "role"
        and "label" that can be used to construct key segments when `roles` is provided.

    Returns
    -------
    tuple[str, str]
        A tuple of (bucket_key, thread_key) where `bucket_key` is the computed key
        based on the provided roles and topic path parts, or the default thread key, or
        the fallback segments, or a generated unthreaded sentinel if none of the above
        yield a key.
    """

    role_values = [role.value for role in roles or []]
    role_values = [role for role in role_values if role]

    if role_values:
        parts_by_role: dict[str, list[str]] = {}
        roles_set = set(role_values)

        for tpp in topic_path_parts or []:
            if not isinstance(tpp, dict):
                continue

            role = tpp["role"]
            label = str(tpp.get("label") or "").strip()

            if label and role in roles_set:
                val = normalize_key_token(label=label, separator="_")

                if val:
                    parts_by_role.setdefault(role, []).append(val)

        segments: list[str] = []

        for role in role_values:
            for val in parts_by_role.get(role, []):
                segments.append(f"{role}={val}")

        key = "|".join(segments) if segments else None
    else:
        raw_default = str(default_thread_key or "").strip()
        key = raw_default or None

    if key is None and fallback_segments:
        fallback_key = "|".join(
            str(seg).strip() for seg in fallback_segments if str(seg).strip()
        )
        key = fallback_key or None

    if key is None:
        local_context = (
            str(default_thread_key or "").strip()
            or _topic_path_signature(topic_path_parts)
            or "no_topic_path"
        )
        safe_subject = (
            normalize_key_token(label=subject_label, separator="_")
            or "unspecified_subject"
        )
        safe_level = (
            normalize_key_token(label=normalized_level_key, separator="_")
            or "unspecified_level"
        )
        key = f"__unthreaded__::{safe_subject}::{safe_level}::{local_context}"

    # Currently identical but separated for future policy expansion.
    return key, key


def _create_new_bucket(
    *,
    bucket_key_value: str,
    bucket_scope: str,
    bucket_used_fallback: bool,
    cleaned_fallbacks: list[str],
    default_thread_key: str | None,
    level_basis: str,
    level_hi: int,
    level_key: str | None,
    level_label: str,
    level_lo: int,
    subject_label: str,
    thread_key_value: str,
    topic_key_str: str,
    topic_path: str,
) -> dict[str, Any]:
    """Create a new learning-progression inference bucket dictionary.

    Constructs the base dictionary payload for a bucket, populating basic metadata
    along with conditional fallback metadata if the scope is `within_level`.

    Parameters
    ----------
    bucket_key_value
        The computed key for the bucket in the given scope.
    bucket_scope
        The bucket scope (`"within_level"` or `"cross_level"`).
    bucket_used_fallback
        True if the bucket key was produced from fallback segments.
    cleaned_fallbacks
        Cleaned source-field fallback segments.
    default_thread_key
        The source/default thread key from the progression context.
    level_basis
        How level ordinals were resolved.
    level_hi
        Highest level ordinal represented by the bucket.
    level_key
        Human-readable source level key.
    level_label
        The level label used as the outer key in the bucket store.
    level_lo
        Lowest level ordinal represented by the bucket.
    subject_label
        Subject-like label for the bucket.
    thread_key_value
        Thread key associated with this bucket.
    topic_key_str
        Cleaned canonical topic path key.
    topic_path
        Cleaned topic path string.

    Returns
    -------
    dict[str, Any]
        The newly created bucket.
    """

    default_thread_key_s = str(default_thread_key or "").strip() or None
    level_key_str = (
        level_key.strip() if isinstance(level_key, str) and level_key.strip() else None
    )
    grade_key = (
        level_key_str
        if level_basis in {"grade_ordinals", "level_label_map_grade_key"}
        else None
    )
    stage_key = (
        level_key_str
        if level_basis in {"stage_ordinals", "level_label_map_stage_key"}
        else None
    )
    bucket: dict[str, Any] = {
        "bucket_key": f"{level_label}::{bucket_key_value}",
        "bucket_scope": bucket_scope,
        "default_thread_key": default_thread_key_s,
        "effective_bucket_key": bucket_key_value,
        "grade_key": grade_key,
        "items": [],
        "level_basis": level_basis,
        "level_key": level_key_str,
        "level_label": level_label,
        "level_ordinal": level_lo,
        "level_ordinal_high": level_hi,
        "level_ordinal_low": level_lo,
        "lp_bucket_key": bucket_key_value,
        "lp_thread_key": thread_key_value,
        "stage_key": stage_key,
        "subject_label": subject_label,
        "topic_path_examples": [topic_path] if topic_path else [],
        "topic_path_keys": [topic_key_str] if topic_key_str else [],
    }

    if bucket_scope == "within_level":
        bucket["within_level_bucket_used_fallback"] = bucket_used_fallback
        bucket["within_level_fallback_segments"] = (
            cleaned_fallbacks if bucket_used_fallback else []
        )

    return bucket


def _dedupe_winner_sort_key(*, candidate: CandidateEdge) -> tuple[Any, ...]:
    """Return a deterministic sort key for selecting a dedupe winner.

    Higher confidence wins first. Ties prefer earlier pipeline phases, then earlier raw
    candidate order, then stable string fields so the result is deterministic even when
    all substantive scores are identical.

    Parameters
    ----------
    candidate
        Candidate edge in a single canonical dedupe group.

    Returns
    -------
    tuple[Any, ...]
        Sort key suitable for `max()`.
    """

    metadata = candidate.metadata if isinstance(candidate.metadata, dict) else {}
    phase_raw = metadata.get("phase")

    try:
        phase = 999 if phase_raw is None else int(phase_raw)
    except (TypeError, ValueError):
        phase = 999

    order_raw = metadata.get("candidate_order_index")

    try:
        candidate_order_index = 10**9 if order_raw is None else int(order_raw)
    except (TypeError, ValueError):
        candidate_order_index = 10**9

    return (
        float(candidate.confidence),
        -phase,
        -candidate_order_index,
        str(candidate.inference_type),
        str(candidate.source_sfi_uuid),
        str(candidate.target_sfi_uuid),
        str(metadata.get("candidate_uid") or ""),
    )


def _dedupe_edges(
    edges: list[CandidateEdge],
) -> tuple[
    list[CandidateEdge],
    dict[tuple[str, str, str], CandidateEdge],
    int,
    dict[tuple[str, str, str], list[dict[str, Any]]],
]:
    """Deduplicate by `(rel_type, canonical endpoints)` and audit all candidates.

    Canonicalization is direction-aware: for directed `buildsTowards` edges the
    original `(source, target)` order is preserved, while for associative `relatesTo`
    edges the endpoints are lexicographically ordered via `canon_str_pair` so that
    `(A, B)` and `(B, A)` are treated as the same edge.

    The winning candidate for each dedupe group receives a `dedupe` metadata block with
    every candidate source considered for that group. This lets reviewers inspect which
    phase/source won and which duplicate candidates were dropped.

    Parameters
    ----------
    edges
        Candidate edges from all inference phases.

    Returns
    -------
    tuple
        A tuple containing:
        1. Deduplicated CandidateEdge instances, each enriched with dedupe audit
            metadata.
        2. A mapping from canonical key `(rel_type, source_uuid, target_uuid)` to the
            winning CandidateEdge.
        3. The number of edges dropped during deduplication.
        4. A mapping from canonical key to candidate-source audit records.
    """

    groups: dict[tuple[str, str, str], list[CandidateEdge]] = defaultdict(list)

    for edge in edges:
        key, canonical_edge = _canonicalize_candidate_for_dedupe(edge)
        groups[key].append(canonical_edge)

    audit_by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    best: dict[tuple[str, str, str], CandidateEdge] = {}

    for key, group in groups.items():
        winner_index, _ = max(
            enumerate(group),
            key=lambda pair: _dedupe_winner_sort_key(candidate=pair[1]),
        )
        audit_records: list[dict[str, Any]] = []

        for idx, candidate in enumerate(group):
            disposition = "dedupe_winner" if idx == winner_index else "dropped_dedupe"
            candidate_metadata = (
                candidate.metadata if isinstance(candidate.metadata, dict) else {}
            )

            audit_records.append(
                {
                    "candidate_order_index": candidate_metadata.get(
                        "candidate_order_index"
                    ),
                    "candidate_uid": candidate_metadata.get("candidate_uid"),
                    "confidence": float(candidate.confidence),
                    "dedupe_key": list(key),
                    "disposition": disposition,
                    "evidence": candidate.evidence,
                    "inference_source": candidate.inference_source,
                    "inference_type": candidate.inference_type,
                    "llm_confidence": candidate.llm_confidence,
                    "metadata": candidate_metadata,
                    "phase": candidate_metadata.get("phase"),
                    "rel_type": candidate.rel_type,
                    "source_sfi_uuid": str(candidate.source_sfi_uuid),
                    "target_sfi_uuid": str(candidate.target_sfi_uuid),
                }
            )

        winner = group[winner_index]
        winner_metadata = dict(winner.metadata)
        winner_metadata["dedupe"] = {
            "candidate_count": len(group),
            "candidate_sources": audit_records,
            "dedupe_key": list(key),
            "dropped_count": len(group) - 1,
            "winner_confidence": float(winner.confidence),
            "winner_inference_type": winner.inference_type,
        }
        winner = _replace_candidate_metadata(candidate=winner, metadata=winner_metadata)
        audit_by_key[key] = audit_records
        best[key] = winner

    deduped = list(best.values())
    dropped = len(edges) - len(deduped)
    return deduped, best, dropped, audit_by_key


def _emit_relationship(
    *,
    candidate: CandidateEdge,
    config: CreateKGConfig,
    doc_key: str,
    sfi_context_index: Optional[dict[str, dict[str, Any]]] = None,
) -> Relationship:
    """Convert a CandidateEdge to an LC KG Relationship.

    The emitted metadata includes the candidate evidence, inference metadata, dedupe
    audit metadata when present, the full source/target SFI context blocks, and the
    scoped source/target context that matches the candidate's inference phase.

    Parameters
    ----------
    candidate
        Candidate edge to convert into a Relationship.
    config
        The knowledge graph run configuration.
    doc_key
        The document key for this export, included in the relationship ID namespace
        string for consistency with the rest of the pipeline.
    sfi_context_index
        Optional combined SFI context index from `_build_combined_sfi_context_index()`.

    Returns
    -------
    Relationship
        Relationship instance constructed from the candidate edge.
    """

    if candidate.inference_type in WITHIN_LEVEL_INFERENCE_TYPES:
        inference_context_scope: Optional[SFIContextScope] = "within_level"
    elif candidate.inference_type in CROSS_LEVEL_INFERENCE_TYPES:
        inference_context_scope = "cross_level"
    else:
        inference_context_scope = None

    metadata = dict(candidate.metadata)
    metadata.update(
        {
            "confidence": candidate.confidence,
            "evidence": candidate.evidence,
            "inference_context_scope": inference_context_scope,
            "inference_source": candidate.inference_source,
            "inference_type": candidate.inference_type,
        }
    )

    if sfi_context_index:
        source_context = sfi_context_index.get(str(candidate.source_sfi_uuid))
        target_context = sfi_context_index.get(str(candidate.target_sfi_uuid))
        metadata["source_sfi_context"] = source_context
        metadata["target_sfi_context"] = target_context
        metadata["source_sfi_inference_context"] = _select_sfi_inference_context(
            context=source_context, scope=inference_context_scope
        )
        metadata["target_sfi_inference_context"] = _select_sfi_inference_context(
            context=target_context, scope=inference_context_scope
        )

    rid = uuid5(
        config.namespace_uuid,
        f"lc:curriculum:{doc_key}:rel:{candidate.rel_type}:{candidate.source_sfi_uuid}:{candidate.target_sfi_uuid}",
    )
    return Relationship(
        attribution_statement=config.as_attribution_statement,
        author=config.as_author,
        date_created=None,
        date_modified=None,
        description="",
        identifier=rid,
        license=config.as_license,
        metadata=metadata,
        provider=config.as_provider,
        relationship_type=candidate.rel_type,
        source_entity="StandardsFrameworkItem",
        source_entity_key="case_identifier_uuid",
        source_entity_value=str(candidate.source_sfi_uuid),
        target_entity="StandardsFrameworkItem",
        target_entity_key="case_identifier_uuid",
        target_entity_value=str(candidate.target_sfi_uuid),
    )


def _evenly_spaced_indexes(*, max_items: int, total_items: int) -> list[int]:
    """Return deterministic, order-preserving indexes spread across a sequence.

    This helper is used when Phase 3 compares a subject-like group represented by one
    large bucket. Taking the first N items from a long curriculum strand over
    represents the early part of the source sequence. Evenly spaced indexes preserve
    deterministic ordering while giving the prompt coverage across the beginning,
    middle, and end of the bucket.

    Parameters
    ----------
    max_items
        Maximum number of indexes to return.
    total_items
        Number of available ordered items.

    Returns
    -------
    list[int]
        Zero-based indexes into the original item list. The list is sorted and contains
        no duplicates.
    """

    if max_items <= 0 or total_items <= 0:
        return []

    if total_items <= max_items:
        return list(range(total_items))

    if max_items == 1:
        return [0]

    last_index = total_items - 1
    indexes = [round(i * last_index / (max_items - 1)) for i in range(max_items)]

    # With total_items > max_items, the step is > 1, so duplicates should not occur.
    # Keep this defensive de-duplication anyway so the function remains safe if reused.
    deduped: list[int] = []
    seen: set[int] = set()

    for idx in indexes:
        idx = max(0, min(last_index, int(idx)))

        if idx not in seen:
            deduped.append(idx)
            seen.add(idx)

    return deduped


def _filter_builds_towards_within_level_order(
    *, edges: list[CandidateEdge], within_sfi_index: dict[str, dict[str, Any]]
) -> tuple[list[CandidateEdge], list[CandidateEdge]]:
    """Drop Phase-1 buildsTowards edges that contradict within-level document order.

    Phase 1 inference is scoped by the within-level bucket. Therefore order comparisons
    use `within_level_ordering_domain_key`, not item-level `topic_path_key`, as the
    comparable-domain gate. This lets broad but intentional buckets such as
    `strand=lecture` compare items across finer paliers/weeks while still avoiding
    comparisons between unrelated ordering domains.

    Parameters
    ----------
    edges
        Candidate edges expected to be Phase 1 within-level buildsTowards edges.
    within_sfi_index
        Scoped SFI index built from `by_within_level` by `_build_scoped_sfi_index()`.

    Returns
    -------
    tuple[list[CandidateEdge], list[CandidateEdge]]
        `(kept_edges, dropped_edges)`.
    """

    def _compare_within_level_order(
        *, source_context: dict[str, Any], target_context: dict[str, Any]
    ) -> Optional[int]:
        """Compare two scoped SFI contexts by within-level curriculum order.

        Parameters
        ----------
        source_context
            Source SFI context entry containing `item_context` and
            `within_level_context`.
        target_context
            Target SFI context entry containing `item_context` and
            `within_level_context`.

        Returns
        -------
        Optional[int]
            -1 if source is before target, 0 if equal, 1 if source is after target, or
            None if the order cannot be determined safely.
        """

        source_item = source_context.get("item_context") or {}
        source_within = source_context.get("within_level_context") or {}
        target_item = target_context.get("item_context") or {}
        target_within = target_context.get("within_level_context") or {}

        if source_item.get("level_label") != target_item.get("level_label"):
            return None

        source_domain = (
            source_within.get("within_level_ordering_domain_key")
            or source_within.get("topic_path_key")
            or source_item.get("item_topic_path_key")
        )
        target_domain = (
            target_within.get("within_level_ordering_domain_key")
            or target_within.get("topic_path_key")
            or target_item.get("item_topic_path_key")
        )

        if source_domain != target_domain:
            return None

        src_missing = int(source_within.get("numeric_order_missing_count") or 0)
        tgt_missing = int(target_within.get("numeric_order_missing_count") or 0)
        src_path = source_within.get("numeric_order_path") or []
        tgt_path = target_within.get("numeric_order_path") or []

        if src_missing == 0 and tgt_missing == 0 and src_path and tgt_path:
            return -1 if src_path < tgt_path else (1 if src_path > tgt_path else 0)

        src_page = source_item.get("doc_pos_page_index")
        src_page = source_item.get("page_index") if src_page is None else src_page

        tgt_page = target_item.get("doc_pos_page_index")
        tgt_page = target_item.get("page_index") if tgt_page is None else tgt_page

        if not isinstance(src_page, int) or not isinstance(tgt_page, int):
            return None

        src_y0 = source_item.get("doc_pos_y0")
        tgt_y0 = target_item.get("doc_pos_y0")
        src_key = (src_page, float(src_y0) if isinstance(src_y0, (int, float)) else 0.0)
        tgt_key = (tgt_page, float(tgt_y0) if isinstance(tgt_y0, (int, float)) else 0.0)

        return -1 if src_key < tgt_key else (1 if src_key > tgt_key else 0)

    kept: list[CandidateEdge] = []
    dropped: list[CandidateEdge] = []

    for edge in edges:
        source_context = within_sfi_index.get(str(edge.source_sfi_uuid))
        target_context = within_sfi_index.get(str(edge.target_sfi_uuid))

        if not source_context or not target_context:
            kept.append(edge)
            continue

        comparison = _compare_within_level_order(
            source_context=source_context, target_context=target_context
        )

        if comparison is None:
            kept.append(edge)
            continue

        if comparison >= 0:
            dropped.append(edge)
        else:
            kept.append(edge)

    return kept, dropped


def _finalize_bucket_store(
    store: DefaultDict[str, DefaultDict[str, dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, dict[str, Any]]]]:
    """Finalize the bucket store by sorting items within each bucket and organizing the
    data into the final output structures.

    This function mutates each bucket dictionary in place by replacing
    `bucket["items"]` with its sorted version and rebuilding
    `bucket["topic_path_examples"]`/`bucket["topic_path_keys"]` from the sorted item
    list; the returned views reference the same bucket objects.

    Examples
    --------
    1. Finalize a within-level Senegal reading bucket

        Suppose `_process_single_standard()` has grouped two CE1 Orthographe items into
        the same within-level fallback bucket:

            store = defaultdict(dict)
            store["CE1"]["statement_type=orthographe"] = {
                "bucket_key": "CE1::statement_type=orthographe",
                "bucket_scope": "within_level",
                "lp_bucket_key": "statement_type=orthographe",
                "lp_thread_key": "statement_type=orthographe",
                "subject_label": "UNSPECIFIED_SUBJECT",
                "topic_path_examples": [
                    "substage:Palier 2 - Communication écrite",
                ],
                "topic_path_keys": [
                    "substage=palier_2_communication_ecrite",
                ],
                "items": [
                    {
                        "sfi_uuid": "uuid-week-16",
                        "description": "Orthographier des mots fréquents...",
                        "numeric_order_missing_count": 0,
                        "numeric_order_path": [4, 2, 16, 3],
                        "doc_pos_page_index": 43,
                        "doc_pos_y0": 703.3,
                        "order_index_within_parent": 3,
                    },
                    {
                        "sfi_uuid": "uuid-week-15",
                        "description": "Orthographier des mots fréquents...",
                        "numeric_order_missing_count": 0,
                        "numeric_order_path": [4, 2, 15, 3],
                        "doc_pos_page_index": 42,
                        "doc_pos_y0": 55.0,
                        "order_index_within_parent": 3,
                    },
                ],
            }

        Calling:

            by_level, by_bucket_key = _finalize_bucket_store(store)

        returns a level-oriented view:

            by_level["CE1"] == [
                {
                    "bucket_key": "CE1::statement_type=orthographe",
                    "items": [
                        {"sfi_uuid": "uuid-week-15", ...},
                        {"sfi_uuid": "uuid-week-16", ...},
                    ],
                    ...
                }
            ]

        and a key-oriented view:

            by_bucket_key["statement_type=orthographe"]["CE1"] is by_level["CE1"][0]

        The items are sorted by `numeric_order_path`, so week 15 comes before week 16
        even if the input list arrived in the opposite order.

    2. Prefer complete numeric order paths over fallback document position

        If one item has a complete numeric path and another item is missing part of its
        numeric path:

            complete = {
                "sfi_uuid": "uuid-complete",
                "numeric_order_missing_count": 0,
                "numeric_order_path": [4, 1, 6, 3],
                "doc_pos_page_index": 50,
                "doc_pos_y0": 100.0,
            }
            incomplete = {
                "sfi_uuid": "uuid-incomplete",
                "numeric_order_missing_count": 1,
                "numeric_order_path": [4, 1, 999999],
                "doc_pos_page_index": 10,
                "doc_pos_y0": 50.0,
            }

        the complete item sorts first because the sort key begins with
        `numeric_order_missing_count`. Page/bbox position is only a fallback after
        numeric curriculum order quality has been considered.

    3. Sort buckets within a level by aggregate topic context

        Suppose CE1 has two finalized buckets:

            store["CE1"]["strand=lecture"] = {
                "lp_bucket_key": "strand=lecture",
                "topic_path_examples": ["strand:Lecture -> substage:Palier 1"],
                "items": [...],
            }
            store["CE1"]["strand=production_d_ecrits"] = {
                "lp_bucket_key": "strand=production_d_ecrits",
                "topic_path_examples": [
                    "strand:Production d'écrits -> subtopic:Grammaire",
                    "strand:Production d'écrits -> subtopic:Vocabulaire",
                ],
                "items": [...],
            }

        `_finalize_bucket_store()` sorts the bucket list using
        `_bucket_topic_context(bucket)` and then `lp_bucket_key`, giving stable,
        human-readable bucket order for reports and downstream inference planning.

    4. Same approach works for within-level and cross-level stores

        The function does not care whether the input store came from
        `within_level_buckets` or `cross_level_buckets`.

        For within-level buckets, the second-level key is usually a within-level
        inference bucket such as:

            "strand=lecture"
            "statement_type=orthographe"

        For cross-level buckets, the second-level key is usually a cross-level thread
        key such as:

            "strand=lecture|substage=palier_1_lecture"

        In both cases, the return shape is:

            by_level[level_label] -> list of sorted bucket dictionaries
            by_bucket_key[key][level_label] -> bucket dictionary

    Parameters
    ----------
    store
        A nested dict structure where the first level keys are level labels, the second
        level keys are thread or bucket keys, and the values are dictionaries
        containing bucket information and lists of items (standards).

    Returns
    -------
    tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, dict[str, Any]]]]
        A tuple containing:
            1. A dictionary mapping level labels to lists of bucket dictionaries, where
                each bucket dictionary contains information about the bucket and a
                sorted list of items (standards) belonging to that bucket. The buckets
                within each level are sorted by their topic path and bucket key to
                ensure a stable order for the LLM.
            2. A nested dictionary mapping thread or bucket keys to level labels and
                their corresponding bucket dictionaries. This structure allows for
                quick lookup of buckets by thread or bucket key and level label, which
                can be useful for certain inference strategies that need to access
                standards grouped by thread across levels.
    """

    by_level: dict[str, list[dict[str, Any]]] = {}
    by_bucket_key: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    for level_label, per_thread in store.items():
        level_buckets: list[dict[str, Any]] = []

        for tkey, b in per_thread.items():
            # Sort items by numeric_order_path (document position resolved to integer
            # order indices) to preserve intended pedagogical sequence within each
            # configured inference bucket.
            b["items"] = sorted(
                b["items"],
                key=lambda s: (
                    int(s.get("numeric_order_missing_count") or 0),
                    s.get("numeric_order_path") or [],
                    _item_doc_position_key(item=s),
                    _sort_key_for_bucket_sfi(s),
                ),
            )

            # Rebuild aggregate topic context from the sorted item list so prompts and
            # reports show representative paths in the same pedagogical order as the
            # bucket items. `_get_or_create_bucket()` maintains these fields while
            # building buckets, but its insertion order can differ from final sorted
            # item order when items are encountered out of curriculum sequence.
            topic_path_examples: list[str] = []
            topic_path_keys: list[str] = []

            for item in b["items"]:
                topic_path = str(item.get("topic_path") or "").strip()

                if topic_path and topic_path not in topic_path_examples:
                    topic_path_examples.append(topic_path)

                topic_path_key = str(item.get("topic_path_key") or "").strip()

                if topic_path_key and topic_path_key not in topic_path_keys:
                    topic_path_keys.append(topic_path_key)

            b["topic_path_examples"] = topic_path_examples
            b["topic_path_keys"] = topic_path_keys

            level_buckets.append(b)
            by_bucket_key[tkey][level_label] = b

        by_level[level_label] = sorted(
            level_buckets,
            key=lambda x: (
                _bucket_topic_context(bucket=x),
                x.get("lp_bucket_key") or "",
            ),
        )

    return by_level, dict(by_bucket_key)


def _finalize_lp_export(
    *,
    academic_standards: AcademicStandardsExport,
    builds_rels: list[Relationship],
    config: CreateKGConfig,
    ctx: ExportContext,
    drops: dict[str, Any],
    kg_dirs: KGDirs,
    lp_stats: dict[str, int],
    provenance_rows: list[dict[str, Any]],
    relates_rels: list[Relationship],
) -> LearningProgressionsExport:
    """Verify, persist, and wrap LearningProgressions export artifacts.

    Parameters
    ----------
    academic_standards
        The exported Academic Standards KG artifacts, used here for building the graph
        bundle with nodes.
    builds_rels
        The list of emitted buildsTowards relationships after processing and filtering.
    config
        KG export configuration.
    ctx
        ExportContext (doc_key).
    drops
        The dictionary of dropped items collected during the bucketing process,
        included in the final report.
    kg_dirs
        Output directories.
    lp_stats
        The dictionary of statistics about the emitted relationships and filtering
        outcomes, included in the final report.
    provenance_rows
        The list of provenance dictionaries for all candidate edges, enriched with
        final disposition after filtering and deduplication. This is included in the
        export for transparency and debugging purposes.
    relates_rels
        The list of emitted relatesTo relationships after processing and filtering.

    Returns
    -------
    LearningProgressionsExport
        The wrapped export object.
    """

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

    # Combine builds_rels and relates_rels for the relationship loop.
    for r in builds_rels + relates_rels:
        rels.append(
            {
                "id": str(r.identifier),
                "type": r.relationship_type,
                "start": r.source_entity_value,
                "end": r.target_entity_value,
                "properties": r.model_dump(mode="json"),
            }
        )

    lp_kg = {
        "doc_key": ctx.doc_key,
        "export_dialect": config.as_export_dialect,
        "generated_at": generated_at,
        "graph_type": "learning_progressions",
        "nodes": nodes,
        "relationships": rels,
    }
    write_to_json(
        fp=kg_dirs.learning_progressions / "learning_progressions_kg.json",
        json_info=lp_kg,
    )

    report = {
        "doc_key": ctx.doc_key,
        "counts": lp_stats,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "phase_toggles": {
            "cross_level_builds_towards": config.lp_cross_level_builds_towards,
            "cross_level_relates_to": config.lp_cross_level_relates_to,
            "cross_stage_builds_towards": config.lp_cross_stage_builds_towards,
            "cross_stage_relates_to": config.lp_cross_stage_relates_to,
            "within_level_builds_towards": config.lp_within_level_builds_towards,
            "within_level_relates_to": config.lp_within_level_relates_to,
        },
        "thresholds": {
            "builds_towards_min_confidence": config.lp_builds_towards_min_confidence,
            "cross_level_relates_to_max_items_per_subject": config.lp_cross_level_relates_to_max_items_per_subject,
            "relates_to_max_edges_per_sfi": config.lp_relates_to_max_edges_per_sfi,
            "relates_to_min_confidence": config.lp_relates_to_min_confidence,
            "within_level_relates_to_max_items_per_subject": config.lp_within_level_relates_to_max_items_per_subject,
        },
        "drops": drops,
    }
    write_to_json(
        fp=kg_dirs.learning_progressions / "learning_progressions_report.json",
        json_info=report,
    )

    learning_progressions = LearningProgressionsExport(
        builds_towards_relationships=builds_rels,
        lp_kg=lp_kg,
        relates_to_relationships=relates_rels,
        report=report,
    )

    logger.success(
        f"Exported Learning Progressions KG: "
        f"{len(learning_progressions.builds_towards_relationships)} `buildsTowards` relationships, "
        f"{len(learning_progressions.relates_to_relationships)} `relatesTo` relationships"
    )

    return learning_progressions


def _first_topic_path_key(bucket: dict[str, Any]) -> str | None:
    """Return the first non-empty topic path key stored on a bucket, if any.

    Parameters
    ----------
    bucket
        The bucket dictionary created by `_get_or_create_bucket()`.

    Returns
    -------
    str | None
        The first non-empty value in `bucket["topic_path_keys"]` or `None`.
    """

    tpks = bucket.get("topic_path_keys")

    if not isinstance(tpks, list):
        return None

    for tpk in tpks:
        tpk = str(tpk or "").strip()

        if tpk:
            return tpk

    return None


def _get_or_create_bucket(
    *,
    bucket_key_value: str,
    bucket_scope: str,
    default_thread_key: str | None,
    fallback_segments: list[str] | None,
    level_basis: str,
    level_hi: int,
    level_key: str | None,
    level_label: str,
    level_lo: int,
    store: DefaultDict[str, DefaultDict[str, dict[str, Any]]],
    subject_label: str,
    thread_key_value: str,
    topic_key: str,
    topic_path_parts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Get or create a learning-progression inference bucket.

    Buckets are the unit of work for learning-progression inference. Each exported
    StandardsFrameworkItem is normally placed into two independent bucket stores:

    1. a `within_level` bucket, used by within-level buildsTowards and within-level
        relatesTo inference and
    2. a `cross_level` bucket, used by cross-level/cross-stage inference.

    If the bucket already exists, this function returns it and updates aggregate
    topic-path bookkeeping fields. For within-level fallback buckets, it also preserves
    fallback-used metadata. Other bucket-level metadata remains first-SFI-wins by
    design.

    Bucket-level `within_level_fallback_segments` are populated only for `within_level`
    buckets whose key was actually produced from fallback segments. When a within-level
    bucket is keyed by hierarchy roles, fallback segments remain item-level provenance
    and the bucket records `within_level_bucket_used_fallback` as False. Cross-level
    buckets deliberately do not carry within-level fallback fields because those
    fallbacks are not used to compute cross-level thread keys.

    Examples
    --------
    1. Create a within-level Senegal reading bucket

        Given a CE1 item under the strand "Lecture":

            bucket = _get_or_create_bucket(
                bucket_key_value="strand=lecture",
                bucket_scope="within_level",
                default_thread_key="strand=lecture|substage=palier_1_lecture",
                fallback_segments=["statement_type=objectif_specifique"],
                level_basis="grade_ordinals",
                level_hi=1,
                level_key="CE1",
                level_label="CE1",
                level_lo=1,
                store=within_level_buckets,
                subject_label="Lecture",
                thread_key_value="strand=lecture",
                topic_key="strand=lecture|substage=palier_1_lecture",
                topic_path_parts=[
                    {"role": "strand", "label": "Lecture"},
                    {"role": "substage", "label": "Palier 1 - Lecture"},
                ],
            )

        the created bucket contains:

            bucket["bucket_scope"] == "within_level"
            bucket["default_thread_key"] == "strand=lecture|substage=palier_1_lecture"
            bucket["level_label"] == "CE1"
            bucket["topic_path_examples"] == [
                "strand:Lecture -> substage:Palier 1 - Lecture"
            ]
            bucket["topic_path_keys"] == [
                "strand=lecture|substage=palier_1_lecture"
            ]
            bucket["within_level_bucket_used_fallback"] is False
            bucket["within_level_fallback_segments"] == []

        The fallback segment is still preserved on the item payload; it is not stored
        as bucket-level fallback metadata because the bucket key came from `strand`.

    2. Create a within-level fallback bucket

        Given a CE1 item without the configured within-level role but with:

            fallback_segments=["statement_type=grammaire"]
            bucket_key_value="statement_type=grammaire"

        the created bucket contains:

            bucket["within_level_bucket_used_fallback"] is True
            bucket["within_level_fallback_segments"] == [
                "statement_type=grammaire"
            ]

        If later items reuse this same fallback bucket, the True flag is preserved.

    3. Create a cross-level bucket without within-level fallback fields

        Calling the same function with:

            bucket_scope="cross_level"
            fallback_segments=None
            bucket_key_value="strand=lecture|substage=palier_1_lecture"

        creates a bucket that does not include `within_level_fallback_segments`.
        Cross-level matching should use hierarchy-derived thread keys, not source-field
        fallbacks such as `statement_type`.

    4. Reuse an existing bucket and update aggregate topic context

        If a later item has the same `level_label` and `bucket_key_value` but comes
        from a different subtopic, this function returns the existing bucket and
        appends the new path to `topic_path_examples` and the new key to
        `topic_path_keys` when they are not already present.

        This keeps broad buckets truthful for prompts: the bucket can show several
        representative paths instead of assuming that the first SFI's path describes
        every item in the bucket.

    Parameters
    ----------
    bucket_key_value
        The computed key for the bucket in the given scope.
    bucket_scope
        The bucket scope. Expected values are `"within_level"` and
        `"cross_level"`.
    default_thread_key
        The source/default thread key from `progression_context.thread_key`. Stored
        as bucket metadata under `default_thread_key` for traceability.
    fallback_segments
        Source-field fallback segments used for within-level bucketing. These are
        stored as bucket-level metadata only when `bucket_scope == "within_level"` and
        the bucket key was actually produced from those fallback segments. Otherwise,
        they remain item-level provenance.
    level_basis
        How level ordinals were resolved, e.g. `"grade_ordinals"` or
        `"stage_ordinals"`.
    level_hi
        Highest level ordinal represented by the bucket.
    level_key
        Human-readable source level key, e.g. `"CE1"` or `"Standard III–VI"`.
    level_label
        The level label used as the outer key in the bucket store. Prefer source labels
        such as "CE1" or "Standard III–VI [3–6]"; synthetic labels such as "LEVEL 1"
        are used only when no source label is available.
    level_lo
        Lowest level ordinal represented by the bucket.
    store
        Nested bucket store keyed by `level_label` then `bucket_key_value`.
    subject_label
        Subject-like label for the bucket, often a strand in single-subject curricula.
    thread_key_value
        Thread key associated with this bucket, used later for cross-level grouping.
    topic_key
        Canonical topic path key for the current SFI.
    topic_path_parts
        Topic path parts for the current SFI.

    Returns
    -------
    dict[str, Any]
        The existing or newly created bucket dictionary.
    """

    if bucket_scope not in {"within_level", "cross_level"}:
        raise ValueError(
            f"bucket_scope must be either 'within_level' or 'cross_level'. "
            f"Got: {bucket_scope!r}"
        )

    # Clean and prepare key and fallback values for bucket lookup and metadata.
    topic_key_str = str(topic_key or "").strip()
    topic_path = _path_string(topic_path_parts)
    cleaned_fallbacks = [
        str(seg).strip() for seg in fallback_segments or [] if str(seg).strip()
    ]
    fallback_key = "|".join(cleaned_fallbacks)
    bucket_used_fallback = (
        bucket_scope == "within_level"
        and bool(fallback_key)
        and bucket_key_value == fallback_key
    )
    bucket = store[level_label].get(bucket_key_value)

    if bucket is not None:
        _update_existing_bucket(
            bucket=bucket,
            bucket_key_value=bucket_key_value,
            bucket_scope=bucket_scope,
            bucket_used_fallback=bucket_used_fallback,
            cleaned_fallbacks=cleaned_fallbacks,
            level_label=level_label,
            subject_label=subject_label,
            topic_key_str=topic_key_str,
            topic_path=topic_path,
        )
    else:
        bucket = _create_new_bucket(
            bucket_key_value=bucket_key_value,
            bucket_scope=bucket_scope,
            bucket_used_fallback=bucket_used_fallback,
            cleaned_fallbacks=cleaned_fallbacks,
            default_thread_key=default_thread_key,
            level_basis=level_basis,
            level_hi=level_hi,
            level_key=level_key,
            level_label=level_label,
            level_lo=level_lo,
            subject_label=subject_label,
            thread_key_value=thread_key_value,
            topic_key_str=topic_key_str,
            topic_path=topic_path,
        )
        store[level_label][bucket_key_value] = bucket

    return bucket


def _group_threads_by_level_and_subject(
    *, by_level: dict[str, list[dict[str, Any]]], config: CreateKGConfig
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Group within-level inference buckets by level and subject-like label.

    Phase 3 compares buckets across a configurable "subject-like" axis within the same
    level. The label comes from upstream bucketing as `bucket["subject_label"]`;
    depending on `config.lp_subject_role`, it may be a true academic subject, a
    learning area, a strand, or another curriculum grouping. For single-subject
    curricula (e.g., Senegal CE1 reading), this commonly means cross-strand rather than
    cross-subject comparison.

    Empty buckets are dropped here so later work-item construction only considers
    buckets with at least one candidate StandardsFrameworkItem. Buckets that are not
    allowed by `_allow_within_level_inference()` are also skipped, which preserves the
    configured single-level vs. banded-level policy.

    Examples
    --------
    1. Cross-strand grouping for a single-level reading curriculum

        Input `by_level` may contain CE1 buckets like:

            {
                "CE1": [
                    {
                        "subject_label": "Communication écrite - Lecture",
                        "lp_bucket_key": "strand=lecture",
                        "items": [<SFI 1>, <SFI 2>],
                    },
                    {
                        "subject_label": "Communication orale",
                        "lp_bucket_key": "strand=oral",
                        "items": [<SFI 3>],
                    },
                ]
            }

        The output is::

            {
                "CE1": {
                    "Communication écrite - Lecture": [<lecture bucket>],
                    "Communication orale": [<oral bucket>],
                }
            }

        Phase 3 can then compare the two subject-like groups for `relatesTo` edges.

    2. Empty buckets are omitted

        A bucket with `items=[]` is ignored, so a subject-like label with no usable
        standards does not create an empty work item downstream.

    3. Missing subject labels are retained as explicit sentinels

        A bucket with items but no `subject_label` is grouped under
        "UNSPECIFIED_SUBJECT". The caller can then exclude that sentinel via the
        normal Phase 3 exclusion policy.

    Parameters
    ----------
    by_level
        Dictionary mapping level labels to lists of finalized within-level bucket
        dictionaries.
    config
        The knowledge graph run configuration.

    Returns
    -------
    dict[str, dict[str, list[dict[str, Any]]]]
        Nested mapping `level_label -> subject_like_label -> list[bucket]` for Phase 3
        within-level `relatesTo` inference.
    """

    level_subject_threads: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for level_label, level_buckets in by_level.items():
        for bucket in level_buckets:
            if not (
                _allow_within_level_inference(bucket=bucket, config=config)
                and (bucket.get("items") or [])
            ):
                continue

            subject = str(bucket.get("subject_label") or "UNSPECIFIED_SUBJECT")
            level_subject_threads[level_label][subject].append(bucket)

    return level_subject_threads


def _infer_cross_level_builds_towards(
    *,
    by_level: dict[str, list[dict[str, Any]]],
    config: CreateKGConfig,
    usage_tracker: KGUsageTracker,
) -> tuple[list[CandidateEdge], list[dict[str, Any]], set[tuple[UUID, UUID]]]:
    """Perform Phase 2 inference: Cross-level/cross-stage buildsTowards relationships
    with optional cross-stage handling.

    1. If BOTH adjacent buckets represent single levels (low == high), run true
        cross-level.
    2. If EITHER side is banded (low != high) and cross-stage is enabled, run
        cross-stage.

    Parameters
    ----------
    by_level
        Dictionary mapping level labels to lists of bucket dictionaries.
    config
        The knowledge graph run configuration.
    usage_tracker
        The KGUsageTracker for recording KG generation and validation calls during the
        export process.

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
    cross_level_build_pairs: set[tuple[UUID, UUID]] = set()
    provenance_rows: list[dict[str, Any]] = []

    if not (
        config.lp_cross_level_builds_towards or config.lp_cross_stage_builds_towards
    ):
        return candidates, provenance_rows, cross_level_build_pairs

    thread_map = _build_thread_map(by_level)

    # Debug: count unthreaded sentinel buckets that will be excluded from cross-level
    # matching because their level-specific sentinels prevent cross-level pairing.
    unthreaded_count = sum(1 for tk in thread_map if tk.startswith("__unthreaded__::"))

    if unthreaded_count > 0:
        logger.warning(
            f"Cross-level matching: {unthreaded_count} unthreaded thread(s) "
            f"excluded (level-specific sentinel prevents cross-level pairing)"
        )

    work_items = _collect_builds_towards_work_items(
        config=config, thread_map=thread_map
    )

    total_calls = len(work_items)
    cross_level_calls = sum(
        1 for _, _, _, it, _ in work_items if it == "cross_level_builds_towards"
    )
    cross_stage_calls = total_calls - cross_level_calls

    if config.lp_cross_level_builds_towards:
        logger.info(
            f"{cross_level_calls} adjacent single-level pairs for "
            f"cross-level buildsTowards inference."
        )

    if config.lp_cross_stage_builds_towards:
        logger.info(
            f"{cross_stage_calls} adjacent level pairs for "
            f"cross-stage buildsTowards inference."
        )

    min_confidence = config.lp_builds_towards_min_confidence

    for current_call, (
        thread_key,
        b_lo,
        b_hi,
        inference_type,
        prompt_builder,
    ) in enumerate(work_items, 1):
        lo_label = _level_label(b_lo)
        hi_label = _level_label(b_hi)

        logger.info(
            f"Phase 2 Progress: {current_call}/{total_calls} "
            f"({lo_label} -> {hi_label} | {thread_key} | {inference_type})"
        )

        lo_lo, lo_hi = _level_bounds(b_lo)
        hi_lo, hi_hi = _level_bounds(b_hi)
        lower_payload = [
            _build_item_payload(item=it) for it in (b_lo.get("items") or [])
        ]
        upper_payload = [
            _build_item_payload(item=it) for it in (b_hi.get("items") or [])
        ]
        lower_topic_context = _bucket_topic_context(bucket=b_lo)
        upper_topic_context = _bucket_topic_context(bucket=b_hi)

        prompt = prompt_builder(
            lower_bucket_key=b_lo.get("lp_bucket_key"),
            lower_items=lower_payload,
            lower_level_label=lo_label,
            lower_subject_label=b_lo.get("subject_label"),
            lower_topic_context=lower_topic_context,
            min_confidence=min_confidence,
            thread_key=thread_key,
            upper_bucket_key=b_hi.get("lp_bucket_key"),
            upper_items=upper_payload,
            upper_level_label=hi_label,
            upper_subject_label=b_hi.get("subject_label"),
            upper_topic_context=upper_topic_context,
        )

        response = infer_progression_edges(
            instructions=prompt.system_message,
            usage_tracker=usage_tracker,
            user_message=prompt.user_message,
            validator=partial(
                validate_cross_level_builds_towards,
                allowed_lo={str(it["sfi_uuid"]) for it in lower_payload},
                allowed_hi={str(it["sfi_uuid"]) for it in upper_payload},
                min_confidence=min_confidence,
            ),
        )

        for e in response.edges:
            confidence_val = float(e.confidence)
            source_uuid = _uuid(e.source_sfi_uuid)
            target_uuid = _uuid(e.target_sfi_uuid)
            ce = CandidateEdge(
                confidence=confidence_val,
                evidence={"rationale": e.rationale},
                inference_source="llm",
                llm_confidence=confidence_val,
                inference_type=inference_type,
                metadata={
                    "lower_level_high": lo_hi,
                    "lower_level_label": lo_label,
                    "lower_level_low": lo_lo,
                    "lp_bucket_key_lower": b_lo.get("lp_bucket_key"),
                    "lp_bucket_key_upper": b_hi.get("lp_bucket_key"),
                    "lp_thread_key": b_hi.get("lp_thread_key"),
                    "phase": 2,
                    "subject_label": b_hi.get("subject_label"),
                    "thread_key": thread_key,
                    "topic_path_examples_lower": b_lo.get("topic_path_examples"),
                    "topic_path_examples_upper": b_hi.get("topic_path_examples"),
                    "topic_path_keys_lower": b_lo.get("topic_path_keys"),
                    "topic_path_keys_upper": b_hi.get("topic_path_keys"),
                    "upper_level_high": hi_hi,
                    "upper_level_label": hi_label,
                    "upper_level_low": hi_lo,
                },
                rel_type=BUILDS_TOWARDS,
                source_sfi_uuid=source_uuid,
                target_sfi_uuid=target_uuid,
            )
            candidates.append(ce)

            # Validator-side checks should already enforce the confidence threshold.
            # Keep this guard just in case so only final-threshold buildsTowards pairs
            # suppress Phase 4 relatesTo candidates.
            if confidence_val >= min_confidence:
                cross_level_build_pairs.add((source_uuid, target_uuid))

            provenance_rows.append(
                _make_provenance_row(
                    candidate=ce,
                    inference_type=inference_type,
                    phase=2,
                    lower_bucket_key=b_lo.get("lp_bucket_key"),
                    lower_item_count=len(lower_payload),
                    lower_level=lo_label,
                    lower_level_high=lo_hi,
                    lower_level_low=lo_lo,
                    lower_topic_context=lower_topic_context,
                    rationale=e.rationale,
                    subject_label=b_hi.get("subject_label"),
                    thread_key=thread_key,
                    upper_bucket_key=b_hi.get("lp_bucket_key"),
                    upper_item_count=len(upper_payload),
                    upper_level=hi_label,
                    upper_level_high=hi_hi,
                    upper_level_low=hi_lo,
                    upper_topic_context=upper_topic_context,
                )
            )

    return candidates, provenance_rows, cross_level_build_pairs


def _infer_cross_level_relates_to(
    *,
    by_level: dict[str, list[dict[str, Any]]],
    config: CreateKGConfig,
    forbidden_builds_pairs: set[tuple[UUID, UUID]],
    usage_tracker: KGUsageTracker,
) -> tuple[list[CandidateEdge], list[dict[str, Any]]]:
    """Perform Phase 4 inference: cross-level/cross-stage `relatesTo` edges.

    Phase 4 compares sampled StandardsFrameworkItems from adjacent level ranges within
    the **same** subject-like label. It is intentionally associative rather than
    prerequisite-oriented: pairs already accepted as cross-level/cross-stage
    `buildsTowards` are passed as forbidden pairs and must not also be emitted as
    `relatesTo`.

    The process is as follows:

    1. Return no candidates when both `config.lp_cross_level_relates_to` and
        `config.lp_cross_stage_relates_to` are disabled.
    2. Use Phase 4 limits from config: max relatesTo edges per SFI and max sampled
        items per subject per level range.
    3. Prepare subject-level samples with `_prepare_subject_level_samples()`, producing
        `subject_label -> (level_low, level_high) -> sampled items + sampling
        provenance`. Excluded subject labels are omitted before level-bound checks.
    4. Build adjacent same-subject work items with `_collect_relates_to_work_items()`.
        Adjacent single-level ranges use the cross-level prompt when enabled; pairs
        where either side is banded use the cross-stage prompt when enabled.
    5. For each work item, resolve prompt-specific forbidden pairs with
        `_resolve_forbidden_pairs()`. These are accepted `buildsTowards` pairs whose
        endpoints both appear in the current lower/upper sampled item lists.
    6. Run the selected prompt builder in the lower -> upper presentation order and
        validate with `validate_cross_level_relates_to()`.
    7. Run the same prompt builder in the upper -> lower presentation order, swapping
        both item lists and level labels so the prompt remains self-consistent.
    8. Canonicalize both outputs with `_best_map()` and keep only pairs that appear in
        both orientations. The confirmed candidate confidence is the lower of the two
        orientation-specific confidence scores.
    9. Emit one undirected `relatesTo` `CandidateEdge` plus one raw provenance row for
        each bidirectionally confirmed pair, preserving lower/upper level metadata,
        sampling provenance, confidence/rationale from both orientations, and the Phase
        4 inference type.

    NB: `lp_cross_level_relates_to_max_items_per_subject` is an upper bound, not a
    required item count. For each subject-like label and level range, the sampler
    returns up to this many StandardsFrameworkItems. If fewer are available, all
    available items are used and no error is raised.

    NB: Phase 4 compares adjacent ranges only. It does not compare Grade 1 directly to
    Grade 3 when Grade 2 is missing, and it treats banded ranges such as I-II -> III-VI
    as cross-stage, not single-grade cross-level, comparisons.

    NB: The returned `relatesTo` candidates use canonicalized endpoint order because
    the relationship is conceptually undirected. Lower/upper curriculum context is
    preserved separately in candidate metadata and provenance.

    Parameters
    ----------
    by_level
        Dictionary mapping level labels to finalized cross-level bucket dictionaries.
        This should usually be the `by_cross_level` view returned by
        `group_standards_for_learning_progressions()`.
    config
        The knowledge graph run configuration.
    forbidden_builds_pairs
        Global set of accepted Phase 2 cross-level/cross-stage `buildsTowards` pairs.
        Pairs whose endpoints both appear in the current Phase 4 prompt are excluded
        from `relatesTo` inference.
    usage_tracker
        Tracker for KG generation and validation LLM calls.

    Returns
    -------
    tuple[list[CandidateEdge], list[dict[str, Any]]]
        Generated candidate edges and corresponding raw provenance rows.
    """

    candidates: list[CandidateEdge] = []
    provenance_rows: list[dict[str, Any]] = []

    if not (config.lp_cross_level_relates_to or config.lp_cross_stage_relates_to):
        return candidates, provenance_rows

    max_edges_per_sfi = config.lp_relates_to_max_edges_per_sfi
    max_items = config.lp_cross_level_relates_to_max_items_per_subject

    subject_level_samples = _prepare_subject_level_samples(
        by_level=by_level,
        excluded_subject_labels=set(config.lp_excluded_subject_labels or []),
        max_items=max_items,
    )

    work_items = _collect_relates_to_work_items(
        config=config, subject_level_samples=subject_level_samples
    )

    cross_level_calls = sum(
        1 for w in work_items if w["inference_type"] == "cross_level_relates_to"
    )
    cross_stage_calls = len(work_items) - cross_level_calls
    total_calls = len(work_items) * 2

    if config.lp_cross_level_relates_to:
        logger.info(
            f"{cross_level_calls} adjacent single-level pairs for "
            f"cross-level relatesTo inference."
        )

    if config.lp_cross_stage_relates_to:
        logger.info(
            f"{cross_stage_calls} adjacent level pairs for "
            f"cross-stage relatesTo inference."
        )

    current_call_idx = 0

    for wi in work_items:
        lower_dict = wi["lower"]
        upper_dict = wi["upper"]
        lower_items = lower_dict["items"]
        upper_items = upper_dict["items"]

        lower_lvl_lbl = str(lower_dict["level_label"])
        upper_lvl_lbl = str(upper_dict["level_label"])
        subject_label = wi["subject_label"]
        inference_type = wi["inference_type"]
        prompt_builder = wi["prompt_builder"]

        forbidden_pairs_set, forbidden_pairs = _resolve_forbidden_pairs(
            forbidden_builds_pairs=forbidden_builds_pairs,
            lower_items=lower_items,
            upper_items=upper_items,
        )

        # Call 1: lower -> upper.
        prompt_lo_hi = prompt_builder(
            forbidden_pairs=forbidden_pairs,
            list_a_items=lower_items,
            list_a_level_label=lower_lvl_lbl,
            list_b_items=upper_items,
            list_b_level_label=upper_lvl_lbl,
            max_edges_per_sfi=max_edges_per_sfi,
            min_confidence=config.lp_relates_to_min_confidence,
            subject_label=subject_label,
        )

        current_call_idx += 1

        logger.info(
            f"Phase 4 Progress: {current_call_idx}/{total_calls} "
            f"({subject_label}: {lower_lvl_lbl} -> {upper_lvl_lbl} | {inference_type} | lo -> hi)"
        )

        resp_lo_hi = infer_progression_edges(
            instructions=prompt_lo_hi.system_message,
            usage_tracker=usage_tracker,
            user_message=prompt_lo_hi.user_message,
            validator=partial(
                validate_cross_level_relates_to,
                allowed_lo={str(it["sfi_uuid"]) for it in lower_items},
                allowed_hi={str(it["sfi_uuid"]) for it in upper_items},
                forbidden_pairs=forbidden_pairs_set,
            ),
        )

        # Call 2: upper -> lower (reverse presentation order for bidirectional
        # confirmation). We swap BOTH items and labels so the LLM sees a
        # self-consistent view. The neutral "List A"/"List B" names in the prompt avoid
        # the semantic confusion of calling upper-level items "lower".
        prompt_hi_lo = prompt_builder(
            forbidden_pairs=forbidden_pairs,
            list_a_items=upper_items,
            list_a_level_label=upper_lvl_lbl,
            list_b_items=lower_items,
            list_b_level_label=lower_lvl_lbl,
            max_edges_per_sfi=max_edges_per_sfi,
            min_confidence=config.lp_relates_to_min_confidence,
            subject_label=subject_label,
        )

        current_call_idx += 1

        logger.info(
            f"Phase 4 Progress: {current_call_idx}/{total_calls} "
            f"({subject_label}: {lower_lvl_lbl} -> {upper_lvl_lbl} | {inference_type} | hi -> lo)"
        )

        resp_hi_lo = infer_progression_edges(
            instructions=prompt_hi_lo.system_message,
            usage_tracker=usage_tracker,
            user_message=prompt_hi_lo.user_message,
            validator=partial(
                validate_cross_level_relates_to,
                allowed_lo={str(it["sfi_uuid"]) for it in upper_items},
                allowed_hi={str(it["sfi_uuid"]) for it in lower_items},
                forbidden_pairs=forbidden_pairs_set,
            ),
        )

        map_lo_hi = _best_map(resp_lo_hi)
        map_hi_lo = _best_map(resp_hi_lo)
        common_pairs = sorted(set(map_lo_hi.keys()) & set(map_hi_lo.keys()))

        for a, b in common_pairs:
            conf_lo_hi, rat_lo_hi = map_lo_hi[(a, b)]
            conf_hi_lo, rat_hi_lo = map_hi_lo[(a, b)]
            conf = min(conf_lo_hi, conf_hi_lo)
            ce = CandidateEdge(
                confidence=float(conf),
                evidence={
                    "bidirectional_confirmed": True,
                    "confidence_hi_lo": float(conf_hi_lo),
                    "confidence_lo_hi": float(conf_lo_hi),
                    "rationale_hi_lo": rat_hi_lo,
                    "rationale_lo_hi": rat_lo_hi,
                },
                inference_source="llm",
                llm_confidence=float(conf),
                inference_type=inference_type,
                metadata={
                    "bidirectional_confirmed": True,
                    "lower_level_high": wi["lo_high"],
                    "lower_level_label": lower_lvl_lbl,
                    "lower_level_low": wi["lo_low"],
                    "lower_max_items": lower_dict.get("max_items"),
                    "lower_sampled_count": lower_dict.get("sampled_count"),
                    "lower_sampled_sfi_uuids": lower_dict.get("sampled_sfi_uuids"),
                    "lower_source_bucket_count": lower_dict.get("source_bucket_count"),
                    "lower_source_bucket_keys": lower_dict.get("source_bucket_keys"),
                    "lower_source_item_count": lower_dict.get("source_item_count"),
                    "lower_source_thread_keys": lower_dict.get("source_thread_keys"),
                    "phase": 4,
                    "subject_label": subject_label,
                    "upper_level_high": wi["hi_high"],
                    "upper_level_label": upper_lvl_lbl,
                    "upper_level_low": wi["hi_low"],
                    "upper_max_items": upper_dict.get("max_items"),
                    "upper_sampled_count": upper_dict.get("sampled_count"),
                    "upper_sampled_sfi_uuids": upper_dict.get("sampled_sfi_uuids"),
                    "upper_source_bucket_count": upper_dict.get("source_bucket_count"),
                    "upper_source_bucket_keys": upper_dict.get("source_bucket_keys"),
                    "upper_source_item_count": upper_dict.get("source_item_count"),
                    "upper_source_thread_keys": upper_dict.get("source_thread_keys"),
                },
                rel_type=RELATES_TO,
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
                    lower_level=lower_lvl_lbl,
                    upper_level=upper_lvl_lbl,
                    lower_max_items=lower_dict.get("max_items"),
                    lower_sampled_count=lower_dict.get("sampled_count"),
                    lower_sampled_sfi_uuids=lower_dict.get("sampled_sfi_uuids"),
                    lower_source_bucket_count=lower_dict.get("source_bucket_count"),
                    lower_source_bucket_keys=lower_dict.get("source_bucket_keys"),
                    lower_source_item_count=lower_dict.get("source_item_count"),
                    lower_source_thread_keys=lower_dict.get("source_thread_keys"),
                    upper_max_items=upper_dict.get("max_items"),
                    upper_sampled_count=upper_dict.get("sampled_count"),
                    upper_sampled_sfi_uuids=upper_dict.get("sampled_sfi_uuids"),
                    upper_source_bucket_count=upper_dict.get("source_bucket_count"),
                    upper_source_bucket_keys=upper_dict.get("source_bucket_keys"),
                    upper_source_item_count=upper_dict.get("source_item_count"),
                    upper_source_thread_keys=upper_dict.get("source_thread_keys"),
                )
            )

    return candidates, provenance_rows


def _infer_within_level_builds_towards(
    *,
    by_level: dict[str, list[dict[str, Any]]],
    config: CreateKGConfig,
    usage_tracker: KGUsageTracker,
) -> tuple[list[CandidateEdge], list[dict[str, Any]]]:
    """Perform Phase 1 inference: Within-level buildsTowards relationships.

    Parameters
    ----------
    by_level
        Dictionary mapping level labels to lists of bucket dictionaries.
    config
        The knowledge graph run configuration.
    usage_tracker
        The KGUsageTracker for recording KG generation and validation calls during the
        export process.

    Returns
    -------
    tuple[list[CandidateEdge], list[dict[str, Any]]]
        A tuple containing the list of generated candidate edges and the list of
        provenance dictionaries.
    """

    candidates: list[CandidateEdge] = []
    provenance_rows: list[dict[str, Any]] = []

    if not config.lp_within_level_builds_towards:
        return candidates, provenance_rows

    # Collect eligible buckets so `total_calls` is exact.
    eligible: list[tuple[str, dict[str, Any]]] = [
        (level_label, bucket)
        for level_label, level_buckets in by_level.items()
        for bucket in level_buckets
        if _allow_within_level_inference(bucket=bucket, config=config)
        and len(bucket.get("items") or []) >= 2
    ]
    total_calls = len(eligible)

    logger.info(
        f"{total_calls} buckets with 2+ items for within-level buildsTowards inference."
    )

    inference_type = "within_level_builds_towards"

    for current_call, (level_label, bucket) in enumerate(eligible, 1):
        items = bucket["items"]  # Should have at least 2 items due to the filter above

        logger.info(
            f"Phase 1 Progress: {current_call}/{total_calls} "
            f"({level_label}: {bucket.get('lp_bucket_key')})"
        )

        ordered_items = [
            _build_item_payload(item=item, sequence_index=idx)
            for idx, item in enumerate(items)
        ]
        pos = {str(item["sfi_uuid"]): idx for idx, item in enumerate(ordered_items)}
        allowed = set(pos.keys())

        prompt = within_level_builds_towards(
            bucket_key=str(
                bucket.get("lp_bucket_key") or bucket.get("bucket_key") or ""
            ),
            items=ordered_items,
            level_label=str(level_label),
            min_confidence=config.lp_builds_towards_min_confidence,
            subject_label=str(bucket.get("subject_label") or ""),
            thread_key=str(bucket.get("lp_thread_key") or ""),
            thread_path=_bucket_topic_context(bucket=bucket),
        )
        response = infer_progression_edges(
            instructions=prompt.system_message,
            usage_tracker=usage_tracker,
            user_message=prompt.user_message,
            validator=partial(
                validate_within_level_builds_towards,
                allowed_uuids=allowed,
                min_confidence=config.lp_builds_towards_min_confidence,
                uuid_positions=pos,
            ),
        )

        for edge in response.edges:
            candidate_edge = CandidateEdge(
                confidence=float(edge.confidence),
                evidence={"rationale": edge.rationale},
                inference_source="llm",
                inference_type=inference_type,
                llm_confidence=float(edge.confidence),
                metadata={
                    "phase": 1,
                    "level_label": level_label,
                    "lp_bucket_key": bucket.get("lp_bucket_key"),
                    "lp_thread_key": bucket.get("lp_thread_key"),
                    "subject_label": bucket.get("subject_label"),
                    "topic_path_examples": bucket.get("topic_path_examples"),
                    "topic_path_keys": bucket.get("topic_path_keys"),
                },
                rel_type=BUILDS_TOWARDS,
                source_sfi_uuid=_uuid(edge.source_sfi_uuid),
                target_sfi_uuid=_uuid(edge.target_sfi_uuid),
            )
            candidates.append(candidate_edge)
            provenance_rows.append(
                _make_provenance_row(
                    candidate=candidate_edge,
                    inference_type=inference_type,
                    phase=1,
                    bucket_key=bucket.get("bucket_key"),
                    level_label=level_label,
                    llm_confidence=float(edge.confidence),
                    lp_bucket_key=bucket.get("lp_bucket_key"),
                    lp_thread_key=bucket.get("lp_thread_key"),
                    rationale=edge.rationale,
                    source_sequence_index=pos.get(edge.source_sfi_uuid),
                    subject_label=bucket.get("subject_label"),
                    target_sequence_index=pos.get(edge.target_sfi_uuid),
                    topic_path_examples=bucket.get("topic_path_examples"),
                    topic_path_keys=bucket.get("topic_path_keys"),
                )
            )

    return candidates, provenance_rows


def _infer_within_level_relates_to(
    *,
    by_level: dict[str, list[dict[str, Any]]],
    config: CreateKGConfig,
    usage_tracker: KGUsageTracker,
) -> tuple[list[CandidateEdge], list[dict[str, Any]]]:
    """Perform Phase 3 inference: within-level cross-thread `relatesTo` edges.

    Phase 3 compares subject-like groups within the same level. The comparison axis is
    `subject_label`, which is assigned upstream according to `config.lp_subject_role`.
    In a multi-subject curriculum this may be a true academic subject; in a
    single-subject curriculum it may be a strand, learning area, or similar grouping.

    The process is as follows:

    1. Return no candidates when `config.lp_within_level_relates_to` is disabled.
    2. Use Phase 3 limits from config: max relatesTo edges per SFI and max sampled
        items per subject-like group.
    3. Group finalized buckets with `_group_threads_by_level_and_subject()` into
        `level -> subject_like_label -> buckets`.
    4. Build the exclusion set from `config.lp_excluded_subject_labels` plus standard
        sentinel labels such as `UNSPECIFIED_SUBJECT`.
    5. For each level, enumerate all unordered pairs of non-excluded subject-like
        labels.
    6. Stable-sort each side's buckets with `_thread_sort_key`.
    7. Sample bounded, diverse prompt items from each side with
        `_sample_items_across_threads()`.
    8. Store a work item when both sides have at least one sampled item.
    9. For each work item, build prompt payloads with `_build_item_payload()`.
    10. Run `within_level_relates_to()` in the A -> B orientation and validate.
    11. Run the same prompt in the B -> A orientation, then canonicalize both outputs
        with `_best_map()`.
    12. Keep only bidirectionally confirmed canonical pairs, using the lower of the two
        confidence scores, and emit one `CandidateEdge` plus one provenance row for
        each confirmed pair.

    NB: `lp_within_level_relates_to_max_items_per_subject` is an upper bound, not a
    required item count. For each subject-like group/thread side in a within-level
    relatesTo comparison, the sampler returns up to this many StandardsFrameworkItems.
    If a group contains fewer items than the configured maximum, all available items
    are used and no error is raised.

    Setting this value too small improves cost, latency, and precision, but reduces
    recall. The model sees only a small slice of each subject-like group, so legitimate
    relatesTo edges may be undiscoverable because the relevant SFI was never sampled.

    Setting this value too large improves coverage, but increases prompt size, cost,
    and latency. Very large prompts can also reduce semantic quality: the model has
    more opportunities to infer weak/generic links, and structural validators cannot
    fully catch weak but formally valid relatesTo edges.

    For large curriculum strands, prefer a moderate value or add smarter
    sampling/chunked comparisons rather than simply increasing this value indefinitely.

    NB: Phase 3 does not take forbidden within-level `buildsTowards` pairs as input. It
    relies on the bucketing invariant that each prompt compares disjoint subject-like
    groups. If that invariant changes, pass forbidden pairs into this phase or filter
    candidate pairs before emission.

    Parameters
    ----------
    by_level
        Dictionary mapping level labels to lists of finalized within-level bucket
        dictionaries.
    config
        The knowledge graph run configuration.
    usage_tracker
        Tracker for KG generation and validation LLM calls.

    Returns
    -------
    tuple[list[CandidateEdge], list[dict[str, Any]]]
        Generated candidate edges and corresponding raw provenance rows.
    """

    candidates: list[CandidateEdge] = []
    provenance_rows: list[dict[str, Any]] = []

    if not config.lp_within_level_relates_to:
        return candidates, provenance_rows

    max_edges_per_sfi = config.lp_relates_to_max_edges_per_sfi
    max_items = config.lp_within_level_relates_to_max_items_per_subject

    # Group threads by level -> subject-like label.
    level_subject_threads = _group_threads_by_level_and_subject(
        by_level=by_level, config=config
    )

    excluded = set(config.lp_excluded_subject_labels or []) | {
        "UNSPECIFIED_SUBJECT",
        "UNKNOWN",
        "",
    }

    work_items = _build_relates_to_work_items(
        excluded=excluded,
        level_subject_threads=level_subject_threads,
        max_items=max_items,
    )

    current_call = 0
    inference_type = "within_level_cross_thread_relates_to"
    total_pairs = len(work_items)
    total_calls = total_pairs * 2  # Bidirectional confirmation

    for wi in work_items:
        level_label = wi["level_label"]
        subject_a, subject_b = wi["subject_a"], wi["subject_b"]

        logger.info(f"Phase 3 Pair: ({level_label}: {subject_a} x {subject_b})")

        sampled_a, sampled_b = wi["sampled_a"], wi["sampled_b"]
        sampled_a_count = wi["sampled_a_count"]
        sampled_b_count = wi["sampled_b_count"]
        thread_a_path, thread_b_path = wi["thread_a_path"], wi["thread_b_path"]
        items_a = [_build_item_payload(item=it) for it in sampled_a]
        items_b = [_build_item_payload(item=it) for it in sampled_b]
        sampled_a_sfi_uuids = [str(it["sfi_uuid"]) for it in items_a]
        sampled_b_sfi_uuids = [str(it["sfi_uuid"]) for it in items_b]
        allowed_a = set(sampled_a_sfi_uuids)
        allowed_b = set(sampled_b_sfi_uuids)

        # Bidirectional confirmation: run A x B and B x A, then keep only edges that
        # appear in both runs (canonicalized by UUID order).
        prompt_ab = within_level_relates_to(
            items_a=items_a,
            items_b=items_b,
            level_label=str(level_label),
            max_edges_per_sfi=max_edges_per_sfi,
            min_confidence=config.lp_relates_to_min_confidence,
            subject_label=f"{subject_a} × {subject_b}",
            thread_a_key=f"subject_like:{subject_a}",
            thread_b_key=f"subject_like:{subject_b}",
            thread_a_path=thread_a_path or subject_a,
            thread_b_path=thread_b_path or subject_b,
        )

        current_call += 1

        logger.info(
            f"Phase 3 Progress: {current_call}/{total_calls} "
            f"({level_label}: {subject_a} x {subject_b} | relatesTo | A -> B)"
        )

        resp_ab = infer_progression_edges(
            instructions=prompt_ab.system_message,
            usage_tracker=usage_tracker,
            user_message=prompt_ab.user_message,
            validator=partial(
                validate_within_level_relates_to,
                allowed_uuids_a=allowed_a,
                allowed_uuids_b=allowed_b,
                min_confidence=config.lp_relates_to_min_confidence,
            ),
        )

        prompt_ba = within_level_relates_to(
            items_a=items_b,
            items_b=items_a,
            level_label=str(level_label),
            max_edges_per_sfi=max_edges_per_sfi,
            min_confidence=config.lp_relates_to_min_confidence,
            subject_label=f"{subject_b} × {subject_a}",
            thread_a_key=f"subject_like:{subject_b}",
            thread_b_key=f"subject_like:{subject_a}",
            thread_a_path=thread_b_path or subject_b,
            thread_b_path=thread_a_path or subject_a,
        )

        current_call += 1

        logger.info(
            f"Phase 3 Progress: {current_call}/{total_calls} "
            f"({level_label}: {subject_a} x {subject_b} | relatesTo | B -> A)"
        )

        resp_ba = infer_progression_edges(
            instructions=prompt_ba.system_message,
            usage_tracker=usage_tracker,
            user_message=prompt_ba.user_message,
            validator=partial(
                validate_within_level_relates_to,
                allowed_uuids_a=allowed_b,
                allowed_uuids_b=allowed_a,
                min_confidence=config.lp_relates_to_min_confidence,
            ),
        )

        map_ab, map_ba = _best_map(resp_ab), _best_map(resp_ba)
        common_pairs = sorted(set(map_ab.keys()) & set(map_ba.keys()))

        for u_a, u_b in common_pairs:
            conf_ab, rat_ab = map_ab[(u_a, u_b)]
            conf_ba, rat_ba = map_ba[(u_a, u_b)]
            conf = min(conf_ab, conf_ba)
            ce = CandidateEdge(
                confidence=float(conf),
                evidence={
                    "bidirectional_confirmed": True,
                    "confidence_ab": float(conf_ab),
                    "confidence_ba": float(conf_ba),
                    "rationale_ab": rat_ab,
                    "rationale_ba": rat_ba,
                },
                inference_source="llm",
                inference_type=inference_type,
                llm_confidence=float(conf),
                metadata={
                    "bidirectional_confirmed": True,
                    "level_label": level_label,
                    "phase": 3,
                    "sampled_a_count": sampled_a_count,
                    "sampled_a_sfi_uuids": sampled_a_sfi_uuids,
                    "sampled_b_count": sampled_b_count,
                    "sampled_b_sfi_uuids": sampled_b_sfi_uuids,
                    "subject_a": subject_a,
                    "subject_b": subject_b,
                    "thread_a_path": thread_a_path or subject_a,
                    "thread_b_path": thread_b_path or subject_b,
                },
                rel_type=RELATES_TO,
                source_sfi_uuid=_uuid(u_a),
                target_sfi_uuid=_uuid(u_b),
            )
            candidates.append(ce)
            provenance_rows.append(
                _make_provenance_row(
                    candidate=ce,
                    inference_type=inference_type,
                    phase=3,
                    bidirectional_confirmed=True,
                    confidence_fwd=float(conf_ab),
                    confidence_rev=float(conf_ba),
                    level_label=level_label,
                    rationale_fwd=rat_ab,
                    rationale_rev=rat_ba,
                    sampled_a_count=sampled_a_count,
                    sampled_a_sfi_uuids=sampled_a_sfi_uuids,
                    sampled_b_count=sampled_b_count,
                    sampled_b_sfi_uuids=sampled_b_sfi_uuids,
                    subject_a=subject_a,
                    subject_b=subject_b,
                    thread_a_path=thread_a_path or subject_a,
                    thread_b_path=thread_b_path or subject_b,
                )
            )

    return candidates, provenance_rows


def _is_single_level_bucket(bucket: dict[str, Any]) -> bool:
    """Determine if a bucket corresponds to a single level based on its level ordinal.

    Parameters
    ----------
    bucket
        A bucket dictionary that may contain level ordinal information.

    Returns
    -------
    bool
        True if the bucket corresponds to a single level (i.e., low and high ordinals
        are both integers and equal), False otherwise.
    """

    lo, hi = _level_bounds(bucket)
    return isinstance(lo, int) and isinstance(hi, int) and lo == hi


def _item_doc_position_key(item: dict[str, Any]) -> tuple[int, float]:
    """Build a stable, comparable document-position key for a bucket item.

    Ordering fallback uses:

    1. page index (min page if multi-page provenance exists).
    2. bbox y0 (top coordinate) as an intra-page tie-breaker

    Missing values are sent to the end of the ordering.

    Parameters
    ----------
    item
        A bucket item dictionary.

    Returns
    -------
    tuple[int, float]
        (page_index, bbox_y0) ordering key.
    """

    page = item.get("doc_pos_page_index")

    if page is None:
        page = item.get("page_index")

    page_i = int(page) if isinstance(page, int) else 10**9

    y0 = item.get("doc_pos_y0")
    y0_f = float(y0) if isinstance(y0, (int, float)) else float(10**9)
    return page_i, y0_f


def _level_bounds(bucket: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    """Return (low, high) ordinals for a bucket when available.

    Parameters
    ----------
    bucket
        A bucket dictionary that may contain "level_ordinal_low", "level_ordinal_high",
        or "level_ordinal" keys representing the curriculum level information for the
        standards contained in the bucket.

    Returns
    -------
    tuple[Optional[int], Optional[int]]
        The low and high level ordinals for the bucket. If both "level_ordinal_low"
        and "level_ordinal_high" are present and valid integers, those values are
        returned. If only "level_ordinal" is present and valid, it is returned as both
        the low and high ordinal. If neither is available nor valid, (None, None) is
        returned.
    """

    lo = bucket.get("level_ordinal_low")
    hi = bucket.get("level_ordinal_high")

    if isinstance(lo, int) and isinstance(hi, int):
        return lo, hi

    ordinal = bucket.get("level_ordinal")

    if isinstance(ordinal, int):
        return int(ordinal), int(ordinal)

    return None, None


def _level_label(b: dict[str, Any]) -> str:
    """Return a human-readable label for a single-level or banded-level bucket.

    Parameters
    ----------
    b
        A bucket dictionary that may contain level information, including
        "level_ordinal_low", "level_ordinal_high", "level_label", and "stage_key" keys.

    Returns
    -------
    str
        A human-readable label for the level or banded stage represented by the bucket.
    """

    lo, hi = _level_bounds(b)

    if isinstance(lo, int) and isinstance(hi, int) and hi != lo:
        # Prefer stage_key stored directly on the bucket.
        stage_key = b.get("stage_key")

        if isinstance(stage_key, str) and stage_key.strip():
            return stage_key.strip()

        return f"LEVELS {lo}–{hi}"

    return str(
        b.get("level_label")
        or (f"LEVEL {lo}" if isinstance(lo, int) else "UNSPECIFIED_LEVEL")
    )


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
        (lp_within_level_relates_to/lp_cross_level_relates_to).

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


def _lp_statement_type_drop_reason(
    *, config: CreateKGConfig, statement_type: Optional[str]
) -> Optional[str]:
    """Return the LP drop reason for a statement type, or None if it is eligible.

    Exclude filters take precedence over include filters. This is intentional: a value
    present in both config sets is treated as explicitly blocked, not allowed.

    Parameters
    ----------
    config
        KG creation config containing LP-specific source statement-type filters.
    statement_type
        The source statement type from the StandardsFrameworkItem.

    Returns
    -------
    Optional[str]
        "lp_statement_type_excluded", "lp_statement_type_not_included", or None.
    """

    # Normalize the target statement type.
    normalized_statement_type = " ".join(str(statement_type or "").split()).casefold()

    # Normalization and blank-dropping for the exclude set.
    excluded_statement_types = {
        normalized_value
        for value in (config.lp_source_statement_types_exclude or set())
        if (normalized_value := " ".join(str(value or "").split()).casefold())
    }

    if (
        excluded_statement_types
        and normalized_statement_type in excluded_statement_types
    ):
        return "lp_statement_type_excluded"

    # Normalization and blank-dropping for the include set.
    included_statement_types = {
        normalized_value
        for value in (config.lp_source_statement_types_include or set())
        if (normalized_value := " ".join(str(value or "").split()).casefold())
    }

    if (
        included_statement_types
        and normalized_statement_type not in included_statement_types
    ):
        return "lp_statement_type_not_included"

    return None


def _make_provenance_row(
    *, candidate: CandidateEdge, inference_type: str, phase: int, **extra: Any
) -> dict[str, Any]:
    """Build a provenance row from a CandidateEdge plus phase-specific extras.

    This is the single source of truth for the common fields present in every
    provenance row. Phase-specific fields (e.g., rationale, level labels, bidirectional
    confirmation flags) are passed as keyword arguments.

    Parameters
    ----------
    candidate
        The CandidateEdge that produced this row.
    inference_type
        The inference type string (e.g., `"within_level_builds_towards"`).
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
        "confidence": candidate.confidence,
        "inference_type": inference_type,
        "phase": phase,
        "rel_type": candidate.rel_type,
        "source": str(candidate.source_sfi_uuid),
        "target": str(candidate.target_sfi_uuid),
    }
    row.update(extra)
    return row


def _normalize_level_label_key(value: Any) -> str:
    """Normalize a level/stage label for LP level-label map lookup.

    The normalization is intentionally lighter than `normalize_key_token()` because
    `lp_level_label_map` keys should remain human-recognizable. It converts the value
    to a string, strips leading/trailing whitespace, collapses internal whitespace, and
    uses `casefold()` for robust case-insensitive matching while preserving accents and
    punctuation.

    Parameters
    ----------
    value
        Raw level or stage label to normalize.

    Returns
    -------
    str
        Normalized label key, or an empty string if the input has no non-whitespace
        content.
    """

    return " ".join(str(value or "").split()).casefold()


def _path_string(topic_path_parts: list[dict[str, Any]]) -> str:
    """Convert a list of topic path parts (with optional "role" and "label" keys) into
    a compact, stable-ish context string for the LLM.

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

    for tpp in topic_path_parts:
        role = tpp["role"]
        label = (tpp.get("label") or "").strip()

        if label:
            chunks.append(f"{role}:{label}")

    return " -> ".join(chunks)


def _prepare_subject_level_samples(
    *,
    by_level: dict[str, list[dict[str, Any]]],
    excluded_subject_labels: set[str] | None = None,
    max_items: int,
) -> dict[str, dict[tuple[int, int], dict[str, Any]]]:
    """Group and sample items by subject and level range for Phase 4.

    Phase 4 cross-level/cross-stage `relatesTo` inference compares sampled items from
    the same subject-like label across adjacent level ranges. This function returns
    finalized cross-level buckets into a compact lookup keyed by subject label and
    `(level_ordinal_low, level_ordinal_high)`. Stage-banded buckets therefore remain
    truthful: e.g., `Standards I-II` is keyed as `(1, 2)` rather than as a single level.

    The function is intentionally conservative about level bounds. It validates level
    bounds using only buckets whose `subject_label` is not excluded, so noisy excluded
    groups such as `UNSPECIFIED_SUBJECT` cannot affect otherwise valid subject-level
    samples. If the included buckets under a single `level_label` still have
    inconsistent ordinal bounds, the entire level label is skipped because Phase 4
    adjacency would be unreliable.

    Examples
    --------
    1. Adjacent single-grade levels are grouped by subject

        Suppose the finalized cross-level buckets are:

            by_level = {
                "Grade 1": [
                    {
                        "subject_label": "Reading",
                        "level_ordinal_low": 1,
                        "level_ordinal_high": 1,
                        "lp_thread_key": "strand=phonics",
                        "lp_bucket_key": "strand=phonics",
                        "items": [
                            {
                                "sfi_uuid": "11111111-1111-1111-1111-111111111111",
                                "description": "Identify letter sounds.",
                                "statement_type": "Standard",
                                "topic_path": "Reading > Phonics",
                                "topic_path_key": "strand=phonics",
                            }
                        ],
                    }
                ],
                "Grade 2": [
                    {
                        "subject_label": "Reading",
                        "level_ordinal_low": 2,
                        "level_ordinal_high": 2,
                        "lp_thread_key": "strand=fluency",
                        "lp_bucket_key": "strand=fluency",
                        "items": [
                            {
                                "sfi_uuid": "22222222-2222-2222-2222-222222222222",
                                "description": "Read grade-level text fluently.",
                                "statement_type": "Standard",
                                "topic_path": "Reading > Fluency",
                                "topic_path_key": "strand=fluency",
                            }
                        ],
                    }
                ],
            }

        Calling:

            _prepare_subject_level_samples(
                by_level=by_level,
                excluded_subject_labels={"UNSPECIFIED_SUBJECT"},
                max_items=10,
            )

        returns a nested shape like:

            {
                "Reading": {
                    (1, 1): {
                        "level_label": "Grade 1",
                        "level_low": 1,
                        "level_high": 1,
                        "items": [
                            {
                                "sfi_uuid": "11111111-1111-1111-1111-111111111111",
                                "description": "Identify letter sounds.",
                                "statement_type": "Standard",
                                "topic_path": "Reading > Phonics",
                                "topic_path_key": "strand=phonics",
                                "thread_key": "strand=phonics",
                                ...
                            }
                        ],
                        "sampled_count": 1,
                        "source_item_count": 1,
                        "source_bucket_count": 1,
                        "source_thread_keys": ["strand=phonics"],
                        "source_bucket_keys": ["strand=phonics"],
                        "sampled_sfi_uuids": [
                            "11111111-1111-1111-1111-111111111111"
                        ],
                        "max_items": 10,
                    },
                    (2, 2): {
                        "level_label": "Grade 2",
                        "level_low": 2,
                        "level_high": 2,
                        "items": [...],
                        "sampled_count": 1,
                        "source_item_count": 1,
                        "source_bucket_count": 1,
                        "source_thread_keys": ["strand=fluency"],
                        "source_bucket_keys": ["strand=fluency"],
                        "sampled_sfi_uuids": [
                            "22222222-2222-2222-2222-222222222222"
                        ],
                        "max_items": 10,
                    },
                }
            }

        `_collect_relates_to_work_items()` can then compare Reading `(1, 1)`
        against Reading `(2, 2)` because the ranges are adjacent.

    2. Stage-banded levels are preserved as ranges

        Banded curricula may provide one bucket for Standards I-II and another for
        Standards III-VI:

            by_level = {
                "Standards I-II": [
                    {
                        "subject_label": "Mathematics",
                        "level_ordinal_low": 1,
                        "level_ordinal_high": 2,
                        "lp_thread_key": "learning_area=number",
                        "items": [...],
                    }
                ],
                "Standards III-VI": [
                    {
                        "subject_label": "Mathematics",
                        "level_ordinal_low": 3,
                        "level_ordinal_high": 6,
                        "lp_thread_key": "learning_area=number",
                        "items": [...],
                    }
                ],
            }

        The returned keys are:

            {
                "Mathematics": {
                    (1, 2): {"level_label": "Standards I-II", ...},
                    (3, 6): {"level_label": "Standards III-VI", ...},
                }
            }

        `_collect_relates_to_work_items()` treats these as adjacent because
        `2 + 1 == 3`. Since at least one side is banded, the later inference phase
        uses the cross-stage relatesTo prompt when `lp_cross_stage_relates_to=True`.

    3. Excluded subject labels do not affect level-bound validation

        Given:

            by_level = {
                "CE1": [
                    {
                        "subject_label": "UNSPECIFIED_SUBJECT",
                        "level_ordinal_low": 99,
                        "level_ordinal_high": 99,
                        "items": [...],
                    },
                    {
                        "subject_label": "Lecture",
                        "level_ordinal_low": 1,
                        "level_ordinal_high": 1,
                        "items": [...],
                    },
                ]
            }

        Calling with:

            excluded_subject_labels={"UNSPECIFIED_SUBJECT"}

        omits the `UNSPECIFIED_SUBJECT` bucket before checking bounds. The `Lecture`
        sample for level range `(1, 1)` is still returned. Without this ordering, the
        excluded `(99, 99)` bucket would make the level label look inconsistent and
        incorrectly skip the valid `Lecture` sample.

    4. Multiple buckets for the same subject and level are sampled together

        A subject may have several thread buckets at the same level:

            by_level = {
                "Grade 2": [
                    {
                        "subject_label": "Reading",
                        "level_ordinal_low": 2,
                        "level_ordinal_high": 2,
                        "lp_thread_key": "strand=phonics",
                        "items": [<many phonics items>],
                    },
                    {
                        "subject_label": "Reading",
                        "level_ordinal_low": 2,
                        "level_ordinal_high": 2,
                        "lp_thread_key": "strand=fluency",
                        "items": [<many fluency items>],
                    },
                ]
            }

        `_sample_items_across_threads()` samples across both Reading thread buckets,
        capped by `max_items`. This prevents one large thread from completely
        dominating the Phase 4 prompt.

    5. Duplicate subject/range aliases are warned about

        If two different level labels map to the same subject and ordinal range:

            by_level = {
                "CE1 planification": [
                    {
                        "subject_label": "Lecture",
                        "level_ordinal_low": 1,
                        "level_ordinal_high": 1,
                        "items": [...],
                    }
                ],
                "CE1 paliers": [
                    {
                        "subject_label": "Lecture",
                        "level_ordinal_low": 1,
                        "level_ordinal_high": 1,
                        "items": [...],
                    }
                ],
            }

        both map to `("Lecture", (1, 1))`. For now, this function logs a warning and
        replaces the previous sample. A future implementation should merge aliases or
        accumulate buckets before sampling.

    6. Inconsistent included bounds under one level label are skipped

        If included buckets under one level label have different bounds:

            by_level = {
                "CE1": [
                    {
                        "subject_label": "Lecture",
                        "level_ordinal_low": 1,
                        "level_ordinal_high": 1,
                        "items": [...],
                    },
                    {
                        "subject_label": "Production d'écrits",
                        "level_ordinal_low": 2,
                        "level_ordinal_high": 2,
                        "items": [...],
                    },
                ]
            }

        the function skips "CE1" entirely and logs a warning. This is intentional since
        Phase 4 depends on clean adjacent level ranges, and mixed included bounds under
        one label could create invalid cross-level relationships.

    Parameters
    ----------
    by_level
        Dictionary mapping level labels to lists of bucket dictionaries. Each bucket is
        expected to contain `subject_label`, level ordinal fields, and an `items` list.
        Buckets may also include `lp_thread_key`/`lp_bucket_key` for sampling and
        provenance.
    excluded_subject_labels
        Optional set of subject labels to skip during sampling. Buckets whose
        `subject_label` is in this set are excluded before level-bound validation.
        Typically, `{"UNSPECIFIED_SUBJECT"}` to avoid noise from unmapped items.
    max_items
        Maximum number of items to sample across threads for each subject and level
        range. If the total source items exceed this limit,
        `_sample_items_across_threads()` selects a representative subset for the LLM
        prompt.

    Returns
    -------
    dict[str, dict[tuple[int, int], dict[str, Any]]]
        Nested dictionary of sampled items and sampling provenance:

        subject_label -> (level_low, level_high) -> sample_info

        Each `sample_info` dictionary contains at least:
            - `level_label`: Human-readable source level/stage label
            - `level_low`/`level_high`: Integer ordinal range
            - `items`: Prompt-ready sampled item payloads
            - `sampled_count`: Number of sampled prompt items
            - `source_item_count`: Number of source items available before sampling
            - `source_bucket_count`: Number of source buckets sampled across
            - `source_thread_keys`: Thread keys represented by the source buckets
            - `source_bucket_keys`: Bucket keys represented by the source buckets
            - `sampled_sfi_uuids`: Sampled SFI UUIDs included in the prompt payload
            - `max_items`: Sampling cap used for this subject/level sample
    """

    subject_level_samples: dict[str, dict[tuple[int, int], dict[str, Any]]] = (
        defaultdict(dict)
    )
    excluded_subject_labels = set(excluded_subject_labels or [])

    for raw_level_label, level_buckets in by_level.items():
        bounds: list[tuple[int, int]] = []
        buckets_by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
        excluded_bucket_count = 0
        excluded_labels_seen: set[str] = set()
        exemplar_bucket: Optional[dict[str, Any]] = None

        for bucket in level_buckets:
            subject_label = str(bucket.get("subject_label") or "UNSPECIFIED_SUBJECT")

            if excluded_subject_labels and subject_label in excluded_subject_labels:
                excluded_bucket_count += 1
                excluded_labels_seen.add(subject_label)
                continue

            lo, hi = _level_bounds(bucket)

            if isinstance(lo, int) and isinstance(hi, int):
                bounds.append((lo, hi))
                exemplar_bucket = exemplar_bucket or bucket

            buckets_by_subject[subject_label].append(bucket)

        if excluded_bucket_count > 0:
            logger.info(
                f"Phase 4 subject sampling: excluded {excluded_bucket_count} "
                f"bucket(s) in level '{raw_level_label}' across "
                f"{len(excluded_labels_seen)} subject label(s): "
                f"{sorted(excluded_labels_seen)}"
            )

        if not bounds:
            continue

        level_low = min(lo for lo, _ in bounds)
        level_high = max(hi for _, hi in bounds)
        level_key = (level_low, level_high)

        if any((lo, hi) != level_key for lo, hi in bounds):
            distinct_bounds = sorted(set(bounds))
            logger.warning(
                f"Phase 4 subject sampling: SKIPPING level '{raw_level_label}' due to "
                f"inconsistent level bounds across its {len(bounds)} included bucket(s). "
                f"Distinct included (low, high) values found: {distinct_bounds}. "
                f"Aggregated level_key would be {level_key}, which could create "
                f"invalid adjacency relationships. Excluded subject labels were already "
                f"removed before this check. Fix the upstream Academic Standards export "
                f"so all included buckets within a level_label share identical ordinal "
                f"bounds."
            )
            continue

        level_label = _level_label(
            exemplar_bucket
            or {
                "level_label": raw_level_label,
                "level_ordinal_low": level_low,
                "level_ordinal_high": level_high,
            }
        )

        _process_subject_buckets(
            buckets_by_subject=buckets_by_subject,
            level_high=level_high,
            level_key=level_key,
            level_label=level_label,
            level_low=level_low,
            max_items=max_items,
            subject_level_samples=subject_level_samples,
        )

    return subject_level_samples


def _process_and_filter_candidates(
    *,
    candidates: list[CandidateEdge],
    config: CreateKGConfig,
    doc_key: str,
    sfi_context_index: Optional[dict[str, dict[str, Any]]] = None,
    within_sfi_index: Optional[dict[str, dict[str, Any]]] = None,
) -> tuple[
    list[Relationship],
    list[Relationship],
    dict[str, int],
    dict[tuple[str, str, str], str],
    dict[tuple[str, str, str], CandidateEdge],
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
    sfi_context_index
        Optional combined SFI context index used to enrich emitted relationship
        metadata with item-level, within-level, and cross-level context blocks.
    within_sfi_index
        Optional scoped within-level SFI context index used by the Phase 1
        document-order safety filter.

    Returns
    -------
    tuple
        A tuple containing:
        1. List of final buildsTowards relationships.
        2. List of final relatesTo relationships.
        3. A dictionary of counts/statistics for the report.
        4. A disposition map keyed by (rel_type, source_uuid, target_uuid) ->
            disposition string (kept, dropped_low_conf, dropped_cap).
        5. A mapping of dedupe winners keyed by (rel_type, source_uuid, target_uuid).
    """

    candidate_edges_total_pre_dedupe = len(candidates)
    candidates, dedupe_winners, dedupe_dropped, dedupe_audit_by_key = _dedupe_edges(
        candidates
    )

    builds_candidates = [e for e in candidates if e.rel_type == BUILDS_TOWARDS]
    relates_candidates = [e for e in candidates if e.rel_type == RELATES_TO]

    # Confidence thresholds.
    builds_kept = [
        e
        for e in builds_candidates
        if e.confidence >= config.lp_builds_towards_min_confidence
    ]
    builds_dropped_low = [
        e
        for e in builds_candidates
        if e.confidence < config.lp_builds_towards_min_confidence
    ]

    # Enforce within-level directionality using the exporter-derived document order.
    # The LLM is prompted with items in (supposed) curriculum order and validators
    # enforce directionality relative to the presented list. If the list is misordered,
    # directionality can be inverted even when the model follows instructions. This
    # post-filter provides a hard safety net for Phase 1 within-level buildsTowards
    # edges.
    builds_dropped_doc_order: list[CandidateEdge] = []
    builds_kept_before_doc_order = len(builds_kept)

    if within_sfi_index:
        phase_1 = [e for e in builds_kept if int(e.metadata.get("phase") or 0) == 1]
        non_phase_1 = [e for e in builds_kept if int(e.metadata.get("phase") or 0) != 1]

        phase_1_kept, builds_dropped_doc_order = (
            _filter_builds_towards_within_level_order(
                edges=phase_1, within_sfi_index=within_sfi_index
            )
        )
        builds_kept = [*phase_1_kept, *non_phase_1]

    relates_kept_thr = [
        e
        for e in relates_candidates
        if e.confidence >= config.lp_relates_to_min_confidence
    ]
    relates_dropped_low = [
        e
        for e in relates_candidates
        if e.confidence < config.lp_relates_to_min_confidence
    ]

    # Limit relatesTo per SFI.
    relates_kept, relates_dropped_cap = _limit_relates_to_edges_per_sfi(
        edges=relates_kept_thr,
        max_edges_per_sfi=int(config.lp_relates_to_max_edges_per_sfi),
    )

    builds_relationships: list[Relationship] = [
        _emit_relationship(
            candidate=e,
            config=config,
            doc_key=doc_key,
            sfi_context_index=sfi_context_index,
        )
        for e in builds_kept
    ]

    relates_relationships: list[Relationship] = [
        _emit_relationship(
            candidate=e,
            config=config,
            doc_key=doc_key,
            sfi_context_index=sfi_context_index,
        )
        for e in relates_kept
    ]

    stats = {
        "candidate_edges_total_pre_dedupe": candidate_edges_total_pre_dedupe,
        "candidate_edges_total_after_dedupe": len(candidates),
        "candidate_edges_dedupe_duplicate_groups": sum(
            1 for records in dedupe_audit_by_key.values() if len(records) > 1
        ),
        "candidate_edges_dedupe_groups": len(dedupe_audit_by_key),
        "candidate_edges_dropped_dedupe": int(dedupe_dropped),
        "candidate_builds_towards": len(builds_candidates),
        "candidate_relates_to": len(relates_candidates),
        "builds_kept": len(builds_kept),
        "builds_kept_before_doc_order": int(builds_kept_before_doc_order),
        "builds_dropped_doc_order": len(builds_dropped_doc_order),
        "builds_dropped_low_conf": len(builds_dropped_low),
        "relates_kept_after_threshold": len(relates_kept_thr),
        "relates_dropped_low_conf": len(relates_dropped_low),
        "relates_kept_after_cap": len(relates_kept),
        "relates_dropped_cap": len(relates_dropped_cap),
    }

    # Build disposition map keyed by (rel_type, source_uuid, target_uuid) for enriching
    # provenance rows downstream.
    #
    # NB: relatesTo edges are canonicalized during dedup (lexicographic UUID string
    # order), so keys here are already canonical for relatesTo. The *lookup* side (in
    # export_learning_progressions) must canonicalize provenance row keys the same way
    # (see _canon_disposition_key).
    disposition_map: dict[tuple[str, str, str], str] = {}

    for e in builds_kept:
        _set_disposition(candidate=e, disposition_map=disposition_map, value="kept")

    for e in builds_dropped_doc_order:
        _set_disposition(
            candidate=e, disposition_map=disposition_map, value="dropped_doc_order"
        )

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

    return (
        builds_relationships,
        relates_relationships,
        stats,
        disposition_map,
        dedupe_winners,
    )


def _process_single_standard(
    *,
    cross_level_buckets: DefaultDict[str, DefaultDict[str, dict[str, Any]]],
    drops: dict[str, list[dict[str, Any]]],
    config: CreateKGConfig,
    include_provenance: bool,
    normalized_level_label_map: dict[str, int],
    order_index_lookup: dict[str, int],
    sfi: StandardsFrameworkItem,
    within_level_buckets: DefaultDict[str, DefaultDict[str, dict[str, Any]]],
) -> None:
    """Process a single standard item and sort it into buckets or drops.

    The bucketing logic computes independent axes (level ordinal, subject label,
    within-level bucket key, and cross-level thread key) using config-driven mappings:

    1. **LP source eligibility** is checked first. The item must be an exported
        normative Standard SFI and must pass
        `lp_source_statement_types_include`/`lp_source_statement_types_exclude`
        filters. This lets broad structural competency statements remain in the
        Academic Standards KG while being excluded from LP inference.
    2. **Level bounds** are resolved primarily from ordinals in `progression_context`
        (`level_ordinal_low/high` or `stage_ordinal_low/high`). If ordinals are absent,
        we fall back to `config.lp_level_label_map` using `level_key` or `stage_key`.
    3. **Subject label** is resolved via `config.lp_subject_role`. Items without a
        matching role get `UNSPECIFIED_SUBJECT`.
    4. **Within-level bucket key** uses `config.lp_within_level_bucket_roles`, with
        configured `lp_within_level_fallback_fields` as a source-field fallback.
    5. **Cross-level thread key** uses `config.lp_cross_level_thread_roles`, or the
        default `progression_context.thread_key` when that config is None. Items with
        no usable key receive a per-level sentinel to prevent false cross-level
        matching.

    NB: Banded/stage-level curricula (e.g., Tanzania "Standard I–II",
    "Standard III–VI"): When `progression_context` includes a true range
        (low != high), the bucket stores `level_ordinal_low != level_ordinal_high`,
        enabling cross-stage inference phases. If ordinals are unavailable and we rely
        on the config map, the bucket is treated as a single representative level
        (low == high).

    NB: Phase 3 is specifically trying to infer cross-subject or cross-strand
    associative links, not sequence links. As an example, in the Senegal reading setup,
    `lp_subject_role = "strand"` means: “For Phase 3, treat each `strand` as the
    subject-like partition.” So Phase 3 asks questions like: "Are there meaningful
    `relatesTo` links between expectations in **Lecture** and expectations in
    **Production d’écrits**?" or "Are expectations in **Communication orale**
    associated with expectations in **Communication écrite**?" For that to work, each
    item needs a reliable strand label. If an item has no `strand`, the code cannot
    know which side of the comparison it belongs to. It becomes "UNSPECIFIED_SUBJECT",
    and including those items would create comparisons like:

        UNSPECIFIED_SUBJECT × Lecture
        UNSPECIFIED_SUBJECT × Production d’écrits
        UNSPECIFIED_SUBJECT × Communication orale

    Those edges are likely noisy because “unspecified” is not a real pedagogical
    category. It is an extraction/mapping gap.

    In other words, the key distinction is:

        Phase 1 buildsTowards:
            "Within this bucket, does item A build toward item B?"

        Phase 3 relatesTo:
            "Across subject-like partitions, is item A related to item B?"

    For Phase 1, a missing `strand` is less dangerous because the fallback can still
    create a meaningful local bucket from `statement_type`, for example:

        statement_type=orthographe
        statement_type=grammaire
        statement_type=ecriture_copie

    That can still support sequence inference: Orthographe item 1 -> Orthographe item 2.

    But for Phase 3, `statement_type` is not a clean replacement for `strand`. If we
    used `statement_type` as the subject-like partition, we would be asking
    cross-“subject” questions like:

        Orthographe × Grammaire
        Écriture / Copie × Vocabulaire
        Objectif spécifique × Contenus

    Some of those may be useful, but they are not the same semantic layer as
    strand-to-strand relationships. We would be mixing source column labels, skill
    tracks, and actual curriculum strands. That can create a lot of false `relatesTo`
    edges.

    Parameters
    ----------
    cross_level_buckets
        A nested dictionary for organizing standards into cross-level thread buckets.
    config
        The KG creation config with LP-specific fields.
    drops
        A dictionary for collecting standards that are dropped due to validation
        issues, categorized by the reason for dropping.
    include_provenance
        Whether to include prompt-facing provenance fields such as `page_index`.
        Internal ordering fields such as `doc_pos_page_index` and `doc_pos_y0` are
        always retained because they are used for deterministic sorting and
        reverse-edge filtering.
    normalized_level_label_map
        Precomputed normalized mapping from configured level labels to integer ordinals.
    order_index_lookup
        A mapping from canonical IR node ID strings to sibling order indices. Used to
        convert `progression_context.canon_order_path` into a numeric order path for
        correct document-order sorting.
    sfi
        The standard item to process.
    within_level_buckets
        A nested dictionary for organizing standards into within-level inference
        buckets.
    """

    metadata = sfi.metadata or {}
    progression_context = metadata.get("progression_context") or {}
    sfi_uuid = str(sfi.case_identifier_uuid or sfi.identifier)

    # LP source eligibility: only exported normative Standards can participate in LP
    # inference, and broad/source-structural statement types can be blocked explicitly
    # without removing them from the Academic Standards KG.
    if sfi.normalized_statement_type != "Standard":
        drops.setdefault("non_standard_item", []).append(
            {
                "description": sfi.description,
                "normalized_statement_type": sfi.normalized_statement_type,
                "sfi_uuid": sfi_uuid,
                "statement_type": sfi.statement_type,
            }
        )
        return

    statement_type_drop_reason = _lp_statement_type_drop_reason(
        config=config, statement_type=sfi.statement_type
    )

    if statement_type_drop_reason:
        drops.setdefault(statement_type_drop_reason, []).append(
            {
                "description": sfi.description,
                "normalized_statement_type": sfi.normalized_statement_type,
                "sfi_uuid": sfi_uuid,
                "statement_type": sfi.statement_type,
            }
        )
        return

    # Level validation (level or stage).
    level_data = _resolve_level_ordinals(
        drops=drops,
        normalized_level_label_map=normalized_level_label_map,
        progression_context=progression_context,
        sfi_description=sfi.description,
        sfi_uuid=sfi_uuid,
    )

    if not level_data:
        return

    level_lo, level_hi, level_key, normalized_level_key, level_basis = level_data

    # Level label (used as the top-level key for grouping buckets). Prefer the source
    # level label (e.g., "CE1" or "Standard III–VI") over synthetic "LEVEL N" labels so
    # reports and prompt logs remain interpretable.
    level_key_label = (
        level_key.strip() if isinstance(level_key, str) and level_key.strip() else None
    )

    level_label = (
        (
            f"{level_key_label} [{level_lo}–{level_hi}]"
            if level_hi != level_lo
            else level_key_label
        )
        if level_key_label
        else (
            f"LEVEL {level_lo}-{level_hi}"
            if level_hi != level_lo
            else f"LEVEL {level_lo}"
        )
    )

    # Topic path setup. Prefer the exported aggregate `topic_path_key`, but if it is
    # missing, derive a local signature from `topic_path_parts`.
    #
    # Do not drop the SFI when no topic path is available. `_compute_bucket_keys()` can
    # still place the item into a configured source-field fallback bucket (e.g.,
    # `statement_type=orthographe`) or, failing that, a level-specific unthreaded
    # sentinel. Treat a missing topic path as weak context, not as LP ineligibility.
    raw_topic_path_parts = progression_context.get("topic_path_parts")
    topic_path_parts = (
        raw_topic_path_parts if isinstance(raw_topic_path_parts, list) else []
    )
    raw_topic_key = progression_context.get("topic_path_key", "")
    topic_key = raw_topic_key.strip() if isinstance(raw_topic_key, str) else ""

    if not topic_key:
        topic_key = _topic_path_signature(topic_path_parts)

    topic_path_key_missing = not bool(topic_key)

    # Subject label setup.
    subject_label = _resolve_subject_label(
        subject_role=config.lp_subject_role, topic_path_parts=topic_path_parts
    )

    # Fallback segments for within-level thread/bucket keys. This is a list of strings
    # that can be joined and used as a fallback key when the primary role-based keys
    # are not available.
    fallback_segments = _build_fallback_segments(
        config=config, metadata=metadata, sfi=sfi
    )

    # Within-level and cross-level keys are deliberately separate. The within-level key
    # controls which items the Phase 1 LLM can compare inside one level. The
    # cross-level key controls which buckets can be matched across adjacent levels.
    default_thread_key = (
        str(progression_context.get("thread_key") or "").strip() or None
    )
    within_bucket_key, within_thread_key = _compute_bucket_keys(
        default_thread_key=default_thread_key,
        fallback_segments=fallback_segments,
        normalized_level_key=normalized_level_key,
        roles=config.lp_within_level_bucket_roles,
        subject_label=subject_label,
        topic_path_parts=topic_path_parts,
    )
    cross_bucket_key, cross_thread_key = _compute_bucket_keys(
        default_thread_key=default_thread_key,
        fallback_segments=None,
        normalized_level_key=normalized_level_key,
        roles=config.lp_cross_level_thread_roles,
        subject_label=subject_label,
        topic_path_parts=topic_path_parts,
    )

    # Get or create buckets for the SFI. Within-level buckets are used by Phase 1
    # within-level buildsTowards inference and Phase 3 within-level cross-subject
    # relatesTo inference. Cross-level buckets are used by Phase 2/4 adjacent-level
    # inference. Items with the same bucket key are presented together to the LLM, so
    # the keys should reflect meaningful pedagogical groupings. The thread keys are
    # included in the bucket data for debugging and cross-level grouping.
    within_bucket = _get_or_create_bucket(
        bucket_key_value=within_bucket_key,
        bucket_scope="within_level",
        default_thread_key=default_thread_key,
        fallback_segments=fallback_segments,
        level_basis=level_basis,
        level_hi=level_hi,
        level_key=level_key,
        level_label=level_label,
        level_lo=level_lo,
        store=within_level_buckets,
        subject_label=subject_label,
        thread_key_value=within_thread_key,
        topic_key=topic_key,
        topic_path_parts=topic_path_parts,
    )
    cross_bucket = _get_or_create_bucket(
        bucket_key_value=cross_bucket_key,
        bucket_scope="cross_level",
        default_thread_key=default_thread_key,
        fallback_segments=None,
        level_basis=level_basis,
        level_hi=level_hi,
        level_key=level_key,
        level_label=level_label,
        level_lo=level_lo,
        store=cross_level_buckets,
        subject_label=subject_label,
        thread_key_value=cross_thread_key,
        topic_key=topic_key,
        topic_path_parts=topic_path_parts,
    )

    # Payload generation and append.
    raw_canon_order_path = progression_context.get("canon_order_path", [])
    canon_order_path = (
        raw_canon_order_path if isinstance(raw_canon_order_path, list) else []
    )
    numeric_order_path, numeric_order_missing_count = (
        _resolve_canonical_order_path_to_indices(
            canon_order_path=canon_order_path,
            missing_default=0,
            order_index_lookup=order_index_lookup,
        )
    )

    # Provenance-derived document position (page index and y0) for potential fallback
    # ordering signals.
    raw_indices = metadata.get("page_indices")
    valid_indices = (
        [idx for idx in raw_indices or [] if isinstance(idx, int)]
        if isinstance(raw_indices, list)
        else []
    )
    doc_pos_page_index = min(valid_indices) if valid_indices else None
    bbox = metadata.get("bbox")
    doc_pos_y0: Optional[float] = None

    if (
        isinstance(bbox, (list, tuple))
        and len(bbox) == 4
        and isinstance(bbox[1], (int, float))
    ):
        doc_pos_y0 = float(bbox[1])

    # Build a comprehensive payload for the item that includes all relevant context for
    # LLM inference and debugging. This payload is included in both the within-level
    # and cross-level buckets, as it may be useful for both types of inference and for
    # understanding how items are grouped and compared.
    payload: dict[str, Any] = {
        "canon_order_path": canon_order_path,
        "code_tuple": progression_context.get("code_tuple"),
        "description": sfi.description,
        "notes": sfi.notes,
        "numeric_order_missing_count": numeric_order_missing_count,
        "numeric_order_path": numeric_order_path,
        "level_basis": level_basis,
        "level_key": (
            level_key.strip()
            if isinstance(level_key, str) and level_key.strip()
            else None
        ),
        "order_index_within_parent": progression_context.get(
            "order_index_within_parent"
        ),
        "sfi_uuid": sfi_uuid,
        "statement_code": sfi.statement_code,
        "statement_type": sfi.statement_type,
        # Provenance-derived ordering fallback (kept even when include_provenance=False).
        "doc_pos_page_index": doc_pos_page_index,
        "doc_pos_y0": doc_pos_y0,
        # Item-level topic context.
        "default_thread_key": str(progression_context.get("thread_key") or ""),
        "topic_path": _path_string(topic_path_parts),
        "topic_path_key": topic_key,
        "topic_path_key_missing": topic_path_key_missing,
        # Bucket/thread context kept separately for debugging.
        "cross_level_bucket_key": cross_bucket_key,
        "cross_level_thread_key": cross_thread_key,
        "within_level_bucket_key": within_bucket_key,
        "within_level_thread_key": within_thread_key,
    }

    if include_provenance:
        payload["page_index"] = doc_pos_page_index

    within_payload = dict(payload)
    within_payload["within_level_fallback_segments"] = fallback_segments
    cross_payload = dict(payload)
    within_bucket["items"].append(within_payload)
    cross_bucket["items"].append(cross_payload)


def _process_subject_buckets(
    *,
    buckets_by_subject: dict[str, list[dict[str, Any]]],
    level_high: int,
    level_key: tuple[int, int],
    level_label: str,
    level_low: int,
    max_items: int,
    subject_level_samples: dict[str, dict[tuple[int, int], dict[str, Any]]],
) -> None:
    """Process and sample items across threads for a specific level range.

    Iterates over buckets grouped by subject, sorts them to ensure deterministic
    sampling, and samples items across threads. Mutates `subject_level_samples` in
    place with the newly constructed prompt payload and provenance metadata. If a
    duplicate subject and level range alias is found, it replaces the existing
    entry and logs a warning.

    Parameters
    ----------
    buckets_by_subject
        Dictionary mapping subject labels to their lists of buckets.
    level_high
        The highest ordinal bound for the current level range.
    level_key
        A tuple of `(level_low, level_high)` representing the ordinal range.
    level_label
        The human-readable label for the current level or stage.
    level_low
        The lowest ordinal bound for the current level range.
    max_items
        Maximum number of items to sample across threads for each subject.
    subject_level_samples
        The nested dictionary to populate with the finalized samples.
    """

    for subject_label, thread_buckets in buckets_by_subject.items():
        thread_buckets_sorted = sorted(
            thread_buckets,
            key=lambda b: (
                _bucket_topic_context(bucket=b),
                str(b.get("lp_thread_key") or b.get("lp_bucket_key") or ""),
            ),
        )
        sampled = _sample_items_across_threads(
            max_items=max_items, thread_buckets=thread_buckets_sorted
        )

        if not sampled:
            continue

        prompt_items = [
            _build_item_payload(item=item, thread_key_field="_thread_key")
            for item in sampled
        ]
        source_thread_keys = sorted(
            {
                str(b.get("lp_thread_key") or b.get("lp_bucket_key") or "").strip()
                for b in thread_buckets_sorted
                if str(b.get("lp_thread_key") or b.get("lp_bucket_key") or "").strip()
            }
        )
        source_bucket_keys = sorted(
            {
                str(b.get("lp_bucket_key") or b.get("bucket_key") or "").strip()
                for b in thread_buckets_sorted
                if str(b.get("lp_bucket_key") or b.get("bucket_key") or "").strip()
            }
        )
        source_item_count = sum(
            len(b.get("items") or []) for b in thread_buckets_sorted
        )
        sampled_sfi_uuids = [str(it.get("sfi_uuid")) for it in prompt_items]

        existing_sample = subject_level_samples[subject_label].get(level_key)

        if existing_sample is not None:
            logger.warning(
                f"Phase 4 subject sampling: duplicate sample for subject "
                f"'{subject_label}' and level range {level_key}. Existing "
                f"level_label={existing_sample.get('level_label')}; new "
                f"level_label={level_label}. Replacing the existing sample for "
                f"now. Consider merging level-label aliases before sampling."
            )

        subject_level_samples[subject_label][level_key] = {
            "level_label": level_label,
            "level_low": level_low,
            "level_high": level_high,
            "items": prompt_items,
            "max_items": max_items,
            "sampled_count": len(prompt_items),
            "sampled_sfi_uuids": sampled_sfi_uuids,
            "source_bucket_count": len(thread_buckets_sorted),
            "source_bucket_keys": source_bucket_keys,
            "source_item_count": source_item_count,
            "source_thread_keys": source_thread_keys,
        }


def _replace_candidate_metadata(
    *, candidate: CandidateEdge, metadata: dict[str, Any]
) -> CandidateEdge:
    """Return a CandidateEdge copy with updated metadata.

    Parameters
    ----------
    candidate
        Existing candidate edge.
    metadata
        Replacement metadata dictionary.

    Returns
    -------
    CandidateEdge
        Candidate copy with all original fields preserved except metadata.
    """

    return CandidateEdge(
        confidence=candidate.confidence,
        evidence=candidate.evidence,
        inference_source=candidate.inference_source,
        inference_type=candidate.inference_type,
        llm_confidence=candidate.llm_confidence,
        metadata=metadata,
        rel_type=candidate.rel_type,
        source_sfi_uuid=candidate.source_sfi_uuid,
        target_sfi_uuid=candidate.target_sfi_uuid,
    )


def _resolve_canonical_order_path_to_indices(
    *,
    canon_order_path: Any,
    missing_default: int = 0,
    order_index_lookup: dict[str, int],
) -> tuple[list[int], int]:
    """Convert a canonical-node path into sibling-order indices.

    Academic Standards SFIs store `progression_context.canon_order_path` as canonical
    IR node IDs from the exported hierarchy down to the leaf expectation. UUIDs are
    stable identifiers, but they are not sortable in curriculum order. This function
    resolves each canonical node ID to the sibling order index recorded during Academic
    Standards export, producing a lexicographically comparable numeric path.

    The numeric path is used to:

    1. Sort items inside LP inference buckets before prompting the LLM, and
    2. Filter Phase-1 `buildsTowards` edges that contradict within-level curriculum
        order.

    Missing canonical IDs are replaced with `missing_default`.

    At a high level, this function converts this:

    [
        "canonical_section_node_id",
        "canonical_palier_node_id",
        "canonical_week_node_id",
        "canonical_expectation_node_id",
    ]

    into this:

    [4, 1, 6, 3]

    where each number is the node's sibling order index in the Academic Standards
    hierarchy.

    This is important because `canon_order_path` stores canonical node IDs, but UUIDs
    are not pedagogically sortable. The function uses `order_index_lookup`, which is
    built from Academic Standards hasChild relationship metadata such as
    `canonical_child_id`, `canonical_order_index`, and `export_order_index`.

    For example, for a Senegal reading item, a path like:

    "canon_order_path": [
      "46d148dc-0eda-5f8a-ac3b-d025d17e6f7a",
      "e9f53d00-b113-5a37-9949-7d9549312e80",
      "99b0d4f5-b3d4-53a6-9a96-adf5cbe09033",
      "13ceb96a-ff49-56b0-a362-8a738900793a"
    ]

    can become something like:

    [4, 1, 6, 3]

    meaning roughly:

    4th top-level exported section
    -> 1st child under that section
    -> 6th child under that palier/substage
    -> 3rd leaf item in that local table/row group

    NB: This function is needed for two later steps.

    First, it lets the LP exporter sort items inside each bucket in curriculum order
    before sending them to the LLM. The final bucket-sort key uses:

    (
        numeric_order_missing_count,
        numeric_order_path,
        _item_doc_position_key(...),
        _sort_key_for_bucket_sfi(...)
    )

    So complete numeric order paths win; page/bbox position is only a fallback.

    Second, it lets the exporter reject bad within-level buildsTowards edges that go
    backward. The reverse-edge filter compares `numeric_order_path` when both source
    and target have complete paths; only if that fails does it fall back to provenance
    position (page_index, bbox_y0).

    Examples
    --------
    1. Complete canonical path from Senegal reading

        The Academic Standards export stores canonical node IDs in each SFI's
        progression context:

            canon_order_path = [
                "46d148dc-0eda-5f8a-ac3b-d025d17e6f7a",  # section
                "e9f53d00-b113-5a37-9949-7d9549312e80",  # palier/substage
                "99b0d4f5-b3d4-53a6-9a96-adf5cbe09033",  # week/table row group
                "13ceb96a-ff49-56b0-a362-8a738900793a",  # expectation leaf
            ]

        and `_build_order_index_lookup()` resolves each canonical node ID to its
        sibling order index:

            order_index_lookup = {
                "46d148dc-0eda-5f8a-ac3b-d025d17e6f7a": 4,
                "e9f53d00-b113-5a37-9949-7d9549312e80": 1,
                "99b0d4f5-b3d4-53a6-9a96-adf5cbe09033": 6,
                "13ceb96a-ff49-56b0-a362-8a738900793a": 4,
            }

        Calling:

            _resolve_canonical_order_path_to_indices(
                canon_order_path=canon_order_path,
                missing_default=0,
                order_index_lookup=order_index_lookup,
            )

        returns:

            [4, 1, 6, 4], 0

        This numeric path can be compared lexicographically with another item's path
        to preserve curriculum order inside a progression bucket.

    2. Adjacent items in the same local ordering domain

        Suppose two Orthographe expectations are under the same section and palier but
        appear in different week/table-row positions:

            item_a_path = ["section_id", "palier_2_id", "week_14_id", "leaf_a_id"]
            item_b_path = ["section_id", "palier_2_id", "week_16_id", "leaf_b_id"]

            order_index_lookup = {
                "section_id": 4,
                "palier_2_id": 1,
                "week_14_id": 4,
                "week_16_id": 6,
                "leaf_a_id": 3,
                "leaf_b_id": 3,
            }

        The resolved paths are:

            item_a_numeric = [4, 1, 4, 3]
            item_b_numeric = [4, 1, 6, 3]

        Since:

            item_a_numeric < item_b_numeric

        item A precedes item B. A Phase-1 `buildsTowards` edge from A to B is
        **directionally** plausible; an edge from B to A can be filtered as backwards.

    3. Missing canonical node IDs

        If one canonical ID is missing from the lookup:

            canon_order_path = ["section_id", "unknown_week_id", "leaf_id"]

            order_index_lookup = {
                "section_id": 4,
                "leaf_id": 2,
            }

        then:

            _resolve_canonical_order_path_to_indices(
                canon_order_path=canon_order_path,
                missing_default=0,
                order_index_lookup=order_index_lookup,
            )

        returns:

            [4, 0, 2], 1

        The function counts the missing ID:

            numeric_order_missing_count = 1

        Downstream ordering logic should only trust `numeric_order_path` fully when
        `numeric_order_missing_count == 0`; otherwise it can fall back to page/bbox
        provenance.

    4. Non-list or blank path values

        Non-list inputs return an empty path and zero missing counts:

            _resolve_canonical_order_path_to_indices(
                canon_order_path=None,
                missing_default=0,
                order_index_lookup={},
            )

        returns:

            [], 0

        Blank values inside a list are skipped:

            _resolve_canonical_order_path_to_indices(
                canon_order_path=["section_id", "", None, "leaf_id"],
                missing_default=0,
                order_index_lookup={"section_id": 1, "leaf_id": 5},
            )

        returns:

            [1, 5], 0

    Parameters
    ----------
    canon_order_path
        A list of canonical IR node IDs representing the path from the exported
        hierarchy root to the leaf node. Non-list values return an empty path.
    missing_default
        The integer value to use for canonical node IDs that are not present in
        `order_index_lookup`.
    order_index_lookup
        A mapping from canonical IR node ID strings to sibling order indices.

    Returns
    -------
    tuple[list[int], int]
        A list of integer order indices corresponding to `canon_order_path` and the
        count of missing canonical node IDs that were not found in `order_index_lookup`.
    """

    if not isinstance(canon_order_path, list):
        canon_order_path = []

    numeric_order_missing_count = 0
    numeric_path: list[int] = []

    for value in canon_order_path:
        key = str(value or "").strip()

        if not key:
            continue

        if key in order_index_lookup:
            numeric_path.append(order_index_lookup[key])
        else:
            numeric_path.append(missing_default)
            numeric_order_missing_count += 1

    return numeric_path, numeric_order_missing_count


def _resolve_forbidden_pairs(
    *,
    forbidden_builds_pairs: set[tuple[UUID, UUID]],
    lower_items: list[dict[str, Any]],
    upper_items: list[dict[str, Any]],
) -> tuple[set[tuple[str, str]], list[dict[str, Any]]]:
    """Resolve current prompt relatesTo exclusions from accepted buildsTowards pairs.

    Phase 4 infers cross-level/cross-stage `relatesTo` edges. A pair that already has
    an accepted Phase 2 `buildsTowards` relationship should not also be emitted as
    `relatesTo`, because `relatesTo` is reserved for associative concept links rather
    than prerequisite/progression links.

    This function filters the global set of accepted cross-level/cross-stage
    buildsTowards pairs down to the pairs that cross the current lower/upper prompt
    item lists. The returned pairs are canonicalized as undirected UUID-string tuples
    because `relatesTo` is conceptually undirected and the Phase 4 prompt is run in
    both lower -> upper and upper -> lower presentation orders.

    Examples
    --------
    1. One accepted buildsTowards pair applies to the current prompt

        Given:

            forbidden_builds_pairs = {
                (
                    UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                    UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
                ),
                (
                    UUID("xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"),
                    UUID("yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy"),
                ),
            }

            lower_items = [
                {"sfi_uuid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"},
                {"sfi_uuid": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"},
            ]

            upper_items = [
                {"sfi_uuid": "dddddddd-dddd-dddd-dddd-dddddddddddd"},
                {"sfi_uuid": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"},
            ]

        only the A-D pair crosses the current lower/upper item lists, so the function
        returns:

            (
                {
                    (
                        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                        "dddddddd-dddd-dddd-dddd-dddddddddddd",
                    )
                },
                [
                    {
                        "a_sfi_uuid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                        "b_sfi_uuid": "dddddddd-dddd-dddd-dddd-dddddddddddd",
                    }
                ],
            )

    2. Direction does not matter

        If the accepted buildsTowards pair is stored as upper -> lower:

            forbidden_builds_pairs = {
                (
                    UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
                    UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                )
            }

        and the current prompt still compares lower item A against upper item D, the
        pair is still forbidden. The returned set uses `canon_str_pair()`, so the pair
        is represented in stable lexicographic order regardless of the original
        buildsTowards direction.

    3. Irrelevant buildsTowards pairs are ignored

        Given:

            forbidden_builds_pairs = {
                (
                    UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                    UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
                )
            }

            lower_items = [
                {"sfi_uuid": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"},
            ]

            upper_items = [
                {"sfi_uuid": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"},
            ]

        no accepted buildsTowards pair crosses the current lower/upper item lists, so
        the function returns:

            set(), []

    Parameters
    ----------
    forbidden_builds_pairs
        Global set of accepted Phase 2 cross-level/cross-stage buildsTowards pairs.
        These pairs should be excluded from Phase 4 relatesTo inference when both
        endpoints appear in the current lower/upper item lists.
    lower_items
        Prompt payload items for the lower level/range in the current Phase 4
        comparison. Each item must contain `sfi_uuid`.
    upper_items
        Prompt payload items for the upper level/range in the current Phase 4
        comparison. Each item must contain `sfi_uuid`.

    Returns
    -------
    tuple[set[tuple[str, str]], list[dict[str, Any]]]
        A tuple containing:
        1. `forbidden_pairs_set`: undirected canonical UUID-string pairs for validator
           membership checks.
        2. `forbidden_pairs_list`: the same pairs as sorted dictionaries with
           `a_sfi_uuid` and `b_sfi_uuid`, suitable for JSON prompt payloads.
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


def _resolve_level_ordinals(
    *,
    drops: dict[str, list[dict[str, Any]]],
    normalized_level_label_map: dict[str, int],
    progression_context: dict[str, Any],
    sfi_description: str | None,
    sfi_uuid: str,
) -> tuple[int, int, str, str, str] | None:
    """Resolve grade/stage level ordinals for LP bucketing.

    Preference order:

    1. Explicit grade ordinal range from `progression_context`.
    2. Explicit stage ordinal range from `progression_context`.
    3. Config fallback via `config.lp_level_label_map` using `grade_key` or `stage_key`.

    The returned `level_key` and `level_basis` identify which source actually resolved
    the level.

    Examples
    --------
    1. Explicit single-level ordinals from Academic Standards export

        For the Senegal CE1 reading curriculum, `as_default_level_context` may write a
        document-level grade context into each expectation SFI:

            progression_context = {
                "grade_key": "CE1",
                "grade_ordinal_low": 1,
                "grade_ordinal_high": 1,
                "stage_key": None,
                "stage_ordinal_low": None,
                "stage_ordinal_high": None,
            }

        The function returns:

            (1, 1, "CE1", "ce1", "grade_ordinals")

        This is the normal path for single-level PDFs when the Academic Standards
        export has already populated `metadata.progression_context`.

    2. Explicit grade band

        Some curricula assign standards to a multi-grade band:

            progression_context = {
                "grade_key": "Standard III–VI",
                "grade_ordinal_low": 3,
                "grade_ordinal_high": 6,
                "stage_key": None,
                "stage_ordinal_low": None,
                "stage_ordinal_high": None,
            }

        The function returns:

            (3, 6, "Standard III–VI", "standard iii–vi", "grade_ordinals")

        Downstream code can detect this as a banded bucket because
        `level_lo != level_hi`.

    3. Explicit stage ordinals when grade ordinals are unavailable

        Stage-based curricula may provide stage ordinals instead of grade ordinals:

            progression_context = {
                "grade_key": None,
                "grade_ordinal_low": None,
                "grade_ordinal_high": None,
                "stage_key": "Étape 2",
                "stage_ordinal_low": 2,
                "stage_ordinal_high": 2,
            }

        The function returns:

            (2, 2, "Étape 2", "étape 2", "stage_ordinals")

        Stage ordinals are used only when a complete grade ordinal range is not present.

    4. Grade label fallback via lp_level_label_map

        If the Academic Standards export preserved a grade label but did not populate
        ordinals:

            progression_context = {
                "grade_key": "CE1",
                "grade_ordinal_low": None,
                "grade_ordinal_high": None,
                "stage_key": None,
                "stage_ordinal_low": None,
                "stage_ordinal_high": None,
            }

        and the run config contains:

            lp_level_label_map = {"ce1": 1}

        then the function returns:

            (1, 1, "CE1", "ce1", "level_label_map_grade_key")

        Fallback map keys are matched using whitespace collapse + `casefold()`. They
        are not normalized with `normalize_key_token()`.

    5. Stage label fallback via lp_level_label_map

        If only a stage label exists:

            progression_context = {
                "grade_key": None,
                "grade_ordinal_low": None,
                "grade_ordinal_high": None,
                "stage_key": "Étape 2",
                "stage_ordinal_low": None,
                "stage_ordinal_high": None,
            }

        and the run config contains:

            lp_level_label_map = {"étape 2": 2}

        then the function returns:

            (2, 2, "Étape 2", "étape 2", "level_label_map_stage_key")

    6. Missing level key and missing ordinals

        If neither grade/stage ordinals nor a usable grade/stage key exist:

            progression_context = {
                "grade_key": None,
                "grade_ordinal_low": None,
                "grade_ordinal_high": None,
                "stage_key": None,
                "stage_ordinal_low": None,
                "stage_ordinal_high": None,
            }

        the function appends a record to `drops["missing_level_key"]` and returns:

            None

    7. Unmapped level key

        If a level label exists but there is no explicit ordinal and no matching config
        fallback:

            progression_context = {
                "grade_key": "CE1",
                "grade_ordinal_low": None,
                "grade_ordinal_high": None,
                "stage_key": None,
                "stage_ordinal_low": None,
                "stage_ordinal_high": None,
            }

            config.lp_level_label_map = None

        the function appends a record to `drops["unmapped_level_key"]` and returns:

            None

    Parameters
    ----------
    drops
        A dictionary for collecting standards that are dropped due to validation issues.
    normalized_level_label_map
        Precomputed normalized mapping from configured level labels to integer ordinals.
    progression_context
        The progression context extracted from the standard item's metadata.
    sfi_description
        The description of the standard item.
    sfi_uuid
        The UUID of the standard item.

    Returns
    -------
    tuple[int, int, str, str, str] | None
        (level_lo, level_hi, level_key, normalized_level_key, level_basis), or None
        when no usable level can be resolved.
    """

    def _clean_label(value: Any) -> str | None:
        """Clean a label value for use as a level key in bucket metadata.

        Parameters
        ----------
        value
            The raw label value to clean.

        Returns
        -------
        str | None
            The cleaned label, obtained by converting the input to a string, stripping
            leading/trailing whitespace, and collapsing internal whitespace. Returns
            None if the cleaned label is empty.
        """

        s = " ".join(str(value or "").split())
        return s or None

    grade_key = progression_context.get("grade_key")
    stage_key = progression_context.get("stage_key")

    grade_key_label = _clean_label(grade_key)
    stage_label = _clean_label(stage_key)

    normalized_grade_key = _normalize_level_label_key(grade_key_label)
    normalized_stage_key = _normalize_level_label_key(stage_label)

    g_lo = progression_context.get("grade_ordinal_low")
    g_hi = progression_context.get("grade_ordinal_high")
    s_lo = progression_context.get("stage_ordinal_low")
    s_hi = progression_context.get("stage_ordinal_high")

    if isinstance(g_lo, int) and isinstance(g_hi, int):
        normalized_level_key = normalized_grade_key
        level_basis = "grade_ordinals"
        level_key = grade_key_label
        level_lo, level_hi = min(g_lo, g_hi), max(g_lo, g_hi)
    elif isinstance(s_lo, int) and isinstance(s_hi, int):
        normalized_level_key = normalized_stage_key
        level_basis = "stage_ordinals"
        level_key = stage_label
        level_lo, level_hi = min(s_lo, s_hi), max(s_lo, s_hi)
    else:
        # Try the configured label map in the same precedence order documented in
        # CreateKGConfig: grade_key first, then stage_key. Importantly, if a
        # `grade_key` exists but is not mapped, still try the `stage_key` before
        # dropping the SFI.
        fallback_candidates = [
            ("level_label_map_grade_key", grade_key_label, normalized_grade_key),
            ("level_label_map_stage_key", stage_label, normalized_stage_key),
        ]
        attempted_level_keys: list[dict[str, str | None]] = []
        mapped_level: tuple[str, str | None, str, int] | None = None

        for (
            candidate_basis,
            candidate_label,
            candidate_normalized_key,
        ) in fallback_candidates:
            if not candidate_normalized_key:
                continue

            attempted_level_keys.append(
                {
                    "basis": candidate_basis,
                    "level_key": candidate_label,
                    "normalized_level_key": candidate_normalized_key,
                }
            )

            mapped = normalized_level_label_map.get(candidate_normalized_key)

            if mapped is not None:
                mapped_level = (
                    candidate_basis,
                    candidate_label,
                    candidate_normalized_key,
                    int(mapped),
                )
                break

        if not attempted_level_keys:
            drops.setdefault("missing_level_key", []).append(
                {"description": sfi_description, "sfi_uuid": sfi_uuid}
            )
            return None

        if mapped_level is None:
            attempted_keys_s = ", ".join(
                str(item["normalized_level_key"]) for item in attempted_level_keys
            )
            logger.warning(
                f"lp_level_label_map: none of the candidate level keys [{attempted_keys_s}] "
                f"(grade_key={grade_key}, stage_key={stage_key}) were found in map. "
                f"Excluding SFI {sfi_uuid} from LP inference."
            )
            drops.setdefault("unmapped_level_key", []).append(
                {
                    "description": sfi_description,
                    "grade_key": grade_key,
                    "stage_key": stage_key,
                    "attempted_level_keys": attempted_level_keys,
                    "sfi_uuid": sfi_uuid,
                }
            )
            return None

        level_basis, level_key, normalized_level_key, mapped_ordinal = mapped_level
        level_lo = level_hi = mapped_ordinal

    normalized_level_key = normalized_level_key or (
        f"level:{level_lo}-{level_hi}" if level_lo != level_hi else f"level:{level_lo}"
    )
    return (
        level_lo,
        level_hi,
        level_key or normalized_level_key,
        normalized_level_key,
        level_basis,
    )


def _resolve_subject_label(
    *, subject_role: NodeRole | None, topic_path_parts: list[dict[str, Any]]
) -> str:
    """Resolve the subject-like label used for LP grouping.

    This label partitions buckets for Phase 3 within-level cross-subject `relatesTo`
    and Phase 4 cross-level same-subject `relatesTo`. In some curricula this is a true
    subject; in single-subject PDFs, it may intentionally be a strand or learning area.

    Examples
    --------
    1. Explicit subject role from Senegal reading curriculum

        For the Senegal reading curriculum, the run config uses:

            lp_subject_role = "strand"

        and Academic Standards export may produce topic path parts like:

            topic_path_parts = [
                {
                    "role": "strand",
                    "label": "communication écrite - production d'écrits",
                    "canonical_node_id": "7080b096-e23d-55a1-b6c5-ec3f36bddbe9",
                },
                {
                    "role": "substage",
                    "label": "palier 2 - production d'écrits",
                    "canonical_node_id": "8f5408fb-e847-5aa1-b957-96ff1e929bf3",
                },
            ]

        Calling:

            _resolve_subject_label(
                subject_role="strand", topic_path_parts=topic_path_parts
            )

        returns:

            "communication écrite - production d'écrits"

        This lets LP Phase 3 treat strands as subject-like buckets for within-level
        cross-strand `relatesTo` inference.

    2. Explicit subject role returns the first matching role

        If multiple entries have the configured role:

            topic_path_parts = [
                {"role": "strand", "label": "Lecture"},
                {"role": "strand", "label": "Production d'écrits"},
            ]

        Calling:

            _resolve_subject_label(
                subject_role="strand", topic_path_parts=topic_path_parts
            )

        returns:

            "Lecture"

        The function intentionally uses the first matching label in document/path order.

    3. Explicit subject role does not fall back

        If `subject_role` is set but no matching role exists:

            topic_path_parts = [
                {"role": "learning_area", "label": "Langue et Communication"},
                {"role": "substage", "label": "Palier 1"},
            ]

        Calling:

            _resolve_subject_label(
                subject_role="strand", topic_path_parts=topic_path_parts
            )

        returns:

            "UNSPECIFIED_SUBJECT"

        Because an explicit role was provided, the fallback roles "subject" and
        "learning_area" are not tried.

    4. Default fallback to subject

        If `subject_role` is None:

            topic_path_parts = [
                {"role": "subject", "label": "Mathematics"},
                {"role": "strand", "label": "Number"},
            ]

        Calling:

            _resolve_subject_label(
                subject_role=None, topic_path_parts=topic_path_parts
            )

        returns:

            "Mathematics"

    5. Default fallback to learning area

        If `subject_role` is None and no `"subject"` role exists:

            topic_path_parts = [
                {"role": "learning_area", "label": "Langue et Communication"},
                {"role": "strand", "label": "Lecture"},
            ]

        Calling:

            _resolve_subject_label(
                subject_role=None, topic_path_parts=topic_path_parts
            )

        returns:

            "Langue et Communication"

    6. No usable subject-like role

        If no configured or fallback role exists:

            topic_path_parts = [
                {"role": "substage", "label": "Palier 1"},
                {"role": "week", "label": "Semaine 3"},
            ]

        Calling:

            _resolve_subject_label(
                subject_role=None, topic_path_parts=topic_path_parts
            )

        returns:

            "UNSPECIFIED_SUBJECT"

    Parameters
    ----------
    subject_role
        The role used to identify the subject in topic path parts. When None, the
        function searches for "subject" then "learning_area" as fallback roles.
    topic_path_parts
        A list of topic path part dictionaries, each expected to contain "role" and
        "label" keys.

    Returns
    -------
    str
        The resolved subject label or "UNSPECIFIED_SUBJECT" if not found.
    """

    # Build the ordered list of roles to search. When the caller provides an explicit
    # role, only that role is tried. Otherwise, fall back to the two most common
    # curriculum-document roles for subject/learning-area groupings.
    roles_to_try: list[str] = (
        [subject_role.value] if subject_role else ["subject", "learning_area"]
    )

    for role in roles_to_try:
        label = next(
            (
                str(tpp["label"]).strip()
                for tpp in topic_path_parts
                if tpp["role"] == role and str(tpp.get("label") or "").strip()
            ),
            None,
        )

        if label is not None:
            return label

    return "UNSPECIFIED_SUBJECT"


def _sample_items_across_threads(
    *, max_items: int, thread_buckets: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Sample prompt items across one or more buckets for a subject-like group.

    Phase 3 may compare subject-like groups that contain one bucket/thread or many
    buckets/threads. The sampler keeps each LLM prompt bounded while trying to preserve
    useful coverage:

    - For one large bucket, sample evenly across the ordered item sequence instead of
      taking the first N items. This avoids over representing only the beginning of a
      long curriculum strand.
    - For multiple buckets, round-robin across buckets: first item from each bucket,
      second item from each bucket, and so on until `max_items` is reached or all
      buckets are exhausted. This preserves diversity across threads.

    The sampling state for multi-bucket groups is tracked by bucket position rather
    than by thread key. This is important because two buckets can legitimately share
    the same `lp_thread_key` or fallback key; they should still be sampled
    independently.

    Each returned item is a shallow copy of the source item with prompt/debug helper
    fields added:

    - `_sampling_strategy`: `even_spread_single_bucket` or `round_robin_threads`.
    - `_thread_key`: the bucket's thread key, bucket key, or a synthetic bucket key.
    - `_thread_path`: compact human-readable topic context for the source bucket.

    Examples
    --------
    1. Even spread from one large bucket

        Given `max_items=4` and one bucket with 10 ordered items, the sampled indexes
        are approximately `[0, 3, 6, 9]`.

    2. Balanced sampling from two buckets

        Given `max_items=4` and buckets::

            [
                {"lp_thread_key": "oral", "items": [A1, A2, A3]},
                {"lp_thread_key": "lecture", "items": [B1, B2]},
            ]

        the output order is `[A1, B1, A2, B2]`.

    3. Buckets with duplicate thread keys are still independent

        Given two buckets that both have `lp_thread_key="palier_1"`, each bucket gets
        its own internal index. The second bucket is not skipped or advanced because
        the first bucket used the same key.

    4. Empty or malformed buckets do not raise

        A bucket with missing `items` or `items=[]` simply contributes no sampled items.

    Parameters
    ----------
    max_items
        Maximum number of items to sample across all supplied buckets.
    thread_buckets
        Stable-sorted bucket dictionaries to sample from.

    Returns
    -------
    list[dict[str, Any]]
        A flat list of sampled item dictionaries for prompt construction.
    """

    if max_items <= 0:
        return []

    # Filter and prepare valid buckets.
    valid_buckets: list[tuple[str, str, list[dict[str, Any]]]] = []

    for bucket_idx, b in enumerate(thread_buckets):
        items = list(b.get("items") or [])

        if not items:
            continue

        tkey = str(
            b.get("lp_thread_key") or b.get("lp_bucket_key") or f"bucket_{bucket_idx}"
        )
        thread_path = _bucket_topic_context(bucket=b)

        valid_buckets.append((tkey, thread_path, items))

    if not valid_buckets:
        return []

    # Single bucket gets even spread.
    if len(valid_buckets) == 1:
        tkey, thread_path, items = valid_buckets[0]
        return [
            _with_sampling_debug_fields(
                item=items[idx],
                sampling_strategy="even_spread_single_bucket",
                thread_key=tkey,
                thread_path=thread_path,
            )
            for idx in _evenly_spaced_indexes(
                max_items=max_items, total_items=len(items)
            )
        ]

    # Multiple buckets uses round robin.
    bucket_generators = [
        (
            _with_sampling_debug_fields(
                item=item,
                sampling_strategy="round_robin_threads",
                thread_key=tkey,
                thread_path=thread_path,
            )
            for item in items
        )
        for tkey, thread_path, items in valid_buckets
    ]

    sampled: list[dict[str, Any]] = []

    # zip_longest pulls one item from each generator, returning None when exhausted.
    for row in itertools.zip_longest(*bucket_generators):
        for item in row:
            if item is not None:
                sampled.append(item)
                if len(sampled) == max_items:
                    return sampled

    return sampled


def _select_sfi_inference_context(
    *, context: Optional[dict[str, Any]], scope: Optional[SFIContextScope]
) -> Optional[dict[str, Any]]:
    """Select the scoped SFI context used by the candidate's inference phase.

    Parameters
    ----------
    context
        Combined SFI context entry from `_build_combined_sfi_context_index()`.
    scope
        Scope inferred from `candidate.inference_type` in `_emit_relationship()`.

    Returns
    -------
    Optional[dict[str, Any]]
        The relevant scoped context block, or None when either the context or scope is
        unavailable.
    """

    if not context or not scope:
        return None

    return context.get(f"{scope}_context")


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
    """Stable ordering inside a bucket. Prefer explicit `order_index`; fall back to
    numeric code tuple (if available), then `statement_code`, then `uuid`.

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
    raw_code_tuple = s.get("code_tuple")
    nums: list[int] = []

    if isinstance(raw_code_tuple, list):
        for item in raw_code_tuple:
            if isinstance(item, (int, float)):
                nums.append(int(item))
            elif isinstance(item, str):
                nums.extend(int(match) for match in re.findall(r"\d+", item))

    if not nums and code:
        nums = [int(match) for match in re.findall(r"\d+", code)]

    code_tuple = tuple(nums) if nums else None
    missing_code_tuple = 1 if code_tuple is None else 0
    code_tuple_key = code_tuple if code_tuple is not None else (10**9,)
    uuid_key = s.get("sfi_uuid") or s.get("case_identifier_uuid") or ""
    return order_index, missing_code_tuple, code_tuple_key, code, uuid_key


def _thread_sort_key(b: dict[str, Any]) -> tuple[str, str]:
    """Return a deterministic sort key for LP thread buckets.

    Parameters
    ----------
    b
        A learning-progression bucket dictionary.

    Returns
    -------
    tuple[str, str]
        `(_bucket_topic_context(b), lp_thread_key_or_bucket_key)`. The first element
        uses aggregate topic examples when present so broad buckets sort by truthful
        topic context rather than a first-item-only path.
    """

    return _bucket_topic_context(bucket=b), str(
        b.get("lp_thread_key") or b.get("lp_bucket_key") or ""
    )


def _topic_path_signature(topic_path_parts: list[dict[str, Any]]) -> str:
    """Return a normalized signature for all available topic path parts.

    Parameters
    ----------
    topic_path_parts
        Topic path entries, each ideally containing `role` and `label` keys.

    Returns
    -------
    str
        A pipe-delimited signature such as "strand=lecture|substage=palier_1".
        Malformed entries and blank roles or labels are skipped.
    """

    segments: list[str] = []

    for tpp in topic_path_parts or []:
        if not isinstance(tpp, dict):
            continue

        role = tpp["role"]
        label = str(tpp.get("label") or "").strip()

        if not label:
            continue

        value = normalize_key_token(label=label, separator="_")

        if value:
            segments.append(f"{role}={value}")

    return "|".join(segments)


def _update_existing_bucket(
    *,
    bucket: dict[str, Any],
    bucket_key_value: str,
    bucket_scope: str,
    bucket_used_fallback: bool,
    cleaned_fallbacks: list[str],
    level_label: str,
    subject_label: str,
    topic_key_str: str,
    topic_path: str,
) -> None:
    """Update an existing bucket with incoming item metadata.

    Modifies the provided bucket dictionary in place, updating topic path examples/keys
    and subject label anomalies, as well as preserving fallback usage metadata if
    applicable.

    Parameters
    ----------
    bucket
        The existing bucket to update.
    bucket_key_value
        The computed key for the bucket in the given scope.
    bucket_scope
        The bucket scope (`"within_level"` or `"cross_level"`).
    bucket_used_fallback
        True if the incoming item's bucket key was produced from fallback segments.
    cleaned_fallbacks
        Cleaned source-field fallback segments.
    level_label
        The level label for logging context.
    subject_label
        Subject-like label for the incoming item.
    topic_key_str
        Cleaned canonical topic path key.
    topic_path
        Cleaned topic path string.
    """

    existing_subject_label = (
        str(bucket.get("subject_label") or "").strip() or "UNSPECIFIED_SUBJECT"
    )
    incoming_subject_label = str(subject_label or "").strip() or "UNSPECIFIED_SUBJECT"

    # Subject label anomalies.
    if existing_subject_label != incoming_subject_label:
        subject_labels = bucket.setdefault("subject_label_examples", [])
        should_log = incoming_subject_label not in subject_labels

        # Deduplicate while preserving order.
        bucket["subject_label_examples"] = list(
            dict.fromkeys(
                subject_labels + [existing_subject_label, incoming_subject_label]
            )
        )

        if should_log:
            is_fallback = bool(bucket.get("within_level_bucket_used_fallback"))

            if bucket_scope == "within_level" and (bucket_used_fallback or is_fallback):
                logger.warning(
                    f"Within-level fallback LP bucket reused across different "
                    f"subject labels; bucket identity does not include "
                    f"subject_label, so metadata remains first-SFI-wins. "
                    f"bucket_key={bucket_key_value!r}, level={level_label!r}, "
                    f"existing_subject={existing_subject_label!r}, "
                    f"incoming_subject={incoming_subject_label!r}."
                )
            else:
                logger.warning(
                    f"LP bucket reused across different subject labels; "
                    f"bucket metadata remains first-SFI-wins. "
                    f"bucket_scope={bucket_scope!r}, "
                    f"bucket_key={bucket_key_value!r}, level={level_label!r}, "
                    f"existing_subject={existing_subject_label!r}, "
                    f"incoming_subject={incoming_subject_label!r}."
                )

    # Topic path examples.
    if topic_path:
        examples = bucket.setdefault("topic_path_examples", [])
        bucket["topic_path_examples"] = list(dict.fromkeys(examples + [topic_path]))[
            :10
        ]

    # Topic path keys.
    if topic_key_str:
        keys = bucket.setdefault("topic_path_keys", [])
        bucket["topic_path_keys"] = list(dict.fromkeys(keys + [topic_key_str]))

    # Fallback segments.
    if bucket_scope == "within_level":
        bucket["within_level_bucket_used_fallback"] = bool(
            bucket.get("within_level_bucket_used_fallback") or bucket_used_fallback
        )
        existing_segments = bucket.setdefault("within_level_fallback_segments", [])

        if bucket_used_fallback:
            bucket["within_level_fallback_segments"] = list(
                dict.fromkeys(existing_segments + cleaned_fallbacks)
            )


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


def _with_sampling_debug_fields(
    *, item: dict[str, Any], sampling_strategy: str, thread_key: str, thread_path: str
) -> dict[str, Any]:
    """Return a shallow item copy annotated with prompt-sampling debug fields.

    Parameters
    ----------
    item
        Source bucket item.
    sampling_strategy
        Human-readable strategy used to select the item.
    thread_key
        Source thread/bucket key.
    thread_path
        Human-readable source bucket context.

    Returns
    -------
    dict[str, Any]
        A shallow copy of the item with `_thread_key`, `_thread_path`, and
        `_sampling_strategy` fields added.
    """

    sampled_item = dict(item)
    sampled_item["_sampling_strategy"] = sampling_strategy
    sampled_item["_thread_key"] = thread_key
    sampled_item["_thread_path"] = thread_path
    return sampled_item


def export_learning_progressions(
    *,
    academic_standards: AcademicStandardsExport,
    config: CreateKGConfig,
    ctx: ExportContext,
    kg_dirs: KGDirs,
    usage_tracker: KGUsageTracker,
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
    usage_tracker
        The KGUsageTracker for recording KG generation and validation calls during the
        export process

    Returns
    -------
    LearningProgressionsExport
        Emitted buildsTowards and relatesTo relationships and a report of the export
        process.

    Raises
    ------
    ValueError
        If duplicate relationship identifiers are found in the final emitted
        relationships, which should be unique. This check guards against regressions in
        the UUID generation logic that could lead to non-unique identifiers.
    """

    # Group standards into learning progression buckets, which are the basis for
    # candidate generation. This step also enriches each SFI with metadata used for
    # inference and provenance, such as resolved level ordinals and subject labels.
    lp_buckets = group_standards_for_learning_progressions(
        academic_standards=academic_standards, config=config, include_provenance=True
    )

    # Write the buckets artifact for debugging.
    write_to_json(
        fp=kg_dirs.learning_progressions / "learning_progressions_buckets.json",
        json_info=lp_buckets,
    )

    # Extract the within-level and cross-level buckets.
    by_within_level: dict[str, list[dict[str, Any]]] = (
        lp_buckets.get("by_within_level") or {}
    )
    by_cross_level: dict[str, list[dict[str, Any]]] = (
        lp_buckets.get("by_cross_level") or {}
    )
    candidates: list[CandidateEdge] = []
    provenance_rows: list[dict[str, Any]] = []

    # Build indices for candidate filtering and relationship metadata enrichment. The
    # within-level index is used by the Phase 1 document-order safety filter. The
    # combined index is attached to emitted relationship metadata.
    within_sfi_index = _build_scoped_sfi_index(
        by_level=by_within_level, scope="within_level"
    )
    cross_sfi_index = _build_scoped_sfi_index(
        by_level=by_cross_level, scope="cross_level"
    )
    sfi_context_index = _build_combined_sfi_context_index(
        cross_sfi_index=cross_sfi_index, within_sfi_index=within_sfi_index
    )

    # Phase 1: Within-level buildsTowards.
    p1_candidates, p1_prov = _infer_within_level_builds_towards(
        by_level=by_within_level, config=config, usage_tracker=usage_tracker
    )
    candidates.extend(p1_candidates)
    provenance_rows.extend(p1_prov)

    # Phase 2: Cross-level buildsTowards.
    p2_candidates, p2_prov, cross_level_build_pairs = _infer_cross_level_builds_towards(
        by_level=by_cross_level, config=config, usage_tracker=usage_tracker
    )
    candidates.extend(p2_candidates)
    provenance_rows.extend(p2_prov)

    # Phase 3: Within-level relatesTo.
    p3_candidates, p3_prov = _infer_within_level_relates_to(
        by_level=by_within_level, config=config, usage_tracker=usage_tracker
    )
    candidates.extend(p3_candidates)
    provenance_rows.extend(p3_prov)

    # Phase 4: Cross-level relatesTo.
    p4_candidates, p4_prov = _infer_cross_level_relates_to(
        by_level=by_cross_level,
        config=config,
        forbidden_builds_pairs=cross_level_build_pairs,
        usage_tracker=usage_tracker,
    )
    candidates.extend(p4_candidates)
    provenance_rows.extend(p4_prov)

    # Assign candidate UIDs after all inference phases are complete so that the
    # provenance records can include the candidate UID for all candidates, including
    # those that are later dropped during filtering and deduplication.
    candidates = _assign_candidate_uids(
        candidates=candidates, provenance_rows=provenance_rows
    )

    # Dedupe, filter, limit, and emit final relationships, and gather stats for the
    # report.
    builds_rels, relates_rels, lp_stats, disposition_map, dedupe_winners = (
        _process_and_filter_candidates(
            candidates=candidates,
            config=config,
            doc_key=ctx.doc_key,
            sfi_context_index=sfi_context_index,
            within_sfi_index=within_sfi_index,
        )
    )

    # Verify that all emitted relationship IDs are unique. The UUIDv5 derivation from
    # (source, target, rel_type) combined with dedup guarantees this, but an explicit
    # check guards against future regressions.
    all_rels = builds_rels + relates_rels
    all_ids = [r.identifier for r in all_rels]

    if len(set(all_ids)) != len(all_ids):
        dupes = {uid: c for uid, c in Counter(all_ids).items() if c > 1}
        raise ValueError(
            f"Duplicate relationship identifiers in Learning Progressions export: "
            f"{dupes}"
        )

    # Enrich provenance rows with post-filtering disposition.
    #
    # NB: relatesTo edges are canonicalized during dedup (lexicographic UUID string
    # order), so the disposition map keys for relatesTo are already canonical.
    # Provenance rows, however, carry the *original* (pre-dedup) source/target which
    # may be in either order. _canon_disposition_key normalises the lookup key so that
    # the match succeeds regardless of the original edge direction.
    for row in provenance_rows:
        key = _canon_disposition_key(
            rel_type=row.get("rel_type", ""),
            source=row.get("source", ""),
            target=row.get("target", ""),
        )

        # Provenance rows include *all* raw candidates, including those dropped during
        # deduplication. The disposition_map only records the final outcome for the
        # single dedupe winner per canonical edge key. To avoid mislabeling duplicates
        # as "kept"/"dropped_low_conf"/etc., mark non-winners explicitly as
        # `dropped_dedupe`.
        winner, is_winner = dedupe_winners.get(key), False

        if winner is not None:
            winner_metadata = (
                winner.metadata if isinstance(winner.metadata, dict) else {}
            )
            row_candidate_uid = str(row.get("candidate_uid") or "").strip()
            winner_candidate_uid = str(
                winner_metadata.get("candidate_uid") or ""
            ).strip()
            is_winner = bool(
                row_candidate_uid and row_candidate_uid == winner_candidate_uid
            )

        row["disposition"] = (
            "dropped_dedupe"
            if winner is not None and not is_winner
            else disposition_map.get(key, "dropped_dedupe")
        )

    return _finalize_lp_export(
        academic_standards=academic_standards,
        builds_rels=builds_rels,
        config=config,
        ctx=ctx,
        drops=lp_buckets.get("drops") or {},
        kg_dirs=kg_dirs,
        lp_stats=lp_stats,
        provenance_rows=provenance_rows,
        relates_rels=relates_rels,
    )


def group_standards_for_learning_progressions(
    *,
    academic_standards: AcademicStandardsExport,
    config: CreateKGConfig,
    include_provenance: bool = True,
) -> dict[str, Any]:
    """Build learning progression buckets for the LLM.

    Uses config-driven bucketing as follows:

    1. **LP source eligibility** checked via normalized statement type plus
        `config.lp_source_statement_types_include`/`config.lp_source_statement_types_exclude`.
    2. **Level bounds** resolved from `progression_context` ordinals when present, with
        a fallback to `config.lp_level_label_map`.
    3. **Subject label** resolved via `config.lp_subject_role`.
    4. **Within-level bucket key** computed via `config.lp_within_level_bucket_roles`,
        with optional `config.lp_within_level_fallback_fields` when hierarchy roles are
        missing.
    5. **Cross-level thread key** computed via `config.lp_cross_level_thread_roles`, or
        from `progression_context.thread_key` when that config is None.

    Parameters
    ----------
    academic_standards
        The exported Academic Standards KG artifacts.
    config
        The KG creation config with LP-specific fields that drive the bucketing logic
        in `_process_single_standard`.
    include_provenance
        Whether to include provenance metadata in the payload for each standard item,
        which the LLM can use as signals when deciding buildsTowards relationships.

    Returns
    -------
    dict[str, Any]
        A dictionary containing grouped standards by level and thread, as well as any
        dropped items due to ineligible or unmapped source data.
    """

    # level label -> effective bucket/thread key -> bucket. Keep within-level and
    # cross-level structures separate because the correct grouping granularity can
    # differ by inference scope.
    within_level_buckets: DefaultDict[str, DefaultDict[str, dict[str, Any]]] = (
        defaultdict(lambda: defaultdict(dict))
    )
    cross_level_buckets: DefaultDict[str, DefaultDict[str, dict[str, Any]]] = (
        defaultdict(lambda: defaultdict(dict))
    )

    # Track dropped standards for reporting. Each key corresponds to a specific reason
    # for dropping, and the value is a list of dictionaries containing relevant
    # information about each dropped standard item.
    drops: dict[str, list[dict[str, Any]]] = {
        "lp_statement_type_excluded": [],
        "lp_statement_type_not_included": [],
        "missing_level_key": [],
        "non_standard_item": [],
        "unmapped_level_key": [],
    }

    # Precompute level-label fallback mappings once per run instead of rebuilding them
    # for every SFI. Keys are normalized with the same helper used in
    # `_resolve_level_ordinals()`.
    normalized_level_label_map: dict[str, int] = {}

    for map_key, map_value in (config.lp_level_label_map or {}).items():
        normalized_map_key = _normalize_level_label_key(map_key)

        if normalized_map_key:
            normalized_level_label_map[normalized_map_key] = map_value

    # Build canonical-node-id -> sibling order index. Prefer Academic Standards
    # hasChild relationship metadata because it covers grouping ancestors as well as
    # leaf SFIs; supplement from SFI progression metadata when needed.
    order_index_lookup = _build_order_index_lookup(academic_standards)

    for sfi in academic_standards.items:
        _process_single_standard(
            cross_level_buckets=cross_level_buckets,
            config=config,
            drops=drops,
            include_provenance=include_provenance,
            normalized_level_label_map=normalized_level_label_map,
            order_index_lookup=order_index_lookup,
            sfi=sfi,
            within_level_buckets=within_level_buckets,
        )

    by_within_level, by_within_bucket_key = _finalize_bucket_store(within_level_buckets)
    by_cross_level, by_cross_thread_key = _finalize_bucket_store(cross_level_buckets)
    return {
        "by_cross_thread_key": by_cross_thread_key,
        "by_cross_level": by_cross_level,
        "by_within_bucket_key": by_within_bucket_key,
        "by_within_level": by_within_level,
        "drops": drops,
    }


def load_learning_progressions_export(kg_dirs: KGDirs) -> LearningProgressionsExport:
    """Reconstruct a LearningProgressionsExport from previously written disk artifacts.

    Parameters
    ----------
    kg_dirs
        The KG output directories containing the prior run's artifacts.

    Returns
    -------
    LearningProgressionsExport
        The reconstructed export object.
    """

    d = kg_dirs.learning_progressions
    builds_towards = [
        Relationship.model_validate(raw)
        for raw in open_json_type(
            d / "learning_progressions_builds_towards_relationships.json"
        )
    ]
    relates_to = [
        Relationship.model_validate(raw)
        for raw in open_json_type(
            d / "learning_progressions_relates_to_relationships.json"
        )
    ]
    lp_kg = open_json_type(d / "learning_progressions_kg.json")
    report = open_json_type(d / "learning_progressions_report.json")
    return LearningProgressionsExport(
        builds_towards_relationships=builds_towards,
        lp_kg=lp_kg,
        relates_to_relationships=relates_to,
        report=report,
    )


def load_or_export_learning_progressions(
    *,
    academic_standards: AcademicStandardsExport,
    config: CreateKGConfig,
    ctx: ExportContext,
    kg_dirs: KGDirs,
    usage_tracker: KGUsageTracker,
) -> tuple[LearningProgressionsExport, bool]:
    """Load an existing Learning Progressions KG from disk or export a new one.

    Checks whether the learning progressions sentinel bundle file already exists on
    disk. If it exists and `config.overwrite` is False, the prior export is loaded from
    disk. Otherwise, a new export is generated.

    Parameters
    ----------
    academic_standards
        The exported academic standards artifacts.
    config
        The CreateKGConfig for export.
    ctx
        The ExportContext for the CanonicalIR.
    kg_dirs
        The KG output directories.
    usage_tracker
        The KGUsageTracker for recording KG generation and validation calls during the
        export process.

    Returns
    -------
    tuple[LearningProgressionsExport, bool]
        A tuple containing the Learning Progressions export artifacts and a boolean
        indicating whether the export was reused from disk (`True`) or newly generated
        (`False`).
    """

    lp_sentinel = kg_dirs.learning_progressions / "learning_progressions_kg.json"
    lp_reused = False

    if lp_sentinel.exists() and not config.overwrite:
        logger.warning(
            "Learning Progressions KG already exists and overwrite=False---loading from disk."
        )

        learning_progressions = load_learning_progressions_export(kg_dirs)
        lp_reused = True
    else:
        if lp_sentinel.exists():
            logger.warning(
                "config.overwrite=True: re-exporting Learning Progressions KG "
                "(existing artifacts will be overwritten)."
            )

        learning_progressions = export_learning_progressions(
            academic_standards=academic_standards,
            config=config,
            ctx=ctx,
            kg_dirs=kg_dirs,
            usage_tracker=usage_tracker,
        )

    return learning_progressions, lp_reused
