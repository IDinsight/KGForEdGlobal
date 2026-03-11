"""This module contains schemas used for stitching individual (verified) PageIRs into a
single DocumentIR.
"""

# Future Library
from __future__ import annotations

# Standard Library
from typing import Annotated, Any, Literal, Optional, Self, Union

# Third Party Library
from pydantic import Field, model_validator

# Package Library
from skg.page_ir_extraction.schemas import ListItem, TableRow, TextUnit
from skg.schemas import BaseSchema, BBox
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

    @model_validator(mode="after")
    def validate_payload_by_block_type(self) -> Self:
        """Validate mutually exclusive payload fields by block type.

        Returns
        -------
        Self
            The validated slice.

        Raises
        ------
        ValueError
            If the slice payload does not match its block type.
        """

        b_type = self.block_type

        expected_field = {BlockType.FIGURE: "figure", BlockType.LIST: "list_items"}.get(
            b_type, "text"
        )

        if expected_field == "figure" and self.figure is None:
            raise ValueError(
                f"BlockSlice block_type='{b_type}' requires figure metadata."
            )

        if expected_field == "list_items" and not self.list_items:
            raise ValueError(
                f"BlockSlice block_type='{b_type}' requires non-empty list_items."
            )

        if expected_field == "text" and (
            self.text is None or not self.text.text.strip()
        ):
            raise ValueError(
                f"BlockSlice block_type='{b_type}' requires non-empty text."
            )

        forbidden_fields = {"figure", "list_items", "text"} - {expected_field}

        for field in forbidden_fields:
            if getattr(self, field) is not None:
                raise ValueError(
                    f"BlockSlice block_type='{b_type}' requires {field}=null."
                )

        return self


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

    @model_validator(mode="after")
    def validate_dropped_header_rows(self) -> Self:
        """Validate dropped-header bookkeeping.

        Returns
        -------
        Self
            The validated slice.

        Raises
        ------
        ValueError
            If dropped-header counts are inconsistent.
        """

        if not self.rows:
            raise ValueError("TableSlice.rows must contain at least one row.")

        if self.dropped_header_rows > len(self.rows):
            raise ValueError(
                f"dropped_header_rows ({self.dropped_header_rows}) cannot exceed number of rows ({len(self.rows)})."
            )

        if self.dropped_header_rows > 0 and self.boundary not in {
            ItemBoundary.BOTH,
            ItemBoundary.RESUMED,
        }:
            raise ValueError(
                "dropped_header_rows may only be > 0 when boundary is resumed or both."
            )

        return self

    @model_validator(mode="after")
    def validate_header_row_count(self) -> Self:
        """Validate that header_row_count does not exceed the number of rows.

        Returns
        -------
        Self
            The validated slice.

        Raises
        ------
        ValueError
            If header_row_count is inconsistent with rows.
        """

        if self.header_row_count > len(self.rows):
            raise ValueError(
                f"header_row_count ({self.header_row_count}) cannot exceed number of rows ({len(self.rows)})."
            )

        return self

    @model_validator(mode="after")
    def validate_repeats_header_consistency(self) -> Self:
        """Validate repeats_header consistency for continuation slices.

        Returns
        -------
        Self
            The validated slice.

        Raises
        ------
        ValueError
            If repeats_header is set on a non-continuation slice.
        """

        if self.repeats_header is not None and self.boundary not in {
            ItemBoundary.BOTH,
            ItemBoundary.RESUMED,
        }:
            raise ValueError(
                "repeats_header is only allowed when boundary is resumed or both."
            )

        return self


# Schemas for provenance.
class SectionHeadingRef(BaseSchema):
    """Lightweight heading pointer captured at segment start for downstream context."""

    item_index: int = Field(
        ..., description="0-based index of the heading item inside PageIR.items."
    )
    page_index: int = Field(..., description="0-based page index of the heading block.")
    text: str = Field(..., description="Heading text as extracted (no translation).")

    @model_validator(mode="after")
    def validate_text_not_whitespace_only(self) -> Self:
        """Validate that heading text is not empty or whitespace-only.

        Returns
        -------
        Self
            The validated heading reference.

        Raises
        ------
        ValueError
            If heading text is empty or whitespace-only.
        """

        if not self.text.strip():
            raise ValueError("SectionHeadingRef.text must not be whitespace-only.")

        return self


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

    @model_validator(mode="after")
    def validate_row_indices(self) -> Self:
        """Validate row-level provenance index relationships.

        Returns
        -------
        Self
            The validated row provenance entry.

        Raises
        ------
        ValueError
            If index bookkeeping is inconsistent.
        """

        if self.dropped_header_rows > self.slice_total_rows:
            raise ValueError(
                "dropped_header_rows cannot exceed slice_total_rows in TableRowProvenance."
            )

        if self.slice_row_index >= self.slice_total_rows:
            raise ValueError(
                "slice_row_index must be < slice_total_rows in TableRowProvenance."
            )

        expected_row_index_after_drop = self.slice_row_index - self.dropped_header_rows

        if expected_row_index_after_drop != self.slice_row_index_after_drop:
            raise ValueError(
                "slice_row_index_after_drop must equal slice_row_index - dropped_header_rows."
            )

        if self.slice_row_index_after_drop < 0:
            raise ValueError(
                "slice_row_index_after_drop must be non-negative in TableRowProvenance."
            )

        return self


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

    @model_validator(mode="after")
    def validate_payload_by_block_type(self) -> Self:
        """Validate mutually exclusive segment payload fields by block type.

        Returns
        -------
        Self
            The validated block segment.

        Raises
        ------
        ValueError
            If the segment payload does not match its block type.
        """

        b_type = self.block_type

        expected_field = {BlockType.FIGURE: "figure", BlockType.LIST: "list_items"}.get(
            b_type, "text"
        )

        if expected_field == "figure" and self.figure is None:
            raise ValueError(
                f"BlockSegment block_type='{b_type}' requires figure metadata."
            )

        if expected_field == "list_items" and not self.list_items:
            raise ValueError(
                f"BlockSegment block_type='{b_type}' requires non-empty list_items."
            )

        if expected_field == "text" and (
            self.text is None or not self.text.text.strip()
        ):
            raise ValueError(
                f"BlockSegment block_type='{b_type}' requires non-empty text."
            )

        forbidden_fields = {"figure", "list_items", "text"} - {expected_field}

        for field in forbidden_fields:
            if getattr(self, field) is not None:
                raise ValueError(
                    f"BlockSegment block_type='{b_type}' requires {field}=null."
                )

        return self

    @model_validator(mode="after")
    def validate_slice_and_provenance_alignment(self) -> Self:
        """Validate slice-level and provenance-level alignment.

        Returns
        -------
        Self
            The validated block segment.

        Raises
        ------
        ValueError
            If slices or provenance are missing or inconsistent.
        """

        if not self.slices:
            raise ValueError("BlockSegment.slices must contain at least one slice.")

        if not self.segment_provenance:
            raise ValueError(
                "BlockSegment.segment_provenance must contain at least one entry."
            )

        if len(self.segment_provenance) != len(self.slices):
            raise ValueError(
                "BlockSegment.segment_provenance length must equal len(slices)."
            )

        for provenance in self.segment_provenance:
            if provenance.kind != "block":
                raise ValueError(
                    "BlockSegment.segment_provenance entries must all have kind='block'."
                )

        slice_positions = [
            (slice_.page_index, slice_.item_index) for slice_ in self.slices
        ]

        if slice_positions != sorted(slice_positions):
            raise ValueError(
                "BlockSegment.slices must be ordered by (page_index, item_index)."
            )

        if any(slice_.block_type != self.block_type for slice_ in self.slices):
            raise ValueError(
                "All BlockSegment.slices must share the segment's block_type."
            )

        if self.text is not None and self.combined_text is not None:
            if self.text.text != self.combined_text:
                raise ValueError(
                    "BlockSegment.text.text must equal combined_text when both are present."
                )

        return self


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

    @model_validator(mode="after")
    def validate_header_shapes(self) -> Self:
        """Validate header-count and header-shape consistency.

        Returns
        -------
        Self
            The validated table segment.

        Raises
        ------
        ValueError
            If header rows or canonical headers are inconsistent.
        """

        if not self.rows:
            raise ValueError("TableSegment.rows must contain at least one row.")

        if self.header_row_count > len(self.rows):
            raise ValueError(
                f"header_row_count ({self.header_row_count}) cannot exceed number of rows ({len(self.rows)})."
            )

        if len(self.header_rows) != self.header_row_count:
            raise ValueError(
                "TableSegment.header_rows length must equal header_row_count."
            )

        if len(self.header_rows_canonical) != len(self.header_rows):
            raise ValueError(
                "TableSegment.header_rows_canonical length must equal len(header_rows)."
            )

        return self

    @model_validator(mode="after")
    def validate_optional_row_aligned_structures(self) -> Self:
        """Validate optional row-aligned structures such as row_provenance and grids.

        Returns
        -------
        Self
            The validated table segment.

        Raises
        ------
        ValueError
            If any optional row-aligned structure is malformed.
        """

        n_rows = len(self.rows)
        n_cols = self.n_cols

        for attr in ("row_provenance", "rows_filldown", "rows_grid"):
            val = getattr(self, attr)

            if val is not None and len(val) != n_rows:
                raise ValueError(f"TableSegment.{attr} length must equal len(rows).")

        if self.grid_sources is not None:
            if self.rows_grid is None:
                raise ValueError(
                    "TableSegment.grid_sources requires rows_grid to also be present."
                )
            if len(self.grid_sources) != len(self.rows_grid):
                raise ValueError(
                    "TableSegment.grid_sources length must equal len(rows_grid)."
                )

            bad_src_idx = next(
                (i for i, row in enumerate(self.grid_sources) if len(row) != n_cols),
                None,
            )

            if bad_src_idx is not None:
                raise ValueError(
                    f"grid_sources[{bad_src_idx}] must contain exactly n_cols={n_cols} entries."
                )

        if self.rows_grid is not None:
            for i, row in enumerate(self.rows_grid):
                if len(row.cells) != n_cols:
                    raise ValueError(
                        f"rows_grid[{i}] must contain exactly n_cols={n_cols} cells."
                    )

                bad_cell_idx = next(
                    (
                        j
                        for j, cell in enumerate(row.cells)
                        if cell.col_span != 1 or cell.row_span != 1
                    ),
                    None,
                )

                if bad_cell_idx is not None:
                    raise ValueError(
                        f"rows_grid[{i}].cells[{bad_cell_idx}] must have row_span=1 and col_span=1."
                    )

        return self

    @model_validator(mode="after")
    def validate_slice_and_provenance_alignment(self) -> Self:
        """Validate slice-level and provenance-level alignment.

        Returns
        -------
        Self
            The validated table segment.

        Raises
        ------
        ValueError
            If slices or provenance are missing or inconsistent.
        """

        if not self.slices:
            raise ValueError("TableSegment.slices must contain at least one slice.")

        if not self.segment_provenance:
            raise ValueError(
                "TableSegment.segment_provenance must contain at least one entry."
            )

        if len(self.segment_provenance) != len(self.slices):
            raise ValueError(
                "TableSegment.segment_provenance length must equal len(slices)."
            )

        for provenance in self.segment_provenance:
            if provenance.kind != "table":
                raise ValueError(
                    "TableSegment.segment_provenance entries must all have kind='table'."
                )

        slice_positions = [
            (slice_.page_index, slice_.item_index) for slice_ in self.slices
        ]
        if slice_positions != sorted(slice_positions):
            raise ValueError(
                "TableSegment.slices must be ordered by (page_index, item_index)."
            )

        return self


Segment = Annotated[Union[BlockSegment, TableSegment], Field(discriminator="kind")]


# Schemas for stitching.
class DocumentPageMeta(BaseSchema):
    """Metadata about each page in the source PDF, used for stitching and provenance."""

    coord_space: str = Field(
        "px", description="Coordinate space for bboxes on this page."
    )
    dpi: int = Field(..., description="DPI used to render this page.")
    image_height: int = Field(..., description="Rendered page height in pixels.")
    image_width: int = Field(..., description="Rendered page width in pixels.")
    is_blank: bool = Field(
        False, description="True if the page contains no extracted items."
    )
    page_index: int = Field(..., description="0-based page index in the PDF.")


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
    page_count: int = Field(..., description="Total number of pages stitched.")
    pages: list[DocumentPageMeta] = Field(
        default_factory=list,
        description="Per-page rendering and extraction metadata. Use this for bbox interpretation.",
    )
    pdf_name: Optional[str] = Field(None, description="Source PDF filename (no path).")
    segments: list[Segment] = Field(
        ..., description="Ordered stitched segments across the whole document."
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Any non-fatal issues detected during stitching.",
    )

    @model_validator(mode="after")
    def validate_page_count(self) -> Self:
        """Validate top-level page-count consistency.

        Returns
        -------
        Self
            The validated DocumentIR.

        Raises
        ------
        ValueError
            If page_count does not match len(pages).
        """

        if self.page_count != len(self.pages):
            raise ValueError("DocumentIR.page_count must equal len(pages).")

        return self
