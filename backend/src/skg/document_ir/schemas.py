"""This module contains schemas used for stitching individual (verified) PageIRs into a
single DocumentIR.
"""

# Future Library
from __future__ import annotations

# Standard Library
from typing import Annotated, Any, Literal, Optional, Union

# Third Party Library
from pydantic import Field

# Package Library
from skg.page_ir_extraction.schemas import ListItem, TableRow, TextUnit
from skg.utils.constants import BlockType, ItemBoundary


# Schemas for page slices.
class BlockSlice(BaseSchema):
    """A single page-slice of a (potentially multi-page) block segment."""

    bbox: BBox
    block_type: BlockType = Field(
        ...,
        description="The extracted type of the block (e.g., Paragraph, List, Heading).",
    )
    boundary: ItemBoundary = Field(
        ...,
        description="Indicates structural continuity (e.g., if this block flows from the previous page or continues to the next).",
    )
    figure: Optional[dict[str, Any]] = Field(
        None,
        description="Raw figure metadata if this slice represents an image/chart.",
    )
    item_index: int = Field(
        ...,
        description="The 0-based index of this item in the source PageIR.items list. Used for backtracking provenance.",
    )
    list_items: Optional[list[ListItem]] = Field(
        None,
        description="Structured list items if this slice is a list; mutually exclusive with `text`.",
    )
    local_code: Optional[str] = Field(
        None,
        description="Explicit numbering found on this specific slice (e.g., '2.1.1'). Used to resolve the segment-level local_code.",
    )
    page_index: int = Field(
        ...,
        description="The 0-based page index where this slice is located.",
    )
    text: Optional[TextUnit] = Field(
        None,
        description="The textual content of this slice. If stitched, this may be concatenated with subsequent slices.",
    )


class TableSlice(BaseSchema):
    """A single page-slice of a (potentially multi-page) table segment."""

    bbox: BBox
    boundary: ItemBoundary = Field(
        ...,
        description="Indicates table continuity. If RESUMED or BOTH, stitching logic checks `repeats_header` to align columns.",
    )
    dropped_header_rows: int = Field(
        0,
        description="Number of header rows dropped from this slice during stitching (0 for first slice). This is calculated in `_process_next_table_slice` based on `repeats_header` or canonical header matching.",
    )
    header_row_count: int = Field(
        ...,
        description="The number of rows on *this specific slice* that function as headers. Note: A continuation slice might visually have 0 headers, or N repeated headers.",
    )
    item_index: int = Field(
        ...,
        description="The 0-based index of this item in the source PageIR.items list.",
    )
    local_code: Optional[str] = Field(
        None,
        description="Table identifier (e.g., 'Table 1') found on this page. If missing, the stitcher may backfill it from the segment's resolved code.",
    )
    page_index: int = Field(
        ...,
        description="The 0-based page index where this table slice is located.",
    )
    repeats_header: Optional[bool] = Field(
        None,
        description="True if the extractor detected visual header repetition on this continuation slice. Used to determine `dropped_header_rows`.",
    )
    rows: list[TableRow] = Field(
        ...,
        description="The raw rows extracted from this page (including any repeated headers). These are the source for the final stitched grid.",
    )


# Schemas for provenance.
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

    bbox: BBox
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


class TableRowProvenance(BaseSchema):
    """Row-level provenance aligned to the stitched table rows."""

    bbox: BBox = Field(
        ..., description="Approximate bbox for the full source row region."
    )
    dropped_header_rows: int = Field(
        ...,
        description="How many header rows were dropped from this slice during stitching.",
    )
    page_index: int = Field(
        ..., description="0-based page index for the slice that contributed this row."
    )
    row_bbox: Optional[BBox] = Field(
        default=None,
        description="Approximate bbox for the stitched row in the table (may equal bbox).",
    )
    slice_index: int = Field(
        ..., description="0-based slice index within the stitched TableSegment."
    )
    slice_row_index: int = Field(
        ..., description="0-based row index within the original slice rows list."
    )
    slice_row_index_after_drop: int = Field(
        ...,
        description="Row index after dropping repeated headers on continuation slices.",
    )
    slice_total_rows: int = Field(
        ..., description="Total number of raw rows in the originating slice."
    )


# Schemas for stitched segments.
class BlockSegment(BaseSchema):
    """A stitched block segment (paragraph/list/caption/heading/figure, etc.)."""

    block_type: BlockType = Field(
        ...,
        description="The structural category of this segment (e.g., 'Paragraph', 'List', 'Heading'). Derived from the constituent slices.",
    )

    # We keep both the structured field(s) and a convenience "combined_text". For
    # lists, the canonical representation is list_items; combined_text is optional.
    combined_text: Optional[str] = Field(
        None,
        description="Concatenated text across page slices when continuation occurs.",
    )

    figure: Optional[dict[str, Any]] = Field(
        None,
        description="Metadata and content if this segment represents an image, chart, or diagram. Null for text/list blocks.",
    )
    kind: Literal["block"] = Field(
        "block",
        description="Discriminator field used to distinguish `BlockSegment` from `TableSegment` in union types.",
    )
    list_items: Optional[list[ListItem]] = Field(
        None,
        description="Structured list entries if `block_type` is 'List'. This is the canonical data source for lists, containing individual bullets/numbers merged across all stitched pages.",
    )
    local_code: Optional[str] = Field(
        None,
        description="The resolved explicit numbering (e.g., '2.1.1') for this segment. Computed by normalizing and resolving local codes found across the stitched slices.",
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
        description="All source PageIR items that were stitched into this segment.",
    )
    slices: list[BlockSlice] = Field(
        default_factory=list, description="Per-page slices in order."
    )
    text: Optional[TextUnit] = Field(
        None,
        description="The structured text content (including language metadata). For multi-page segments, the `.text` field here matches the content of `combined_text`.",
    )


class TableSegment(BaseSchema):
    """A stitched table segment merged across pages."""

    columns_signature: Optional[str] = Field(
        default=None,
        description="Normalized string signature derived from `header_rows_canonical`, useful for matching and downstream canonicalization.",
    )
    grid_sources: Optional[list[list[dict[str, Any]]]] = Field(
        default=None,
        description="Optional grid aligned to `rows_grid` that records where each grid cell came from (e.g., original row index and start cell position). Useful for debugging spans.",
    )
    header_row_count: int = Field(
        ...,
        description="The number of rows at the top of the stitched table that function as headers. Determined from the first slice or inferred via heuristic if missing.",
    )
    header_rows: list[TableRow] = Field(
        default_factory=list, description="Header rows (first header_row_count rows)."
    )
    header_rows_canonical: list[list[str]] = Field(
        default_factory=list,
        description="Canonicalized header rows as normalized strings per cell, derived from `header_rows`. Shape: [[cell0, cell1, ...], ...].",
    )
    kind: Literal["table"] = Field(
        "table",
        description="Discriminator field used to distinguish `TableSegment` from `BlockSegment` in union types.",
    )
    local_code: Optional[str] = Field(
        None,
        description="The resolved table identifier (e.g., 'Table 1') for this segment. Computed by scanning the slice chain for the first non-null code.",
    )
    n_cols: int = Field(..., description="Max number of columns across stitched rows.")
    row_provenance: Optional[list[TableRowProvenance]] = Field(
        default=None,
        description=(
            "Row-level provenance aligned to stitched `rows` (and `rows_grid`). "
            "Length must equal len(rows). Each entry includes at least page_index, "
            "slice_index, and an approximate row_bbox."
        ),
    )
    rows: list[TableRow] = Field(
        ..., description="Stitched visual rows (header rows included once)."
    )
    rows_grid: Optional[list[TableRow]] = Field(
        default=None,
        description=(
            "Span-expanded rectangular version of `rows` where every row has exactly "
            "`n_cols` cells and each cell has row_span=1 and col_span=1. "
            "Pure structural normalization (no semantic interpretation)."
        ),
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


Segment = Annotated[Union[BlockSegment, TableSegment], Field(discriminator="kind")]


# Schemas for stitching.
class DocumentIR(BaseSchema):
    """Document-level IR after stitching."""

    coord_space: str = Field(
        ..., description="Coordinate space used for all bounding boxes."
    )
    doc_key: str = Field(
        ...,
        description="Deterministic hash key of the source PDF bytes (e.g., SHA-256 hex).",
    )
    dpi: int = Field(
        ...,
        description="DPI used to render the page image that these pixel bboxes refer to.",
    )
    image_height: int = Field(..., description="Height of the source image in pixels.")
    image_width: int = Field(..., description="Width of the source image in pixels.")
    page_count: int = Field(..., description="Total number of pages stitched.")
    pdf_name: Optional[str] = Field(None, description="Source PDF filename (no path).")
    segments: list[Segment] = Field(
        ..., description="Ordered stitched segments across the whole document."
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Any non-fatal issues detected during stitching.",
    )
