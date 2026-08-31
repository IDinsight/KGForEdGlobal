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
from typing import Any, Sequence, cast, get_args

# Third Party Library
from loguru import logger
from pydantic import BaseModel

# Package Library
from kgfeg.config import Settings
from kgfeg.document_ir.schemas import DocumentIR
from kgfeg.kgs.schemas import (
    AcademicStandardsExportSummary,
    AcademicStandardsKGBundle,
    AcademicStandardsUnresolvedItems,
    AcademicStandardsValidationReport,
    LearningCommonsNode,
    LearningCommonsRelationship,
    LearningCommonsRelationshipProperties,
    LearningCommonsStandardsFrameworkItemProperties,
    LearningCommonsStandardsFrameworkProperties,
    Relationship,
    SFIFinalRecord,
    SFIFinalSummary,
    SFIHasChildEdge,
    SFIHasChildResolutionSummary,
    StandardsFramework,
    StandardsFrameworkItem,
)
from kgfeg.kgs.utils import (
    KGDirs,
    append_jsonl_model,
    build_standards_framework_uuid,
    model_dump_key,
    reset_output_files,
)
from kgfeg.schemas import CreateKGConfig, LearningCommonsGradeLevel
from kgfeg.utils.general import make_dir, open_json_type, write_to_json

_LEARNING_COMMONS_GRADE_LEVELS = set(get_args(LearningCommonsGradeLevel))
_LEARNING_COMMONS_UNRESOLVED_ROOT_FALLBACK = "unresolvedRootFallback"


def _bool_to_learning_commons_string(value: bool) -> str:
    """Serialize one Boolean using Learning Commons' JSONL property convention.

    Parameters
    ----------
    value
        Boolean value to serialize.

    Returns
    -------
    str
        `"true"` or `"false"`.
    """

    return "true" if value else "false"


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


def _build_learning_commons_nodes(
    *,
    grade_level_mapping: dict[str, list[LearningCommonsGradeLevel]],
    sf: StandardsFramework,
    sfis: Sequence[StandardsFrameworkItem],
) -> list[LearningCommonsNode]:
    """Build slim Learning Commons-shaped node records.

    Parameters
    ----------
    grade_level_mapping
        Runtime mapping from local canonical grades to Learning Commons grades.
    sf
        Internal framework entity.
    sfis
        Internal standards-item entities in deterministic export order.

    Returns
    -------
    list[LearningCommonsNode]
        Framework-first Learning Commons node records.
    """

    framework_identifier = str(sf.identifier)
    nodes = [
        LearningCommonsNode(
            identifier=framework_identifier,
            labels=["StandardsFramework"],
            properties=LearningCommonsStandardsFrameworkProperties(
                academic_subject=sf.academic_subject,
                adoption_status=sf.adoption_status,
                attribution_statement=sf.attribution_statement,
                author=sf.author,
                case_identifier_uri=sf.case_identifier_uri,
                case_identifier_uuid=str(sf.case_identifier_uuid),
                date_created=sf.date_created,
                date_modified=sf.date_modified,
                description=sf.description,
                identifier=framework_identifier,
                in_language=sf.in_language,
                is_current=_bool_to_learning_commons_string(sf.is_current),
                jurisdiction=sf.jurisdiction,
                license=sf.license,
                name=sf.name,
                notes=sf.notes,
                provider=sf.provider,
            ),
        )
    ]

    for item in sfis:
        mapped_grade_levels = _map_learning_commons_grade_levels(
            grade_level_mapping=grade_level_mapping, local_grade_levels=item.grade_level
        )
        grade_level = (
            json.dumps(mapped_grade_levels, ensure_ascii=False, separators=(",", ":"))
            if mapped_grade_levels
            else None
        )
        item_identifier = str(item.identifier)
        nodes.append(
            LearningCommonsNode(
                identifier=item_identifier,
                labels=["StandardsFrameworkItem"],
                properties=LearningCommonsStandardsFrameworkItemProperties(
                    academic_subject=item.academic_subject,
                    adoption_status=sf.adoption_status,
                    alternate_statement_code=item.alternate_statement_code,
                    attribution_statement=item.attribution_statement,
                    author=item.author,
                    case_identifier_uri=item.case_identifier_uri,
                    case_identifier_uuid=str(item.case_identifier_uuid),
                    date_created=item.date_created,
                    date_modified=item.date_modified,
                    description=item.description,
                    grade_level=grade_level,
                    identifier=item_identifier,
                    in_language=item.in_language,
                    is_current=_bool_to_learning_commons_string(item.is_current),
                    jurisdiction=item.jurisdiction,
                    license=item.license,
                    normalized_statement_type=item.normalized_statement_type,
                    notes=item.notes,
                    provider=item.provider,
                    statement_code=item.statement_code,
                    statement_type=item.statement_type,
                ),
            )
        )

    return nodes


def _build_learning_commons_relationships(
    *, nodes: Sequence[LearningCommonsNode], relationships: Sequence[Relationship]
) -> list[LearningCommonsRelationship]:
    """Build slim Learning Commons-shaped relationship records.

    The graph topology is preserved exactly, including valid multi-parent DAG edges.
    Unresolved root fallbacks remain present and receive an explicit extension status.

    Parameters
    ----------
    nodes
        Learning Commons node records used to resolve graph identifiers and labels.
    relationships
        Internal relationship records in deterministic order.

    Returns
    -------
    list[LearningCommonsRelationship]
        Learning Commons relationship records aligned to the internal graph.

    Raises
    ------
    ValueError
        If a declared relationship endpoint cannot be resolved to a node.
    """

    nodes_by_case_identifier_uuid = {
        case_identifier_uuid: node
        for node in nodes
        if (
            case_identifier_uuid := getattr(
                node.properties, "case_identifier_uuid", None
            )
        )
        is not None
    }
    nodes_by_identifier = {node.properties.identifier: node for node in nodes}
    output: list[LearningCommonsRelationship] = []

    for relationship in relationships:
        source_lookup = (
            nodes_by_case_identifier_uuid
            if relationship.source_entity_key == "case_identifier_uuid"
            else nodes_by_identifier
        )
        target_lookup = (
            nodes_by_case_identifier_uuid
            if relationship.target_entity_key == "case_identifier_uuid"
            else nodes_by_identifier
        )
        source_node = source_lookup.get(relationship.source_entity_value)
        target_node = target_lookup.get(relationship.target_entity_value)

        if source_node is None:
            raise ValueError(
                f"Unable to resolve Learning Commons relationship source endpoint "
                f"{relationship.source_entity_key}="
                f"{relationship.source_entity_value!r}."
            )

        if target_node is None:
            raise ValueError(
                f"Unable to resolve Learning Commons relationship target endpoint "
                f"{relationship.target_entity_key}="
                f"{relationship.target_entity_value!r}."
            )

        resolution_status = (
            _LEARNING_COMMONS_UNRESOLVED_ROOT_FALLBACK
            if relationship.metadata.get("unresolved_root_fallback") is True
            else None
        )
        support_confidence = relationship.metadata.get("support_confidence")
        relationship_identifier = str(relationship.identifier)
        output.append(
            LearningCommonsRelationship(
                identifier=relationship_identifier,
                label=relationship.relationship_type,
                properties=LearningCommonsRelationshipProperties(
                    attribution_statement=relationship.attribution_statement,
                    author=relationship.author,
                    date_created=relationship.date_created,
                    date_modified=relationship.date_modified,
                    description=relationship.description,
                    identifier=relationship_identifier,
                    license=relationship.license,
                    provider=relationship.provider,
                    relationship_type=relationship.relationship_type,
                    resolution_status=resolution_status,
                    source_entity=relationship.source_entity,
                    source_entity_key=_to_learning_commons_entity_key(
                        relationship.source_entity_key
                    ),
                    source_entity_value=relationship.source_entity_value,
                    support_confidence=(
                        None if support_confidence is None else str(support_confidence)
                    ),
                    target_entity=relationship.target_entity,
                    target_entity_key=_to_learning_commons_entity_key(
                        relationship.target_entity_key
                    ),
                    target_entity_value=relationship.target_entity_value,
                ),
                source_identifier=source_node.identifier,
                source_labels=list(source_node.labels),
                target_identifier=target_node.identifier,
                target_labels=list(target_node.labels),
            )
        )

    return output


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


def _detect_learning_commons_cycles(
    relationships: Sequence[LearningCommonsRelationship],
) -> list[list[str]]:
    """Detect directed cycles in Learning Commons relationship records.

    Parameters
    ----------
    relationships
        Learning Commons relationship records.

    Returns
    -------
    list[list[str]]
        Detected cycles represented by outer node identifiers.
    """

    graph: dict[str, list[str]] = defaultdict(list)

    for relationship in relationships:
        graph[relationship.source_identifier].append(relationship.target_identifier)

    cycles: list[list[str]] = []
    stack: list[str] = []
    visited: set[str] = set()
    visiting: set[str] = set()

    def _visit(node_identifier: str) -> None:
        """Visit one node while detecting Learning Commons graph cycles.

        Parameters
        ----------
        node_identifier
            Outer graph-node identifier.
        """

        if node_identifier in visiting:
            cycle_start = (
                stack.index(node_identifier) if node_identifier in stack else 0
            )
            cycles.append(stack[cycle_start:] + [node_identifier])
            return

        if node_identifier in visited:
            return

        visiting.add(node_identifier)
        stack.append(node_identifier)

        for child_identifier in graph.get(node_identifier, []):
            _visit(child_identifier)

        stack.pop()
        visiting.remove(node_identifier)
        visited.add(node_identifier)

    for node_identifier in sorted(graph):
        _visit(node_identifier)

    return cycles


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


def _extract_local_grade_levels(
    *, grade_level_statement_types: Sequence[str], record: SFIFinalRecord
) -> list[str]:
    """Recover source-canonical local grade values from one final SFI record.

    Runtime configuration supplies the curriculum-to-export mapping. For each
    configured canonical statement type, the function uses the record's own canonical
    value when the record represents that organizer and the matching final-record
    identity-scope value. Candidate-level identity scopes are consulted only when
    neither authoritative source is available. It does not infer grade-like meaning
    from statement-type wording, hierarchy position, or framework metadata.

    Parameters
    ----------
    grade_level_statement_types
        Ordered canonical statement-type labels mapped to the internal
        `StandardsFrameworkItem.grade_level` field.
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


def _get_learning_commons_export_schema_version() -> str:
    """Return the configured Learning Commons export schema version.

    Returns
    -------
    str
        Non-empty schema version loaded through `pydantic-settings`.

    Raises
    ------
    ValueError
        If `LEARNING_COMMONS_EXPORT_SCHEMA_VERSION` is unset or blank.
    """

    value = Settings.LEARNING_COMMONS_EXPORT_SCHEMA_VERSION

    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            "LEARNING_COMMONS_EXPORT_SCHEMA_VERSION must be set to a non-empty "
            "string in the active environment before exporting Learning Commons "
            "JSONL artifacts."
        )

    return value.strip()


def _json_object_from_unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one JSON object while rejecting duplicate property names.

    Parameters
    ----------
    pairs
        Ordered key-value pairs supplied by `json.loads` for one JSON object.

    Returns
    -------
    dict[str, Any]
        JSON object preserving the parsed values.

    Raises
    ------
    ValueError
        If the object contains a duplicate property name.
    """

    output: dict[str, Any] = {}

    for key, value in pairs:
        if key in output:
            raise ValueError(f"Duplicate JSON property name: {key!r}.")

        output[key] = value

    return output


def _load_complete_existing_export_artifacts(
    *,
    bundle: AcademicStandardsKGBundle,
    bundle_fp: Path,
    entity_provenance: dict[str, Any],
    entity_provenance_fp: Path,
    framework_fp: Path,
    items_fp: Path,
    learning_commons_nodes: Sequence[LearningCommonsNode],
    learning_commons_nodes_fp: Path,
    learning_commons_relationships: Sequence[LearningCommonsRelationship],
    learning_commons_relationships_fp: Path,
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
    learning_commons_nodes
        Expected Learning Commons node sequence.
    learning_commons_nodes_fp
        Persisted Learning Commons node JSONL path.
    learning_commons_relationships
        Expected Learning Commons relationship sequence.
    learning_commons_relationships_fp
        Persisted Learning Commons relationship JSONL path.
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
            artifact_label="as_standards_framework.json",
            expected=sf,
        )
        loaded_items = _load_jsonl_models(
            fp=items_fp, model_type=StandardsFrameworkItem
        )
        _validate_model_sequences_equal(
            actual=loaded_items,
            artifact_label="as_standards_framework_items.jsonl",
            expected=sfis,
        )
        loaded_relationships = _load_jsonl_models(
            fp=relationships_fp, model_type=Relationship
        )
        _validate_model_sequences_equal(
            actual=loaded_relationships,
            artifact_label="as_relationships_has_child.jsonl",
            expected=relationships,
        )
        loaded_learning_commons_nodes = _load_jsonl_objects(learning_commons_nodes_fp)
        _validate_learning_commons_wire_sequences_equal(
            actual=loaded_learning_commons_nodes,
            artifact_label="as_nodes.jsonl",
            expected=learning_commons_nodes,
        )
        loaded_learning_commons_relationships = _load_jsonl_objects(
            learning_commons_relationships_fp
        )
        _validate_learning_commons_wire_sequences_equal(
            actual=loaded_learning_commons_relationships,
            artifact_label="as_relationships.jsonl",
            expected=learning_commons_relationships,
        )
        loaded_entity_provenance = open_json_type(entity_provenance_fp)

        if json.dumps(loaded_entity_provenance, sort_keys=True) != json.dumps(
            entity_provenance, sort_keys=True
        ):
            raise ValueError("as_entity_provenance.json does not match current output.")

        loaded_unresolved_items = AcademicStandardsUnresolvedItems.model_validate(
            open_json_type(unresolved_items_fp)
        )
        _validate_model_equal(
            actual=loaded_unresolved_items,
            artifact_label="as_unresolved_items.json",
            expected=unresolved_items,
        )
        loaded_validation_report = AcademicStandardsValidationReport.model_validate(
            open_json_type(validation_report_fp)
        )
        _validate_model_equal(
            actual=loaded_validation_report,
            artifact_label="as_validation_report.json",
            expected=validation_report,
        )

        if not loaded_validation_report.passed:
            raise ValueError("Existing as_validation_report.json did not pass.")

        loaded_bundle = AcademicStandardsKGBundle.model_validate(
            open_json_type(bundle_fp)
        )
        _validate_model_equal(
            actual=loaded_bundle, artifact_label="as_kg_bundle.json", expected=bundle
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


def _load_jsonl_objects(fp: Path) -> list[dict[str, Any]]:
    """Load a JSONL artifact as raw JSON objects without model normalization.

    Raw loading is required for wire-format reuse checks because Pydantic parsing can
    accept aliases or otherwise normalize a noncanonical persisted representation.
    Blank lines are ignored, while every nonblank line must contain exactly one JSON
    object with unique property names.

    Parameters
    ----------
    fp
        JSONL artifact path.

    Returns
    -------
    list[dict[str, Any]]
        Parsed JSON objects in file order.

    Raises
    ------
    ValueError
        If the artifact is missing, a nonblank line is invalid JSON, a JSON value is
        not an object, or any object contains duplicate property names.
    """

    if not fp.exists():
        raise ValueError(f"Missing JSONL artifact: {fp}")

    objects: list[dict[str, Any]] = []

    with fp.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line_clean = line.strip()

            if not line_clean:
                continue

            try:
                payload = json.loads(
                    line_clean, object_pairs_hook=_json_object_from_unique_pairs
                )
            except Exception as exc:  # pylint: disable=W0718
                raise ValueError(
                    f"Invalid JSONL record in {fp} at line {line_number}."
                ) from exc

            if not isinstance(payload, dict):
                raise ValueError(
                    f"JSONL record in {fp} at line {line_number} must be an object."
                )

            objects.append(payload)

    return objects


def _map_learning_commons_grade_levels(
    *,
    grade_level_mapping: dict[str, list[LearningCommonsGradeLevel]],
    local_grade_levels: Sequence[str],
) -> list[LearningCommonsGradeLevel]:
    """Map source-canonical local grades to Learning Commons grade values.

    Explicit mappings take precedence. Local values already present in the Learning
    Commons grade vocabulary use identity mapping. An explicit empty target list is
    retained as an intentional decision to omit `gradeLevel` from the slim export.

    Parameters
    ----------
    grade_level_mapping
        Runtime mapping from local canonical values to Learning Commons grades.
    local_grade_levels
        Source-canonical local grade values associated with one item.

    Returns
    -------
    list[LearningCommonsGradeLevel]
        Stable, de-duplicated Learning Commons grade values.

    Raises
    ------
    ValueError
        If a local value is neither explicitly mapped nor already a valid Learning
        Commons grade value.
    """

    mapped: list[LearningCommonsGradeLevel] = []
    seen: set[str] = set()

    for local_grade_level in local_grade_levels:
        if local_grade_level in grade_level_mapping:
            targets = grade_level_mapping[local_grade_level]
        elif local_grade_level in _LEARNING_COMMONS_GRADE_LEVELS:
            targets = [cast(LearningCommonsGradeLevel, local_grade_level)]
        else:
            raise ValueError(
                f"No Learning Commons grade mapping exists for local grade value "
                f"{local_grade_level!r}."
            )

        for target in targets:
            if target in seen:
                continue

            mapped.append(target)
            seen.add(target)

    return mapped


def _reachable_learning_commons_item_identifiers(
    *, framework_identifier: str, relationships: Sequence[LearningCommonsRelationship]
) -> set[str]:
    """Compute Learning Commons item nodes reachable from the framework root.

    Parameters
    ----------
    framework_identifier
        Outer identifier of the framework node.
    relationships
        Learning Commons relationship records.

    Returns
    -------
    set[str]
        Reachable outer node identifiers excluding the root.
    """

    graph: dict[str, list[str]] = defaultdict(list)

    for relationship in relationships:
        graph[relationship.source_identifier].append(relationship.target_identifier)

    reachable: set[str] = set()
    stack = [framework_identifier]

    while stack:
        node_identifier = stack.pop()

        for child_identifier in graph.get(node_identifier, []):
            if child_identifier in reachable:
                continue

            reachable.add(child_identifier)
            stack.append(child_identifier)

    return reachable


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


def _remove_artifacts(artifact_fps: Sequence[Path]) -> None:
    """Remove stale artifacts without recreating empty placeholder files.

    Parameters
    ----------
    artifact_fps
        Artifact paths to remove when they exist.
    """

    for artifact_fp in artifact_fps:
        if artifact_fp.exists():
            artifact_fp.unlink()


def _to_learning_commons_entity_key(value: str) -> str:
    """Convert one internal entity-key name to its Learning Commons spelling.

    Parameters
    ----------
    value
        Internal entity-key name.

    Returns
    -------
    str
        Learning Commons entity-key value.

    Raises
    ------
    ValueError
        If the internal entity key is unsupported.
    """

    mapping = {"case_identifier_uuid": "caseIdentifierUUID", "identifier": "identifier"}

    try:
        return mapping[value]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported Learning Commons relationship entity key: {value!r}."
        ) from exc


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
            "has_child_resolution_summary.sfi_to_sfi_edge_count does not match "
            "SFI-to-SFI edge count."
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
    learning_commons_export_schema_version: str,
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
    learning_commons_export_schema_version
        Configured Learning Commons schema baseline for the slim JSONL artifacts.
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
        "configured_local_grade_extraction",
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
        learning_commons_export_schema_version=(learning_commons_export_schema_version),
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


def _validate_learning_commons_export(
    *,
    grade_level_mapping: dict[str, list[LearningCommonsGradeLevel]],
    internal_relationships: Sequence[Relationship],
    nodes: Sequence[LearningCommonsNode],
    relationships: Sequence[LearningCommonsRelationship],
    sf: StandardsFramework,
    sfis: Sequence[StandardsFrameworkItem],
) -> list[str]:
    """Validate slim Learning Commons-shaped node and relationship artifacts.

    Validation preserves source-authored graph semantics. Multi-parent DAGs are
    allowed, while duplicate edges, missing endpoints, cycles, and unreachable nodes
    are rejected.

    Parameters
    ----------
    grade_level_mapping
        Runtime local-to-Learning-Commons grade mapping.
    internal_relationships
        Internal relationships that the slim export must preserve exactly.
    nodes
        Learning Commons node records.
    relationships
        Learning Commons relationship records.
    sf
        Internal framework record.
    sfis
        Internal standards-item records.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []
    errors.extend(
        _validate_learning_commons_node_structure(nodes=nodes, sf=sf, sfis=sfis)
    )
    errors.extend(_validate_learning_commons_node_properties(nodes))
    errors.extend(
        _validate_learning_commons_node_payloads(
            grade_level_mapping=grade_level_mapping, nodes=nodes, sf=sf, sfis=sfis
        )
    )
    errors.extend(
        _validate_learning_commons_relationship_structure(
            internal_relationships=internal_relationships, relationships=relationships
        )
    )
    errors.extend(
        _validate_learning_commons_relationship_payloads(
            internal_relationships=internal_relationships,
            nodes=nodes,
            relationships=relationships,
        )
    )
    errors.extend(
        _validate_learning_commons_reachability(
            nodes=nodes, relationships=relationships, sf=sf
        )
    )
    return errors


def _validate_learning_commons_framework_payload(
    *, framework_node: LearningCommonsNode | None, sf: StandardsFramework
) -> list[str]:
    """Validate that the framework node payload preserves the internal framework.

    Parameters
    ----------
    framework_node
        Learning Commons node resolved for the framework identifier, if present.
    sf
        Internal framework record.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []

    if framework_node is None:
        return errors

    if not isinstance(
        framework_node.properties,
        LearningCommonsStandardsFrameworkProperties,
    ):
        errors.append(
            "Learning Commons framework identifier resolves to item properties."
        )
        return errors

    if framework_node.properties.case_identifier_uuid != str(sf.case_identifier_uuid):
        errors.append(
            "Learning Commons framework caseIdentifierUUID was not preserved."
        )

    if framework_node.properties.is_current != (
        _bool_to_learning_commons_string(sf.is_current)
    ):
        errors.append("Learning Commons framework isCurrent was not preserved.")

    return errors


def _validate_learning_commons_grade_mapping(
    *,
    grade_level_mapping: dict[str, list[LearningCommonsGradeLevel]],
    sfis: Sequence[StandardsFrameworkItem],
) -> list[str]:
    """Validate that every observed local grade can be exported deterministically.

    Parameters
    ----------
    grade_level_mapping
        Runtime local-to-Learning-Commons grade mapping.
    sfis
        Internal standards items containing source-canonical local grade values.

    Returns
    -------
    list[str]
        Validation errors for unmapped or invalid observed grade values.
    """

    errors: list[str] = []
    observed_local_grade_levels = dict.fromkeys(
        grade_level for item in sfis for grade_level in item.grade_level
    )

    for local_grade_level in observed_local_grade_levels:
        try:
            _map_learning_commons_grade_levels(
                grade_level_mapping=grade_level_mapping,
                local_grade_levels=[local_grade_level],
            )
        except ValueError as exc:
            errors.append(str(exc))

    return errors


def _validate_learning_commons_item_payload(
    *,
    grade_level_mapping: dict[str, list[LearningCommonsGradeLevel]],
    item: StandardsFrameworkItem,
    item_node: LearningCommonsNode,
) -> list[str]:
    """Validate that a single item node payload preserves its internal item.

    Parameters
    ----------
    grade_level_mapping
        Runtime local-to-Learning-Commons grade mapping.
    item
        Internal standards-item record.
    item_node
        Learning Commons node resolved for the item identifier.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []

    if not isinstance(
        item_node.properties,
        LearningCommonsStandardsFrameworkItemProperties,
    ):
        errors.append(
            f"Learning Commons item {item.identifier} resolves to framework properties."
        )
        return errors

    expected_grade_levels = _map_learning_commons_grade_levels(
        grade_level_mapping=grade_level_mapping, local_grade_levels=item.grade_level
    )
    expected_grade_level = (
        json.dumps(expected_grade_levels, ensure_ascii=False, separators=(",", ":"))
        if expected_grade_levels
        else None
    )

    if item_node.properties.case_identifier_uuid != str(item.case_identifier_uuid):
        errors.append(
            f"Learning Commons item {item.identifier} caseIdentifierUUID was "
            f"not preserved."
        )

    if item_node.properties.description != item.description:
        errors.append(
            f"Learning Commons item {item.identifier} description was not preserved."
        )

    if item_node.properties.grade_level != expected_grade_level:
        errors.append(
            f"Learning Commons item {item.identifier} gradeLevel does not match "
            f"the configured mapping."
        )

    if item_node.properties.is_current != _bool_to_learning_commons_string(
        item.is_current
    ):
        errors.append(
            f"Learning Commons item {item.identifier} isCurrent was not preserved."
        )

    if item_node.properties.alternate_statement_code != item.alternate_statement_code:
        errors.append(
            f"Learning Commons item {item.identifier} alternateStatementCode "
            f"was not preserved."
        )

    return errors


def _validate_learning_commons_node_payloads(
    *,
    grade_level_mapping: dict[str, list[LearningCommonsGradeLevel]],
    nodes: Sequence[LearningCommonsNode],
    sf: StandardsFramework,
    sfis: Sequence[StandardsFrameworkItem],
) -> list[str]:
    """Validate that node payloads preserve the internal framework and items.

    Parameters
    ----------
    grade_level_mapping
        Runtime local-to-Learning-Commons grade mapping.
    nodes
        Learning Commons node records.
    sf
        Internal framework record.
    sfis
        Internal standards-item records.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []
    nodes_by_identifier = {node.identifier: node for node in nodes}

    errors.extend(
        _validate_learning_commons_framework_payload(
            framework_node=nodes_by_identifier.get(str(sf.identifier)), sf=sf
        )
    )

    for item in sfis:
        item_node = nodes_by_identifier.get(str(item.identifier))

        if item_node is None:
            continue

        errors.extend(
            _validate_learning_commons_item_payload(
                grade_level_mapping=grade_level_mapping, item=item, item_node=item_node
            )
        )

    return errors


def _validate_learning_commons_node_properties(
    nodes: Sequence[LearningCommonsNode],
) -> list[str]:
    """Validate that Learning Commons node properties are string-encoded.

    Enforces that every serialized property is a string and that any `gradeLevel`
    property encodes a JSON array of recognized grade-level strings.

    Parameters
    ----------
    nodes
        Learning Commons node records.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []

    for node in nodes:
        property_payload = node.properties.model_dump(
            by_alias=True, exclude_none=True, mode="json"
        )
        non_string_properties = sorted(
            key for key, value in property_payload.items() if not isinstance(value, str)
        )

        if non_string_properties:
            errors.append(
                f"Learning Commons node {node.identifier} contains non-string "
                f"properties: {non_string_properties}."
            )

        grade_level = property_payload.get("gradeLevel")

        if grade_level is not None:
            try:
                parsed_grade_levels = json.loads(grade_level)
            except json.JSONDecodeError:
                errors.append(
                    f"Learning Commons node {node.identifier} has invalid "
                    f"gradeLevel JSON encoding."
                )
                continue

            if not isinstance(parsed_grade_levels, list) or not all(
                isinstance(value, str) for value in parsed_grade_levels
            ):
                errors.append(
                    f"Learning Commons node {node.identifier} gradeLevel must encode "
                    f"a JSON array of strings."
                )
                continue

            invalid_grade_levels = sorted(
                set(parsed_grade_levels) - _LEARNING_COMMONS_GRADE_LEVELS
            )

            if invalid_grade_levels:
                errors.append(
                    f"Learning Commons node {node.identifier} contains invalid "
                    f"gradeLevel values: {invalid_grade_levels}."
                )

    return errors


def _validate_learning_commons_node_structure(
    *,
    nodes: Sequence[LearningCommonsNode],
    sf: StandardsFramework,
    sfis: Sequence[StandardsFrameworkItem],
) -> list[str]:
    """Validate Learning Commons node counts, uniqueness, and identity coverage.

    Parameters
    ----------
    nodes
        Learning Commons node records.
    sf
        Internal framework record.
    sfis
        Internal standards-item records.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []
    expected_node_count = len(sfis) + 1

    if len(nodes) != expected_node_count:
        errors.append(
            f"Learning Commons node count does not match one framework plus all "
            f"items; expected={expected_node_count}, actual={len(nodes)}."
        )

    framework_nodes = [node for node in nodes if node.labels == ["StandardsFramework"]]

    if len(framework_nodes) != 1:
        errors.append(
            f"Learning Commons export must contain exactly one StandardsFramework "
            f"node; actual={len(framework_nodes)}."
        )

    node_identifiers = [node.identifier for node in nodes]

    if len(node_identifiers) != len(set(node_identifiers)):
        duplicate_node_identifiers = sorted(
            identifier
            for identifier, count in Counter(node_identifiers).items()
            if count > 1
        )
        errors.append(
            f"Duplicate Learning Commons node identifiers detected: "
            f"{duplicate_node_identifiers}."
        )

    nodes_by_identifier = {node.identifier: node for node in nodes}
    expected_node_identifiers = {
        str(sf.identifier),
        *(str(item.identifier) for item in sfis),
    }

    if set(nodes_by_identifier) != expected_node_identifiers:
        missing_node_identifiers = sorted(
            expected_node_identifiers - set(nodes_by_identifier)
        )
        extra_node_identifiers = sorted(
            set(nodes_by_identifier) - expected_node_identifiers
        )
        errors.append(
            f"Learning Commons node identifiers do not preserve the internal graph; "
            f"missing={missing_node_identifiers}, extra={extra_node_identifiers}."
        )

    return errors


def _validate_learning_commons_reachability(
    *,
    nodes: Sequence[LearningCommonsNode],
    relationships: Sequence[LearningCommonsRelationship],
    sf: StandardsFramework,
) -> list[str]:
    """Validate acyclicity and framework-root reachability of item nodes.

    Parameters
    ----------
    nodes
        Learning Commons node records.
    relationships
        Learning Commons relationship records.
    sf
        Internal framework record.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []
    framework_nodes = [node for node in nodes if node.labels == ["StandardsFramework"]]

    cycles = _detect_learning_commons_cycles(relationships)

    if cycles:
        errors.append(f"Learning Commons relationship cycles detected: {cycles[:5]}.")

    if framework_nodes:
        framework_identifier = framework_nodes[0].identifier
    else:
        framework_identifier = str(sf.identifier)

    item_identifiers = {
        node.identifier for node in nodes if node.labels == ["StandardsFrameworkItem"]
    }
    reachable_item_identifiers = _reachable_learning_commons_item_identifiers(
        framework_identifier=framework_identifier, relationships=relationships
    )
    unreachable_item_identifiers = sorted(item_identifiers - reachable_item_identifiers)

    if unreachable_item_identifiers:
        errors.append(
            f"Learning Commons item nodes are not reachable from the framework root: "
            f"{unreachable_item_identifiers}."
        )

    return errors


def _validate_learning_commons_relationship_endpoints(
    *,
    nodes_by_identifier: dict[str, LearningCommonsNode],
    relationship: LearningCommonsRelationship,
) -> list[str]:
    """Validate relationship endpoint existence, labels, and self-loops.

    Parameters
    ----------
    nodes_by_identifier
        Learning Commons nodes keyed by identifier.
    relationship
        Learning Commons relationship record to validate.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []
    source_node = nodes_by_identifier.get(relationship.source_identifier)
    target_node = nodes_by_identifier.get(relationship.target_identifier)

    if source_node is None:
        errors.append(
            f"Learning Commons relationship {relationship.identifier} source "
            f"endpoint does not exist: {relationship.source_identifier}."
        )
    elif relationship.source_labels != source_node.labels:
        errors.append(
            f"Learning Commons relationship {relationship.identifier} source "
            f"labels do not match the source node."
        )

    if target_node is None:
        errors.append(
            f"Learning Commons relationship {relationship.identifier} target "
            f"endpoint does not exist: {relationship.target_identifier}."
        )
    elif relationship.target_labels != target_node.labels:
        errors.append(
            f"Learning Commons relationship {relationship.identifier} target "
            f"labels do not match the target node."
        )

    if relationship.source_identifier == relationship.target_identifier:
        errors.append(
            f"Learning Commons relationship {relationship.identifier} is a "
            f"self-loop."
        )

    return errors


def _validate_learning_commons_relationship_payloads(
    *,
    internal_relationships: Sequence[Relationship],
    nodes: Sequence[LearningCommonsNode],
    relationships: Sequence[LearningCommonsRelationship],
) -> list[str]:
    """Validate relationship endpoints, payloads, and resolution status.

    Enforces string-encoded properties, existing and correctly labeled endpoints,
    absence of self-loops, and exact preservation of the internal relationship payload
    and resolution status.

    Parameters
    ----------
    internal_relationships
        Internal relationships that the slim export must preserve exactly.
    nodes
        Learning Commons node records.
    relationships
        Learning Commons relationship records.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []
    nodes_by_identifier = {node.identifier: node for node in nodes}
    internal_relationships_by_id = {
        str(relationship.identifier): relationship
        for relationship in internal_relationships
    }

    for relationship in relationships:
        errors.extend(
            _validate_learning_commons_relationship_property_strings(relationship)
        )
        errors.extend(
            _validate_learning_commons_relationship_endpoints(
                nodes_by_identifier=nodes_by_identifier, relationship=relationship
            )
        )

        internal_relationship = internal_relationships_by_id.get(
            relationship.identifier
        )

        if internal_relationship is None:
            errors.append(
                f"Learning Commons relationship {relationship.identifier} does not "
                f"exist in the internal graph."
            )
            continue

        errors.extend(
            _validate_learning_commons_relationship_preservation(
                internal_relationship=internal_relationship, relationship=relationship
            )
        )

    return errors


def _validate_learning_commons_relationship_preservation(
    *, internal_relationship: Relationship, relationship: LearningCommonsRelationship
) -> list[str]:
    """Validate that a relationship preserves its internal payload and status.

    Parameters
    ----------
    internal_relationship
        Internal relationship the slim export must preserve exactly.
    relationship
        Learning Commons relationship record to validate.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []

    if (
        relationship.properties.source_entity != internal_relationship.source_entity
        or relationship.properties.source_entity_key
        != _to_learning_commons_entity_key(internal_relationship.source_entity_key)
        or relationship.properties.source_entity_value
        != internal_relationship.source_entity_value
        or relationship.properties.target_entity != internal_relationship.target_entity
        or relationship.properties.target_entity_key
        != _to_learning_commons_entity_key(internal_relationship.target_entity_key)
        or relationship.properties.target_entity_value
        != internal_relationship.target_entity_value
        or relationship.properties.relationship_type
        != internal_relationship.relationship_type
    ):
        errors.append(
            f"Learning Commons relationship {relationship.identifier} does not "
            f"preserve the internal relationship payload."
        )

    expected_resolution_status = (
        _LEARNING_COMMONS_UNRESOLVED_ROOT_FALLBACK
        if internal_relationship.metadata.get("unresolved_root_fallback") is True
        else None
    )

    if relationship.properties.resolution_status != expected_resolution_status:
        errors.append(
            f"Learning Commons relationship {relationship.identifier} has an "
            f"incorrect resolutionStatus."
        )

    return errors


def _validate_learning_commons_relationship_property_strings(
    relationship: LearningCommonsRelationship,
) -> list[str]:
    """Validate that all serialized relationship properties are strings.

    Parameters
    ----------
    relationship
        Learning Commons relationship record to validate.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    property_payload = relationship.properties.model_dump(
        by_alias=True, exclude_none=True, mode="json"
    )
    non_string_properties = sorted(
        key for key, value in property_payload.items() if not isinstance(value, str)
    )

    if non_string_properties:
        return [
            f"Learning Commons relationship {relationship.identifier} contains "
            f"non-string properties: {non_string_properties}."
        ]

    return []


def _validate_learning_commons_relationship_structure(
    *,
    internal_relationships: Sequence[Relationship],
    relationships: Sequence[LearningCommonsRelationship],
) -> list[str]:
    """Validate Learning Commons relationship counts and uniqueness.

    Parameters
    ----------
    internal_relationships
        Internal relationships that the slim export must preserve exactly.
    relationships
        Learning Commons relationship records.

    Returns
    -------
    list[str]
        Validation error messages.
    """

    errors: list[str] = []

    if len(relationships) != len(internal_relationships):
        errors.append(
            f"Learning Commons relationship count does not match the internal graph; "
            f"expected={len(internal_relationships)}, actual={len(relationships)}."
        )

    relationship_identifiers = [
        relationship.identifier for relationship in relationships
    ]

    if len(relationship_identifiers) != len(set(relationship_identifiers)):
        duplicate_relationship_identifiers = sorted(
            identifier
            for identifier, count in Counter(relationship_identifiers).items()
            if count > 1
        )
        errors.append(
            f"Duplicate Learning Commons relationship identifiers detected: "
            f"{duplicate_relationship_identifiers}."
        )

    edge_triples = [
        (
            relationship.source_identifier,
            relationship.label,
            relationship.target_identifier,
        )
        for relationship in relationships
    ]

    if len(edge_triples) != len(set(edge_triples)):
        duplicate_edge_triples = sorted(
            edge for edge, count in Counter(edge_triples).items() if count > 1
        )
        errors.append(
            f"Duplicate Learning Commons relationship edge triples detected: "
            f"{duplicate_edge_triples}."
        )

    return errors


def _validate_learning_commons_wire_sequences_equal(
    *,
    actual: Sequence[dict[str, Any]],
    artifact_label: str,
    expected: Sequence[BaseModel],
) -> None:
    """Validate exact Learning Commons JSON-object shape, values, and record order.

    Unlike model-to-model comparison, this check preserves the persisted wire shape. It
    therefore rejects snake_case field names accepted through Pydantic population
    aliases, explicit null fields that should have been omitted, extra properties, and
    any other representation that differs from the canonical writer output.

    Parameters
    ----------
    actual
        Raw JSON objects loaded from the persisted JSONL artifact.
    artifact_label
        Human-readable artifact label for errors.
    expected
        Expected Learning Commons wire-model sequence.

    Raises
    ------
    ValueError
        If sequence lengths, record order, JSON-object shape, or values differ.
    """

    if len(actual) != len(expected):
        raise ValueError(
            f"{artifact_label} has {len(actual)} records, but expected {len(expected)}."
        )

    for index, (actual_payload, expected_model) in enumerate(
        zip(actual, expected, strict=True), start=1
    ):
        expected_payload = expected_model.model_dump(
            by_alias=True, exclude_none=True, mode="json"
        )

        if actual_payload != expected_payload:
            raise ValueError(
                f"{artifact_label} record {index} does not exactly match the current "
                f"Learning Commons wire payload."
            )


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

    expected_grade_levels = _extract_local_grade_levels(
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


def _write_learning_commons_artifacts(
    *,
    nodes: Sequence[LearningCommonsNode],
    nodes_fp: Path,
    relationships: Sequence[LearningCommonsRelationship],
    relationships_fp: Path,
) -> None:
    """Write slim Learning Commons-shaped JSONL artifacts.

    Parameters
    ----------
    nodes
        Learning Commons node records in deterministic order.
    nodes_fp
        Destination path for `as_nodes.jsonl`.
    relationships
        Learning Commons relationship records in deterministic order.
    relationships_fp
        Destination path for `as_relationships.jsonl`.
    """

    make_dir(nodes_fp.parent)
    nodes_fp.write_text("", encoding="utf-8")

    for node in nodes:
        append_jsonl_model(
            by_alias=True, compact=True, exclude_none=True, fp=nodes_fp, model=node
        )

    make_dir(relationships_fp.parent)
    relationships_fp.write_text("", encoding="utf-8")

    for relationship in relationships:
        append_jsonl_model(
            by_alias=True,
            compact=True,
            exclude_none=True,
            fp=relationships_fp,
            model=relationship,
        )


def compile_academic_standards_kg(  # pylint: disable=R0915
    *,
    document_ir: DocumentIR,
    has_child_edges: Sequence[SFIHasChildEdge],
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
    overwrite: bool,
) -> AcademicStandardsKGBundle:
    """Compile, validate, and write final Academic Standards KG artifacts.

    The process is as follows:

    1. Create internal and Learning Commons artifact paths.
    2. Load authoritative artifacts from previous pipeline stages and export settings.
    3. Build the internal `StandardsFramework` entity.
    4. Build source-faithful internal `StandardsFrameworkItem` entities.
    5. Compile final `hasChild` edges into internal `Relationship` entities.
    6. Build the unresolved-item report.
    7. Validate grade mappings and build slim Learning Commons-shaped graph records.
    8. Build stable fingerprints for all semantic and delivery inputs.
    9. Validate the internal graph and Learning Commons-shaped delivery graph.
    10. Build entity provenance.
    11. Build the export summary and complete internal bundle.
    12. Persist diagnostic internal artifacts and fail when validation does not pass.
    13. Reuse existing artifacts only when every persisted payload matches exactly.
    14. Otherwise reset and write all validated internal and delivery artifacts.

    Parameters
    ----------
    document_ir
        Source stitched DocumentIR.
    has_child_edges
        Validated final `hasChild` edges.
    kg_config
        Runtime KG creation configuration.
    kg_dirs
        KG artifact directory wrapper.
    overwrite
        Whether to rebuild artifacts even if exact current artifacts exist.

    Returns
    -------
    AcademicStandardsKGBundle
        Complete compiled internal KG bundle.

    Raises
    ------
    ValueError
        If the Learning Commons export schema version is unset or final validation
        fails.
    """

    # 1.
    make_dir(kg_dirs.root)
    bundle_fp = kg_dirs.root / "as_kg_bundle.json"
    entity_provenance_fp = kg_dirs.root / "as_entity_provenance.json"
    learning_commons_nodes_fp = kg_dirs.root / "as_nodes.jsonl"
    learning_commons_relationships_fp = kg_dirs.root / "as_relationships.jsonl"
    relationships_fp = kg_dirs.root / "as_relationships_has_child.jsonl"
    sf_fp = kg_dirs.root / "as_standards_framework.json"
    sfi_fp = kg_dirs.root / "as_standards_framework_items.jsonl"
    unresolved_items_fp = kg_dirs.root / "as_unresolved_items.json"
    validation_report_fp = kg_dirs.root / "as_validation_report.json"

    # 2.
    grade_level_mapping = kg_config.academic_standards.grade_level_mapping
    grade_level_statement_types = (
        kg_config.academic_standards.grade_level_statement_types
    )
    has_child_resolution_summary = SFIHasChildResolutionSummary.model_validate(
        open_json_type(kg_dirs.root / "has_child_resolution_summary.json")
    )
    has_child_unresolved_edges = [
        SFIHasChildEdge.model_validate(edge)
        for edge in open_json_type(kg_dirs.root / "has_child_unresolved_edges.json")
    ]
    kg_run_manifest = open_json_type(kg_dirs.root / "kg_run_manifest.json")
    try:
        learning_commons_export_schema_version = (
            _get_learning_commons_export_schema_version()
        )
    except ValueError:
        _remove_artifacts(
            [learning_commons_nodes_fp, learning_commons_relationships_fp]
        )
        raise
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
        is_current=metadata.is_current,
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
            alternate_statement_code=None,
            attribution_statement=record.attribution_statement,
            author=record.author,
            case_identifier_uri=record.case_identifier_uri,
            case_identifier_uuid=record.case_identifier_uuid,
            description=record.description,
            grade_level=_extract_local_grade_levels(
                grade_level_statement_types=grade_level_statement_types, record=record
            ),
            identifier=record.identifier,
            in_language=record.in_language,
            is_current=metadata.is_current,
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
    learning_commons_errors = _validate_learning_commons_grade_mapping(
        grade_level_mapping=grade_level_mapping, sfis=sfis
    )
    learning_commons_nodes: list[LearningCommonsNode] = []
    learning_commons_relationships: list[LearningCommonsRelationship] = []

    if not learning_commons_errors:
        try:
            learning_commons_nodes = _build_learning_commons_nodes(
                grade_level_mapping=grade_level_mapping, sf=sf, sfis=sfis
            )
            learning_commons_relationships = _build_learning_commons_relationships(
                nodes=learning_commons_nodes, relationships=relationships
            )
        except ValueError as exc:
            learning_commons_errors.append(str(exc))

    # 8.
    learning_commons_node_payload = [
        node.model_dump(by_alias=True, exclude_none=True, mode="json")
        for node in learning_commons_nodes
    ]
    learning_commons_relationship_payload = [
        relationship.model_dump(by_alias=True, exclude_none=True, mode="json")
        for relationship in learning_commons_relationships
    ]
    input_fingerprints = {
        "grade_level_mapping": _fingerprint_jsonable(grade_level_mapping),
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
        "is_current": _fingerprint_jsonable(metadata.is_current),
        "kg_run_manifest": _fingerprint_jsonable(kg_run_manifest),
        "learning_commons_export_schema_version": _fingerprint_jsonable(
            learning_commons_export_schema_version
        ),
        "learning_commons_nodes": _fingerprint_jsonable(learning_commons_node_payload),
        "learning_commons_relationships": _fingerprint_jsonable(
            learning_commons_relationship_payload
        ),
        "sfi_final_records": _fingerprint_jsonable(
            [model.model_dump(mode="json") for model in sfi_final_records]
        ),
        "sfi_final_summary": _fingerprint_jsonable(
            sfi_final_summary.model_dump(mode="json")
        ),
    }

    # 9.
    internal_validation_report = _validate_export(
        grade_level_statement_types=grade_level_statement_types,
        has_child_edges=has_child_edges,
        has_child_resolution_summary=has_child_resolution_summary,
        has_child_unresolved_edges=has_child_unresolved_edges,
        input_fingerprints=input_fingerprints,
        learning_commons_export_schema_version=(learning_commons_export_schema_version),
        relationships=relationships,
        sf=sf,
        sfi_final_records=sfi_final_records,
        sfi_final_summary=sfi_final_summary,
        sfis=sfis,
    )

    if learning_commons_nodes and learning_commons_relationships:
        learning_commons_errors.extend(
            _validate_learning_commons_export(
                grade_level_mapping=grade_level_mapping,
                internal_relationships=relationships,
                nodes=learning_commons_nodes,
                relationships=learning_commons_relationships,
                sf=sf,
                sfis=sfis,
            )
        )

    intentionally_unmapped_local_grade_values = {
        local_grade_level
        for item in sfis
        for local_grade_level in item.grade_level
        if local_grade_level in grade_level_mapping
        and not grade_level_mapping[local_grade_level]
    }
    unresolved_fallback_relationship_count = sum(
        1
        for relationship in learning_commons_relationships
        if relationship.properties.resolution_status
        == _LEARNING_COMMONS_UNRESOLVED_ROOT_FALLBACK
    )
    validation_errors = [*internal_validation_report.errors, *learning_commons_errors]
    validation_report = AcademicStandardsValidationReport(
        errors=validation_errors,
        input_fingerprints=input_fingerprints,
        learning_commons_export_schema_version=(learning_commons_export_schema_version),
        object_counts={
            **internal_validation_report.object_counts,
            "intentionally_unmapped_local_grade_values": len(
                intentionally_unmapped_local_grade_values
            ),
            "learning_commons_framework_nodes": sum(
                1
                for node in learning_commons_nodes
                if node.labels == ["StandardsFramework"]
            ),
            "learning_commons_item_nodes": sum(
                1
                for node in learning_commons_nodes
                if node.labels == ["StandardsFrameworkItem"]
            ),
            "learning_commons_nodes": len(learning_commons_nodes),
            "learning_commons_relationships": len(learning_commons_relationships),
            "learning_commons_unresolved_fallback_relationships": (
                unresolved_fallback_relationship_count
            ),
        },
        passed=not validation_errors,
        validation_checks=[
            *internal_validation_report.validation_checks,
            "learning_commons_delivery_count_alignment",
            "learning_commons_endpoint_existence",
            "learning_commons_grade_mapping",
            "learning_commons_grade_value_validity",
            "learning_commons_identifier_preservation",
            "learning_commons_node_property_string_encoding",
            "learning_commons_relationship_property_string_encoding",
            "learning_commons_root_reachability",
            "learning_commons_schema_version_configuration",
            "learning_commons_source_topology_preservation",
            "learning_commons_unresolved_fallback_status",
        ],
    )

    # 10.
    entity_provenance = _build_entity_provenance(
        document_ir=document_ir,
        kg_run_manifest=kg_run_manifest,
        relationships=relationships,
        sf=sf,
        sfi_final_records=sfi_final_records,
        sfis=sfis,
    )

    # 11.
    summary = AcademicStandardsExportSummary(
        final_sfi_count=len(sfis),
        finalization_exclusion_summary=unresolved_items.finalization_exclusion_summary,
        framework_count=1,
        has_child_relationship_count=len(relationships),
        learning_commons_node_count=len(learning_commons_nodes),
        learning_commons_relationship_count=len(learning_commons_relationships),
        learning_commons_unresolved_fallback_relationship_count=(
            unresolved_fallback_relationship_count
        ),
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
        # 12.
        _remove_artifacts(
            [learning_commons_nodes_fp, learning_commons_relationships_fp]
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
        raise ValueError(
            f"Final Academic Standards KG export validation failed: "
            f"{validation_report.errors}"
        )

    if not overwrite:
        # 13.
        existing_bundle = _load_complete_existing_export_artifacts(
            bundle=bundle,
            bundle_fp=bundle_fp,
            entity_provenance=entity_provenance,
            entity_provenance_fp=entity_provenance_fp,
            framework_fp=sf_fp,
            items_fp=sfi_fp,
            learning_commons_nodes=learning_commons_nodes,
            learning_commons_nodes_fp=learning_commons_nodes_fp,
            learning_commons_relationships=learning_commons_relationships,
            learning_commons_relationships_fp=(learning_commons_relationships_fp),
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

    # 14.
    reset_output_files(
        [
            bundle_fp,
            entity_provenance_fp,
            learning_commons_nodes_fp,
            learning_commons_relationships_fp,
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
    _write_learning_commons_artifacts(
        nodes=learning_commons_nodes,
        nodes_fp=learning_commons_nodes_fp,
        relationships=learning_commons_relationships,
        relationships_fp=learning_commons_relationships_fp,
    )

    return bundle
