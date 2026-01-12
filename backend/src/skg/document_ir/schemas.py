"""This module contains schemas used for stitching individual (verified) PageIRs into a
single DocumentIR.
"""

# Future Library
from __future__ import annotations

# Standard Library
from typing import Any, Literal, Optional, Union

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field

# Package Library
from skg.page_ir_extraction.schemas import ListItem, TableRow, TextUnit
from skg.utils.constants import BlockType, ItemBoundary


# Schemas for primitives.
class BaseModelDocumentIR(BaseModel):
    """Base model that enforces 'additionalProperties: false' in JSON schema for
    compatibility with OpenAI Structured Outputs.
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)


# Schemas for page slices.
class BlockSlice(BaseModelDocumentIR):
    """A single page-slice of a (potentially multi-page) block segment."""

    bbox: list[float]
    block_type: BlockType
    boundary: ItemBoundary
    figure: Optional[dict[str, Any]] = None
    page_index: int
    item_index: int
    list_items: Optional[list[ListItem]] = None
    local_code: Optional[str] = None
    text: Optional[TextUnit] = None


class TableSlice(BaseModelDocumentIR):
    """A single page-slice of a (potentially multi-page) table segment."""

    bbox: list[float]
    boundary: ItemBoundary
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
class SegmentProvenance(BaseModelDocumentIR):
    """Provenance pointer to the original PageIR item."""

    bbox: list[float] = Field(
        ..., description="BBox [x0,y0,x1,y1] in px, copied from the source item."
    )
    boundary: ItemBoundary = Field(
        ..., description="Original boundary flag on this page slice."
    )
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


class BlockSegment(BaseModelDocumentIR):
    """A stitched block segment (paragraph/list/caption/heading/figure, etc.)."""

    block_type: BlockType
    kind: Literal["block"] = "block"
    local_code: Optional[str] = None
    provenance: list[SegmentProvenance] = Field(
        default_factory=list,
        description="All source PageIR items that were stitched into this segment.",
    )
    segment_key: str = Field(..., description="Deterministic segment key.")
    slices: list[BlockSlice] = Field(
        default_factory=list, description="Per-page slices in order."
    )

    # We keep both the structured field(s) and a convenience "combined_text". For
    # lists, the canonical representation is list_items; combined_text is optional.
    combined_text: Optional[str] = Field(
        None,
        description="Concatenated text across page slices when continuation occurs.",
    )
    figure: Optional[dict[str, Any]] = None
    list_items: Optional[list[ListItem]] = None
    text: Optional[TextUnit] = None


class TableSegment(BaseModelDocumentIR):
    """A stitched table segment merged across pages."""

    header_row_count: int

    # Keep an extracted 'header_rows' copy for convenience.
    header_rows: list[TableRow] = Field(
        default_factory=list, description="Header rows (first header_row_count rows)."
    )

    kind: Literal["table"] = "table"
    local_code: Optional[str] = None
    n_cols: int = Field(..., description="Max number of columns across stitched rows.")
    provenance: list[SegmentProvenance] = Field(
        default_factory=list,
        description="All source PageIR table slices merged into this segment.",
    )

    # The stitched rows include the header rows once (from the first slice), followed
    # by body rows.
    rows: list[TableRow] = Field(
        ..., description="Stitched visual rows (header rows included once)."
    )

    segment_key: str = Field(..., description="Deterministic segment key.")
    slices: list[TableSlice] = Field(
        default_factory=list, description="Per-page slices in order."
    )


Segment = Union[BlockSegment, TableSegment]


# Schemas for stitching.
class DocumentIR(BaseModelDocumentIR):
    """Document-level IR after stitching (steps 1 & 2)."""

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
