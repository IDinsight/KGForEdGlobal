"""This module contains schemas for exporting a *shape-only* Learning Commons Knowledge
Graph.

These models are intentionally **non-US-centric**:

1. All enum-like fields (jurisdiction, language, academic subject, adoption status,
    etc.) are modeled as strings.
2. Unknown/extra per-node and per-relationship details should go into `metadata`.
"""

# Future Library
from __future__ import annotations

# Standard Library
from typing import Any, Literal, Optional
from uuid import UUID

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Package Library
from skg.utils.constants import NormalizedStatementType

ALLOWED_RELATIONSHIP_TYPES = {"hasChild", "supports", "buildsTowards", "relatesTo"}
ALLOWED_ENTITY_KEYS = {"identifier", "caseIdentifierUUID"}
MetadataT = dict[str, Any]
ValidationLevel = Literal["error", "warning", "info"]


# Schemas for primitives.
class BaseModelKG(BaseModel):
    """Base model that enforces 'additionalProperties: false' in JSON schema for
    compatibility with OpenAI Structured Outputs.
    """

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        populate_by_name=True,  # Allow snake case or JSON aliases on input
    )


# Schemas for entities.
class StandardsFramework(BaseModelKG):
    """Root node for a standards framework (typically one per PDF)."""

    academic_subject: str = Field(alias="academicSubject")
    adoption_status: str = Field(alias="adoptionStatus")
    attribution_statement: str = Field(alias="attributionStatement")
    author: str
    case_identifier_uri: str = Field(alias="caseIdentifierURI")
    case_identifier_uuid: UUID = Field(alias="caseIdentifierUUID")
    date_created: Optional[str] = Field(alias="dateCreated", default=None)
    date_modified: Optional[str] = Field(alias="dateModified", default=None)
    description: Optional[str] = None
    identifier: UUID
    in_language: str = Field(alias="inLanguage")
    jurisdiction: str
    license: str
    metadata: MetadataT = Field(default_factory=dict)
    name: str
    provider: str

    @model_validator(mode="after")
    def _check_case_uri(self) -> StandardsFramework:
        """Validate that case_identifier_uri includes case_identifier_uuid.

        Returns
        -------
        StandardsFramework
            The validated StandardsFramework object.

        Raises
        ------
        ValueError
            If case_identifier_uri does not include case_identifier_uuid.
        """

        if str(self.case_identifier_uuid) not in self.case_identifier_uri:
            raise ValueError("caseIdentifierURI must include caseIdentifierUUID")

        return self


class StandardsFrameworkItem(BaseModelKG):
    """Standards item or grouping within a framework."""

    academic_subject: str = Field(alias="academicSubject")
    attribution_statement: str = Field(alias="attributionStatement")
    author: str

    # LC KG conventions (relationships commonly key off CASE ids).
    case_identifier_uri: str = Field(alias="caseIdentifierURI")
    case_identifier_uuid: UUID = Field(alias="caseIdentifierUUID")

    date_created: Optional[str] = Field(alias="dateCreated", default=None)
    date_modified: Optional[str] = Field(alias="dateModified", default=None)

    # NB: LC KG says description is optional, but *we* should keep it required so that
    # we never export a blank item.
    description: str

    # LC KG: gradeLevel is 0...n.
    grade_level: list[str] = Field(alias="gradeLevel", default_factory=list)

    metadata: MetadataT = Field(default_factory=dict)
    identifier: UUID
    in_language: str = Field(alias="inLanguage")
    jurisdiction: str
    license: str
    normalized_statement_type: NormalizedStatementType = Field(
        alias="normalizedStatementType"
    )
    provider: str
    statement_code: Optional[str] = Field(alias="statementCode", default=None)
    statement_type: Optional[str] = Field(
        alias="statementType",
        default=None,
        description="Human-readable source label (e.g., 'Subject', 'Main competence', 'Specific competence', 'Indicator', etc.).",
    )

    @model_validator(mode="after")
    def _check_case_uri(self) -> StandardsFrameworkItem:
        """Validate that case_identifier_uri includes case_identifier_uuid.

        Returns
        -------
        StandardsFrameworkItem
            The validated StandardsFrameworkItem object.

        Raises
        ------
        ValueError
            If case_identifier_uri does not include case_identifier_uuid.
        """

        if str(self.case_identifier_uuid) not in self.case_identifier_uri:
            raise ValueError("caseIdentifierURI must include caseIdentifierUUID")

        return self


class LearningComponent(BaseModelKG):
    """Granular skill/concept aligned to one or more standards items via `supports`."""

    academic_subject: str = Field(alias="academicSubject")
    attribution_statement: str = Field(alias="attributionStatement")
    author: str
    date_created: Optional[str] = Field(alias="dateCreated", default=None)
    date_modified: Optional[str] = Field(alias="dateModified", default=None)
    description: str
    identifier: UUID
    in_language: str = Field(alias="inLanguage")
    license: str
    metadata: MetadataT = Field(default_factory=dict)
    provider: str


# Schemas for relationship.
class Relationship(BaseModelKG):
    """LC KG relationship record (shared schema across relationship types)."""

    attribution_statement: str = Field(alias="attributionStatement")
    author: str
    date_created: Optional[str] = Field(alias="dateCreated", default=None)
    date_modified: Optional[str] = Field(alias="dateModified", default=None)
    description: str = ""
    identifier: UUID
    license: str
    metadata: MetadataT = Field(default_factory=dict)
    provider: str
    relationship_type: str = Field(alias="relationshipType")
    source_entity: str = Field(alias="sourceEntity")
    source_entity_key: str = Field(alias="sourceEntityKey")
    source_entity_value: str = Field(alias="sourceEntityValue")
    target_entity: str = Field(alias="targetEntity")
    target_entity_key: str = Field(alias="targetEntityKey")
    target_entity_value: str = Field(alias="targetEntityValue")

    def _validate_has_child(self) -> None:
        """Validate 'hasChild' specific constraints.

        Raises
        ------
        ValueError
            If any hasChild validation fails.
        """

        if self.target_entity != "StandardsFrameworkItem":
            raise ValueError("hasChild targetEntity must be StandardsFrameworkItem")

        if self.source_entity not in {"StandardsFramework", "StandardsFrameworkItem"}:
            raise ValueError(
                "hasChild sourceEntity must be StandardsFramework or StandardsFrameworkItem"
            )
        if (
            self.source_entity_key != "caseIdentifierUUID"
            or self.target_entity_key != "caseIdentifierUUID"
        ):
            raise ValueError("hasChild must use caseIdentifierUUID endpoints")

    def _validate_common_schema(self) -> None:
        """Validate generic allowed values for types and keys.

        Raises
        ------
        ValueError
            If any common schema validation fails.
        """

        if self.relationship_type not in ALLOWED_RELATIONSHIP_TYPES:
            raise ValueError(
                f"Unsupported relationshipType: {self.relationship_type}\n"
                f"Valid relationship types are: {ALLOWED_RELATIONSHIP_TYPES}"
            )

        if self.source_entity_key not in ALLOWED_ENTITY_KEYS:
            raise ValueError(f"Invalid sourceEntityKey: {self.source_entity_key}")

        if self.target_entity_key not in ALLOWED_ENTITY_KEYS:
            raise ValueError(f"Invalid targetEntityKey: {self.target_entity_key}")

    def _validate_data_integrity(self) -> None:
        """Validate that entity values are valid UUIDs.

        Parameters
        -------
        Raises
        ------
        ValueError
            If any entity value is not a valid UUID.
        """

        try:
            UUID(str(self.source_entity_value))
        except Exception as e:  # pylint: disable=broad-except
            raise ValueError(
                f"sourceEntityValue is not a UUID: {self.source_entity_value}"
            ) from e

        try:
            UUID(str(self.target_entity_value))
        except Exception as e:  # pylint: disable=broad-except
            raise ValueError(
                f"targetEntityValue is not a UUID: {self.target_entity_value}"
            ) from e

    def _validate_supports(self) -> None:
        """Validate 'supports' specific constraints.

        Raises
        ------
        ValueError
            If any supports validation fails.
        """

        if (
            self.source_entity != "LearningComponent"
            or self.target_entity != "StandardsFrameworkItem"
        ):
            raise ValueError(
                "supports must be LearningComponent -> StandardsFrameworkItem"
            )
        if not (
            self.source_entity_key == "identifier"
            and self.target_entity_key == "caseIdentifierUUID"
        ):
            raise ValueError(
                "supports must use source identifier + target caseIdentifierUUID"
            )

    def _validate_type_specific_logic(self) -> None:
        """Dispatch validation to specific methods based on relationship type."""

        if self.relationship_type == "hasChild":
            self._validate_has_child()
        elif self.relationship_type == "supports":
            self._validate_supports()

    @model_validator(mode="after")
    def _validate_relationship_shape(self) -> Relationship:
        """Orchestrator for relationship validation.

        Returns
        -------
        Relationship
            The validated Relationship object.
        """

        self._validate_common_schema()
        self._validate_data_integrity()
        self._validate_type_specific_logic()

        return self


# Schemas for knowledge graph export.
class KnowledgeGraphExport(BaseModelKG):
    """Schema for Knowledge Graph export."""

    export_dialect: Literal["shape_only"] = Field(
        alias="exportDialect",
        default="shape_only",
        description="This schema is always shape_only (non-US compatible).",
    )
    frameworks: list[StandardsFramework] = Field(default_factory=list)
    learning_components: list[LearningComponent] = Field(
        alias="learningComponents", default_factory=list
    )
    relationships: list[Relationship] = Field(default_factory=list)
    standards_framework_items: list[StandardsFrameworkItem] = Field(
        alias="standardsFrameworkItems", default_factory=list
    )

    def _build_id_maps(self) -> dict[str, set[str]]:
        """Extract sets of valid IDs for each entity type.

        Returns
        -------
        dict[str, set[str]]
            Mapping of entity types to sets of valid IDs.
        """

        return {
            "components": {str(lc.identifier) for lc in self.learning_components},
            "frameworks": {str(f.case_identifier_uuid) for f in self.frameworks},
            "items": {
                str(s.case_identifier_uuid) for s in self.standards_framework_items
            },
        }

    @staticmethod
    def _enforce_reference(
        *,
        expected_entity: str,
        expected_key: str,
        id_desc: str,
        rel: Relationship,
        side: Literal["source", "target"],
        valid_ids: set[str],
    ) -> None:
        """Validate that a relationship side matches expected entity type, key type,
        and points to an existing ID.

        Parameters
        ----------
        expected_entity
            The expected entity type (e.g., "LearningComponent").
        expected_key
            The expected key type (e.g., "identifier").
        id_desc
            Description of the ID for error messages.
        rel
            The Relationship to validate.
        side
            Which side of the relationship to validate ("source" or "target").
        valid_ids
            Set of valid IDs for referential integrity check.

        Raises
        ------
        ValueError
            If any validation fails.
        """

        prefix = f"{rel.relationship_type}.{side}"

        # Determine which fields to check based on side.
        entity_val = rel.source_entity if side == "source" else rel.target_entity
        key_val = rel.source_entity_key if side == "source" else rel.target_entity_key
        id_val = (
            rel.source_entity_value if side == "source" else rel.target_entity_value
        )

        # Check entity type.
        if entity_val != expected_entity:
            raise ValueError(f"{prefix}Entity must be {expected_entity}")

        # Check key type.
        if key_val != expected_key:
            raise ValueError(f"{prefix}EntityKey must be {expected_key}")

        # Check ID existence.
        if id_val not in valid_ids:
            raise ValueError(f"{prefix}EntityValue must reference a {id_desc}")

    def _validate_has_child(
        self, rel: Relationship, id_maps: dict[str, set[str]]
    ) -> None:
        """Validate 'hasChild': (Framework|SFI) -> SFI.

        Parameters
        ----------
        rel
            The Relationship to validate.
        id_maps
            Mapping of entity types to sets of valid IDs.

        Raises
        ------
        ValueError
            If any hasChild validation fails.
        """

        # Source: can be framework OR item.
        if rel.source_entity == "StandardsFramework":
            self._enforce_reference(
                expected_entity="StandardsFramework",
                expected_key="caseIdentifierUUID",
                id_desc="StandardsFramework.caseIdentifierUUID",
                rel=rel,
                side="source",
                valid_ids=id_maps["frameworks"],
            )
        elif rel.source_entity == "StandardsFrameworkItem":
            self._enforce_reference(
                expected_entity="StandardsFrameworkItem",
                expected_key="caseIdentifierUUID",
                id_desc="StandardsFrameworkItem.caseIdentifierUUID",
                rel=rel,
                side="source",
                valid_ids=id_maps["items"],
            )
        else:
            raise ValueError(
                "hasChild.sourceEntity must be StandardsFramework or StandardsFrameworkItem"
            )

        # Target: StandardsFrameworkItem.
        self._enforce_reference(
            expected_entity="StandardsFrameworkItem",
            expected_key="caseIdentifierUUID",
            id_desc="StandardsFrameworkItem.caseIdentifierUUID",
            rel=rel,
            side="target",
            valid_ids=id_maps["items"],
        )

    def _validate_sfi_connection(
        self, rel: Relationship, id_maps: dict[str, set[str]]
    ) -> None:
        """Validate 'buildsTowards'/'relatesTo': SFI -> SFI.

        Parameters
        ----------
        rel
            The Relationship to validate.
        id_maps
            Mapping of entity types to sets of valid IDs.
        """

        # Source.
        self._enforce_reference(
            expected_entity="StandardsFrameworkItem",
            expected_key="caseIdentifierUUID",
            id_desc="StandardsFrameworkItem.caseIdentifierUUID",
            rel=rel,
            side="source",
            valid_ids=id_maps["items"],
        )

        # Target.
        self._enforce_reference(
            expected_entity="StandardsFrameworkItem",
            expected_key="caseIdentifierUUID",
            id_desc="StandardsFrameworkItem.caseIdentifierUUID",
            rel=rel,
            side="target",
            valid_ids=id_maps["items"],
        )

    def _validate_single_relationship(
        self, rel: Relationship, id_maps: dict[str, set[str]]
    ) -> None:
        """Dispatch validation based on relationship type.

        Parameters
        ----------
        rel
            The Relationship to validate.
        id_maps
            Mapping of entity types to sets of valid IDs.
        """

        if rel.relationship_type == "supports":
            self._validate_supports(rel, id_maps)
        elif rel.relationship_type == "hasChild":
            self._validate_has_child(rel, id_maps)
        elif rel.relationship_type in {"buildsTowards", "relatesTo"}:
            self._validate_sfi_connection(rel, id_maps)

    def _validate_supports(
        self, rel: Relationship, id_maps: dict[str, set[str]]
    ) -> None:
        """Validate 'supports': LC -> SFI.

        Parameters
        ----------
        rel
            The Relationship to validate.
        id_maps
            Mapping of entity types to sets of valid IDs.
        """

        # Source: LearningComponent (identifier).
        self._enforce_reference(
            expected_entity="LearningComponent",
            expected_key="identifier",
            id_desc="LearningComponent.identifier",
            rel=rel,
            side="source",
            valid_ids=id_maps["components"],
        )

        # Target: StandardsFrameworkItem (caseIdentifierUUID).
        self._enforce_reference(
            expected_entity="StandardsFrameworkItem",
            expected_key="caseIdentifierUUID",
            id_desc="StandardsFrameworkItem.caseIdentifierUUID",
            rel=rel,
            side="target",
            valid_ids=id_maps["items"],
        )

    @model_validator(mode="after")
    def _validate_integrity(self) -> KnowledgeGraphExport:
        """Validate referential integrity of relationships against entities.

        Returns
        -------
        KnowledgeGraphExport
            The validated KnowledgeGraphExport object.
        """

        id_maps = self._build_id_maps()

        for rel in self.relationships:
            self._validate_single_relationship(rel, id_maps)

        return self


# Schemas for export configuration and utilities.
class KnowledgeGraphConfig(BaseModelKG):
    """Configuration for CanonicalIR --> LC-KG export.

    Notes
    -----
    1. export_dialect defaults to "shape_only". We *can* keep "strict" as an option for
        internal experiments, but the schemas/models are intentionally non-US-centric.
    2. namespace_uuid MUST be pinned and never changed once you start generating IDs.
    """

    academic_subject_default: str
    adoption_status: str
    attribution_statement: str
    author: str
    case_uri_base: str = Field(
        default="urn:lc:case:",
        description="Stable CASE identifier URI prefix (e.g., urn:lc:case:).",
    )
    description_text_policy: Literal["source", "prefer_text_en"] = "source"
    enable_normative_safety_overrides: bool = True
    export_dialect: Literal["shape_only"] = "shape_only"
    export_in_language_policy: Literal["default", "source"] = "source"
    include_descriptors: bool = True
    include_guidance: bool = False
    generate_learning_components: bool = True
    jurisdiction_default: str
    language_default: str
    license: str
    namespace_uuid: UUID = Field(
        default=UUID("b9a2b2d5-0f6c-4f3f-8d32-b7a66f999c5a"),
        description="Pinned UUID namespace used with uuid5 for deterministic IDs.",
    )
    provider: str
    prune_dead_groupings: bool = Field(
        default=False,
        description=(
            "If true, drop grouping nodes (SUBJECT/GRADE_LEVEL/SECTION/STRAND/TOPIC) "
            "that have no EXPECTATION descendant, and then drop any nodes that become "
            "unreachable from the framework after that pruning step."
        ),
    )

    @model_validator(mode="after")
    def _validate_stable_bases(self) -> KnowledgeGraphConfig:
        """Validate that case_uri_base is non-empty and stable.

        Returns
        -------
        KnowledgeGraphConfig
            The validated KnowledgeGraphConfig object.

        Raises
        ------
        ValueError
            If case_uri_base is empty.
        """

        if not self.case_uri_base:
            raise ValueError("case_uri_base must be non-empty and stable.")

        return self


# Schemas for graph validation reporting.
class GraphValidationIssue(BaseModelKG):
    """A single validation finding."""

    code: str
    context: dict[str, Any] = Field(default_factory=dict)
    level: ValidationLevel
    message: str


class GraphValidationReport(BaseModelKG):
    """Accumulates validation issues and basic knowledge graph building stats."""

    doc_key: Optional[str] = None
    issues: list[GraphValidationIssue] = Field(default_factory=list)
    pdf_name: Optional[str] = None
    stats: dict[str, Any] = Field(default_factory=dict)

    def add(
        self,
        *,
        code: str,
        context: Optional[dict[str, Any]] = None,
        level: ValidationLevel,
        message: str,
    ) -> None:
        """Add a validation issue.

        Parameters
        ----------
        code
            Short machine-readable code for the issue.
        context
            Optional additional context for debugging.
        level
            Severity level of the issue.
        message
            Human-readable description of the issue.
        """

        self.issues.append(
            GraphValidationIssue(
                code=code, context=context or {}, level=level, message=message
            )
        )

    def error(
        self, code: str, message: str, context: Optional[dict[str, Any]] = None
    ) -> None:
        """Add an error-level issue.

        Parameters
        ----------
        code
            Short machine-readable code for the issue.
        message
            Human-readable description of the issue.
        context
            Optional additional context for debugging.
        """

        self.add(level="error", code=code, message=message, context=context)

    def errors(self) -> list[GraphValidationIssue]:
        """Get all error-level issues.

        Returns
        -------
        list[GraphValidationIssue]
            List of error-level issues.
        """

        return [i for i in self.issues if i.level == "error"]

    def has_errors(self) -> bool:
        """Check if any error-level issues are present.

        Returns
        -------
        bool
            True if any error-level issues are present, False otherwise.
        """

        return any(i.level == "error" for i in self.issues)

    def info(
        self, code: str, message: str, context: Optional[dict[str, Any]] = None
    ) -> None:
        """Add an info-level issue.

        Parameters
        ----------
        code
            Short machine-readable code for the issue.
        message
            Human-readable description of the issue.
        context
            Optional additional context for debugging.
        """

        self.add(level="info", code=code, message=message, context=context)

    def raise_if_errors(self) -> None:
        """Raise a ValueError if any errors are present in the report.

        Raises
        ------
        ValueError
            If any errors are present in the report.
        """

        if not self.has_errors():
            return

        # Keep the exception message readable.
        lines = ["CanonicalIR pre-validation failed:"]
        for i in self.errors()[:15]:
            lines.append(f"- [{i.code}] {i.message}")
        if len(self.errors()) > 15:
            lines.append(f"- ... plus {len(self.errors()) - 15} more errors")

        raise ValueError("\n".join(lines))

    def warn(
        self, code: str, message: str, context: Optional[dict[str, Any]] = None
    ) -> None:
        """Add a warning issue.

        Parameters
        ----------
        code
            Short machine-readable code for the issue.
        message
            Human-readable description of the issue.
        context
            Optional additional context for debugging.
        """

        self.add(level="warning", code=code, message=message, context=context)

    def warnings(self) -> list[GraphValidationIssue]:
        """Get all warning-level issues.

        Returns
        -------
        list[GraphValidationIssue]
            List of warning-level issues.
        """

        return [i for i in self.issues if i.level == "warning"]
