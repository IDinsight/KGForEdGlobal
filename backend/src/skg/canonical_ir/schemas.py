"""This module contains schemas used for creating the canonical Intermediate
Representation (IR) from a single document IR.
"""

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import json

from collections import Counter
from datetime import datetime, timezone
from typing import Literal, Optional, Self

# Third Party Library
from pydantic import Field, model_validator

# Package Library
from skg.page_ir_extraction.schemas import TextUnit
from skg.schemas import BaseSchema, BBox
from skg.utils.constants import (
    BlockType,
    CaptionKind,
    CurriculumEmitPolicy,
    NodeRole,
    SegmentDecisionType,
    StatementRole,
    UnresolvedReason,
)


def compute_decision_set_id(
    *, decisions: list[SegmentDecision], encoding: str = "utf-8"
) -> str:
    """Compute a stable fingerprint for a SegmentDecision set.

    NB: This MUST be deterministic across reruns. We intentionally exclude: confidence,
    rationale, created_at.

    Parameters
    ----------
    decisions
        The list of SegmentDecision objects to compute the fingerprint for.
    encoding
        The string encoding to use when computing the SHA256 digest.

    Returns
    -------
    str
        The stable SHA256 hex digest representing the decision set ID.

    Raises
    ------
    ValueError
        If any SegmentDecision objects have empty or non-string decision_id.
    """

    stable = []

    for d in sorted(decisions, key=lambda x: x.decision_id or ""):
        if not isinstance(d.decision_id, str) or not d.decision_id:
            raise ValueError(
                f"All SegmentDecision objects must have non-empty decision_id: {d}"
            )

        stable.append(
            {
                "decision_id": d.decision_id,
                "segment_id": d.segment_id,
                "segment_kind": d.segment_kind,
                "decision_type": d.decision_type.value,
                "block_type": (d.block_type.value if d.block_type else None),
                "row_range_start": d.row_range_start,
                "row_range_end": d.row_range_end,
                "context_groupings": [
                    context_grouping.model_dump(exclude_none=True, mode="json")
                    for context_grouping in d.context_groupings
                ],
                "groupings": [
                    group.model_dump(exclude_none=True, mode="json")
                    for group in d.groupings
                ],
                "leaves": [
                    leaf.model_dump(exclude_none=True, mode="json") for leaf in d.leaves
                ],
                "rows": [
                    row.model_dump(exclude_none=True, mode="json") for row in d.rows
                ],
            }
        )

    payload = json.dumps(
        stable, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )

    return hashlib.sha256(payload.encode(encoding)).hexdigest()


# Schemas for provenance.
class CaptionBinding(BaseSchema):
    """Represents a binding between a caption segment and a table segment in DocumentIR."""

    caption_kind: CaptionKind = Field(
        ..., description="Kind of caption (table/figure)."
    )
    caption_page_index: Optional[int] = Field(
        None, description="Page index of the caption."
    )
    caption_segment_id: str = Field(..., description="Segment ID of the caption.")
    caption_text: str = Field(..., description="Text of the caption.")
    gap_segments: int = Field(
        ..., description="Number of segments between caption and table.", ge=0
    )
    table_page_index: Optional[int] = Field(
        None, description="Page index of the table."
    )
    table_segment_id: str = Field(..., description="Segment ID of the table.")


class UnresolvedItem(BaseSchema):
    """Represents a DocumentIR segment (or part of one) that could not be resolved into
    canonical nodes/leaves with sufficient confidence.
    """

    caption_text: str | None = Field(
        default=None, description="Caption text associated with this segment (if any)."
    )
    headers: list[str] = Field(
        default_factory=list,
        description="Extracted header texts for a table (if applicable).",
    )
    kind: Literal["block", "table"] = Field(
        ..., description="High-level kind of unresolved source content."
    )
    local_code: str | None = Field(
        default=None, description="Local identifier (e.g., 'Table 3') if available."
    )
    page_indices: list[int] = Field(
        ..., description="Page indices where this unresolved segment appears."
    )
    reason: UnresolvedReason = Field(
        ..., description="Reason classification for unresolved content (enum)."
    )
    sample: str | None = Field(
        default=None,
        description="Short sample text to help humans debug what went unresolved.",
    )
    section_path_text: list[str] = Field(
        default_factory=list,
        description="Heading context snapshot (strings) from Step 3 for this segment.",
    )
    segment_id: str = Field(
        ..., description="DocumentIR segment_id that produced this unresolved item."
    )


# Schemas for the curriculum skeleton.
class CurriculumColumnMapping(BaseSchema):
    """Maps a table column to a semantic role for EMIT_TABLE_ROWS nodes.

    The `header_pattern` is matched (regex, case-insensitive) against the canonical
    header text for each column. The `role` string encodes both the kind ("grouping" or
    "leaf") and the specific enum value.
    """

    header_pattern: str = Field(
        ..., description="Regex matched against header_rows_canonical cell text."
    )
    role: str = Field(
        ...,
        description=(
            "Semantic role: 'grouping:{NodeRole.value}' (e.g., 'grouping:strand'), "
            "'leaf:{StatementRole.value}' (e.g., 'leaf:expectation'), or 'skip'."
        ),
    )
    source_label_override: Optional[str] = Field(
        default=None,
        description="If set, use this as source_label instead of the column header text.",
    )

    @model_validator(mode="after")
    def validate_role_format(self) -> CurriculumColumnMapping:
        """Ensure `role` is parseable as 'kind:value' or 'skip', and that the value is
        a valid NodeRole or StatementRole member.

        Returns
        -------
        CurriculumColumnMapping
            The validated CurriculumColumnMapping object.

        Raises
        ------
        ValueError
            If `role` is not in the correct format or contains invalid enum values.
        """

        if self.role == "skip":
            return self

        parts = self.role.split(":", 1)

        if len(parts) != 2 or parts[0] not in ("grouping", "leaf"):
            raise ValueError(
                f"ColumnMapping.role must be 'grouping:{{role}}', "
                f"'leaf:{{role}}', or 'skip'. Got: {self.role!r}"
            )

        kind, value = parts

        if kind == "grouping":
            try:
                NodeRole(value)
            except ValueError:
                raise ValueError(
                    f"Unknown NodeRole in ColumnMapping.role: {value!r}. "
                    f"Valid values: {[r.value for r in NodeRole]}"
                ) from None
        elif kind == "leaf":
            try:
                StatementRole(value)
            except ValueError:
                raise ValueError(
                    f"Unknown StatementRole in ColumnMapping.role: {value!r}. "
                    f"Valid values: {[r.value for r in StatementRole]}"
                ) from None

        return self


class CurriculumMultilingualLabel(BaseSchema):
    """Canonical name in one or more languages.

    The `primary` field is the canonical form used in the KG. Additional languages are
    stored in `translations`.
    """

    primary: str = Field(
        ...,
        description="Canonical name in the document's primary administrative language.",
    )
    translations: dict[str, str] = Field(
        default_factory=dict,
        description="Other language versions. Key = BCP-47 code, value = translated name.",
    )


class CurriculumSkeletonMetadata(BaseSchema):
    """Document-level metadata for the curriculum."""

    academic_subject: str = Field(
        ..., description="Canonical subject name in primary language."
    )
    context_groupings_role_order: Optional[list[NodeRole]] = Field(
        default=None,
        description=(
            "Custom role precedence for context_groupings sorting. "
            "If None, uses DEFAULT_CONTEXT_GROUPINGS_ROLE_ORDER from constants.py."
        ),
    )
    country: str = Field(
        ..., description="Country name (e.g., 'Senegal', 'India', 'Kenya')."
    )
    grades: list[str] = Field(
        ..., min_length=1, description="Grade levels covered (e.g., ['CE1', 'CE2'])."
    )
    languages: list[str] = Field(
        ..., min_length=1, description="BCP-47 codes of languages in the document."
    )
    ministry: Optional[str] = Field(default=None, description="Issuing ministry/body.")
    primary_language: str = Field(
        ..., description="Primary administrative language (BCP-47)."
    )
    stage: Optional[str] = Field(
        default=None,
        description="Stage/cycle name (e.g., 'Etape 2', 'Upper Primary').",
    )


class CurriculumSkeletonNode(BaseSchema):
    """A single node in the curriculum skeleton tree.

    1. Every node has an `id` (unique within the skeleton) and a canonical name.
    2. Roles are split: `grouping_role` (NodeRole) for grouping context and `leaf_role`
        (StatementRole) for leaf content. A single node can have both.
    3. Match rules are optional: CONTAINER_ONLY nodes have no rules because they don't
        correspond to document segments.
    4. Metadata fields (grade, substage_index, topic, etc.) are inherited downward.
    5. Children are ordered: their position in the list reflects expected document
        order.
    """

    canonical_name: CurriculumMultilingualLabel
    children: list[CurriculumSkeletonNode] = Field(default_factory=list)
    doc_note: Optional[str] = Field(default=None)
    emit: CurriculumEmitPolicy = Field(default=CurriculumEmitPolicy.EMIT_GROUPING)
    id: str = Field(
        ...,
        description=(
            "Unique identifier within the skeleton (kebab-case). "
            "Convention: '{strand}-{role}-{index}', e.g., 'num-palier-1-def'."
        ),
    )
    match_phrases: list[str] = Field(
        default_factory=list,
        description=(
            "Plain-text phrases to match against document segments. Matching is done "
            "via case-insensitive, unicode-normalized substring containment. "
            "Multiple phrases are OR'd — any match suffices."
        ),
    )
    match_target: Literal["text", "caption"] = Field(
        default="text",
        description=(
            "What to match against. 'text' matches against the segment's combined text "
            "(blocks: heading/paragraph text; tables: not matched). "
            "'caption' matches against the caption text bound to a table segment."
        ),
    )

    # Roles.
    grouping_role: Optional[NodeRole] = Field(
        default=None,
        description="NodeRole for grouping output. Set for nodes that emit groupings.",
    )
    leaf_role: Optional[StatementRole] = Field(
        default=None,
        description="StatementRole for leaf output. Set for nodes that emit leaves.",
    )

    # Column mappings (required for EMIT_TABLE_ROWS).
    column_mappings: list[CurriculumColumnMapping] = Field(
        default_factory=list,
        description="Column-to-role mappings. Required when emit=EMIT_TABLE_ROWS.",
    )

    # Metadata (inherited downward unless overridden by a descendant).
    allow_multiple_segments: bool = Field(
        default=False,
        description="If True, multiple consecutive segments can match this node. Used for bilingual pairs (Jéego + PALIER) that map to one unit.",
    )
    grade: Optional[str] = Field(
        default=None,
        description="Grade level (e.g., 'CE1'). Inherited by children.",
    )
    implicit: bool = Field(
        default=False,
        description=(
            "If True with CONTAINER_ONLY, this node's grouping_role still appears in "
            "context_groupings of descendant matches. Use for logical groupings that "
            "have no document heading but must provide context."
        ),
    )
    is_continuation: bool = Field(
        default=False,
        description="True if this node continues a previous node's substage (e.g., Geometry 'Palier 2 (suite)').",
    )
    local_code: Optional[str] = Field(
        default=None,
        description="Maps to GroupingDecision.local_code (e.g., 'Tableau 4').",
    )
    source_label: Optional[str] = Field(
        default=None,
        description="Maps to GroupingDecision.source_label — original document text.",
    )
    substage_index: Optional[int] = Field(
        default=None,
        description="1-based substage/palier number. For building progression chains.",
    )
    topic: Optional[CurriculumMultilingualLabel] = Field(
        default=None, description="Topic subdivision within a substage."
    )

    @model_validator(mode="after")
    def validate_emit_match_consistency(self) -> CurriculumSkeletonNode:
        """CONTAINER_ONLY nodes must have no match phrases; others should have at least 1.

        Returns
        -------
        CurriculumSkeletonNode
            The validated CurriculumSkeletonNode object.

        Raises
        ------
        ValueError
            If a CONTAINER_ONLY node has match phrases, or if a non-CONTAINER_ONLY node
            has no match phrases (unless it's a FRAMEWORK node).
        """

        if self.emit == CurriculumEmitPolicy.CONTAINER_ONLY and self.match_phrases:
            raise ValueError(
                f"Node '{self.id}': CONTAINER_ONLY must not have match_phrases."
            )

        if (
            self.emit != CurriculumEmitPolicy.CONTAINER_ONLY
            and not self.match_phrases
            and self.grouping_role != NodeRole.FRAMEWORK
        ):
            raise ValueError(
                f"Node '{self.id}': non-CONTAINER_ONLY nodes must have >= 1 match phrase."
            )

        return self

    @model_validator(mode="after")
    def validate_role_emit_consistency(self) -> CurriculumSkeletonNode:
        """Ensure the node has the right roles for its emit policy.

        Returns
        -------
        CurriculumSkeletonNode
            The validated CurriculumSkeletonNode object.

        Raises
        ------
        ValueError
            If the node's roles are inconsistent with its emit policy.
        """

        if (
            self.emit == CurriculumEmitPolicy.EMIT_GROUPING
            and self.grouping_role is None
        ):
            raise ValueError(f"Node '{self.id}': EMIT_GROUPING requires grouping_role.")

        if self.emit == CurriculumEmitPolicy.EMIT_LEAF and self.leaf_role is None:
            raise ValueError(f"Node '{self.id}': EMIT_LEAF requires leaf_role.")

        if self.emit == CurriculumEmitPolicy.EMIT_GROUPING_AND_LEAF:
            if self.grouping_role is None:
                raise ValueError(
                    f"Node '{self.id}': EMIT_GROUPING_AND_LEAF requires grouping_role."
                )
            if self.leaf_role is None:
                raise ValueError(
                    f"Node '{self.id}': EMIT_GROUPING_AND_LEAF requires leaf_role."
                )

        if self.emit == CurriculumEmitPolicy.EMIT_TABLE_ROWS:
            if self.leaf_role is None:
                raise ValueError(
                    f"Node '{self.id}': EMIT_TABLE_ROWS requires leaf_role."
                )
            if not self.column_mappings:
                raise ValueError(
                    f"Node '{self.id}': EMIT_TABLE_ROWS requires >=1 column_mapping."
                )

        return self

    @model_validator(mode="after")
    def validate_implicit_flag(self) -> CurriculumSkeletonNode:
        """`implicit=True` only valid on CONTAINER_ONLY nodes with grouping_role.

        Returns
        -------
        CurriculumSkeletonNode
            The validated CurriculumSkeletonNode object.

        Raises
        ------
        ValueError
            If implicit=True is set on a node that is not CONTAINER_ONLY or lacks a
            grouping_role.
        """

        if self.implicit:
            if self.emit != CurriculumEmitPolicy.CONTAINER_ONLY:
                raise ValueError(
                    f"Node '{self.id}': implicit=True only valid with CONTAINER_ONLY."
                )
            if self.grouping_role is None:
                raise ValueError(
                    f"Node '{self.id}': implicit=True requires grouping_role."
                )

        return self

    @model_validator(mode="after")
    def validate_table_rows_target_tables(self) -> CurriculumSkeletonNode:
        """EMIT_TABLE_ROWS nodes should use match_target='caption' to match tables
        via their bound caption text.

        Returns
        -------
        CurriculumSkeletonNode
            The validated CurriculumSkeletonNode object.

        Raises
        ------
        ValueError
            If an EMIT_TABLE_ROWS node uses match_target='text' (which only matches
            block segments, not tables).
        """

        if self.emit != CurriculumEmitPolicy.EMIT_TABLE_ROWS:
            return self

        if self.match_target != "caption":
            raise ValueError(
                f"Node '{self.id}': EMIT_TABLE_ROWS should use match_target='caption' "
                f"to match tables via their bound caption text."
            )

        return self


class CurriculumSkeleton(BaseSchema):
    """Root model for a curriculum skeleton file.

    Authored once per curriculum type (e.g., "Senegal Maths CE1-CE2",
    "NCERT Science Grade 6", "KICD English Grade 4").

    The curriculum skeleton encodes the complete expected hierarchy of the curriculum
    document as a tree. The curriculum skeleton matching engine walks document IR
    segments in order and binds each segment to the deepest matching node.
    """

    metadata: CurriculumSkeletonMetadata
    schema_version: str = Field(
        default="1.0", description="Schema version for forward compatibility."
    )
    skeleton_id: str = Field(
        ...,
        description=(
            "Globally unique identifier for this skeleton. "
            "Convention: '{country}-{subject}-{stage}-{language}', "
            "e.g., 'senegal-math-etape2-wolof-fr'."
        ),
    )
    root: CurriculumSkeletonNode = Field(
        ...,
        description="Root of the curriculum tree. Must have grouping_role=FRAMEWORK.",
    )

    @model_validator(mode="after")
    def validate_root_is_framework(self) -> CurriculumSkeleton:
        """Root node must have grouping_role=FRAMEWORK.

        Returns
        -------
        CurriculumSkeleton
            The validated CurriculumSkeleton object.

        Raises
        ------
        ValueError
            If the root node does not have grouping_role=FRAMEWORK.
        """

        if self.root.grouping_role != NodeRole.FRAMEWORK:
            raise ValueError("Root node must have grouping_role=FRAMEWORK.")

        return self

    @model_validator(mode="after")
    def validate_unique_ids(self) -> CurriculumSkeleton:
        """All node IDs within the skeleton must be unique.

        Returns
        -------
        CurriculumSkeleton
            The validated CurriculumSkeleton object.

        Raises
        ------
        ValueError
            If duplicate node IDs are found in the skeleton.
        """

        ids: list[str] = []

        def _collect(node: CurriculumSkeletonNode) -> None:
            """Recursively collect node IDs from the skeleton tree.

            Parameters
            ----------
            node
                The current CurriculumSkeletonNode being processed.
            """

            ids.append(node.id)

            for child in node.children:
                _collect(child)

        _collect(self.root)
        counts = Counter(ids)
        dupes = sorted(k for k, v in counts.items() if v > 1)

        if dupes:
            raise ValueError(f"Duplicate node IDs in skeleton: {dupes}")

        return self


# Schemas for segment decisions.
class GroupingDecision(BaseSchema):
    """A grouping node in the canonical hierarchy.

    Examples: Grade, Subject, Theme, Strand, Topic, Unit, Week, Stage, Section.
    """

    local_code: str | None = Field(
        default=None,
        description="Optional local code associated with the grouping (rare; often used for table codes or document codes).",
    )
    role: NodeRole = Field(
        ...,
        description="Grouping role (NodeRole enum). Must represent a container/group node, not a leaf statement role.",
    )
    source_label: str | None = Field(
        default=None,
        description=(
            "Verbatim label that introduced this grouping (e.g., table column header "
            "'Topic', 'Sub-topic', 'Theme', or a heading label). "
            "Used to preserve framework-native taxonomy (LC export statementType)."
        ),
    )
    title: str = Field(
        ...,
        description="Human-readable title for the grouping node (original text, not translated).",
    )

    @model_validator(mode="after")
    def _validate_grouping(self) -> GroupingDecision:
        """Validate GroupingDecision consistency.

        Returns
        -------
        GroupingDecision
            The validated GroupingDecision object.

        Raises
        ------
        ValueError
            If the GroupingDecision is inconsistent.
        """

        title = (self.title or "").strip()

        if not title:
            raise ValueError("GroupingDecision.title must be non-empty.")

        if self.source_label is not None and not (self.source_label or "").strip():
            raise ValueError(
                "GroupingDecision.source_label must be non-empty when provided."
            )

        if self.role in (NodeRole.FRAMEWORK, NodeRole.UNRESOLVED, NodeRole.PROSE):
            raise ValueError(
                f"GroupingDecision.role must be a real grouping role (not {self.role})."
            )

        return self


class LeafDecision(BaseSchema):
    """An atomic leaf statement. Leaves become CanonicalNodes with StatementRole roles
    (expectation/descriptor/guidance).
    """

    body: str = Field(..., description="Atomic leaf statement body text (original).")
    list_marker: str | None = Field(
        default=None,
        description="Optional list/bullet marker for this leaf (e.g., 'a)', 'i', '1.', 'A.'). Use ONLY for list markers, not for official curriculum codes.",
    )
    local_code: str | None = Field(
        default=None,
        description="Optional local curriculum identifier code for this leaf (e.g., '3.9.4.1'). Use this for stable codes printed in the document.",
    )
    role: StatementRole = Field(
        ...,
        description="Leaf semantic role (StatementRole enum), e.g. EXPECTATION / DESCRIPTOR / GUIDANCE.",
    )
    source_label: str | None = Field(
        default=None,
        description=(
            "Verbatim label that introduced this leaf statement (usually the table column "
            "header like 'Specific Competences', 'Expected Standard', 'Learning Activities', "
            "or a heading label like 'Learning Outcomes'). "
            "Used to preserve framework-native taxonomy (LC export statementType)."
        ),
    )

    @model_validator(mode="after")
    def _validate_leaf(self) -> LeafDecision:
        """Validate LeafDecision consistency.

        Returns
        -------
        LeafDecision
            The validated LeafDecision object.

        Raises
        ------
        ValueError
            If the LeafDecision is inconsistent.
        """

        body = (self.body or "").strip()

        if not body:
            raise ValueError("LeafDecision.body must be non-empty.")

        if self.source_label is not None and not (self.source_label or "").strip():
            raise ValueError(
                "LeafDecision.source_label must be non-empty when provided."
            )

        return self


class RowDecision(BaseSchema):
    """Canonical interpretation for a single table row.

    Each row may emit:

    1. Additional grouping nodes (e.g., subject/strand values repeated by row).
    2. One or more leaf decisions (expectations/descriptors/guidance).
    """

    col_index: int | None = Field(
        default=None,
        description=(
            "Optional 0-based column index into the ORIGINAL stitched table columns that this RowDecision applies to. "
            "Use this when a single table row contains multiple independent statements by column (e.g., one strand per column). "
            "When provided, row-local groupings may be grounded against header_rows_canonical[*][col_index] and leaves must come from that column's cell."
        ),
        ge=0,
    )
    groupings: list[GroupingDecision] = Field(
        default_factory=list,
        description="Grouping nodes derived specifically from this row (e.g., row subject/strand/topic columns).",
    )
    leaves: list[LeafDecision] = Field(
        default_factory=list,
        description="Leaf statements derived from this row (e.g., specific competence statements).",
    )
    row_index: int = Field(
        ...,
        description="0-based ABSOLUTE row index into the ORIGINAL stitched DocumentIR table rows.",
        ge=0,
    )

    @model_validator(mode="after")
    def _validate_non_empty(self) -> Self:
        """RowDecision must emit something useful. Prevents empty stubs like:
        RowDecision(row_index=12, groupings=[], leaves=[]).

        Returns
        -------
        Self
            The validated RowDecision object.

        Raises
        ------
        ValueError
            If both groupings[] and leaves[] are empty.
        """

        if not self.groupings and not self.leaves:
            raise ValueError(
                "RowDecision must include at least one grouping or one leaf "
                "(groupings[] and leaves[] cannot both be empty)."
            )

        return self


class SegmentDecision(BaseSchema):
    """Canonicalization decision for one DocumentIR segment.

    A segment may be:

    1. A block segment (paragraph/list/heading/caption/etc.).
    2. A table segment (optionally chunked via row ranges).

    The Step 4 compiler treats SegmentDecision as an auditable semantic plan and
    compiles it deterministically into canonical nodes/edges.
    """

    block_type: BlockType | None = Field(
        default=None,
        description="If segment_kind='block', this is the block subtype (e.g., paragraph/list/heading/caption/footnote/figure).",
    )
    caption_kind: CaptionKind | None = Field(
        default=None,
        description="If segment_kind='table' and a caption was bound to this table, the caption kind (table/figure/unknown). Audit-only.",
    )
    caption_text: str | None = Field(
        default=None,
        description="If segment_kind='table' and a caption was bound to this table, the caption text. Audit-only.",
    )
    caption_segment_id: str | None = Field(
        default=None,
        description="Segment ID of the bound caption block (if any). Audit-only.",
    )
    caption_page_index: int | None = Field(
        default=None,
        description="Page index of the bound caption block (if any). Audit-only.",
        ge=0,
    )
    caption_gap_segments: int | None = Field(
        default=None,
        description="Number of segments between caption and table when bound. Audit-only.",
        ge=0,
    )
    columns_signature: str | None = Field(
        default=None,
        description="The columns signature for table segments (taken from the document IR). This only applies to table segment kinds.",
    )
    confidence: float = Field(
        ...,
        description="Confidence for this decision in [0,1]. Skeleton-generated decisions use 1.0. Used for QA and human review, not determinism.",
        ge=0.0,
        le=1.0,
    )
    context_groupings: list[GroupingDecision] = Field(
        default_factory=list,
        description="Optional typed interpretation of section_path into grouping decisions (very useful for context anchoring).",
    )
    created_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when this decision was produced. Audit-only; excluded from decision_set_id determinism.",
    )
    decision_id: Optional[str] = Field(
        None,
        description="Stable ID for this decision. This should be populated by the Python pipeline; it may be null during segment decision.",
    )
    decision_type: SegmentDecisionType = Field(
        ...,
        description="High-level action category describing what the compiler should emit from this decision.",
    )
    groupings: list[GroupingDecision] = Field(
        default_factory=list,
        description="Table-level or block-level grouping nodes emitted by this decision (not tied to a specific table row).",
    )
    leaves: list[LeafDecision] = Field(
        default_factory=list,
        description="Block-level or table-level leaf decisions (used when rows[] are not supplied).",
    )
    rationale: str = Field(
        ...,
        description="Brief explanation of why this decision and interpretation were chosen.",
    )
    row_range_end: int | None = Field(
        default=None,
        description="Optional end row index (EXCLUSIVE) for chunked tables.",
    )
    row_range_start: int | None = Field(
        default=None, description="Optional start row index for chunked tables."
    )
    rows: list[RowDecision] = Field(
        default_factory=list,
        description="For table segments: per-row canonical decisions (preferred for real table parsing).",
    )
    segment_id: Optional[str] = Field(
        None,
        description="DocumentIR segment_id that this decision applies to. This should be populated by the Python pipeline; it may be null during segment decision.",
    )
    segment_kind: Optional[Literal["block", "table"]] = Field(
        None,
        description="High-level segment kind from DocumentIR. This should be populated by the Python pipeline; it may be null during segment decision.",
    )

    @model_validator(mode="after")
    def _validate_decision_semantics(self) -> SegmentDecision:
        """Single entry point for all SegmentDecision semantic validation.

        This single validator calls private helpers in an **explicitly documented**
        order:

        1. `_check_ignore_unresolved_empty`: IGNORE/UNRESOLVED must have all arrays
            empty. Runs first so later checks can safely assume the decision intends to
            emit something.
        2. `_check_emit_flagged_unresolved`: emit_flagged_unresolved must carry
            reviewable candidates (context_groupings alone is not sufficient).
        3. `_check_decision_type_shape`: Per-decision-type checks
            (emit_groupings_only/emit_leaves_only/emit_groupings_and_leaves).
            context_groupings do **NOT** count as emitted output here—only
            `groupings[]`, `rows[].groupings[]`, `leaves[]`, and `rows[].leaves[]`
            count. **Must run before** `_check_non_noop_emit` so that a decision with
            only context_groupings is rejected here rather than passing the weaker
            non-noop check.
        4. `_check_non_noop_emit`: Any emit_* decision must have *some* output.
            context_groupings DO count here (a decision with only context_groupings is
            not truly empty). Weaker than step 3 intentionally.
        5. `_check_table_rows_vs_leaves`: rows[] and top-level leaves[] are mutually
            exclusive.
        6. `_check_segment_kind_specifics`: Block- and table-specific leaf requirements.

        Returns
        -------
        SegmentDecision
            The validated SegmentDecision object.

        Raises
        ------
        ValueError
            If any semantic invariant is violated.
        """

        # 1.
        self._check_ignore_unresolved_empty()

        # 2.
        self._check_emit_flagged_unresolved()

        # Early return: remaining checks only apply to proper emit_* types.
        if self.decision_type in (
            SegmentDecisionType.IGNORE,
            SegmentDecisionType.UNRESOLVED,
        ):
            return self

        # 3.
        self._check_decision_type_shape()

        # 4.
        self._check_non_noop_emit()

        # 5.
        self._check_table_rows_vs_leaves()

        # 6.
        self._check_segment_kind_specifics()

        return self

    def _check_ignore_unresolved_empty(self) -> None:
        """IGNORE/UNRESOLVED must have all output arrays empty.

        Raises
        ------
        ValueError
            If the decision type is IGNORE or UNRESOLVED but
            context_groupings/groupings/leaves/rows are not all empty.
        """

        if self.decision_type in (
            SegmentDecisionType.IGNORE,
            SegmentDecisionType.UNRESOLVED,
        ) and (self.context_groupings or self.groupings or self.leaves or self.rows):
            raise ValueError(
                f"Decision type is '{self.decision_type.value}', so "
                f"context_groupings/groupings/leaves/rows must be empty."
            )

    def _check_emit_flagged_unresolved(self) -> None:
        """emit_flagged_unresolved must carry reviewable candidates.

        Raises
        ------
        ValueError
            If the decision type is emit_flagged_unresolved but there are no candidates
            in context_groupings/groupings/leaves/rows (context_groupings alone is not
            sufficient).
        """

        if self.decision_type == SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED and not (
            self.groupings or self.leaves or self.rows
        ):
            raise ValueError(
                "Decision type is 'emit_flagged_unresolved', so at least one of "
                "groupings/leaves/rows must be non-empty (context_groupings alone is not sufficient)."
            )

    def _check_decision_type_shape(self) -> None:
        """Per-decision-type checks. context_groupings do NOT count as output.

        This MUST run before `_check_non_noop_emit` (which counts context_groupings) so
        that `emit_groupings_only` with only context_groupings is rejected here.

        Raises
        ------
        ValueError
            If the decision's content does not match the requirements of its declared
            decision_type.
        """

        # Aggregate signals across segment-level and row-level outputs.
        has_segment_groupings = bool(self.groupings)
        has_row_groupings = any(bool(r.groupings) for r in self.rows)
        has_any_groupings = has_segment_groupings or has_row_groupings

        has_segment_leaves = bool(self.leaves)
        has_row_leaves = any(bool(r.leaves) for r in self.rows)
        has_any_leaves = has_segment_leaves or has_row_leaves

        # emit_groupings_only must actually emit at least one grouping.
        # context_groupings do NOT count (they are context snapshots).
        if (
            self.decision_type == SegmentDecisionType.EMIT_GROUPINGS_ONLY
            and not has_any_groupings
        ):
            raise ValueError(
                "Decision type 'emit_groupings_only' must include at least one grouping "
                "in groupings[] or RowDecision.groupings[] (context_groupings alone is not sufficient)."
            )

        # emit_leaves_only must actually emit at least one leaf.
        if (
            self.decision_type == SegmentDecisionType.EMIT_LEAVES_ONLY
            and not has_any_leaves
        ):
            raise ValueError(
                "Decision type 'emit_leaves_only' must include at least one leaf "
                "in leaves[] or RowDecision.leaves[]."
            )

        # emit_groupings_only must not emit any leaves.
        if (
            self.decision_type == SegmentDecisionType.EMIT_GROUPINGS_ONLY
            and has_any_leaves
        ):
            raise ValueError(
                "Decision type 'emit_groupings_only' must not emit any leaves "
                "(top-level leaves[] and RowDecision.leaves[] must be empty)."
            )

        # emit_leaves_only: ban segment-level groupings; row-level allowed.
        if (
            self.decision_type == SegmentDecisionType.EMIT_LEAVES_ONLY
            and self.groupings
        ):
            raise ValueError(
                "Decision type 'emit_leaves_only' must have empty segment-level groupings[]. "
                "Use RowDecision.groupings[] for row-local containers if needed."
            )

        # Some upstream policies (e.g., spine correction) may legitimately drop either
        # groupings or leaves. Rather than failing validation (and demoting to
        # UNRESOLVED), coerce to the most specific compatible emit type.
        if self.decision_type == SegmentDecisionType.EMIT_GROUPINGS_AND_LEAVES:
            if has_any_groupings and not has_any_leaves:
                self.decision_type = SegmentDecisionType.EMIT_GROUPINGS_ONLY
            elif has_any_leaves and not has_any_groupings:
                self.decision_type = SegmentDecisionType.EMIT_LEAVES_ONLY
            elif not has_any_groupings and not has_any_leaves:
                raise ValueError(
                    "Decision type 'emit_groupings_and_leaves' produced no groupings or leaves."
                )

    def _check_non_noop_emit(self) -> None:
        """Any emit_* decision must produce *some* output.

        context_groupings DO count here—a decision with only context_groupings is not
        truly empty. This is intentionally weaker than `_check_decision_type_shape` and
        relies on running after it.

        Raises
        ------
        ValueError
            If the decision_type is an emit_* type but there is no output in any of
            context_groupings/groupings/leaves/rows.
        """

        has_any_output = bool(
            self.context_groupings or self.groupings or self.leaves or self.rows
        )
        if not has_any_output:
            raise ValueError(
                f"Decision type '{self.decision_type.value}' emitted no output "
                f"(context_groupings/groupings/leaves/rows all empty). "
                f"This should usually be IGNORE or UNRESOLVED."
            )

    def _check_table_rows_vs_leaves(self) -> None:
        """rows[] and top-level leaves[] are mutually exclusive.

        Raises
        ------
        ValueError
            If both rows[] and top-level leaves[] are populated in the same decision.
        """

        if self.rows and self.leaves:
            raise ValueError(
                "SegmentDecision includes both rows[] and top-level leaves[]. "
                "Use rows[] only for table parsing to avoid duplication."
            )

    def _check_segment_kind_specifics(self) -> None:
        """Block/table specific requirements for leaf emission.

        Raises
        ------
        ValueError
            If the decision violates block/table-specific invariants regarding leaf
            emission.
        """

        if (
            self.segment_kind == "block"
            and self.decision_type
            in (
                SegmentDecisionType.EMIT_GROUPINGS_AND_LEAVES,
                SegmentDecisionType.EMIT_LEAVES_ONLY,
            )
            and not self.leaves
        ):
            raise ValueError("Block decision emitting leaves must include leaves[].")

        if (
            self.segment_kind == "table"
            and self.decision_type
            in (
                SegmentDecisionType.EMIT_GROUPINGS_AND_LEAVES,
                SegmentDecisionType.EMIT_LEAVES_ONLY,
            )
            and not (self.rows or self.leaves)
        ):
            raise ValueError(
                "Table decision emitting leaves must include either rows[] or leaves[]."
            )


class SegmentDecisionSet(BaseSchema):
    """A persisted, replayable set of segment decisions for canonical IR creation."""

    created_at: datetime | None = Field(
        default=None,
        description="Audit-only timestamp for when the decision set was produced.",
    )
    decision_set_id: str = Field(
        ...,
        description="Stable fingerprint of decisions[] content. Must match compute_decision_set_id().",
    )
    decisions: list[SegmentDecision] = Field(
        default_factory=list,
        description="All per-segment (or table-chunk) decisions used in compilation.",
    )
    doc_key: str = Field(
        ...,
        description="Document key the decisions were produced for (must match DocumentIR.doc_key).",
    )
    generator: str | None = Field(
        default=None,
        description="Optional string describing how these decisions were generated (e.g., skeleton ID, model name).",
    )
    pdf_name: str = Field(
        ..., description="Optional PDF name for audit/provenance convenience."
    )

    @model_validator(mode="after")
    def _validate_set(self) -> SegmentDecisionSet:
        """Validate SegmentDecisionSet consistency.

        Returns
        -------
        SegmentDecisionSet
            The validated SegmentDecisionSet object.

        Raises
        ------
        ValueError
            If any validation invariant is violated.
        """

        # Unique decision_id invariant.
        dupes: list[str] = []
        seen: set[str] = set()

        for d in self.decisions:
            if d.decision_id:
                if d.decision_id in seen:
                    dupes.append(d.decision_id)
                seen.add(d.decision_id)

        if dupes:
            raise ValueError(
                f"Duplicate decision_id(s) found in SegmentDecisionSet: {sorted(set(dupes))}"
            )

        # decision_set_id must match stable fingerprint.
        expected = compute_decision_set_id(decisions=self.decisions)

        if self.decision_set_id != expected:
            raise ValueError(
                "SegmentDecisionSet.decision_set_id mismatch.\n"
                f"  provided: {self.decision_set_id}\n"
                f"  expected: {expected}\n"
                "This usually means the file was edited or regenerated without updating decision_set_id."
            )

        return self


# Schemas for canonical IR.
class CanonicalEdge(BaseSchema):
    """A hierarchy edge in the canonical IR.

    NB: Canonical IR creation should only emit hasChild containment edges (tree mode).
    """

    child_id: str = Field(..., description="CanonicalNode.node_id of the child node.")
    order_index: int = Field(
        ...,
        description="Deterministic sibling order index under parent_id (encounter order).",
    )
    parent_id: str = Field(..., description="CanonicalNode.node_id of the parent node.")
    rel: Literal["hasChild"] = Field(
        default="hasChild",
        description="Relationship type for Step 4. Only 'hasChild' is emitted here.",
    )
    source_decision_ids: list[str] = Field(
        default_factory=list,
        description="Decision IDs whose outputs caused this edge to exist.",
    )
    source_segment_ids: list[str] = Field(
        default_factory=list,
        description="DocumentIR segment IDs that contributed to this edge (provenance pointers).",
    )


class CanonicalNode(BaseSchema):
    """A single semantic node in the canonical curriculum hierarchy.

    Canonical nodes are *flat*; hierarchy is represented only by CanonicalEdge hasChild
    edges. A node may represent either:
        - A grouping container (NodeRole): these are containers and their text is
            treated as a label.
        - A statement leaf (StatementRole): these are content and their text is treated
            as normative learning content.
    """

    bbox: Optional[BBox] = Field(
        default=None,
        description="Optional bbox for this node. For leaf nodes this may come from row bbox; for groupings may be union or None.",
    )
    body: TextUnit | None = Field(
        default=None,
        description="Full body text for statement nodes (EXPECTATION/DESCRIPTOR/GUIDANCE).",
    )
    list_marker: str | None = Field(
        default=None,
        description="Optional list marker (e.g., 'a)', 'i', '•'). Use this ONLY for list/bullet markers, not for official curriculum codes.",
    )
    local_code: str | None = Field(
        default=None,
        description="Optional local code (e.g., 'Table 4') relevant to this node.",
    )
    node_id: str = Field(
        ..., description="Deterministic globally stable UUID for this node."
    )
    normalized_text: str | None = Field(
        default=None,
        description="Optional normalized text snapshot for debugging/matching. Node IDs should remain deterministic even if this is omitted.",
    )
    page_indices: list[int] = Field(
        default_factory=list,
        description="Page indices from which this node was derived.",
    )
    role: NodeRole | StatementRole = Field(
        ...,
        description="Node role union: NodeRole for groupings, StatementRole for leaf statements.",
    )
    section_path_text: list[str] = Field(
        default_factory=list,
        description="Heading context snapshot from Step 3 used for provenance and debugging.",
    )
    source_decision_ids: list[str] = Field(
        default_factory=list,
        description="Decision IDs that produced this canonical node.",
    )
    source_label: str | None = Field(
        default=None,
        description=(
            "Verbatim framework-native label that introduced this node "
            "(e.g., 'Specific Competences', 'Expected Standard', 'Topic'). "
            "Used for LC KG statementType export."
        ),
    )
    source_segment_ids: list[str] = Field(
        default_factory=list,
        description="DocumentIR segment IDs that contributed to this node (provenance pointers).",
    )
    source_type: Literal["block", "table"] | None = Field(
        default=None,
        description="Origin type used for node creation (heading-derived, table-derived, or block-derived). Row-level provenance lives elsewhere.",
    )
    title: TextUnit | None = Field(
        default=None,
        description="Short title text for grouping nodes (or heading-derived nodes).",
    )

    @model_validator(mode="after")
    def _validate_title_body_by_role(self) -> Self:
        """Enforce role/text consistency:

        1. NodeRole (grouping nodes): require title, forbid body
        2. StatementRole (statement leaves): require body, forbid title

        Returns
        -------
        Self
            The validated CanonicalNode object.

        Raises
        ------
        ValueError
            If the CanonicalNode is inconsistent based on its role.
        """

        if isinstance(self.role, NodeRole):
            if self.title is None or not (self.title.text or "").strip():
                raise ValueError(
                    f"CanonicalNode role '{self.role.value}' is a grouping (NodeRole) "
                    "so it must have a non-empty title."
                )
            if self.body is not None:
                raise ValueError(
                    f"CanonicalNode role '{self.role.value}' is a grouping (NodeRole) "
                    "so it must not have a body."
                )
        else:
            if self.body is None or not (self.body.text or "").strip():
                raise ValueError(
                    f"CanonicalNode role '{self.role.value}' is a statement (StatementRole) "
                    "so it must have a non-empty body."
                )
            if self.title is not None:
                raise ValueError(
                    f"CanonicalNode role '{self.role.value}' is a statement (StatementRole) "
                    "so it must not have a title."
                )

        return self


class CanonicalIR(BaseSchema):
    """Represents the canonical, semantic, provenance-rich representation of a document.

    CanonicalIR is produced deterministically from:

    1. The stitched DocumentIR.
    2. A SegmentDecisionSet (from skeleton matching or other generators).
    """

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when this CanonicalIR was compiled.",
    )
    decision_set_id: str = Field(
        ...,
        description="Stable fingerprint/ID of the SegmentDecision set used to compile this CanonicalIR. Computed from a normalized stable subset of fields (excludes confidence, rationale, created_at).",
    )
    doc_key: str = Field(
        ..., description="Deterministic document key (e.g., sha256 of PDF bytes)."
    )
    edges: list[CanonicalEdge] = Field(
        default_factory=list,
        description="Canonical hasChild edges (tree containment) with deterministic sibling ordering.",
    )
    nodes: list[CanonicalNode] = Field(
        default_factory=list,
        description="Flat list of canonical nodes (groupings + leaves).",
    )
    pdf_name: str = Field(
        ..., description="Optional original PDF filename for provenance."
    )
    root_id: str = Field(
        ..., description="Canonical node_id of the framework root node."
    )
    segment_decisions: list[SegmentDecision] = Field(
        default_factory=list,
        description="Audit trail: per-segment decisions used to compile this CanonicalIR.",
    )
    unresolved: list[UnresolvedItem] = Field(
        default_factory=list,
        description="Items that could not be confidently resolved into canonical nodes/leaves.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Human-readable warnings produced during compilation (conflicts, drops, anomalies).",
    )
