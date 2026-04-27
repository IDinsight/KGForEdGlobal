"""This module contains functionalities related to exporting the Learning Components
 knowledge graph artifacts from an Academic Standards export.

This module implements a shape-preserving Learning Commons Learning Components export:

- Entities: LearningComponent
- Relationships: supports (LearningComponent -> StandardsFrameworkItem)
"""

# Standard Library
import json
import math
import re

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from functools import partial
from typing import Any, Iterable
from uuid import UUID, uuid5

# Third Party Library
import pycountry

from loguru import logger

# Package Library
from skg.kgs.export_academic_standards import AcademicStandardsExport
from skg.kgs.llm import KGUsageTracker, infer_atomic_skills
from skg.kgs.prompts import decompose_atomic_skills
from skg.kgs.schemas import LearningComponent, Relationship, StandardsFrameworkItem
from skg.kgs.utils import (
    ExportContext,
    KGDirs,
    canonicalize_stable_text,
    normalize_ws,
    stable_text_hash,
)
from skg.kgs.validators import validate_atomic_skills
from skg.schemas import CreateKGConfig
from skg.utils.constants import LANG_PRIMARY_CODE_TO_NAME
from skg.utils.general import open_json_type, write_to_json

_DEFAULT_LC_PROMPT_LANGUAGE_INSTRUCTION = "the same language as the input text"

# Inline bullets: exclude hyphen/dash so we never split hyphenated words.
_INLINE_BULLET_CHARS = r"[\u2022\u00b7•·\*]"

# Line bullets: allow hyphen/dash, but we only treat them as bullets at line-start.
_LINE_BULLET_CHARS = r"[\u2022\u00b7•·\-\–\—\*]"

SUPPORTS = "supports"


@dataclass
class LearningComponentsExport:
    """The output of exporting Learning Components KG artifacts."""

    lc_stats: dict[str, Any]
    learning_components: list[LearningComponent]
    supports_relationships: list[Relationship]


@dataclass(frozen=True)
class LearningComponentsSourceDecision:
    """Eligibility decision for whether one SFI should generate LearningComponents.

    Academic Standards export and Learning Components export make different decisions:
    a StandardsFrameworkItem may be a valid standards node while still being too broad
    to decompose into granular LearningComponents. This decision record makes that
    second LC-source decision explicit, reportable, and configurable.
    """

    eligible: bool
    fields: dict[str, Any]
    reasons: list[str]
    sfi: StandardsFrameworkItem


def _atomic_skills_cache_key_from_prompt_item(prompt_item: dict[str, Any]) -> str:
    """Build a deterministic semantic cache key for atomic-skills inference.

    The key includes the meaning-bearing prompt fields that can legitimately affect
    atomic-skill decomposition:

    1. `display_text`
    2. `language_instruction`
    3. `statement_type`
    4. `source_label` when present in the prompt payload
    5. `topic_context` (grade/stage keys and topic role+label path)
    6. `aux_statements` (role+text only)

    The key intentionally excludes SFI identity, statement codes, provenance, and
    debug-only truncation fields. This allows repeated equivalent prompt content to
    reuse the same decomposition while avoiding false cache hits when source labels,
    topic context, auxiliary statements, or output-language instructions change the
    effective prompt meaning.

    Parameters
    ----------
    prompt_item
        A prompt item produced by `_build_single_prompt_item()`.

    Returns
    -------
    str
        A stable cache key suitable for a within-run atomic-skills cache.
    """

    cache_basis = {
        "aux_statements": _normalize_atomic_skills_cache_aux_statements(
            prompt_item.get("aux_statements")
        ),
        "display_text": _normalize_atomic_skills_cache_text(
            prompt_item.get("display_text")
        ),
        "language_instruction": _normalize_atomic_skills_cache_text(
            prompt_item.get("language_instruction")
        ),
        "statement_type": _normalize_atomic_skills_cache_text(
            prompt_item.get("statement_type")
        ),
        "source_label": _normalize_atomic_skills_cache_text(
            prompt_item.get("source_label")
        ),
        "topic_context": _normalize_atomic_skills_cache_topic_context(
            prompt_item.get("topic_context")
        ),
    }
    cache_basis_json = json.dumps(
        cache_basis, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return f"atomic_skills:{stable_text_hash(s=cache_basis_json)}"


def _bbox_reading_order(metadata: dict[str, Any]) -> tuple[float, float]:
    """Return a `(top, left)` reading-order key from SFI bbox metadata.

    The bbox format is `[x0, y0, x1, y1]`, so sorting by `(y0, x0)` approximates
    top-to-bottom, left-to-right document order when page/path metadata is otherwise
    tied.

    Parameters
    ----------
    metadata
        The metadata dictionary from a StandardsFrameworkItem, which may contain a
        "bbox" field with a list of four numbers representing the bounding box of the
        source text on the page.

    Returns
    -------
    tuple[float, float]
        A `(top, left)` key for reading-order sorting, or `(inf, inf)` if bbox metadata
        is unavailable or invalid, which will sort after any valid keys.
    """

    bbox = metadata.get("bbox") or []

    if isinstance(bbox, list) and len(bbox) >= 2:
        return _sort_number(value=bbox[1]), _sort_number(value=bbox[0])

    return float("inf"), float("inf")


def _build_atomic_skills_response_dict(
    *,
    sfis: list[StandardsFrameworkItem],
    skills_by_sfi: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Build a schema-shaped AtomicSkillsResponse dictionary in SFI order.

    Parameters
    ----------
    sfis
        SFIs whose order should be preserved in the response-shaped debug object.
    skills_by_sfi
        Mapping from SFI UUID string to skill dictionaries.

    Returns
    -------
    dict[str, Any]
        A JSON-serializable dictionary shaped like AtomicSkillsResponse.
    """

    return {
        "items": [
            {
                "sfi_uuid": str(sfi.case_identifier_uuid),
                "skills": deepcopy(
                    skills_by_sfi.get(str(sfi.case_identifier_uuid), [])
                ),
            }
            for sfi in sfis
        ]
    }


def _build_lc_graph_bundle(
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

    Raises
    ------
    ValueError
        If any relationship in `supports_relationships` does not have the expected
        relationship type defined by the `SUPPORTS` constant.
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
        if r.relationship_type != SUPPORTS:
            raise ValueError(f"{r.relationship_type} is not '{SUPPORTS}'")

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


def _build_lc_semantic_context_from_sfi(sfi: StandardsFrameworkItem) -> dict[str, Any]:
    """Extract stable semantic context from the supporting SFI for LC metadata.

    The goal is to make each LearningComponent more self-describing when inspected on
    its own, without changing LC identity semantics or duplicating low-level provenance
    fields that already live under `metadata["provenance"]`.

    Included fields are intentionally limited to higher-level semantic context that is
    useful for downstream inspection, filtering, and debugging, such as statement
    typing, canonical placement, progression context, and attached auxiliary
    statements.

    Parameters
    ----------
    sfi
        The supporting StandardsFrameworkItem.

    Returns
    -------
    dict[str, Any]
        A dictionary of selected semantic context fields copied from the supporting SFI
        and its metadata.
    """

    metadata = sfi.metadata or {}
    semantic_context: dict[str, Any] = {
        "supporting_sfi_canonical_path_key": metadata.get("canonical_path_key"),
        "supporting_sfi_grade_level": list(sfi.grade_level or []),
        "supporting_sfi_in_language": str(sfi.in_language or ""),
        "supporting_sfi_normalized_statement_type": sfi.normalized_statement_type,
        "supporting_sfi_role": metadata.get("role"),
        "supporting_sfi_source_label": metadata.get("source_label"),
        "supporting_sfi_statement_code": sfi.statement_code,
        "supporting_sfi_statement_type": sfi.statement_type,
    }
    progression_context = metadata.get("progression_context")

    if isinstance(progression_context, dict) and progression_context:
        semantic_context["supporting_sfi_progression_context"] = deepcopy(
            progression_context
        )

    aux_statements = metadata.get("aux_statements")

    if isinstance(aux_statements, list) and aux_statements:
        semantic_context["supporting_sfi_aux_statements"] = deepcopy(aux_statements)

    return {
        key: value
        for key, value in semantic_context.items()
        if value is not None and value != "" and value != [] and value != {}
    }


def _build_lc_source_eligibility_report(
    decisions: list[LearningComponentsSourceDecision],
) -> dict[str, Any]:
    """Build a pre-generation report of which SFIs are eligible LC sources.

    Parameters
    ----------
    decisions
        A list of LearningComponentsSourceDecision records for all SFIs considered as
        LC sources.

    Returns
    -------
    dict[str, Any]
        A JSON-serializable report dictionary summarizing LC source eligibility
        decisions, including counts by various SFI metadata fields, example eligible
        and excluded SFIs with reasons, and overall statistics.
    """

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    eligible = [decision for decision in decisions if decision.eligible]
    excluded = [decision for decision in decisions if not decision.eligible]
    reason_counts: Counter[str] = Counter()

    for decision in decisions:
        for reason in decision.reasons:
            reason_counts[reason] += 1

    return {
        "counts": {
            "by_normalized_statement_type": _summarize_lc_source_decisions(
                decisions=decisions, group_key="normalized_statement_type"
            ),
            "by_path_pattern": _summarize_lc_source_decisions(
                decisions=decisions, group_key="path_pattern"
            ),
            "by_role": _summarize_lc_source_decisions(
                decisions=decisions, group_key="role"
            ),
            "by_source_label": _summarize_lc_source_decisions(
                decisions=decisions, group_key="source_label"
            ),
            "by_statement_type": _summarize_lc_source_decisions(
                decisions=decisions, group_key="statement_type"
            ),
        },
        "description": (
            "Pre-generation report showing which StandardsFrameworkItems were eligible "
            "to generate LearningComponents under the configured LC source filters. "
            "Excluded SFIs remain valid standards items; they simply do not produce LCs."
        ),
        "examples": {
            "eligible": [_lc_source_decision_example(d) for d in eligible[:25]],
            "excluded": [_lc_source_decision_example(d) for d in excluded[:50]],
        },
        "generated_at": generated_at,
        "report_type": "learning_components_source_eligibility",
        "review_guidance": [
            "Use this report before inspecting generated LCs: it explains which standards were allowed to become LC sources.",
            "Broad framework competencies should usually remain in Academic Standards but may be excluded here.",
            "Tighten or loosen lc_source_* config fields, then re-export Learning Components.",
        ],
        "summary": {
            "eligible_sfis": len(eligible),
            "excluded_sfis": len(excluded),
            "reason_counts": dict(sorted(reason_counts.items())),
            "total_sfis_considered": len(decisions),
        },
    }


def _build_lc_source_level_quality_report(
    learning_components: list[LearningComponent],
) -> dict[str, Any]:
    """Build a descriptive QA report for the SFI levels that produced Learning
    Components.

    This report is intentionally curriculum-agnostic. It does not classify any source
    label or path pattern as "good" or "bad". Instead, it exposes Learning Components
    by source-label, statement-type, and role-only path-pattern so broad or unexpected
    LC sources become visible during review.

    Parameters
    ----------
    learning_components
        The LearningComponents emitted by the export.

    Returns
    -------
    dict[str, Any]
        JSON-serializable source-level quality report.
    """

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    records = [_lc_source_level_record(lc) for lc in learning_components]
    supporting_sfi_uuids = {
        record["supporting_sfi_case_uuid"]
        for record in records
        if record["supporting_sfi_case_uuid"] != "(missing)"
    }
    total_lcs = len(records)

    source_label_rows = _summarize_lc_source_level_records(
        group_key="supporting_sfi_source_label", records=records, total_lcs=total_lcs
    )
    statement_type_rows = _summarize_lc_source_level_records(
        group_key="supporting_sfi_statement_type", records=records, total_lcs=total_lcs
    )
    path_pattern_rows = _summarize_lc_source_level_records(
        group_key="supporting_sfi_path_pattern", records=records, total_lcs=total_lcs
    )

    return {
        "counts": {
            "by_supporting_sfi_source_label": source_label_rows,
            "by_supporting_sfi_statement_type": statement_type_rows,
            "by_supporting_sfi_path_pattern": path_pattern_rows,
        },
        "description": (
            "Descriptive QA report showing which source SFI labels, statement types, "
            "and hierarchy path patterns produced LearningComponents. Use this to "
            "spot unexpectedly broad or weakly-labeled LC sources before treating an "
            "export as production quality."
        ),
        "generated_at": generated_at,
        "report_type": "learning_components_source_level_quality",
        "review_guidance": [
            "This report is descriptive only; it does not drop or relabel any LCs.",
            "Rows with high Learning Component counts at shallow path patterns may indicate broad framework-level sources worth reviewing.",
            "Rows with '(missing)' values indicate metadata gaps in the supporting SFI export.",
        ],
        "total_lcs": total_lcs,
        "total_supporting_sfis": len(supporting_sfi_uuids),
    }


def _build_provenance_from_sfi(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Extract standard provenance fields from SFI metadata.

    Parameters
    ----------
    metadata
        The SFI metadata dictionary (`sfi.metadata or {}`), or None.

    Returns
    -------
    dict[str, Any]
        A provenance dictionary containing bbox, bbox_ref, page_indices,
        source_decision_ids, and source_segment_ids.
    """

    metadata = metadata or {}
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
    split_id_text: str,
    split_index: int,
    truncated: bool,
) -> LearningComponent:
    """Construct a single LearningComponent with deterministic ID.

    This is the single source of truth for LC entity construction. All policy paths
    (`1_to_1`, `split_bullets`, `llm_atomic_skills`) delegate here so that the metadata
    contract, text normalization, semantic-context carry-through, and UUID seed format
    are maintained in one place.

    Parameters
    ----------
    config
        KG export configuration (namespace_uuid).
    description
        Human-facing LC description text.
    doc_key
        Document key for UUID-seed construction.
    extra_metadata
        Optional additional metadata entries (e.g. `llm_rationale`) merged into the LC
        metadata dict.
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

    description = normalize_ws(description)
    id_source_kind = normalize_ws(id_source_kind)
    policy = normalize_ws(policy)
    split_display_text = normalize_ws(split_display_text)
    split_id_text = normalize_ws(split_id_text)
    split_hash = stable_text_hash(s=split_id_text)
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
        **_build_lc_semantic_context_from_sfi(sfi=sfi),
    }

    if extra_metadata:
        metadata.update(extra_metadata)

    date_created, date_modified = _resolve_derived_timestamps(sfi)
    return LearningComponent(
        academic_subject=str(
            sfi.academic_subject or fw_metadata["academic_subject_default"]
        ),
        attribution_statement=str(fw_metadata["attribution_statement"]),
        author=str(fw_metadata["author"]),
        date_created=date_created,
        date_modified=date_modified,
        description=description or split_display_text or split_id_text,
        identifier=uuid5(
            config.namespace_uuid,
            f"lc:curriculum:{doc_key}:lc:{policy}:{sfi.case_identifier_uuid}:{split_index}:{split_hash}",
        ),
        in_language=_resolve_lc_output_language_tag(
            config=config, fw_metadata=fw_metadata, sfi=sfi
        ),
        license=str(fw_metadata["license"]),
        metadata=metadata,
        provider=str(fw_metadata["provider"]),
    )


def _build_single_prompt_item(
    *, config: CreateKGConfig, fw_metadata: dict[str, Any], sfi: StandardsFrameworkItem
) -> dict[str, Any]:
    """Build the prompt payload object for a single expectation SFI.

    Parameters
    ----------
    config
        The KG export configuration, used to determine which metadata fields to include.
    fw_metadata
        Framework metadata dict used for fallback language values.
    sfi
        The StandardsFrameworkItem representing the expectation.

    Returns
    -------
    dict[str, Any]
        A dictionary representing the SFI with the fields needed for the atomic-skills
        decomposition prompt. Includes an item-specific language instruction so
        mixed-language batches can still be handled correctly. The full untrimmed
        source text is intentionally not included here to keep prompt size bounded.
        Downstream validation checks response structure, allowed SFI UUIDs, min/max
        skill counts, and rationale policy.
    """

    metadata = sfi.metadata or {}
    source_text = _resolve_prompt_display_text(sfi)
    trimmed_display_text, display_text_was_truncated, display_text_original_length = (
        _trim_text_with_debug(max_chars=2000, s=source_text)
    )

    payload: dict[str, Any] = {
        "display_text": trimmed_display_text,
        "language_instruction": _resolve_lc_prompt_language_instruction(
            config=config, fw_metadata=fw_metadata, sfi=sfi
        ),
        "sfi_uuid": str(sfi.case_identifier_uuid),
    }

    if display_text_was_truncated:
        payload["display_text_truncated"] = True
        payload["display_text_original_length"] = display_text_original_length
        payload["display_text_max_chars"] = 2000

    if sfi.statement_code:
        payload["statement_code"] = sfi.statement_code

    statement_type = normalize_ws(str(sfi.statement_type or ""))
    source_label = normalize_ws(str(metadata.get("source_label") or ""))

    if statement_type:
        payload["statement_type"] = statement_type

    if source_label and source_label != statement_type:
        payload["source_label"] = source_label

    if config.lc_atomic_skills_include_topic_context:
        if topic_context := _extract_topic_context(metadata):
            payload["topic_context"] = topic_context

    if config.lc_atomic_skills_include_aux_statements:
        if aux_statements := _extract_aux_statements(metadata):
            payload["aux_statements"] = aux_statements

    return payload


def _clean_lc_source_filter_value(value: Any) -> str:
    """Normalize a config/source value for LC source eligibility comparisons.

    Parameters
    ----------
    value
        Raw config or SFI metadata value to normalize for eligibility filtering.

    Returns
    -------
    str
        A normalized string suitable for case-insensitive comparison. Missing/blank
        values are normalized to an empty string.
    """

    return normalize_ws(str(value or "")).casefold()


def _clean_lc_source_report_value(value: Any) -> str:
    """Normalize a source-selection report value without case-folding display text.

    Parameters
    ----------
    value
        Raw metadata value from a LearningComponent or supporting SFI.

    Returns
    -------
    str
        A normalized non-empty string. Missing/blank values are represented with the
        explicit "(missing)" sentinel so gaps are visible in reports.
    """

    cleaned = normalize_ws(str(value or ""))
    return cleaned if cleaned else "(missing)"


def _config_value_set(values: Iterable[Any] | None) -> set[str]:
    """Return normalized non-empty config values as a set.

    Parameters
    ----------
    values
        An iterable of raw config values to normalize and convert to a set. Missing or
        blank values are ignored.

    Returns
    -------
    set[str]
        A set of normalized, non-empty strings suitable for eligibility comparisons. If
        the input is None or contains no valid values, an empty set is returned.
    """

    if not values:
        return set()

    return {
        cleaned for value in values if (cleaned := _clean_lc_source_filter_value(value))
    }


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
        LearningComponents created for the given expectation SFI according to the
        requested split policy.
    """

    policy = normalize_ws(policy_override or config.lc_policy)
    max_splits = int(config.lc_max_splits_per_standard)
    display_text, id_source_text, id_source_kind, metadata = _resolve_lc_text_sources(
        id_source_kind_override=id_source_kind_override, sfi=sfi
    )
    id_parts, truncated = _split_lc_parts(
        max_splits=max_splits, policy=policy, text=id_source_text
    )

    if not id_parts and display_text:
        id_parts = [display_text]

    if not id_parts:
        logger.warning(
            f"Zero LearningComponents for expectation SFI "
            f"{sfi.case_identifier_uuid}: both id_source_text and display_text "
            f"are empty (canonical_node_id={metadata.get('canonical_node_id')}). "
            f"This SFI will have no `supports` edge."
        )

        return []

    display_source_text = display_text or id_source_text
    display_parts, _ = _split_lc_parts(
        max_splits=max_splits, policy=policy, text=display_source_text
    )

    if not display_parts and display_source_text:
        display_parts = [display_source_text]

    paired_parts = _pair_id_and_display_parts(
        display_parts=display_parts, id_parts=id_parts
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
    max_splits: int,
    sfi: StandardsFrameworkItem,
    skills: list[dict[str, Any]],
) -> list[LearningComponent]:
    """Create LearningComponents from validated atomic skills for one SFI.

    NB:

    1. Skill ordering preserves the validated LLM/source order.
    2. Deduplication uses the same canonical text normalization as `stable_text_hash()`.
    3. LC UUID seed uses the split policy, supporting SFI UUID, split index, and
        `split_hash` derived from description.

    Parameters
    ----------
    config
        The KG export configuration, used to determine ID namespace and LC metadata.
    doc_key
        The document key for this export, used in ID generation.
    fw_metadata
        The standards framework metadata, used for populating LC provenance and
        attribution.
    max_splits
        The maximum number of atomic skills to materialize for this SFI. This should be
        the same value used in the prompt and validator for the current LLM batch.
    sfi
        The StandardsFrameworkItem representing the expectation for which to create LCs.
    skills
        The list of validated atomic skills dictionaries for this SFI, each containing
        at least a "description" field, and optionally "rationale". These are the
        outputs from the LLM-based atomic skills decomposition and validation process,
        and are assumed to be pre-validated according to the specified criteria (e.g.,
        allowed SFI UUIDs, min/max skills per SFI, presence of rationale if required).
        Each skill will be transformed into a LearningComponent entity, with
        deterministic UUID generation based on the split policy, supporting SFI UUID,
        skill position, and stable hash of the skill description.

    Returns
    -------
    list[LearningComponent]
        A list of LearningComponent entities created from the provided atomic skills
        for the given expectation SFI. Each LC will have a deterministic UUID based on
        the doc_key, split policy, SFI UUID, skill index, and a hash of the skill
        description to ensure stable IDs across runs.
    """

    norm_skills: list[tuple[str, str]] = []
    policy = "llm_atomic_skills"
    provenance = _build_provenance_from_sfi(sfi.metadata or {})

    for skill in skills:
        description = normalize_ws(str(skill.get("description") or ""))
        rationale = (
            normalize_ws(str(skill.get("rationale") or ""))
            if skill.get("rationale") is not None
            else ""
        )

        if description:
            norm_skills.append((description, rationale))

    if not norm_skills:
        return []

    # Preserve the validated LLM/source order. The prompt asks the model to decompose
    # each source expectation into meaningful atomic skills, so the returned order is
    # the best available semantic order. We still deduplicate deterministically, but we
    # do NOT sort by hash before truncation; otherwise `lc_max_splits_per_standard`
    # would keep an arbitrary hash-ordered subset rather than the first N skills in the
    # model/source order.
    deduped: list[tuple[str, str]] = []
    seen_desc: set[str] = set()

    for description, rationale in norm_skills:
        canonical_desc = canonicalize_stable_text(description)

        if canonical_desc in seen_desc:
            continue

        deduped.append((description, rationale))
        seen_desc.add(canonical_desc)

    min_splits = int(config.lc_atomic_skills_min_per_sfi)

    if deduped and len(deduped) < min_splits:
        logger.warning(
            f"SFI {sfi.case_identifier_uuid}: atomic-skills output passed validation "
            f"with {len(norm_skills)} normalized skill(s), but post-validation "
            f"deduplication reduced this to {len(deduped)} LC(s), below "
            f"lc_atomic_skills_min_per_sfi={min_splits}. "
            f"Continuing with {len(deduped)} emitted LC(s)."
        )

    truncated = False

    if len(deduped) > max_splits:
        deduped = deduped[:max_splits]
        truncated = True

    lcs: list[LearningComponent] = []

    for i, (description, rationale) in enumerate(deduped):
        lcs.append(
            _build_single_lc(
                config=config,
                description=description,
                doc_key=doc_key,
                extra_metadata={"llm_rationale": rationale} if rationale else None,
                fw_metadata=fw_metadata,
                id_source_kind="llm_atomic_skills.description",
                policy=policy,
                provenance=provenance,
                sfi=sfi,
                split_display_text=description,
                split_id_text=description,
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

    date_created, date_modified = _resolve_derived_timestamps(sfi)
    return Relationship(
        attribution_statement=str(fw_metadata["attribution_statement"]),
        author=str(fw_metadata["author"]),
        date_created=date_created,
        date_modified=date_modified,
        description="LearningComponent supports StandardsFrameworkItem",
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
        relationship_type=SUPPORTS,
        source_entity="LearningComponent",
        source_entity_key="identifier",
        source_entity_value=str(lc.identifier),
        target_entity="StandardsFrameworkItem",
        target_entity_key="case_identifier_uuid",
        target_entity_value=str(sfi.case_identifier_uuid),
    )


def _extract_aux_statements(metadata: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Extract and trim up to 10 auxiliary statements from an SFI's metadata.

    Parameters
    ----------
    metadata
        The metadata dictionary from a StandardsFrameworkItem.

    Returns
    -------
    list[dict[str, Any]] | None
        A list of trimmed auxiliary statement dictionaries, or None if no valid
        statements exist or the input is not a list.
    """

    aux = metadata.get("aux_statements")

    if not isinstance(aux, list):
        return None

    aux_items: list[dict[str, Any]] = []

    for a in aux[:10]:
        if not isinstance(a, dict):
            continue

        role = normalize_ws(a["role"])
        assert role, f"{a = }"
        trimmed_aux_text, aux_text_was_truncated, aux_text_original_length = (
            _trim_text_with_debug(max_chars=400, s=a.get("text") or "")
        )

        if not trimmed_aux_text:
            continue

        aux_item: dict[str, Any] = {"role": role, "text": trimmed_aux_text}

        if aux_text_was_truncated:
            aux_item["text_truncated"] = True
            aux_item["text_original_length"] = aux_text_original_length
            aux_item["text_max_chars"] = 400

        aux_items.append(aux_item)

    return aux_items if aux_items else None


def _extract_topic_context(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Extract and clean the topic context from an SFI's metadata.

    Parameters
    ----------
    metadata
        The metadata dictionary from a StandardsFrameworkItem.

    Returns
    -------
    dict[str, Any] | None
        A dictionary containing cleaned topic context parts, or None if no valid
        context data adds benefit.
    """

    pc = metadata.get("progression_context") or {}
    cleaned_topic_path_parts: list[dict[str, Any]] = []

    if isinstance(pc.get("topic_path_parts"), list):
        for part in pc["topic_path_parts"]:
            if not isinstance(part, dict):
                continue

            role = normalize_ws(str(part.get("role") or ""))
            label = normalize_ws(str(part.get("label") or ""))

            if role and label:
                cleaned_topic_path_parts.append({"role": role, "label": label})

    topic_ctx = {
        "grade_key": normalize_ws(str(pc.get("grade_key") or "")) or None,
        "stage_key": normalize_ws(str(pc.get("stage_key") or "")) or None,
        "topic_path_parts": cleaned_topic_path_parts,
    }

    # Only return `topic_context` when at least one cleaned value is present; empty
    # strings/lists add prompt tokens for no benefit.
    if any(value not in (None, "", [], {}) for value in topic_ctx.values()):
        return topic_ctx

    return None


def _finalize_lc_export(
    *,
    config: CreateKGConfig,
    ctx: ExportContext,
    kg_dirs: KGDirs,
    lc_stats: dict[str, Any],
    lcs: list[LearningComponent],
    rels: list[Relationship],
    valid_sfi_case_uuids: set[str],
) -> LearningComponentsExport:
    """Verify, persist, and wrap LearningComponents export artifacts.

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
    valid_sfi_case_uuids
        The set of valid StandardsFrameworkItem CASE UUIDs that supports edges may
        target.

    Returns
    -------
    LearningComponentsExport
        The wrapped export object.

    Raises
    ------
    ValueError
        If integrity checks fail.
    """

    source_eligibility_report = lc_stats.pop("_source_eligibility_report", None)

    _validate_lc_export_integrity(
        lcs=lcs, rels=rels, valid_sfi_case_uuids=valid_sfi_case_uuids
    )

    sorted_lcs, sorted_rels = _sort_lc_export_artifacts(lcs=lcs, rels=rels)

    write_to_json(
        fp=kg_dirs.learning_components / "learning_components.json",
        json_info=[lc.model_dump(mode="json") for lc in sorted_lcs],
    )
    write_to_json(
        fp=kg_dirs.learning_components
        / "learning_components_supports_relationships.json",
        json_info=[r.model_dump(mode="json") for r in sorted_rels],
    )
    write_to_json(
        fp=kg_dirs.learning_components / "learning_components_stats.json",
        json_info=lc_stats,
    )
    write_to_json(
        fp=kg_dirs.learning_components / "learning_components_source_level_report.json",
        json_info=_build_lc_source_level_quality_report(sorted_lcs),
    )

    if source_eligibility_report is not None:
        write_to_json(
            fp=kg_dirs.learning_components
            / "learning_components_source_eligibility_report.json",
            json_info=source_eligibility_report,
        )

    write_to_json(
        fp=kg_dirs.learning_components / "learning_components_kg.json",
        json_info=_build_lc_graph_bundle(
            doc_key=ctx.doc_key,
            export_dialect=config.as_export_dialect,
            learning_components=sorted_lcs,
            supports_relationships=sorted_rels,
        ),
    )

    export = LearningComponentsExport(
        lc_stats=lc_stats,
        learning_components=sorted_lcs,
        supports_relationships=sorted_rels,
    )

    logger.success(
        f"Exported Learning Components KG ({lc_stats['split_policy']}): "
        f"{len(export.learning_components)} learning components, "
        f"{len(export.supports_relationships)} `supports` relationships"
    )

    return export


def _first_page_index(metadata: dict[str, Any]) -> float:
    """Return the first source page index for an SFI, or infinity when unavailable.

    Parameters
    ----------
    metadata
        The metadata dictionary from a StandardsFrameworkItem.

    Returns
    -------
    float
        The first page index if available, or infinity if not available or invalid.
    """

    page_indices = metadata.get("page_indices") or []

    if isinstance(page_indices, list) and page_indices:
        return _sort_number(value=page_indices[0])

    return float("inf")


def _format_language_for_prompt(tag: str | None) -> str:
    """Format a BCP-47 language tag as a human-friendly language name for prompts. We
    still attempt to resolve unknown codes via `pycountry`.

    Parameters
    ----------
    tag
        A BCP-47 language tag like "en", "fr", "sw", or "en-US". May be None.

    Returns
    -------
    str
        A human-readable language name.
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

    return name


def _handle_atomic_skills_fallback(
    *,
    batch: list[StandardsFrameworkItem],
    config: CreateKGConfig,
    ctx: ExportContext,
    current_batch_num: int,
    fw_metadata: dict[str, Any],
) -> tuple[list[LearningComponent], list[Relationship], dict[int, int], list[str]]:
    """Handle fallback by creating 1-to-1 LCs for each provided SFI.

    This function is commonly used for uncached SFIs whose atomic-skills inference
    failed. Cached SFIs from the same processing batch may still be handled through the
    atomic-skills success path. Every provided SFI is routed through the deterministic
    `1_to_1` LC path. Most SFIs will therefore produce exactly one LearningComponent,
    but SFIs whose usable text sources are both empty can still produce zero
    LearningComponents and thus no `supports` relationship.

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
    tuple[list[LearningComponent], list[Relationship], dict[int, int], list[str]]
        A tuple containing the fallback LCs, supports relationships, split statistics,
        and the SFI UUIDs that still produced zero LearningComponents during fallback.
    """

    lcs: list[LearningComponent] = []
    rels: list[Relationship] = []
    splits: defaultdict[int, int] = defaultdict(int)
    zero_lc_sfi_uuids: list[str] = []

    for sfi_idx, sfi in enumerate(batch, start=1):
        sfi_uuid_str = str(sfi.case_identifier_uuid)

        logger.debug(
            f"Batch {current_batch_num} Fallback: Processing SFI "
            f"{sfi_uuid_str} ({sfi_idx}/{len(batch)})..."
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

        if not created:
            zero_lc_sfi_uuids.append(sfi_uuid_str)
            logger.warning(
                f"Batch {current_batch_num} Fallback: SFI {sfi_uuid_str} still "
                f"produced 0 LearningComponents under 1_to_1 fallback; no "
                f"`supports` edge will be emitted."
            )
            continue

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

    return lcs, rels, dict(splits), zero_lc_sfi_uuids


def _handle_atomic_skills_success(
    *,
    batch: list[StandardsFrameworkItem],
    batch_debug: dict[str, Any],
    config: CreateKGConfig,
    ctx: ExportContext,
    current_batch_num: int,
    fw_metadata: dict[str, Any],
    max_splits: int,
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
    max_splits
        The maximum number of atomic skills to materialize per SFI.
    skills_by_sfi
        A dictionary mapping SFI UUIDs to their generated atomic skills.

    Returns
    -------
    tuple[list[LearningComponent], list[Relationship], dict[int, int], list[str]]
        A tuple containing the generated LCs, supports relationships, split statistics,
        and any fallback SFI UUIDs triggered during empty post-processing output.

    Raises
    ------
    ValueError
        If an SFI UUID from the batch is missing in the skills_by_sfi mapping,
        indicating a mismatch between the parsed LLM response and the expected SFIs.
        This should not occur if the validation step passed, and would suggest a
        critical bug in the data handling logic.
    """

    fallback_uuids: list[str] = []
    lcs: list[LearningComponent] = []
    rels: list[Relationship] = []
    splits: defaultdict[int, int] = defaultdict(int)

    for sfi_idx, sfi in enumerate(batch, start=1):
        sfi_uuid_str = str(sfi.case_identifier_uuid)

        logger.debug(
            f"Batch {current_batch_num}: Processing SFI "
            f"{sfi_uuid_str} ({sfi_idx}/{len(batch)})..."
        )

        skills = skills_by_sfi.get(sfi_uuid_str, [])

        if not skills:
            raise ValueError(
                f"ERROR: SFI {sfi_uuid_str} passed validation but has no skills in "
                f"`skills_by_sfi`. This indicates a mapping error between `parsed_dict` "
                f"and `skills_by_sfi`."
            )

        created = _create_lcs_from_atomic_skills(
            config=config,
            doc_key=ctx.doc_key,
            fw_metadata=fw_metadata,
            max_splits=max_splits,
            sfi=sfi,
            skills=skills,
        )

        if not created:
            logger.warning(
                f"Atomic skills for SFI {sfi_uuid_str} produced 0 LCs "
                f"after normalization/dedup; falling back to `1_to_1` policy."
            )
            created = _create_lcs_for_expectation(
                config=config,
                doc_key=ctx.doc_key,
                fw_metadata=fw_metadata,
                id_source_kind_override="fallback_1_to_1",
                policy_override="1_to_1",
                sfi=sfi,
            )
            previous_source = batch_debug["response_source_by_sfi_uuid"].get(
                sfi_uuid_str, "unknown"
            )
            batch_debug["response_source_by_sfi_uuid"][
                sfi_uuid_str
            ] = f"{previous_source}_then_fallback_1_to_1"
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

    metadata = sfi.metadata or {}
    id_source = normalize_ws(metadata.get("normalized_text") or "")
    display = normalize_ws(sfi.description or "")
    return bool(id_source or display)


def _lc_source_decision_example(
    decision: LearningComponentsSourceDecision,
) -> dict[str, Any]:
    """Return a compact, JSON-serializable example for an eligibility decision.

    Parameters
    ----------
    decision
        The LearningComponentsSourceDecision to summarize.

    Returns
    -------
    dict[str, Any]
        A dictionary containing key fields from the decision, suitable for inclusion in
        a JSON report. This includes identifiers, canonical path information, statement
        type, role, source label, eligibility result, reasons, and a description
        preview for context.
    """

    fields = decision.fields
    return {
        "canonical_node_id": fields.get("canonical_node_id"),
        "canonical_path_key": fields.get("canonical_path_key"),
        "case_identifier_uuid": fields.get("case_identifier_uuid"),
        "description_preview": fields.get("description_preview"),
        "eligible": decision.eligible,
        "path_pattern": fields.get("path_pattern"),
        "reasons": decision.reasons,
        "role": fields.get("role"),
        "source_label": fields.get("source_label"),
        "statement_type": fields.get("statement_type"),
    }


def _lc_source_fields(sfi: StandardsFrameworkItem) -> dict[str, Any]:
    """Extract the SFI fields used by LC source eligibility filtering and reporting.

    Parameters
    ----------
    sfi
        The StandardsFrameworkItem to extract fields from.

    Returns
    -------
    dict[str, Any]
        A dictionary containing the extracted and normalized fields relevant for LC
        source eligibility and reporting. This includes identifiers, canonical path
        information, statement type, role, source label, and a trimmed description
        preview for context.
    """

    metadata = sfi.metadata or {}
    canonical_path_key = normalize_ws(str(metadata.get("canonical_path_key") or ""))
    path_pattern = _path_pattern_from_canonical_path_key(canonical_path_key)
    return {
        "canonical_node_id": metadata.get("canonical_node_id"),
        "canonical_path_key": canonical_path_key,
        "case_identifier_uuid": str(sfi.case_identifier_uuid),
        "description_preview": _trim_text(max_chars=240, s=sfi.description or ""),
        "normalized_statement_type": sfi.normalized_statement_type,
        "path_depth": _lc_source_path_depth(canonical_path_key),
        "path_pattern": path_pattern,
        "role": normalize_ws(str(metadata.get("role") or "")),
        "source_label": normalize_ws(str(metadata.get("source_label") or "")),
        "statement_code": sfi.statement_code,
        "statement_type": normalize_ws(str(sfi.statement_type or "")),
    }


def _lc_source_level_record(lc: LearningComponent) -> dict[str, str]:
    """Extract the source-level fields used by the LC source-level QA report.

    Parameters
    ----------
    lc
        The LearningComponent to summarize.

    Returns
    -------
    dict[str, str]
        A small normalized record with supporting SFI UUID, source label, statement
        type, canonical path key, and role-only path pattern.
    """

    metadata = lc.metadata or {}
    canonical_path_key = metadata.get("supporting_sfi_canonical_path_key")
    return {
        "lc_uuid": str(lc.identifier),
        "supporting_sfi_canonical_path_key": _clean_lc_source_report_value(
            canonical_path_key
        ),
        "supporting_sfi_case_uuid": _clean_lc_source_report_value(
            metadata.get("supporting_sfi_case_uuid")
        ),
        "supporting_sfi_path_pattern": _path_pattern_from_canonical_path_key(
            canonical_path_key
        ),
        "supporting_sfi_source_label": _clean_lc_source_report_value(
            metadata.get("supporting_sfi_source_label")
        ),
        "supporting_sfi_statement_type": _clean_lc_source_report_value(
            metadata.get("supporting_sfi_statement_type")
        ),
    }


def _lc_source_path_depth(path_key: str) -> int:
    """Return the number of non-empty path pieces in a canonical path key.

    Parameters
    ----------
    path_key
        The canonical path key string, typically from SFI metadata.

    Returns
    -------
    int
        The count of non-empty path pieces, which indicates the depth of the SFI in the
        curriculum hierarchy. A path key of "" or None is considered to have depth 0.
    """

    if not path_key:
        return 0

    return len([part for part in path_key.split("/") if normalize_ws(part)])


def _lc_source_path_matches_any(
    *, path_key: str, path_pattern: str, patterns: Iterable[str] | None
) -> bool:
    """Return True if any configured path pattern matches the path key or role pattern.

    Pattern semantics are intentionally simple and curriculum-agnostic:

    - `re:<expr>` runs a case-insensitive regular expression.
    - Glob patterns containing `*`, `?`, or `[` use fnmatch-style matching.
    - All other patterns are case-insensitive substring matches.

    Both the full canonical path key and the role-only path pattern are tested so
    reviewers can filter either exact curriculum paths or broad structural shapes.

    Parameters
    ----------
    path_key
        The canonical path key from SFI metadata.
    path_pattern
        The role-only path pattern derived from the canonical path key.
    patterns
        An iterable of path patterns to match against, which may be None or empty. If
        None or empty, this function returns False (no matches).

    Returns
    -------
    bool
        True if any pattern matches the path key or role pattern, False otherwise.
    """

    cleaned_patterns = [normalize_ws(str(p or "")) for p in (patterns or [])]
    cleaned_patterns = [p for p in cleaned_patterns if p]

    if not cleaned_patterns:
        return False

    candidates = [
        _clean_lc_source_filter_value(path_key),
        _clean_lc_source_filter_value(path_pattern),
    ]

    for raw_pattern in cleaned_patterns:
        pattern = _clean_lc_source_filter_value(raw_pattern)

        if pattern.startswith("re:"):
            regex = raw_pattern[3:].strip()

            if not regex:
                continue

            for candidate in (path_key, path_pattern):
                if re.search(regex, candidate or "", flags=re.IGNORECASE):
                    return True

            continue

        if any(ch in pattern for ch in "*?["):
            if any(fnmatchcase(candidate, pattern) for candidate in candidates):
                return True

            continue

        if any(pattern in candidate for candidate in candidates):
            return True

    return False


def _natural_sort_key(value: Any) -> tuple[tuple[int, int | str], ...]:
    """Build a deterministic natural-sort key for curriculum labels/paths.

    Curriculum path fragments often contain embedded numbers such as `week:10`,
    `palier-2`, or `grade 3`. A plain lexicographic sort would place `week:10` before
    `week:2`, so this helper splits digit runs into integers while keeping non-numeric
    text as normalized lowercase strings.

    Parameters
    ----------
    value
        Any label/path value to normalize for natural sorting.

    Returns
    -------
    tuple[tuple[int, int | str], ...]
        A stable comparable key where numeric runs sort numerically and text runs sort
        lexicographically.
    """

    text = normalize_ws(str(value or "")).casefold()

    if not text:
        return ()

    key_parts: list[tuple[int, int | str]] = []

    for part in re.split(r"(\d+)", text):
        if not part:
            continue

        if part.isdigit():
            key_parts.append((0, int(part)))
        else:
            key_parts.append((1, part))

    return tuple(key_parts)


def _normalize_atomic_skills_cache_aux_statements(value: Any) -> list[dict[str, str]]:
    """Normalize meaning-bearing auxiliary statements for the cache key.

    Only the auxiliary statement role and text are retained. Debug-only truncation
    fields such as `text_truncated`, `text_original_length`, and `text_max_chars` are
    intentionally excluded because they do not add semantic meaning beyond the text
    actually shown to the LLM.

    Parameters
    ----------
    value
        The raw value of the `aux_statements` field from SFI metadata, which is
        expected to be a list of dictionaries, each containing at least a "role" and
        "text" field.

    Returns
    -------
    list[dict[str, str]]
        A list of dictionaries with normalized "role" and "text" fields, suitable for
        inclusion in the atomic skills cache key. Non-string values are coerced to
        strings, and None values become empty strings. Invalid entries are skipped.
    """

    if not isinstance(value, list):
        return []

    normalized: list[dict[str, str]] = []

    for item in value:
        if not isinstance(item, dict):
            continue

        role = _normalize_atomic_skills_cache_text(item["role"])
        text = _normalize_atomic_skills_cache_text(item.get("text"))
        normalized.append({"role": role, "text": text})

    return normalized


def _normalize_atomic_skills_cache_text(value: Any) -> str:
    """Normalize a prompt value for atomic-skills cache-key construction.

    This uses the same stable text canonicalization policy as the rest of the KG export
    path so semantically equivalent whitespace/case/Unicode variants collapse to the
    same cache-key value.

    Parameters
    ----------
    value
        Any value to be normalized as text for cache key construction. Non-string
        values will be coerced to strings, and None will become an empty string.

    Returns
    -------
    str
        The normalized text string, suitable for use in cache key construction.
    """

    return canonicalize_stable_text(str(value or ""))


def _normalize_atomic_skills_cache_topic_context(value: Any) -> dict[str, Any]:
    """Normalize meaning-bearing topic context for the atomic-skills cache key.

    Only semantic fields that may affect decomposition are retained. Provenance,
    ordering, and debug-only fields are intentionally excluded.

    Parameters
    ----------
    value
        The raw value of the prompt item's `topic_context` field, which is expected to
        be a dictionary potentially containing "grade_key", "stage_key", and
        "topic_path_parts".

    Returns
    -------
    dict[str, Any]
        A dictionary containing normalized topic context fields relevant for cache key
        construction. Non-string values are coerced to strings, and None values become
        empty strings. Invalid entries are skipped, and only fields that add semantic
        meaning are retained to ensure that the cache key reflects the information that
        may influence the LLM's decomposition output.
    """

    if not isinstance(value, dict):
        return {}

    normalized: dict[str, Any] = {}
    grade_key = _normalize_atomic_skills_cache_text(value.get("grade_key"))
    stage_key = _normalize_atomic_skills_cache_text(value.get("stage_key"))

    if grade_key:
        normalized["grade_key"] = grade_key

    if stage_key:
        normalized["stage_key"] = stage_key

    topic_path_parts: list[dict[str, str]] = []

    if isinstance(value.get("topic_path_parts"), list):
        for part in value["topic_path_parts"]:
            if not isinstance(part, dict):
                continue

            role = _normalize_atomic_skills_cache_text(part.get("role"))
            label = _normalize_atomic_skills_cache_text(part.get("label"))

            if role or label:
                topic_path_parts.append({"role": role, "label": label})

    if topic_path_parts:
        normalized["topic_path_parts"] = topic_path_parts

    return normalized


def _pair_id_and_display_parts(
    *, display_parts: list[str], id_parts: list[str]
) -> list[tuple[str, str]]:
    """Pair ID parts with display parts deterministically.

    Parameters
    ----------
    display_parts
        Candidate description parts for emitted LearningComponents.
    id_parts
        Canonical ID parts used for hashing and UUID generation.

    Returns
    -------
    list[tuple[str, str]]
        Ordered `(id_part, display_part)` pairs. If the split counts do not match,
        the ID parts are reused as display parts to preserve deterministic pairing.
    """

    if len(display_parts) == len(id_parts):
        return list(zip(id_parts, display_parts))

    return [(part, part) for part in id_parts]


def _partition_batch_and_init_debug(
    *,
    atomic_skills_cache: dict[str, list[dict[str, Any]]],
    batch: list[StandardsFrameworkItem],
    batch_index: int,
    prompt_items: list[dict[str, Any]],
) -> tuple[
    dict[str, Any],
    list[StandardsFrameworkItem],
    dict[str, list[dict[str, Any]]],
    list[str],
    dict[str, str],
    dict[str, list[dict[str, Any]]],
    list[StandardsFrameworkItem],
    list[dict[str, Any]],
]:
    """Partition batch items by cache presence and initialize debug payload.

    Parameters
    ----------
    atomic_skills_cache
        The current run's cache mapping prompt keys to validated skill dictionaries.
    batch
        The list of StandardsFrameworkItem items in the current batch.
    batch_index
        The normalized batch index (0-based) for debugging.
    prompt_items
        The generated prompt items corresponding to the SFIs in the batch.

    Returns
    -------
    tuple
        A tuple containing the initialized state variables needed for batch processing:
            - batch_debug
            - cached_batch
            - cached_skills_by_sfi
            - fallback_sfis_total
            - key_by_sfi_uuid
            - skills_by_sfi
            - uncached_batch
            - uncached_prompt_items
    """

    key_by_sfi_uuid: dict[str, str] = {}
    cached_batch: list[StandardsFrameworkItem] = []
    cached_skills_by_sfi: dict[str, list[dict[str, Any]]] = {}
    uncached_batch: list[StandardsFrameworkItem] = []
    uncached_prompt_items: list[dict[str, Any]] = []

    for sfi, prompt_item in zip(batch, prompt_items):
        sfi_uuid = str(sfi.case_identifier_uuid)
        cache_key = _atomic_skills_cache_key_from_prompt_item(prompt_item)
        key_by_sfi_uuid[sfi_uuid] = cache_key

        if cache_key in atomic_skills_cache:
            cached_batch.append(sfi)
            cached_skills_by_sfi[sfi_uuid] = deepcopy(atomic_skills_cache[cache_key])
        else:
            uncached_batch.append(sfi)
            uncached_prompt_items.append(prompt_item)

    cache_hit_uuids = [str(sfi.case_identifier_uuid) for sfi in cached_batch]
    cache_miss_uuids = [str(sfi.case_identifier_uuid) for sfi in uncached_batch]
    llm_called = bool(uncached_batch)
    batch_debug: dict[str, Any] = {
        "atomic_skills_cache": {
            "hit_sfi_uuids": cache_hit_uuids,
            "hits": len(cache_hit_uuids),
            "key_by_sfi_uuid": key_by_sfi_uuid,
            "miss_sfi_uuids": cache_miss_uuids,
            "misses": len(cache_miss_uuids),
        },
        "batch_index": batch_index,
        "error": None,
        "fallback_sfi_uuids": [],
        "input_items": prompt_items,
        "llm_called": llm_called,
        "llm_input_items": uncached_prompt_items,
        "llm_response": None,
        "response": None,
        "response_source_by_sfi_uuid": {
            **{sfi_uuid: "cache" for sfi_uuid in cache_hit_uuids},
            **{sfi_uuid: "pending_llm" for sfi_uuid in cache_miss_uuids},
        },
        "zero_lc_fallback_sfi_uuids": [],
    }
    fallback_sfis_total: list[str] = []
    skills_by_sfi: dict[str, list[dict[str, Any]]] = dict(cached_skills_by_sfi)
    return (
        batch_debug,
        cached_batch,
        cached_skills_by_sfi,
        fallback_sfis_total,
        key_by_sfi_uuid,
        skills_by_sfi,
        uncached_batch,
        uncached_prompt_items,
    )


def _path_pattern_from_canonical_path_key(path_key: Any) -> str:
    """Convert a canonical path key into a role-only path pattern. For example,
    `section:foo/stage:bar/expectation::abc` becomes `section/stage/expectation`.

    This intentionally removes labels, codes, and hashes so the report stays general
    across curricula and highlights which *level* of the hierarchy produced LCs.

    Parameters
    ----------
    path_key
        Canonical SFI path key, usually from
        `LearningComponent.metadata["supporting_sfi_canonical_path_key"]`.

    Returns
    -------
    str
        Slash-delimited role pattern, or "(missing)" if no usable path is present.
    """

    path_key_str = normalize_ws(str(path_key or ""))

    if not path_key_str:
        return "(missing)"

    roles: list[str] = []

    for raw_part in path_key_str.split("/"):
        part = normalize_ws(raw_part)

        if not part:
            continue

        role = normalize_ws(part.split(":", 1)[0] if ":" in part else part)

        if role:
            roles.append(role)

    return "/".join(roles) if roles else "(missing)"


def _process_atomic_skills_batch(
    *,
    atomic_skills_cache: dict[str, list[dict[str, Any]]],
    batch: list[StandardsFrameworkItem],
    batch_index: int,
    config: CreateKGConfig,
    ctx: ExportContext,
    current_batch_num: int,
    fw_metadata: dict[str, Any],
    max_splits: int,
    total_batches: int,
    usage_tracker: KGUsageTracker,
) -> tuple[
    list[LearningComponent],
    list[Relationship],
    dict[int, int],
    dict[str, Any],
    list[str],
]:
    """Process a single batch of SFIs via LLM inference to create LCs.

    Repeated prompt items are served from a deterministic within-run cache keyed by
    semantic prompt fields: normalized `display_text`, `language_instruction`,
    `statement_type`, `source_label` when present, `topic_context`, and
    `aux_statements`. Cache misses are sent to the LLM; cache hits reuse the previously
    validated atomic skills and are materialized for the current SFI without another
    model call.

    Parameters
    ----------
    atomic_skills_cache
        Within-run cache mapping deterministic prompt keys to validated atomic-skill
        dictionaries. The cache is intentionally not persisted across runs because its
        values are model outputs tied to the current prompt/schema policy.
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
    usage_tracker
        Tracker to accumulate token usage across KG generation and validation calls.

    Returns
    -------
    tuple[
        list[LearningComponent],
        list[Relationship],
        dict[int, int],
        dict[str, Any],
        list[str],
    ]
        A tuple containing the LCs, relationships, splits distribution, debug
        information, and fallback UUIDs for this batch.
    """

    logger.info(
        f"Processing batch {current_batch_num}/{total_batches} ({len(batch)} SFIs)..."
    )

    prompt_items = [
        _build_single_prompt_item(config=config, fw_metadata=fw_metadata, sfi=sfi)
        for sfi in batch
    ]

    (
        batch_debug,
        cached_batch,
        cached_skills_by_sfi,
        fallback_sfis_total,
        key_by_sfi_uuid,
        skills_by_sfi,
        uncached_batch,
        uncached_prompt_items,
    ) = _partition_batch_and_init_debug(
        atomic_skills_cache=atomic_skills_cache,
        batch=batch,
        batch_index=batch_index,
        prompt_items=prompt_items,
    )

    if uncached_batch:
        prompt = decompose_atomic_skills(
            default_language_instruction=_DEFAULT_LC_PROMPT_LANGUAGE_INSTRUCTION,
            items=uncached_prompt_items,
            max_per_sfi=max_splits,
            min_per_sfi=int(config.lc_atomic_skills_min_per_sfi),
            require_rationale=bool(config.lc_atomic_skills_require_rationale),
        )
        allowed = {UUID(str(s.case_identifier_uuid)) for s in uncached_batch}

        try:
            parsed = infer_atomic_skills(
                instructions=prompt.system_message,
                usage_tracker=usage_tracker,
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
            batch_debug["llm_response"] = parsed_dict

            for it in parsed_dict.get("items", []):
                sfi_uuid = str(it.get("sfi_uuid"))
                skills = list(it.get("skills") or [])
                skills_by_sfi[sfi_uuid] = skills
                batch_debug["response_source_by_sfi_uuid"][sfi_uuid] = "llm"

                if retrieved_key := key_by_sfi_uuid.get(sfi_uuid):
                    atomic_skills_cache[retrieved_key] = deepcopy(skills)
        except Exception as e:  # pylint: disable=broad-except
            # Reconstruct the missing UUIDs from `uncached_batch`.
            cache_miss_uuids = [str(sfi.case_identifier_uuid) for sfi in uncached_batch]

            batch_debug["error"] = f"{e.__class__.__name__}: {e}"
            batch_debug["fallback_sfi_uuids"] = cache_miss_uuids

            for sfi_uuid in cache_miss_uuids:
                batch_debug["response_source_by_sfi_uuid"][sfi_uuid] = "fallback_1_to_1"

            fallback_sfis_total.extend(cache_miss_uuids)

            logger.warning(
                f"Atomic skills batch {current_batch_num} failed "
                f"({e.__class__.__name__}); all {len(uncached_batch)} uncached "
                f"SFI(s) in this batch fall back to 1_to_1. Cached SFI(s), if any, "
                f"will still reuse cached atomic skills. Fallback SFI UUIDs: "
                f"{batch_debug['fallback_sfi_uuids']}"
            )

            lcs: list[LearningComponent] = []
            rels: list[Relationship] = []
            splits: dict[int, int] = defaultdict(int)

            if cached_batch:
                cached_lcs, cached_rels, cached_splits, cached_fallbacks = (
                    _handle_atomic_skills_success(
                        batch=cached_batch,
                        batch_debug=batch_debug,
                        config=config,
                        ctx=ctx,
                        current_batch_num=current_batch_num,
                        fw_metadata=fw_metadata,
                        max_splits=max_splits,
                        skills_by_sfi=cached_skills_by_sfi,
                    )
                )
                lcs.extend(cached_lcs)
                rels.extend(cached_rels)
                fallback_sfis_total.extend(cached_fallbacks)

                for split_count, occurrences in cached_splits.items():
                    splits[split_count] += occurrences

            (
                fallback_lcs,
                fallback_rels,
                fallback_splits,
                zero_lc_fallback_sfi_uuids,
            ) = _handle_atomic_skills_fallback(
                batch=uncached_batch,
                config=config,
                ctx=ctx,
                current_batch_num=current_batch_num,
                fw_metadata=fw_metadata,
            )
            lcs.extend(fallback_lcs)
            rels.extend(fallback_rels)

            for split_count, occurrences in fallback_splits.items():
                splits[split_count] += occurrences

            batch_debug["response"] = _build_atomic_skills_response_dict(
                skills_by_sfi=cached_skills_by_sfi, sfis=cached_batch
            )
            batch_debug["zero_lc_fallback_sfi_uuids"] = zero_lc_fallback_sfi_uuids
            return lcs, rels, dict(splits), batch_debug, fallback_sfis_total

    batch_debug["response"] = _build_atomic_skills_response_dict(
        skills_by_sfi=skills_by_sfi, sfis=batch
    )

    lcs, rels, splits, fallback_uuids = _handle_atomic_skills_success(
        batch=batch,
        batch_debug=batch_debug,
        config=config,
        ctx=ctx,
        current_batch_num=current_batch_num,
        fw_metadata=fw_metadata,
        max_splits=max_splits,
        skills_by_sfi=skills_by_sfi,
    )
    fallback_sfis_total.extend(fallback_uuids)
    return lcs, rels, splits, batch_debug, fallback_sfis_total


def _resolve_derived_timestamps(
    sfi: StandardsFrameworkItem,
) -> tuple[str | None, str | None]:
    """Resolve stable timestamps for derived LC entities and relationships.

    Derived LearningComponents and `supports` relationships inherit timestamps from the
    supporting StandardsFrameworkItem so reruns remain stable and traceable to the
    underlying academic-standards export.

    Parameters
    ----------
    sfi
        The supporting StandardsFrameworkItem.

    Returns
    -------
    tuple[str | None, str | None]
        `(date_created, date_modified)` for the derived object. If the source SFI does
        not provide `date_modified`, we fall back to `date_created` so derived exports
        populate both fields deterministically whenever a source creation timestamp is
        available.
    """

    date_created = sfi.date_created
    date_modified = sfi.date_modified or date_created
    return date_created, date_modified


def _resolve_lc_output_language_tag(
    *, config: CreateKGConfig, fw_metadata: dict[str, Any], sfi: StandardsFrameworkItem
) -> str:
    """Resolve the emitted `in_language` tag for a derived LearningComponent.

    Parameters
    ----------
    config
        KG export configuration containing LC output-language policy.
    fw_metadata
        Framework metadata dict used for fallback language values.
    sfi
        The supporting StandardsFrameworkItem.

    Returns
    -------
    str
        The BCP-47 tag to write onto the derived LearningComponent.
    """

    policy = normalize_ws(config.lc_output_language_policy or "source").lower()

    if policy == "english":
        return "en"

    if policy == "explicit_tag":
        return normalize_ws(config.lc_output_language_tag or "en") or "en"

    return normalize_ws(sfi.in_language or fw_metadata["in_language"] or "en") or "en"


def _resolve_lc_prompt_language_instruction(
    *, config: CreateKGConfig, fw_metadata: dict[str, Any], sfi: StandardsFrameworkItem
) -> str:
    """Resolve the output-language instruction for LC prompts.

    Parameters
    ----------
    config
        KG export configuration containing LC output-language policy.
    fw_metadata
        Framework metadata dict used for fallback language values.
    sfi
        The SFI whose language policy should drive the prompt instruction.

    Returns
    -------
    str
        Prompt instruction describing what language the LLM should use for emitted
        atomic skill descriptions for this specific SFI.
    """

    policy = normalize_ws(str(config.lc_output_language_policy or "source")).lower()

    if policy == "english":
        return "English"

    if policy == "explicit_tag":
        return _format_language_for_prompt(tag=config.lc_output_language_tag or "en")

    resolved_tag = _resolve_lc_output_language_tag(
        config=config, fw_metadata=fw_metadata, sfi=sfi
    )
    primary = resolved_tag.replace("_", "-").split("-")[0].lower().strip()

    if primary == "mul":
        return (
            "the same language(s) as the input text; if the source restates the same "
            "competency in multiple languages, treat it as one competency rather than "
            "separate skills"
        )

    if primary == "und":
        return "the same language as the input text"

    return _format_language_for_prompt(tag=resolved_tag)


def _resolve_lc_text_sources(
    *, id_source_kind_override: str | None = None, sfi: StandardsFrameworkItem
) -> tuple[str, str, str, dict[str, Any]]:
    """Resolve the text channels used for LC display and deterministic IDs.

    Parameters
    ----------
    id_source_kind_override
        Optional override label for the ID-source provenance field.
    sfi
        The supporting StandardsFrameworkItem.

    Returns
    -------
    tuple[str, str, str, dict[str, Any]]
        A tuple of `(display_text, id_source_text, id_source_kind, metadata)` where:
            - `display_text` comes from the exported SFI description.
            - `id_source_text` prefers `metadata.normalized_text` and falls back to the
                display text only when needed.
            - `id_source_kind` records which source supplied the ID text.
            - `metadata` is the SFI metadata dictionary used by downstream helpers.
    """

    metadata = sfi.metadata or {}
    display_text = normalize_ws(sfi.description or "")
    id_source_text = normalize_ws(str(metadata.get("normalized_text") or ""))
    id_source_kind = "metadata.normalized_text"

    if not id_source_text:
        id_source_text = display_text
        id_source_kind = "sfi.description_fallback"

    if id_source_kind_override:
        id_source_kind = normalize_ws(id_source_kind_override)

    return display_text, id_source_text, id_source_kind, metadata


def _resolve_prompt_display_text(sfi: StandardsFrameworkItem) -> str:
    """Resolve the full untrimmed display text for atomic-skills prompting.

    This centralizes the prompt text selection logic. It prefers the exported SFI
    `description` because that is the human-readable text shown to the model, then
    falls back to `metadata.normalized_text` when the description is empty.

    Parameters
    ----------
    sfi
        The StandardsFrameworkItem representing the expectation.

    Returns
    -------
    str
        The full untrimmed display text that underlies the prompt `display_text`.
    """

    metadata = sfi.metadata or {}
    display_text = normalize_ws(sfi.description or "")
    fallback_text = normalize_ws(metadata.get("normalized_text") or "") or display_text
    return display_text or fallback_text


def _select_lc_source_sfis_for_export(
    *, config: CreateKGConfig, sfis: Iterable[StandardsFrameworkItem]
) -> tuple[list[StandardsFrameworkItem], dict[str, Any]]:
    """Return eligible LC source SFIs plus their pre-generation eligibility report.

    Parameters
    ----------
    config
        The KG export configuration containing LC source selection policies.
    sfis
        An iterable of StandardsFrameworkItems to evaluate for LC source eligibility.

    Returns
    -------
    tuple[list[StandardsFrameworkItem], dict[str, Any]]
        A tuple of `(eligible_sfis, report)` where `eligible_sfis` is a
        list of StandardsFrameworkItems that passed the LC source selection criteria,
        and `report` is a dictionary summarizing the eligibility decisions for all
        considered SFIs, suitable for JSON serialization and export as a QA artifact.
    """

    decisions = select_lc_source_sfis(config=config, sfis=sfis)
    report = _build_lc_source_eligibility_report(decisions)
    eligible_sfis = [decision.sfi for decision in decisions if decision.eligible]

    logger.info(
        f"LC source selection: "
        f"{len(eligible_sfis)} eligible / {len(decisions)} considered "
        f"({len(decisions) - len(eligible_sfis)} excluded)."
    )

    return eligible_sfis, report


def _sort_lc_export_artifacts(
    *, lcs: list[LearningComponent], rels: list[Relationship]
) -> tuple[list[LearningComponent], list[Relationship]]:
    """Sort LearningComponents export artifacts deterministically.

    Parameters
    ----------
    lcs
        The LearningComponent entities to sort.
    rels
        The supports Relationship entities to sort.

    Returns
    -------
    tuple[list[LearningComponent], list[Relationship]]
        A tuple of `(sorted_lcs, sorted_rels)` with deterministic ordering applied.
        LearningComponents are sorted by `identifier`. Relationships are sorted by
        `(source_entity_value, target_entity_value, identifier)`.
    """

    sorted_lcs = sorted(lcs, key=lambda lc: str(lc.identifier))
    sorted_rels = sorted(
        rels,
        key=lambda rel: (
            str(rel.source_entity_value),
            str(rel.target_entity_value),
            str(rel.identifier),
        ),
    )
    return sorted_lcs, sorted_rels


def _sort_number(*, default: float = float("inf"), value: Any) -> float:
    """Convert a possible numeric sort value to float with a stable missing default.

    Parameters
    ----------
    default
        The value to return when the input is None or cannot be converted to a number.
        Defaults to positive infinity so missing values sort last.
    value
        The value to convert to a float for sorting. Can be of any type;
        non-convertible values will trigger the default.

    Returns
    -------
    float
        The converted float value, or the default when input is None or non-convertible.
    """

    if value is None:
        return default

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _sort_sfis_for_lc_generation(
    sfis: Iterable[StandardsFrameworkItem],
) -> list[StandardsFrameworkItem]:
    """Sort LC source SFIs in deterministic curriculum/document order.

    This is used by all LC policies so non-LLM exports and LLM atomic-skills batches
    traverse the same stable order.
    """

    def _canonical_parent_path_key(value: Any) -> str:
        """Return the grouping/path portion of a canonical path key.

        Canonical path keys for leaf standards usually end with an expectation fragment
        that contains a text hash. Using the full key too early in the sort would make
        hash text ordering outrank the canonical order index within the parent. This
        helper keeps sibling groupings together while reserving the full path for a
        late deterministic tie-breaker.

        Parameters
        ----------
        value
            The canonical path key to extract the parent portion from.

        Returns
        -------
        str
            The parent path key with the final fragment removed, or the original value
            if it does not contain a slash. For example,
            `section:foo/stage:bar/expectation::abc123` becomes
            `section:foo/stage:bar`, while `section:foo` remains `section:foo`. If the
            input is empty or None, returns an empty string.
        """

        path = normalize_ws(str(value or ""))

        if not path:
            return ""

        return path.rsplit("/", 1)[0] if "/" in path else path

    def _sort_key(sfi: StandardsFrameworkItem) -> tuple[Any, ...]:
        """Return a deterministic, curriculum-aware sort key for LC source SFIs.

        UUID-only ordering is stable but pedagogically arbitrary. This key keeps nearby
        curriculum items near each other before LC creation and, especially, before
        LLM-based atomic-skills batching. It prefers source document order, canonical
        grouping context, and order metadata before using leaf-level path hashes and
        the SFI UUID as tie-breakers.

        Parameters
        ----------
        sfi
            The StandardsFrameworkItem to generate a sort key for.

        Returns
        -------
        tuple[Any, ...]
            A tuple key that can be used to sort SFIs in a stable, curriculum-aware
            order that prioritizes source document order and canonical curriculum
            metadata.
        """

        metadata = sfi.metadata or {}
        progression_context = metadata.get("progression_context", {})
        bbox_top, bbox_left = _bbox_reading_order(metadata)
        canonical_path_key = metadata.get("canonical_path_key")
        canonical_parent_path_key = _canonical_parent_path_key(canonical_path_key)
        order_index = _sort_number(
            value=progression_context.get(
                "canonical_order_index_within_parent",
                progression_context.get("order_index_within_parent"),
            )
        )
        return (
            _first_page_index(metadata),
            bbox_top,
            bbox_left,
            _natural_sort_key(progression_context.get("topic_path_key")),
            _natural_sort_key(progression_context.get("thread_key")),
            _natural_sort_key(canonical_parent_path_key),
            order_index,
            _natural_sort_key(sfi.statement_type),
            _natural_sort_key(canonical_path_key),
            str(sfi.case_identifier_uuid),
        )

    return sorted(sfis, key=_sort_key)


def _split_bullets(text: str) -> list[str]:
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

    src = re.sub(r"[\u00ad\u200b\u200c\u200d\ufeff]+", "", src)
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


def _split_lc_parts(
    *, max_splits: int, policy: str, text: str
) -> tuple[list[str], bool]:
    """Split LC source text according to the configured policy.

    Parameters
    ----------
    max_splits
        Maximum number of parts allowed for a single standard.
    policy
        LC split policy label (`1_to_1`, `split_bullets`, or equivalent override).
    text
        The normalized text source to split.

    Returns
    -------
    tuple[list[str], bool]
        A tuple of `(parts, truncated)` where `parts` is the ordered split list and
        `truncated` indicates whether additional parts were dropped after applying the
        maximum-splits cap.
    """

    raw_text = str(text or "").strip()
    normalized_text = normalize_ws(raw_text)

    if not normalized_text:
        return [], False

    if policy == "split_bullets":
        # Preserve original line breaks for bullet/numbered-line detection. Collapsing
        # whitespace before `_split_bullets()` would make line-start bullets and
        # numbered lists indistinguishable from ordinary inline text.
        parts = _split_bullets(raw_text) or [normalized_text]
    else:
        parts = [normalized_text]

    cleaned_parts = [normalize_ws(part) for part in parts if normalize_ws(part)]
    truncated = len(cleaned_parts) > max_splits

    if truncated:
        cleaned_parts = cleaned_parts[:max_splits]

    return cleaned_parts, truncated


def _summarize_lc_source_decisions(
    *, decisions: list[LearningComponentsSourceDecision], group_key: str
) -> list[dict[str, Any]]:
    """Summarize LC source eligibility decisions by one extracted field.

    Parameters
    ----------
    decisions
        The list of LC source eligibility decisions to summarize.
    group_key
        The key of the extracted SFI field to group by (e.g., `source_label` or
        `path_pattern`).

    Returns
    -------
    list[dict[str, Any]]
        A list of summary rows, one per distinct value of the grouping field, sorted by
        descending total SFI count and then by grouping value. Each row contains the
        grouping value, counts of eligible and excluded SFIs, total SFI count, and a
        breakdown of exclusion reasons with their respective counts.
    """

    grouped: dict[str, dict[str, Any]] = {}

    for decision in decisions:
        value = _clean_lc_source_report_value(decision.fields.get(group_key))
        row = grouped.setdefault(
            value,
            {
                "eligible_sfis": 0,
                "excluded_sfis": 0,
                "exclusion_reasons": Counter(),
                "total_sfis": 0,
                "value": value,
            },
        )
        row["total_sfis"] += 1

        if decision.eligible:
            row["eligible_sfis"] += 1
        else:
            row["excluded_sfis"] += 1

            for reason in decision.reasons:
                row["exclusion_reasons"][reason] += 1

    rows: list[dict[str, Any]] = []

    for row in grouped.values():
        exclusion_reasons = row.pop("exclusion_reasons")
        row["exclusion_reasons"] = dict(sorted(exclusion_reasons.items()))
        rows.append(row)

    return sorted(rows, key=lambda r: (-r["total_sfis"], r["value"]))


def _summarize_lc_source_level_records(
    *, group_key: str, records: list[dict[str, str]], total_lcs: int
) -> list[dict[str, Any]]:
    """Summarize LC counts and distinct supporting-SFI counts by a report key.

    Parameters
    ----------
    group_key
        Record key to group by (e.g., `supporting_sfi_source_label`).
    records
        Source-level records, one per LearningComponent.
    total_lcs
        Total number of LCs in the export; used for percent calculations.

    Returns
    -------
    list[dict[str, Any]]
        Rows sorted by descending LC count and then by grouping value.
    """

    grouped: dict[str, dict[str, Any]] = {}

    for record in records:
        value = record[group_key]
        row = grouped.setdefault(
            value, {"value": value, "lc_count": 0, "supporting_sfi_case_uuids": set()}
        )
        row["lc_count"] += 1

        if record["supporting_sfi_case_uuid"] != "(missing)":
            row["supporting_sfi_case_uuids"].add(record["supporting_sfi_case_uuid"])

    rows: list[dict[str, Any]] = []

    for value, row in grouped.items():
        supporting_sfi_count = len(row["supporting_sfi_case_uuids"])
        lc_count = int(row["lc_count"])
        out_row: dict[str, Any] = {
            "avg_lcs_per_supporting_sfi": (
                round(lc_count / supporting_sfi_count, 4)
                if supporting_sfi_count
                else 0.0
            ),
            "lc_count": lc_count,
            "lc_percent": round(lc_count / total_lcs, 4) if total_lcs else 0.0,
            "supporting_sfi_count": supporting_sfi_count,
            "value": value,
        }

        if group_key == "supporting_sfi_path_pattern":
            out_row["path_depth"] = 0 if value == "(missing)" else len(value.split("/"))

        rows.append(out_row)

    return sorted(rows, key=lambda x: (-int(x["lc_count"]), str(x["value"])))


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


def _trim_text_with_debug(*, max_chars: int, s: str) -> tuple[str, bool, int]:
    """Trim text and return debug metadata about truncation.

    Parameters
    ----------
    max_chars
        The maximum number of characters to allow in the output string, including the
        ellipsis if truncation occurs.
    s
        The input string to trim and analyze for truncation. This is typically the text
        used for ID generation or display in the LearningComponent. It will be
        normalized for whitespace before trimming, to ensure consistent length counting
        and truncation behavior.

    Returns
    -------
    tuple[str, bool, int]
        A tuple of:
        - trimmed_text
        - was_truncated
        - original_length (after whitespace normalization)
    """

    normalized = normalize_ws(s or "")
    trimmed = _trim_text(max_chars=max_chars, s=normalized)
    return trimmed, trimmed != normalized, len(normalized)


def _validate_lc_export_integrity(
    *,
    lcs: list[LearningComponent],
    rels: list[Relationship],
    valid_sfi_case_uuids: set[str],
) -> None:
    """Validate Learning Components export integrity before persistence.

    Parameters
    ----------
    lcs
        The LearningComponent entities prepared for export.
    rels
        The supports Relationship entities prepared for export.
    valid_sfi_case_uuids
        The set of valid StandardsFrameworkItem CASE UUIDs that supports edges may
        target.

    Raises
    ------
    ValueError
        If relationship types are invalid, counts do not match, identifiers are
        duplicated, supports sources do not map one-to-one with LearningComponents, or
        supports targets reference unknown StandardsFrameworkItems.
    """

    if any(rel.relationship_type != SUPPORTS for rel in rels):
        raise ValueError(
            "Non-supports relationship found in Learning Components export."
        )

    if len(rels) != len(lcs):
        raise ValueError(
            f"Expected 1 supports edge per LC, got {len(rels)} rels for {len(lcs)} LCs."
        )

    lc_ids = [str(lc.identifier) for lc in lcs]
    rel_ids = [str(rel.identifier) for rel in rels]
    rel_source_ids = [str(rel.source_entity_value) for rel in rels]
    rel_target_ids = [str(rel.target_entity_value) for rel in rels]
    lc_id_counts = Counter(lc_ids)
    rel_id_counts = Counter(rel_ids)
    rel_source_counts = Counter(rel_source_ids)
    duplicate_lc_ids = sorted(item for item, count in lc_id_counts.items() if count > 1)

    if duplicate_lc_ids:
        raise ValueError(
            f"Duplicate LearningComponent identifier(s) found in export: "
            f"{duplicate_lc_ids[:10]}"
            + (
                f" ... (+{len(duplicate_lc_ids) - 10} more)"
                if len(duplicate_lc_ids) > 10
                else ""
            )
        )

    duplicate_rel_ids = sorted(
        item for item, count in rel_id_counts.items() if count > 1
    )

    if duplicate_rel_ids:
        raise ValueError(
            f"Duplicate supports relationship identifier(s) found in export: "
            f"{duplicate_rel_ids[:10]}"
            + (
                f" ... (+{len(duplicate_rel_ids) - 10} more)"
                if len(duplicate_rel_ids) > 10
                else ""
            )
        )

    unknown_lc_sources = sorted(set(rel_source_ids) - set(lc_ids))

    if unknown_lc_sources:
        raise ValueError(
            f"supports relationship source_entity_value references unknown "
            f"LearningComponent identifier(s): {unknown_lc_sources[:10]}"
            + (
                f" ... (+{len(unknown_lc_sources) - 10} more)"
                if len(unknown_lc_sources) > 10
                else ""
            )
        )

    unknown_sfi_targets = sorted(set(rel_target_ids) - set(valid_sfi_case_uuids))

    if unknown_sfi_targets:
        raise ValueError(
            f"supports relationship target_entity_value references unknown "
            f"StandardsFrameworkItem CASE UUID(s): {unknown_sfi_targets[:10]}"
            + (
                f" ... (+{len(unknown_sfi_targets) - 10} more)"
                if len(unknown_sfi_targets) > 10
                else ""
            )
        )

    missing_sources = sorted(set(lc_ids) - set(rel_source_ids))

    if missing_sources:
        raise ValueError(
            f"LearningComponent identifier(s) missing a supports relationship: "
            f"{missing_sources[:10]}"
            + (
                f" ... (+{len(missing_sources) - 10} more)"
                if len(missing_sources) > 10
                else ""
            )
        )

    duplicated_sources = sorted(
        item for item, count in rel_source_counts.items() if count > 1
    )

    if duplicated_sources:
        raise ValueError(
            f"LearningComponent identifier(s) have multiple supports relationships: "
            f"{duplicated_sources[:10]}"
            + (
                f" ... (+{len(duplicated_sources) - 10} more)"
                if len(duplicated_sources) > 10
                else ""
            )
        )


def export_learning_components(
    *,
    academic_standards: AcademicStandardsExport,
    config: CreateKGConfig,
    ctx: ExportContext,
    kg_dirs: KGDirs,
    usage_tracker: KGUsageTracker,
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
    usage_tracker
        The KGUsageTracker instance used to accumulate token usage across LLM calls for
        generation and validation calls.

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
        lcs, rels, lc_stats = export_learning_components_using_llm(
            academic_standards=academic_standards,
            config=config,
            ctx=ctx,
            kg_dirs=kg_dirs,
            usage_tracker=usage_tracker,
        )
        return _finalize_lc_export(
            config=config,
            ctx=ctx,
            kg_dirs=kg_dirs,
            lc_stats=lc_stats,
            lcs=lcs,
            rels=rels,
            valid_sfi_case_uuids={
                str(sfi.case_identifier_uuid) for sfi in academic_standards.items
            },
        )

    lc_source_sfis, source_eligibility_report = _select_lc_source_sfis_for_export(
        config=config, sfis=academic_standards.items
    )
    fw_metadata = ctx.get_framework_metadata()
    lcs = []
    rels = []
    splits_per_sfi: defaultdict[int, int] = defaultdict(int)

    # Deterministic curriculum/document order keeps related SFIs adjacent.
    lc_source_sfis_sorted = _sort_sfis_for_lc_generation(lc_source_sfis)

    for sfi in lc_source_sfis_sorted:
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
            f"Learning Components: {zero_lc_count} eligible LC source SFI(s) produced 0 "
            f"LearningComponents (empty text). These SFIs have no `supports` edges."
        )

    lc_stats = {
        "_source_eligibility_report": source_eligibility_report,
        "max_splits_observed": max(splits_per_sfi.keys()) if splits_per_sfi else 0,
        "source_eligibility_summary": source_eligibility_report["summary"],
        "split_policy": config.lc_policy,
        "splits_distribution": {str(k): v for k, v in sorted(splits_per_sfi.items())},
        "total_expectations": len(lc_source_sfis_sorted),
        "total_lc_source_sfis_considered": source_eligibility_report["summary"][
            "total_sfis_considered"
        ],
        "total_lc_source_sfis_eligible": source_eligibility_report["summary"][
            "eligible_sfis"
        ],
        "total_lc_source_sfis_excluded": source_eligibility_report["summary"][
            "excluded_sfis"
        ],
        "total_lcs": len(lcs),
    }
    return _finalize_lc_export(
        config=config,
        ctx=ctx,
        kg_dirs=kg_dirs,
        lc_stats=lc_stats,
        lcs=lcs,
        rels=rels,
        valid_sfi_case_uuids={
            str(sfi.case_identifier_uuid) for sfi in academic_standards.items
        },
    )


def export_learning_components_using_llm(
    *,
    academic_standards: AcademicStandardsExport,
    config: CreateKGConfig,
    ctx: ExportContext,
    kg_dirs: KGDirs,
    usage_tracker: KGUsageTracker,
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
    usage_tracker
        The KGUsageTracker instance used to accumulate token usage across LLM calls for
        generation and validation calls.

    Returns
    -------
    tuple[list[LearningComponent], list[Relationship], dict[str, Any]]
        A tuple containing the list of LearningComponent entities created, the list of
        supports Relationships emitted, and a dictionary of statistics about the LC
        creation process (e.g., split distribution, fallback counts) for analysis and
        debugging.
    """

    lc_source_sfis, source_eligibility_report = _select_lc_source_sfis_for_export(
        config=config, sfis=academic_standards.items
    )
    fw_metadata = ctx.get_framework_metadata()

    # Deterministic curriculum/document order keeps related SFIs adjacent before
    # batching, while still falling back to SFI UUID as the final tiebreaker.
    lc_source_sfis_sorted = _sort_sfis_for_lc_generation(lc_source_sfis)

    # Pre-filter: remove SFIs whose text is entirely empty so they never reach the LLM.
    # These would produce 0 LCs anyway (same outcome as the
    # `_create_lcs_for_expectation()` empty-text guard), but sending them to the LLM
    # wastes tokens and risks the model hallucinating content for a blank input.
    batchable_sfis: list[StandardsFrameworkItem] = []
    empty_text_sfis: list[StandardsFrameworkItem] = []

    for sfi in lc_source_sfis_sorted:
        if _has_usable_text(sfi):
            batchable_sfis.append(sfi)
        else:
            empty_text_sfis.append(sfi)

    if empty_text_sfis:
        logger.warning(
            f"LLM atomic skills: skipping {len(empty_text_sfis)} eligible LC source SFI(s) "
            f"with empty text (no `normalized_text` or `description`). These SFIs will "
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

    logger.info(
        f"Starting LLM atomic skills export for {total_sfis} batchable LC source "
        f"SFI(s) across {total_batches} batches (batch size: {batch_size})."
        + (
            f" ({len(empty_text_sfis)} eligible LC source SFI(s) excluded for empty text.)"
            if empty_text_sfis
            else ""
        )
    )

    atomic_skills_cache: dict[str, list[dict[str, Any]]] = {}
    debug_batches: list[dict[str, Any]] = []
    fallback_sfis_total: list[str] = []

    for batch_index in range(0, total_sfis, batch_size):
        current_batch_num = (batch_index // batch_size) + 1
        batch = batchable_sfis[batch_index : batch_index + batch_size]
        batch_lcs, batch_rels, batch_splits, batch_debug, batch_fallbacks = (
            _process_atomic_skills_batch(
                atomic_skills_cache=atomic_skills_cache,
                batch=batch,
                batch_index=batch_index // batch_size,
                config=config,
                ctx=ctx,
                current_batch_num=current_batch_num,
                fw_metadata=fw_metadata,
                max_splits=max_splits,
                total_batches=total_batches,
                usage_tracker=usage_tracker,
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

    cache_hits_sfis = sum(
        int(batch_debug.get("atomic_skills_cache", {}).get("hits", 0))
        for batch_debug in debug_batches
    )
    cache_misses_sfis = sum(
        int(batch_debug.get("atomic_skills_cache", {}).get("misses", 0))
        for batch_debug in debug_batches
    )
    llm_batches_executed = sum(
        1 for batch_debug in debug_batches if batch_debug.get("llm_called")
    )

    lc_stats = {
        "_source_eligibility_report": source_eligibility_report,
        "atomic_skills_cache_entries": len(atomic_skills_cache),
        "atomic_skills_cache_hit_sfis": cache_hits_sfis,
        "atomic_skills_cache_miss_sfis": cache_misses_sfis,
        "fallback_sfis_count": len(set(fallback_sfis_total)),
        "llm_batches": llm_batches_executed,
        "llm_processing_batches": len(debug_batches),
        "max_splits_observed": max(splits_per_sfi.keys()) if splits_per_sfi else 0,
        "source_eligibility_summary": source_eligibility_report["summary"],
        "split_policy": "llm_atomic_skills",
        "splits_distribution": {str(k): v for k, v in sorted(splits_per_sfi.items())},
        "total_lc_source_sfis": len(batchable_sfis) + len(empty_text_sfis),
        "total_lc_source_sfis_batchable": len(batchable_sfis),
        "total_lc_source_sfis_considered": source_eligibility_report["summary"][
            "total_sfis_considered"
        ],
        "total_lc_source_sfis_empty_text": len(empty_text_sfis),
        "total_lc_source_sfis_eligible": source_eligibility_report["summary"][
            "eligible_sfis"
        ],
        "total_lc_source_sfis_excluded": source_eligibility_report["summary"][
            "excluded_sfis"
        ],
        "total_lcs": len(lcs),
    }
    return lcs, rels, lc_stats


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
    usage_tracker: KGUsageTracker,
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
    usage_tracker
        The KGUsageTracker instance for tracking LLM usage during export.

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
            usage_tracker=usage_tracker,
        )

    return learning_components, lc_reused


def select_lc_source_sfis(
    *, config: CreateKGConfig, sfis: Iterable[StandardsFrameworkItem]
) -> list[LearningComponentsSourceDecision]:
    """Decide which StandardsFrameworkItems are eligible LC-generation sources.

    This is intentionally separate from Academic Standards export. Broad competencies
    may remain valid StandardsFrameworkItems while being excluded from
    LearningComponent generation through config.

    Parameters
    ----------
    config
        The CreateKGConfig containing the LC source filtering criteria.
    sfis
        The iterable of StandardsFrameworkItems to evaluate for LC source eligibility.

    Returns
    -------
    list[LearningComponentsSourceDecision]
        A list of decisions, one per input SFI, indicating whether it is eligible as an
        LC source and the reasons for exclusion if not eligible.
    """

    allowed_normalized_types = set(config.lc_source_normalized_statement_types or [])
    roles_inc = _config_value_set(config.lc_source_roles_include)
    roles_exc = _config_value_set(config.lc_source_roles_exclude)
    stmt_types_inc = _config_value_set(config.lc_source_statement_types_include)
    stmt_types_exc = _config_value_set(config.lc_source_statement_types_exclude)
    labels_inc = _config_value_set(config.lc_source_labels_include)
    labels_exc = _config_value_set(config.lc_source_labels_exclude)
    decisions: list[LearningComponentsSourceDecision] = []

    for sfi in sfis:
        fields = _lc_source_fields(sfi)
        reasons: list[str] = []
        norm_type = fields["normalized_statement_type"]
        role = _clean_lc_source_filter_value(fields["role"])
        stmt_type = _clean_lc_source_filter_value(fields["statement_type"])
        label = _clean_lc_source_filter_value(fields["source_label"])

        if allowed_normalized_types and norm_type not in allowed_normalized_types:
            reasons.append("excluded_normalized_statement_type")

        set_checks = [
            (
                role,
                roles_inc,
                "excluded_role_not_in_include",
                roles_exc,
                "excluded_role",
            ),
            (
                stmt_type,
                stmt_types_inc,
                "excluded_statement_type_not_in_include",
                stmt_types_exc,
                "excluded_statement_type",
            ),
            (
                label,
                labels_inc,
                "excluded_source_label_not_in_include",
                labels_exc,
                "excluded_source_label",
            ),
        ]

        for val, inc_set, inc_reason, exc_set, exc_reason in set_checks:
            if inc_set and val not in inc_set:
                reasons.append(inc_reason)

            if exc_set and val in exc_set:
                reasons.append(exc_reason)

        path_key = str(fields["canonical_path_key"] or "")
        path_pattern = str(fields["path_pattern"] or "")

        if config.lc_source_path_patterns_include and not _lc_source_path_matches_any(
            path_key=path_key,
            path_pattern=path_pattern,
            patterns=config.lc_source_path_patterns_include,
        ):
            reasons.append("excluded_path_pattern_not_in_include")

        if config.lc_source_path_patterns_exclude and _lc_source_path_matches_any(
            path_key=path_key,
            path_pattern=path_pattern,
            patterns=config.lc_source_path_patterns_exclude,
        ):
            reasons.append("excluded_path_pattern")

        depth = int(fields["path_depth"] or 0)

        if (
            config.lc_source_min_path_depth is not None
            and depth < config.lc_source_min_path_depth
        ):
            reasons.append("excluded_path_depth_below_min")

        if (
            config.lc_source_max_path_depth is not None
            and depth > config.lc_source_max_path_depth
        ):
            reasons.append("excluded_path_depth_above_max")

        decisions.append(
            LearningComponentsSourceDecision(
                eligible=not reasons,
                fields=fields,
                reasons=reasons or ["eligible"],
                sfi=sfi,
            )
        )

    return decisions
