"""This module contains functionalities related to exporting the Learning Components
 knowledge graph artifacts from an Academic Standards export.

This module implements a shape-preserving Learning Commons Learning Components export:

- Entities: LearningComponent
- Relationships: supports (LearningComponent -> StandardsFrameworkItem)
"""

# Standard Library
import math
import re

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from typing import Any, Iterable
from uuid import UUID, uuid5

# Third Party Library
import pycountry

from loguru import logger

# Package Library
from skg.kgs.export_academic_standards import AcademicStandardsExport
from skg.kgs.llm import infer_atomic_skills
from skg.kgs.prompts import decompose_atomic_skills
from skg.kgs.schemas import LearningComponent, Relationship, StandardsFrameworkItem
from skg.kgs.utils import ExportContext, KGDirs, normalize_ws, stable_text_hash
from skg.kgs.validators import validate_atomic_skills
from skg.schemas import CreateKGConfig
from skg.utils.constants import LANG_PRIMARY_CODE_TO_NAME
from skg.utils.general import open_json_type, write_to_json

# Inline bullets: exclude hyphen/dash so we never split hyphenated words.
_INLINE_BULLET_CHARS = r"[\u2022\u00b7•·\*]"

# Line bullets: allow hyphen/dash, but we only treat them as bullets at line-start.
_LINE_BULLET_CHARS = r"[\u2022\u00b7•·\-\–\—\*]"


@dataclass
class LearningComponentsExport:
    """The output of exporting Learning Components KG artifacts."""

    lc_stats: dict[str, Any]
    learning_components: list[LearningComponent]
    supports_relationships: list[Relationship]


def _build_atomic_skills_prompt_items(
    *, config: CreateKGConfig, sfis: list[StandardsFrameworkItem]
) -> list[dict[str, Any]]:
    """Build prompt payload objects for a batch of expectation SFIs.

    Parameters
    ----------
    config
        The KG export configuration, used to determine which metadata fields to include
        in the prompt for each SFI.
    sfis
        The list of StandardsFrameworkItems representing expectations for which to
        generate prompt items. Each item will be transformed into a dictionary
        containing the relevant text and metadata fields needed for the atomic skills
        decomposition prompt. The transformation will include normalization of
        whitespace and trimming of text to ensure that the prompt stays within
        reasonable length limits for LLM input.
    Returns
    -------
    list[dict[str, Any]]
        A list of dictionaries, each representing an SFI with the necessary fields for
        the atomic skills decomposition prompt. Each dictionary will contain the SFI
        UUID, statement code, grade level, display text, and ID source text, as well as
        optional topic context and auxiliary statements if configured to include them.
    """

    items: list[dict[str, Any]] = []

    for sfi in sfis:
        md = sfi.metadata or {}
        display_text = normalize_ws(sfi.description or "")
        id_source_text = (
            normalize_ws(str(md.get("normalized_text") or "")) or display_text
        )

        payload: dict[str, Any] = {
            "sfi_uuid": str(sfi.case_identifier_uuid),
            "statement_code": sfi.statement_code,
            "grade_level": list(sfi.grade_level or []),
            "id_source_text": _trim_text(max_chars=2000, s=id_source_text),
            "display_text": _trim_text(
                max_chars=2000, s=display_text or id_source_text
            ),
        }

        if config.lc_atomic_skills_include_topic_context:
            pc = (md.get("progression_context") or {}) if isinstance(md, dict) else {}
            topic_ctx = {
                "grade_key": pc.get("grade_key"),
                "stage_key": pc.get("stage_key"),
                "thread_key": pc.get("thread_key"),
                "topic_path_key": pc.get("topic_path_key"),
                "topic_path_parts": pc.get("topic_path_parts"),
            }

            # Only include topic_context when at least one value is non-None; an
            # all-None dict adds prompt tokens for no benefit.
            if any(v is not None for v in topic_ctx.values()):
                payload["topic_context"] = topic_ctx

        if config.lc_atomic_skills_include_aux_statements:
            aux = md.get("aux_statements") if isinstance(md, dict) else None
            aux_items: list[dict[str, Any]] = []

            if isinstance(aux, list):
                for a in aux[:10]:
                    if not isinstance(a, dict):
                        continue

                    aux_items.append(
                        {
                            "role": a.get("role"),
                            "text": _trim_text(
                                max_chars=400, s=str(a.get("text") or "")
                            ),
                        }
                    )

            if aux_items:
                payload["aux_statements"] = aux_items

        items.append(payload)

    return items


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
        assert (
            r.relationship_type == "supports"
        ), f"{r.relationship_type} is not 'supports'"
        relationships.append(
            {
                "id": str(r.identifier),
                "type": r.relationship_type,
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


def _build_provenance_from_sfi(metadata: dict[str, Any]) -> dict[str, Any]:
    """Extract the standard provenance dict from SFI metadata.

    Parameters
    ----------
    metadata
        The SFI metadata dictionary (`sfi.metadata or {}`).

    Returns
    -------
    dict[str, Any]
        A provenance dictionary with page_indices, bbox, bbox_ref, source_decision_ids,
        and source_segment_ids.
    """

    return {
        "bbox": metadata.get("bbox"),
        "bbox_ref": metadata.get("bbox_ref"),
        "page_indices": metadata.get("page_indices", []),
        "source_decision_ids": metadata.get("source_decision_ids", []),
        "source_segment_ids": metadata.get("source_segment_ids", []),
    }


def _build_single_lc(
    *,
    config: CreateKGConfig,
    description: str,
    doc_key: str,
    extra_metadata: dict[str, Any] | None = None,
    fw_metadata: dict[str, Any],
    id_source_kind: str,
    policy: str,
    provenance: dict[str, Any],
    sfi: StandardsFrameworkItem,
    split_display_text: str,
    split_hash: str,
    split_id_text: str,
    split_index: int,
    truncated: bool,
) -> LearningComponent:
    """Construct a single LearningComponent with deterministic ID.

    This is the single source of truth for LC entity construction. All policy paths
    (`1_to_1`, `split_bullets`, `llm_atomic_skills`) delegate here so that the metadata
    contract and UUID seed format are maintained in one place.

    Parameters
    ----------
    config
        KG export configuration (namespace_uuid).
    description
        Human-facing LC description text.
    doc_key
        Document key for UUID-seed construction.
    extra_metadata
        Optional additional metadata entries (e.g. `llm_rationale`, `llm_model`) merged
        into the LC metadata dict.
    fw_metadata
        Standards framework metadata for attribution fields.
    id_source_kind
        Label describing the provenance of the text used for ID generation.
    policy
        The split policy label embedded in the UUID seed and metadata.
    provenance
        The provenance dictionary extracted from the SFI metadata.
    sfi
        The parent StandardsFrameworkItem.
    split_display_text
        The display-oriented text stored in metadata for traceability.
    split_hash
        Stable text hash of the canonical ID text for this split.
    split_id_text
        The canonical text used for ID generation.
    split_index
        Zero-based index of this split within the parent SFI.
    truncated
        Whether the split list was truncated to `lc_max_splits_per_standard`.

    Returns
    -------
    LearningComponent
        A fully constructed LearningComponent entity.
    """

    metadata: dict[str, Any] = {
        "canonical_node_id": (sfi.metadata or {}).get("canonical_node_id"),
        "id_source_kind": id_source_kind,
        "provenance": provenance,
        "split_display_text": split_display_text,
        "split_hash": split_hash,
        "split_id_text": split_id_text,
        "split_index": split_index,
        "split_policy": policy,
        "split_truncated": truncated,
        "supporting_sfi_case_uuid": str(sfi.case_identifier_uuid),
    }

    if extra_metadata:
        metadata.update(extra_metadata)

    return LearningComponent(
        academic_subject=str(
            sfi.academic_subject or fw_metadata["academic_subject_default"]
        ),
        attribution_statement=str(fw_metadata["attribution_statement"]),
        author=str(fw_metadata["author"]),
        description=description,
        identifier=uuid5(
            config.namespace_uuid,
            f"lc:curriculum:{doc_key}:lc:{policy}:{sfi.case_identifier_uuid}:{split_index}:{split_hash}",
        ),
        in_language=str(sfi.in_language or fw_metadata["in_language"]),
        license=str(fw_metadata["license"]),
        metadata=metadata,
        provider=str(fw_metadata["provider"]),
    )


def _create_lcs_for_expectation(
    *,
    config: CreateKGConfig,
    doc_key: str,
    fw_metadata: dict[str, Any],
    id_source_kind_override: str | None = None,
    policy_override: str | None = None,
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
    id_source_kind_override
        If provided, overrides the auto-detected `id_source_kind` label.
    policy_override
        If provided, overrides `config.lc_policy` for both the UUID seed and the
        metadata label.
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

    policy = policy_override or config.lc_policy

    # Display text (human-facing): use SFI.description as exported by academic
    # standards.
    display_text = normalize_ws(sfi.description or "")

    # Canonical ID text: always prefer stable `normalized_text` from SFI.metadata so
    # IDs don't change when description display policy/translations change.
    metadata = sfi.metadata or {}

    id_source_text = normalize_ws(metadata.get("normalized_text") or "")
    id_source_kind = "metadata.normalized_text"

    if not id_source_text:
        # Fallback only if canonical normalized_text is missing.
        id_source_text = display_text
        id_source_kind = "sfi.description_fallback"

    if id_source_kind_override:
        id_source_kind = id_source_kind_override

    # Build ID parts (used for hashing + UUIDv5 name strings).
    id_parts: list[str]

    if policy == "split_bullets":
        id_parts = _split_bullets_deterministic(id_source_text)
        id_parts = id_parts or [id_source_text]
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

    if not id_parts:
        logger.warning(
            f"Zero LearningComponents for expectation SFI "
            f"{sfi.case_identifier_uuid}: both id_source_text and display_text "
            f"are empty (canonical_node_id={metadata.get('canonical_node_id')}). "
            f"This SFI will have no `supports` edge."
        )
        return []

    # Build display parts (used for LC.description). Try to split display_text the same
    # way as ID text so each LC gets a meaningful description.
    display_source_text = display_text or id_source_text
    display_parts: list[str]

    if policy == "split_bullets":
        display_parts = _split_bullets_deterministic(display_source_text)
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

    provenance = _build_provenance_from_sfi(metadata)
    lcs: list[LearningComponent] = []

    for i, (id_part, display_part) in enumerate(paired_parts):
        lcs.append(
            _build_single_lc(
                config=config,
                description=display_part,
                doc_key=doc_key,
                fw_metadata=fw_metadata,
                id_source_kind=id_source_kind,
                policy=policy,
                provenance=provenance,
                sfi=sfi,
                split_display_text=display_part,
                split_hash=stable_text_hash(s=id_part),
                split_id_text=id_part,
                split_index=i,
                truncated=truncated,
            )
        )

    return lcs


def _create_lcs_from_atomic_skills(
    *,
    config: CreateKGConfig,
    doc_key: str,
    fw_metadata: dict[str, Any],
    sfi: StandardsFrameworkItem,
    skills: list[dict[str, Any]],
) -> list[LearningComponent]:
    """Create LearningComponents from validated atomic skills for one SFI.

    NB:

    1. Skill ordering is based on stable_text_hash (description).
    2. LC UUID seed uses split_hash derived from description.

    Parameters
    ----------
    config
        The KG export configuration, used to determine ID namespace and max splits.
    doc_key
        The document key for this export, used in ID generation.
    fw_metadata
        The standards framework metadata, used for populating LC provenance and
        attribution.
    sfi
        The StandardsFrameworkItem representing the expectation for which to create LCs.
    skills
        The list of validated atomic skills dictionaries for this SFI, each containing
        at least a "description" field, and optionally "rationale". These are the
        outputs from the LLM-based atomic skills decomposition and validation process,
        and are assumed to be pre-validated according to the specified criteria (e.g.,
        allowed SFI UUIDs, min/max skills per SFI, presence of rationale if required).
        Each skill will be transformed into a LearningComponent entity, with
        deterministic UUID generation based on the skill description and its position
        in the list.

    Returns
    -------
    list[LearningComponent]
        A list of LearningComponent entities created from the provided atomic skills
        for the given expectation SFI. Each LC will have a deterministic UUID based on
        the doc_key, SFI UUID, skill index, and a hash of the skill description to
        ensure stable IDs across runs.
    """

    policy = "llm_atomic_skills"
    md = sfi.metadata or {}
    provenance = _build_provenance_from_sfi(md)
    max_splits = int(config.lc_max_splits_per_standard)
    norm_skills: list[tuple[str, str]] = []

    for sk in skills:
        desc = normalize_ws(str(sk.get("description") or ""))
        rat = (
            normalize_ws(str(sk.get("rationale") or ""))
            if sk.get("rationale") is not None
            else ""
        )

        if desc:
            norm_skills.append((desc, rat))

    if not norm_skills:
        return []

    keyed: list[tuple[str, str, str]] = []

    for desc, rat in norm_skills:
        h = stable_text_hash(s=desc)
        keyed.append((h, desc, rat))

    keyed.sort(key=lambda t: t[0])

    # Deduplicate by normalized description BEFORE truncation so that duplicates don't
    # consume slots that could be used by unique skills beyond the cutoff.
    seen_desc: set[str] = set()
    deduped: list[tuple[str, str, str]] = []

    for h, desc, rat in keyed:
        nd = " ".join(desc.split()).lower()

        if nd in seen_desc:
            continue

        seen_desc.add(nd)
        deduped.append((h, desc, rat))

    truncated = False

    if len(deduped) > max_splits:
        deduped = deduped[:max_splits]
        truncated = True

    final: list[tuple[str, str]] = [(desc, rat) for _, desc, rat in deduped]

    lcs: list[LearningComponent] = []

    for i, (desc, rat) in enumerate(final):
        split_hash = stable_text_hash(s=desc)

        lcs.append(
            _build_single_lc(
                config=config,
                description=desc,
                doc_key=doc_key,
                extra_metadata={
                    "llm_rationale": rat or None,
                    "llm_model": str(config.model),
                },
                fw_metadata=fw_metadata,
                id_source_kind="llm_atomic_skills.description",
                policy=policy,
                provenance=provenance,
                sfi=sfi,
                split_display_text=desc,
                split_hash=split_hash,
                split_id_text=desc,
                split_index=i,
                truncated=truncated,
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

    return Relationship(
        attribution_statement=str(fw_metadata["attribution_statement"]),
        author=str(fw_metadata["author"]),
        description="",
        identifier=uuid5(
            config.namespace_uuid,
            f"lc:curriculum:{doc_key}:rel:supports:{lc.identifier}:{sfi.case_identifier_uuid}",
        ),
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


def _export_lcs_via_llm_atomic_skills(
    *,
    academic_standards: AcademicStandardsExport,
    config: CreateKGConfig,
    ctx: ExportContext,
    kg_dirs: KGDirs,
) -> tuple[list[LearningComponent], list[Relationship], dict[str, Any]]:
    """Export LearningComponents using LLM-based atomic skills decomposition.

    Parameters
    ----------
    academic_standards
        The exported academic standards artifacts, containing the
        StandardsFrameworkItems that represent normative expectations. These SFIs will
        be the targets of the supports relationships emitted by the LearningComponents
        created in this function.
    config
        The KG export configuration, which includes settings for the LLM-based atomic
        skills decomposition.
    ctx
        The ExportContext, which provides access to the document key and framework
        metadata needed for ID generation and provenance.
    kg_dirs
        The KGDirs for output, used for writing debug information.

    Returns
    -------
    tuple[list[LearningComponent], list[Relationship], dict[str, Any]]
        A tuple containing the list of LearningComponent entities created, the list of
        supports Relationships emitted, and a dictionary of statistics about the LC
        creation process (e.g., split distribution, fallback counts) for analysis and
        debugging.
    """

    expectation_sfis = _iter_expectation_sfis(academic_standards.items)
    fw_metadata = ctx.get_framework_metadata()

    expectation_sfis_sorted = sorted(
        expectation_sfis, key=lambda x: str(x.case_identifier_uuid)
    )

    # Pre-filter: remove SFIs whose text is entirely empty so they never reach the LLM.
    # These would produce 0 LCs anyway (same outcome as the _create_lcs_for_expectation
    # empty-text guard), but sending them to the LLM wastes tokens and risks the model
    # hallucinating content for a blank input.
    batchable_sfis: list[StandardsFrameworkItem] = []
    empty_text_sfis: list[StandardsFrameworkItem] = []

    for sfi in expectation_sfis_sorted:
        if _has_usable_text(sfi):
            batchable_sfis.append(sfi)
        else:
            empty_text_sfis.append(sfi)

    if empty_text_sfis:
        logger.warning(
            f"LLM atomic skills: skipping {len(empty_text_sfis)} expectation SFI(s) "
            f"with empty text (no normalized_text or description). These SFIs will "
            f"have no LearningComponents or `supports` edges. UUIDs: "
            f"{[str(s.case_identifier_uuid) for s in empty_text_sfis[:20]]}"
            + (
                f" ... (+{len(empty_text_sfis) - 20} more)"
                if len(empty_text_sfis) > 20
                else ""
            )
        )

    total_sfis = len(batchable_sfis)
    batch_size = int(config.lc_atomic_skills_batch_size)
    total_batches = math.ceil(total_sfis / batch_size) if total_sfis else 0
    max_splits = int(config.lc_max_splits_per_standard)

    lcs: list[LearningComponent] = []
    rels: list[Relationship] = []
    splits_per_sfi: defaultdict[int, int] = defaultdict(int)

    # Account for empty-text SFIs in the split distribution (0 LCs each).
    if empty_text_sfis:
        splits_per_sfi[0] += len(empty_text_sfis)

    debug_batches: list[dict[str, Any]] = []
    fallback_sfis_total: list[str] = []

    logger.info(
        f"Starting LLM atomic skills export for {total_sfis} SFIs "
        f"across {total_batches} batches (Batch size: {batch_size})."
        + (
            f" ({len(empty_text_sfis)} empty-text SFI(s) excluded.)"
            if empty_text_sfis
            else ""
        )
    )

    for batch_index in range(0, total_sfis, batch_size):
        current_batch_num = (batch_index // batch_size) + 1
        batch = batchable_sfis[batch_index : batch_index + batch_size]

        batch_lcs, batch_rels, batch_splits, batch_debug, batch_fallbacks = (
            _process_atomic_skills_batch(
                batch=batch,
                batch_index=batch_index // batch_size,
                config=config,
                ctx=ctx,
                current_batch_num=current_batch_num,
                fw_metadata=fw_metadata,
                max_splits=max_splits,
                total_batches=total_batches,
            )
        )

        lcs.extend(batch_lcs)
        rels.extend(batch_rels)
        for splits_count, occurrences in batch_splits.items():
            splits_per_sfi[splits_count] += occurrences

        debug_batches.append(batch_debug)
        fallback_sfis_total.extend(batch_fallbacks)

    save_fp = (
        kg_dirs.learning_components / "learning_components_llm_atomic_skills_debug.json"
    )
    write_to_json(fp=save_fp, json_info=debug_batches)

    logger.success(f"Saved LLM atomic skills debug info to: {save_fp}")

    lc_stats = {
        "split_policy": "llm_atomic_skills",
        "total_expectations": len(batchable_sfis) + len(empty_text_sfis),
        "total_expectations_batchable": len(batchable_sfis),
        "total_expectations_empty_text": len(empty_text_sfis),
        "total_lcs": len(lcs),
        "splits_distribution": {str(k): v for k, v in sorted(splits_per_sfi.items())},
        "max_splits_observed": max(splits_per_sfi.keys()) if splits_per_sfi else 0,
        "llm_batches": len(debug_batches),
        "fallback_sfis_count": len(set(fallback_sfis_total)),
    }

    return lcs, rels, lc_stats


def _finalize_lc_export(
    *,
    config: CreateKGConfig,
    ctx: ExportContext,
    kg_dirs: KGDirs,
    lc_stats: dict[str, Any],
    lcs: list[LearningComponent],
    rels: list[Relationship],
) -> LearningComponentsExport:
    """Verify, persist, and wrap LC export artifacts.

    Parameters
    ----------
    config
        KG export configuration.
    ctx
        ExportContext (doc_key).
    kg_dirs
        Output directories.
    lc_stats
        Statistics dictionary for this export run.
    lcs
        The LearningComponent entities to persist.
    rels
        The supports Relationship entities to persist.

    Returns
    -------
    LearningComponentsExport
        The wrapped export object.

    Raises
    ------
    ValueError
        If integrity checks fail.
    """

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
            export_dialect=config.as_export_dialect,
            learning_components=lcs,
            supports_relationships=rels,
        ),
    )
    write_to_json(
        fp=kg_dirs.learning_components / "learning_components_stats.json",
        json_info=lc_stats,
    )

    export = LearningComponentsExport(
        lc_stats=lc_stats, learning_components=lcs, supports_relationships=rels
    )

    logger.success(
        f"Exported Learning Components KG ({lc_stats['split_policy']}): "
        f"{len(export.learning_components)} learning components, "
        f"{len(export.supports_relationships)} `supports` relationships"
    )

    return export


def _handle_atomic_skills_fallback(
    *,
    batch: list[StandardsFrameworkItem],
    config: CreateKGConfig,
    ctx: ExportContext,
    current_batch_num: int,
    fw_metadata: dict[str, Any],
) -> tuple[list[LearningComponent], list[Relationship], dict[int, int]]:
    """Handle fallback logic by creating 1-to-1 LCs for a failed batch.

    Parameters
    ----------
    batch
        The list of SFI items in the current batch.
    config
        The KG export configuration.
    ctx
        The ExportContext for the current export.
    current_batch_num
        The index (1-based) of the current batch being processed.
    fw_metadata
        The framework metadata dict.

    Returns
    -------
    tuple[list[LearningComponent], list[Relationship], dict[int, int]]
        A tuple containing the fallback LCs, supports relationships, and split
        statistics.
    """

    lcs: list[LearningComponent] = []
    rels: list[Relationship] = []
    splits: defaultdict[int, int] = defaultdict(int)

    for sfi_idx, sfi in enumerate(batch, start=1):
        logger.debug(
            f"Batch {current_batch_num} Fallback: Processing SFI "
            f"{sfi.case_identifier_uuid} ({sfi_idx}/{len(batch)})..."
        )

        created = _create_lcs_for_expectation(
            config=config,
            doc_key=ctx.doc_key,
            fw_metadata=fw_metadata,
            id_source_kind_override="fallback_1_to_1",
            policy_override="1_to_1",
            sfi=sfi,
        )
        splits[len(created)] += 1

        for lc_item in created:
            lcs.append(lc_item)
            rels.append(
                _emit_supports(
                    config=config,
                    doc_key=ctx.doc_key,
                    fw_metadata=fw_metadata,
                    lc=lc_item,
                    sfi=sfi,
                )
            )

    return lcs, rels, dict(splits)


def _handle_atomic_skills_success(
    *,
    batch: list[StandardsFrameworkItem],
    batch_debug: dict[str, Any],
    config: CreateKGConfig,
    ctx: ExportContext,
    current_batch_num: int,
    fw_metadata: dict[str, Any],
    skills_by_sfi: dict[str, list[dict[str, Any]]],
) -> tuple[list[LearningComponent], list[Relationship], dict[int, int], list[str]]:
    """Process successfully inferred atomic skills to create LCs.

    Parameters
    ----------
    batch
        The list of SFI items in the current batch.
    batch_debug
        The debug dictionary for the current batch.
    config
        The KG export configuration.
    ctx
        The ExportContext for the current export.
    current_batch_num
        The index (1-based) of the current batch being processed.
    fw_metadata
        The framework metadata dict.
    skills_by_sfi
        A dictionary mapping SFI UUIDs to their generated atomic skills.

    Returns
    -------
    tuple[list[LearningComponent], list[Relationship], dict[int, int], list[str]]
        A tuple containing the generated LCs, supports relationships, split statistics,
        and any fallback SFI UUIDs triggered during empty/invalid creation.

    Raises
    ------
    ValueError
        If an SFI UUID from the batch is missing in the skills_by_sfi mapping,
        indicating a mismatch between the parsed LLM response and the expected SFIs.
        This should not occur if the validation step passed, and would suggest a
        critical bug in the data handling logic.
    """

    lcs: list[LearningComponent] = []
    rels: list[Relationship] = []
    splits: defaultdict[int, int] = defaultdict(int)
    fallback_uuids: list[str] = []

    for sfi_idx, sfi in enumerate(batch, start=1):
        sfi_uuid_str = str(sfi.case_identifier_uuid)

        logger.debug(
            f"Batch {current_batch_num}: Processing SFI "
            f"{sfi_uuid_str} ({sfi_idx}/{len(batch)})..."
        )

        skills = skills_by_sfi.get(sfi_uuid_str, [])

        if not skills:
            raise ValueError(
                f"BUG: SFI {sfi_uuid_str} passed validation but has no skills in "
                f"skills_by_sfi. This indicates a mapping error between parsed_dict "
                f"and skills_by_sfi."
            )

        created = _create_lcs_from_atomic_skills(
            config=config,
            doc_key=ctx.doc_key,
            fw_metadata=fw_metadata,
            sfi=sfi,
            skills=skills,
        )

        if not created:
            logger.warning(
                f"Atomic skills for SFI {sfi_uuid_str} produced 0 LCs "
                f"after normalization/dedup; falling back to 1_to_1."
            )
            created = _create_lcs_for_expectation(
                config=config,
                doc_key=ctx.doc_key,
                fw_metadata=fw_metadata,
                id_source_kind_override="fallback_1_to_1",
                policy_override="1_to_1",
                sfi=sfi,
            )
            batch_debug["fallback_sfi_uuids"].append(sfi_uuid_str)
            fallback_uuids.append(sfi_uuid_str)

        splits[len(created)] += 1

        for lc_item in created:
            lcs.append(lc_item)
            rels.append(
                _emit_supports(
                    config=config,
                    doc_key=ctx.doc_key,
                    fw_metadata=fw_metadata,
                    lc=lc_item,
                    sfi=sfi,
                )
            )

    return lcs, rels, dict(splits), fallback_uuids


def _has_usable_text(sfi: StandardsFrameworkItem) -> bool:
    """Check whether an SFI has enough text to be worth sending to the LLM.

    Returns False when both the canonical normalized_text and the exported description
    are empty/whitespace-only, which is the same condition that causes
    `_create_lcs_for_expectation` to return an empty list.

    Parameters
    ----------
    sfi
        The StandardsFrameworkItem to check.

    Returns
    -------
    bool
        True if the SFI has at least one non-empty text source.
    """

    md = sfi.metadata or {}
    id_source = normalize_ws(str(md.get("normalized_text") or ""))
    display = normalize_ws(sfi.description or "")

    return bool(id_source or display)


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

    expectation_sfis = [
        sfi for sfi in items if sfi.normalized_statement_type == "Standard"
    ]

    logger.info(f"Found {len(expectation_sfis)} expectation SFIs.")

    return expectation_sfis


def _process_atomic_skills_batch(
    *,
    batch: list[StandardsFrameworkItem],
    batch_index: int,
    config: CreateKGConfig,
    ctx: ExportContext,
    current_batch_num: int,
    fw_metadata: dict[str, Any],
    max_splits: int,
    total_batches: int,
) -> tuple[
    list[LearningComponent],
    list[Relationship],
    dict[int, int],
    dict[str, Any],
    list[str],
]:
    """Process a single batch of SFIs via LLM inference to create LCs.

    Parameters
    ----------
    batch
        The list of SFI items in the current batch.
    batch_index
        The normalized batch index (0-based) for debugging.
    config
        The KG export configuration.
    ctx
        The ExportContext for the current export.
    current_batch_num
        The current human-readable batch number (1-based).
    fw_metadata
        The framework metadata dict.
    max_splits
        The maximum number of splits allowed per standard.
    total_batches
        The total number of batches to process.

    Returns
    -------
    tuple[list[LearningComponent], list[Relationship], dict[int, int], dict[str, Any], list[str]]
        A tuple containing the LCs, relationships, splits distribution, debug information,
        and fallback UUIDs for this batch.
    """

    logger.info(
        f"Processing batch {current_batch_num}/{total_batches} ({len(batch)} SFIs)..."
    )

    allowed = {UUID(str(s.case_identifier_uuid)) for s in batch}
    prompt_items = _build_atomic_skills_prompt_items(config=config, sfis=batch)
    prompt = decompose_atomic_skills(
        display_language=format_language_for_prompt(
            tag=str(fw_metadata["in_language"])
        ),
        items=prompt_items,
        max_per_sfi=max_splits,
        min_per_sfi=int(config.lc_atomic_skills_min_per_sfi),
        require_rationale=bool(config.lc_atomic_skills_require_rationale),
    )

    batch_debug: dict[str, Any] = {
        "batch_index": batch_index,
        "input_items": prompt_items,
        "response": None,
        "fallback_sfi_uuids": [],
        "error": None,
    }

    skills_by_sfi: dict[str, list[dict[str, Any]]] = {}
    fallback_sfis_total: list[str] = []

    try:
        parsed = infer_atomic_skills(
            always_double_check_first_attempt=config.always_double_check_first_attempt,
            instructions=prompt.system_message,
            user_message=prompt.user_message,
            validator=partial(
                validate_atomic_skills,
                allowed_sfi_uuids=allowed,
                min_per_sfi=int(config.lc_atomic_skills_min_per_sfi),
                max_per_sfi=max_splits,
                require_rationale=bool(config.lc_atomic_skills_require_rationale),
            ),
        )

        parsed_dict = parsed.model_dump(mode="json")
        batch_debug["response"] = parsed_dict

        for it in parsed_dict.get("items", []):
            sfi_uuid = str(it.get("sfi_uuid"))
            skills_by_sfi[sfi_uuid] = list(it.get("skills") or [])
    except Exception as e:  # pylint: disable=broad-except
        batch_debug["error"] = f"{e.__class__.__name__}: {e}"
        batch_debug["fallback_sfi_uuids"] = [str(s.case_identifier_uuid) for s in batch]
        fallback_sfis_total.extend(batch_debug["fallback_sfi_uuids"])

        logger.warning(
            f"Atomic skills batch {current_batch_num} failed "
            f"({e.__class__.__name__}); all {len(batch)} SFI(s) in this batch "
            f"fall back to 1_to_1. SFI UUIDs: "
            f"{batch_debug['fallback_sfi_uuids']}"
        )

        lcs, rels, splits = _handle_atomic_skills_fallback(
            batch=batch,
            config=config,
            ctx=ctx,
            current_batch_num=current_batch_num,
            fw_metadata=fw_metadata,
        )
        return lcs, rels, splits, batch_debug, fallback_sfis_total

    lcs, rels, splits, fallback_uuids = _handle_atomic_skills_success(
        batch=batch,
        batch_debug=batch_debug,
        config=config,
        ctx=ctx,
        current_batch_num=current_batch_num,
        fw_metadata=fw_metadata,
        skills_by_sfi=skills_by_sfi,
    )
    fallback_sfis_total.extend(fallback_uuids)

    return lcs, rels, splits, batch_debug, fallback_sfis_total


def _split_bullets_deterministic(text: str) -> list[str]:
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

    # Detect markers before we mutate heavily. NB: (?:^|\s+) so a leading bullet
    # ("• item 1 • item 2") is detected too.
    has_inline_bullet = bool(re.search(rf"(?:^|\s+){_INLINE_BULLET_CHARS}\s+", src))
    has_line_bullet = any(
        re.match(rf"^{_LINE_BULLET_CHARS}\s*", ln.strip()) for ln in src.split("\n")
    )
    has_numbering = bool(
        re.search(r"(?m)^\s*(?:\(?\d+\)?|[A-Za-z]|[ivxlcdmIVXLCDM]+)[\)\.]\s+", src)
    )
    had_list_marker = has_inline_bullet or has_line_bullet or has_numbering

    # NB: (?:^|\s+) so a leading bullet is also converted to a newline split point.
    # A leading \n from ^-match is harmless; the split+strip below discards it.
    src = re.sub(rf"(?:^|\s+){_INLINE_BULLET_CHARS}\s+", "\n• ", src)
    lines = [line.strip() for line in re.split(r"\n+", src) if line.strip()]

    if not lines:
        return []

    parts: list[str] = []

    for line in lines:
        line2 = re.sub(rf"^{_LINE_BULLET_CHARS}\s*", "", line).strip()
        line2 = re.sub(
            r"^(?:\(?\d+\)?|[A-Za-z]|[ivxlcdmIVXLCDM]+)[\)\.]\s+", "", line2
        ).strip()
        line2 = re.sub(
            r"^(?:\(?\d+\)?|[A-Za-z]|[ivxlcdmIVXLCDM]+)\s*[-–—]\s+", "", line2
        ).strip()
        line2 = normalize_ws(line2)

        if line2:
            parts.append(line2)

    # If there was an explicit marker and we got 1 clean part, keep it.
    if len(parts) == 1 and had_list_marker:
        return parts

    if len(parts) < 2:
        return []

    # De-dupe while preserving order.
    deduped: list[str] = []
    seen: set[str] = set()

    for p in parts:
        if p not in seen:
            deduped.append(p)
            seen.add(p)

    return deduped


def _trim_text(*, max_chars: int, s: str) -> str:
    """Trim text to a maximum number of characters, adding ellipsis if truncated.

    Parameters
    ----------
    max_chars
        The maximum number of characters to allow in the output string. If the input
        string exceeds this length, it will be truncated and an ellipsis character
        (...) will be appended, ensuring that the total length does not exceed
        max_chars.
    s
        The input string to trim. This is typically the text used for ID generation or
        display in the LearningComponent. It will be normalized for whitespace before
        trimming, to ensure consistent length counting.

    Returns
    -------
    str
        The trimmed string, with normalized whitespace and an ellipsis if truncation
        occurred. If the input string is within the max_chars limit, it will be
        returned unchanged (except for whitespace normalization). If it exceeds the
        limit, it will be truncated to fit within max_chars when the ellipsis is added.
    """

    s2 = normalize_ws(s or "")

    if len(s2) <= max_chars:
        return s2

    if max_chars < 4:
        return s2[:max_chars]

    return s2[: max_chars - 3].rstrip() + "..."


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
        edges MUST target emitted StandardsFrameworkItems by `case_identifier_uuid`.
    config
        KG config for LC policy + deterministic ID namespace.
    ctx
        ExportContext (doc_key, framework metadata, indexes).
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

    if config.lc_policy == "llm_atomic_skills":
        lcs, rels, lc_stats = _export_lcs_via_llm_atomic_skills(
            academic_standards=academic_standards,
            config=config,
            ctx=ctx,
            kg_dirs=kg_dirs,
        )

        return _finalize_lc_export(
            config=config,
            ctx=ctx,
            kg_dirs=kg_dirs,
            lc_stats=lc_stats,
            lcs=lcs,
            rels=rels,
        )

    expectation_sfis = _iter_expectation_sfis(academic_standards.items)
    fw_metadata = ctx.get_framework_metadata()
    lcs = []
    rels = []
    splits_per_sfi: defaultdict[int, int] = defaultdict(int)

    # Deterministic order: sort by SFI UUID string.
    expectation_sfis_sorted = sorted(
        expectation_sfis, key=lambda x: x.case_identifier_uuid
    )

    for sfi in expectation_sfis_sorted:
        created_lcs = _create_lcs_for_expectation(
            config=config, doc_key=ctx.doc_key, fw_metadata=fw_metadata, sfi=sfi
        )
        splits_per_sfi[len(created_lcs)] += 1

        for lc in created_lcs:
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
    zero_lc_count = splits_per_sfi.get(0, 0)

    if zero_lc_count > 0:
        logger.warning(
            f"Learning Components: {zero_lc_count} expectation SFI(s) produced 0 "
            f"LearningComponents (empty text). These SFIs have no `supports` edges."
        )

    lc_stats = {
        "max_splits_observed": max(splits_per_sfi.keys()) if splits_per_sfi else 0,
        "split_policy": config.lc_policy,
        "splits_distribution": {str(k): v for k, v in sorted(splits_per_sfi.items())},
        "total_expectations": len(expectation_sfis_sorted),
        "total_lcs": len(lcs),
    }
    return _finalize_lc_export(
        config=config,
        ctx=ctx,
        kg_dirs=kg_dirs,
        lc_stats=lc_stats,
        lcs=lcs,
        rels=rels,
    )


def format_language_for_prompt(*, include_tag: bool = False, tag: str | None) -> str:
    """Format a BCP-47 language tag as a human-friendly language name for prompts.

    Parameters
    ----------
    include_tag
        If True, include the original tag in parentheses, e.g. "English (en)".
    tag
        A BCP-47 language tag like "en", "fr", "sw", or "en-US". May be None.

    Returns
    -------
    str
        A human-readable language name (optionally with the tag).
    """

    raw = normalize_ws(str(tag or "")).strip()

    if not raw:
        return "English"

    # Normalize tag formatting but preserve the original for display.
    tag_norm = raw.replace("_", "-")
    primary = tag_norm.split("-")[0].lower().strip()
    name = LANG_PRIMARY_CODE_TO_NAME.get(primary)

    if not name:
        lang = (
            pycountry.languages.get(alpha_2=primary)
            or pycountry.languages.get(alpha_3=primary)
            or pycountry.languages.get(bibliographic=primary)
            or pycountry.languages.get(terminology=primary)
        )

        if lang and getattr(lang, "name", None):
            name = str(lang.name)

    # Final fallback: return the tag itself.
    if not name:
        return tag_norm

    return f"{name} ({tag_norm})" if include_tag else name


def load_learning_components_export(kg_dirs: KGDirs) -> LearningComponentsExport:
    """Reconstruct a LearningComponentsExport from previously written disk artifacts.

    Parameters
    ----------
    kg_dirs
        The KG output directories containing the prior run's artifacts.

    Returns
    -------
    LearningComponentsExport
        The reconstructed export object.
    """

    d = kg_dirs.learning_components

    learning_components = [
        LearningComponent.model_validate(raw)
        for raw in open_json_type(d / "learning_components.json")
    ]
    supports_relationships = [
        Relationship.model_validate(raw)
        for raw in open_json_type(d / "learning_components_supports_relationships.json")
    ]

    # `lc_stats` is persisted so the policy coverage report can be fully regenerated.
    lc_stats_fp = d / "learning_components_stats.json"
    lc_stats: dict = open_json_type(lc_stats_fp) if lc_stats_fp.exists() else {}

    return LearningComponentsExport(
        lc_stats=lc_stats,
        learning_components=learning_components,
        supports_relationships=supports_relationships,
    )


def load_or_export_learning_components(
    *,
    academic_standards: AcademicStandardsExport,
    config: CreateKGConfig,
    ctx: ExportContext,
    kg_dirs: KGDirs,
) -> tuple[LearningComponentsExport, bool]:
    """Load an existing Learning Components KG from disk or export a new one.

    Checks whether the learning components sentinel bundle file already exists on disk.
    If it exists and `config.overwrite` is False, the prior export is loaded from disk.
    Otherwise, a new export is generated.

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

    Returns
    -------
    tuple[LearningComponentsExport, bool]
        A tuple containing the Learning Components export artifacts and a boolean
        indicating whether the export was reused from disk (`True`) or newly generated
        (`False`).
    """

    lc_sentinel = kg_dirs.learning_components / "learning_components_kg.json"
    lc_reused = False

    if lc_sentinel.exists() and not config.overwrite:
        logger.warning(
            "Learning Components KG already exists and overwrite=False--loading "
            "from disk."
        )

        learning_components = load_learning_components_export(kg_dirs)
        lc_reused = True
    else:
        if lc_sentinel.exists():
            logger.warning(
                "config.overwrite=True: re-exporting Learning Components KG (existing "
                "artifacts will be overwritten)."
            )

        learning_components = export_learning_components(
            academic_standards=academic_standards,
            config=config,
            ctx=ctx,
            kg_dirs=kg_dirs,
        )

    return learning_components, lc_reused
