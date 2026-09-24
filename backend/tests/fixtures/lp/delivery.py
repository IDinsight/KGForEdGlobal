"""Independent explicit wire oracle and synthetic upstream delivery fixtures."""

# Future Library
from __future__ import annotations

# Standard Library
import json

from typing import Any

_ALIASES = {
    "academic_subject": "academicSubject",
    "adoption_status": "adoptionStatus",
    "alternate_statement_code": "alternateStatementCode",
    "attribution_statement": "attributionStatement",
    "case_identifier_uri": "caseIdentifierURI",
    "case_identifier_uuid": "caseIdentifierUUID",
    "date_created": "dateCreated",
    "date_modified": "dateModified",
    "in_language": "inLanguage",
    "normalized_statement_type": "normalizedStatementType",
    "relationship_type": "relationshipType",
    "source_entity": "sourceEntity",
    "source_entity_key": "sourceEntityKey",
    "source_entity_value": "sourceEntityValue",
    "statement_code": "statementCode",
    "statement_type": "statementType",
    "target_entity": "targetEntity",
    "target_entity_key": "targetEntityKey",
    "target_entity_value": "targetEntityValue",
}
_COMMON = (
    "attribution_statement",
    "author",
    "description",
    "identifier",
    "license",
    "provider",
)
_GROUPS = (
    "relationships_has_child",
    "relationships_supports",
    "relationships_builds_towards",
    "relationships_relates_to",
)


def _properties(*, fields: tuple[str, ...], row: dict[str, Any]) -> dict[str, Any]:
    """Select only declared wire fields and apply explicit aliases.

    Parameters
    ----------
    fields
        Fields supported by this entity's wire schema.
    row
        Authoritative internal JSON record.

    Returns
    -------
    dict[str, Any]
        Non-null public properties without arbitrary internal metadata.
    """
    return {
        _ALIASES.get(key, key): row[key] for key in fields if row.get(key) is not None
    }


def delivery_records(  # pylint: disable=too-complex,too-many-branches
    *, grade_mapping: dict[str, Any] | None = None, material: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Derive expected wire records without importing production serializers.

    Parameters
    ----------
    grade_mapping
        Explicit curriculum grade mappings; standard grade strings map to themselves.
    material
        Internal graph with complete metadata and authoritative list order.

    Returns
    -------
    tuple[list[dict[str, Any]], list[dict[str, Any]]]
        Independently mapped nodes and four ordered relationship groups.
    """
    grade_mapping = grade_mapping or {}
    nodes = []
    cases = {}
    identifiers = {}
    for group, label in (
        ("framework", "StandardsFramework"),
        ("items", "StandardsFrameworkItem"),
        ("learning_components", "LearningComponent"),
    ):
        rows = [material[group]] if group == "framework" else material[group]
        for row in rows:
            fields: tuple[str, ...] = _COMMON + ("academic_subject", "in_language")
            if group != "learning_components":
                fields += (
                    "case_identifier_uri",
                    "case_identifier_uuid",
                    "date_created",
                    "date_modified",
                    "jurisdiction",
                    "notes",
                )
                fields += (
                    ("name",)
                    if group == "framework"
                    else (
                        "alternate_statement_code",
                        "normalized_statement_type",
                        "statement_code",
                        "statement_type",
                    )
                )
            properties = _properties(fields=fields, row=row)
            if group == "learning_components":
                properties["identityKey"] = row["metadata"]["identity"]["identity_key"]
                if row["metadata"].get("tags"):
                    properties["tags"] = json.dumps(
                        ensure_ascii=False,
                        obj=row["metadata"]["tags"],
                        separators=(",", ":"),
                    )
            else:
                properties["adoptionStatus"] = material["framework"]["adoption_status"]
                properties["isCurrent"] = "true" if row["is_current"] else "false"
                if group == "items":
                    grades = list(
                        dict.fromkeys(
                            target
                            for grade in row["grade_level"]
                            for target in grade_mapping.get(grade, [grade])
                        )
                    )
                    if grades:
                        properties["gradeLevel"] = json.dumps(
                            ensure_ascii=False, obj=grades, separators=(",", ":")
                        )
            node = {
                "type": "node",
                "identifier": row["identifier"],
                "labels": [label],
                "properties": properties,
            }
            nodes.append(node)
            identifiers[row["identifier"]] = node
            if group != "learning_components":
                cases[row["case_identifier_uuid"]] = node
    relationships = []
    for group in _GROUPS:
        rows = material.get(group, [])
        if group in _GROUPS[2:]:
            rows = sorted(rows, key=lambda row: row["identifier"])
        for row in rows:
            properties = _properties(
                fields=_COMMON
                + (
                    "date_created",
                    "date_modified",
                    "relationship_type",
                    "source_entity",
                    "source_entity_key",
                    "source_entity_value",
                    "target_entity",
                    "target_entity_key",
                    "target_entity_value",
                ),
                row=row,
            )
            endpoints = {}
            for side in ("source", "target"):
                key = row[f"{side}_entity_key"]
                node = (cases if key == "case_identifier_uuid" else identifiers)[
                    row[f"{side}_entity_value"]
                ]
                endpoints[f"{side}_identifier"] = node["identifier"]
                endpoints[f"{side}_labels"] = node["labels"]
                properties[f"{side}EntityKey"] = {
                    "case_identifier_uuid": "caseIdentifierUUID",
                    "identifier": "identifier",
                }[key]
            if row["metadata"].get("unresolved_root_fallback") is True:
                properties["resolutionStatus"] = "unresolvedRootFallback"
            if row["metadata"].get("support_confidence") is not None:
                properties["supportConfidence"] = str(
                    row["metadata"]["support_confidence"]
                )
            relationships.append(
                {
                    "type": "relationship",
                    "identifier": row["identifier"],
                    "label": row["relationship_type"],
                    "properties": properties,
                    **endpoints,
                }
            )
    return nodes, relationships


def projection_bytes(
    *, grade_mapping: dict[str, Any] | None = None, material: dict[str, Any]
) -> dict[str, bytes]:
    """Encode independently expected delivery records.

    Parameters
    ----------
    grade_mapping
        Explicit local grade mapping.
    material
        Internal graph authority.

    Returns
    -------
    dict[str, bytes]
        Canonical two-file wire oracle.
    """
    nodes, edges = delivery_records(grade_mapping=grade_mapping, material=material)
    return {
        name: b"".join(
            (
                json.dumps(
                    ensure_ascii=False, obj=row, separators=(",", ":"), sort_keys=True
                )
                + "\n"
            ).encode()
            for row in rows
        )
        for name, rows in (
            ("as_lc_lp_nodes.jsonl", nodes),
            ("as_lc_lp_relationships.jsonl", edges),
        )
    }


def write_upstream(*, harness: Any) -> None:
    """Persist valid synthetic AS+LC delivery before exercising the LP boundary.

    Parameters
    ----------
    harness
        Test-owned upstream bundle, configuration and temporary root.
    """
    harness.root.mkdir(exist_ok=True, parents=True)
    for name, payload in projection_bytes(
        grade_mapping=harness.config.academic_standards.grade_level_mapping,
        material=harness.bundle.model_dump(mode="json"),
    ).items():
        path = harness.root / name.replace("as_lc_lp_", "as_lc_")
        if not path.exists():
            path.write_bytes(payload)
