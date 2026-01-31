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
    CONTEXT_GROUPINGS_ROLE_ORDER,
    BlockType,
    CaptionKind,
    GroupingCanonicalizationAction,
    NodeRole,
    SegmentDecisionType,
    StatementRole,
    UnresolvedReason,
)

ROLE_PRECEDENCE = {role: i for i, role in enumerate(CONTEXT_GROUPINGS_ROLE_ORDER)}


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


# Schemas for decisions produced by the LLM and used for deterministic parsing.
class GroupingDecision(BaseSchema):
    """A grouping node the LLM believes should exist in the canonical hierarchy.

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
    confidence: float = Field(
        ...,
        description="LLM confidence for this decision in [0,1]. Used for QA and human review, not determinism.",
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
    def _validate_decision_type_semantics(self) -> SegmentDecision:
        """Enforce that decision_type matches the shape of emitted outputs.

        Updated rules (supports real table parsing):

        1. emit_groupings_only:
            - MUST NOT emit any leaves anywhere (top-level or row-level)
            - MAY emit groupings[] and/or rows[] with row-level groupings
        2. emit_leaves_only:
            - MUST have empty *segment-level* groupings[]
            - MAY emit leaves[] and/or rows[] (including row-level groupings for
                attachment)
        3. emit_groupings_and_leaves:
            - MUST emit at least one grouping somewhere (segment or row)
            - MUST emit at least one leaf somewhere (segment or row)
        """

        if self.decision_type in (
            SegmentDecisionType.IGNORE,
            SegmentDecisionType.UNRESOLVED,
        ):
            return self

        # Aggregate signals across segment-level and row-level outputs.
        has_segment_groupings = bool(self.groupings)
        has_row_groupings = any(bool(r.groupings) for r in self.rows)
        has_any_groupings = has_segment_groupings or has_row_groupings

        has_segment_leaves = bool(self.leaves)
        has_row_leaves = any(bool(r.leaves) for r in self.rows)
        has_any_leaves = has_segment_leaves or has_row_leaves

        # emit_groupings_only must actually emit at least one grouping.
        # NB: context_groupings do NOT count here, because they are "context snapshots"
        # and almost always present. We want actual emitted groupings.
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

        # Allow rows[] only if they contain groupings-only rows (no leaves).
        if (
            self.decision_type == SegmentDecisionType.EMIT_GROUPINGS_ONLY
            and has_any_leaves
        ):
            raise ValueError(
                "Decision type 'emit_groupings_only' must not emit any leaves "
                "(top-level leaves[] and RowDecision.leaves[] must be empty)."
            )

        # Only ban segment-level groupings; allow row-level groupings for tables.
        if (
            self.decision_type == SegmentDecisionType.EMIT_LEAVES_ONLY
            and self.groupings
        ):
            raise ValueError(
                "Decision type 'emit_leaves_only' must have empty segment-level groupings[]. "
                "Use RowDecision.groupings[] for row-local containers if needed."
            )

        if self.decision_type == SegmentDecisionType.EMIT_GROUPINGS_AND_LEAVES and (
            not has_any_groupings or not has_any_leaves
        ):
            raise ValueError(
                "Decision type 'emit_groupings_and_leaves' must include BOTH "
                "groupings and leaves (either segment-level or row-level for tables)."
            )

        return self

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

        # "emit_flagged_unresolved" is allowed to carry candidate outputs, but it must
        # carry *something* (otherwise it should be UNRESOLVED).
        if self.decision_type == SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED and not (
            self.context_groupings or self.groupings or self.leaves or self.rows
        ):
            raise ValueError(
                "Decision type is 'emit_flagged_unresolved', so at least one of "
                "context_groupings/groupings/leaves/rows must be non-empty."
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


# Schemas for canonicalization of segment decisions.
class GroupingCanonicalizationKey(BaseSchema):
    """The minimal identity of a grouping candidate that we want to canonicalize.

    NB:

    1. role + title is usually enough.
    2. local_code/source_label are optional but useful to preserve provenance and
        disambiguate weird cases.
    """

    local_code: Optional[str] = Field(
        default=None, description="Optional local code associated with this grouping."
    )
    role: NodeRole = Field(
        ..., description="Grouping node role (GRADE_LEVEL, SUBJECT, THEME, etc.)"
    )
    source_label: Optional[str] = Field(
        default=None,
        description="Optional verbatim label that introduced this grouping (e.g. 'Topic', 'Strand').",
    )
    title: str = Field(
        ...,
        description="Grouping title as emitted by the segment-decision LLM (may be noisy).",
    )

    @model_validator(mode="after")
    def _validate_title_nonempty(self) -> GroupingCanonicalizationKey:
        """Ensure title is non-empty after trimming whitespace.

        Returns
        -------
        GroupingCanonicalizationKey
            The validated GroupingCanonicalizationKey object.

        Raises
        ------
        ValueError
            If the title is empty after trimming.
        """

        t = (self.title or "").strip()

        if not t:
            raise ValueError("GroupingKey.title must be non-empty.")

        self.title = t

        if self.local_code is not None:
            self.local_code = self.local_code.strip() or None

        if self.source_label is not None:
            self.source_label = self.source_label.strip() or None

        return self


class GroupingCanonicalizationItem(BaseSchema):
    """Canonicalization rewrite rule for grouping candidates.

    We just have one rewrite rule: input grouping -> action -> output grouping(s).
    Matching should be done deterministically in Python (exact match on
    role/title/local_code/source_label).
    """

    action: GroupingCanonicalizationAction = Field(
        ..., description="Canonicalization action to apply."
    )
    confidence: float = Field(
        default=1.0,
        description="Confidence in this rewrite. If below threshold, you may choose not to apply automatically.",
        ge=0.0,
        le=1.0,
    )
    input: GroupingCanonicalizationKey = Field(
        ..., description="Original grouping candidate (verbatim-ish from decisions)."
    )
    output: list[GroupingCanonicalizationKey] = Field(
        default_factory=list,
        description="Canonical grouping(s) that replace the input. Empty for KEEP/DROP.",
    )
    rationale: Optional[str] = Field(
        default=None,
        description="Short justification for audit/debugging (not used for determinism).",
    )

    def _ensure_unique_outputs(self) -> None:
        """Ensure no duplicate keys in the output list.

        Raises
        ------
        ValueError
            If duplicate output groupings are detected.
        """

        output_seen = set()

        for o in self.output:
            # Create a hashable tuple key for the object.
            k = (o.role.value, o.title, o.local_code or "", o.source_label or "")

            if k in output_seen:
                raise ValueError(f"Duplicate output grouping in mapping item: {k}")

            output_seen.add(k)

    def _validate_drop(self) -> None:
        """Validate DROP action.

        Raises
        ------
        ValueError
            If the output is not empty for DROP action.
        """

        if self.output:
            raise ValueError("DROP requires output=[]")

    def _validate_keep(self) -> None:
        """Validate KEEP action.

        Raises
        ------
        ValueError
            If the output is not empty or not equal to input for KEEP action.
        """

        if self.output and self.output != [self.input]:
            raise ValueError("KEEP requires output=[] (preferred) or output=[input]")

    def _validate_replace(self) -> None:
        """Validate REPLACE action.

        Raises
        ------
        ValueError
            If the output does not meet REPLACE action requirements.
        """

        if len(self.output) != 1:
            raise ValueError("REPLACE requires exactly one output grouping")

        target = self.output[0]

        if target.role != self.input.role:
            raise ValueError(
                "REPLACE must not change role (use SPLIT if role must change)"
            )

        if target == self.input:
            raise ValueError("REPLACE identical to input; use KEEP instead.")

    def _validate_split(self) -> None:
        """Validate SPLIT action.

        Raises
        ------
        ValueError
            If the output does not meet SPLIT action requirements.
        """

        if len(self.output) < 2:
            raise ValueError("SPLIT requires 2+ output groupings")

        # Validate precedence.
        idxs = [ROLE_PRECEDENCE[o.role] for o in self.output]

        if idxs != sorted(idxs):
            roles = [o.role.value for o in self.output]

            raise ValueError(
                f"SPLIT output roles must follow precedence order: {roles}"
            )

    @model_validator(mode="after")
    def _validate_action_output_contract(self) -> GroupingCanonicalizationItem:
        """Enforce action/output consistency rules.

        Returns
        -------
        GroupingCanonicalizationItem
            The validated GroupingCanonicalizationItem object.

        Raises
        ------
        ValueError
            If the action/output combination is inconsistent.
        """

        self._ensure_unique_outputs()

        validators = {
            GroupingCanonicalizationAction.DROP: self._validate_drop,
            GroupingCanonicalizationAction.KEEP: self._validate_keep,
            GroupingCanonicalizationAction.REPLACE: self._validate_replace,
            GroupingCanonicalizationAction.SPLIT: self._validate_split,
        }
        validator_func = validators.get(self.action)

        if validator_func:
            validator_func()

        return self


class GroupingCanonicalizationMap(BaseSchema):
    """Top-level LLM response: a full mapping for all unique groupings found in a
    decision set.

    This mapping should be applied deterministically to:

    1. SegmentDecision.context_groupings
    2. SegmentDecision.groupings
    3. RowDecision.groupings
    """

    doc_key: Optional[str] = Field(
        default=None,
        description="Deterministic hash key of the source PDF bytes (e.g., SHA-256 hex). This should be populated by the Python pipeline; it may be null.",
    )
    generator: Optional[str] = Field(
        default=None,
        description="Model identifier for audit/debugging. This should be populated by the Python pipeline; it may be null.",
    )
    items: list[GroupingCanonicalizationItem] = Field(
        default_factory=list,
        description="Rewrite rules covering all unique input groupings.",
    )

    @model_validator(mode="after")
    def _validate_no_duplicate_inputs(self) -> GroupingCanonicalizationMap:
        """Ensure no duplicate inputs in the mapping.

        Returns
        -------
        GroupingCanonicalizationMap

        Raises
        ------
        ValueError
            If duplicate mapping inputs are detected.
        """

        seen = set()

        for item in self.items:
            key = (
                item.input.role.value,
                item.input.title,
                item.input.local_code or "",
                item.input.source_label or "",
            )

            if key in seen:
                raise ValueError(f"Duplicate mapping input detected for {key}")

            seen.add(key)

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
