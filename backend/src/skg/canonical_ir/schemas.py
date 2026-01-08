"""This module contains schemas used for creating the canonical Intermediate
Representation (IR) from a single document IR.
"""

# Future Library
from __future__ import annotations

# Standard Library
from datetime import datetime, timezone
from typing import Any, Literal, Optional

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Package Library
from skg.canonical_ir.parse_document import (
    BlockSpec,
    GraphPolicy,
    HeadingRule,
    LeafParsingConfig,
    ParseConfig,
)
from skg.canonical_ir.table_specs import TableSpec
from skg.page_ir.schemas import TextUnit
from skg.utils.constants import StatementRole


# Schemas for primitives.
class BaseModelCanonicalIR(BaseModel):
    """Base model that enforces 'additionalProperties: false' in JSON schema for
    compatibility with OpenAI Structured Outputs.
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)


# Schemas for canonical IR.
class CanonicalEdge(BaseModelCanonicalIR):
    """A hierarchy edge in the canonical IR."""

    child_id: str
    parent_id: str
    rel: Literal["hasChild"] = "hasChild"


class CanonicalNode(BaseModelCanonicalIR):
    """A single semantic node in the curriculum hierarchy.

    NB: Do NOT include children nodes here---this is meant to be a flat hierarchy.
    """

    bbox: Optional[list[float]] = None
    body: TextUnit | None = Field(None, description="Full normative text.")
    doc_key: str
    list_id: Optional[str] = Field(
        None, description="The alphanumeric code (e.g., '3.1.1')"
    )
    node_id: str = Field(..., description="Deterministic global UUID.")
    page_indices: list[int] = Field(default_factory=list)
    role: StatementRole
    source_ids: list[str] = Field(
        default_factory=list, description="Pointers to segment keys."
    )
    title: TextUnit | None = Field(None, description="Short title/heading text")


class NormalizedRow(BaseModelCanonicalIR):
    """A row where all spans are filled and cells are accessible by index."""

    cells: list[TextUnit | None]
    original_row_index: int
    provenance_bbox: list[float]
    provenance_page_index: int
    provenance_slice_index: int
    row_index: int


class CanonicalIR(BaseModelCanonicalIR):
    """Represents a semantic, provenance-rich representation of a document."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    doc_key: str
    edges: list[CanonicalEdge] = Field(default_factory=list)
    pdf_name: Optional[str] = None
    nodes: list[CanonicalNode] = Field(default_factory=list)
    root_id: str
    unresolved: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# Schemas for specs.
class BlockSpecModel(BaseModelCanonicalIR):
    """Specification for matching and interpreting blocks in the canonical IR."""

    block_types: list[str]
    context_scope: Literal["current", "any"] = "current"
    name: str
    pattern: str | None = None
    required_block_terms: list[str] = Field(default_factory=list)
    required_context_terms: list[str] = Field(default_factory=list)
    role: StatementRole = StatementRole.UNRESOLVED
    split: bool = True

    @model_validator(mode="after")
    def _validate_block_types(self) -> BlockSpecModel:
        """Ensures that block_types is non-empty.

        Returns
        -------
        TableSpecModel
            The validated TableSpecModel object.

        Raises
        ------
        ValueError
            If block_types is empty.
        """

        if not self.block_types:
            raise ValueError("BlockSpec.block_types must be non-empty")

        return self

    def to_runtime(self) -> BlockSpec:
        """Returns the runtime BlockSpec object.

        Returns
        -------
        BlockSpec
            The runtime BlockSpec object.
        """

        return BlockSpec(
            block_types=tuple(self.block_types),
            context_scope=self.context_scope,
            name=self.name,
            pattern=self.pattern,
            required_block_terms=tuple(self.required_block_terms),
            required_context_terms=tuple(self.required_context_terms),
            role=self.role,
            split=self.split,
        )


class GraphPolicySpec(BaseModelCanonicalIR):
    """Graph construction policy for the canonical IR."""

    keep_first_parent: bool = True
    mode: Literal["tree"] = "tree"

    def to_runtime(self) -> GraphPolicy:
        """Returns the runtime GraphPolicy object.

        Returns
        -------
        GraphPolicy
            The runtime GraphPolicy object.
        """

        return GraphPolicy(keep_first_parent=self.keep_first_parent, mode=self.mode)


class HeadingRuleSpec(BaseModelCanonicalIR):
    """Rule for matching headings in the document IR."""

    level: int | None = None
    pattern: str | None = None
    required_terms: list[str] = Field(default_factory=list)
    role: StatementRole
    unique_per_occurrence: bool = False

    @model_validator(mode="after")
    def _require_some_matcher(self) -> HeadingRuleSpec:
        """Ensures that at least one of pattern or required_terms is set.

        Returns
        -------
        HeadingRuleSpec
            The validated HeadingRuleSpec object.

        Raises
        ------
        ValueError
            If neither pattern nor required_terms is set.
        """

        if not self.pattern and not self.required_terms:
            raise ValueError(
                "HeadingRule must have at least one of: pattern or required_terms"
            )
        return self

    def to_runtime(self) -> HeadingRule:
        """Returns the runtime HeadingRule object.

        Returns
        -------
        HeadingRule
            The runtime HeadingRule object.
        """

        return HeadingRule(
            level=self.level,
            pattern=self.pattern,
            required_terms=tuple(self.required_terms),
            role=self.role,
            unique_per_occurrence=self.unique_per_occurrence,
        )


class LeafParsingConfigSpec(BaseModelCanonicalIR):
    """Configuration for parsing leaf nodes in the canonical IR."""

    bullet_regex: str = LeafParsingConfig().bullet_regex
    code_line_regex: str | None = None
    drop_empty: bool = True
    split_on_blank_lines: bool = True
    split_on_bullets: bool = True

    def to_runtime(self) -> LeafParsingConfig:
        """Returns the runtime LeafParsingConfig object.

        Returns
        -------
        LeafParsingConfig
            The runtime LeafParsingConfig object.
        """

        return LeafParsingConfig(
            bullet_regex=self.bullet_regex,
            code_line_regex=self.code_line_regex,
            drop_empty=self.drop_empty,
            split_on_blank_lines=self.split_on_blank_lines,
            split_on_bullets=self.split_on_bullets,
        )


class ParserConfigSpec(BaseModelCanonicalIR):
    """Configuration for parsing a document IR into a canonical IR."""

    block_specs: list[BlockSpecModel] = Field(default_factory=list)
    graph_policy: GraphPolicySpec = Field(default_factory=GraphPolicySpec)
    heading_rules: list[HeadingRuleSpec] = Field(default_factory=list)
    leaf_parsing: LeafParsingConfigSpec = Field(default_factory=LeafParsingConfigSpec)
    table_specs: list[TableSpec] = Field(default_factory=list)

    def to_runtime(self) -> ParseConfig:
        """Returns the runtime ParseConfig object.

        Returns
        -------
        ParseConfig
            The runtime ParseConfig object.
        """

        return ParseConfig(
            block_specs=[b.to_runtime() for b in self.block_specs],
            graph_policy=self.graph_policy.to_runtime(),
            heading_rules=[r.to_runtime() for r in self.heading_rules],
            leaf_parsing=self.leaf_parsing.to_runtime(),
            table_specs=[t.to_runtime() for t in self.table_specs],
        )


class TableSpecModel(BaseModelCanonicalIR):
    """Specification for matching and interpreting tables in the canonical IR."""

    ignore: bool = False
    name: str

    # Matching hints.
    caption_regex: str | None = None
    required_caption_any_terms: list[str] = Field(default_factory=list)
    required_caption_terms: list[str] = Field(default_factory=list)
    required_header_any_terms: list[str] = Field(default_factory=list)
    required_header_terms: list[str] = Field(default_factory=list)
    required_local_code_prefixes: list[str] = Field(default_factory=list)

    # Structural columns.
    group_col: int | None = None
    subject_col: int | None = None
    topic_col: int | None = None

    # Leaf columns.
    descriptor_col: int | None = None
    expectation_col: int | None = None

    # Forward fill (structural columns frequently span/merge).
    forward_fill_cols: list[int] = Field(default_factory=list)

    # Leaf parsing behavior.
    split_descriptors: bool = True
    split_expectations: bool = True

    # Role mapping.
    descriptor_role: StatementRole = StatementRole.DESCRIPTOR
    expectation_role: StatementRole = StatementRole.EXPECTATION
    group_role: StatementRole = StatementRole.STRAND
    subject_role: StatementRole = StatementRole.SUBJECT
    topic_role: StatementRole = StatementRole.TOPIC

    @model_validator(mode="after")
    def _validate_table_spec(self) -> TableSpecModel:
        """Validates the TableSpecModel object.

        Returns
        -------
        TableSpecModel
            The validated TableSpecModel object.

        Raises
        ------
        ValueError
            If the TableSpecModel is invalid.
        """

        # Prevent "match everything" tablespecs.
        has_any_matcher = any(
            [
                self.caption_regex,
                self.required_caption_any_terms,
                self.required_caption_terms,
                self.required_header_any_terms,
                self.required_header_terms,
                self.required_local_code_prefixes,
            ]
        )
        if not has_any_matcher:
            raise ValueError(
                f"TableSpec '{self.name}' has no match constraints; it would match everything."
            )

        # If not ignored, should usually have expectation_col.
        if not self.ignore and self.expectation_col is None:
            raise ValueError(
                f"TableSpec '{self.name}' must set expectation_col unless ignore=true."
            )

        # Validate non-negative cols.
        cols = [
            self.group_col,
            self.subject_col,
            self.topic_col,
            self.descriptor_col,
            self.expectation_col,
        ] + list(self.forward_fill_cols or [])
        for c in cols:
            if c is not None and c < 0:
                raise ValueError(
                    f"TableSpec '{self.name}' has negative column index: {c}"
                )

        return self

    def to_runtime(self) -> TableSpec:
        """Returns the runtime TableSpec object.

        Returns
        -------
        TableSpec
            The runtime TableSpec object.
        """

        return TableSpec(
            caption_regex=self.caption_regex,
            descriptor_col=self.descriptor_col,
            descriptor_role=self.descriptor_role,
            expectation_col=self.expectation_col,
            expectation_role=self.expectation_role,
            forward_fill_cols=tuple(self.forward_fill_cols),
            group_col=self.group_col,
            group_role=self.group_role,
            ignore=self.ignore,
            name=self.name,
            required_caption_any_terms=tuple(self.required_caption_any_terms),
            required_caption_terms=tuple(self.required_caption_terms),
            required_header_any_terms=tuple(self.required_header_any_terms),
            required_header_terms=tuple(self.required_header_terms),
            required_local_code_prefixes=tuple(self.required_local_code_prefixes),
            subject_col=self.subject_col,
            topic_col=self.topic_col,
            split_descriptors=self.split_descriptors,
            split_expectations=self.split_expectations,
            subject_role=self.subject_role,
            topic_role=self.topic_role,
        )
