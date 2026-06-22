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


class CreateKGConfig(BaseSchema):
    """Configuration for knowledge graph creation from document IR."""

    # General attributes.
    overwrite: bool = Field(
        False, description="Overwrite existing knowledge graph artifacts."
    )

    # FRAMEWORK METADATA #
    adoption_status: str | None = None
    attribution_statement: str
    author: str
    context_spine: _ContextSpineConfig = Field(
        default_factory=_ContextSpineConfig,
        description=(
            "KG configuration-driven rules for deriving structured extraction-window context "
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

    # ACADEMIC STANDARDS #

    # Code handling.
    as_code_parent_rules: list[dict[str, str]] = Field(default_factory=list)
    as_code_patterns: dict[str, str] = Field(default_factory=dict)

    # Table selection policy.
    as_excluded_table_columns_signatures: list[str] = Field(
        default_factory=list,
        description=(
            "Column signatures that should never be sent through the table SFI "
            "extraction path, even if another table rule would otherwise include them."
        ),
    )
    as_excluded_table_section_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Regex patterns over bounded nearby heading/section text that exclude "
            "tables from SFI extraction, even if their column signature is otherwise "
            "eligible."
        ),
    )
    as_included_table_columns_signatures: list[str] = Field(
        default_factory=list,
        description=(
            "Column signatures that identify table segments eligible for SFI extraction."
        ),
    )
    as_included_table_section_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Regex patterns over bounded nearby heading/section text that can include "
            "tables when column signatures alone are not sufficient."
        ),
    )
    as_max_rows_per_table_window: Optional[int] = Field(
        default=20,
        description=(
            "Maximum number of table body rows per extraction window. Set to null "
            "to emit one whole-table window per selected table."
        ),
    )
    as_row_overlap: int = Field(default=1, ge=0)

    # Duplication behavior.
    as_duplicate_review_instructions: str
    as_repeated_statement_policy: str = ""
    as_synthetic_merge_key_fields: list[str] = Field(
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
    as_bilingual_pair_policy: str | None = None
    as_sfi_extraction_instructions: str

    # LEARNING COMPONENTS #
    lc_generation_instructions: str

    @field_validator(
        "as_duplicate_review_instructions",
        "as_sfi_extraction_instructions",
        "attribution_statement",
        "author",
        "country",
        "framework_title",
        "jurisdiction",
        "lc_generation_instructions",
        "license",
        "primary_language",
        "provider",
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

    @field_validator("adoption_status", "as_bilingual_pair_policy", mode="before")
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
                    f"CreateKGConfig.{field_name} must contain only strings."
                )

            pattern_clean = pattern.strip()

            if not pattern_clean:
                continue

            try:
                re.compile(pattern_clean)
            except re.error as exc:
                raise ValueError(
                    f"Invalid regex in CreateKGConfig.{field_name}: {pattern_clean!r}: {exc}"
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
                    f"CreateKGConfig.{field_name} must contain only strings."
                )

            value_clean = value.strip()

            if not value_clean:
                continue

            if value_clean not in seen:
                cleaned.append(value_clean)
                seen.add(value_clean)

        return cleaned

    @field_validator(
        "as_excluded_table_columns_signatures", "as_included_table_columns_signatures"
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
        "as_excluded_table_section_patterns", "as_included_table_section_patterns"
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

    @field_validator("as_code_patterns")
    @classmethod
    def validate_as_code_patterns(cls, v: dict[str, str]) -> dict[str, str]:
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
                    f"as_code_patterns[{name!r}] must be a string. "
                    f"Got {type(pattern).__name__}."
                )

            if not pattern.strip():
                raise ValueError(f"as_code_patterns[{name!r}] must be non-empty.")

            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(
                    f"Invalid regex for as_code_patterns[{name!r}]: {exc}"
                ) from exc

        return v

    @field_validator("languages")
    @classmethod
    def validate_languages(cls, v: list[str]) -> list[str]:
        """Validate configured languages are present, non-empty, and de-duplicated.

        Parameters
        ----------
        v
            Language tags configured for KG extraction.

        Returns
        -------
        list[str]
            Cleaned language tags in stable order.
        """

        if not v:
            raise ValueError(
                "CreateKGConfig.languages must contain at least one value."
            )

        cleaned: list[str] = []
        seen: set[str] = set()

        for language in v:
            if not isinstance(language, str):
                raise TypeError("CreateKGConfig.languages must contain only strings.")

            language_clean = language.strip()

            if not language_clean:
                continue

            if language_clean not in seen:
                cleaned.append(language_clean)
                seen.add(language_clean)

        if not cleaned:
            raise ValueError(
                "CreateKGConfig.languages must contain at least one non-empty value."
            )

        return cleaned

    @field_validator("as_synthetic_merge_key_fields")
    @classmethod
    def validate_as_synthetic_merge_key_fields(cls, v: list[str]) -> list[str]:
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
                    "CreateKGConfig.as_synthetic_merge_key_fields must contain only strings."
                )

            field_name_clean = field_name.strip()

            if not field_name_clean:
                continue

            if field_name_clean not in seen:
                cleaned.append(field_name_clean)
                seen.add(field_name_clean)

        if not cleaned:
            raise ValueError(
                "CreateKGConfig.as_synthetic_merge_key_fields must contain at least one value."
            )

        return cleaned

    def _validate_windowing(self) -> None:
        """Ensure table row windowing configuration is internally consistent.

        Raises
        ------
        ValueError
            If as_max_rows_per_table_window is non-positive, or if as_row_overlap is
            not smaller than as_max_rows_per_table_window when chunking is enabled.
        """

        if self.as_max_rows_per_table_window is None:
            return

        if self.as_max_rows_per_table_window <= 0:
            raise ValueError(
                "CreateKGConfig.as_max_rows_per_table_window must be positive or null."
            )

        if self.as_row_overlap >= self.as_max_rows_per_table_window:
            raise ValueError(
                "CreateKGConfig.as_row_overlap must be smaller than as_max_rows_per_table_window."
            )

    @staticmethod
    def _validate_as_code_parent_rule(
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
            If the rule references unknown patterns, uses a method other than
            `regex_substitution`, is missing required `regex_substitution` fields, or
            has an invalid regex.
        """

        child = rule.get("child")
        parent = rule.get("parent")
        method = rule.get("method")

        if child not in known:
            raise ValueError(
                f"as_code_parent_rules[{idx}] unknown child pattern: {child!r}"
            )

        if parent not in known:
            raise ValueError(
                f"as_code_parent_rules[{idx}] unknown parent pattern: {parent!r}"
            )

        if method != "regex_substitution":
            raise ValueError(
                f"as_code_parent_rules[{idx}] invalid method: {method!r}. "
                f"Only 'regex_substitution' is supported."
            )

        if "regex" not in rule or "replacement" not in rule:
            raise ValueError(
                f"as_code_parent_rules[{idx}] regex_substitution requires regex and replacement"
            )

        re.compile(rule["regex"])

    def _validate_as_code_parent_rules(self, known: set[str]) -> None:
        """Validate all configured code parent rules.

        Parameters
        ----------
        known
            Set of known code pattern names.
        """

        for idx, rule in enumerate(self.as_code_parent_rules):
            self._validate_as_code_parent_rule(idx, rule, known)

    def _validate_selection_overlap_policy(self) -> None:
        """Ensure table-selection policy does not both include and exclude the same
        value.

        Raises
        ------
        ValueError
            If a table columns_signature appears in both included and excluded lists.
        """

        overlapping_table_signatures = sorted(
            set(self.as_included_table_columns_signatures)
            & set(self.as_excluded_table_columns_signatures)
        )

        if overlapping_table_signatures:
            raise ValueError(
                f"CreateKGConfig table-selection policy cannot include and exclude the "
                f"same columns_signature values: {overlapping_table_signatures}"
            )

    @model_validator(mode="after")
    def validate_kg_configuration(self) -> Self:
        """Validate cross-field CreateKGConfig configuration.

        Returns
        -------
        Self
            The validated KG configuration.

        Raises
        ------
        ValueError
            If code handling, parent rules, or windowing configuration is invalid.
        """

        known = set(self.as_code_patterns.keys())
        self._validate_windowing()
        self._validate_as_code_parent_rules(known)
        self._validate_selection_overlap_policy()
        return self


# Config schemas.
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
