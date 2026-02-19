"""This module contains functionalities related to exporting the Learning Components
 knowledge graph artifacts from an Academic Standards export.

This module implements a shape-preserving Learning Commons Learning Components export:

- Entities: LearningComponent
- Relationships: supports (LearningComponent -> StandardsFrameworkItem)
"""

# Standard Library
import re

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import UUID, uuid5

# Third Party Library
from loguru import logger

# Package Library
from skg.kgs.export_academic_standards import AcademicStandardsExport
from skg.kgs.schemas import LearningComponent, Relationship, StandardsFrameworkItem
from skg.kgs.utils import ExportContext, KGDirs, normalize_ws, stable_text_hash
from skg.schemas import CreateKGConfig
from skg.utils.general import write_to_json

# Inline bullets: exclude hyphen/dash so we never split hyphenated words.
_INLINE_BULLET_CHARS = r"[\u2022\u00b7•·\*]"

# Line bullets: allow hyphen/dash, but we only treat them as bullets at line-start.
_LINE_BULLET_CHARS = r"[\u2022\u00b7•·\-\–\—\*]"


@dataclass
class LearningComponentsExport:
    """The output of exporting Learning Components KG artifacts."""

    lc_stats: dict[str, Any]  # split_policy, splits_distribution, max_splits_observed
    learning_components: list[LearningComponent]
    supports_relationships: list[Relationship]


def _build_learning_components_graph_bundle(
    *,
    doc_key: str,
    export_dialect: str,
    learning_components: list[LearningComponent],
    supports_relationships: list[Relationship],
) -> dict[str, Any]:
    """Build a shape-preserving graph bundle for Learning Components export.

    Parameters
    ----------
    doc_key
        The document key for this export, used in ID generation.
    export_dialect
        The export dialect string, included in metadata for traceability.
    learning_components
        The list of LearningComponent entities to include as nodes.
    supports_relationships
        The list of supports relationships to include as edges.

    Returns
    -------
    dict[str, Any]
        A dictionary representing the graph bundle, with nodes and relationships in a
        shape-preserving format.
    """

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nodes: list[dict[str, Any]] = []

    for lc in learning_components:
        nodes.append(
            {
                "id": str(lc.identifier),
                "labels": ["LearningComponent"],
                "properties": lc.model_dump(mode="json"),
            }
        )

    relationships: list[dict[str, Any]] = []

    for r in supports_relationships:
        relationships.append(
            {
                "id": str(r.identifier),
                "type": "SUPPORTS",
                "start": r.source_entity_value,
                "end": r.target_entity_value,
                "properties": r.model_dump(mode="json"),
            }
        )

    return {
        "doc_key": doc_key,
        "export_dialect": export_dialect,
        "generated_at": generated_at,
        "graph_type": "learning_components",
        "nodes": nodes,
        "relationships": relationships,
    }


def _create_lcs_for_expectation(
    *,
    config: CreateKGConfig,
    doc_key: str,
    fw_metadata: dict[str, Any],
    sfi: StandardsFrameworkItem,
) -> list[LearningComponent]:
    """Create LearningComponents for a single expectation SFI according to policy.

    Parameters
    ----------
    config
        The KG export configuration, used to determine LC creation policy and ID
        namespace.
    doc_key
        The document key for this export, used in ID generation.
    fw_metadata
        The standards framework metadata, used for populating LC provenance and
        attribution.
    sfi
        The StandardsFrameworkItem representing the expectation for which to create LCs.

    Returns
    -------
    list[LearningComponent]
        A list of LearningComponent entities created for the given expectation SFI,
        according to the specified policy. Each LC will have a deterministic UUID based
        on the doc_key, SFI UUID, split index, and split hash to ensure stable IDs
        across runs.
    """

    policy = config.learning_component_policy

    # Display text (human-facing): use SFI.description as exported by academic
    # standards.
    display_text = normalize_ws(getattr(sfi, "description", "") or "")

    # Canonical ID text: ALWAYS prefer stable normalized_text from SFI.metadata so IDs
    # don't change when description display policy/translations change.
    metadata = getattr(sfi, "metadata", {}) or {}

    id_source_text = normalize_ws(str(metadata.get("normalized_text") or ""))
    id_source_kind = "metadata.normalized_text"
    if not id_source_text:
        # Fallback only if canonical normalized_text is missing.
        id_source_text = display_text
        id_source_kind = "sfi.description_fallback"

    # Build ID parts (used for hashing + UUIDv5 name strings).
    id_parts: list[str]

    if policy == "split_bullets":
        id_parts = _split_bullets_deterministic(text=id_source_text)

        if not id_parts:
            id_parts = [id_source_text]
    else:
        id_parts = [id_source_text]

    # Enforce max splits deterministically (keep earliest parts).
    max_splits = int(config.lc_max_splits_per_standard)
    truncated = False

    if len(id_parts) > max_splits:
        id_parts = id_parts[:max_splits]
        truncated = True

    # Drop empty ID parts.
    id_parts = [p for p in id_parts if p]

    if not id_parts:
        id_parts = [display_text] if display_text else []

    # Build display parts (used for LC.description). Try to split display_text the same
    # way as ID text so each LC gets a meaningful description.
    display_source_text = display_text or id_source_text
    display_parts: list[str]

    if policy == "split_bullets":
        display_parts = _split_bullets_deterministic(text=display_source_text)
        display_parts = display_parts or [display_source_text]
    else:
        display_parts = [display_source_text]

    if len(display_parts) > max_splits:
        display_parts = display_parts[:max_splits]

    display_parts = [p for p in display_parts if p]

    # If the split counts don't match, fall back to using ID parts for descriptions
    # (keeps determinism + avoids mismatched pairing).
    paired_parts = (
        list(zip(id_parts, display_parts))
        if len(display_parts) == len(id_parts)
        else [(p, p) for p in id_parts]
    )

    lcs: list[LearningComponent] = []
    ns: UUID = config.namespace_uuid

    for i, (id_part, display_part) in enumerate(paired_parts):
        # NB: split_hash (and thus lc_id) must be derived from canonical ID text.
        split_hash = stable_text_hash(s=id_part)
        lcs.append(
            LearningComponent(
                academic_subject=str(
                    getattr(sfi, "academic_subject", None)
                    or fw_metadata["academic_subject_default"]
                ),
                attribution_statement=str(fw_metadata["attribution_statement"]),
                author=str(fw_metadata["author"]),
                description=display_part,  # Human-facing description can be display-derived, but IDs are not
                identifier=uuid5(
                    ns,
                    f"lc:curriculum:{doc_key}:lc:{policy}:{sfi.case_identifier_uuid}:{i}:{split_hash}",
                ),
                in_language=str(
                    getattr(sfi, "in_language", None) or fw_metadata["in_language"]
                ),
                license=str(fw_metadata["license"]),
                metadata={
                    "id_source_kind": id_source_kind,
                    "supporting_sfi_case_uuid": str(sfi.case_identifier_uuid),
                    "canonical_node_id": metadata.get("canonical_node_id"),
                    "split_policy": policy,
                    "split_id_text": id_part,
                    "split_display_text": display_part,
                    "split_index": i,
                    "split_hash": split_hash,
                    "split_truncated": truncated,
                    "provenance": {
                        "page_indices": metadata.get("page_indices", []),
                        "bbox": metadata.get("bbox"),
                        "bbox_ref": metadata.get("bbox_ref"),
                        "source_decision_ids": metadata.get("source_decision_ids", []),
                        "source_segment_ids": metadata.get("source_segment_ids", []),
                    },
                },
                provider=str(fw_metadata["provider"]),
            )
        )

    return lcs


def _emit_supports(
    *,
    config: CreateKGConfig,
    doc_key: str,
    fw_metadata: dict[str, Any],
    lc: LearningComponent,
    sfi: StandardsFrameworkItem,
) -> Relationship:
    """Emit a supports relationship from a LearningComponent to its supporting SFI.

    Parameters
    ----------
    config
        The KG export configuration, used to determine ID namespace.
    doc_key
        The document key for this export, used in ID generation.
    fw_metadata
        The standards framework metadata, used for populating relationship provenance
        and attribution.
    lc
        The LearningComponent entity that supports the SFI.
    sfi
        The StandardsFrameworkItem that is supported by the LearningComponent.

    Returns
    -------
    Relationship
        A Relationship entity representing the "supports" relationship from the
        LearningComponent to the StandardsFrameworkItem, with a deterministic UUID
        based on the doc_key, LC UUID, and SFI UUID to ensure stable IDs across runs.
    """

    ns: UUID = config.namespace_uuid
    edge_id = uuid5(
        ns,
        f"lc:curriculum:{doc_key}:rel:supports:{lc.identifier}:{sfi.case_identifier_uuid}",
    )

    return Relationship(
        attribution_statement=str(fw_metadata["attribution_statement"]),
        author=str(fw_metadata["author"]),
        description="",
        identifier=edge_id,
        license=str(fw_metadata["license"]),
        metadata={
            "source_kg": "learning_components",
            "supporting_sfi_case_uuid": str(sfi.case_identifier_uuid),
        },
        provider=str(fw_metadata["provider"]),
        relationship_type="supports",
        source_entity="LearningComponent",
        source_entity_key="identifier",
        source_entity_value=str(lc.identifier),
        target_entity="StandardsFrameworkItem",
        target_entity_key="case_identifier_uuid",
        target_entity_value=str(sfi.case_identifier_uuid),
    )


def _iter_expectation_sfis(
    items: Iterable[StandardsFrameworkItem],
) -> list[StandardsFrameworkItem]:
    """Return a StandardsFrameworkItems that represent normative expectations.

    Parameters
    ----------
    items
        An iterable of StandardsFrameworkItems to filter.

    Returns
    -------
    list[StandardsFrameworkItem]
        A list of StandardsFrameworkItems that are considered normative expectations,
        based on their normalized_statement_type being "Standard". This is a policy
        decision that may be refined in the future with more sophisticated logic, but
        for now serves as a simple heuristic to identify which SFIs should be supported
        by Learning Components.
    """

    output: list[StandardsFrameworkItem] = []

    for sfi in items:
        nst = sfi.normalized_statement_type

        if str(nst).strip().lower() == "standard":
            output.append(sfi)

    return output


def _split_bullets_deterministic(*, text: str) -> list[str]:
    """Deterministically split text into bullet/numbered parts.

    The process is as follows:

    1. Normalize newlines.
    2. Convert inline bullet characters (•, ·, etc.) to line breaks.
    3. Split on line breaks.
    4. Strip leading bullet/number markers.
    5. Collapse whitespace.
    6. Preserve original order; de-dupe exact matches.

    Parameters
    ----------
    text
        The input text to split into parts. This is typically the normalized_text from
        SFI metadata, or the SFI description as a fallback.

    Returns
    -------
    list[str]
        A list of split parts extracted from the input text, based on bullet/number
        splitting. The splitting is deterministic and stable across runs, as it relies
        on consistent normalization and hashing. If the input text does not contain any
        recognizable bullet or numbering patterns, the output may be an empty list, in
        which case the caller may choose to fallback to using the original text as a
        single part.
    """

    src = (text or "").strip()

    if not src:
        return []

    src = src.replace("\r\n", "\n").replace("\r", "\n")

    # Detect markers before we mutate heavily.
    has_inline_bullet = bool(re.search(rf"\s+{_INLINE_BULLET_CHARS}\s+", src))
    has_line_bullet = any(
        re.match(rf"^{_LINE_BULLET_CHARS}\s*", ln.strip()) for ln in src.split("\n")
    )
    has_numbering = bool(
        re.search(r"(?m)^\s*(?:\(?\d+\)?|[A-Za-z]|[ivxlcdmIVXLCDM]+)[\)\.]\s+", src)
    )
    had_list_marker = has_inline_bullet or has_line_bullet or has_numbering

    src = re.sub(rf"\s+{_INLINE_BULLET_CHARS}\s+", "\n• ", src)
    lines = [ln.strip() for ln in re.split(r"\n+", src) if ln.strip()]

    if not lines:
        return []

    parts: list[str] = []

    for ln in lines:
        ln2 = re.sub(rf"^{_LINE_BULLET_CHARS}\s*", "", ln).strip()
        ln2 = re.sub(
            r"^(?:\(?\d+\)?|[A-Za-z]|[ivxlcdmIVXLCDM]+)[\)\.]\s+", "", ln2
        ).strip()
        ln2 = re.sub(
            r"^(?:\(?\d+\)?|[A-Za-z]|[ivxlcdmIVXLCDM]+)\s*[-–—]\s+", "", ln2
        ).strip()
        ln2 = normalize_ws(ln2)
        if ln2:
            parts.append(ln2)

    # If there was an explicit marker and we got 1 clean part, keep it.
    if len(parts) == 1 and had_list_marker:
        return parts

    if len(parts) < 2:
        return []

    # De-dupe while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []

    for p in parts:
        if p not in seen:
            seen.add(p)
            deduped.append(p)

    return deduped


def export_learning_components(
    *,
    academic_standards: AcademicStandardsExport,
    config: CreateKGConfig,
    ctx: ExportContext,
    kg_dirs: KGDirs,
) -> LearningComponentsExport:
    """Export Learning Components KG artifacts.

    Parameters
    ----------
    academic_standards
        Exported academic standards artifacts. This is the shared backbone: supports
        edges MUST target emitted StandardsFrameworkItems by case_identifier_uuid.
    config
        KG config for LC policy + deterministic ID namespace.
    ctx
        ExportContext (doc_key, framework metadata, indexes). Only doc_key + framework
        metadata are required here.
    kg_dirs
        The KGDirs for output.

    Returns
    -------
    LearningComponentsExport
        The exported LCs and supports relationships.

    Raises
    ------
    ValueError
        If any integrity checks fail, such as non-supports relationships emitted or
        mismatched counts of LCs and relationships.
    """

    expectation_sfis = _iter_expectation_sfis(academic_standards.items)

    logger.info(f"Learning Components: found {len(expectation_sfis)} expectation SFIs")

    fw_metadata = ctx.get_framework_metadata()
    lcs: list[LearningComponent] = []
    rels: list[Relationship] = []
    splits_per_sfi: defaultdict[int, int] = defaultdict(int)

    # Deterministic order for determinism: sort by SFI UUID string.
    expectation_sfis_sorted = sorted(
        expectation_sfis, key=lambda x: str(x.case_identifier_uuid)
    )

    for sfi in expectation_sfis_sorted:
        created = _create_lcs_for_expectation(
            config=config, doc_key=ctx.doc_key, fw_metadata=fw_metadata, sfi=sfi
        )
        splits_per_sfi[len(created)] += 1

        for lc in created:
            lcs.append(lc)
            rels.append(
                _emit_supports(
                    config=config,
                    doc_key=ctx.doc_key,
                    fw_metadata=fw_metadata,
                    lc=lc,
                    sfi=sfi,
                )
            )

    # Integrity checks.
    if any(r.relationship_type != "supports" for r in rels):
        raise ValueError(
            "Non-supports relationship found in Learning Components export."
        )
    if len(rels) != len(lcs):
        raise ValueError(
            f"Expected 1 supports edge per LC, got {len(rels)} rels for {len(lcs)} LCs."
        )

    write_to_json(
        fp=kg_dirs.learning_components / "learning_components.json",
        json_info=[lc.model_dump(mode="json") for lc in lcs],
    )
    write_to_json(
        fp=kg_dirs.learning_components
        / "learning_components_supports_relationships.json",
        json_info=[r.model_dump(mode="json") for r in rels],
    )
    write_to_json(
        fp=kg_dirs.learning_components / "learning_components_kg.json",
        json_info=_build_learning_components_graph_bundle(
            doc_key=ctx.doc_key,
            export_dialect=str(config.export_dialect),
            learning_components=lcs,
            supports_relationships=rels,
        ),
    )

    lc_stats = {
        "split_policy": str(config.learning_component_policy),
        "total_expectations": len(expectation_sfis_sorted),
        "total_lcs": len(lcs),
        "splits_distribution": {str(k): v for k, v in sorted(splits_per_sfi.items())},
        "max_splits_observed": max(splits_per_sfi.keys()) if splits_per_sfi else 0,
    }

    return LearningComponentsExport(
        learning_components=lcs, supports_relationships=rels, lc_stats=lc_stats
    )
