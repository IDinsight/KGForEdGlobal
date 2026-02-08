"""This module contains functionalities related to exporting the Learning Progressions
knowledge graph. It exports relationships between *exported* StandardsFrameworkItems
(SFIs) from an Academic Standards export.

- Relationships:
  - buildsTowards (SFI -> SFI), directional
  - relatesTo (SFI -- SFI), associative (canonicalized to a single directed edge)

The export is *shape-preserving* for the LC Knowledge Graph ontology and is designed to
work for non-US curriculum documents mapped into the LC "academic standards" shape.
"""

# Standard Library
import re

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, DefaultDict, Optional
from uuid import UUID

# Third Party Library
from loguru import logger

# Package Library
from skg.kgs.export_academic_standards import AcademicStandardsExport
from skg.kgs.schemas import Relationship, StandardsFrameworkItem
from skg.kgs.utils import ExportContext, KGDirs
from skg.schemas import CreateKGConfig

# Compiled regexes.
GRADE_INT_RE = re.compile(r"\b(\d+)\b")


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

    standards_by_grade_level = group_standards_for_learning_progressions(
        academic_standards=academic_standards
    )
    grade_buckets = standards_by_grade_level["by_grade"]["GRADE 1"]
    multi_items = [b for b in grade_buckets if len(b["items"]) >= 2]

    logger.info(f"{multi_items = }")

    # # Write artifacts.
    # write_to_json(
    #     fp=kg_dirs.learning_progressions
    #     / "learning_progressions_builds_towards_relationships.json",
    #     json_info=[r.model_dump(mode="json") for r in builds_towards],
    # )
    # write_to_json(
    #     fp=kg_dirs.learning_progressions
    #     / "learning_progressions_relates_to_relationships.json",
    #     json_info=[r.model_dump(mode="json") for r in relates_to],
    # )
    #
    # graph_bundle = _build_learning_progressions_graph_bundle(
    #     ctx=ctx,
    #     export_dialect=str(config.export_dialect),
    #     relationships=(builds_towards + relates_to),
    # )
    # write_to_json(
    #     fp=kg_dirs.learning_progressions / "learning_progressions_kg.json",
    #     json_info=graph_bundle,
    # )
    #
    # write_to_json(
    #     fp=kg_dirs.learning_progressions / "learning_progressions_report.json",
    #     json_info=report,
    # )
    #
    # return LearningProgressionsExport(
    #     builds_towards_relationships=builds_towards,
    #     graph_bundle=graph_bundle,
    #     relates_to_relationships=relates_to,
    #     report=report,
    # )


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
