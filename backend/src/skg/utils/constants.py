"""This module contains constants used throughout the application."""

# Standard Library
import re

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence


# Enums for various constant types.
class BlockType(str, Enum):
    """The visual structural category of a content block.

    Types include:
        - artifact: Page numbers, running headers/footers (to be filtered later)
        - caption: Text specifically describing a table or figure
        - figure: Diagrams/figures/illustrations/flowcharts (boxed visual regions)
        - heading: Visually distinct titles, section headers
        - list: A group of items (bullets, numbers)
        - paragraph: Standard blocks of prose
    """

    ARTIFACT = "artifact"
    CAPTION = "caption"
    FIGURE = "figure"
    HEADING = "heading"
    LIST = "list"
    PARAGRAPH = "paragraph"


class FigureKind(str, Enum):
    """Classification of figure/diagram type (non-semantic)."""

    CHART = "chart"
    DIAGRAM = "diagram"
    FLOWCHART = "flowchart"
    GRAPH = "graph"
    ILLUSTRATION = "illustration"
    IMAGE = "image"
    MAP = "map"
    OTHER = "other"
    SCHEMATIC = "schematic"
    TIMELINE = "timeline"
    UNKNOWN = "unknown"


class ItemBoundary(str, Enum):
    """Enumeration for item boundary states on a page.

    NB: These are semantic continuity flags (not "missing borders"):
        - both: Item continues from previous page AND onto next page (middle slice of a
            long item)
        - complete: Item is fully contained on this page
        - resumed: Item continues from the previous page
        - truncated: Item continues onto the next page
    """

    BOTH = "both"
    COMPLETE = "complete"
    RESUMED = "resumed"
    TRUNCATED = "truncated"


class PageBoundaryState(str, Enum):
    """Enumeration for page boundary states of items.

    NB: These are semantic continuity flags (not "missing borders"):
        - both: Item continues from previous page AND onto next page (middle slice of a
            long item)
        - from_prev: Item continues from the previous page
        - to_next: Item continues onto the next page
        - standalone: Item is fully contained on this page
    """

    BOTH = "both"
    CONTINUES_FROM_PREV = "from_prev"
    CONTINUES_TO_NEXT = "to_next"
    STANDALONE = "standalone"


class PageContinuationKind(str, Enum):
    """The type of content continuity detected between pages."""

    FIGURE = "figure"
    NONE = "none"
    TABLE = "table"
    TEXT = "text"


class StatementRole(str, Enum):
    """Semantic role of a KG node in the hierarchy."""

    EXPECTATION = "expectation"  # Normative statement (standard/outcome)
    DESCRIPTOR = "descriptor"  # Performance indicator/benchmark
    FRAMEWORK = "framework"  # The root document
    GRADE_LEVEL = "grade_level"  # e.g., "Standard III"
    SECTION = "section"  # Structural grouping (e.g., "Section One")
    STRAND = "strand"  # e.g., "Main Competence"
    SUBJECT = "subject"  # e.g., "Mathematics"
    TOPIC = "topic"  # e.g., "Topic" or sub-strand
    UNRESOLVED = "unresolved"  # Content that could not be classified


# Dataclasses for various configurations.
@dataclass(frozen=True)
class BlockSpec:
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
    required_block_terms
        Terms that must appear within the block's text itself for a match.
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

    name: str
    block_types: tuple[str, ...]
    role: StatementRole = StatementRole.UNRESOLVED
    required_context_terms: tuple[str, ...] = ()
    context_scope: str = "current"
    required_block_terms: tuple[str, ...] = ()
    pattern: str | None = None
    split: bool = True

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
            t.casefold() in bt_cf for t in self.required_block_terms
        ):
            return False

        if self.pattern and not re.search(self.pattern, bt, flags=re.IGNORECASE):
            return False

        if self.required_context_terms:
            ctx = " | ".join(context_titles).casefold()
            if not all(term.casefold() in ctx for term in self.required_context_terms):
                return False

        return True


@dataclass(frozen=True)
class CanonicalRowIR:
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
    provenance: dict[str, Any]
    subject: Optional[str]
    topic: Optional[str]


@dataclass(frozen=True)
class GraphPolicy:
    """Configuration for graph topology enforcement.

    Attributes
    ----------
    mode
        The topology mode. Currently only "tree" is supported.
    keep_first_parent
        If True, when a node has multiple parents (violating tree structure), the edge
        to the first-seen parent is kept, and subsequent edges are dropped.
    """

    keep_first_parent: bool = True
    mode: str = "tree"


@dataclass(frozen=True)
class HeadingRule:
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

    role: StatementRole
    level: int | None = None
    pattern: str | None = None
    required_terms: tuple[str, ...] = ()
    unique_per_occurrence: bool = False

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
            term.casefold() in t for term in self.required_terms
        ):
            return False

        if self.pattern:
            return re.search(self.pattern, text or "", flags=re.IGNORECASE) is not None

        # Prevent "always true" rules: require at least one required_term if no pattern.
        return bool(self.required_terms)


@dataclass(frozen=True)
class LeafParsingConfig:
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


@dataclass
class LeafStatement:
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


@dataclass(frozen=True)
class TableSpec:
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
    forward_fill_cols
        Column indices that should be forward-filled (merged cells handling).
    group_col
        0-based index of the column containing Group/Strand data.
    group_role
        The canonical role for the group column.
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
    subject_col
        0-based index of the column containing Subject data.
    subject_role
        The canonical role to assign to nodes extracted from the subject column.
    topic_col
        0-based index of the column containing Topic data.
    topic_role
        The canonical role for the topic column.
    """

    name: str

    # If True: matched table is acknowledged but not converted to nodes/edges.
    ignore: bool = False

    # Matching hints.

    # All-of/any-of caption/header checks keep specs simple but more resilient to
    # wording/formatting drift.
    caption_regex: str | None = None  # optional regex on caption (case-insensitive)
    required_caption_any_terms: tuple[str, ...] = ()  # OR: at least one must match
    required_caption_terms: tuple[str, ...] = ()  # AND: all must match
    required_header_any_terms: tuple[
        str, ...
    ] = ()  # OR: at least one must match (token-ish)
    required_header_terms: tuple[str, ...] = ()  # AND: all must match (token-ish)
    required_local_code_prefixes: tuple[str, ...] = ()

    # Interpretation

    # Structural columns.
    group_col: int | None = None
    subject_col: int | None = None
    topic_col: int | None = None

    # Leaf columns.
    descriptor_col: int | None = None
    expectation_col: int | None = None

    # Forward fill (structural columns frequently span/merge).
    forward_fill_cols: tuple[int, ...] = ()

    # Leaf parsing behavior.
    split_descriptors: bool = True
    split_expectations: bool = True

    # Role mapping.
    descriptor_role: StatementRole = StatementRole.DESCRIPTOR
    expectation_role: StatementRole = StatementRole.EXPECTATION
    group_role: StatementRole = StatementRole.STRAND
    subject_role: StatementRole = StatementRole.SUBJECT
    topic_role: StatementRole = StatementRole.TOPIC

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
        self, *, required_all: tuple[str, ...], required_any: tuple[str, ...], text: str
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
        if not all(
            self._tokenish_contains(haystack=text, term=t) for t in required_all
        ):
            return False

        # Check OR logic (At least one term, if any are specified).
        if required_any:
            if not any(
                self._tokenish_contains(haystack=text, term=t) for t in required_any
            ):
                return False

        return True

    @staticmethod
    def _tokenish_contains(*, haystack: str, term: str) -> bool:
        """Check if ``term`` exists in ``haystack`` as a distinct token/phrase.
        Treats non-alphanumeric characters as boundaries to prevent partial matches
        (e.g., 'art' matching inside 'part').

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

        tt = (term or "").casefold().strip()
        if not tt:
            return True

        # Allow flexible whitespace in multi-word terms.
        tt_re = re.escape(tt).replace(r"\ ", r"\\s+")

        return re.search(rf"(^|[^a-z0-9]){tt_re}([^a-z0-9]|$)", haystack) is not None

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


@dataclass(frozen=True)
class ParseConfig:
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

    block_specs: list[BlockSpec] = field(default_factory=list)
    graph_policy: GraphPolicy = field(default_factory=GraphPolicy)
    heading_rules: list[HeadingRule] = field(default_factory=list)
    leaf_parsing: LeafParsingConfig = field(default_factory=LeafParsingConfig)
    role_levels: dict[StatementRole, int] = field(
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
        }
    )
    table_specs: list[TableSpec] = field(default_factory=list)

    # Wizard/debugging configuration.
    capture_table_row_facts_sample_always: bool = False
    capture_unmatched_blocks_in_wizard: bool = True
    capture_unmatched_tables_in_wizard: bool = True
    unmatched_block_max_chars: int = 600
    unmatched_block_min_chars: int = 120
    unmatched_table_min_nonempty_cells: int = 12
    unmatched_table_min_total_chars: int = 80
