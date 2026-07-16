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

from collections import Counter, defaultdict
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


def _build_entity_provenance(
    *,
    document_ir: DocumentIR,
    kg_run_manifest: dict[str, Any],
    relationships: Sequence[Relationship],
    sf: StandardsFramework,
    sfi_final_records: Sequence[SFIFinalRecord],
    sfis: Sequence[StandardsFrameworkItem],
) -> dict[str, Any]:
    """Build provenance for exported StandardsFramework, StandardsFrameworkItem, and
    relationship entities.

    Parameters
    ----------
    document_ir
        Source stitched DocumentIR.
    kg_run_manifest
        Persisted KG run manifest.
    relationships
        Exported hasChild relationships.
    sf
        Exported StandardsFramework object.
    sfi_final_records
        Source final SFI records aligned to exported items.
    sfis
        Exported StandardsFrameworkItem objects.

    Returns
    -------
    dict[str, Any]
        Deterministic provenance artifact for final KG entities.
    """

    items_provenance: dict[str, Any] = {}
    records_by_id = {str(record.final_sfi_uuid): record for record in sfi_final_records}

    for item in sfis:
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
            "case_identifier_uri": sf.case_identifier_uri,
            "case_identifier_uuid": str(sf.case_identifier_uuid),
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


def _collect_candidate_scope_value_maps(record: SFIFinalRecord) -> list[dict[str, Any]]:
    """Collect candidate-level identity-scope value maps from a final SFI record.

    Only dict-shaped `candidate_source_refs` entries that carry a dict
    `identity_scope_values` payload are retained, preserving source order.

    Parameters
    ----------
    record
        Final SFI record to inspect.

    Returns
    -------
    list[dict[str, Any]]
        Candidate identity-scope value maps in source order.
    """

    candidate_scope_value_maps: list[dict[str, Any]] = []

    for source_ref in record.candidate_source_refs:
        identity_scope_values = (
            source_ref.get("identity_scope_values")
            if isinstance(source_ref, dict)
            else None
        )

        if isinstance(identity_scope_values, dict):
            candidate_scope_value_maps.append(identity_scope_values)

    return candidate_scope_value_maps


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


def _extract_grade_levels(
    *, grade_level_statement_types: Sequence[str], record: SFIFinalRecord
) -> list[str]:
    """Recover configured LC grade-level values from one final SFI record.

    Runtime configuration supplies the curriculum-to-export mapping. For each
    configured canonical statement type, the function uses the record's own canonical
    value when the record represents that organizer and the matching final-record
    identity-scope value. Candidate-level identity scopes are consulted only when
    neither authoritative source is available. It does not infer grade-like meaning
    from statement-type wording, hierarchy position, or framework metadata.

    Parameters
    ----------
    grade_level_statement_types
        Ordered canonical statement_type labels mapped to
        StandardsFrameworkItem.grade_level.
    record
        Final SFI record to inspect.

    Returns
    -------
    list[str]
        Stable, de-duplicated grade-level values in configured statement-type order.
    """

    candidate_scope_value_maps = _collect_candidate_scope_value_maps(record)

    grade_levels: dict[str, None] = {}

    for statement_type in grade_level_statement_types:
        authoritative_values: dict[str, None] = {}

        if record.statement_type == statement_type:
            own_value = str(
                record.canonical_statement_value or record.description or ""
            ).strip()

            if own_value:
                authoritative_values[own_value] = None

        final_scope_value = str(
            record.identity_scope_values.get(statement_type) or ""
        ).strip()

        if final_scope_value:
            authoritative_values[final_scope_value] = None

        if not authoritative_values:
            for scope_values in candidate_scope_value_maps:
                candidate_scope_value = str(
                    scope_values.get(statement_type) or ""
                ).strip()

                if candidate_scope_value:
                    authoritative_values[candidate_scope_value] = None

        grade_levels.update(authoritative_values)

    return list(grade_levels)


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
    framework_fp: Path,
    items_fp: Path,
    relationships: Sequence[Relationship],
    relationships_fp: Path,
    sf: StandardsFramework,
    sfis: Sequence[StandardsFrameworkItem],
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
    framework_fp
        Persisted framework path.
    items_fp
        Persisted item JSONL path.
    relationships
        Expected relationship sequence.
    relationships_fp
        Persisted relationship JSONL path.
    sf
        Expected framework object.
    sfis
        Expected item sequence.
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
            expected=sf,
        )
        loaded_items = _load_jsonl_models(
            fp=items_fp, model_type=StandardsFrameworkItem
        )
        _validate_model_sequences_equal(
            actual=loaded_items,
            artifact_label="standards_framework_items.jsonl",
            expected=sfis,
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

        if json.dumps(loaded_entity_provenance, sort_keys=True) != json.dumps(
            entity_provenance, sort_keys=True
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
    *, relationships: Sequence[Relationship], sf_uuid: uuid.UUID
) -> set[str]:
    """Compute final SFI IDs reachable from the StandardsFramework root.

    Parameters
    ----------
    relationships
        Compiled hasChild relationships.
    sf_uuid
        StandardsFramework root UUID.

    Returns
    -------
    set[str]
        Reachable StandardsFrameworkItem UUID strings.
    """

    graph: dict[str, list[str]] = defaultdict(list)

    for relationship in relationships:
        graph[relationship.source_entity_value].append(relationship.target_entity_value)

    reachable: set[str] = set()
    stack = [str(sf_uuid)]

    while stack:
        node_id = stack.pop()

        for child_id in graph.get(node_id, []):
            if child_id in reachable:
                continue

            reachable.add(child_id)
            stack.append(child_id)

    return reachable


def _validate_count_alignment(
    *,
    has_child_edges: Sequence[SFIHasChildEdge],
    has_child_resolution_summary: SFIHasChildResolutionSummary,
    has_child_unresolved_edges: Sequence[SFIHasChildEdge],
    relationships: Sequence[Relationship],
    sfi_final_records: Sequence[SFIFinalRecord],
    sfi_final_summary: SFIFinalSummary,
    sfis: Sequence[StandardsFrameworkItem],
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
    relationships
        Compiled hasChild relationships.
    sfi_final_records
        Final SFI records.
    sfi_final_summary
        SFI finalization summary.
    sfis
        Compiled SFI items.

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

    if len(sfis) != len(sfi_final_records):
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

    errors.extend(
        _validate_unresolved_edges_match_source_edges(
            has_child_edges=has_child_edges,
            has_child_unresolved_edges=has_child_unresolved_edges,
        )
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


def _validate_coverage_and_reachability(
    *, relationships: Sequence[Relationship], sf_uuid: uuid.UUID, sfi_id_set: set[str]
) -> list[str]:
    """Validate incoming-edge coverage, cycle absence, and root reachability.

    Parameters
    ----------
    relationships
        Exported hasChild relationships.
    sf_uuid
        UUID of the exported StandardsFramework object.
    sfi_id_set
        Set of exported StandardsFrameworkItem UUIDs.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []

    represented_child_ids = {
        relationship.target_entity_value for relationship in relationships
    }
    missing_child_ids = sorted(sfi_id_set - represented_child_ids)

    if missing_child_ids:
        errors.append(
            f"Final SFIs missing incoming hasChild edges: {missing_child_ids}."
        )

    cycles = _detect_sfi_cycles(relationships)

    if cycles:
        errors.append(f"SFI-to-SFI hasChild cycles detected: {cycles[:5]}.")

    reachable = _reachable_sfi_ids(relationships=relationships, sf_uuid=sf_uuid)
    unreachable = sorted(sfi_id_set - reachable)

    if unreachable:
        errors.append(
            f"Final SFIs are not reachable from StandardsFramework root: {unreachable}."
        )

    return errors


def _validate_export(
    *,
    grade_level_statement_types: Sequence[str],
    has_child_edges: Sequence[SFIHasChildEdge],
    has_child_resolution_summary: SFIHasChildResolutionSummary,
    has_child_unresolved_edges: Sequence[SFIHasChildEdge],
    input_fingerprints: dict[str, str],
    relationships: Sequence[Relationship],
    sf: StandardsFramework,
    sfi_final_records: Sequence[SFIFinalRecord],
    sfi_final_summary: SFIFinalSummary,
    sfis: Sequence[StandardsFrameworkItem],
) -> AcademicStandardsValidationReport:
    """Validate the compiled Academic Standards KG export.

    Parameters
    ----------
    grade_level_statement_types
        Ordered canonical statement types mapped to LC grade_level output.
    has_child_edges
        Source hasChild edges.
    has_child_resolution_summary
        hasChild relationship-resolution summary.
    has_child_unresolved_edges
        hasChild unresolved root-fallback edges.
    input_fingerprints
        Stable fingerprints for all required inputs.
    relationships
        Compiled hasChild Relationship records.
    sf
        Compiled StandardsFramework object.
    sfi_final_records
        Final SFI records.
    sfi_final_summary
        SFI finalization summary.
    sfis
        Compiled StandardsFrameworkItem records.

    Returns
    -------
    AcademicStandardsValidationReport
        Validation report with errors and count diagnostics.
    """

    errors: list[str] = []

    if not sfi_final_records:
        errors.append("Zero final SFI output is invalid for Academic Standards export.")

    if not sfis:
        errors.append(
            "Empty final KG output: no StandardsFrameworkItems were compiled."
        )

    errors.extend(
        _validate_count_alignment(
            has_child_edges=has_child_edges,
            has_child_resolution_summary=has_child_resolution_summary,
            has_child_unresolved_edges=has_child_unresolved_edges,
            relationships=relationships,
            sfi_final_records=sfi_final_records,
            sfi_final_summary=sfi_final_summary,
            sfis=sfis,
        )
    )
    errors.extend(_validate_graph_export(relationships=relationships, sf=sf, sfis=sfis))
    errors.extend(
        _validate_sfi_exports(
            grade_level_statement_types=grade_level_statement_types,
            sfi_final_records=sfi_final_records,
            sfis=sfis,
        )
    )

    object_counts = {
        "frameworks": 1,
        "has_child_edges_input": len(has_child_edges),
        "relationships_has_child": len(relationships),
        "sfi_final_records_input": len(sfi_final_records),
        "standards_framework_items": len(sfis),
        "unresolved_relationship_edges": len(has_child_unresolved_edges),
    }
    validation_checks = [
        "configured_grade_level_mapping",
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
    relationships: Sequence[Relationship],
    sf: StandardsFramework,
    sfis: Sequence[StandardsFrameworkItem],
) -> list[str]:
    """Validate endpoint existence, coverage, reachability, and graph structure.

    Parameters
    ----------
    relationships
        Exported hasChild relationships.
    sf
        Single exported StandardsFramework object.
    sfis
        Exported StandardsFrameworkItem records.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []
    sf_uuid = sf.case_identifier_uuid
    sfi_ids = [str(item.case_identifier_uuid) for item in sfis]
    sfi_id_set = set(sfi_ids)

    # Check for item uniqueness.
    if len(sfi_ids) != len(sfi_id_set):
        duplicate_item_ids = sorted(
            {item_id for item_id in sfi_ids if sfi_ids.count(item_id) > 1}
        )
        errors.append(
            f"Duplicate StandardsFrameworkItem UUIDs detected: {duplicate_item_ids}."
        )

    # Validate relationship presence and uniqueness.
    if not relationships:
        errors.append("Empty final KG output: no hasChild relationships were compiled.")
    else:
        edge_pairs = [
            (rel.source_entity_value, rel.target_entity_value) for rel in relationships
        ]

        if len(edge_pairs) != len(set(edge_pairs)):
            duplicate_pairs = sorted(
                {pair for pair in edge_pairs if edge_pairs.count(pair) > 1}
            )
            errors.append(
                f"Duplicate hasChild parent/child edge pairs detected: {duplicate_pairs}."
            )

        relationship_ids = [str(rel.identifier) for rel in relationships]

        if len(relationship_ids) != len(set(relationship_ids)):
            duplicate_relationship_ids = sorted(
                {
                    rel_id
                    for rel_id in relationship_ids
                    if relationship_ids.count(rel_id) > 1
                }
            )
            errors.append(
                f"Duplicate relationship IDs detected: {duplicate_relationship_ids}."
            )

    # Validate endpoints, coverage, and reachability.
    errors.extend(
        _validate_relationship_endpoints(
            relationships=relationships, sf_uuid=sf_uuid, sfi_id_set=sfi_id_set
        )
    )
    errors.extend(
        _validate_coverage_and_reachability(
            relationships=relationships, sf_uuid=sf_uuid, sfi_id_set=sfi_id_set
        )
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


def _validate_relationship_endpoints(
    *, relationships: Sequence[Relationship], sf_uuid: uuid.UUID, sfi_id_set: set[str]
) -> list[str]:
    """Validate the type, endpoints, and root binding of every relationship.

    Parameters
    ----------
    relationships
        Exported hasChild relationships.
    sf_uuid
        UUID of the exported StandardsFramework object.
    sfi_id_set
        Set of exported StandardsFrameworkItem UUIDs.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []
    sf_uuid_str = str(sf_uuid)
    valid_source_ids = sfi_id_set | {sf_uuid_str}

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

        if relationship.target_entity_value not in sfi_id_set:
            errors.append(
                f"hasChild target SFI does not exist: {relationship.target_entity_value}."
            )

        if relationship.source_entity_value == relationship.target_entity_value:
            errors.append(
                f"hasChild self-loop detected for SFI {relationship.target_entity_value}."
            )

        if (
            relationship.source_entity == "StandardsFramework"
            and relationship.source_entity_value != sf_uuid_str
        ):
            errors.append(
                "Root hasChild edge source UUID does not match exported StandardsFramework UUID."
            )

    return errors


def _validate_sfi_export_record(
    *,
    grade_level_statement_types: Sequence[str],
    item: StandardsFrameworkItem,
    record: SFIFinalRecord,
) -> list[str]:
    """Validate one matched StandardsFrameworkItem against its final SFI record.

    Check that identifiers and URIs are preserved, that the grade_level matches the
    configured export mapping, that description text is present, and that source
    provenance (or no-code identity material) is retained.

    Parameters
    ----------
    grade_level_statement_types
        Ordered canonical statement types mapped to LC grade_level output.
    item
        Compiled StandardsFrameworkItem matched to `record`.
    record
        Final SFI record backing `item`.

    Returns
    -------
    list[str]
        Validation error messages for this record.
    """

    errors: list[str] = []

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

    expected_grade_levels = _extract_grade_levels(
        grade_level_statement_types=grade_level_statement_types, record=record
    )

    if item.grade_level != expected_grade_levels:
        errors.append(
            f"SFI {record.final_sfi_uuid} grade_level does not match the "
            f"configured export mapping; expected={expected_grade_levels!r}, "
            f"actual={item.grade_level!r}."
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
            f"SFI {record.final_sfi_uuid} lacks source provenance or synthetic "
            f"provenance explanation."
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
                f"No-code SFI {record.final_sfi_uuid} does not preserve "
                f"source-context/text identity material."
            )

    return errors


def _validate_sfi_exports(
    *,
    grade_level_statement_types: Sequence[str],
    sfi_final_records: Sequence[SFIFinalRecord],
    sfis: Sequence[StandardsFrameworkItem],
) -> list[str]:
    """Validate SFI export coverage, identifiers, descriptions, and provenance.

    Parameters
    ----------
    grade_level_statement_types
        Ordered canonical statement types mapped to LC grade_level output.
    sfi_final_records
        Final SFI records.
    sfis
        Compiled StandardsFrameworkItem records.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []
    sfis_by_id = {str(item.case_identifier_uuid): item for item in sfis}

    for record in sfi_final_records:
        item = sfis_by_id.get(str(record.final_sfi_uuid))

        if item is None:
            errors.append(
                f"Missing exported StandardsFrameworkItem for final SFI {record.final_sfi_uuid}."
            )
            continue

        errors.extend(
            _validate_sfi_export_record(
                grade_level_statement_types=grade_level_statement_types,
                item=item,
                record=record,
            )
        )

    return errors


def _validate_unresolved_edges_match_source_edges(
    *,
    has_child_edges: Sequence[SFIHasChildEdge],
    has_child_unresolved_edges: Sequence[SFIHasChildEdge],
) -> list[str]:
    """Validate unresolved hasChild edges match the source edge subset exactly.

    The unresolved-edge artifact must contain exactly the same edge records as the
    subset of final hasChild edges whose `unresolved_root_fallback` flag is true.
    Relationship IDs are used as stable edge identifiers, and matching IDs must also
    have identical serialized payloads.

    Parameters
    ----------
    has_child_edges
        Complete final hasChild edge sequence supplied to the export compiler.
    has_child_unresolved_edges
        Persisted unresolved hasChild edge sequence.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []
    expected_ids = [
        str(edge.relationship_id)
        for edge in has_child_edges
        if edge.unresolved_root_fallback
    ]
    actual_ids = [str(edge.relationship_id) for edge in has_child_unresolved_edges]

    duplicate_expected_ids = sorted(
        edge_id for edge_id, count in Counter(expected_ids).items() if count > 1
    )
    duplicate_actual_ids = sorted(
        edge_id for edge_id, count in Counter(actual_ids).items() if count > 1
    )

    if duplicate_expected_ids:
        errors.append(
            f"Duplicate unresolved root-fallback relationship IDs found in "
            f"has_child_edges: {duplicate_expected_ids}."
        )

    if duplicate_actual_ids:
        errors.append(
            f"Duplicate relationship IDs found in has_child_unresolved_edges: "
            f"{duplicate_actual_ids}."
        )

    expected_by_id = {
        str(edge.relationship_id): model_dump_key(edge)
        for edge in has_child_edges
        if edge.unresolved_root_fallback
    }
    actual_by_id = {
        str(edge.relationship_id): model_dump_key(edge)
        for edge in has_child_unresolved_edges
    }

    missing_ids = sorted(set(expected_by_id) - set(actual_by_id))
    extra_ids = sorted(set(actual_by_id) - set(expected_by_id))
    mismatched_ids = sorted(
        edge_id
        for edge_id in set(expected_by_id) & set(actual_by_id)
        if expected_by_id[edge_id] != actual_by_id[edge_id]
    )

    if missing_ids:
        errors.append(
            f"has_child_unresolved_edges is missing unresolved root-fallback edges: "
            f"{missing_ids}."
        )

    if extra_ids:
        errors.append(
            f"has_child_unresolved_edges contains edges that are not unresolved "
            f"root-fallback edges in has_child_edges: {extra_ids}."
        )

    if mismatched_ids:
        errors.append(
            f"has_child_unresolved_edges contains payloads that do not exactly match "
            f"the corresponding unresolved root-fallback edges in has_child_edges: "
            f"{mismatched_ids}."
        )

    return errors


def _write_artifacts(
    *,
    bundle: AcademicStandardsKGBundle,
    bundle_fp: Path,
    entity_provenance: dict[str, Any],
    entity_provenance_fp: Path,
    relationships: Sequence[Relationship],
    relationships_fp: Path,
    sf: StandardsFramework,
    sf_fp: Path,
    sfis: Sequence[StandardsFrameworkItem],
    sfis_fp: Path,
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
    relationships
        Compiled hasChild relationships.
    relationships_fp
        Relationships JSONL path.
    sf
        Compiled framework object.
    sf_fp
        Framework JSON path.
    sfis
        Compiled SFI item records.
    sfis_fp
        Items JSONL path.
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
    write_to_json(fp=sf_fp, json_info=sf.model_dump(mode="json"))

    make_dir(sfis_fp.parent)
    sfis_fp.write_text("", encoding="utf-8")

    for sfi in sfis:
        append_jsonl_model(fp=sfis_fp, model=sfi)

    make_dir(relationships_fp.parent)
    relationships_fp.write_text("", encoding="utf-8")

    for relationship in relationships:
        append_jsonl_model(fp=relationships_fp, model=relationship)

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
    overwrite: bool,
) -> AcademicStandardsKGBundle:
    """Compile, validate, and write final Academic Standards KG artifacts.

    The process is as follows:

    1. Create artifact filepaths.
    2. Load artifacts from previous steps.
    3. Build StandardsFramework (i.e., root) node.
    4. Build StandardsFrameworkItems (i.e., SFI) nodes.
    5. Compile hasChild edges into Relationship objects.
    6. Build the unresolved report.
    7. Build stable fingerprints for input payloads.
    8. Validate the compiled Academic Standards KG export.
    9. Build provenance for exported StandardsFramework, StandardsFrameworkItem, and
        relationship entities.
    10. Build the Academic Standards export summary and KG bundle.
    11. If the export failed, then persist the failed export and validation report for
        inspection, and then fail the run so invalid KG artifacts are not treated as
        successful output.
    12. Otherwise, reuse existing final export artifacts only when the full persisted
        payload exactly matches the freshly compiled bundle and prior validation passed.
    13. Finally, rebuild the final export artifacts from the freshly compiled,
        validated objects, replacing any stale partial or previous outputs.

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
    overwrite
        Whether to rebuild artifacts even if exact current artifacts exist.

    Returns
    -------
    AcademicStandardsKGBundle
        Complete compiled final KG bundle.

    Raises
    ------
    ValueError
        If final KG validation fails.
    """

    # 1.
    make_dir(kg_dirs.root)
    bundle_fp = kg_dirs.root / "academic_standards_kg_bundle.json"
    entity_provenance_fp = kg_dirs.root / "entity_provenance.json"
    relationships_fp = kg_dirs.root / "relationships_has_child.jsonl"
    sf_fp = kg_dirs.root / "standards_framework.json"
    sfi_fp = kg_dirs.root / "standards_framework_items.jsonl"
    unresolved_items_fp = kg_dirs.root / "unresolved_items.json"
    validation_report_fp = kg_dirs.root / "validation_report.json"

    # 2.
    has_child_resolution_summary = SFIHasChildResolutionSummary.model_validate(
        open_json_type(kg_dirs.root / "has_child_resolution_summary.json")
    )
    has_child_unresolved_edges = [
        SFIHasChildEdge.model_validate(edge)
        for edge in open_json_type(kg_dirs.root / "has_child_unresolved_edges.json")
    ]
    grade_level_statement_types = (
        kg_config.academic_standards.grade_level_statement_types
    )
    kg_run_manifest = open_json_type(kg_dirs.root / "kg_run_manifest.json")
    metadata = kg_config.metadata
    sf_uuid = build_standards_framework_uuid(document_ir.doc_key)
    sfi_final_records = [
        SFIFinalRecord.model_validate(record)
        for record in open_json_type(kg_dirs.root / "sfi_final_records.json")
    ]
    sfi_final_summary = SFIFinalSummary.model_validate(
        open_json_type(kg_dirs.root / "sfi_final_summary.json")
    )

    # 3.
    sf = StandardsFramework(
        academic_subject=metadata.subject,
        adoption_status=metadata.adoption_status or "Unknown",
        attribution_statement=metadata.attribution_statement,
        author=metadata.author,
        case_identifier_uri=f"urn:uuid:{sf_uuid}",
        case_identifier_uuid=sf_uuid,
        description=None,
        identifier=sf_uuid,
        in_language=metadata.primary_language,
        jurisdiction=metadata.jurisdiction,
        license=metadata.license,
        metadata={
            "country": metadata.country,
            "doc_key": document_ir.doc_key,
            "framework_title": metadata.framework_title,
            "grades_or_stages": metadata.grades_or_stages,
            "languages": metadata.languages,
            "page_count": document_ir.page_count,
            "pdf_name": document_ir.pdf_name,
            "primary_language": metadata.primary_language,
        },
        name=metadata.framework_title,
        notes=None,
        provider=metadata.provider,
    )

    # 4.
    sfis = [
        StandardsFrameworkItem(
            academic_subject=record.academic_subject,
            attribution_statement=record.attribution_statement,
            author=record.author,
            case_identifier_uri=record.case_identifier_uri,
            case_identifier_uuid=record.case_identifier_uuid,
            description=record.description,
            grade_level=_extract_grade_levels(
                grade_level_statement_types=grade_level_statement_types, record=record
            ),
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
                "canonical_statement_value": record.canonical_statement_value,
                "canonical_statement_value_key": record.canonical_statement_value_key,
                "confidence_max": record.confidence_max,
                "confidence_min": record.confidence_min,
                "final_sfi_uuid": str(record.final_sfi_uuid),
                "identity": record.metadata.get("identity", {}),
                "identity_key": record.identity_key,
                "identity_scope_key": record.identity_scope_key,
                "identity_scope_values": record.identity_scope_values,
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

    # 5.
    relationships = [
        Relationship(
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
                    str(edge.parent_final_sfi_uuid)
                    if edge.parent_final_sfi_uuid
                    else None
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
        for edge in has_child_edges
    ]

    # 6.
    unresolved_items = AcademicStandardsUnresolvedItems(
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

    # 7.
    input_fingerprints = {
        "grade_level_statement_types": _fingerprint_jsonable(
            grade_level_statement_types
        ),
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

    # 8.
    validation_report = _validate_export(
        grade_level_statement_types=grade_level_statement_types,
        has_child_edges=has_child_edges,
        has_child_resolution_summary=has_child_resolution_summary,
        has_child_unresolved_edges=has_child_unresolved_edges,
        input_fingerprints=input_fingerprints,
        relationships=relationships,
        sf=sf,
        sfi_final_records=sfi_final_records,
        sfi_final_summary=sfi_final_summary,
        sfis=sfis,
    )

    # 9.
    entity_provenance = _build_entity_provenance(
        document_ir=document_ir,
        kg_run_manifest=kg_run_manifest,
        relationships=relationships,
        sf=sf,
        sfi_final_records=sfi_final_records,
        sfis=sfis,
    )

    # 10.
    summary = AcademicStandardsExportSummary(
        final_sfi_count=len(sfis),
        finalization_exclusion_summary=unresolved_items.finalization_exclusion_summary,
        framework_count=1,
        has_child_relationship_count=len(relationships),
        relationship_unresolved_edge_count=len(
            unresolved_items.relationship_unresolved_edges
        ),
    )
    bundle = AcademicStandardsKGBundle(
        entity_provenance=entity_provenance,
        framework=sf,
        items=sfis,
        relationships_has_child=list(relationships),
        summary=summary,
        unresolved_items=unresolved_items,
        validation_report=validation_report,
    )

    if not validation_report.passed:
        # 11.
        _write_artifacts(
            bundle=bundle,
            bundle_fp=bundle_fp,
            entity_provenance=entity_provenance,
            entity_provenance_fp=entity_provenance_fp,
            relationships=relationships,
            relationships_fp=relationships_fp,
            sf=sf,
            sf_fp=sf_fp,
            sfis=sfis,
            sfis_fp=sfi_fp,
            unresolved_items=unresolved_items,
            unresolved_items_fp=unresolved_items_fp,
            validation_report=validation_report,
            validation_report_fp=validation_report_fp,
        )
        raise ValueError(
            f"Final Academic Standards KG export validation failed: "
            f"{validation_report.errors}"
        )

    if not overwrite:
        # 12.
        existing_bundle = _load_complete_existing_export_artifacts(
            bundle=bundle,
            bundle_fp=bundle_fp,
            entity_provenance=entity_provenance,
            entity_provenance_fp=entity_provenance_fp,
            framework_fp=sf_fp,
            items_fp=sfi_fp,
            relationships=relationships,
            relationships_fp=relationships_fp,
            sf=sf,
            sfis=sfis,
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

    # 13.
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
        relationships=relationships,
        relationships_fp=relationships_fp,
        sf=sf,
        sf_fp=sf_fp,
        sfis=sfis,
        sfis_fp=sfi_fp,
        unresolved_items=unresolved_items,
        unresolved_items_fp=unresolved_items_fp,
        validation_report=validation_report,
        validation_report_fp=validation_report_fp,
    )

    return bundle
