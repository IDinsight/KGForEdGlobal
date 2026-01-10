"""This module contains schemas used for creating the canonical Intermediate
Representation (IR) from a single document IR.
"""

# Future Library
from __future__ import annotations

# Standard Library
import re

from datetime import datetime, timezone
from typing import Any, Literal, Optional, Sequence

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Package Library
from skg.page_ir.schemas import TextUnit
from skg.utils.constants import StatementRole


# Schemas for primitives.
class BaseModelCanonicalIR(BaseModel):
    """Base model that enforces 'additionalProperties: false' in JSON schema for
    compatibility with OpenAI Structured Outputs.
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    @staticmethod
    def tokenish_contains(*, haystack: str, term: str) -> bool:
        """Check if `term` exists in `haystack` as a distinct token/phrase. Prevents
        partial matches like 'art' matching inside 'partial'.

        Parameters
        ----------
        haystack
            The text to search within.
        term
            The term to search for.

        Returns
        -------
        bool
            True if the term is found as a distinct token/phrase.
        """

        h = (haystack or "").casefold()
        tt = (term or "").casefold().strip()

        if not tt:
            return True

        # Allow flexible whitespace in multi-word terms.
        tt_re = re.escape(tt).replace(r"\ ", r"\s+")
        return re.search(rf"(^|[^a-z0-9]){tt_re}([^a-z0-9]|$)", h) is not None


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
class CanonicalRowIR(BaseModelCanonicalIR):
    """Intermediate representation for a single curriculum row (diagnostic purposes
    only).

    This class is used primarily in "Wizard Mode" to capture structured data extracted
    from tables for debugging or validation purposes.

    Attributes
    ----------
    descriptors_raw
        The raw text extracted for the descriptor column.
    expectations_raw
        The raw text extracted for the expectation column.
    group
        The extracted group (e.g., Strand) title.
    guidance_raw
        The raw text extracted for the guidance column.
    provenance
        Metadata tracing the row back to the source PDF segment (page, bbox, etc.).
    subject
        The extracted subject title.
    topic
        The extracted topic title.
    """

    descriptors_raw: Optional[str]
    expectations_raw: Optional[str]
    group: Optional[str]
    guidance_raw: Optional[str]
    provenance: dict[str, Any]
    subject: Optional[str]
    topic: Optional[str]


class LeafStatement(BaseModelCanonicalIR):
    """Container for a parsed atomic statement.

    Attributes
    ----------
    body
        The textual content of the statement.
    list_id
        The identifier code (e.g., "1.2", "a)") if extracted.
    """

    body: str
    list_id: str | None = None


class BlockSpec(BaseModelCanonicalIR):
    """Configuration for interpreting non-heading blocks (e.g., lists/paragraphs).

    This class defines how to match specific block types and convert them into
    statements with specific roles (e.g., turning a bulleted list into EXPECTATION
    nodes).

    Attributes
    ----------
    block_types
        A tuple of valid block types to match against. Values should match DocumentIR
        `block_type` strings (e.g., "list", "paragraph").
    context_scope
        The scope of the context search.
            - "current": Checks only the immediate parent heading.
            - "any": Checks the entire heading stack.
    name
        A unique identifier for this specification.
    required_block_any_terms
        Terms where at least one must appear within the block's text for a match.
    required_block_terms
        Terms that must appear within the block's text itself for a match.
    required_context_any_terms
        Terms where at least one must appear in the context (heading hierarchy) for
        this spec to match.
    required_context_terms
        Terms that must appear in the context (heading hierarchy) for this spec to
        match.
    role
        The semantic role to assign to the emitted leaf nodes.
    pattern
        A regex pattern string to match against the block's text.
    split
        For paragraph blocks, whether to split the text into smaller chunks using
        `LeafParsingConfig` rules. If False, the entire paragraph becomes one node.
    """

    block_types: list[str]
    context_scope: Literal["current", "any"] = "current"
    name: str
    pattern: str | None = None
    required_block_any_terms: list[str] = Field(default_factory=list)
    required_block_terms: list[str] = Field(default_factory=list)
    required_context_any_terms: list[str] = Field(default_factory=list)
    required_context_terms: list[str] = Field(default_factory=list)
    role: StatementRole = StatementRole.UNRESOLVED
    split: bool = True

    @model_validator(mode="after")
    def _validate_block_types(self) -> BlockSpec:
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

    def matches(
        self, *, block_text: str, block_type: str, context_titles: list[str]
    ) -> bool:
        """Check if a given block matches this specification.

        Parameters
        ----------
        block_text
            The extracted text content of the block.
        block_type
            The type of the block (e.g., 'paragraph', 'list').
        context_titles
            A list of titles from the current hierarchy stack to check against.

        Returns
        -------
        bool
            True if the block matches all criteria, False otherwise.
        """

        if self.block_types and block_type not in self.block_types:
            return False

        bt = block_text or ""
        bt_cf = bt.casefold()

        if self.required_block_terms and not all(
            self.tokenish_contains(haystack=bt_cf, term=t)
            for t in self.required_block_terms
        ):
            return False

        if self.required_block_any_terms and not any(
            self.tokenish_contains(haystack=bt_cf, term=t)
            for t in self.required_block_any_terms
        ):
            return False

        if self.pattern and not re.search(self.pattern, bt, flags=re.IGNORECASE):
            return False

        if self.required_context_terms or self.required_context_any_terms:
            ctx = " | ".join(context_titles).casefold()
            fail_all = self.required_context_terms and not all(
                self.tokenish_contains(haystack=ctx, term=term)
                for term in self.required_context_terms
            )
            fail_any = self.required_context_any_terms and not any(
                self.tokenish_contains(haystack=ctx, term=term)
                for term in self.required_context_any_terms
            )

            if fail_all or fail_any:
                return False

        return True


class GraphPolicy(BaseModelCanonicalIR):
    """Configuration for graph topology enforcement.

    Attributes
    ----------
    experimental_allow_keep_last
        If True, allows keep_first_parent=False to be used (experimental feature).
    mode
        The topology mode. Currently only "tree" is supported.
    keep_first_parent
        If True, when a node has multiple parents (violating tree structure), the edge
        to the first-seen parent is kept, and subsequent edges are dropped.
    """

    experimental_allow_keep_last: bool = False
    keep_first_parent: bool = True
    mode: Literal["tree"] = "tree"

    @model_validator(mode="after")
    def _guard_keep_last_parent(self) -> GraphPolicy:
        """Ensures that keep_first_parent=false is only used when explicitly allowed.

        Returns
        -------
        GraphPolicy
            The validated GraphPolicy object.

        Raises
        ------
        ValueError
            If keep_first_parent is false but experimental_allow_keep_last is not true.
        """

        if not self.keep_first_parent and not self.experimental_allow_keep_last:
            raise ValueError(
                "graph_policy.keep_first_parent=false is experimental; set "
                "graph_policy.experimental_allow_keep_last=true to enable it."
            )

        return self


class HeadingRule(BaseModelCanonicalIR):
    """Rule for matching heading text to a semantic role.

    Attributes
    ----------
    level
        The hierarchy level. Lower integers indicate higher positions in the hierarchy
        (closer to root).
    pattern
        A regex pattern to match against the heading text.
    required_terms
        A set of substrings; if provided, all must be present in the heading text for a
        match.
    role
        The role to assign if the heading matches (e.g., SUBJECT, GRADE_LEVEL).
    unique_per_occurrence
        If True, forces the generation of a unique ID for every occurrence of this
        heading, even if the text is identical to a previous one. Useful for generic
        headings like "Overview" or "Assessment".
    """

    level: int | None = None
    pattern: str | None = None
    required_terms: list[str] = Field(default_factory=list)
    role: StatementRole
    unique_per_occurrence: bool = False

    @model_validator(mode="after")
    def _require_some_matcher(self) -> HeadingRule:
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

    def matches(self, text: str) -> bool:
        """Check if the text matches this heading rule.

        Parameters
        ----------
        text
            The heading text to check.

        Returns
        -------
        bool
            True if the rule matches.
        """

        t = (text or "").casefold()

        if self.required_terms and not all(
            self.tokenish_contains(haystack=t, term=term)
            for term in self.required_terms
        ):
            return False

        if self.pattern:
            return re.search(self.pattern, text or "", flags=re.IGNORECASE) is not None

        # Prevent "always true" rules: require at least one required_term if no pattern.
        return bool(self.required_terms)


class LeafParsingConfig(BaseModelCanonicalIR):
    """Configuration for splitting text blocks into atomic leaf statements.

    Attributes
    ----------
    bullet_regex
        Regex to identify bullet points at the start of a line. Default handles
        standard bullets, numbering (1., 1)), and letters (a)).
    code_line_regex
        Regex to identify codes at the start of a line. Must include named groups
        `(?P<list_id>...)` and `(?P<body>...)`.
    drop_empty
        Whether to discard empty strings after splitting and cleaning.
    split_on_blank_lines
        Whether to treat blank lines as delimiters between statements.
    split_on_bullets
        Whether to treat lines starting with bullets as new statements.
    """

    bullet_regex: str = r"^\s*(?:[-•*]|\d+[.)]|\([a-zA-Z0-9]+\)|[a-zA-Z][.)])\s+"
    code_line_regex: str | None = None
    drop_empty: bool = True
    split_on_blank_lines: bool = True
    split_on_bullets: bool = True


class TableSpec(BaseModelCanonicalIR):
    """Specification for matching and interpreting a curriculum table.

    A `TableSpec``has two responsibilities:
        1. **MATCH**: decide whether a stitched TableSegment is this table kind.
        2. **INTERPRET**: define which columns map to which canonical roles.

    Attributes
    ----------
    caption_regex
        An optional regex string to validate the caption (case-insensitive).
    descriptor_col
        0-based index of the column containing Descriptor data.
    descriptor_role
        The canonical role for the descriptor column.
    expectation_col
        0-based index of the column containing Expectation/Competency data.
    expectation_role
        The canonical role for the expectation column.
    descriptor_parenting
        How to assign parenting for descriptor statements.
    forward_fill_cols
        Column indices that should be forward-filled (merged cells handling).
    group_col
        0-based index of the column containing Group/Strand data.
    group_role
        The canonical role for the group column.
    guidance_col
        0-based index of the column containing Guidance/Notes data.
    guidance_parenting
        How to assign parenting for guidance statements.
    guidance_role
        The canonical role for the guidance column.
    ignore
        If True, the matched table is acknowledged but not converted to nodes/edges.
    name
        A unique identifier for this table specification.
    required_caption_any_terms
        Terms where **at least one** must appear in the caption for a match (OR logic).
    required_caption_terms
        Terms that **must all** appear in the caption for a match (AND logic).
        Case-insensitive.
    required_header_any_terms
        Terms where **at least one** must appear in the headers (OR logic).
    required_header_terms
        Terms that **must all** appear in the headers (AND logic). Matching is
        "token-ish" (whole word).
    required_local_code_prefixes
        Prefixes that the local table code must start with (e.g., "Table 4").
    split_descriptors
        Whether to split text in the descriptor column into multiple statements.
    split_expectations
        Whether to split text in the expectation column into multiple statements.
    split_guidance
        Whether to split text in the guidance column into multiple statements.
    subject_col
        0-based index of the column containing Subject data.
    subject_role
        The canonical role to assign to nodes extracted from the subject column.
    topic_col
        0-based index of the column containing Topic data.
    topic_role
        The canonical role for the topic column.
    """

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
    guidance_col: int | None = None

    # Forward fill (structural columns frequently span/merge).
    forward_fill_cols: list[int] = Field(default_factory=list)

    # Leaf parsing behavior.
    descriptor_parenting: Literal["group", "expectation_if_single"] = (
        "expectation_if_single"
    )
    guidance_parenting: Literal["group", "expectation_if_single"] = (
        "expectation_if_single"
    )
    split_descriptors: bool = True
    split_expectations: bool = True
    split_guidance: bool = True

    # Role mapping.
    descriptor_role: StatementRole = StatementRole.DESCRIPTOR
    expectation_role: StatementRole = StatementRole.EXPECTATION
    group_role: StatementRole = StatementRole.STRAND
    guidance_role: StatementRole = StatementRole.GUIDANCE
    subject_role: StatementRole = StatementRole.SUBJECT
    topic_role: StatementRole = StatementRole.TOPIC

    @model_validator(mode="after")
    def _validate_table_spec(self) -> TableSpec:
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
            self.guidance_col,
        ] + list(self.forward_fill_cols or [])
        for c in cols:
            if c is not None and c < 0:
                raise ValueError(
                    f"TableSpec '{self.name}' has negative column index: {c}"
                )

        return self

    def _matches_local_code(self, local_code: str | None) -> bool:
        """Check if the local code starts with one of the required prefixes.

        Parameters
        ----------
        local_code
            The local identifier code (e.g., "Table 4").

        Returns
        -------
        bool
            True if the local code matches the required prefixes.
        """

        if not self.required_local_code_prefixes:
            return True

        if not local_code:
            return False

        lc_norm = local_code.casefold()

        return any(
            lc_norm.startswith(p.casefold()) for p in self.required_local_code_prefixes
        )

    def _matches_regex(self, caption_text: str | None) -> bool:
        """Check if the caption matches the optional regex.

        Parameters
        ----------
        caption_text
            The full text of the table caption (e.g., "Table 4.1:

        Returns
        -------
        bool
            True if the caption matches the regex or if no regex is defined.
        """

        if not self.caption_regex:
            return True

        if not caption_text:
            return False

        return bool(re.search(self.caption_regex, caption_text, flags=re.IGNORECASE))

    def _satisfies_terms(
        self, *, required_all: list[str], required_any: list[str], text: str
    ) -> bool:
        """Check if text satisfies the 'ALL' and 'ANY' term constraints.

        Parameters
        ----------
        required_all
            Terms that must ALL be present.
        required_any
            Terms where AT LEAST ONE must be present (if tuple is not empty).
        text
            The normalized text to check against.

        Returns
        -------
        bool
            True if all constraints are met.
        """

        # Check AND logic (All terms must be present).
        if not all(self.tokenish_contains(haystack=text, term=t) for t in required_all):
            return False

        # Check OR logic (At least one term, if any are specified).
        if required_any and not any(
            self.tokenish_contains(haystack=text, term=t) for t in required_any
        ):
            return False

        return True

    def match(
        self,
        *,
        caption_text: str | None,
        header_texts: Sequence[str],
        local_code: str | None,
    ) -> bool:
        """Return True if this table matches the spec. Matches are determined based on
        local code prefixes, header terms, and caption terms using the configured
        AND/OR logic.

        Parameters
        ----------
        caption_text
            The full text of the table caption (e.g., "Table 4.1: Physics").
        header_texts
            A sequence of strings representing the table headers.
        local_code
            The local identifier code (e.g., "Table 4").

        Returns
        -------
        bool
            True if the provided metadata matches the criteria in this spec.
        """

        # Normalize inputs.
        h_norm = " | ".join([t.casefold() for t in header_texts])
        c_norm = (caption_text or "").casefold()

        # Perform matching checks.
        if not self._matches_local_code(local_code):
            return False

        if not self._satisfies_terms(
            required_all=self.required_header_terms,
            required_any=self.required_header_any_terms,
            text=h_norm,
        ):
            return False

        if not self._satisfies_terms(
            required_all=self.required_caption_terms,
            required_any=self.required_caption_any_terms,
            text=c_norm,
        ):
            return False

        if not self._matches_regex(caption_text):
            return False

        return True


class ParserConfig(BaseModelCanonicalIR):
    """Master configuration for the document parser.

    Attributes
    ----------
    block_specs
        Rules for processing non-heading blocks (paragraphs, lists).
    capture_table_row_facts_sample_always
        If True, emits row samples even for successfully parsed tables.
    capture_unmatched_blocks_in_wizard
        If True, unmatched blocks are added to the 'unresolved' output list.
    capture_unmatched_tables_in_wizard
        If True, unmatched tables are added to the 'unresolved' output list.
    graph_policy
        Rules for enforcing graph topology (e.g., tree structure).
    heading_rules
        Rules for interpreting headings and assigning hierarchy levels.
    ignore_section_heading_patterns
        If a HEADING matches any of these regex patterns (case-insensitive), ignore all
        subsequent segments until the next heading at the same or higher level
        (peer-or-higher).
    leaf_parsing
        Rules for splitting text chunks into atomic leaf nodes.
    role_levels
        Default hierarchy levels for roles. Lower values are higher in the tree.
    table_specs
        Specs for extracting structured data from tables.
    unmatched_block_max_chars
        Maximum length of the preview string for unmatched blocks.
    unmatched_block_min_chars
        Minimum length for an unmatched block to be reported (reduces noise).
    unmatched_table_min_nonempty_cells
        Heuristic filter: minimum non-empty cells to consider a table "real".
    unmatched_table_min_total_chars
        Heuristic filter: minimum text content to consider a table "real".
    """

    block_specs: list[BlockSpec] = Field(default_factory=list)
    graph_policy: GraphPolicy = Field(default_factory=GraphPolicy)
    heading_rules: list[HeadingRule] = Field(default_factory=list)
    ignore_section_heading_patterns: list[str] = Field(default_factory=list)
    leaf_parsing: LeafParsingConfig = Field(default_factory=LeafParsingConfig)
    role_levels: dict[StatementRole, int] = Field(
        default_factory=lambda: {
            StatementRole.FRAMEWORK: 0,
            StatementRole.GRADE_LEVEL: 10,
            StatementRole.SUBJECT: 20,
            StatementRole.STRAND: 30,
            StatementRole.TOPIC: 40,
            StatementRole.SECTION: 50,
            StatementRole.UNRESOLVED: 90,
            StatementRole.EXPECTATION: 100,
            StatementRole.DESCRIPTOR: 110,
            StatementRole.GUIDANCE: 120,
        }
    )
    table_specs: list[TableSpec] = Field(default_factory=list)

    # Wizard/debugging configuration.
    caption_binding: Literal["next", "prev", "both"] = "next"
    caption_to_table_max_gap_blocks: int = 2
    caption_to_table_max_gap_chars: int = 80
    capture_table_row_facts_sample_always: bool = False
    capture_unmatched_blocks_in_wizard: bool = True
    capture_unmatched_headings_in_wizard: bool = True
    capture_unmatched_tables_in_wizard: bool = True
    unmatched_block_max_chars: int = 600
    unmatched_block_min_chars: int = 120
    unmatched_table_min_nonempty_cells: int = 12
    unmatched_table_min_total_chars: int = 80
