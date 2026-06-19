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
import re

from collections import Counter
from datetime import datetime
from typing import Any, Literal, Optional, Self, Sequence
from urllib.parse import urlparse
from uuid import UUID

# Third Party Library
from pydantic import Field, field_validator, model_validator

# Package Library
from skg.page_ir_extraction.schemas import TextUnit
from skg.schemas import BaseSchema, LanguageField, validate_bbox_order

_AllowedRelationshipTypes = {"hasChild", "supports", "buildsTowards", "relatesTo"}
_AllowedEntityKeys = {"identifier", "case_identifier_uuid"}
_MetadataT = dict[str, Any]
_NormalizedStatementType = Literal["Standard", "Standard Grouping", "Other"]
_ProgressionSubtype = Literal["developmental_prerequisite", "recurring_practice"]
_ValidationLevel = Literal["error", "info"]


def unique_clean_strings(values: Sequence[str]) -> list[str]:
    """Clean and de-duplicate strings while preserving order.

    Parameters
    ----------
    values
        Raw string values.

    Returns
    -------
    list[str]
        Cleaned unique strings.
    """

    cleaned: list[str] = []
    seen: set[str] = set()

    for value in values:
        value_clean = str(value).strip()

        if not value_clean or value_clean in seen:
            continue

        cleaned.append(value_clean)
        seen.add(value_clean)

    return cleaned


# Schemas for document profile.
class ContextHeadingRule(BaseSchema):
    """Profile rule for deriving structured window context from heading text.

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
    name: str = Field(description="Stable profile-local rule name.")
    normalized_statement_type: _NormalizedStatementType = Field(
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


class ContextResetRule(BaseSchema):
    """Profile rule for clearing lower-level context when a higher-level role changes."""

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


class ContextSpineConfig(BaseSchema):
    """Document-profile configuration for structured extraction-window context.

    For Ghana, this should generally recognize grade -> strand -> sub-strand from
    headings. Row-local standards, such as content standards and learning indicators,
    should still be extracted from table cells rather than treated as window-level
    context.
    """

    description: Optional[str] = None
    heading_rules: list[ContextHeadingRule] = Field(default_factory=list)
    include_nearby_headings: bool = Field(
        default=True,
        description="Whether windows should also carry raw nearby headings for debug/fallback.",
    )
    max_nearby_headings: int = Field(default=8, ge=0)
    reset_rules: list[ContextResetRule] = Field(default_factory=list)
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
    def _validate_context_references(self) -> ContextSpineConfig:
        """Validate that reset rules reference known context roles.

        Returns
        -------
        ContextSpineConfig
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


class SFIHierarchyRole(BaseSchema):
    """Profile declaration of a final SFI hierarchy role.

    This separates the window-level context spine from row-local SFI roles. For Ghana,
    grade/strand/sub-strand are context-derived groupings, while content standards and
    learning indicators are row-local standards extracted from table cells/codes.
    """

    normalized_statement_type: _NormalizedStatementType
    parent_role: Optional[str] = None
    role: str
    source: Literal["context", "row", "framework"] = "row"
    statement_type: str

    @field_validator("role", "statement_type", mode="before")
    @classmethod
    def _strip_required_strings(cls, v: str) -> str:
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

    @field_validator("parent_role", mode="before")
    @classmethod
    def _strip_optional_parent_role(cls, v: Optional[str]) -> Optional[str]:
        """Strip the optional parent_role, coercing blanks to None.

        Parameters
        ----------
        v
            The parent_role value to validate, or None.

        Returns
        -------
        Optional[str]
            The stripped parent_role, or None if the input was None or blank.

        Raises
        ------
        TypeError
            If the input is neither a string nor None.
        """

        if v is None:
            return None

        if not isinstance(v, str):
            raise TypeError("parent_role must be a string or None")

        v2 = v.strip()
        return v2 if v2 else None


class DocumentProfile(BaseSchema):
    """Country/document-specific profile for KG extraction."""

    # Framework metadata.
    adoption_status: str | None = None
    attribution_statement: str
    author: str
    context_spine: ContextSpineConfig = Field(
        default_factory=ContextSpineConfig,
        description=(
            "Document profile-driven rules for deriving structured extraction-window context "
            "from headings (for Ghana: grade_level -> strand -> substrand)."
        ),
    )
    country: str
    framework_title: str
    grades_or_stages: list[str] = Field(default_factory=list)
    jurisdiction: str
    languages: list[str]
    license: str
    primary_language: str
    provider: str
    subject: str

    # Block selection policy.
    excluded_block_section_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Regex patterns over bounded nearby heading/section text that exclude block "
            "segments from direct block extraction and table-context block selection."
        ),
    )
    excluded_block_text_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Regex patterns over a block's own source text that exclude the block from "
            "direct block extraction and table-context block selection."
        ),
    )
    excluded_block_types: list[str] = Field(
        default_factory=list,
        description=(
            "DocumentIR block_type values that should never be selected as block windows."
        ),
    )
    target_block_code_match_types: list[str] = Field(
        default_factory=list,
        description=(
            "DocumentProfile.code_patterns keys whose matches can select direct block "
            "windows. Code patterns are otherwise only hints for extraction windows."
        ),
    )
    target_block_context_rule_names: list[str] = Field(
        default_factory=list,
        description=(
            "ContextSpineConfig.heading_rules names whose matches can select direct "
            "block windows. Context rules are otherwise only structured-context hints."
        ),
    )
    target_block_section_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Regex patterns over bounded nearby heading/section text that can select "
            "block segments independently of table selection."
        ),
    )
    target_block_text_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Regex patterns over a block's own source text that can select block "
            "segments independently of table selection."
        ),
    )
    target_block_types: list[str] = Field(
        default_factory=list,
        description="DocumentIR block_type values that should be selected as direct block windows.",
    )

    # Code handling.
    code_parent_rules: list[dict[str, str]] = Field(default_factory=list)
    code_patterns: dict[str, str] = Field(default_factory=dict)
    code_statement_types: dict[str, str] = Field(default_factory=dict)
    has_stable_codes: bool = False
    sfi_hierarchy_roles: list[SFIHierarchyRole] = Field(
        default_factory=list,
        description=(
            "Final KG hierarchy roles expected for this document. This includes both "
            "context-derived grouping roles and row-local standard roles."
        ),
    )

    # Table selection policy.
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
    target_table_columns_signatures: list[str] = Field(
        default_factory=list,
        description=(
            "Column signatures that identify table segments eligible for SFI extraction."
        ),
    )
    target_table_section_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Regex patterns over bounded nearby heading/section text that can include "
            "tables when column signatures alone are not sufficient."
        ),
    )

    # Windowing.
    include_context_blocks_for_selected_tables: bool = Field(
        default=True,
        description=(
            "Whether headings near selected tables should be selected as contextual "
            "block windows. This is independent of direct block selection."
        ),
    )
    max_rows_per_table_window: int = Field(default=20, ge=1)
    row_overlap: int = Field(default=1, ge=0)
    table_window_mode: Literal["whole_table", "row_chunks"] = "row_chunks"

    # Duplication behavior.
    bilingual_pair_policy: str | None = None
    repeated_statement_policy: str = ""
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

    # LLM instructions.
    duplicate_review_instructions: str
    learning_component_instructions: str
    sfi_extraction_instructions: str

    # All other metadata.
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "attribution_statement",
        "author",
        "country",
        "duplicate_review_instructions",
        "framework_title",
        "jurisdiction",
        "learning_component_instructions",
        "license",
        "primary_language",
        "provider",
        "sfi_extraction_instructions",
        "subject",
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

    @field_validator("adoption_status", "bilingual_pair_policy", mode="before")
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
                    f"DocumentProfile.{field_name} must contain only strings."
                )

            pattern_clean = pattern.strip()

            if not pattern_clean:
                continue

            try:
                re.compile(pattern_clean)
            except re.error as exc:
                raise ValueError(
                    f"Invalid regex in DocumentProfile.{field_name}: {pattern_clean!r}: {exc}"
                ) from exc

            if pattern_clean not in seen:
                cleaned.append(pattern_clean)
                seen.add(pattern_clean)

        return cleaned

    @staticmethod
    def _clean_selection_string_list(
        *, field_name: str, values: list[str]
    ) -> list[str]:
        """Clean and de-duplicate profile selection string lists.

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
                    f"DocumentProfile.{field_name} must contain only strings."
                )

            value_clean = value.strip()

            if not value_clean:
                continue

            if value_clean not in seen:
                cleaned.append(value_clean)
                seen.add(value_clean)

        return cleaned

    @field_validator(
        "excluded_block_types",
        "excluded_table_columns_signatures",
        "target_block_code_match_types",
        "target_block_context_rule_names",
        "target_block_types",
        "target_table_columns_signatures",
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
        "excluded_block_section_patterns",
        "excluded_block_text_patterns",
        "excluded_table_section_patterns",
        "target_block_section_patterns",
        "target_block_text_patterns",
        "target_table_section_patterns",
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
            If a code pattern is empty or cannot be compiled.
        """

        for name, pattern in v.items():
            if not isinstance(pattern, str):
                raise TypeError(
                    f"code_patterns[{name!r}] must be a string. "
                    f"Got {type(pattern).__name__}."
                )

            if not pattern.strip():
                raise ValueError(f"code_patterns[{name!r}] must be non-empty.")

            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(
                    f"Invalid regex for code_patterns[{name!r}]: {exc}"
                ) from exc

        return v

    @field_validator("languages")
    @classmethod
    def validate_languages(cls, v: list[str]) -> list[str]:
        """Validate profile languages are present, non-empty, and de-duplicated.

        Parameters
        ----------
        v
            Language tags configured for the profile.

        Returns
        -------
        list[str]
            Cleaned language tags in stable order.
        """

        if not v:
            raise ValueError(
                "DocumentProfile.languages must contain at least one value."
            )

        cleaned: list[str] = []
        seen: set[str] = set()

        for language in v:
            if not isinstance(language, str):
                raise TypeError("DocumentProfile.languages must contain only strings.")

            language_clean = language.strip()

            if not language_clean:
                continue

            if language_clean not in seen:
                cleaned.append(language_clean)
                seen.add(language_clean)

        if not cleaned:
            raise ValueError(
                "DocumentProfile.languages must contain at least one non-empty value."
            )

        return cleaned

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
                    "DocumentProfile.synthetic_merge_key_fields must contain only strings."
                )

            field_name_clean = field_name.strip()

            if not field_name_clean:
                continue

            if field_name_clean not in seen:
                cleaned.append(field_name_clean)
                seen.add(field_name_clean)

        if not cleaned:
            raise ValueError(
                "DocumentProfile.synthetic_merge_key_fields must contain at least one value."
            )

        return cleaned

    def _validate_stable_codes(self) -> None:
        """Ensure stable codes have associated code patterns.

        Raises
        ------
        ValueError
            If has_stable_codes is true but no code_patterns were configured.
        """

        if self.has_stable_codes and not self.code_patterns:
            raise ValueError(
                "DocumentProfile.has_stable_codes is true, but no code_patterns were configured."
            )

    def _validate_windowing(self) -> None:
        """Ensure row windowing configuration is internally consistent.

        Raises
        ------
        ValueError
            If row_overlap is not smaller than max_rows_per_table_window.
        """

        if self.row_overlap >= self.max_rows_per_table_window:
            raise ValueError(
                "DocumentProfile.row_overlap must be smaller than max_rows_per_table_window."
            )

    @staticmethod
    def _validate_code_parent_rule(
        idx: int, rule: dict[str, Any], known: set[str]
    ) -> None:
        """Validate a single code parent rule.

        Parameters
        ----------
        idx
            Index of the rule (for error messages).
        rule
            The code parent rule mapping to validate.
        known
            Set of known code pattern names.

        Raises
        ------
        ValueError
            If the rule references unknown patterns, uses an invalid method, or is
            missing required regex_substitution fields.
        """

        child = rule.get("child")
        parent = rule.get("parent")
        method = rule.get("method")

        if child not in known:
            raise ValueError(
                f"code_parent_rules[{idx}] unknown child pattern: {child!r}"
            )

        if parent not in known:
            raise ValueError(
                f"code_parent_rules[{idx}] unknown parent pattern: {parent!r}"
            )

        if method not in {"drop_last_dot_component", "regex_substitution"}:
            raise ValueError(f"code_parent_rules[{idx}] invalid method: {method!r}")

        if method == "regex_substitution":
            if "regex" not in rule or "replacement" not in rule:
                raise ValueError(
                    f"code_parent_rules[{idx}] regex_substitution requires regex and replacement"
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
            self._validate_code_parent_rule(idx, rule, known)

    def _validate_statement_type_keys(self, known: set[str]) -> None:
        """Ensure code_statement_types keys are a subset of code_patterns.

        Parameters
        ----------
        known
            Set of known code pattern names.

        Raises
        ------
        ValueError
            If code_statement_types contains keys absent from code_patterns.
        """

        unknown_statement_type_keys = sorted(
            set(self.code_statement_types.keys()) - known
        )

        if unknown_statement_type_keys:
            raise ValueError(
                f"DocumentProfile.code_statement_types contains keys not present in "
                f"code_patterns: {unknown_statement_type_keys}"
            )

    def _validate_explicit_block_target_references(self, known: set[str]) -> None:
        """Validate explicit block targets that reference profile rule names.

        Parameters
        ----------
        known
            Set of known code pattern names.

        Raises
        ------
        ValueError
            If a targeted block code type is absent from code_patterns or a targeted
            context rule name is absent from context_spine.heading_rules.
        """

        unknown_code_types = sorted(set(self.target_block_code_match_types) - known)

        if unknown_code_types:
            raise ValueError(
                f"DocumentProfile.target_block_code_match_types contains values not "
                f"present in code_patterns: {unknown_code_types}"
            )

        context_rule_names = {rule.name for rule in self.context_spine.heading_rules}
        unknown_context_rule_names = sorted(
            set(self.target_block_context_rule_names) - context_rule_names
        )

        if unknown_context_rule_names:
            raise ValueError(
                f"DocumentProfile.target_block_context_rule_names contains values not "
                f"present in context_spine.heading_rules: {unknown_context_rule_names}"
            )

    def _validate_selection_overlap_policy(self) -> None:
        """Ensure selection policies do not both target and exclude the same value.

        Raises
        ------
        ValueError
            If a table columns_signature or block_type appears in both its target and
            excluded selection lists.
        """

        overlapping_block_types = sorted(
            set(self.target_block_types) & set(self.excluded_block_types)
        )
        overlapping_table_signatures = sorted(
            set(self.target_table_columns_signatures)
            & set(self.excluded_table_columns_signatures)
        )

        if overlapping_block_types:
            raise ValueError(
                "DocumentProfile block-selection policy cannot target and exclude the "
                f"same block_type values: {overlapping_block_types}"
            )

        if overlapping_table_signatures:
            raise ValueError(
                "DocumentProfile table-selection policy cannot target and exclude the "
                f"same columns_signature values: {overlapping_table_signatures}"
            )

    def _validate_sfi_hierarchy_roles(self) -> None:
        """Validate final SFI hierarchy role declarations.

        Raises
        ------
        ValueError
            If role names are duplicated, parent roles are unknown, or context-sourced
            roles are not produced by the configured context spine.
        """

        roles = [item.role for item in self.sfi_hierarchy_roles]
        duplicate_roles = sorted(
            role for role, count in Counter(roles).items() if count > 1
        )
        if duplicate_roles:
            raise ValueError(
                f"DocumentProfile.sfi_hierarchy_roles contains duplicate roles: {duplicate_roles}"
            )

        known_roles = set(roles)
        for item in self.sfi_hierarchy_roles:
            if item.parent_role and item.parent_role not in known_roles:
                raise ValueError(
                    f"SFI hierarchy role {item.role!r} references unknown parent_role: "
                    f"{item.parent_role!r}"
                )

        context_roles = {rule.role for rule in self.context_spine.heading_rules}
        missing_context_roles = sorted(
            item.role
            for item in self.sfi_hierarchy_roles
            if item.source == "context" and item.role not in context_roles
        )
        if missing_context_roles:
            raise ValueError(
                "Context-sourced SFI hierarchy roles are not produced by "
                f"context_spine.heading_rules: {missing_context_roles}"
            )

    @model_validator(mode="after")
    def validate_profile_configuration(self) -> DocumentProfile:
        """Validate cross-field DocumentProfile configuration.

        Returns
        -------
        DocumentProfile
            The validated profile.

        Raises
        ------
        ValueError
            If code handling, parent rules, or windowing configuration is invalid.
        """

        known = set(self.code_patterns.keys())
        self._validate_stable_codes()
        self._validate_windowing()
        self._validate_code_parent_rules(known)
        self._validate_statement_type_keys(known)
        self._validate_explicit_block_target_references(known)
        self._validate_selection_overlap_policy()
        self._validate_sfi_hierarchy_roles()
        return self


# Schemas for extraction windows.
class CodeMatch(BaseSchema):
    """A document profile code regex match found in an extraction window."""

    code_type: str = Field(
        description="Document profile local code pattern key, such as 'content_standard'."
    )
    end_char: int = Field(
        description="End character offset of the match within window source_text.", ge=0
    )
    start_char: int = Field(
        description="Start character offset of the match within window source_text.",
        ge=0,
    )
    statement_type: Optional[str] = Field(
        default=None,
        description="Document profile statement type associated with this code type, if any.",
    )
    value: str = Field(description="Matched source-code surface form.")

    @model_validator(mode="after")
    def validate_offsets(self) -> Self:
        """Validate that `end_char` is not before `start_char`.

        Returns
        -------
        Self
            The validated code match.

        Raises
        ------
        ValueError
            If the end offset is smaller than the start offset.
        """

        if self.end_char < self.start_char:
            raise ValueError("end_char must be >= start_char.")

        return self


class CodeParentHint(BaseSchema):
    """A deterministic parent-code suggestion derived from document profile rules."""

    child_code: str = Field(description="Matched child code.")
    child_code_type: str = Field(description="Document profile local child code type.")
    method: str = Field(
        description="Document profile rule method used to derive parent_code."
    )
    parent_code: str = Field(description="Derived parent code.")
    parent_code_type: str = Field(
        description="Document profile local parent code type."
    )
    parent_statement_type: Optional[str] = Field(
        default=None,
        description="Document profile statement type associated with the parent code type, if any.",
    )


class ExtractionWindow(BaseSchema):
    """LLM-ready prompt payload for one Academic Standards extraction window."""

    block: Optional[dict[str, Any]] = Field(
        default=None, description="Block-specific source payload for block windows."
    )
    code_matches: list[CodeMatch] = Field(
        default_factory=list,
        description="Document profile code matches found in source_text.",
    )
    code_parent_hints: list[CodeParentHint] = Field(
        default_factory=list,
        description="Document profile derived code parent hints for later extraction/validation.",
    )
    context_path_text: str = Field(
        description="Readable context path assembled from nearby headings."
    )
    deterministic_hints: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-semantic deterministic hints for LLM extraction and merging.",
    )
    doc_key: str = Field(description="Source DocumentIR doc_key.")
    framework_title: str = Field(description="DocumentProfile framework title.")
    llm_task_instructions: str = Field(
        description="Task instructions for the later SFI extraction LLM call."
    )
    nearby_headings: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Raw nearby heading references for debug/fallback context.",
    )
    pdf_name: Optional[str] = Field(default=None, description="Source PDF filename.")
    primary_language: str = Field(description="DocumentProfile primary language.")
    profile_extraction_instructions: str = Field(
        description="DocumentProfile.sfi_extraction_instructions."
    )
    section_path: list[dict[str, Any]] = Field(
        default_factory=list, description="Raw DocumentIR section_path for the window."
    )
    segment_kind: Literal["block", "table"] = Field(description="Source segment kind.")
    source_provenance: list[dict[str, Any]] = Field(
        default_factory=list, description="Segment/page provenance for the source."
    )
    source_segment_ids: list[str] = Field(
        description="DocumentIR segment_id values included in this window.",
        min_length=1,
    )
    source_text: str = Field(
        description="Human-readable source text assembled from the window payload."
    )
    structured_context: list[StructuredContextItem] = Field(
        default_factory=list,
        description="Document profile derived structured context, if document profile rules are available.",
    )
    subject: str = Field(description="DocumentProfile subject.")
    table: Optional[ExtractionWindowTablePayload] = Field(
        default=None, description="Table-specific source payload for table windows."
    )
    window_id: str = Field(description="Deterministic extraction-window identifier.")
    window_index: int = Field(
        description="0-based index in extraction-window order.", ge=0
    )
    window_notes: list[str] = Field(
        default_factory=list, description="Implementation/debug notes for this window."
    )

    @model_validator(mode="after")
    def validate_payload_matches_segment_kind(self) -> Self:
        """Validate that block/table payloads match segment_kind.

        Returns
        -------
        Self
            The validated extraction window.

        Raises
        ------
        ValueError
            If the payload does not match the declared segment kind.
        """

        if self.segment_kind == "block" and self.block is None:
            raise ValueError("Block extraction windows require block payload.")

        if self.segment_kind == "block" and self.table is not None:
            raise ValueError("Block extraction windows must not include table payload.")

        if self.segment_kind == "table" and self.table is None:
            raise ValueError("Table extraction windows require table payload.")

        if self.segment_kind == "table" and self.block is not None:
            raise ValueError("Table extraction windows must not include block payload.")

        return self


class ExtractionWindowTablePayload(BaseSchema):
    """Table-specific payload included in an extraction window."""

    body_row_end_index_exclusive: int = Field(
        description="Exclusive end index in the source table rows for body rows.", ge=0
    )
    body_row_start_index: int = Field(
        description="Inclusive start index in the source table rows for body rows.",
        ge=0,
    )
    columns_signature: Optional[str] = Field(
        default=None, description="DocumentIR columns_signature for this table."
    )
    grid_sources: Optional[list[list[dict[str, Any]]]] = Field(
        default=None,
        description="Optional grid source-debug view aligned to selected row_indexes.",
    )
    header_row_count: int = Field(description="Number of source header rows.", ge=0)
    header_rows: list[dict[str, Any]] = Field(
        default_factory=list, description="Raw source table header rows."
    )
    header_rows_canonical: list[list[str]] = Field(
        default_factory=list, description="Canonical header text rows from DocumentIR."
    )
    local_code: Optional[str] = Field(
        default=None, description="Resolved table local_code, if present."
    )
    n_cols: int = Field(description="Maximum source table column count.", ge=1)
    row_indexes: list[int] = Field(
        default_factory=list,
        description="Source table row indexes included in rows/rows_grid/rows_filldown.",
    )
    row_provenance: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="Optional row provenance aligned to selected row_indexes.",
    )
    rows: list[dict[str, Any]] = Field(
        default_factory=list, description="Raw selected source rows/cells."
    )
    rows_filldown: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="Optional filldown rows aligned to selected row_indexes.",
    )
    rows_grid: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="Optional grid-normalized rows aligned to selected row_indexes.",
    )
    source_table_row_count: int = Field(
        description="Total number of rows in the source TableSegment.", ge=0
    )
    table_window_mode: Literal["row_chunks", "whole_table"] = Field(
        description="Profile table-window mode used for this table payload."
    )

    @model_validator(mode="after")
    def validate_row_ranges(self) -> Self:
        """Validate row-index and row-range consistency.

        Returns
        -------
        Self
            The validated table payload.

        Raises
        ------
        ValueError
            If the row range or aligned helper views are inconsistent.
        """

        if self.body_row_end_index_exclusive < self.body_row_start_index:
            raise ValueError(
                "body_row_end_index_exclusive must be >= body_row_start_index."
            )

        if self.body_row_end_index_exclusive > self.source_table_row_count:
            raise ValueError(
                "body_row_end_index_exclusive cannot exceed source_table_row_count."
            )

        if len(self.rows) != len(self.row_indexes):
            raise ValueError("rows must be aligned to row_indexes.")

        for helper_field_name in [
            "grid_sources",
            "row_provenance",
            "rows_filldown",
            "rows_grid",
        ]:
            helper_value = getattr(self, helper_field_name)

            if helper_value is not None and len(helper_value) != len(self.row_indexes):
                raise ValueError(
                    f"{helper_field_name} must be aligned to row_indexes when present."
                )

        return self


class SelectedExtractionSegment(BaseSchema):
    """A DocumentIR segment selected for Academic Standards extraction."""

    block_type: Optional[str] = Field(
        default=None,
        description="Block type for selected block segments; null for table segments.",
    )
    columns_signature: Optional[str] = Field(
        default=None,
        description="Table columns_signature for selected table segments; null for blocks.",
    )
    local_code: Optional[str] = Field(
        default=None, description="DocumentIR local_code for the segment, if present."
    )
    row_count: Optional[int] = Field(
        default=None,
        description="Number of source table rows for table selections; null for blocks.",
        ge=0,
    )
    section_path: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Raw DocumentIR section_path for the selected segment.",
    )
    segment_id: str = Field(description="DocumentIR segment_id.")
    segment_kind: Literal["block", "table"] = Field(
        description="Selected segment kind."
    )
    selection_id: str = Field(description="Deterministic selection identifier.")
    selection_index: int = Field(description="0-based index in selected-segment order.")
    selection_reasons: list[str] = Field(
        default_factory=list,
        description="Deterministic reasons this segment was selected.",
    )
    source_page_indexes: list[int] = Field(
        default_factory=list,
        description="Sorted unique 0-based source page indexes for this segment.",
    )

    @field_validator("selection_reasons")
    @classmethod
    def validate_selection_reasons(cls, v: list[str]) -> list[str]:
        """Require at least one non-empty selection reason.

        Parameters
        ----------
        v
            Selection reasons.

        Returns
        -------
        list[str]
            Cleaned selection reasons.

        Raises
        ------
        ValueError
            If no non-empty reasons are provided.
        """

        cleaned = unique_clean_strings(v)

        if not cleaned:
            raise ValueError("SelectedExtractionSegment requires selection_reasons.")

        return cleaned


class SelectedExtractionSegmentsArtifact(BaseSchema):
    """Artifact containing selected extraction segments and counts for inspection
    purposes.
    """

    counts_by_reason: dict[str, int] = Field(default_factory=dict)
    counts_by_segment_kind: dict[str, int] = Field(default_factory=dict)
    selected_segments: list[SelectedExtractionSegment] = Field(default_factory=list)
    total_selected_segments: int = Field(default=0, ge=0)


class StructuredContextItem(BaseSchema):
    """Document profile derived context item attached to an extraction window."""

    label: str = Field(description="Display label derived from the matched heading.")
    metadata: dict[str, Any] = Field(default_factory=dict)
    normalized_statement_type: str = Field(
        description="Expected normalized SFI type if this context becomes a grouping."
    )
    role: str = Field(description="Context role, such as grade_level or strand.")
    rule_name: str = Field(
        description="Document profile context-heading rule that matched."
    )
    source_heading_item_index: int = Field(
        description="Source PageIR item index for the heading reference.", ge=0
    )
    source_heading_page_index: int = Field(
        description="Source page index for the heading reference.", ge=0
    )
    source_text: str = Field(description="Raw source heading text that matched.")
    statement_type: str = Field(
        description="Source-facing statement type if this context becomes a grouping."
    )


# CURRENTLY UNUSED #
def _strip_and_require_non_empty_str(v: str) -> str:
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


def _validate_iso8601_str(v: Optional[str]) -> Optional[str]:
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


class _HasDateFields:
    """Structural type stub for models with date_created/date_modified fields."""

    date_created: Optional[str]
    date_modified: Optional[str]


class _HasCaseIdentifierFields:
    """Structural type stub for models with case_identifier_uri/uuid fields."""

    case_identifier_uri: str
    case_identifier_uuid: UUID


class _CaseIdentifierMixin:
    """Mixin providing CASE-style URI/UUID validation.

    Consuming models must declare `case_identifier_uri: str` and
    `case_identifier_uuid: UUID`.
    """

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

    @model_validator(mode="after")
    def _check_case_uri_contains_uuid(
        self: _HasCaseIdentifierFields,
    ) -> _HasCaseIdentifierFields:
        """Validate that case_identifier_uri includes case_identifier_uuid (deterministic
        traceability).

        Returns
        -------
        Self
            The validated model instance.

        Raises
        ------
        ValueError
            If case_identifier_uri does not include case_identifier_uuid.
        """

        if str(self.case_identifier_uuid) not in self.case_identifier_uri:
            raise ValueError("case_identifier_uri must include case_identifier_uuid")

        return self


class _DateValidationMixin:
    """Mixin providing ISO-8601 date validation and modified >= created check.

    Consuming models must declare `date_created: Optional[str]` and
    `date_modified: Optional[str]`.
    """

    @field_validator("date_created", "date_modified")
    @classmethod
    def _validate_iso8601_dates(cls, v: Optional[str]) -> Optional[str]:
        """Validate that date_created and date_modified, if provided, are valid
        ISO-8601 strings.

        Parameters
        ----------
        v
            The date string to validate.

        Returns
        -------
        Optional[str]
            The validated date string or None.
        """

        return _validate_iso8601_str(v)

    @model_validator(mode="after")
    def _check_modified_not_before_created(self: _HasDateFields) -> _HasDateFields:
        """If both dates exist, ensure dateModified >= dateCreated.

        Returns
        -------
        Self
            The validated model instance.

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


# Schemas for LLM responses.
class AtomicSkill(BaseSchema):
    """An atomic skill extracted from a single expectation statement.

    NB:

    1. `description` is the atomic skill statement (display-language policy).
    2. `rationale` is optional guidance explaining the decomposition decision.
    """

    description: str = Field(
        description="Atomic skill statement (not an activity/resource).", min_length=1
    )
    rationale: Optional[str] = Field(
        default=None,
        description="Optional brief rationale explaining the decomposition.",
    )


class SFIAtomicSkills(BaseSchema):
    """Atomic skills for a single StandardsFrameworkItem (expectation)."""

    sfi_uuid: UUID = Field(
        description="CASE UUID of the supporting StandardsFrameworkItem."
    )
    skills: list[AtomicSkill] = Field(default_factory=list)


class AtomicSkillsResponse(BaseSchema):
    """Top-level structured response for atomic skills inference."""

    items: list[SFIAtomicSkills] = Field(default_factory=list)


class ProgressionEdge(BaseSchema):
    """A single suggested edge between two StandardsFrameworkItems."""

    confidence: float = Field(
        description="0..1 calibrated confidence (higher = more certain).",
        ge=0.0,
        le=1.0,
    )
    progression_subtype: Optional[_ProgressionSubtype] = Field(
        default=None,
        description=(
            "For Phase 1 within-level buildsTowards only: "
            "'developmental_prerequisite' means the source is a meaningful prerequisite "
            "for a more complex or dependent target; 'recurring_practice' means the "
            "target is a later curriculum occurrence continuing practice of the same "
            "or substantially similar skill."
        ),
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
    def _validate_uuid_str(cls, v: Any) -> str:
        """Strip whitespace and validate that the value is a parseable UUID string.

        Parameters
        ----------
        v
            The input value to validate.

        Returns
        -------
        str
            The validated and stripped UUID string.

        Raises
        ------
        ValueError
            If the input value is null, empty, or not a valid UUID string.
        """

        if v is None:
            raise ValueError("UUID cannot be null")

        s = str(v).strip()

        if not s:
            raise ValueError("UUID cannot be empty")

        try:
            UUID(s)
        except Exception as e:  # pylint: disable=broad-except
            raise ValueError(f"Invalid UUID string: {s}") from e

        return s


class ProgressionEdgesResponse(BaseSchema):
    """Top-level structured response: a list of edges (may be empty)."""

    edges: list[ProgressionEdge] = Field(default_factory=list)


# Schemas for nodes.
class StandardsFramework(_CaseIdentifierMixin, _DateValidationMixin, BaseSchema):
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
    in_language: LanguageField = Field(
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
    metadata: _MetadataT = Field(
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


class StandardsFrameworkItem(_CaseIdentifierMixin, _DateValidationMixin, BaseSchema):
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

    metadata: _MetadataT = Field(
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
    in_language: LanguageField = Field(
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
    normalized_statement_type: _NormalizedStatementType = Field(
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
            raise TypeError("grade_level must be a list of strings")

        cleaned: list[str] = []
        seen: set[str] = set()

        for item in v:
            if not isinstance(item, str):
                raise TypeError("grade_level must contain only strings")

            s = item.strip()

            if not s:
                continue

            if s not in seen:
                cleaned.append(s)
                seen.add(s)

        return cleaned

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


class LearningComponent(_DateValidationMixin, BaseSchema):
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
    in_language: LanguageField = Field(
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
    metadata: _MetadataT = Field(
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


# Schemas for relationship.
class Relationship(_DateValidationMixin, BaseSchema):
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
    metadata: _MetadataT = Field(
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

        if self.relationship_type not in _AllowedRelationshipTypes:
            raise ValueError(
                f"Unsupported relationshipType: {self.relationship_type}\n"
                f"Valid relationship types are: {_AllowedRelationshipTypes}"
            )

        if self.source_entity_key not in _AllowedEntityKeys:
            raise ValueError(f"Invalid sourceEntityKey: {self.source_entity_key}")

        if self.target_entity_key not in _AllowedEntityKeys:
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


# Schemas for provenance.
class BBox(BaseSchema):
    """Bounding box in pixel coordinates."""

    coord_space: Literal["px"] = "px"
    x0: float = Field(..., description="Left coordinate in pixels.", ge=0.0)
    x1: float = Field(..., description="Right coordinate in pixels.", ge=0.0)
    y0: float = Field(..., description="Top coordinate in pixels.", ge=0.0)
    y1: float = Field(..., description="Bottom coordinate in pixels.", ge=0.0)

    @model_validator(mode="before")
    @classmethod
    def _coerce_list(cls, data: Any) -> Any:
        """Coerce a list or tuple of 4 numbers into a BBox dict.

        Parameters
        ----------
        data
            The input data to validate, which may be a dict or a list/tuple of 4
            numbers.

        Returns
        -------
        Any
            The validated BBox data, either as a dict or the original data if it was
            not a list/tuple of 4 numbers.

        Raises
        ------
        ValueError
            If the input is a list/tuple but does not have exactly 4 numbers.
        """

        if isinstance(data, (list, tuple)):
            if len(data) != 4:
                raise ValueError(
                    "Bounding box must have exactly 4 numbers: [x0, y0, x1, y1]."
                )

            return {"x0": data[0], "y0": data[1], "x1": data[2], "y1": data[3]}
        return data

    @model_validator(mode="after")
    def _normalize_axis_order(self) -> BBox:
        """Normalize bbox ordering and expand zero-size axes.

        Returns
        -------
        BBox
            The BBox object with normalized coordinates.
        """

        self.x0, self.y0, self.x1, self.y1 = validate_bbox_order(
            [self.x0, self.y0, self.x1, self.y1]
        )
        return self


class EntityProvenance(BaseSchema):
    """Provenance information for a node."""

    bbox: Optional[BBox] = None
    canonical_node_id: str
    columns_signatures: list[str] = Field(
        default_factory=list,
        description=(
            "Columns signature(s) from the source segment decision(s) for this entity, "
            "if the node originated from table-based extraction. Empty for non-table nodes."
        ),
    )
    dialect_fallbacks: dict[str, str] = Field(default_factory=dict)
    entity_identifier: UUID
    entity_type: str = Field(
        default="",
        description="Entity type label (e.g., StandardsFrameworkItem, LearningComponent).",
    )
    local_code: Optional[str] = Field(default=None)
    page_indices: list[int] = Field(default_factory=list)
    role: str = Field(
        default="",
        description=(
            "Source role label for the entity (e.g., NodeRole value for SFIs, "
            "'framework' for StandardsFramework, 'learning_component' for LCs)."
        ),
    )
    section_path_text: list[str] = Field(default_factory=list)
    source_decision_ids: list[str] = Field(default_factory=list)
    source_segment_ids: list[str] = Field(default_factory=list)
    text: Optional[TextUnit] = None


# Schemas for export configurations.
class EntityProvenanceExport(BaseSchema):
    """Schema for entity provenance export.

    Flat lookup table: export_id -> canonical_node_id, source provenance fields.
    Designed for debugging and auditing without cracking open nested entity metadata.
    """

    doc_key: Optional[str] = None
    entities: list[EntityProvenance] = Field(
        default_factory=list, description="List of entities."
    )
    pdf_name: Optional[str] = None


class HierarchyOrderExport(BaseSchema):
    """Schema for exporting explicit ordering of child SFIs under parent SFIs."""

    order: dict[str, list[str]] = Field(
        default_factory=dict, description="Order of child SFIs."
    )


# Schemas for graph validation reporting.
class GraphValidationIssue(BaseSchema):
    """A single validation finding."""

    code: str
    context: dict[str, Any] = Field(default_factory=dict)
    level: _ValidationLevel
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
        level: _ValidationLevel,
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
        lines = ["GraphValidationReport pre-validation failed:"]

        for i in self.errors()[:15]:
            lines.append(f"- [{i.code}] {i.message}")

        if len(self.errors()) > 15:
            lines.append(f"- ... plus {len(self.errors()) - 15} more errors")

        raise ValueError("\n".join(lines))


class PolicyCoverageReport(BaseSchema):
    """Aggregate report explaining what was emitted, dropped, and why.

    This is the primary debuggability artifact for the KG export pipeline. It answers
    "why was this node dropped?" and provides summary statistics for every export phase.
    """

    doc_key: Optional[str] = None
    generated_at: Optional[str] = None
    pdf_name: Optional[str] = None

    # Node-level drop accounting (academic standards).
    drop_reason_counts: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Complete count of Academic Standards drop reasons, keyed by the raw "
            "drop-reason taxonomy string. This preserves new upstream drop reasons "
            "even before curated report fields are added."
        ),
    )
    dropped_aux_attached_to_expectation: int = Field(
        default=0,
        description=(
            "Aux guidance/descriptor nodes converted to expectation metadata "
            "attachments and therefore not emitted as standalone SFIs."
        ),
    )
    dropped_aux_descendants_suppressed: int = Field(
        default=0,
        description=(
            "Descendant nodes suppressed because they lived under an aux node that was "
            "converted into expectation metadata."
        ),
    )
    dropped_due_to_expectation_metadata_attachment: int = Field(
        default=0,
        description=(
            "Total nodes dropped because of expectation-metadata attachment handling: "
            "attached aux nodes plus descendants suppressed below attached aux nodes."
        ),
    )
    dropped_by_columns_signature: dict[str, int] = Field(
        default_factory=dict,
        description="Count of nodes dropped per columns_signature value.",
    )
    dropped_by_decision_type: dict[str, int] = Field(
        default_factory=dict,
        description="Count of nodes dropped per segment decision type (e.g., ignore, unresolved).",
    )
    dropped_descriptor: int = Field(
        default=0, description="Nodes dropped because as_descriptor_handling == 'drop'."
    )
    dropped_guidance: int = Field(
        default=0, description="Nodes dropped because as_guidance_handling == 'drop'."
    )
    dropped_non_grouping_role: int = Field(
        default=0,
        description=(
            "Total nodes dropped with a drop:non_grouping_role:* reason. See "
            "dropped_non_grouping_role_counts for the suffix-level breakdown."
        ),
    )
    dropped_non_grouping_role_counts: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Count of nodes dropped per drop:non_grouping_role:* suffix, such as "
            "'drop' or 'structural_parent'."
        ),
    )
    pruned_empty_groupings: int = Field(
        default=0,
        description="Grouping nodes pruned because they had zero emitted children.",
    )
    total_canonical_nodes: int = 0
    total_emitted_sfis: int = 0

    # Canonical-node accounting completeness.
    coverage_accounted_canonical_nodes: int = Field(
        default=0,
        description=(
            "Number of non-root canonical node IDs covered by the union of emitted "
            "SFI source nodes and dropped canonical nodes."
        ),
    )
    coverage_accounting_ok: bool = Field(
        default=True,
        description=(
            "True when every non-root canonical node is accounted for exactly once as "
            "either emitted as an SFI or intentionally dropped by Academic Standards "
            "policy, with no emitted/dropped overlap and no non-canonical node IDs "
            "appearing in either set."
        ),
    )
    coverage_details_limit: int = Field(
        default=200,
        description="Maximum number of node IDs included per coverage-details list.",
    )
    coverage_details_truncated: bool = Field(
        default=False,
        description=(
            "Whether any coverage-details list was truncated because it exceeded "
            "coverage_details_limit."
        ),
    )
    coverage_emitted_and_dropped_overlap_count: int = Field(
        default=0,
        description=(
            "Canonical node IDs that appear both as emitted SFI source nodes and as "
            "dropped nodes."
        ),
    )
    coverage_emitted_and_dropped_overlap_node_ids: list[str] = Field(
        default_factory=list,
        description="Example canonical node IDs both emitted and dropped.",
    )
    coverage_emitted_sfis_missing_canonical_node_id_count: int = Field(
        default=0,
        description=(
            "Emitted SFI rows whose metadata lacks canonical_node_id and therefore "
            "cannot be tied back to a Canonical IR node for coverage accounting."
        ),
    )
    coverage_emitted_sfis_missing_canonical_node_id_examples: list[str] = Field(
        default_factory=list,
        description=(
            "Example emitted SFI UUIDs whose metadata lacks canonical_node_id."
        ),
    )
    coverage_noncanonical_dropped_node_count: int = Field(
        default=0,
        description=(
            "Academic Standards drop_reasons node IDs that are not non-root Canonical "
            "IR node IDs."
        ),
    )
    coverage_noncanonical_dropped_node_ids: list[str] = Field(
        default_factory=list,
        description="Example dropped node IDs not present in Canonical IR.",
    )
    coverage_noncanonical_emitted_node_count: int = Field(
        default=0,
        description=(
            "Emitted SFI metadata canonical_node_id values that are not non-root "
            "Canonical IR node IDs."
        ),
    )
    coverage_noncanonical_emitted_node_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Example emitted canonical_node_id values not present in Canonical IR."
        ),
    )
    coverage_over_accounted_canonical_nodes: int = Field(
        default=0,
        description=(
            "Canonical-node accounting anomalies caused by emitted/dropped overlap or "
            "node IDs in emitted/drop accounting that do not exist in the Canonical IR."
        ),
    )
    coverage_unaccounted_canonical_nodes: int = Field(
        default=0,
        description=(
            "Canonical nodes that are neither emitted as SFIs nor present in Academic "
            "Standards drop_reasons."
        ),
    )
    coverage_unaccounted_node_ids: list[str] = Field(
        default_factory=list,
        description="Example canonical node IDs not emitted and not dropped.",
    )

    # Aux reparenting/attachment and hierarchy-hoisting stats.
    attach_only_newly_attached_aux_node_count: int = Field(
        default=0,
        description=(
            "Unique aux node IDs newly discovered and attached during the step-4 "
            "attach-only discovery pass."
        ),
    )
    attached_aux_subtree_root_count: int = Field(
        default=0,
        description=(
            "Attached aux nodes that still had exported child subtrees when subtree "
            "suppression ran."
        ),
    )
    child_layout_aux_attached_count: int = Field(
        default=0,
        description=(
            "Aux statements discovered as canonical children of an expectation and "
            "attached during step 3 export-tree construction."
        ),
    )
    dropped_parents_processed: int = Field(
        default=0,
        description=(
            "Dropped parents with emitted children that were processed during hierarchy "
            "hoisting."
        ),
    )
    dropped_parents_removed_from_parent_lists_count: int = Field(
        default=0,
        description=(
            "Dropped parents whose stale references were removed from at least one "
            "export parent child-list."
        ),
    )
    orphan_aux_count: int = Field(
        default=0,
        description=(
            "Total unique aux nodes that could not be attached to an owning "
            "expectation (for example, no preceding expectation in sibling order)."
        ),
    )
    reattach_appended_without_anchor_order_count: int = Field(
        default=0,
        description=(
            "Hoist operations that appended children because no anchor-based ordering "
            "signal was available."
        ),
    )
    reattach_original_sibling_fallback_count: int = Field(
        default=0,
        description=(
            "Hoist operations that used original sibling-position fallback because "
            "canonical edge ordering was unavailable."
        ),
    )
    reattached_children_count: int = Field(
        default=0,
        description="Emitted children newly inserted under surviving ancestors.",
    )
    removed_dropped_parent_reference_list_count: int = Field(
        default=0,
        description=(
            "Total number of export parent child-lists modified while removing stale "
            "dropped-parent references."
        ),
    )
    sibling_aux_reparented_count: int = Field(
        default=0,
        description=(
            "Aux sibling statements reparented to the most recent preceding "
            "expectation during step 3 export-tree construction."
        ),
    )
    suppressed_attached_aux_descendant_count: int = Field(
        default=0,
        description=(
            "Descendant nodes suppressed below attached aux nodes so they cannot be "
            "hoisted back into the exported hierarchy."
        ),
    )
    suppressed_attached_aux_node_count: int = Field(
        default=0,
        description=(
            "Attached aux nodes newly suppressed as standalone SFIs by the "
            "attach-to-expectation policy enforcement step."
        ),
    )
    total_attached_aux_node_count: int = Field(
        default=0,
        description=(
            "Total unique aux node IDs tracked as attached to an expectation after "
            "the attach-only discovery pass (steps 3-4 combined)."
        ),
    )

    # LC stats.
    lc_fallback_sfis_count: int = Field(
        default=0,
        description="LC-source SFIs that fell back to deterministic 1_to_1 generation.",
    )
    lc_max_splits_observed: int = 0
    lc_source_exclusion_reason_counts: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Counts of LC-source eligibility exclusion reasons. The eligible reason is "
            "omitted so this field focuses on exclusions."
        ),
    )
    lc_split_policy: str = ""
    lc_splits_distribution: dict[str, int] = Field(
        default_factory=dict,
        description="Distribution of split counts: how many SFIs produced N LCs. Keys are stringified integers (e.g., '1': 500, '2': 50).",
    )
    total_lc_source_sfis_considered: int = Field(
        default=0,
        description="Total SFIs considered by LC-source eligibility filtering.",
    )
    total_lc_source_sfis_eligible: int = Field(
        default=0,
        description="Total SFIs eligible to generate LearningComponents.",
    )
    total_lc_source_sfis_empty_text: int = Field(
        default=0,
        description=(
            "Eligible LC-source SFIs skipped or producing zero LCs because usable text "
            "was empty."
        ),
    )
    total_lc_source_sfis_excluded: int = Field(
        default=0,
        description="Total SFIs excluded by LC-source eligibility filtering.",
    )
    total_lcs: int = 0

    # LP stats (populated only when `generate_learning_progressions` is True).
    lp_bucket_drop_counts: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Summarized Learning Progressions bucket/source drops copied from the LP "
            "report."
        ),
    )
    lp_candidate_builds_towards: int = Field(
        default=0,
        description="Candidate buildsTowards edges before filtering.",
    )
    lp_candidate_edges_after_dedupe: int = Field(
        default=0,
        description="Total candidate edges remaining after deduplication.",
    )
    lp_candidate_edges_pre_dedupe: int = Field(
        default=0,
        description="Total candidate edges before deduplication.",
    )
    lp_candidate_relates_to: int = Field(
        default=0,
        description="Candidate relatesTo edges before filtering.",
    )
    lp_dropped_cap_relates: int = Field(
        default=0,
        description="relatesTo edges dropped due to per-node cap.",
    )
    lp_dropped_dedupe: int = Field(
        default=0,
        description="Edges dropped during deduplication.",
    )
    lp_dropped_doc_order_builds: int = Field(
        default=0,
        description="buildsTowards edges dropped by document-order filter.",
    )
    lp_dropped_low_conf_builds: int = Field(
        default=0,
        description="buildsTowards edges dropped due to low confidence.",
    )
    lp_dropped_low_conf_relates: int = Field(
        default=0,
        description="relatesTo edges dropped due to low confidence.",
    )
    lp_final_relationship_counts: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Final Learning Progressions relationship counts copied from "
            "learning_progressions.report['final_relationship_counts']."
        ),
    )
    lp_kept_builds_towards: int = Field(
        default=0,
        description="Final kept buildsTowards edges after all filters.",
    )
    lp_kept_builds_towards_before_doc_order: int = Field(
        default=0,
        description="Kept buildsTowards edges before document-order filter.",
    )
    lp_kept_relates_to: int = Field(
        default=0,
        description="Final kept relatesTo edges after all filters.",
    )
    lp_kept_relates_to_after_threshold: int = Field(
        default=0,
        description="Kept relatesTo edges after confidence threshold filter.",
    )
    lp_phase_toggles: dict[str, Any] = Field(default_factory=dict)
    lp_thresholds: dict[str, Any] = Field(default_factory=dict)

    # Detailed per-node drop log (first N for debuggability).
    drop_details: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Per-node drop log (capped at drop_details_limit entries). Each entry "
            "includes canonical_node_id, role, and drop_reason."
        ),
    )
    drop_details_limit: int = Field(
        default=200,
        description="Maximum number of drop_details entries included in this report.",
    )
    drop_details_total_count: int = Field(
        default=0,
        description="Total number of dropped nodes before drop_details truncation.",
    )
    drop_details_truncated: bool = Field(
        default=False,
        description="Whether drop_details was truncated because it exceeded the limit.",
    )
