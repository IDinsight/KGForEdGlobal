"""This module contains schemas used for stitching individual (verified) PageIRs into a
single DocumentIR.
"""

# Future Library
from __future__ import annotations

# Standard Library
from typing import Any, Literal, Optional

# Third Party Library
from pydantic import Field

# Package Library
from skg.page_ir_extraction.schemas import ListItem, TableRow, TextUnit
from skg.schemas import BaseSchema
from skg.utils.constants import BlockType, ItemBoundary


# Schemas for page slices.
class BlockSlice(BaseSchema):
    """A single page-slice of a (potentially multi-page) block segment."""

    bbox: list[float]
    block_type: BlockType
    boundary: ItemBoundary
    figure: Optional[dict[str, Any]] = None
    item_index: int
    list_items: Optional[list[ListItem]] = None
    local_code: Optional[str] = None
    page_index: int
    text: Optional[TextUnit] = None


class TableSlice(BaseSchema):
    """A single page-slice of a (potentially multi-page) table segment."""

    bbox: list[float]
    boundary: ItemBoundary
    dropped_header_rows: int = Field(
        0,
        description="Number of header rows dropped from this slice during stitching (0 for first slice). This is based on repeats_header flag or canonical header matching.",
    )
    header_row_count: int
    item_index: int
    local_code: Optional[str] = None
    page_index: int
    repeats_header: Optional[bool] = None
    rows: list[TableRow] = Field(
        ...,
        description="Rows exactly as extracted on this page slice (includes header rows).",
    )


# Schemas for stitched segments.
class SectionHeadingRef(BaseSchema):
    """Semantic pointer to a prior heading that provides structural context for
    downstream semantic canonicalization. This schema exists to give every stitched
    DocumentIR segment a lightweight "where am I in the document right now?" context,
    without doing any real semantics yet. When the stitching step hits a heading/local
    code, we push this object onto a stack that contains human-readable text and is
    traceable back to the source (i.e., page and item index).

    Why Do We Need This
    -------------------

    When the canonical IR pipeline tries to build the CanonicalIR object
    (e.g., grade → subject → topic → ...), it often needs extra context to interpret a
    table or block, because the table itself might be ambiguous. For example: the table
    just has competences, but doesn’t say the subject in the table cells. The subject
    is in a heading above it. So Step 3 attaches something like:

    section_path = ["Mathematics", "Number", "Addition"]

    to the table segment, even if the table doesn’t repeat that text inside it. That
    way, the canonical IR pipeline can deterministically infer structure using the
    table content and the heading context without re-scanning backward across pages.

    In other words, when we stitch a segment, we snapshot:

    segment.section_path = copy(section_path_stack)

    So every segment knows “the headings that were active when I started.”
    """

    item_index: int = Field(
        ..., description="0-based index of the heading item inside PageIR.items."
    )
    page_index: int = Field(..., description="0-based page index of the heading block.")
    text: str = Field(..., description="Heading text as extracted (no translation).")


class SegmentProvenance(BaseSchema):
    """Provenance pointer to the original PageIR item."""

    bbox: list[float] = Field(
        ..., description="BBox [x0,y0,x1,y1] in px, copied from the source item."
    )
    boundary: ItemBoundary = Field(
        ..., description="Original boundary flag on this page slice."
    )
    item_addr: str = Field(..., description="Address of the original PageIR item.")
    item_index: int = Field(
        ..., description="0-based index of the item inside PageIR.items."
    )
    kind: Literal["block", "table"] = Field(..., description="Original item kind.")
    local_code: Optional[str] = Field(
        None, description="Item local code (e.g., 'Table 4', '2.5.1')."
    )
    page_index: int = Field(..., description="0-based page index in the PDF.")
    repeats_header: Optional[bool] = Field(
        None,
        description="For table slices: whether header rows are repeated on this page slice (null if unknown).",
    )


class BlockSegment(BaseSchema):
    """A stitched block segment (paragraph/list/caption/heading/figure, etc.)."""

    block_type: BlockType

    # We keep both the structured field(s) and a convenience "combined_text". For
    # lists, the canonical representation is list_items; combined_text is optional.
    combined_text: Optional[str] = Field(
        None,
        description="Concatenated text across page slices when continuation occurs.",
    )

    figure: Optional[dict[str, Any]] = None
    kind: Literal["block"] = "block"
    list_items: Optional[list[ListItem]] = None
    local_code: Optional[str] = None
    section_path: list[SectionHeadingRef] = Field(
        default_factory=list,
        description="Heading context at the moment this segment starts. Semantic-light; used for downstream canonicalization.",
    )
    segment_id: str = Field(
        ...,
        description="Deterministic UUIDv5 for this segment (doc_key + first slice pointer).",
    )
    segment_provenance: list[SegmentProvenance] = Field(
        default_factory=list,
        description="All source PageIR items that were stitched into this segment.",
    )
    slices: list[BlockSlice] = Field(
        default_factory=list, description="Per-page slices in order."
    )
    text: Optional[TextUnit] = None


class TableSegment(BaseSchema):
    """A stitched table segment merged across pages."""

    columns_signature: Optional[str] = Field(
        default=None,
        description="Normalized string signature derived from `header_rows_canonical`, useful for matching and downstream canonicalization.",
    )
    header_row_count: int
    header_rows: list[TableRow] = Field(
        default_factory=list, description="Header rows (first header_row_count rows)."
    )
    header_rows_canonical: list[list[str]] = Field(
        default_factory=list,
        description="Canonicalized header rows as normalized strings per cell, derived from `header_rows`. Shape: [[cell0, cell1, ...], ...].",
    )
    kind: Literal["table"] = "table"
    local_code: Optional[str] = None
    n_cols: int = Field(..., description="Max number of columns across stitched rows.")
    rows: list[TableRow] = Field(
        ..., description="Stitched visual rows (header rows included once)."
    )
    rows_filldown: Optional[list[TableRow]] = Field(
        default=None,
        description=(
            "Convenience structural normalization of `rows` where empty cells in the "
            "first N group columns are filled down from the most recent non-empty "
            "value above (header rows are not filled). `rows` remains the raw stitched "
            "visual output."
        ),
    )
    section_path: list[SectionHeadingRef] = Field(
        default_factory=list,
        description="Heading context at the moment this segment starts. Semantic-light; used for downstream canonicalization.",
    )
    segment_id: str = Field(
        ...,
        description="Deterministic UUIDv5 for this segment (doc_key + first slice pointer).",
    )
    segment_provenance: list[SegmentProvenance] = Field(
        default_factory=list,
        description="All source PageIR table slices merged into this segment.",
    )
    slices: list[TableSlice] = Field(
        default_factory=list, description="Per-page slices in order."
    )


Segment = BlockSegment | TableSegment


# Schemas for stitching.
class DocumentIR(BaseSchema):
    """Document-level IR after stitching."""

    coord_space: str
    doc_key: str = Field(
        ...,
        description="Deterministic hash key of the source PDF bytes (e.g., SHA-256 hex).",
    )
    dpi: int
    image_height: int
    image_width: int
    page_count: int = Field(..., description="Total number of pages stitched.")
    pdf_name: Optional[str] = Field(None, description="Source PDF filename (no path).")
    segments: list[Segment] = Field(
        ..., description="Ordered stitched segments across the whole document."
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Any non-fatal issues detected during stitching.",
    )
