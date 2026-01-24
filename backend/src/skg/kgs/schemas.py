"""This module contains schemas for exporting a *shape-preserving* Learning Commons
Knowledge Graph.

These models are intentionally **non-US-centric**:

1. All enum-like fields (jurisdiction, language, academic subject, adoption status,
    etc.) are modeled as strings.
2. Unknown/extra per-node and per-relationship details should go into `metadata`.
"""

# Future Library
from __future__ import annotations

# Standard Library
from datetime import datetime
from typing import Any, Literal, Optional
from urllib.parse import urlparse
from uuid import UUID

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Package Library
from skg.utils.constants import NormalizedStatementType

AllowedRelationshipTypes = {"hasChild", "supports", "buildsTowards", "relatesTo"}
AllowedEntityKeys = {"identifier", "caseIdentifierUUID"}
ExportDialect = Literal["lc_public_strict", "global_relaxed"]
MetadataT = dict[str, Any]
ProgressionGranularity = Literal["coarse", "fine", "auto"]
ProgressionSource = Literal["progression_ir", "llm"]
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


# Schemas for nodes.
class StandardsFramework(BaseModelKG):
    """Root node for a standards framework (typically one per PDF).

    This represents the top-level standards document/container in the LC KG. All
    StandardsFrameworkItems (SFIs) should be reachable from this framework via
    `hasChild` relationships.
    """

    academic_subject: str = Field(
        alias="academicSubject",
        description=(
            "High-level academic subject classification for the framework "
            "(e.g., Mathematics, English Language Arts, Science). "
            "In `lc_public_strict`, this should conform to LC enum values; "
            "in `global_relaxed`, free-form values are allowed."
        ),
    )
    adoption_status: str = Field(
        alias="adoptionStatus",
        description=(
            "Adoption status of the framework (e.g., Draft, Adopted). "
            "In `lc_public_strict`, this should conform to LC enum values; "
            "in `global_relaxed`, free-form values are allowed."
        ),
    )
    attribution_statement: str = Field(
        alias="attributionStatement",
        description=(
            "Attribution text required to credit the original publisher/owner "
            "of the standards framework (e.g., Ministry of Education, year, source)."
        ),
    )
    author: str = Field(
        description=(
            "Human or organization name considered the author/owner of the framework "
            "(e.g., 'Ministry of Education (Zambia)')."
        ),
    )
    case_identifier_uri: str = Field(
        alias="caseIdentifierURI",
        description=(
            "Stable URI identifier for the framework object. LC KG aligns with "
            "CASE-style identifiers; for non-CASE sources this may be a synthetic "
            "deterministic URI/URN minted by the pipeline (e.g., urn:uuid:<uuid>)."
        ),
    )
    case_identifier_uuid: UUID = Field(
        alias="caseIdentifierUUID",
        description=(
            "Stable UUID identifier for the framework object. In LC KG/CASE contexts, "
            "this is used as a stable cross-system identifier. For non-CASE sources, "
            "this may be a synthetic deterministic UUIDv5 minted by the pipeline."
        ),
    )
    date_created: Optional[str] = Field(
        alias="dateCreated",
        default=None,
        description=(
            "Creation timestamp for the framework (ISO-8601 string), if known. "
            "Optional; often unavailable for PDFs."
        ),
    )
    date_modified: Optional[str] = Field(
        alias="dateModified",
        default=None,
        description=(
            "Last-modified timestamp for the framework (ISO-8601 string), if known. "
            "Optional; often unavailable for PDFs."
        ),
    )
    description: Optional[str] = Field(
        default=None,
        description=(
            "Human-readable description of the framework. Optional; may be generated "
            "from document metadata or left empty."
        ),
    )
    identifier: UUID = Field(
        description=(
            "Primary internal identifier for this entity in the export. Must be "
            "deterministic across reruns (UUIDv5 recommended)."
        ),
    )
    in_language: str = Field(
        alias="inLanguage",
        description=(
            "Language tag for the framework (e.g., en-US). In `lc_public_strict`, "
            "this should conform to LC enum values; in `global_relaxed`, any valid "
            "BCP-47 language tag is allowed."
        ),
    )
    jurisdiction: str = Field(
        description=(
            "Jurisdiction that issued the framework (e.g., Zambia, Uganda). "
            "In `lc_public_strict`, this may require an LC-safe fallback "
            "(with the true value stored in provenance)."
        ),
    )
    license: str = Field(
        description=(
            "License string for the framework content. This may be an SPDX-like label "
            "or a publisher-defined license statement; must be present even if it is "
            "a conservative placeholder."
        ),
    )
    metadata: MetadataT = Field(
        default_factory=dict,
        description=(
            "Free-form metadata for pipeline/internal use (e.g., doc_key, source PDF name, "
            "dialect fallback details). This should not be relied on as LC KG canonical fields."
        ),
    )
    name: str = Field(
        description=(
            "Human-readable name/title of the framework, typically derived from the PDF title "
            "or cover page (e.g., 'Lower Primary Education Syllabi Grade 1–3 (2024)')."
        ),
    )
    notes: Optional[str] = Field(
        default=None,
        description=(
            "Optional notes field for additional human-readable context. "
            "This is not always populated; use for brief clarifications."
        ),
    )
    provider: str = Field(
        description=(
            "Provider/host name for the exported KG dataset (often your organization/product). "
            "Used for attribution and provenance in downstream systems."
        ),
    )

    @field_validator(
        "academic_subject",
        "adoption_status",
        "attribution_statement",
        "author",
        "case_identifier_uri",
        "in_language",
        "jurisdiction",
        "license",
        "name",
        "provider",
        mode="before",
    )
    @classmethod
    def _strip_and_require_non_empty(cls, v: str) -> str:
        """Strip whitespace and require non-empty string for required fields.

        Parameters
        ----------
        v
            The input string value to validate.

        Returns
        -------
        str
            The validated and stripped string value.

        Raises
        ------
        ValueError
            If the input value is None or an empty string after stripping.
        """

        if v is None:
            raise ValueError("Required field cannot be None")

        if not isinstance(v, str):
            raise TypeError("Expected a string")

        v2 = v.strip()

        if not v2:
            raise ValueError("Required string field cannot be empty")

        return v2

    @field_validator("case_identifier_uri")
    @classmethod
    def _validate_case_identifier_uri_is_uri_like(cls, v: str) -> str:
        """Validate caseIdentifierURI looks like a URI/URN (supports http(s), urn,
        etc.).

        Parameters
        ----------
        v
            The caseIdentifierURI string to validate.

        Returns
        -------
        str
            The validated caseIdentifierURI string.

        Raises
        ------
        ValueError
            If the caseIdentifierURI does not include a URI scheme.
        """

        parsed = urlparse(v)

        if not parsed.scheme:
            raise ValueError(
                "caseIdentifierURI must include a URI scheme (e.g., urn:, http:, https:)"
            )

        return v

    @field_validator("date_created", "date_modified")
    @classmethod
    def _validate_iso8601_dates(cls, v: Optional[str]) -> Optional[str]:
        """Validate ISO-8601 parseability for timestamps if provided.

        Parameters
        ----------
        v
            The date string to validate.

        Returns
        -------
        Optional[str]
            The validated date string or None.

        Raises
        ------
        TypeError
            If the input is not a string or None.
        ValueError
            If the input string is not a valid ISO-8601 datetime.
        """

        if v is None:
            return None

        if not isinstance(v, str):
            raise TypeError("dateCreated/dateModified must be ISO-8601 strings or None")

        v2 = v.strip()

        if not v2:
            return None

        # Accept common ISO-8601 forms; supports "Z" suffix via replace.
        try:
            datetime.fromisoformat(v2.replace("Z", "+00:00"))
        except Exception as e:
            raise ValueError(f"Invalid ISO-8601 datetime string: {v2}") from e

        return v2

    @model_validator(mode="after")
    def _check_case_uri_contains_uuid(self) -> StandardsFramework:
        """Validate that caseIdentifierURI includes caseIdentifierUUID (deterministic
        traceability).

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

    @model_validator(mode="after")
    def _check_modified_not_before_created(self) -> StandardsFramework:
        """If both dates exist, ensure dateModified >= dateCreated.

        Returns
        -------
        StandardsFramework
            The validated StandardsFramework object.

        Raises
        ------
        ValueError
            If dateModified is before dateCreated.
        """

        if self.date_created and self.date_modified:
            created = datetime.fromisoformat(self.date_created.replace("Z", "+00:00"))
            modified = datetime.fromisoformat(self.date_modified.replace("Z", "+00:00"))

            if modified < created:
                raise ValueError("dateModified must be >= dateCreated")

        return self


class StandardsFrameworkItem(BaseModelKG):
    """Standards item or grouping within a standards framework.

    This is the primary node type in the academic standards hierarchy. Both
    organizational groupings (e.g., Grade, Subject, Topic) and normative learning
    expectations (e.g., outcomes/competences/objectives) are represented using this
    entity type. Hierarchy is represented via `hasChild` edges.
    """

    academic_subject: str = Field(
        alias="academicSubject",
        description=(
            "High-level academic subject classification for the item. "
            "In strict exports this should conform to LC enums; "
            "in relaxed exports this may be a free-form subject label."
        ),
    )
    attribution_statement: str = Field(
        alias="attributionStatement",
        description=(
            "Attribution text required to credit the original publisher/owner "
            "of the standards content that this item derives from."
        ),
    )
    author: str = Field(
        description=(
            "Human or organization name considered the author/owner of this standards item, "
            "typically inherited from the framework (e.g., Ministry of Education)."
        ),
    )

    # LC KG conventions (relationships commonly key off CASE ids).
    case_identifier_uri: str = Field(
        alias="caseIdentifierURI",
        description=(
            "Stable URI identifier for this standards item. LC KG commonly aligns with "
            "CASE-style URIs. For non-CASE sources, this may be a synthetic deterministic "
            "URI/URN minted by the pipeline (e.g., urn:uuid:<uuid>)."
        ),
    )

    case_identifier_uuid: UUID = Field(
        alias="caseIdentifierUUID",
        description=(
            "Stable UUID identifier for this standards item. Used by LC KG exports as a "
            "canonical cross-object key for relationships (hasChild/buildsTowards/relatesTo). "
            "For non-CASE sources, this should be deterministic (UUIDv5 recommended)."
        ),
    )
    date_created: Optional[str] = Field(
        alias="dateCreated",
        default=None,
        description=(
            "Creation timestamp for this item (ISO-8601 string), if known. Optional."
        ),
    )
    date_modified: Optional[str] = Field(
        alias="dateModified",
        default=None,
        description=(
            "Last-modified timestamp for this item (ISO-8601 string), if known. Optional."
        ),
    )

    # NB: LC KG says description is optional, but *we* keep it required so that we
    # never export a blank item.
    description: str = Field(
        description=(
            "Primary human-readable text of the standards item. "
            "For grouping items, this is typically the label/title (e.g., 'Grade 2'). "
            "For normative items, this is the learning expectation statement."
        ),
    )

    # LC KG: gradeLevel is 0...n.
    grade_level: list[str] = Field(
        alias="gradeLevel",
        default_factory=list,
        description=(
            "Zero or more grade-level tags associated with this item (e.g., ['Grade 2']). "
            "May be empty for non-grade-banded or stage-banded frameworks."
        ),
    )

    metadata: MetadataT = Field(
        default_factory=dict,
        description=(
            "Free-form metadata for pipeline/internal use (e.g., canonical node id, "
            "source PDF provenance pointers, dialect fallbacks). Not a core LC KG field."
        ),
    )
    identifier: UUID = Field(
        description=(
            "Primary internal identifier for this entity in the export. Must be deterministic "
            "across reruns (UUIDv5 recommended)."
        ),
    )
    in_language: str = Field(
        alias="inLanguage",
        description=(
            "Language tag for the item text (e.g., en-US). "
            "In strict exports this should conform to LC enums; "
            "in relaxed exports any valid BCP-47 language tag is allowed."
        ),
    )
    jurisdiction: str = Field(
        description=(
            "Jurisdiction that issued the standards (e.g., Zambia, Uganda). "
            "In strict exports this may require a fallback value; store original in provenance."
        ),
    )
    license: str = Field(
        description=(
            "License string for the standards content. Must be present even if it is "
            "a conservative placeholder when the original license is unknown."
        ),
    )
    normalized_statement_type: NormalizedStatementType = Field(
        alias="normalizedStatementType",
        description=(
            "Normalized LC statement classification. Typical values include: "
            "'Standard' for normative expectations, 'Standard Grouping' for organizational "
            "nodes, and 'Other' for descriptors/indicators/guidance depending on policy."
        ),
    )
    notes: Optional[str] = Field(
        default=None, description="Optional human-readable notes/context."
    )
    provider: str = Field(
        description=(
            "Provider/host name for the exported KG dataset (often your organization/product). "
            "Used for attribution and provenance in downstream systems."
        ),
    )
    statement_code: Optional[str] = Field(
        alias="statementCode",
        default=None,
        description=(
            "Stable code/notation for this item from the source framework, if available "
            "(e.g., '2.1.5.1'). This is a key traceability aid and may support progression inference."
        ),
    )
    statement_type: Optional[str] = Field(
        alias="statementType",
        default=None,
        description=(
            "Human-readable source label for the item (e.g., 'Subject', 'Topic', "
            "'Specific competence', 'Expected Standard', 'Indicator'). "
            "This is not the normalized LC type; it preserves source semantics."
        ),
    )

    @field_validator(
        "academic_subject",
        "attribution_statement",
        "author",
        "case_identifier_uri",
        "description",
        "in_language",
        "jurisdiction",
        "license",
        "provider",
        mode="before",
    )
    @classmethod
    def _strip_and_require_non_empty(cls, v: str) -> str:
        """Strip whitespace and require non-empty string for required fields.

        Parameters
        ----------
        v
            The input string value to validate.

        Returns
        -------
        str
            The validated and stripped string value.

        Raises
        ------
        ValueError
            If the input value is None or an empty string after stripping.
        """

        if v is None:
            raise ValueError("Required field cannot be None")

        if not isinstance(v, str):
            raise TypeError("Expected a string")

        v2 = v.strip()

        if not v2:
            raise ValueError("Required string field cannot be empty")

        return v2

    @field_validator("statement_code", "statement_type", mode="before")
    @classmethod
    def _strip_optional_strings(cls, v: Optional[str]) -> Optional[str]:
        """Strip whitespace for optional string fields; treat empty as None.

        Parameters
        ----------
        v
            The input optional string value to validate.

        Returns
        -------
        Optional[str]
            The validated and stripped string value, or None.

        Raises
        ------
        TypeError
            If the input is not a string or None.
        """

        if v is None:
            return None

        if not isinstance(v, str):
            raise TypeError("Expected a string or None")

        v2 = v.strip()

        return v2 if v2 else None

    @field_validator("case_identifier_uri")
    @classmethod
    def _validate_case_identifier_uri_is_uri_like(cls, v: str) -> str:
        """Validate caseIdentifierURI looks like a URI/URN (supports http(s), urn,
        etc.).

        Parameters
        ----------
        v
            The caseIdentifierURI string to validate.

        Returns
        -------
        str
            The validated caseIdentifierURI string.

        Raises
        ------
        ValueError
            If the caseIdentifierURI does not include a URI scheme.
        """

        parsed = urlparse(v)

        if not parsed.scheme:
            raise ValueError(
                "caseIdentifierURI must include a URI scheme (e.g., urn:, http:, https:)"
            )

        return v

    @field_validator("date_created", "date_modified")
    @classmethod
    def _validate_iso8601_dates(cls, v: Optional[str]) -> Optional[str]:
        """Validate ISO-8601 parseability for timestamps if provided.

        Parameters
        ----------
        v
            The date string to validate.

        Returns
        -------
        Optional[str]
            The validated date string or None.

        Raises
        ------
        TypeError
            If the input is not a string or None.
        ValueError
            If the input string is not a valid ISO-8601 datetime.
        """

        if v is None:
            return None

        if not isinstance(v, str):
            raise TypeError("dateCreated/dateModified must be ISO-8601 strings or None")

        v2 = v.strip()

        if not v2:
            return None

        try:
            datetime.fromisoformat(v2.replace("Z", "+00:00"))
        except Exception as e:
            raise ValueError(f"Invalid ISO-8601 datetime string: {v2}") from e

        return v2

    @field_validator("grade_level")
    @classmethod
    def _validate_grade_level(cls, v: list[str]) -> list[str]:
        """Ensure gradeLevel entries are non-empty strings, de-duplicated, and
        stable-ordered.

        Parameters
        ----------
        v
            The list of grade level strings to validate.

        Returns
        -------
        list[str]
            The validated list of grade level strings.

        Raises
        ------
        TypeError
            If the input is not a list of strings or contains non-string items.
        """

        if v is None:
            return []

        if not isinstance(v, list):
            raise TypeError("gradeLevel must be a list of strings")

        cleaned: list[str] = []
        seen: set[str] = set()

        for item in v:
            if not isinstance(item, str):
                raise TypeError("gradeLevel must contain only strings")

            s = item.strip()

            if not s:
                continue

            if s not in seen:
                cleaned.append(s)
                seen.add(s)

        return cleaned

    @model_validator(mode="after")
    def _check_case_uri_contains_uuid(self) -> StandardsFrameworkItem:
        """Validate that caseIdentifierURI includes caseIdentifierUUID (deterministic
        traceability).

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

    @model_validator(mode="after")
    def _check_modified_not_before_created(self) -> StandardsFrameworkItem:
        """If both dates exist, ensure dateModified >= dateCreated.

        Returns
        -------
        StandardsFrameworkItem
            The validated StandardsFrameworkItem object.

        Raises
        ------
        ValueError
            If dateModified is before dateCreated.
        """

        if self.date_created and self.date_modified:
            created = datetime.fromisoformat(self.date_created.replace("Z", "+00:00"))
            modified = datetime.fromisoformat(self.date_modified.replace("Z", "+00:00"))

            if modified < created:
                raise ValueError("dateModified must be >= dateCreated")

        return self

    @model_validator(mode="after")
    def _check_statement_code_not_empty_if_present(self) -> StandardsFrameworkItem:
        """If statementCode is present, it must be a non-empty trimmed string.

        Returns
        -------
        StandardsFrameworkItem
            The validated StandardsFrameworkItem object.

        Raises
        ------
        ValueError
            If statementCode is an empty string.
        """

        if self.statement_code is not None and not self.statement_code.strip():
            raise ValueError("statementCode must be non-empty when provided")

        return self


class LearningComponent(BaseModelKG):
    """Granular skill/concept aligned to one or more standards items via `supports`.

    LearningComponents represent skill/concept units that can be aligned to
    StandardsFrameworkItems using `supports` relationships:

      (:LearningComponent)-[:supports]->(:StandardsFrameworkItem)
    """

    academic_subject: str = Field(
        alias="academicSubject",
        description=(
            "High-level academic subject classification for the component "
            "(e.g., Mathematics, English Language Arts). In strict exports this should "
            "conform to LC enum values; in relaxed exports free-form values are allowed."
        ),
    )
    attribution_statement: str = Field(
        alias="attributionStatement",
        description=(
            "Attribution text required to credit the original publisher/owner of the "
            "source curriculum content that this component derives from."
        ),
    )
    author: str = Field(
        description=(
            "Human or organization name considered the author/owner of this component, "
            "typically inherited from the framework (e.g., Ministry of Education)."
        ),
    )
    date_created: Optional[str] = Field(
        alias="dateCreated",
        default=None,
        description=(
            "Creation timestamp for the component (ISO-8601 string), if known. Optional."
        ),
    )
    date_modified: Optional[str] = Field(
        alias="dateModified",
        default=None,
        description=(
            "Last-modified timestamp for the component (ISO-8601 string), if known. Optional."
        ),
    )
    description: str = Field(
        description=(
            "Primary human-readable text describing the skill/concept represented by the "
            "LearningComponent. In a 1-to-1 policy, this may be identical to the supporting "
            "standards expectation statement."
        ),
    )
    identifier: UUID = Field(
        description=(
            "Primary internal identifier for this entity in the export. Must be deterministic "
            "across reruns (UUIDv5 recommended)."
        ),
    )
    in_language: str = Field(
        alias="inLanguage",
        description=(
            "Language tag for the component text (e.g., en-US). In strict exports this should "
            "conform to LC enum values; in relaxed exports any valid BCP-47 language tag is allowed."
        ),
    )
    license: str = Field(
        description=(
            "License string for the component content. Must be present even if it is a "
            "conservative placeholder when the original license is unknown."
        ),
    )
    metadata: MetadataT = Field(
        default_factory=dict,
        description=(
            "Free-form metadata for pipeline/internal use (e.g., canonical node ids, "
            "doc_key references, provenance pointers, dialect fallback notes). "
            "Not a core LC KG field; consider omitting from strict exports."
        ),
    )
    provider: str = Field(
        description=(
            "Provider/host name for the exported KG dataset (often your organization/product). "
            "Used for attribution and provenance in downstream systems."
        ),
    )

    @field_validator(
        "academic_subject",
        "attribution_statement",
        "author",
        "description",
        "in_language",
        "license",
        "provider",
        mode="before",
    )
    @classmethod
    def _strip_and_require_non_empty(cls, v: str) -> str:
        """Strip whitespace and require non-empty string for required fields.

        Parameters
        ----------
        v
            The input string value to validate.

        Returns
        -------
        str
            The validated and stripped string value.

        Raises
        ------
        TypeError
            If the input is not a string.
        ValueError
            If the input value is None or an empty string after stripping.
        """

        if v is None:
            raise ValueError("Required field cannot be None")

        if not isinstance(v, str):
            raise TypeError("Expected a string")

        v2 = v.strip()

        if not v2:
            raise ValueError("Required string field cannot be empty")

        return v2

    @field_validator("date_created", "date_modified")
    @classmethod
    def _validate_iso8601_dates(cls, v: Optional[str]) -> Optional[str]:
        """Validate ISO-8601 parseability for timestamps if provided.

        Parameters
        ----------
        v
            The date string to validate.

        Returns
        -------
        Optional[str]
            The validated date string or None.

        Raises
        ------
        TypeError
            If the input is not a string or None.
        ValueError
            If the input string is not a valid ISO-8601 datetime.
        """

        if v is None:
            return None

        if not isinstance(v, str):
            raise TypeError("dateCreated/dateModified must be ISO-8601 strings or None")

        v2 = v.strip()

        if not v2:
            return None

        try:
            datetime.fromisoformat(v2.replace("Z", "+00:00"))
        except Exception as e:
            raise ValueError(f"Invalid ISO-8601 datetime string: {v2}") from e

        return v2

    @model_validator(mode="after")
    def _check_modified_not_before_created(self) -> LearningComponent:
        """If both dates exist, ensure dateModified >= dateCreated.

        Returns
        -------
        LearningComponent
            The validated LearningComponent object.

        Raises
        ------
        ValueError
            If dateModified is before dateCreated.
        """

        if self.date_created and self.date_modified:
            created = datetime.fromisoformat(self.date_created.replace("Z", "+00:00"))
            modified = datetime.fromisoformat(self.date_modified.replace("Z", "+00:00"))

            if modified < created:
                raise ValueError("dateModified must be >= dateCreated")

        return self


# Schemas for relationship.
class Relationship(BaseModelKG):
    """LC KG relationship record (shared schema across relationship types).

    Relationships connect two entities in the LC KG export. The meaning of the edge is
    defined by `relationshipType` (e.g., hasChild, supports, buildsTowards, relatesTo).
    """

    attribution_statement: str = Field(
        alias="attributionStatement",
        description=(
            "Attribution text required to credit the original publisher/owner of the "
            "source content that this relationship derives from."
        ),
    )
    author: str = Field(
        description=(
            "Human or organization name considered the author/owner of this relationship record, "
            "typically inherited from the framework/provider."
        ),
    )
    date_created: Optional[str] = Field(
        alias="dateCreated",
        default=None,
        description="Creation timestamp for this relationship (ISO-8601 string), if known. Optional.",
    )
    date_modified: Optional[str] = Field(
        alias="dateModified",
        default=None,
        description="Last-modified timestamp for this relationship (ISO-8601 string), if known. Optional.",
    )
    description: str = Field(
        default="",
        description=(
            "Human-readable description of the relationship. LC expects this to be present; "
            "if omitted/blank, the model will deterministically fill a canonical description."
        ),
    )
    identifier: UUID = Field(
        description=(
            "Primary internal identifier for this relationship record. Must be deterministic "
            "across reruns (UUIDv5 recommended)."
        ),
    )
    license: str = Field(
        description=(
            "License string for the relationship record. Often inherited from the provider "
            "dataset license (e.g., a CC BY URL)."
        ),
    )
    metadata: MetadataT = Field(
        default_factory=dict,
        description=(
            "Free-form metadata for pipeline/internal use (e.g., inference provenance pointers). "
            "Not part of LC’s public relationship schema; consider omitting in strict exports."
        ),
    )
    provider: str = Field(
        description=(
            "Provider/host name for the exported KG dataset (often your organization/product). "
            "Used for attribution and provenance in downstream systems."
        ),
    )
    relationship_type: str = Field(
        alias="relationshipType",
        description=(
            "Normalized relationship label defining the semantic meaning of the connection "
            "(e.g., hasChild, supports, buildsTowards, relatesTo)."
        ),
    )
    source_entity: str = Field(
        alias="sourceEntity",
        description=(
            "Entity type where the relationship originates (e.g., StandardsFramework, "
            "StandardsFrameworkItem, LearningComponent)."
        ),
    )
    source_entity_key: str = Field(
        alias="sourceEntityKey",
        description=(
            "The identifier property name on the source entity used by this relationship "
            "(e.g., identifier, caseIdentifierUUID)."
        ),
    )
    source_entity_value: str = Field(
        alias="sourceEntityValue",
        description="The identifier value of the source entity (string UUID).",
    )
    target_entity: str = Field(
        alias="targetEntity",
        description="Entity type where the relationship points (destination node type).",
    )
    target_entity_key: str = Field(
        alias="targetEntityKey",
        description=(
            "The identifier property name on the target entity used by this relationship "
            "(e.g., identifier, caseIdentifierUUID)."
        ),
    )
    target_entity_value: str = Field(
        alias="targetEntityValue",
        description="The identifier value of the target entity (string UUID).",
    )

    @field_validator(
        "attribution_statement",
        "author",
        "license",
        "provider",
        "relationship_type",
        "source_entity",
        "source_entity_key",
        "source_entity_value",
        "target_entity",
        "target_entity_key",
        "target_entity_value",
        mode="before",
    )
    @classmethod
    def _strip_and_require_non_empty(cls, v: str) -> str:
        """Strip whitespace and require non-empty string for required fields.

        Parameters
        ----------
        v
            The input string value to validate.

        Returns
        -------
        str
            The validated and stripped string value.

        Raises
        ------
        ValueError
            If the input value is None or an empty string after stripping.
        TypeError
            If the input is not a string.
        """

        if v is None:
            raise ValueError("Required field cannot be None")

        if not isinstance(v, str):
            raise TypeError("Expected a string")

        v2 = v.strip()

        if not v2:
            raise ValueError("Required string field cannot be empty")

        return v2

    @field_validator("description", mode="before")
    @classmethod
    def _strip_description(cls, v: Optional[str]) -> str:
        """Strip description; allow blank here (we deterministically fill in model
        validator).

        Parameters
        ----------
        v
            The input description string to validate.

        Returns
        -------
        str
            The validated and stripped description string (may be empty).

        Raises
        ------
        TypeError
            If the input is not a string or None.
        """

        if v is None:
            return ""

        if not isinstance(v, str):
            raise TypeError("description must be a string")

        return v.strip()

    @field_validator("date_created", "date_modified")
    @classmethod
    def _validate_iso8601_dates(cls, v: Optional[str]) -> Optional[str]:
        """Validate ISO-8601 parseability for timestamps if provided.

        Parameters
        ----------
        v
            The date string to validate.

        Returns
        -------
        Optional[str]
            The validated date string or None.

        Raises
        ------
        TypeError
            If the input is not a string or None.
        ValueError
            If the input string is not a valid ISO-8601 datetime.
        """

        if v is None:
            return None

        if not isinstance(v, str):
            raise TypeError("dateCreated/dateModified must be ISO-8601 strings or None")

        v2 = v.strip()

        if not v2:
            return None

        try:
            datetime.fromisoformat(v2.replace("Z", "+00:00"))
        except Exception as e:
            raise ValueError(f"Invalid ISO-8601 datetime string: {v2}") from e

        return v2

    def _validate_has_child(self) -> None:
        """Validate 'hasChild' constraints: (Framework|SFI) -> SFI using CASE UUID
        endpoints.

        Raises
        ------
        ValueError
            If any of the hasChild constraints are violated.
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

    def _validate_supports(self) -> None:
        """Validate 'supports' constraints: LearningComponent -> StandardsFrameworkItem.

        Raises
        ------
        ValueError
            If any of the supports constraints are violated.
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

    def _validate_progression(self) -> None:
        """Validate buildsTowards/relatesTo constraints: SFI -> SFI using CASE UUID
        endpoints.

        Raises
        ------
        ValueError
            If any of the progression constraints are violated.
        """

        if (
            self.source_entity != "StandardsFrameworkItem"
            or self.target_entity != "StandardsFrameworkItem"
        ):
            raise ValueError(
                f"{self.relationship_type} must be StandardsFrameworkItem -> StandardsFrameworkItem"
            )

        if (
            self.source_entity_key != "caseIdentifierUUID"
            or self.target_entity_key != "caseIdentifierUUID"
        ):
            raise ValueError(
                f"{self.relationship_type} must use caseIdentifierUUID endpoints"
            )

    def _validate_common_schema(self) -> None:
        """Validate allowed values for relationship types and entity keys.

        Raises
        ------
        ValueError
            If any common schema constraints are violated.
        """

        if self.relationship_type not in AllowedRelationshipTypes:
            raise ValueError(
                f"Unsupported relationshipType: {self.relationship_type}\n"
                f"Valid relationship types are: {AllowedRelationshipTypes}"
            )

        if self.source_entity_key not in AllowedEntityKeys:
            raise ValueError(f"Invalid sourceEntityKey: {self.source_entity_key}")

        if self.target_entity_key not in AllowedEntityKeys:
            raise ValueError(f"Invalid targetEntityKey: {self.target_entity_key}")

    def _validate_data_integrity(self) -> None:
        """Validate that endpoint values are valid UUID strings.

        Raises
        ------
        ValueError
            If either endpoint value is not a valid UUID string.
        """

        try:
            UUID(str(self.source_entity_value))
        except Exception as e:
            raise ValueError(
                f"sourceEntityValue is not a UUID: {self.source_entity_value}"
            ) from e

        try:
            UUID(str(self.target_entity_value))
        except Exception as e:
            raise ValueError(
                f"targetEntityValue is not a UUID: {self.target_entity_value}"
            ) from e

    def _validate_type_specific_logic(self) -> None:
        """Dispatch validation to specific methods based on relationship type."""

        if self.relationship_type == "hasChild":
            self._validate_has_child()
        elif self.relationship_type == "supports":
            self._validate_supports()
        elif self.relationship_type in {"buildsTowards", "relatesTo"}:
            self._validate_progression()

    @model_validator(mode="after")
    def _fill_missing_description(self) -> Relationship:
        """Deterministically fill description if missing/blank (LC expects it to be
        present).

        Returns
        -------
        Relationship
            The Relationship object with a filled description if it was missing.
        """

        if not self.description:
            default_map = {
                "hasChild": "A hasChild relationship links a parent framework/item to a child standards item.",
                "supports": "A supports relationship links a learning component to a standards item it supports.",
                "buildsTowards": "A buildsTowards relationship indicates prerequisite progression from one standards item to another.",
                "relatesTo": "A relatesTo relationship indicates an associative connection between two standards items.",
            }
            self.description = default_map.get(
                self.relationship_type,
                f"A {self.relationship_type} relationship between {self.source_entity} and {self.target_entity}.",
            )

        return self

    @model_validator(mode="after")
    def _check_modified_not_before_created(self) -> Relationship:
        """If both dates exist, ensure dateModified >= dateCreated.

        Returns
        -------
        Relationship
            The validated Relationship object.

        Raises
        ------
        ValueError
            If dateModified is before dateCreated.
        """

        if self.date_created and self.date_modified:
            created = datetime.fromisoformat(self.date_created.replace("Z", "+00:00"))
            modified = datetime.fromisoformat(self.date_modified.replace("Z", "+00:00"))

            if modified < created:
                raise ValueError("dateModified must be >= dateCreated")

        return self

    @model_validator(mode="after")
    def _prevent_self_loops(self) -> Relationship:
        """Prevent self-loop relationships (especially harmful for progressions/tree
        edges).

        Returns
        -------
        Relationship
            The validated Relationship object.

        Raises
        ------
        ValueError
            If the relationship connects an entity to itself.
        """

        if (
            self.source_entity == self.target_entity
            and self.source_entity_key == self.target_entity_key
            and self.source_entity_value == self.target_entity_value
        ):
            raise ValueError("Relationship cannot connect an entity to itself")

        return self

    @model_validator(mode="after")
    def _validate_relationship_shape(self) -> Relationship:
        """Orchestrator for relationship validation."""

        self._validate_common_schema()
        self._validate_data_integrity()
        self._validate_type_specific_logic()

        return self


# Schemas for provenance.
class BBox(BaseModelKG):
    """Bounding box in pixel coordinates."""

    coord_space: Literal["px"] = "px"
    x0: float = Field(..., description="Left coordinate in pixels.", ge=0.0)
    y0: float = Field(..., description="Top coordinate in pixels.", ge=0.0)
    x1: float = Field(..., description="Right coordinate in pixels.", ge=0.0)
    y1: float = Field(..., description="Bottom coordinate in pixels.", ge=0.0)


class EntityProvenance(BaseModelKG):
    """Provenance information for a node or relationship."""

    bbox: Optional[BBox] = None
    canonical_node_id: str = Field(alias="canonicalNodeId")
    dialect_fallbacks: dict[str, str] = Field(
        alias="dialectFallbacks", default_factory=dict
    )
    entity_identifier: UUID = Field(alias="entityIdentifier")
    local_code: Optional[str] = Field(alias="localCode", default=None)
    page_indices: list[int] = Field(alias="pageIndices", default_factory=list)
    role: str
    section_path_text: list[str] = Field(alias="sectionPathText", default_factory=list)
    source_decision_ids: list[str] = Field(
        alias="sourceDecisionIds", default_factory=list
    )
    source_segment_ids: list[str] = Field(
        alias="sourceSegmentIds", default_factory=list
    )
    text: Optional[str] = None
    text_en: Optional[str] = Field(alias="textEn", default=None)


class ProgressionProvenance(BaseModelKG):
    """Provenance information for a progression relationship."""

    confidence: float = Field(ge=0.0, le=1.0)
    evidence_node_ids: list[str] = Field(alias="evidenceNodeIds", default_factory=list)
    explanation: Optional[str] = None
    inference_source: Literal["progression_ir", "llm"] = Field(alias="inferenceSource")
    granularity: Literal["coarse", "fine"] = "coarse"
    llm_model: Optional[str] = Field(alias="llmModel", default=None)
    prompt_hash: Optional[str] = Field(alias="promptHash", default=None)
    relationship_identifier: UUID = Field(alias="relationshipIdentifier")


class RelationshipProvenance(BaseModelKG):
    """Provenance information for a relationship."""

    evidence_node_ids: list[str] = Field(alias="evidenceNodeIds", default_factory=list)
    evidence_page_indices: list[int] = Field(
        alias="evidencePageIndices", default_factory=list
    )
    relationship_identifier: UUID = Field(alias="relationshipIdentifier")
    relationship_type: str = Field(alias="relationshipType")
    source_uuid: UUID = Field(alias="sourceUuid")
    target_uuid: UUID = Field(alias="targetUuid")


# Schemas for knowledge graph export.
class KnowledgeGraphExport(BaseModelKG):
    """Schema for Knowledge Graph export."""

    export_dialect: ExportDialect = Field(
        alias="exportDialect",
        default="global_relaxed",
        description="Export validation mode: strict LC enums vs relaxed global strings.",
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
    export_dialect: ExportDialect = "global_relaxed"
    export_in_language_policy: Literal["default", "source"] = "source"
    include_descriptors: bool = True
    include_guidance: bool = False
    generate_learning_components: bool = True
    generate_progressions: bool = True
    jurisdiction_default: str
    language_default: str
    learning_component_policy: Literal["1_to_1", "split_bullets"] = "1_to_1"
    lc_max_splits_per_standard: int = Field(
        default=25,
        description="Maximum number of LearningComponents to emit per Standard SFI when splitting.",
        ge=1,
    )
    license: str
    max_progression_edges_per_node: int = Field(default=3, ge=1)
    namespace_uuid: UUID = Field(
        default=UUID("b9a2b2d5-0f6c-4f3f-8d32-b7a66f999c5a"),
        description="Pinned UUID namespace used with uuid5 for deterministic IDs.",
    )
    progression_granularity: ProgressionGranularity = "auto"
    progression_min_confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    progression_source: ProgressionSource = "llm"
    provider: str
    prune_empty_groupings: bool = Field(
        default=True,
        description="If true, drop grouping StandardsFrameworkItems that have zero exported children after filtering, repeating to a fixpoint. No reattachment is performed.",
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


class EntityProvenanceExport(BaseModelKG):
    """Schema for entity provenance export."""

    entities: list[EntityProvenance] = Field(default_factory=list)


class RelationshipProvenanceExport(BaseModelKG):
    """Schema for relationship provenance export."""

    relationships: list[RelationshipProvenance] = Field(default_factory=list)


class ProgressionProvenanceExport(BaseModelKG):
    """Schema for progression provenance export."""

    progressions: list[ProgressionProvenance] = Field(default_factory=list)


class HierarchyOrderExport(BaseModelKG):
    """Schema for exporting explicit ordering of child SFIs under parent SFIs."""

    order: dict[str, list[str]] = Field(default_factory=dict)


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
