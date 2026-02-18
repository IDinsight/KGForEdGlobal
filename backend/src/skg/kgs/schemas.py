"""This module contains schemas for exporting a *shape-preserving* Learning Commons
Knowledge Graph.

These models are intentionally **non-US-centric**:

1. All enum-like fields (jurisdiction, language, academic subject, adoption status,
    etc.) are modeled as strings.
2. Unknown/extra per-node and per-relationship details should go into `metadata`.
3. `notes` are for free use.
"""

# Future Library
from __future__ import annotations

# Standard Library
from datetime import datetime
from typing import Any, Literal, Optional
from urllib.parse import urlparse
from uuid import UUID

# Third Party Library
from pydantic import Field, field_validator, model_validator

# Package Library
from skg.page_ir_extraction.schemas import TextUnit
from skg.schemas import BaseSchema, ExportDialect
from skg.utils.constants import NodeRole, StatementRole

AllowedRelationshipTypes = {"hasChild", "supports", "buildsTowards", "relatesTo"}
AllowedEntityKeys = {"identifier", "case_identifier_uuid"}
MetadataT = dict[str, Any]
NormalizedStatementType = Literal["Standard", "Standard Grouping", "Other"]
ValidationLevel = Literal["error", "warning", "info"]


# Schemas for LLM responses.
class ProgressionEdge(BaseSchema):
    """A single suggested edge between two StandardsFrameworkItems."""

    confidence: float = Field(
        description="0..1 calibrated confidence (higher = more certain).",
        ge=0.0,
        le=1.0,
    )
    rationale: str = Field(
        description="Brief rationale for the edge (>= 50 chars).",
        min_length=50,
    )
    source_sfi_uuid: str = Field(description="UUID string of the source SFI.")
    target_sfi_uuid: str = Field(description="UUID string of the target SFI.")

    @field_validator("rationale", mode="before")
    @classmethod
    def _strip_rationale(cls, v: Any) -> str:
        """Strip whitespace and validate that rationale is a string of at least 50
        characters.

        Parameters
        ----------
        v
            The input value to validate.

        Returns
        -------
        str
            The validated and stripped rationale string.

        Raises
        ------
        ValueError
            If the rationale is not a string or is less than 50 characters after
            stripping.
        """

        s = str(v or "").strip()

        if len(s) < 50:
            raise ValueError("rationale must be >= 50 characters")

        return s

    @field_validator("source_sfi_uuid", "target_sfi_uuid", mode="before")
    @classmethod
    def _strip_uuid_str(cls, v: Any) -> str:
        """Strip whitespace and validate that the value is a non-empty string for UUID
        fields.

        Parameters
        ----------
        v
            The input value to validate.

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
            raise ValueError("UUID cannot be null")

        s = str(v).strip()

        if not s:
            raise ValueError("UUID cannot be empty")

        return s


class ProgressionEdgesResponse(BaseSchema):
    """Top-level structured response: a list of edges (may be empty)."""

    edges: list[ProgressionEdge] = Field(default_factory=list)


# Schemas for nodes.
class StandardsFramework(BaseSchema):
    """Root node for a standards framework (typically one per PDF).

    This represents the top-level standards document/container in the LC KG. All
    StandardsFrameworkItems (SFIs) should be reachable from this framework via
    `hasChild` relationships.
    """

    academic_subject: str = Field(
        description=(
            "High-level academic subject classification for the framework "
            "(e.g., Mathematics, English Language Arts, Science). "
            "In `lc_public_strict`, this should conform to LC enum values; "
            "in `global_relaxed`, free-form values are allowed."
        ),
    )
    adoption_status: str = Field(
        description=(
            "Adoption status of the framework (e.g., Draft, Adopted). "
            "In `lc_public_strict`, this should conform to LC enum values; "
            "in `global_relaxed`, free-form values are allowed."
        ),
    )
    attribution_statement: str = Field(
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
        description=(
            "Stable URI identifier for the framework object. LC KG aligns with "
            "CASE-style identifiers; for non-CASE sources this may be a synthetic "
            "deterministic URI/URN minted by the pipeline (e.g., urn:uuid:<uuid>)."
        ),
    )
    case_identifier_uuid: UUID = Field(
        description=(
            "Stable UUID identifier for the framework object. In LC KG/CASE contexts, "
            "this is used as a stable cross-system identifier. For non-CASE sources, "
            "this may be a synthetic deterministic UUIDv5 minted by the pipeline."
        ),
    )
    date_created: Optional[str] = Field(
        default=None,
        description=(
            "Creation timestamp for the framework (ISO-8601 string), if known. "
            "Optional; often unavailable for PDFs."
        ),
    )
    date_modified: Optional[str] = Field(
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
        """Validate case_identifier_uri looks like a URI/URN (supports http(s), urn,
        etc.).

        Parameters
        ----------
        v
            The case_identifier_uri string to validate.

        Returns
        -------
        str
            The validated case_identifier_uri string.

        Raises
        ------
        ValueError
            If the case_identifier_uri does not include a URI scheme.
        """

        parsed = urlparse(v)

        if not parsed.scheme:
            raise ValueError(
                "case_identifier_uri must include a URI scheme (e.g., urn:, http:, https:)"
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
        """Validate that case_identifier_uri includes case_identifier_uuid (deterministic
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
            raise ValueError("case_identifier_uri must include case_identifier_uuid")

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


class StandardsFrameworkItem(BaseSchema):
    """Standards item or grouping within a standards framework.

    This is the primary node type in the academic standards hierarchy. Both
    organizational groupings (e.g., Grade, Subject, Topic) and normative learning
    expectations (e.g., outcomes/competences/objectives) are represented using this
    entity type. Hierarchy is represented via `hasChild` edges.
    """

    academic_subject: str = Field(
        description=(
            "High-level academic subject classification for the item. "
            "In strict exports this should conform to LC enums; "
            "in relaxed exports this may be a free-form subject label."
        ),
    )
    attribution_statement: str = Field(
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
        description=(
            "Stable URI identifier for this standards item. LC KG commonly aligns with "
            "CASE-style URIs. For non-CASE sources, this may be a synthetic deterministic "
            "URI/URN minted by the pipeline (e.g., urn:uuid:<uuid>)."
        ),
    )

    case_identifier_uuid: UUID = Field(
        description=(
            "Stable UUID identifier for this standards item. Used by LC KG exports as a "
            "canonical cross-object key for relationships (hasChild/buildsTowards/relatesTo). "
            "For non-CASE sources, this should be deterministic (UUIDv5 recommended)."
        ),
    )
    date_created: Optional[str] = Field(
        default=None,
        description=(
            "Creation timestamp for this item (ISO-8601 string), if known. Optional."
        ),
    )
    date_modified: Optional[str] = Field(
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
        default=None,
        description=(
            "Stable code/notation for this item from the source framework, if available "
            "(e.g., '2.1.5.1'). This is a key traceability aid and may support progression inference."
        ),
    )
    statement_type: Optional[str] = Field(
        default=None,
        description=(
            "Human-readable source label for the item (e.g., 'Subject', 'Topic', "
            "'Specific competence', 'Expected Standard', 'Indicator'). "
            "This is not the normalized LC type; it preserves source semantics."
        ),
    )
    statement_type: Optional[str] = Field(
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
        """Validate case_identifier_uri looks like a URI/URN (supports http(s), urn,
        etc.).

        Parameters
        ----------
        v
            The case_identifier_uri string to validate.

        Returns
        -------
        str
            The validated case_identifier_uri string.

        Raises
        ------
        ValueError
            If the case_identifier_uri does not include a URI scheme.
        """

        parsed = urlparse(v)

        if not parsed.scheme:
            raise ValueError(
                "case_identifier_uri must include a URI scheme (e.g., urn:, http:, https:)"
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
        """Validate case_identifier_uri looks like a URI/URN (supports http(s), urn,
        etc.).

        Parameters
        ----------
        v
            The case_identifier_uri string to validate.

        Returns
        -------
        str
            The validated case_identifier_uri string.

        Raises
        ------
        ValueError
            If the case_identifier_uri does not include a URI scheme.
        """

        parsed = urlparse(v)

        if not parsed.scheme:
            raise ValueError(
                "case_identifier_uri must include a URI scheme (e.g., urn:, http:, https:)"
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
        """Validate that case_identifier_uri includes case_identifier_uuid (deterministic
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
            raise ValueError("case_identifier_uri must include case_identifier_uuid")

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


class LearningComponent(BaseSchema):
    """Granular skill/concept aligned to one or more standards items via `supports`.

    LearningComponents represent skill/concept units that can be aligned to
    StandardsFrameworkItems using `supports` relationships:

      (:LearningComponent)-[:supports]->(:StandardsFrameworkItem)
    """

    academic_subject: str = Field(
        description=(
            "High-level academic subject classification for the component "
            "(e.g., Mathematics, English Language Arts). In strict exports this should "
            "conform to LC enum values; in relaxed exports free-form values are allowed."
        ),
    )
    attribution_statement: str = Field(
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
        default=None,
        description=(
            "Creation timestamp for the component (ISO-8601 string), if known. Optional."
        ),
    )
    date_modified: Optional[str] = Field(
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
class Relationship(BaseSchema):
    """LC KG relationship record (shared schema across relationship types).

    Relationships connect two entities in the LC KG export. The meaning of the edge is
    defined by `relationshipType` (e.g., hasChild, supports, buildsTowards, relatesTo).
    """

    attribution_statement: str = Field(
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
        default=None,
        description="Creation timestamp for this relationship (ISO-8601 string), if known. Optional.",
    )
    date_modified: Optional[str] = Field(
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
        description=(
            "Normalized relationship label defining the semantic meaning of the connection "
            "(e.g., hasChild, supports, buildsTowards, relatesTo)."
        ),
    )
    source_entity: str = Field(
        description=(
            "Entity type where the relationship originates (e.g., StandardsFramework, "
            "StandardsFrameworkItem, LearningComponent)."
        ),
    )
    source_entity_key: str = Field(
        description=(
            "The identifier property name on the source entity used by this relationship "
            "(e.g., identifier, case_identifier_uuid)."
        ),
    )
    source_entity_value: str = Field(
        description="The identifier value of the source entity (string UUID)."
    )
    target_entity: str = Field(
        description="Entity type where the relationship points (destination node type).",
    )
    target_entity_key: str = Field(
        description=(
            "The identifier property name on the target entity used by this relationship "
            "(e.g., identifier, case_identifier_uuid)."
        ),
    )
    target_entity_value: str = Field(
        description="The identifier value of the target entity (string UUID)."
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
            self.source_entity_key != "case_identifier_uuid"
            or self.target_entity_key != "case_identifier_uuid"
        ):
            raise ValueError("hasChild must use case_identifier_uuid endpoints")

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
            and self.target_entity_key == "case_identifier_uuid"
        ):
            raise ValueError(
                "supports must use source identifier + target case_identifier_uuid"
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
            self.source_entity_key != "case_identifier_uuid"
            or self.target_entity_key != "case_identifier_uuid"
        ):
            raise ValueError(
                f"{self.relationship_type} must use case_identifier_uuid endpoints"
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
class BBox(BaseSchema):
    """Bounding box in pixel coordinates."""

    coord_space: Literal["px"] = "px"
    x0: float = Field(..., description="Left coordinate in pixels.", ge=0.0)
    x1: float = Field(..., description="Right coordinate in pixels.", ge=0.0)
    y0: float = Field(..., description="Top coordinate in pixels.", ge=0.0)
    y1: float = Field(..., description="Bottom coordinate in pixels.", ge=0.0)


class EntityProvenance(BaseSchema):
    """Provenance information for a node."""

    bbox: Optional[BBox] = None
    canonical_node_id: str
    dialect_fallbacks: dict[str, str] = Field(default_factory=dict)
    entity_identifier: UUID
    local_code: Optional[str] = Field(default=None)
    page_indices: list[int] = Field(default_factory=list)
    role: NodeRole | StatementRole
    section_path_text: list[str] = Field(default_factory=list)
    source_decision_ids: list[str] = Field(default_factory=list)
    source_segment_ids: list[str] = Field(default_factory=list)
    text: Optional[TextUnit] = None


class LearningProgressionProvenance(BaseSchema):
    """Provenance information for a learning progression relationship."""

    confidence: float = Field(ge=0.0, le=1.0)
    evidence_node_ids: list[str] = Field(default_factory=list)
    explanation: Optional[str] = None
    inference_source: Literal["inferred", "llm"]
    granularity: Literal["coarse", "fine"] = "coarse"
    llm_model: Optional[str] = Field(default=None)
    relationship_identifier: UUID


class RelationshipProvenance(BaseSchema):
    """Provenance information for a relationship."""

    evidence_node_ids: list[str] = Field(default_factory=list)
    evidence_page_indices: list[int] = Field(default_factory=list)
    relationship_identifier: UUID
    relationship_type: str
    source_uuid: UUID
    target_uuid: UUID


# Schemas for export configurations.
class EntityProvenanceExport(BaseSchema):
    """Schema for entity provenance export."""

    entities: list[EntityProvenance] = Field(
        default_factory=list, description="List of entities."
    )


class HierarchyOrderExport(BaseSchema):
    """Schema for exporting explicit ordering of child SFIs under parent SFIs."""

    order: dict[str, list[str]] = Field(
        default_factory=dict, description="Order of child SFIs."
    )


class KnowledgeGraphExport(BaseSchema):
    """Schema for Knowledge Graph export."""

    export_dialect: ExportDialect = Field(
        default="global_relaxed",
        description="Export validation mode: strict LC enums vs relaxed global strings.",
    )
    frameworks: list[StandardsFramework] = Field(default_factory=list)
    learning_components: list[LearningComponent] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    standards_framework_items: list[StandardsFrameworkItem] = Field(
        default_factory=list
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
                expected_key="case_identifier_uuid",
                id_desc="StandardsFramework.case_identifier_uuid",
                rel=rel,
                side="source",
                valid_ids=id_maps["frameworks"],
            )
        elif rel.source_entity == "StandardsFrameworkItem":
            self._enforce_reference(
                expected_entity="StandardsFrameworkItem",
                expected_key="case_identifier_uuid",
                id_desc="StandardsFrameworkItem.case_identifier_uuid",
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
            expected_key="case_identifier_uuid",
            id_desc="StandardsFrameworkItem.case_identifier_uuid",
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
            expected_key="case_identifier_uuid",
            id_desc="StandardsFrameworkItem.case_identifier_uuid",
            rel=rel,
            side="source",
            valid_ids=id_maps["items"],
        )

        # Target.
        self._enforce_reference(
            expected_entity="StandardsFrameworkItem",
            expected_key="case_identifier_uuid",
            id_desc="StandardsFrameworkItem.case_identifier_uuid",
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

        # Target: StandardsFrameworkItem (case_identifier_uuid).
        self._enforce_reference(
            expected_entity="StandardsFrameworkItem",
            expected_key="case_identifier_uuid",
            id_desc="StandardsFrameworkItem.case_identifier_uuid",
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


class LearningProgressionProvenanceExport(BaseSchema):
    """Schema for progression provenance export."""

    learning_progressions: list[LearningProgressionProvenance] = Field(
        default_factory=list, description="List of learning progressions."
    )


class RelationshipProvenanceExport(BaseSchema):
    """Schema for relationship provenance export."""

    relationships: list[RelationshipProvenance] = Field(
        default_factory=list, description="List of relationships."
    )


# Schemas for graph validation reporting.
class GraphValidationIssue(BaseSchema):
    """A single validation finding."""

    code: str
    context: dict[str, Any] = Field(default_factory=dict)
    level: ValidationLevel
    message: str


class GraphValidationReport(BaseSchema):
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
        self, *, code: str, context: Optional[dict[str, Any]] = None, message: str
    ) -> None:
        """Add an error-level issue.

        Parameters
        ----------
        code
            Short machine-readable code for the issue.
        context
            Optional additional context for debugging.
        message
            Human-readable description of the issue.
        """

        self.add(code=code, context=context, level="error", message=message)

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
        self, *, code: str, context: Optional[dict[str, Any]] = None, message: str
    ) -> None:
        """Add an info-level issue.

        Parameters
        ----------
        code
            Short machine-readable code for the issue.
        context
            Optional additional context for debugging.
        message
            Human-readable description of the issue.
        """

        self.add(code=code, context=context, level="info", message=message)

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
        self, *, code: str, context: Optional[dict[str, Any]] = None, message: str
    ) -> None:
        """Add a warning issue.

        Parameters
        ----------
        code
            Short machine-readable code for the issue.
        context
            Optional additional context for debugging.
        message
            Human-readable description of the issue.
        """

        self.add(code=code, context=context, level="warning", message=message)

    def warnings(self) -> list[GraphValidationIssue]:
        """Get all warning-level issues.

        Returns
        -------
        list[GraphValidationIssue]
            List of warning-level issues.
        """

        return [i for i in self.issues if i.level == "warning"]
