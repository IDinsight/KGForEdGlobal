"""This module contains top-level Pydantic models."""

# Standard Library
import re

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Self

# Third Party Library
import langcodes

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    FilePath,
    field_validator,
    model_validator,
)

# Package Library
from skg.utils.general import make_dir


def _strip_and_require_non_empty_str(v: str) -> str:
    """Strip whitespace and require a non-empty string.

    Parameters
    ----------
    v
        The input string value to validate.

    Returns
    -------
    str
        The stripped non-empty string.

    Raises
    ------
    TypeError
        If the input is not a string.
    ValueError
        If the input value is None or empty after stripping.
    """

    if v is None:
        raise ValueError("Required field cannot be None")

    if not isinstance(v, str):
        raise TypeError("Expected a string")

    v_clean = v.strip()

    if not v_clean:
        raise ValueError("Required string field cannot be empty")

    return v_clean


def _validate_bcp47(code: str) -> str:
    """Validates that a string is a valid BCP-47 language tag.

    Parameters
    ----------
    code
        The language tag to validate.

    Returns
    -------
    str
        The standardized version (e.g., 'en_us' -> 'en-US').

    Raises
    ------
    ValueError
        If the language tag is invalid or unparseable.
    """

    code = (code or "und").strip().replace("_", "-")

    if code in {"und", "mul"}:
        return code

    try:
        lang = langcodes.Language.get(code)

        if not lang.is_valid():
            raise ValueError(f"Invalid BCP-47 language tag: '{code}'")

        return lang.to_tag()
    except langcodes.LanguageTagError as exc:
        raise ValueError(f"Unparseable language tag: '{code}'") from exc


def validate_bbox_order(bbox: list[float]) -> list[float]:
    """Ensure bbox is well-ordered: [x0, y0, x1, y1] with x0 < x1 and y0 < y1.

    Parameters
    ----------
    bbox
        The bounding box to validate.

    Returns
    -------
    list[float]
        The validated bounding box.

    Raises
    ------
    ValueError
        If the bounding box does not have exactly 4 numbers.
    """

    if len(bbox) != 4:
        raise ValueError(
            f"Bounding box must have exactly 4 numbers: [x0, y0, x1, y1]. Got: {bbox}"
        )

    x0, y0, x1, y1 = bbox

    # Auto-correct inverted or zero-dimension axes. For equal dimensions, add 1 pixel.
    if x0 >= x1:
        if x0 > x1:
            x0, x1 = x1, x0
        else:
            x1 = x0 + 1.0
    if y0 >= y1:
        if y0 > y1:
            y0, y1 = y1, y0
        else:
            y1 = y0 + 1.0

    return [x0, y0, x1, y1]


# Common fields with descriptions.
_BCP47Str = Annotated[str, AfterValidator(_validate_bcp47)]
_ControlledStatementValueDedupScope = Literal[
    "document", "nearest_parent_values", "source_context"
]
BBox = Annotated[
    list[float],
    AfterValidator(validate_bbox_order),
    Field(
        description="Bounding box [x0, y0, x1, y1] in absolute pixels (px) relative to the image dimensions.",
        max_length=4,
        min_length=4,
    ),
]
LanguageField = Annotated[
    _BCP47Str,
    Field(
        description="Strict BCP-47 language code (e.g., 'en', 'sw'). Use 'und' if unknown; use 'mul' if mixed languages.",
    ),
]
NormalizedStatementType = Literal["Standard", "Standard Grouping", "Other"]


class BaseSchema(BaseModel):
    """Base model for all schemas."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)


# Schemas for KG configuration fields.
class _AcademicStandardControlledValueItem(BaseSchema):
    """Canonical source-facing organizer value for a statement type.

    Controlled values let a curriculum preserve visible source strings while using a
    stable value for deduplication and final identity. For example, source-visible
    variants such as `PRIMARY THREE` and `PRIMARY: THREE` can both map to the canonical
    grade value `PRIMARY THREE`.
    """

    aliases: list[str] = Field(
        default_factory=list,
        description=(
            "Alternative source-visible spellings, punctuation variants, or OCR "
            "variants that should map to canonical_value."
        ),
    )
    canonical_value: str = Field(
        description="Canonical source-facing value used for controlled deduplication.",
        min_length=1,
    )

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, v: list[str]) -> list[str]:
        """Clean and de-duplicate controlled-value aliases.

        Parameters
        ----------
        v
            Raw alias strings.

        Returns
        -------
        list[str]
            Cleaned aliases in stable order.

        Raises
        ------
        TypeError
            If any alias is not a string.
        """

        cleaned: list[str] = []
        seen: set[str] = set()

        for alias in v or []:
            if not isinstance(alias, str):
                raise TypeError(
                    "_AcademicStandardControlledValueItem.aliases must contain strings."
                )

            alias_clean = alias.strip()

            if not alias_clean or alias_clean in seen:
                continue

            cleaned.append(alias_clean)
            seen.add(alias_clean)

        return cleaned

    @field_validator("canonical_value", mode="before")
    @classmethod
    def validate_canonical_value(cls, v: str) -> str:
        """Clean and require a canonical controlled value.

        Parameters
        ----------
        v
            Raw canonical value.

        Returns
        -------
        str
            Cleaned canonical value.

        Raises
        ------
        ValueError
            If the canonical value is blank.
        """

        value = str(v or "").strip()

        if not value:
            raise ValueError(
                "_AcademicStandardControlledValueItem.canonical_value is required."
            )

        return value


class _AcademicStandardStatementTypePolicyItem(BaseSchema):
    """Canonical statement-type label allowed for SFI extraction.

    The policy is runtime-configured so each curriculum can preserve its own
    source-facing vocabulary while still forcing LLM outputs into a stable set of
    canonical labels.
    """

    aliases: list[str] = Field(
        default_factory=list,
        description=(
            "Alternative source-facing or model-prone spellings that should map to "
            "statement_type. Examples include lowercase, snake_case, or document-local "
            "variants."
        ),
    )
    code_type: Optional[str] = Field(
        default=None,
        description=(
            "Optional KG config code-pattern key that usually supports this statement "
            "type, such as 'indicator' or 'content_standard'."
        ),
    )
    controlled_value_scope: _ControlledStatementValueDedupScope = Field(
        default="source_context",
        description=(
            "Scope used when controlled_values canonicalize organizer text for "
            "deduplication. Use 'document' for document-wide values, "
            "'nearest_parent_values' for values scoped by configured parent "
            "statement types, and 'source_context' for source-local values."
        ),
    )
    controlled_value_scope_parent_statement_types: list[str] = Field(
        default_factory=list,
        description=(
            "Ordered parent statement_type labels used when controlled_value_scope is "
            "'nearest_parent_values'. The resulting scope key is built from these "
            "parent values in configured order."
        ),
    )
    controlled_values: list[_AcademicStandardControlledValueItem] = Field(
        default_factory=list,
        description=(
            "Optional canonical source-facing values and aliases for this statement "
            "type. These values are used for registry bucketing, dedup review set "
            "construction, and final identity; original source-visible text is still "
            "preserved in candidate and source evidence fields."
        ),
    )
    description: str = Field(
        description="Brief curriculum-specific guidance for when to use this statement type."
    )
    normalized_statement_type: NormalizedStatementType = Field(
        description="Global normalized SFI class expected for this statement type."
    )
    statement_type: str = Field(
        description="Canonical source-facing statement_type label the LLM must output."
    )

    @staticmethod
    def _controlled_value_key(value: str) -> str:
        """Build a stable comparison key for controlled values and aliases.

        Parameters
        ----------
        value
            Controlled value or alias.

        Returns
        -------
        str
            Casefolded key with non-alphanumeric runs collapsed to one space.
        """

        return re.sub(r"[^0-9a-z]+", " ", str(value or "").casefold()).strip()

    @field_validator("controlled_value_scope_parent_statement_types")
    @classmethod
    def validate_controlled_value_scope_parent_statement_types(
        cls, v: list[str]
    ) -> list[str]:
        """Clean controlled-value parent scope statement types.

        Parameters
        ----------
        v
            Parent statement_type labels used to build a controlled-value scope key.

        Returns
        -------
        list[str]
            Cleaned parent statement_type labels in stable order.

        Raises
        ------
        TypeError
            If any parent statement_type label is not a string.
        """

        cleaned: list[str] = []
        seen: set[str] = set()

        for statement_type in v or []:
            if not isinstance(statement_type, str):
                raise TypeError(
                    "_AcademicStandardStatementTypePolicyItem."
                    "controlled_value_scope_parent_statement_types must contain "
                    "only strings."
                )

            statement_type_clean = statement_type.strip()

            if not statement_type_clean or statement_type_clean in seen:
                continue

            cleaned.append(statement_type_clean)
            seen.add(statement_type_clean)

        return cleaned

    @field_validator("controlled_values")
    @classmethod
    def validate_controlled_values(
        cls, v: list[_AcademicStandardControlledValueItem]
    ) -> list[_AcademicStandardControlledValueItem]:
        """Validate controlled-value aliases within one statement type.

        Parameters
        ----------
        v
            Configured controlled-value items.

        Returns
        -------
        list[_AcademicStandardControlledValueItem]
            Validated controlled values in configured order.

        Raises
        ------
        ValueError
            If a controlled value or alias maps to more than one canonical value.
        """

        alias_to_canonical: dict[str, str] = {}

        for item in v or []:
            canonical_key = cls._controlled_value_key(item.canonical_value)

            if not canonical_key:
                raise ValueError(
                    "_AcademicStandardStatementTypePolicyItem.controlled_values "
                    "contains a blank canonical value."
                )

            for alias in [item.canonical_value, *item.aliases]:
                alias_key = cls._controlled_value_key(alias)

                if not alias_key:
                    continue

                existing = alias_to_canonical.get(alias_key)

                if existing is not None and existing != item.canonical_value:
                    raise ValueError(
                        f"_AcademicStandardStatementTypePolicyItem.controlled_values "
                        f"alias conflict: {alias!r} maps to both {existing!r} and "
                        f"{item.canonical_value!r}."
                    )

                alias_to_canonical[alias_key] = item.canonical_value

        return v

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, v: list[str]) -> list[str]:
        """Clean and de-duplicate statement-type aliases.

        Parameters
        ----------
        v
            Raw alias strings.

        Returns
        -------
        list[str]
            Cleaned aliases in stable order.

        Raises
        ------
        TypeError
            If any alias is not a string.
        """

        cleaned: list[str] = []
        seen: set[str] = set()

        for alias in v or []:
            if not isinstance(alias, str):
                raise TypeError(
                    "_AcademicStandardStatementTypePolicyItem.aliases must contain strings."
                )

            alias_clean = alias.strip()

            if not alias_clean or alias_clean in seen:
                continue

            cleaned.append(alias_clean)
            seen.add(alias_clean)

        return cleaned

    @field_validator("code_type", mode="before")
    @classmethod
    def validate_code_type(cls, v: Optional[str]) -> Optional[str]:
        """Clean an optional code-pattern key.

        Parameters
        ----------
        v
            Raw optional code-pattern key.

        Returns
        -------
        Optional[str]
            Cleaned key, or None when blank.

        Raises
        ------
        TypeError
            If the value is not a string or None.
        """

        if v is None:
            return None

        if not isinstance(v, str):
            raise TypeError(
                "_AcademicStandardStatementTypePolicyItem.code_type must be a string or None."
            )

        v_clean = v.strip()
        return v_clean if v_clean else None

    @field_validator("description", "statement_type", mode="before")
    @classmethod
    def validate_required_strings(cls, v: str) -> str:
        """Strip and require non-empty statement-type policy strings.

        Parameters
        ----------
        v
            Raw string value.

        Returns
        -------
        str
            Cleaned non-empty string.
        """

        return _strip_and_require_non_empty_str(v)

    @model_validator(mode="after")
    def validate_controlled_value_scope_config(self) -> Self:
        """Validate controlled-value scope fields for this statement type.

        Returns
        -------
        Self
            Validated statement-type policy item.

        Raises
        ------
        ValueError
            If nearest_parent_values is selected without configured parent statement
            types, or if parent statement types are configured for another scope.
        """

        if (
            self.controlled_value_scope == "nearest_parent_values"
            and not self.controlled_value_scope_parent_statement_types
        ):
            raise ValueError(
                "controlled_value_scope='nearest_parent_values' requires "
                "controlled_value_scope_parent_statement_types."
            )

        if (
            self.controlled_value_scope != "nearest_parent_values"
            and self.controlled_value_scope_parent_statement_types
        ):
            raise ValueError(
                "controlled_value_scope_parent_statement_types may only be set when "
                "controlled_value_scope='nearest_parent_values'."
            )

        return self


class _ContextHeadingRule(BaseSchema):
    """Rule for deriving structured window context from heading text.

    The window builder applies these rules to heading-like DocumentIR segments in
    document order. Matches update the active structured context that is attached to
    extraction windows. The rule describes the *grammar* of the curriculum document;
    it does not hardcode page numbers, segment IDs, or row ranges.
    """

    label_template: Optional[str] = Field(
        default=None,
        description=(
            "Optional Python-format template for the display label. Regex capture "
            "groups are available as {1}, {2}, etc. If omitted, use the matched text."
        ),
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    name: str = Field(description="Stable config-local rule name.")
    normalized_statement_type: NormalizedStatementType = Field(
        default="Standard Grouping",
        description="Expected normalized statement type for groupings created from this context.",
    )
    pattern: str = Field(
        description=(
            "Regex pattern applied to heading text. Inline flags such as (?im) are "
            "allowed. For multi-line headings, the window builder may apply the same "
            "rule to each line and/or to the full heading text."
        )
    )
    priority: int = Field(
        default=0,
        description="Higher priority rules win if multiple rules match the same heading fragment.",
    )
    role: str = Field(
        description="Context role emitted by this rule, e.g. grade_level, strand, substrand.",
    )
    statement_type: str = Field(
        description="Source-facing SFI statement type to use if this context becomes a grouping SFI.",
    )

    @field_validator("name", "pattern", "role", "statement_type", mode="before")
    @classmethod
    def _strip_and_require_non_empty(cls, v: str) -> str:
        """Strip whitespace and require non-empty strings for required fields.

        Parameters
        ----------
        v
            The input string value to validate.

        Returns
        -------
        str
            The validated and stripped string value.
        """

        return _strip_and_require_non_empty_str(v)

    @field_validator("label_template", mode="before")
    @classmethod
    def _strip_optional_template(cls, v: Optional[str]) -> Optional[str]:
        """Strip the optional label template, coercing blanks to None.

        Parameters
        ----------
        v
            The label template value to validate, or None.

        Returns
        -------
        Optional[str]
            The stripped template, or None if the input was None or blank.

        Raises
        ------
        TypeError
            If the input is neither a string nor None.
        """

        if v is None:
            return None

        if not isinstance(v, str):
            raise TypeError("label_template must be a string or None")

        v2 = v.strip()
        return v2 if v2 else None

    @field_validator("pattern")
    @classmethod
    def _validate_pattern(cls, v: str) -> str:
        """Validate that the heading pattern is a compilable regular expression.

        Parameters
        ----------
        v
            The regex pattern applied to heading text.

        Returns
        -------
        str
            The validated regex pattern.

        Raises
        ------
        ValueError
            If the pattern is not a valid regular expression.
        """

        try:
            re.compile(v)
        except re.error as exc:
            raise ValueError(
                f"Invalid context heading regex pattern: {v!r}: {exc}"
            ) from exc
        return v


class _ContextResetRule(BaseSchema):
    """Rule for clearing lower-level context when a higher-level role changes."""

    on_role: str = Field(
        description="When this role is updated, clear all roles listed in reset_roles."
    )
    reset_roles: list[str] = Field(default_factory=list)

    @field_validator("on_role", mode="before")
    @classmethod
    def _strip_on_role(cls, v: str) -> str:
        """Strip whitespace and require a non-empty on_role value.

        Parameters
        ----------
        v
            The on_role string value to validate.

        Returns
        -------
        str
            The validated and stripped on_role value.
        """

        return _strip_and_require_non_empty_str(v)

    @field_validator("reset_roles")
    @classmethod
    def _validate_reset_roles(cls, v: list[str]) -> list[str]:
        """Clean and de-duplicate reset_roles, dropping blanks.

        Parameters
        ----------
        v
            The list of role names to clear when on_role updates.

        Returns
        -------
        list[str]
            Cleaned, non-empty role names in stable order with duplicates removed.

        Raises
        ------
        TypeError
            If any element is not a string.
        """

        cleaned: list[str] = []
        seen: set[str] = set()

        for role in v or []:
            if not isinstance(role, str):
                raise TypeError(
                    "ContextResetRule.reset_roles must contain only strings"
                )

            role_clean = role.strip()

            if not role_clean:
                continue

            if role_clean not in seen:
                cleaned.append(role_clean)
                seen.add(role_clean)

        return cleaned


class _ContextSpineConfig(BaseSchema):
    """Configuration for structured extraction window context."""

    description: Optional[str] = None
    heading_rules: list[_ContextHeadingRule] = Field(default_factory=list)
    include_nearby_headings: bool = Field(
        default=True,
        description="Whether windows should also carry raw nearby headings for debug/fallback.",
    )
    max_nearby_headings: int = Field(default=8, ge=0)
    reset_rules: list[_ContextResetRule] = Field(default_factory=list)
    role_order: list[str] = Field(default_factory=list)
    split_multiline_headings: bool = Field(
        default=True,
        description="Apply heading rules to individual lines in multi-line heading blocks.",
    )

    @field_validator("description", mode="before")
    @classmethod
    def _strip_optional_description(cls, v: Optional[str]) -> Optional[str]:
        """Strip the optional description, coercing blanks to None.

        Parameters
        ----------
        v
            The description value to validate, or None.

        Returns
        -------
        Optional[str]
            The stripped description, or None if the input was None or blank.

        Raises
        ------
        TypeError
            If the input is neither a string nor None.
        """

        if v is None:
            return None

        if not isinstance(v, str):
            raise TypeError("description must be a string or None")

        v2 = v.strip()
        return v2 if v2 else None

    @field_validator("role_order")
    @classmethod
    def _validate_role_order(cls, v: list[str]) -> list[str]:
        """Clean and de-duplicate role_order, dropping blanks.

        Parameters
        ----------
        v
            The ordered list of context role names.

        Returns
        -------
        list[str]
            Cleaned, non-empty role names in stable order with duplicates removed.

        Raises
        ------
        TypeError
            If any element is not a string.
        """

        cleaned: list[str] = []
        seen: set[str] = set()

        for role in v or []:
            if not isinstance(role, str):
                raise TypeError(
                    "ContextSpineConfig.role_order must contain only strings"
                )

            role_clean = role.strip()

            if not role_clean:
                continue

            if role_clean not in seen:
                cleaned.append(role_clean)
                seen.add(role_clean)

        return cleaned

    @model_validator(mode="after")
    def _validate_context_references(self) -> Self:
        """Validate that reset rules reference known context roles.

        Returns
        -------
        Self
            The validated context spine configuration.

        Raises
        ------
        ValueError
            If a reset rule's on_role or any of its reset_roles are not produced by the
            configured heading_rules or role_order.
        """

        known_roles = {rule.role for rule in self.heading_rules} | set(self.role_order)

        for reset_rule in self.reset_rules:
            if reset_rule.on_role not in known_roles:
                raise ValueError(
                    f"Context reset rule references unknown on_role: {reset_rule.on_role!r}"
                )

            unknown_reset_roles = sorted(set(reset_rule.reset_roles) - known_roles)

            if unknown_reset_roles:
                raise ValueError(
                    f"Context reset rule for {reset_rule.on_role!r} references unknown reset_roles: "
                    f"{unknown_reset_roles}"
                )

        return self


class _CreateKGAcademicStandardsConfig(BaseSchema):
    """Academic Standards extraction configuration for KG creation."""

    bilingual_pair_policy: str | None = None
    code_parent_rules: list[dict[str, str]] = Field(default_factory=list)
    code_patterns: dict[str, str] = Field(default_factory=dict)
    excluded_table_columns_signatures: list[str] = Field(
        default_factory=list,
        description=(
            "Column signatures that should never be sent through the table SFI "
            "extraction path, even if another table rule would otherwise include them."
        ),
    )
    excluded_table_section_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Regex patterns over bounded nearby heading/section text that exclude "
            "tables from SFI extraction, even if their column signature is otherwise "
            "eligible."
        ),
    )
    included_table_columns_signatures: list[str] = Field(
        default_factory=list,
        description=(
            "Column signatures that identify table segments eligible for SFI extraction."
        ),
    )
    included_table_section_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Regex patterns over bounded nearby heading/section text that can include "
            "tables when column signatures alone are not sufficient."
        ),
    )
    max_dedup_review_set_candidates: Optional[int] = Field(
        default=None,
        description=(
            "Maximum number of SFI registry candidates to include in one dedup "
            "LLM review set. Set to null to use the full length of each connected "
            "candidate set."
        ),
        ge=2,
    )
    max_has_child_parent_candidates: int = Field(
        default=24,
        description=(
            "Maximum number of parent candidates to include in one hasChild "
            "parent-selection set, including the StandardsFramework root fallback."
        ),
        ge=2,
    )
    max_has_child_section_path_labels: int = Field(
        default=12,
        description=(
            "Maximum number of recent section-path labels to expose and use as "
            "hasChild section-path evidence after reversing DocumentIR section paths "
            "so nearest/current labels come first."
        ),
        ge=1,
    )
    max_rows_per_table_window: Optional[int] = Field(
        default=20,
        description=(
            "Maximum number of table body rows per extraction window. Set to null "
            "to emit one whole-table window per selected table."
        ),
    )
    row_overlap: int = Field(default=1, ge=0)
    sfi_deduplication_instructions: str
    sfi_extraction_instructions: str
    sfi_has_child_instructions: str
    sfi_has_child_parent_statement_types: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Optional direct hasChild parent policy keyed by child statement_type. "
            "Each value is the list of allowed direct parent statement_type labels. "
            "An empty list means the child statement_type is allowed to attach "
            "directly to the StandardsFramework root. When omitted, the ordered "
            "sfi_has_child_statement_type_hierarchy is used to derive one direct "
            "parent type per child type."
        ),
    )
    sfi_has_child_statement_type_hierarchy: list[str] = Field(
        default_factory=list,
        description=(
            "Optional ordered statement_type hierarchy, broadest parent to narrowest "
            "child, used to derive direct hasChild parent candidates when "
            "sfi_has_child_parent_statement_types is not provided. If omitted, "
            "statement_type_policy order is used."
        ),
    )
    statement_type_policy: list[_AcademicStandardStatementTypePolicyItem] = Field(
        description=(
            "Canonical curriculum-specific statement_type labels allowed in "
            "SFIExtractionResult.sfi_candidates. The LLM must output only these "
            "labels; aliases are used only for prompt guidance and validation errors."
        ),
        min_length=1,
    )
    synthetic_merge_key_fields: list[str] = Field(
        default_factory=lambda: [
            "country",
            "subject",
            "grade_level",
            "normalized_statement_type",
            "statement_type",
            "hierarchy_context",
            "normalized_text",
        ]
    )

    @staticmethod
    def _clean_has_child_parent_values(
        parent_statement_types: list[str] | None,
    ) -> list[str]:
        """Clean and de-duplicate one child type's allowed parent labels.

        Parameters
        ----------
        parent_statement_types
            Raw list of allowed direct parent statement_type labels for a single child
            statement_type. `None` is treated as an empty list.

        Returns
        -------
        list[str]
            Stripped, non-blank parent labels de-duplicated in input order.

        Raises
        ------
        TypeError
            If the value is not a list or any parent label is not a string.
        """

        if parent_statement_types is None:
            parent_statement_types = []

        if isinstance(parent_statement_types, (str, bytes)) or not isinstance(
            parent_statement_types, list
        ):
            raise TypeError(
                "CreateKGConfig.as.sfi_has_child_parent_statement_types values "
                "must be lists of parent statement_type strings."
            )

        parent_values: list[str] = []
        seen_parent_values: set[str] = set()

        for parent_statement_type in parent_statement_types:
            if not isinstance(parent_statement_type, str):
                raise TypeError(
                    "CreateKGConfig.as.sfi_has_child_parent_statement_types "
                    "parent labels must be strings."
                )

            parent_statement_type_clean = parent_statement_type.strip()

            if (
                not parent_statement_type_clean
                or parent_statement_type_clean in seen_parent_values
            ):
                continue

            parent_values.append(parent_statement_type_clean)
            seen_parent_values.add(parent_statement_type_clean)

        return parent_values

    @field_validator("sfi_has_child_parent_statement_types", mode="before")
    @classmethod
    def validate_sfi_has_child_parent_statement_types(
        cls, v: dict[str, list[str]] | None
    ) -> dict[str, list[str]]:
        """Clean direct hasChild parent-type policy entries.

        Parameters
        ----------
        v
            Mapping from child statement_type to allowed direct parent
            statement_type labels. Empty parent lists identify root-level child types.

        Returns
        -------
        dict[str, list[str]]
            Cleaned mapping with blank keys/values removed and parent lists
            de-duplicated in input order.

        Raises
        ------
        TypeError
            If the mapping, child keys, or parent values have invalid types.
        """

        if v is None:
            return {}

        if not isinstance(v, dict):
            raise TypeError(
                "CreateKGConfig.as.sfi_has_child_parent_statement_types must be "
                "a mapping from child statement_type to a list of parent "
                "statement_type labels."
            )

        cleaned: dict[str, list[str]] = {}

        for child_statement_type, parent_statement_types in v.items():
            if not isinstance(child_statement_type, str):
                raise TypeError(
                    "CreateKGConfig.as.sfi_has_child_parent_statement_types keys "
                    "must be strings."
                )

            child_statement_type_clean = child_statement_type.strip()

            if not child_statement_type_clean:
                continue

            cleaned[child_statement_type_clean] = cls._clean_has_child_parent_values(
                parent_statement_types
            )

        return cleaned

    @field_validator("sfi_has_child_statement_type_hierarchy")
    @classmethod
    def validate_sfi_has_child_statement_type_hierarchy(cls, v: list[str]) -> list[str]:
        """Clean and de-duplicate the optional hasChild statement-type hierarchy.

        Parameters
        ----------
        v
            Configured statement_type hierarchy labels from broadest parent to
            narrowest child.

        Returns
        -------
        list[str]
            Cleaned hierarchy labels in stable order. Empty means use
            statement_type_policy order.

        Raises
        ------
        TypeError
            If any hierarchy item is not a string.
        """

        cleaned: list[str] = []
        seen: set[str] = set()

        for statement_type in v or []:
            if not isinstance(statement_type, str):
                raise TypeError(
                    "CreateKGConfig.as.sfi_has_child_statement_type_hierarchy "
                    "must contain only strings."
                )

            statement_type_clean = statement_type.strip()

            if not statement_type_clean or statement_type_clean in seen:
                continue

            cleaned.append(statement_type_clean)
            seen.add(statement_type_clean)

        return cleaned

    @field_validator(
        "sfi_deduplication_instructions",
        "sfi_extraction_instructions",
        "sfi_has_child_instructions",
        mode="before",
    )
    @classmethod
    def _strip_and_require_non_empty(cls, v: str) -> str:
        """Strip whitespace and require non-empty strings for required fields.

        Parameters
        ----------
        v
            The input string value to validate.

        Returns
        -------
        str
            The validated and stripped string value.
        """

        return _strip_and_require_non_empty_str(v)

    @field_validator("bilingual_pair_policy", mode="before")
    @classmethod
    def _strip_optional_strings(cls, v: str | None) -> str | None:
        """Strip optional string fields and normalize blank strings to None.

        Parameters
        ----------
        v
            The input optional string value.

        Returns
        -------
        str | None
            The stripped string, or None for blank/None values.

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

    @staticmethod
    def _clean_selection_pattern_list(
        *, field_name: str, values: list[str]
    ) -> list[str]:
        """Clean, de-duplicate, and compile-check selection regex patterns.

        Parameters
        ----------
        field_name
            Human-readable field name used in error messages.
        values
            Configured regex pattern strings.

        Returns
        -------
        list[str]
            Cleaned and de-duplicated regex patterns in stable order.

        Raises
        ------
        TypeError
            If any pattern is not a string.
        ValueError
            If any non-empty pattern does not compile.
        """

        cleaned: list[str] = []
        seen: set[str] = set()

        for pattern in values or []:
            if not isinstance(pattern, str):
                raise TypeError(
                    f"CreateKGConfig.as.{field_name} must contain only strings."
                )

            pattern_clean = pattern.strip()

            if not pattern_clean:
                continue

            try:
                re.compile(pattern_clean)
            except re.error as exc:
                raise ValueError(
                    f"Invalid regex in CreateKGConfig.as.{field_name}: "
                    f"{pattern_clean!r}: {exc}"
                ) from exc

            if pattern_clean not in seen:
                cleaned.append(pattern_clean)
                seen.add(pattern_clean)

        return cleaned

    @staticmethod
    def _clean_selection_string_list(
        *, field_name: str, values: list[str]
    ) -> list[str]:
        """Clean and de-duplicate KG config selection string lists.

        Parameters
        ----------
        field_name
            Human-readable field name used in error messages.
        values
            Configured string values.

        Returns
        -------
        list[str]
            Cleaned and de-duplicated strings in stable order.

        Raises
        ------
        TypeError
            If any value is not a string.
        """

        cleaned: list[str] = []
        seen: set[str] = set()

        for value in values or []:
            if not isinstance(value, str):
                raise TypeError(
                    f"CreateKGConfig.as.{field_name} must contain only strings."
                )

            value_clean = value.strip()

            if not value_clean:
                continue

            if value_clean not in seen:
                cleaned.append(value_clean)
                seen.add(value_clean)

        return cleaned

    @field_validator(
        "excluded_table_columns_signatures", "included_table_columns_signatures"
    )
    @classmethod
    def validate_selection_string_lists(cls, v: list[str]) -> list[str]:
        """Validate non-regex selection string lists.

        Parameters
        ----------
        v
            Configured selection strings.

        Returns
        -------
        list[str]
            Cleaned and de-duplicated strings in stable order.
        """

        return cls._clean_selection_string_list(
            field_name="selection string list", values=v
        )

    @field_validator(
        "excluded_table_section_patterns", "included_table_section_patterns"
    )
    @classmethod
    def validate_selection_pattern_lists(cls, v: list[str]) -> list[str]:
        """Validate regex-based selection pattern lists.

        Parameters
        ----------
        v
            Configured regex patterns.

        Returns
        -------
        list[str]
            Cleaned and de-duplicated regex patterns in stable order.
        """

        return cls._clean_selection_pattern_list(
            field_name="selection pattern list", values=v
        )

    @field_validator("code_patterns")
    @classmethod
    def validate_code_patterns(cls, v: dict[str, str]) -> dict[str, str]:
        """Validate that all configured code patterns compile as regular expressions.

        Parameters
        ----------
        v
            Mapping of code pattern name to regular expression string.

        Returns
        -------
        dict[str, str]
            The original pattern mapping.

        Raises
        ------
        TypeError
            If a code pattern value is not a string.
        ValueError
            If a code pattern is empty or does not compile.
        """

        for name, pattern in v.items():
            if not isinstance(pattern, str):
                raise TypeError(
                    f"as.code_patterns[{name!r}] must be a string. "
                    f"Got {type(pattern).__name__}."
                )

            if not pattern.strip():
                raise ValueError(f"as.code_patterns[{name!r}] must be non-empty.")

            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(
                    f"Invalid regex for as.code_patterns[{name!r}]: {exc}"
                ) from exc

        return v

    @field_validator("synthetic_merge_key_fields")
    @classmethod
    def validate_synthetic_merge_key_fields(cls, v: list[str]) -> list[str]:
        """Validate synthetic merge key fields are non-empty strings.

        Parameters
        ----------
        v
            Configured synthetic merge key field names.

        Returns
        -------
        list[str]
            Cleaned and de-duplicated field names in stable order.
        """

        cleaned: list[str] = []
        seen: set[str] = set()

        for field_name in v or []:
            if not isinstance(field_name, str):
                raise TypeError(
                    "CreateKGConfig.as.synthetic_merge_key_fields must contain only strings."
                )

            field_name_clean = field_name.strip()

            if not field_name_clean:
                continue

            if field_name_clean not in seen:
                cleaned.append(field_name_clean)
                seen.add(field_name_clean)

        if not cleaned:
            raise ValueError(
                "CreateKGConfig.as.synthetic_merge_key_fields must contain at least one value."
            )

        return cleaned

    @staticmethod
    def _statement_type_policy_key(value: str) -> str:
        """Build a stable comparison key for statement-type labels and aliases.

        Parameters
        ----------
        value
            Statement-type label or alias.

        Returns
        -------
        str
            Casefolded key with non-alphanumeric runs collapsed to one space.
        """

        return re.sub(r"[^0-9a-z]+", " ", str(value or "").casefold()).strip()

    @field_validator("statement_type_policy")
    @classmethod
    def validate_statement_type_policy(
        cls, v: list[_AcademicStandardStatementTypePolicyItem]
    ) -> list[_AcademicStandardStatementTypePolicyItem]:
        """Validate canonical statement-type policy labels and aliases.

        Parameters
        ----------
        v
            Configured statement-type policy items.

        Returns
        -------
        list[_AcademicStandardStatementTypePolicyItem]
            Validated policy items in configured order.

        Raises
        ------
        ValueError
            If canonical labels or aliases conflict.
        """

        if not v:
            raise ValueError("CreateKGConfig.as.statement_type_policy is required.")

        alias_to_statement_type: dict[str, str] = {}
        canonical_keys: set[str] = set()

        for item in v:
            canonical_key = cls._statement_type_policy_key(item.statement_type)

            if canonical_key in canonical_keys:
                raise ValueError(
                    "CreateKGConfig.as.statement_type_policy contains duplicate "
                    f"statement_type labels after normalization: {item.statement_type!r}"
                )

            canonical_keys.add(canonical_key)

            for alias in [item.statement_type, *item.aliases]:
                alias_key = cls._statement_type_policy_key(alias)

                if not alias_key:
                    continue

                existing = alias_to_statement_type.get(alias_key)

                if existing is not None and existing != item.statement_type:
                    raise ValueError(
                        "CreateKGConfig.as.statement_type_policy alias conflict: "
                        f"{alias!r} maps to both {existing!r} and "
                        f"{item.statement_type!r}."
                    )

                alias_to_statement_type[alias_key] = item.statement_type

        return v

    def _validate_windowing(self) -> None:
        """Ensure table row windowing configuration is internally consistent.

        Raises
        ------
        ValueError
            If max_rows_per_table_window is non-positive, or if row_overlap is not
            smaller than max_rows_per_table_window when chunking is enabled.
        """

        if self.max_rows_per_table_window is None:
            return

        if self.max_rows_per_table_window <= 0:
            raise ValueError(
                "CreateKGConfig.as.max_rows_per_table_window must be positive or null."
            )

        if self.row_overlap >= self.max_rows_per_table_window:
            raise ValueError(
                "CreateKGConfig.as.row_overlap must be smaller than "
                "as.max_rows_per_table_window."
            )

    @staticmethod
    def _validate_code_parent_rule(
        *, idx: int, known: set[str], rule: dict[str, Any]
    ) -> None:
        """Validate a single code parent rule.

        Parameters
        ----------
        idx
            Index of the rule, used in error messages.
        known
            Set of known code pattern names.
        rule
            The code parent rule mapping to validate.

        Raises
        ------
        ValueError
            If the rule references unknown patterns, uses a method other than
            `regex_substitution`, is missing required substitution fields, or has an
            invalid regex.
        """

        child = rule.get("child")
        parent = rule.get("parent")
        method = rule.get("method")

        if child not in known:
            raise ValueError(
                f"as.code_parent_rules[{idx}] unknown child pattern: {child!r}"
            )

        if parent not in known:
            raise ValueError(
                f"as.code_parent_rules[{idx}] unknown parent pattern: {parent!r}"
            )

        if method != "regex_substitution":
            raise ValueError(
                f"as.code_parent_rules[{idx}] invalid method: {method!r}. "
                "Only 'regex_substitution' is supported."
            )

        if "regex" not in rule or "replacement" not in rule:
            raise ValueError(
                f"as.code_parent_rules[{idx}] regex_substitution requires regex and replacement"
            )

        re.compile(rule["regex"])

    def _validate_code_parent_rules(self, known: set[str]) -> None:
        """Validate all configured code parent rules.

        Parameters
        ----------
        known
            Set of known code pattern names.
        """

        for idx, rule in enumerate(self.code_parent_rules):
            self._validate_code_parent_rule(idx=idx, known=known, rule=rule)

    def _validate_statement_type_policy_code_types(self, known: set[str]) -> None:
        """Validate statement-type policy code_type references.

        Parameters
        ----------
        known
            Known KG config code-pattern keys.

        Raises
        ------
        ValueError
            If a statement-type policy item references an unknown code_type.
        """

        for item in self.statement_type_policy:
            if item.code_type is not None and item.code_type not in known:
                raise ValueError(
                    "CreateKGConfig.as.statement_type_policy references unknown "
                    f"code_type {item.code_type!r} for statement_type "
                    f"{item.statement_type!r}. Known code types: {sorted(known)}"
                )

    def _validate_has_child_statement_type_policy(self) -> None:
        """Validate hasChild hierarchy and direct-parent labels.

        Raises
        ------
        ValueError
            If the explicit hierarchy or direct-parent policy references a
            statement_type that is not present in statement_type_policy.
        """

        known_statement_types = {
            item.statement_type for item in self.statement_type_policy
        }

        if self.sfi_has_child_statement_type_hierarchy:
            unknown_statement_types = sorted(
                set(self.sfi_has_child_statement_type_hierarchy) - known_statement_types
            )

            if unknown_statement_types:
                raise ValueError(
                    f"CreateKGConfig.as.sfi_has_child_statement_type_hierarchy "
                    f"references unknown statement_type labels: "
                    f"{unknown_statement_types}. Known statement_type labels: "
                    f"{sorted(known_statement_types)}"
                )

        if self.sfi_has_child_parent_statement_types:
            unknown_child_types = sorted(
                set(self.sfi_has_child_parent_statement_types) - known_statement_types
            )
            unknown_parent_types = sorted(
                {
                    parent_type
                    for parent_types in self.sfi_has_child_parent_statement_types.values()
                    for parent_type in parent_types
                }
                - known_statement_types
            )

            if unknown_child_types or unknown_parent_types:
                raise ValueError(
                    f"CreateKGConfig.as.sfi_has_child_parent_statement_types "
                    f"references unknown statement_type labels. "
                    f"Unknown child labels: {unknown_child_types}; "
                    f"unknown parent labels: {unknown_parent_types}; "
                    f"known statement_type labels: {sorted(known_statement_types)}"
                )

    def _validate_selection_overlap_policy(self) -> None:
        """Ensure table-selection policy does not include and exclude the same value.

        Raises
        ------
        ValueError
            If a table columns_signature appears in both included and excluded lists.
        """

        overlapping_table_signatures = sorted(
            set(self.included_table_columns_signatures)
            & set(self.excluded_table_columns_signatures)
        )

        if overlapping_table_signatures:
            raise ValueError(
                f"CreateKGConfig.as table-selection policy cannot include and exclude "
                f"the same columns_signature values: "
                f"{overlapping_table_signatures}"
            )

    @model_validator(mode="after")
    def validate_academic_standards_configuration(self) -> Self:
        """Validate cross-field Academic Standards extraction configuration.

        Returns
        -------
        Self
            The validated Academic Standards configuration.

        Raises
        ------
        ValueError
            If code handling, parent rules, table selection, or windowing is invalid.
        """

        known = set(self.code_patterns.keys())
        self._validate_windowing()
        self._validate_code_parent_rules(known)
        self._validate_has_child_statement_type_policy()
        self._validate_selection_overlap_policy()
        self._validate_statement_type_policy_code_types(known)
        return self


class _CreateKGLearningComponentsConfig(BaseSchema):
    """Learning Components configuration for KG creation."""

    generation_instructions: str

    @field_validator("generation_instructions", mode="before")
    @classmethod
    def _strip_and_require_non_empty(cls, v: str) -> str:
        """Strip whitespace and require non-empty strings for required fields.

        Parameters
        ----------
        v
            The input string value to validate.

        Returns
        -------
        str
            The validated and stripped string value.
        """

        return _strip_and_require_non_empty_str(v)


class _CreateKGMetadata(BaseSchema):
    """Framework-level metadata for a KG creation run."""

    adoption_status: Optional[str] = Field(
        default=None,
        description=(
            "Optional adoption or approval status of the framework (e.g., 'adopted', "
            "'draft', 'under review'). Blank strings are normalized to None."
        ),
    )
    attribution_statement: str = Field(
        description="Required attribution or citation statement to credit the source of the framework."
    )
    author: str = Field(
        description="Author or issuing body responsible for the framework."
    )
    country: str = Field(description="Country the framework applies to.")
    framework_title: str = Field(
        description="Human-readable title of the academic standards framework."
    )
    grades_or_stages: list[str] = Field(
        default_factory=list,
        description="Grades or stages covered by the framework (e.g., ['Grade 1', 'Grade 2']).",
    )
    jurisdiction: str = Field(
        description="Jurisdiction that governs the framework (e.g., a national or regional education authority)."
    )
    languages: list[str] = Field(
        description="Languages present in the framework. Must contain at least one non-empty value."
    )
    license: str = Field(
        description="License under which the framework content is published or used."
    )
    primary_language: str = Field(
        description="Primary language of the framework content."
    )
    provider: str = Field(
        description="Provider or organization supplying the framework data."
    )
    subject: str = Field(
        description="Academic subject the framework covers (e.g., 'Mathematics', 'English')."
    )

    @field_validator(
        "attribution_statement",
        "author",
        "country",
        "framework_title",
        "jurisdiction",
        "license",
        "primary_language",
        "provider",
        "subject",
        mode="before",
    )
    @classmethod
    def _strip_and_require_non_empty(cls, v: str) -> str:
        """Strip whitespace and require non-empty strings for required metadata fields.

        Parameters
        ----------
        v
            Raw value for a required string metadata field.

        Returns
        -------
        str
            The stripped non-empty string.
        """

        return _strip_and_require_non_empty_str(v)

    @field_validator("adoption_status", mode="before")
    @classmethod
    def _strip_optional_strings(cls, v: Optional[str]) -> Optional[str]:
        """Strip optional string fields and normalize blank strings to None.

        Parameters
        ----------
        v
            Raw optional adoption-status value.

        Returns
        -------
        Optional[str]
            The stripped string, or None when the value is None or blank.

        Raises
        ------
        TypeError
            If the value is not a string or None.
        """

        if v is None:
            return None

        if not isinstance(v, str):
            raise TypeError("Expected a string or None")

        v2 = v.strip()
        return v2 if v2 else None

    @field_validator("languages")
    @classmethod
    def validate_languages(cls, v: list[str]) -> list[str]:
        """Validate configured metadata languages are present and de-duplicated.

        Parameters
        ----------
        v
            Raw list of language strings.

        Returns
        -------
        list[str]
            Cleaned, de-duplicated languages in stable order.

        Raises
        ------
        TypeError
            If any language is not a string.
        ValueError
            If the list is empty or contains no non-empty values after stripping.
        """

        if not v:
            raise ValueError(
                "CreateKGMetadata.languages must contain at least one value."
            )

        cleaned: list[str] = []
        seen: set[str] = set()

        for language in v:
            if not isinstance(language, str):
                raise TypeError("CreateKGMetadata.languages must contain only strings.")

            language_clean = language.strip()

            if not language_clean:
                continue

            if language_clean not in seen:
                cleaned.append(language_clean)
                seen.add(language_clean)

        if not cleaned:
            raise ValueError(
                "CreateKGMetadata.languages must contain at least one non-empty value."
            )

        return cleaned


# Config schemas.
class CreateKGConfig(BaseSchema):
    """Configuration for knowledge graph creation from DocumentIR.

    The runtime config uses short namespaces under `kgs`:

    - `as` for Academic Standards extraction settings.
    - `lc` for Learning Components settings.

    Python code accesses those namespaces through the valid attribute names
    `academic_standards` and `learning_components`.
    """

    # GENERAL ATTRIBUTES #
    overwrite: bool = Field(
        False, description="Overwrite existing knowledge graph artifacts."
    )

    # ACADEMIC STANDARDS #
    academic_standards: _CreateKGAcademicStandardsConfig = Field(
        alias="as",
        description="Academic Standards extraction settings from the kgs.as config namespace.",
    )

    # LEARNING COMPONENTS #
    learning_components: _CreateKGLearningComponentsConfig = Field(
        alias="lc",
        description="Learning Components settings from the kgs.lc config namespace.",
    )

    # FRAMEWORK METADATA #
    metadata: _CreateKGMetadata

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        serialize_by_alias=True,
        validate_by_alias=True,
        validate_by_name=False,
    )


class ExtractionConfig(BaseSchema):
    """Configuration for page IR extraction from a PDF document."""

    country: str = Field(
        ..., description="The country associated with the PDF document."
    )
    dpi: int = Field(250, description="Render DPI for page images.")
    end_page: Optional[int] = Field(
        None, description="0-based end page (exclusive). Default None is to end."
    )
    languages: list[LanguageField] = Field(
        ...,
        description="One or more languages associated with the PDF document (e.g. en-US, fr-FR).",
        min_length=1,
    )
    output_dir: Path = Field(..., description="Output directory root.")
    overwrite: bool = Field(False, description="Overwrite existing page IR JSONs.")
    pdf_fp: FilePath = Field(
        ...,
        description="The file path to the PDF document to extract curriculum data from.",
    )
    start_page: Optional[int] = Field(
        None, description="0-based start page (inclusive)."
    )
    use_extracted_hints: bool = Field(
        False,
        description=(
            "Whether or not to extract text layer and table layer hints using PyMuPDF "
            "as additional context for the extraction agent's prompt. This is helpful "
            "for PDF with non-English text and accents."
        ),
    )
    year: Optional[int] = Field(
        None, description="Document year (optional; overrides any inferred year)."
    )

    @model_validator(mode="after")
    def check_page_range(self) -> Self:
        """Ensure that if end_page is provided, it is strictly greater than start_page.

        Returns
        -------
        Self
            The passed in ExtractionConfig.

        Raises
        ------
        ValueError
            If end_page is not greater than start_page.
        """

        if (
            self.end_page is not None
            and self.start_page is not None
            and self.end_page <= self.start_page
        ):
            raise ValueError(
                f"end_page ({self.end_page}) must be greater than start_page ({self.start_page})."
            )

        return self

    @field_validator("output_dir")
    @classmethod
    def ensure_output_dir_exists(cls, v: Path) -> Path:
        """Ensure the output directory exists. If it doesn't, it creates it (including
        parents).

        Parameters
        ----------
        v
            The output directory path.

        Returns
        -------
        Path
            The validated output directory path.
        """

        make_dir(v)

        return v


class StitchingConfig(BaseSchema):
    """Configuration for document IR stitching from verified page IR JSONs.

    NB: `table_filldown_group_cols_max` (fill-down/rowspan reconstruction)

    1. Many curriculum PDFs use **merged cells/rowspans** in the *leftmost grouping
        columns* (e.g., **Topic**, **Sub-topic**, **Strand**, **Theme**). When
        extracted, those merged cells often appear as **blank cells** on subsequent
        rows.
    2. `table_filldown_group_cols_max` controls **how many leading columns** should
        have these visually empty cells **filled down** from the most recent non-empty
        value above. This reconstructs the intended grouping structure without changing
        the underlying table content.
            - Only the **first `table_filldown_group_cols_max` columns** are eligible
                for fill-down.
            - Columns beyond this are treated as **leaf/content columns** (e.g.,
                competences/outcomes, activities, expected standards), where blanks
                typically mean **“no content / not applicable”**, not “repeat previous”.
    3. Why not set it very large (e.g., 10)? Because non-grouping columns often contain
        legitimate blanks (or extraction misses). A large value can silently “invent”
        repeated activities/standards by copying prior rows, corrupting the extracted
        table semantics.
    """

    keep_artifacts: bool = Field(
        False,
        description="Whether to keep artifacts such as page numbers, headers, footers, etc. after stitching.",
    )
    max_section_path_length: int = Field(
        12,
        description="Maximum number of section paths in the stack to maintain. For most PDFs, 12 is a good number that will capture enough breadcrumb context for heading traces.",
    )
    min_link_score: float = Field(
        1.0, description="Minimum link score to consider for stitching.", ge=0
    )
    overwrite: bool = Field(False, description="Overwrite existing document IR JSON.")
    repair_hyphenation: bool = Field(
        True, description="Whether to repair hyphenation for stitched text."
    )
    sort_items_by_bbox: bool = Field(
        False,
        description="Whether to sort items by their bounding box positions before stitching.",
    )
    table_filldown_enabled: bool = Field(
        True, description="Whether to enable table filldown during stitching."
    )
    table_filldown_group_cols_max: int = Field(
        1, description="Maximum number of group columns for table filldown.", ge=0
    )
    verification_auto_stitch_confidence: float = Field(
        0.75,
        description="If a verified link has confidence >= this value, it will be automatically stitched.",
        ge=0,
        le=1,
    )


class VerificationConfig(BaseSchema):
    """Configuration for page IR verification from a PDF document."""

    end_page: Optional[int] = Field(
        None, description="0-based end page (exclusive). Default: to end."
    )
    min_confidence_to_patch: float = Field(
        0.75,
        ge=0.0,
        le=1.0,
        description="Only apply compiled continuity decisions/repeats_header patches when verdict.confidence >= this threshold.",
    )
    min_confidence_to_select_positive: float = Field(
        0.50,
        description="Minimum confidence for a positive continuation verdict to outrank negatives during attempt selection. This does not control patching.",
        ge=0.0,
        le=1.0,
    )
    min_confidence_to_stop_negative_search: float = Field(
        0.95,
        description="Minimum confidence for a same-family primary-primary negative verdict to stop alternate candidate-pair search for a page boundary. This controls verification search budget, not compile-time patching.",
        ge=0.0,
        le=1.0,
    )
    next_page_crop_padding_px: int = Field(
        120,
        description="When cropping the top of page N+1 for verification, include this many extra pixels below the selected next candidate bbox. Crops are pair-specific.",
        ge=0,
    )
    overwrite: bool = Field(
        False,
        description="If True, re-verify all page pairs even if pair reports already exist on disk. If False, reuse existing pair reports (resumed run support).",
    )
    start_page: Optional[int] = Field(
        None, description="0-based start page (inclusive)."
    )

    @model_validator(mode="after")
    def check_page_range(self) -> Self:
        """Ensure that if end_page is provided, it is strictly greater than start_page.

        Returns
        -------
        Self
            The passed in VerificationConfig.

        Raises
        ------
        ValueError
            If end_page is not greater than start_page.
        """

        if (
            self.end_page is not None
            and self.start_page is not None
            and self.end_page <= self.start_page
        ):
            raise ValueError(
                f"end_page ({self.end_page}) must be greater than start_page ({self.start_page})."
            )

        return self

    @model_validator(mode="after")
    def check_confidences(self) -> Self:
        """Ensure confidence thresholds remain logically consistent.

        Returns
        -------
        Self
            The passed in VerificationConfig.

        Raises
        ------
        ValueError
            If min_confidence_to_select_positive is greater than
                min_confidence_to_patch.
            If min_confidence_to_stop_negative_search is lower than
                min_confidence_to_patch.
        """

        if self.min_confidence_to_select_positive > self.min_confidence_to_patch:
            raise ValueError(
                "min_confidence_to_select_positive must be <= min_confidence_to_patch so selection remains at least as permissive as patching."
            )

        if self.min_confidence_to_stop_negative_search < self.min_confidence_to_patch:
            raise ValueError(
                "min_confidence_to_stop_negative_search must be >= min_confidence_to_patch so early negative stopping remains at least as conservative as patching."
            )

        return self


class RunCtx(BaseSchema):
    """Pydantic model for run metadata."""

    completed_at: Optional[datetime] = Field(
        default=None, description="UTC timestamp when the run completed."
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary key-value metadata attached to the run (e.g., status, error details, doc_key).",
    )
    models: dict[str, str] = Field(
        default_factory=dict,
        description="Dictionary mapping model types to their identifiers used during the run.",
    )
    run_id: str = Field(
        description="Unique identifier for this run (typically a UUID or slug)."
    )
    started_at: Optional[datetime] = Field(
        default=None, description="UTC timestamp when the run started."
    )


class RunConfig(BaseSchema):
    """Pydantic model for run configuration."""

    page_ir_extraction: ExtractionConfig = Field(
        description="Configuration for page-level IR extraction from the source PDF."
    )
    page_ir_verification: VerificationConfig = Field(
        description="Configuration for page-boundary verification between adjacent pages."
    )
    document_ir: StitchingConfig = Field(
        description="Configuration for stitching verified page IRs into a single document IR."
    )
    kgs: Optional[CreateKGConfig] = Field(
        default=None,
        description="Configuration for knowledge graph creation. If None, the KG step is skipped.",
    )
