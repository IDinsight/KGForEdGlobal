"""This module contains functionalities for compiling, validating, and write final
Academic Standards KG export artifacts.

This module consumes final SFI records and validated hasChild edges, compiles Learning
Commons-shaped export objects, validates the complete exported graph, and writes the
final inspectable artifacts.
"""

# Standard Library
import hashlib
import json
import uuid

from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

# Third Party Library
from loguru import logger
from pydantic import BaseModel

# Package Library
from skg.document_ir.schemas import DocumentIR
from skg.kgs.schemas import (
    AcademicStandardsExportSummary,
    AcademicStandardsKGBundle,
    AcademicStandardsUnresolvedItems,
    AcademicStandardsValidationReport,
    Relationship,
    SFIFinalRecord,
    SFIFinalSummary,
    SFIHasChildEdge,
    SFIHasChildResolutionSummary,
    StandardsFramework,
    StandardsFrameworkItem,
)
from skg.kgs.utils import (
    KGDirs,
    append_jsonl_model,
    build_standards_framework_uuid,
    model_dump_key,
    reset_output_files,
)
from skg.schemas import CreateKGConfig
from skg.utils.general import make_dir, open_json_type, write_to_json


def _append_jsonl_models(*, fp: Path, models: Sequence[BaseModel]) -> None:
    """Write Pydantic models to a JSONL file.

    Parameters
    ----------
    fp
        JSONL artifact path.
    models
        Models to write, one model per line.
    """

    make_dir(fp.parent)
    fp.write_text("", encoding="utf-8")

    for model in models:
        append_jsonl_model(fp=fp, model=model)


def _build_bundle(
    *,
    entity_provenance: dict[str, Any],
    framework: StandardsFramework,
    items: Sequence[StandardsFrameworkItem],
    relationships: Sequence[Relationship],
    unresolved_items: AcademicStandardsUnresolvedItems,
    validation_report: AcademicStandardsValidationReport,
) -> AcademicStandardsKGBundle:
    """Build the complete final Academic Standards KG bundle.

    Parameters
    ----------
    entity_provenance
        Provenance artifact keyed by exported entity type and identifier.
    framework
        Single exported StandardsFramework object.
    items
        Exported StandardsFrameworkItem records.
    relationships
        Exported hasChild relationship records.
    unresolved_items
        Unresolved report.
    validation_report
        Validation report for the compiled export.

    Returns
    -------
    AcademicStandardsKGBundle
        Complete final export bundle.
    """

    summary = AcademicStandardsExportSummary(
        final_sfi_count=len(items),
        finalization_exclusion_summary=unresolved_items.finalization_exclusion_summary,
        framework_count=1,
        has_child_relationship_count=len(relationships),
        relationship_unresolved_edge_count=len(
            unresolved_items.relationship_unresolved_edges
        ),
    )
    return AcademicStandardsKGBundle(
        entity_provenance=entity_provenance,
        framework=framework,
        items=list(items),
        relationships_has_child=list(relationships),
        summary=summary,
        unresolved_items=unresolved_items,
        validation_report=validation_report,
    )


def _build_entity_provenance(
    *,
    document_ir: DocumentIR,
    framework: StandardsFramework,
    items: Sequence[StandardsFrameworkItem],
    kg_run_manifest: dict[str, Any],
    relationships: Sequence[Relationship],
    sfi_final_records: Sequence[SFIFinalRecord],
) -> dict[str, Any]:
    """Build provenance for exported framework, item, and relationship entities.

    Parameters
    ----------
    document_ir
        Source stitched DocumentIR.
    framework
        Exported StandardsFramework object.
    items
        Exported StandardsFrameworkItem objects.
    kg_run_manifest
        Persisted KG run manifest.
    relationships
        Exported hasChild relationships.
    sfi_final_records
        Source final SFI records aligned to exported items.

    Returns
    -------
    dict[str, Any]
        Deterministic provenance artifact for final KG entities.
    """

    records_by_id = {str(record.final_sfi_uuid): record for record in sfi_final_records}
    items_provenance: dict[str, Any] = {}

    for item in items:
        record = records_by_id[str(item.case_identifier_uuid)]
        items_provenance[str(item.case_identifier_uuid)] = {
            "audit_flags": record.audit_flags,
            "audit_notes": record.audit_notes,
            "audit_peer_merge_group_ids": record.audit_peer_merge_group_ids,
            "candidate_source_refs": record.candidate_source_refs,
            "candidate_source_texts": record.candidate_source_texts,
            "identity_key": record.identity_key,
            "merge_decision": record.merge_decision,
            "merge_group_id": record.merge_group_id,
            "merge_reason": record.merge_reason,
            "source_context_keys": record.source_context_keys,
            "source_page_indexes": record.source_page_indexes,
            "source_registry_candidate_ids": record.source_registry_candidate_ids,
            "source_segment_ids": record.source_segment_ids,
            "source_window_ids": record.source_window_ids,
            "source_window_indexes": record.source_window_indexes,
        }

    return {
        "framework": {
            "case_identifier_uri": framework.case_identifier_uri,
            "case_identifier_uuid": str(framework.case_identifier_uuid),
            "doc_key": document_ir.doc_key,
            "pdf_name": document_ir.pdf_name,
            "provenance_note": (
                "Synthetic StandardsFramework root deterministically minted from "
                "DocumentIR doc_key and the LC canonical namespace."
            ),
        },
        "items": items_provenance,
        "kg_run_manifest": kg_run_manifest,
        "relationships_has_child": {
            str(relationship.identifier): {
                "metadata": relationship.metadata,
                "relationship_type": relationship.relationship_type,
                "source_entity": relationship.source_entity,
                "source_entity_value": relationship.source_entity_value,
                "target_entity": relationship.target_entity,
                "target_entity_value": relationship.target_entity_value,
            }
            for relationship in relationships
        },
    }


def _build_framework(
    *,
    document_ir: DocumentIR,
    kg_config: CreateKGConfig,
    kg_run_manifest: dict[str, Any],
) -> StandardsFramework:
    """Compile one StandardsFramework export object.

    Parameters
    ----------
    document_ir
        Source stitched DocumentIR.
    kg_config
        Runtime KG creation configuration.
    kg_run_manifest
        Persisted KG run manifest.

    Returns
    -------
    StandardsFramework
        Compiled framework object with the same root UUID used during hasChild
        relationship building.
    """

    metadata = kg_config.metadata
    framework_uuid = build_standards_framework_uuid(doc_key=document_ir.doc_key)
    return StandardsFramework(
        academic_subject=metadata.subject,
        adoption_status=metadata.adoption_status or "Unknown",
        attribution_statement=metadata.attribution_statement,
        author=metadata.author,
        case_identifier_uri=f"urn:uuid:{framework_uuid}",
        case_identifier_uuid=framework_uuid,
        description=None,
        identifier=framework_uuid,
        in_language=metadata.primary_language,
        jurisdiction=metadata.jurisdiction,
        license=metadata.license,
        metadata={
            "country": metadata.country,
            "doc_key": document_ir.doc_key,
            "framework_title": metadata.framework_title,
            "grades_or_stages": metadata.grades_or_stages,
            "kg_run_manifest_status": kg_run_manifest.get("status"),
            "languages": metadata.languages,
            "page_count": document_ir.page_count,
            "pdf_name": document_ir.pdf_name,
            "primary_language": metadata.primary_language,
        },
        name=metadata.framework_title,
        notes=None,
        provider=metadata.provider,
    )


def _build_input_fingerprints(
    *,
    has_child_edges: Sequence[SFIHasChildEdge],
    has_child_resolution_summary: SFIHasChildResolutionSummary,
    has_child_unresolved_edges: Sequence[SFIHasChildEdge],
    kg_run_manifest: dict[str, Any],
    sfi_final_records: Sequence[SFIFinalRecord],
    sfi_final_summary: SFIFinalSummary,
) -> dict[str, str]:
    """Build stable fingerprints for input payloads.

    Parameters
    ----------
    has_child_edges
        Validated hasChild edges.
    has_child_resolution_summary
        hasChild relationship-resolution summary.
    has_child_unresolved_edges
        hasCHild unresolved root-fallback edges.
    kg_run_manifest
        KG run manifest.
    sfi_final_records
        Final SFI records.
    sfi_final_summary
        SFI finalization summary.

    Returns
    -------
    dict[str, str]
        Stable SHA-256 fingerprints keyed by input artifact label.
    """

    return {
        "has_child_edges_final": _fingerprint_jsonable(
            [model.model_dump(mode="json") for model in has_child_edges]
        ),
        "has_child_resolution_summary": _fingerprint_jsonable(
            has_child_resolution_summary.model_dump(mode="json")
        ),
        "has_child_unresolved_edges": _fingerprint_jsonable(
            [model.model_dump(mode="json") for model in has_child_unresolved_edges]
        ),
        "kg_run_manifest": _fingerprint_jsonable(kg_run_manifest),
        "sfi_final_records": _fingerprint_jsonable(
            [model.model_dump(mode="json") for model in sfi_final_records]
        ),
        "sfi_final_summary": _fingerprint_jsonable(
            sfi_final_summary.model_dump(mode="json")
        ),
    }


def _build_unresolved_items(
    *,
    has_child_unresolved_edges: Sequence[SFIHasChildEdge],
    sfi_final_summary: SFIFinalSummary,
) -> AcademicStandardsUnresolvedItems:
    """Build the unresolved report.

    Parameters
    ----------
    has_child_unresolved_edges
        hasChild nresolved root-fallback relationship edges.
    sfi_final_summary
        SFI finalization summary carrying exclusion counts.

    Returns
    -------
    AcademicStandardsUnresolvedItems
        Unresolved report that uses relationship unresolved edges plus summary counts.
    """

    return AcademicStandardsUnresolvedItems(
        finalization_exclusion_summary={
            "excluded_conflict_group_count": (
                sfi_final_summary.excluded_conflict_group_count
            ),
            "excluded_needs_review_group_count": (
                sfi_final_summary.excluded_needs_review_group_count
            ),
        },
        relationship_unresolved_edges=[
            edge.model_dump(mode="json") for edge in has_child_unresolved_edges
        ],
    )


def _compile_relationship(
    *, edge: SFIHasChildEdge, kg_config: CreateKGConfig
) -> Relationship:
    """Compile one hasChild edge into a final Relationship object.

    Parameters
    ----------
    edge
        Validated hasChild edge.
    kg_config
        Runtime KG creation configuration.

    Returns
    -------
    Relationship
        Exportable hasChild relationship preserving the edge identifier exactly.
    """

    metadata = kg_config.metadata
    return Relationship(
        attribution_statement=metadata.attribution_statement,
        author=metadata.author,
        description="",
        identifier=edge.relationship_id,
        license=metadata.license,
        metadata={
            "child_final_sfi_uuid": str(edge.child_final_sfi_uuid),
            "confidence": edge.confidence,
            "edge_metadata": edge.metadata,
            "evidence_reasons": edge.evidence_reasons,
            "is_root_edge": edge.is_root_edge,
            "llm_reason": edge.llm_reason,
            "parent_endpoint_id": edge.parent_endpoint_id,
            "parent_final_sfi_uuid": (
                str(edge.parent_final_sfi_uuid) if edge.parent_final_sfi_uuid else None
            ),
            "unresolved_root_fallback": edge.unresolved_root_fallback,
        },
        provider=metadata.provider,
        relationship_type="hasChild",
        source_entity=edge.source_entity,
        source_entity_key="case_identifier_uuid",
        source_entity_value=str(edge.source_entity_uuid),
        target_entity="StandardsFrameworkItem",
        target_entity_key="case_identifier_uuid",
        target_entity_value=str(edge.target_sfi_uuid),
    )


def _compile_relationships(
    *, has_child_edges: Sequence[SFIHasChildEdge], kg_config: CreateKGConfig
) -> list[Relationship]:
    """Compile all hasChild edges into final Relationship objects.

    Parameters
    ----------
    has_child_edges
        Validated hasChild edges.
    kg_config
        Runtime KG creation configuration.

    Returns
    -------
    list[Relationship]
        Compiled hasChild relationship objects.
    """

    return [
        _compile_relationship(edge=edge, kg_config=kg_config)
        for edge in has_child_edges
    ]


def _detect_sfi_cycles(relationships: Sequence[Relationship]) -> list[list[str]]:
    """Detect directed cycles among SFI-to-SFI hasChild relationships.

    Parameters
    ----------
    relationships
        Compiled hasChild relationship objects.

    Returns
    -------
    list[list[str]]
        Detected cycles represented as UUID strings.
    """

    graph: dict[str, list[str]] = defaultdict(list)

    for relationship in relationships:
        if relationship.source_entity != "StandardsFrameworkItem":
            continue

        graph[relationship.source_entity_value].append(relationship.target_entity_value)

    cycles: list[list[str]] = []
    stack: list[str] = []
    visited: set[str] = set()
    visiting: set[str] = set()

    def _visit(node_id: str) -> None:
        """Visit one node during DFS cycle detection.

        Parameters
        ----------
        node_id
            Current UUID string.
        """

        if node_id in visiting:
            cycle_start = stack.index(node_id) if node_id in stack else 0
            cycles.append(stack[cycle_start:] + [node_id])
            return

        if node_id in visited:
            return

        visiting.add(node_id)
        stack.append(node_id)

        for child_id in graph.get(node_id, []):
            _visit(child_id)

        stack.pop()
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in sorted(graph):
        _visit(node_id)

    return cycles


def _extract_grade_levels(record: SFIFinalRecord) -> list[str]:
    """Recover stable grade-level labels when they are explicit in SFI metadata.

    Parameters
    ----------
    record
        Final SFI record to inspect.

    Returns
    -------
    list[str]
        Stable grade-level labels, or an empty list when no grade is explicit.
    """

    values: list[str] = []

    if record.statement_type and record.statement_type.casefold() == "grade":
        values.append(record.description)

    scope_key = record.canonical_statement_scope_key or ""

    for raw_part in scope_key.split("|"):
        if ":" not in raw_part:
            continue

        label, value = raw_part.split(":", 1)

        if label.strip().casefold() == "grade" and value.strip():
            values.append(value.strip())

    for source_ref in record.candidate_source_refs:
        if not isinstance(source_ref, dict):
            continue

        source_scope_key = str(source_ref.get("canonical_statement_scope_key") or "")

        for raw_part in source_scope_key.split("|"):
            if ":" not in raw_part:
                continue

            label, value = raw_part.split(":", 1)

            if label.strip().casefold() == "grade" and value.strip():
                values.append(value.strip())

    return _unique_nonempty(values)


def _fingerprint_jsonable(value: Any) -> str:
    """Build a deterministic SHA-256 fingerprint for a JSON-compatible value.

    Parameters
    ----------
    value
        JSON-compatible value to fingerprint.

    Returns
    -------
    str
        Hex SHA-256 digest.
    """

    payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_complete_existing_export_artifacts(
    *,
    bundle: AcademicStandardsKGBundle,
    bundle_fp: Path,
    entity_provenance: dict[str, Any],
    entity_provenance_fp: Path,
    framework: StandardsFramework,
    framework_fp: Path,
    items: Sequence[StandardsFrameworkItem],
    items_fp: Path,
    relationships: Sequence[Relationship],
    relationships_fp: Path,
    unresolved_items: AcademicStandardsUnresolvedItems,
    unresolved_items_fp: Path,
    validation_report: AcademicStandardsValidationReport,
    validation_report_fp: Path,
) -> AcademicStandardsKGBundle | None:
    """Reuse existing artifacts only when they exactly match current output.

    Parameters
    ----------
    bundle
        Expected current KG bundle.
    bundle_fp
        Persisted bundle path.
    entity_provenance
        Expected provenance payload.
    entity_provenance_fp
        Persisted provenance path.
    framework
        Expected framework object.
    framework_fp
        Persisted framework path.
    items
        Expected item sequence.
    items_fp
        Persisted item JSONL path.
    relationships
        Expected relationship sequence.
    relationships_fp
        Persisted relationship JSONL path.
    unresolved_items
        Expected unresolved report.
    unresolved_items_fp
        Persisted unresolved report path.
    validation_report
        Expected validation report.
    validation_report_fp
        Persisted validation report path.

    Returns
    -------
    AcademicStandardsKGBundle | None
        Loaded reusable bundle, or None when existing artifacts are missing/stale.
    """

    try:
        loaded_framework = StandardsFramework.model_validate(
            open_json_type(framework_fp)
        )
        _validate_model_equal(
            actual=loaded_framework,
            artifact_label="standards_framework.json",
            expected=framework,
        )
        loaded_items = _load_jsonl_models(
            fp=items_fp, model_type=StandardsFrameworkItem
        )
        _validate_model_sequences_equal(
            actual=loaded_items,
            artifact_label="standards_framework_items.jsonl",
            expected=items,
        )
        loaded_relationships = _load_jsonl_models(
            fp=relationships_fp, model_type=Relationship
        )
        _validate_model_sequences_equal(
            actual=loaded_relationships,
            artifact_label="relationships_has_child.jsonl",
            expected=relationships,
        )
        loaded_entity_provenance = open_json_type(entity_provenance_fp)

        if _stable_json_key(loaded_entity_provenance) != _stable_json_key(
            entity_provenance
        ):
            raise ValueError("entity_provenance.json does not match current output.")

        loaded_unresolved_items = AcademicStandardsUnresolvedItems.model_validate(
            open_json_type(unresolved_items_fp)
        )
        _validate_model_equal(
            actual=loaded_unresolved_items,
            artifact_label="unresolved_items.json",
            expected=unresolved_items,
        )
        loaded_validation_report = AcademicStandardsValidationReport.model_validate(
            open_json_type(validation_report_fp)
        )
        _validate_model_equal(
            actual=loaded_validation_report,
            artifact_label="validation_report.json",
            expected=validation_report,
        )

        if not loaded_validation_report.passed:
            raise ValueError("Existing validation_report.json did not pass.")

        loaded_bundle = AcademicStandardsKGBundle.model_validate(
            open_json_type(bundle_fp)
        )
        _validate_model_equal(
            actual=loaded_bundle,
            artifact_label="academic_standards_kg_bundle.json",
            expected=bundle,
        )
    except Exception as e:  # pylint: disable=W0718
        logger.warning(
            f"Existing Academic Standards export artifacts are missing, incomplete, "
            f"or stale; rebuilding final KG export: {e}"
        )
        return None

    logger.info(
        f"Loading complete existing final Academic Standards KG export because "
        f"overwrite=False: {bundle_fp}"
    )
    return loaded_bundle


def _load_json_model_sequence(
    *, fp: Path, model_type: type[BaseModel]
) -> list[BaseModel]:
    """Load a JSON list artifact into a Pydantic model sequence.

    Parameters
    ----------
    fp
        JSON artifact path containing a list payload.
    model_type
        Pydantic model class used for each item.

    Returns
    -------
    list[BaseModel]
        Parsed model instances.
    """

    data = open_json_type(fp)

    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list artifact: {fp}")

    return [model_type.model_validate(item) for item in data]


def _load_jsonl_models(*, fp: Path, model_type: type[BaseModel]) -> list[BaseModel]:
    """Load a JSONL artifact into a Pydantic model sequence.

    Parameters
    ----------
    fp
        JSONL artifact path.
    model_type
        Pydantic model class used for each line.

    Returns
    -------
    list[BaseModel]
        Parsed model instances.
    """

    if not fp.exists():
        raise ValueError(f"Missing JSONL artifact: {fp}")

    models: list[BaseModel] = []

    with fp.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line_clean = line.strip()

            if not line_clean:
                continue

            try:
                models.append(model_type.model_validate_json(line_clean))
            except Exception as e:  # pylint: disable=W0718
                raise ValueError(
                    f"Invalid JSONL record in {fp} at line {line_number}."
                ) from e

    return models


def _reachable_sfi_ids(
    *, framework_uuid: uuid.UUID, relationships: Sequence[Relationship]
) -> set[str]:
    """Compute final SFI IDs reachable from the StandardsFramework root.

    Parameters
    ----------
    framework_uuid
        StandardsFramework root UUID.
    relationships
        Compiled hasChild relationships.

    Returns
    -------
    set[str]
        Reachable StandardsFrameworkItem UUID strings.
    """

    graph: dict[str, list[str]] = defaultdict(list)

    for relationship in relationships:
        graph[relationship.source_entity_value].append(relationship.target_entity_value)

    reachable: set[str] = set()
    stack = [str(framework_uuid)]

    while stack:
        node_id = stack.pop()

        for child_id in graph.get(node_id, []):
            if child_id in reachable:
                continue

            reachable.add(child_id)
            stack.append(child_id)

    return reachable


def _stable_json_key(value: Any) -> str:
    """Build a stable JSON comparison key for a JSON-compatible value.

    Parameters
    ----------
    value
        JSON-compatible value.

    Returns
    -------
    str
        Stable JSON string.
    """

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _unique_nonempty(values: Sequence[Any]) -> list[str]:
    """Return unique non-empty strings while preserving order.

    Parameters
    ----------
    values
        Raw values.

    Returns
    -------
    list[str]
        Unique non-empty strings.
    """

    cleaned: list[str] = []
    seen: set[str] = set()

    for value in values:
        value_clean = str(value or "").strip()

        if not value_clean or value_clean in seen:
            continue

        cleaned.append(value_clean)
        seen.add(value_clean)

    return cleaned


def _validate_count_alignment(
    *,
    has_child_edges: Sequence[SFIHasChildEdge],
    has_child_resolution_summary: SFIHasChildResolutionSummary,
    has_child_unresolved_edges: Sequence[SFIHasChildEdge],
    items: Sequence[StandardsFrameworkItem],
    relationships: Sequence[Relationship],
    sfi_final_records: Sequence[SFIFinalRecord],
    sfi_final_summary: SFIFinalSummary,
) -> list[str]:
    """Validate count consistency across artifacts.

    Parameters
    ----------
    has_child_edges
        hasChild edges.
    has_child_resolution_summary
        hasChild relationship-resolution summary.
    has_child_unresolved_edges
        hasChild unresolved edges.
    items
        Compiled SFI items.
    relationships
        Compiled hasChild relationships.
    sfi_final_records
        Final SFI records.
    sfi_final_summary
        SFI finalization summary.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []

    if len(sfi_final_records) != sfi_final_summary.final_sfi_count:
        errors.append(
            "sfi_final_summary.final_sfi_count does not match supplied "
            "sfi_final_records length."
        )

    if len(items) != len(sfi_final_records):
        errors.append("Compiled item count does not match final SFI record count.")

    if len(has_child_edges) != has_child_resolution_summary.edge_count:
        errors.append(
            "has_child_resolution_summary.edge_count does not match supplied "
            "has_child_edges length."
        )

    if len(relationships) != len(has_child_edges):
        errors.append("Compiled relationship count does not match hasChild edge count.")

    if has_child_resolution_summary.final_sfi_count != len(sfi_final_records):
        errors.append(
            "has_child_resolution_summary.final_sfi_count does not match final SFI "
            "record count."
        )

    unresolved_edge_count = sum(
        1 for edge in has_child_edges if edge.unresolved_root_fallback
    )

    if unresolved_edge_count != has_child_resolution_summary.unresolved_child_count:
        errors.append(
            "has_child_resolution_summary.unresolved_child_count does not match "
            "unresolved root-fallback edge count."
        )

    if len(has_child_unresolved_edges) != unresolved_edge_count:
        errors.append(
            "has_child_unresolved_edges length does not match unresolved root-fallback "
            "edge count."
        )

    root_edge_count = sum(1 for edge in has_child_edges if edge.is_root_edge)

    if root_edge_count != has_child_resolution_summary.root_edge_count:
        errors.append(
            "has_child_resolution_summary.root_edge_count does not match root edge count."
        )

    sfi_to_sfi_edge_count = sum(1 for edge in has_child_edges if not edge.is_root_edge)

    if sfi_to_sfi_edge_count != has_child_resolution_summary.sfi_to_sfi_edge_count:
        errors.append(
            "has_child_resolution_summary.sfi_to_sfi_edge_count does not match SFI-to-SFI edge count."
        )

    return errors


def _validate_export(
    *,
    framework: StandardsFramework,
    has_child_edges: Sequence[SFIHasChildEdge],
    has_child_resolution_summary: SFIHasChildResolutionSummary,
    has_child_unresolved_edges: Sequence[SFIHasChildEdge],
    input_fingerprints: dict[str, str],
    items: Sequence[StandardsFrameworkItem],
    relationships: Sequence[Relationship],
    sfi_final_records: Sequence[SFIFinalRecord],
    sfi_final_summary: SFIFinalSummary,
) -> AcademicStandardsValidationReport:
    """Validate the compiled Academic Standards KG export.

    Parameters
    ----------
    framework
        Compiled StandardsFramework object.
    has_child_edges
        Source hasChild edges.
    has_child_resolution_summary
        hasChild relationship-resolution summary.
    has_child_unresolved_edges
        hasChild unresolved root-fallback edges.
    input_fingerprints
        Stable fingerprints for all required inputs.
    items
        Compiled StandardsFrameworkItem records.
    relationships
        Compiled hasChild Relationship records.
    sfi_final_records
        Final SFI records.
    sfi_final_summary
        SFI finalization summary.

    Returns
    -------
    AcademicStandardsValidationReport
        Validation report with errors and count diagnostics.
    """

    errors: list[str] = []

    if not sfi_final_records:
        errors.append("Zero-final-SFI output is invalid for Academic Standards export.")

    if not items:
        errors.append(
            "Empty final KG output: no StandardsFrameworkItems were compiled."
        )

    errors.extend(
        _validate_count_alignment(
            has_child_edges=has_child_edges,
            has_child_resolution_summary=has_child_resolution_summary,
            has_child_unresolved_edges=has_child_unresolved_edges,
            items=items,
            relationships=relationships,
            sfi_final_records=sfi_final_records,
            sfi_final_summary=sfi_final_summary,
        )
    )
    errors.extend(
        _validate_graph_export(
            framework=framework, items=items, relationships=relationships
        )
    )
    errors.extend(
        _validate_sfi_exports(items=items, sfi_final_records=sfi_final_records)
    )

    object_counts = {
        "frameworks": 1,
        "has_child_edges_input": len(has_child_edges),
        "relationships_has_child": len(relationships),
        "sfi_final_records_input": len(sfi_final_records),
        "standards_framework_items": len(items),
        "unresolved_relationship_edges": len(has_child_unresolved_edges),
    }
    validation_checks = [
        "schema_validity",
        "single_framework",
        "framework_uuid_matches_root_edges",
        "sfi_item_coverage",
        "relationship_coverage",
        "endpoint_existence",
        "incoming_edge_coverage",
        "root_reachability",
        "self_loop_absence",
        "sfi_cycle_absence",
        "duplicate_relationship_id_absence",
        "duplicate_parent_child_pair_absence",
        "non_empty_source_backed_sfi_descriptions",
        "identifier_preservation",
        "provenance_presence",
        "no_code_identity_material_preservation",
        "summary_count_alignment",
        "unresolved_relationship_reporting",
        "zero_final_sfi_failure",
        "empty_final_kg_failure",
    ]
    return AcademicStandardsValidationReport(
        errors=errors,
        input_fingerprints=input_fingerprints,
        object_counts=object_counts,
        passed=not errors,
        validation_checks=validation_checks,
    )


def _validate_graph_export(
    *,
    framework: StandardsFramework,
    items: Sequence[StandardsFrameworkItem],
    relationships: Sequence[Relationship],
) -> list[str]:
    """Validate endpoint existence, coverage, reachability, and graph structure.

    Parameters
    ----------
    framework
        Single exported StandardsFramework object.
    items
        Exported StandardsFrameworkItem records.
    relationships
        Exported hasChild relationships.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []
    framework_uuid = framework.case_identifier_uuid
    item_ids = [str(item.case_identifier_uuid) for item in items]
    item_id_set = set(item_ids)
    valid_source_ids = item_id_set | {str(framework_uuid)}
    duplicate_item_ids = sorted(
        {item_id for item_id in item_ids if item_ids.count(item_id) > 1}
    )

    if duplicate_item_ids:
        errors.append(
            f"Duplicate StandardsFrameworkItem UUIDs detected: {duplicate_item_ids}."
        )

    if not relationships:
        errors.append("Empty final KG output: no hasChild relationships were compiled.")

    edge_pairs = [
        (relationship.source_entity_value, relationship.target_entity_value)
        for relationship in relationships
    ]
    duplicate_pairs = sorted(
        {pair for pair in edge_pairs if edge_pairs.count(pair) > 1}
    )

    if duplicate_pairs:
        errors.append(
            f"Duplicate hasChild parent/child edge pairs detected: {duplicate_pairs}."
        )

    relationship_ids = [str(relationship.identifier) for relationship in relationships]
    duplicate_relationship_ids = sorted(
        {
            relationship_id
            for relationship_id in relationship_ids
            if relationship_ids.count(relationship_id) > 1
        }
    )

    if duplicate_relationship_ids:
        errors.append(
            f"Duplicate relationship IDs detected: {duplicate_relationship_ids}."
        )

    for relationship in relationships:
        if relationship.relationship_type != "hasChild":
            errors.append(
                f"Unsupported final relationship type {relationship.relationship_type!r}; "
                f"Academic Standards exports only hasChild relationships."
            )

        if relationship.source_entity_value not in valid_source_ids:
            errors.append(
                f"hasChild source endpoint does not exist: {relationship.source_entity_value}."
            )

        if relationship.target_entity_value not in item_id_set:
            errors.append(
                f"hasChild target SFI does not exist: {relationship.target_entity_value}."
            )

        if relationship.source_entity_value == relationship.target_entity_value:
            errors.append(
                f"hasChild self-loop detected for SFI {relationship.target_entity_value}."
            )

        if (
            relationship.source_entity == "StandardsFramework"
            and relationship.source_entity_value != str(framework_uuid)
        ):
            errors.append(
                "Root hasChild edge source UUID does not match exported StandardsFramework UUID."
            )

    represented_child_ids = {
        relationship.target_entity_value for relationship in relationships
    }
    missing_child_ids = sorted(item_id_set - represented_child_ids)

    if missing_child_ids:
        errors.append(
            f"Final SFIs missing incoming hasChild edges: {missing_child_ids}."
        )

    cycles = _detect_sfi_cycles(relationships)

    if cycles:
        errors.append(f"SFI-to-SFI hasChild cycles detected: {cycles[:5]}.")

    reachable = _reachable_sfi_ids(
        framework_uuid=framework_uuid, relationships=relationships
    )
    unreachable = sorted(item_id_set - reachable)

    if unreachable:
        errors.append(
            f"Final SFIs are not reachable from StandardsFramework root: {unreachable}."
        )

    return errors


def _validate_model_equal(
    *, actual: BaseModel, artifact_label: str, expected: BaseModel
) -> None:
    """Validate exact equality between two model payloads.

    Parameters
    ----------
    actual
        Loaded model payload.
    artifact_label
        Human-readable artifact label for errors.
    expected
        Expected model payload.

    Raises
    ------
    ValueError
        If the model payloads differ.
    """

    if model_dump_key(actual) != model_dump_key(expected):
        raise ValueError(f"{artifact_label} does not match current output.")


def _validate_model_sequences_equal(
    *, actual: Sequence[BaseModel], artifact_label: str, expected: Sequence[BaseModel]
) -> None:
    """Validate exact equality between two model sequences.

    Parameters
    ----------
    actual
        Loaded model sequence.
    artifact_label
        Human-readable artifact label for errors.
    expected
        Expected model sequence.

    Raises
    ------
    ValueError
        If sequence lengths or payloads differ.
    """

    if len(actual) != len(expected):
        raise ValueError(
            f"{artifact_label} has {len(actual)} records, but expected {len(expected)}."
        )

    for index, (actual_model, expected_model) in enumerate(
        zip(actual, expected, strict=True), start=1
    ):
        if model_dump_key(actual_model) != model_dump_key(expected_model):
            raise ValueError(
                f"{artifact_label} record {index} does not match current output."
            )


def _validate_sfi_exports(
    *,
    items: Sequence[StandardsFrameworkItem],
    sfi_final_records: Sequence[SFIFinalRecord],
) -> list[str]:
    """Validate SFI export coverage, identifiers, descriptions, and provenance.

    Parameters
    ----------
    items
        Compiled StandardsFrameworkItem records.
    sfi_final_records
        Final SFI records.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []
    items_by_id = {str(item.case_identifier_uuid): item for item in items}

    for record in sfi_final_records:
        item = items_by_id.get(str(record.final_sfi_uuid))

        if item is None:
            errors.append(
                f"Missing exported StandardsFrameworkItem for final SFI {record.final_sfi_uuid}."
            )
            continue

        if item.case_identifier_uuid != record.case_identifier_uuid:
            errors.append(
                f"SFI {record.final_sfi_uuid} case_identifier_uuid was not preserved."
            )

        if item.identifier != record.identifier:
            errors.append(f"SFI {record.final_sfi_uuid} identifier was not preserved.")

        if item.case_identifier_uri != record.case_identifier_uri:
            errors.append(
                f"SFI {record.final_sfi_uuid} case_identifier_uri was not preserved."
            )

        if not item.description.strip():
            errors.append(f"SFI {record.final_sfi_uuid} has empty description text.")

        has_provenance = bool(
            record.candidate_source_refs
            or record.source_page_indexes
            or record.source_registry_candidate_ids
            or record.source_segment_ids
            or record.source_window_ids
        )

        if not has_provenance:
            errors.append(
                f"SFI {record.final_sfi_uuid} lacks source provenance or synthetic provenance explanation."
            )

        if record.normalized_statement_code is None:
            identity_metadata = item.metadata.get("identity", {})
            has_no_code_identity_material = bool(
                item.metadata.get("identity_key")
                and (
                    item.metadata.get("source_context_keys")
                    or identity_metadata.get("no_code_identity_family_key")
                )
            )

            if not has_no_code_identity_material:
                errors.append(
                    f"No-code SFI {record.final_sfi_uuid} does not preserve source-context/text identity material."
                )

    return errors


def _write_artifacts(
    *,
    bundle: AcademicStandardsKGBundle,
    bundle_fp: Path,
    entity_provenance: dict[str, Any],
    entity_provenance_fp: Path,
    framework: StandardsFramework,
    framework_fp: Path,
    items: Sequence[StandardsFrameworkItem],
    items_fp: Path,
    relationships: Sequence[Relationship],
    relationships_fp: Path,
    unresolved_items: AcademicStandardsUnresolvedItems,
    unresolved_items_fp: Path,
    validation_report: AcademicStandardsValidationReport,
    validation_report_fp: Path,
) -> None:
    """Write all final Academic Standards output artifacts.

    Parameters
    ----------
    bundle
        Complete final KG bundle.
    bundle_fp
        Bundle JSON path.
    entity_provenance
        Provenance dictionary.
    entity_provenance_fp
        Provenance JSON path.
    framework
        Compiled framework object.
    framework_fp
        Framework JSON path.
    items
        Compiled SFI item records.
    items_fp
        Items JSONL path.
    relationships
        Compiled hasChild relationships.
    relationships_fp
        Relationships JSONL path.
    unresolved_items
        Unresolved report.
    unresolved_items_fp
        Unresolved JSON path.
    validation_report
        Validation report.
    validation_report_fp
        Validation JSON path.
    """

    write_to_json(fp=bundle_fp, json_info=bundle.model_dump(mode="json"))
    write_to_json(fp=entity_provenance_fp, json_info=entity_provenance)
    write_to_json(fp=framework_fp, json_info=framework.model_dump(mode="json"))
    _append_jsonl_models(fp=items_fp, models=items)
    _append_jsonl_models(fp=relationships_fp, models=relationships)
    write_to_json(
        fp=unresolved_items_fp, json_info=unresolved_items.model_dump(mode="json")
    )
    write_to_json(
        fp=validation_report_fp, json_info=validation_report.model_dump(mode="json")
    )


def compile_academic_standards_kg(
    *,
    document_ir: DocumentIR,
    has_child_edges: Sequence[SFIHasChildEdge],
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
    kg_run_manifest: dict[str, Any] | None = None,
    overwrite: bool,
    sfi_final_records: Sequence[SFIFinalRecord],
) -> AcademicStandardsKGBundle:
    """Compile, validate, and write final Academic Standards KG artifacts.

    Parameters
    ----------
    document_ir
        Source stitched DocumentIR.
    has_child_edges
        Validated hasChild edges.
    kg_config
        Runtime KG creation configuration.
    kg_dirs
        KG artifact directory wrapper.
    kg_run_manifest
        KG run manifest. Loaded from disk when omitted.
    overwrite
        Whether to rebuild artifacts even if exact current artifacts exist.
    sfi_final_records
        Final SFI records.

    Returns
    -------
    AcademicStandardsKGBundle
        Complete compiled final KG bundle.

    Raises
    ------
    ValueError
        If final KG validation fails.
    """

    make_dir(kg_dirs.root)
    bundle_fp = kg_dirs.root / "academic_standards_kg_bundle.json"
    entity_provenance_fp = kg_dirs.root / "entity_provenance.json"
    relationships_fp = kg_dirs.root / "relationships_has_child.jsonl"
    sf_fp = kg_dirs.root / "standards_framework.json"
    sfi_fp = kg_dirs.root / "standards_framework_items.jsonl"
    unresolved_items_fp = kg_dirs.root / "unresolved_items.json"
    validation_report_fp = kg_dirs.root / "validation_report.json"

    kg_run_manifest_loaded = kg_run_manifest or open_json_type(
        kg_dirs.root / "kg_run_manifest.json"
    )
    sfi_final_summary_loaded = SFIFinalSummary.model_validate(
        open_json_type(kg_dirs.root / "sfi_final_summary.json")
    )
    has_child_resolution_summary_loaded = SFIHasChildResolutionSummary.model_validate(
        open_json_type(kg_dirs.root / "has_child_resolution_summary.json")
    )
    has_child_unresolved_edges_loaded = _load_json_model_sequence(
        fp=kg_dirs.root / "has_child_unresolved_edges.json", model_type=SFIHasChildEdge
    )

    sfi_final_summary_typed = SFIFinalSummary.model_validate(sfi_final_summary_loaded)
    has_child_resolution_summary_typed = SFIHasChildResolutionSummary.model_validate(
        has_child_resolution_summary_loaded
    )
    has_child_unresolved_edges_typed = [
        SFIHasChildEdge.model_validate(edge)
        for edge in has_child_unresolved_edges_loaded
    ]

    framework = _build_framework(
        document_ir=document_ir,
        kg_config=kg_config,
        kg_run_manifest=kg_run_manifest_loaded,
    )
    items = [
        StandardsFrameworkItem(
            academic_subject=record.academic_subject,
            attribution_statement=record.attribution_statement,
            author=record.author,
            case_identifier_uri=record.case_identifier_uri,
            case_identifier_uuid=record.case_identifier_uuid,
            description=record.description,
            grade_level=_extract_grade_levels(record),
            identifier=record.identifier,
            in_language=record.in_language,
            jurisdiction=record.jurisdiction,
            license=record.license,
            metadata={
                "audit_flags": record.audit_flags,
                "audit_notes": record.audit_notes,
                "audit_peer_merge_group_ids": record.audit_peer_merge_group_ids,
                "candidate_descriptions": record.candidate_descriptions,
                "candidate_source_refs": record.candidate_source_refs,
                "candidate_source_texts": record.candidate_source_texts,
                "canonical_statement_scope_key": record.canonical_statement_scope_key,
                "canonical_statement_value": record.canonical_statement_value,
                "canonical_statement_value_key": record.canonical_statement_value_key,
                "confidence_max": record.confidence_max,
                "confidence_min": record.confidence_min,
                "final_sfi_uuid": str(record.final_sfi_uuid),
                "identity": record.metadata.get("identity", {}),
                "identity_key": record.identity_key,
                "language": record.language,
                "merge_decision": record.merge_decision,
                "merge_group_id": record.merge_group_id,
                "merge_reason": record.merge_reason,
                "normalized_statement_code": record.normalized_statement_code,
                "source_context_keys": record.source_context_keys,
                "source_page_indexes": record.source_page_indexes,
                "source_registry_candidate_ids": record.source_registry_candidate_ids,
                "source_segment_ids": record.source_segment_ids,
                "source_window_ids": record.source_window_ids,
                "source_window_indexes": record.source_window_indexes,
                "statement_value_canonicalization": record.metadata.get(
                    "statement_value_canonicalization", {}
                ),
            },
            normalized_statement_type=record.normalized_statement_type,
            provider=record.provider,
            statement_code=record.statement_code,
            statement_type=record.statement_type,
        )
        for record in sfi_final_records
    ]
    relationships = _compile_relationships(
        has_child_edges=has_child_edges, kg_config=kg_config
    )
    unresolved_items = _build_unresolved_items(
        has_child_unresolved_edges=has_child_unresolved_edges_typed,
        sfi_final_summary=sfi_final_summary_typed,
    )
    input_fingerprints = _build_input_fingerprints(
        has_child_edges=has_child_edges,
        has_child_resolution_summary=has_child_resolution_summary_typed,
        has_child_unresolved_edges=has_child_unresolved_edges_typed,
        kg_run_manifest=kg_run_manifest_loaded,
        sfi_final_records=sfi_final_records,
        sfi_final_summary=sfi_final_summary_typed,
    )
    validation_report = _validate_export(
        framework=framework,
        has_child_edges=has_child_edges,
        has_child_resolution_summary=has_child_resolution_summary_typed,
        has_child_unresolved_edges=has_child_unresolved_edges_typed,
        input_fingerprints=input_fingerprints,
        items=items,
        relationships=relationships,
        sfi_final_records=sfi_final_records,
        sfi_final_summary=sfi_final_summary_typed,
    )
    entity_provenance = _build_entity_provenance(
        document_ir=document_ir,
        framework=framework,
        items=items,
        kg_run_manifest=kg_run_manifest_loaded,
        relationships=relationships,
        sfi_final_records=sfi_final_records,
    )
    bundle = _build_bundle(
        entity_provenance=entity_provenance,
        framework=framework,
        items=items,
        relationships=relationships,
        unresolved_items=unresolved_items,
        validation_report=validation_report,
    )

    if not validation_report.passed:
        _write_artifacts(
            bundle=bundle,
            bundle_fp=bundle_fp,
            entity_provenance=entity_provenance,
            entity_provenance_fp=entity_provenance_fp,
            framework=framework,
            framework_fp=sf_fp,
            items=items,
            items_fp=sfi_fp,
            relationships=relationships,
            relationships_fp=relationships_fp,
            unresolved_items=unresolved_items,
            unresolved_items_fp=unresolved_items_fp,
            validation_report=validation_report,
            validation_report_fp=validation_report_fp,
        )
        raise ValueError(
            "Final Academic Standards KG export validation failed: "
            f"{validation_report.errors}"
        )

    if not overwrite:
        existing_bundle = _load_complete_existing_export_artifacts(
            bundle=bundle,
            bundle_fp=bundle_fp,
            entity_provenance=entity_provenance,
            entity_provenance_fp=entity_provenance_fp,
            framework=framework,
            framework_fp=sf_fp,
            items=items,
            items_fp=sfi_fp,
            relationships=relationships,
            relationships_fp=relationships_fp,
            unresolved_items=unresolved_items,
            unresolved_items_fp=unresolved_items_fp,
            validation_report=validation_report,
            validation_report_fp=validation_report_fp,
        )

        if existing_bundle is not None:
            return existing_bundle
    else:
        logger.info(
            "Rebuilding final Academic Standards KG export because overwrite=True."
        )

    reset_output_files(
        output_fps=[
            bundle_fp,
            entity_provenance_fp,
            relationships_fp,
            sf_fp,
            sfi_fp,
            unresolved_items_fp,
            validation_report_fp,
        ]
    )
    _write_artifacts(
        bundle=bundle,
        bundle_fp=bundle_fp,
        entity_provenance=entity_provenance,
        entity_provenance_fp=entity_provenance_fp,
        framework=framework,
        framework_fp=sf_fp,
        items=items,
        items_fp=sfi_fp,
        relationships=relationships,
        relationships_fp=relationships_fp,
        unresolved_items=unresolved_items,
        unresolved_items_fp=unresolved_items_fp,
        validation_report=validation_report,
        validation_report_fp=validation_report_fp,
    )

    return bundle
