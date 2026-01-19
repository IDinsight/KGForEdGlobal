"""This module contains schemas used for creating the canonical Intermediate
Representation (IR) from a single document IR.
"""

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import json

from datetime import datetime, timezone
from typing import Literal, Optional, Self

# Third Party Library
from pydantic import Field, model_validator

# Package Library
from skg.page_ir_extraction.schemas import TextUnit
from skg.schemas import BaseSchema, BBox
from skg.utils.constants import (
    BlockType,
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
    """

    stable = []

    for d in sorted(decisions, key=lambda x: x.decision_id or ""):
        assert isinstance(d.decision_id, str) and d.decision_id
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


# Schemas for decisions produced by the LLM and used for deterministic parsing.
class GroupingDecision(BaseSchema):
    """A grouping node the LLM believes should exist in the canonical hierarchy.

    Examples: Grade, Subject, Theme, Strand, Topic, Unit, Week, Stage, Section.
    """

    list_id: str | None = Field(
        default=None,
        description="Optional identifier code attached to the grouping (e.g., '3.1' or 'Theme 2').",
    )
    local_code: str | None = Field(
        default=None,
        description="Optional local code associated with the grouping (rare; often used for table codes or document codes).",
    )
    role: NodeRole = Field(
        ...,
        description="Grouping role (NodeRole enum). Must represent a container/group node, not a leaf statement role.",
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

        if self.role in (NodeRole.FRAMEWORK, NodeRole.UNRESOLVED):
            raise ValueError(
                f"GroupingDecision.role must be a real grouping role (not {self.role})."
            )

        return self


class LeafDecision(BaseSchema):
    """An atomic leaf statement extracted by the LLM. Leaves become CanonicalNodes with
    StatementRole roles (expectation/descriptor/guidance).
    """

    body: str = Field(..., description="Atomic leaf statement body text (original).")
    list_id: str | None = Field(
        default=None,
        description="Optional identifier code extracted for this leaf (e.g., '3.9.4.1', 'a)', '1.2').",
    )
    role: StatementRole = Field(
        ...,
        description="Leaf semantic role (StatementRole enum), e.g. EXPECTATION / DESCRIPTOR / GUIDANCE.",
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

        return self


class RowDecision(BaseSchema):
    """Canonical interpretation for a single table row.

    Each row may emit:

    1. Additional grouping nodes (e.g., subject/strand values repeated by row).
    2. One or more leaf decisions (expectations/descriptors/guidance).
    """

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


class SegmentDecision(BaseSchema):
    """LLM-produced canonicalization decision for one DocumentIR segment.

    A segment may be:

    1. A block segment (paragraph/list/heading/caption/etc.).
    2. A table segment (optionally chunked via row ranges).

    The Step 4 compiler should treat SegmentDecision as an auditable semantic plan and
    compile it deterministically into canonical nodes/edges.
    """

    block_type: BlockType | None = Field(
        default=None,
        description="If segment_kind='block', this is the block subtype (e.g., paragraph/list/heading/caption/footnote/figure).",
    )
    confidence: float = Field(
        ...,
        description="LLM confidence for this decision in [0,1]. Used for QA and human review, not determinism.",
        ge=0.0,
        le=1.0,
    )
    context_groupings: list[GroupingDecision] = Field(
        default_factory=list,
        description="Optional typed interpretation of Step-3 section_path into grouping decisions (very useful for context anchoring).",
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
        description="Brief concrete explanation of why the LLM chose this decision and interpretation.",
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
    def _validate_non_noop_emit_decision(self) -> SegmentDecision:
        """If decision_type indicates emission, ensure something will actually be
        emitted. This prevents 'emit_*' decisions that are effectively empty.

        Returns
        -------
        SegmentDecision
            The validated SegmentDecision object.

        Raises
        ------
        ValueError
            If an emit decision_type has no output
            (context_groupings/groupings/leaves/rows all empty).
        """

        if self.decision_type in (
            SegmentDecisionType.IGNORE,
            SegmentDecisionType.UNRESOLVED,
        ):
            return self

        has_any_output = bool(
            self.context_groupings or self.groupings or self.leaves or self.rows
        )
        if not has_any_output:
            raise ValueError(
                f"Decision type '{self.decision_type.value}' emitted no output "
                f"(context_groupings/groupings/leaves/rows all empty). "
                f"This should usually be IGNORE or UNRESOLVED."
            )

        return self

    @model_validator(mode="after")
    def _validate_table_rows_vs_leaves(self) -> SegmentDecision:
        """Prevent double counting: if using rows[] for a table, top-level leaves[]
        must be empty.

        Returns
        -------
        SegmentDecision
            The validated SegmentDecision object.

        Raises
        ------
        ValueError
            If both rows[] and top-level leaves[] are populated for a table segment.
        """

        if self.decision_type in (
            SegmentDecisionType.IGNORE,
            SegmentDecisionType.UNRESOLVED,
        ):
            return self

        if self.rows and self.leaves:
            raise ValueError(
                "SegmentDecision includes both rows[] and top-level leaves[]. "
                "Use rows[] only for table parsing to avoid duplication."
            )

        return self

    @model_validator(mode="after")
    def _validate(self) -> SegmentDecision:
        """Validate SegmentDecision consistency based on decision_type and segment_kind.

        Returns
        -------
        SegmentDecision
            The validated SegmentDecision object.

        Raises
        ------
        ValueError
            If the SegmentDecision is inconsistent based on its decision_type and
            segment_kind.
        """

        # If IGNORE, we should not be emitting anything.
        if self.decision_type in (
            SegmentDecisionType.IGNORE,
            SegmentDecisionType.UNRESOLVED,
        ) and (self.context_groupings or self.groupings or self.leaves or self.rows):
            raise ValueError(
                f"Decision type is '{self.decision_type.value}', so "
                "context_groupings/groupings/leaves/rows must be empty."
            )

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

        return self


class SegmentDecisionSet(BaseSchema):
    """A persisted, replayable set of LLM decisions for canonical IR creation. This
    object is the ONLY non-deterministic input to the CanonicalIR compiler.
    """

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
        description="Optional string describing how these decisions were generated (model name/version, etc).",
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
    list_id: Optional[str] = Field(
        default=None,
        description="Optional extracted identifier code for this node (e.g., '3.1.1').",
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
    2. The list of SegmentDecision objects (LLM output).
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
        description="Audit trail: per-segment LLM decisions used to compile this CanonicalIR.",
    )
    unresolved: list[UnresolvedItem] = Field(
        default_factory=list,
        description="Items that could not be confidently resolved into canonical nodes/leaves.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Human-readable warnings produced during compilation (conflicts, drops, anomalies).",
    )
