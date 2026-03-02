"""This module contains schemas used for **extracting** page Intermediate
Representations (IRs).
"""

# Standard Library
from typing import Literal, Optional, Self

# Third Party Library
from pydantic import Field, model_validator

# Package Library
from skg.schemas import BaseSchema, BBox, LanguageField
from skg.utils.constants import BlockType, FigureKind, ItemBoundary, PageBoundaryState


# Schemas for component models.
class TextUnit(BaseSchema):
    """The atomic unit of text extraction. Represents a span of text with consistent
    styling.
    """

    language: LanguageField = Field(..., description="BCP-47 language code.")
    text: str = Field(
        ...,
        description="Verbatim text content. Do NOT fix typos or complete cut-off sentences.",
    )
    text_en: Optional[str] = Field(
        default=None,
        description="English translation of the text content. This is populated by a later translation pass and should be null during extraction.",
    )


class TableCell(BaseSchema):
    """A single cell within a table grid."""

    col_span: int = Field(1, description="Number of columns this cell spans.", ge=1)
    row_span: int = Field(1, description="Number of rows this cell spans.", ge=1)
    text: Optional[TextUnit] = Field(
        None, description="The content of the cell. Null if visually empty."
    )


class TableRow(BaseSchema):
    """A single horizontal row in a table."""

    cells: list[TableCell] = Field(
        ...,
        description="Ordered list of cells in this row, from left to right.",
        min_length=1,
    )


class Table(BaseSchema):
    """Represents a tabular grid extracted from the page."""

    bbox: BBox
    boundary: ItemBoundary = Field(
        ItemBoundary.COMPLETE,
        description=(
            f"Semantic continuity of this table across page boundaries: "
            f"'{ItemBoundary.RESUMED.value}' if this table is a continuation from the previous page; "
            f"'{ItemBoundary.TRUNCATED.value}' if it continues onto the next page; "
            f"'{ItemBoundary.BOTH.value}' if it continues from prev page and to next page; "
            f"'{ItemBoundary.COMPLETE.value}' if fully contained on this page. "
            f"DO NOT rely on whether table borders are drawn. Many PDFs repeat gridlines and headers on continuation pages."
        ),
    )
    header_row_count: int = Field(
        0,
        description="Number of rows at the top that function as headers. 0 if none.",
        ge=0,
    )
    kind: Literal["table"] = Field(..., description="Must be 'table'.")
    local_code: Optional[str] = Field(
        None,
        description="Explicit curriculum code if present (e.g., 'Table 1.2'). Preserve punctuation exactly (dots/hyphens/slashes).",
    )
    n_cols: Optional[int] = Field(
        default=None,
        description="Intended number of visual columns in the table grid if clearly inferable from the ruling/grid. Omit or set null if unsure.",
        ge=1,
    )
    repeats_header: Optional[bool] = Field(
        None,
        description="For tables that continue from a previous page, indicates whether the header rows are visibly repeated on this page. Null if unknown.",
    )
    rows: list[TableRow] = Field(
        ...,
        description="All visual rows. Do NOT separate headers; extract the grid exactly as seen.",
        min_length=1,  # Enforce at least one row to avoid empty table hallucinations
    )

    @model_validator(mode="after")
    def validate_header_row_count(self) -> Self:
        """Validate that header_row_count does not exceed number of rows.

        Returns
        -------
        Self
            The passed in Table.

        Raises
        ------
        ValueError
            If header_row_count exceeds number of rows.
        """

        if self.header_row_count > len(self.rows):
            raise ValueError(
                f"header_row_count ({self.header_row_count}) cannot exceed number of "
                f"rows ({len(self.rows)})."
            )

        return self

    @model_validator(mode="after")
    def validate_repeats_header_consistency(self) -> Self:
        """Validate that repeats_header is only set when boundary is resumed/both.

        Returns
        -------
        Self
            The passed in Table.

        Raises
        ------
        ValueError
            If repeats_header is True when boundary is not resumed/both.
        """

        if self.repeats_header is not None and self.boundary not in {
            ItemBoundary.RESUMED,
            ItemBoundary.BOTH,
        }:
            raise ValueError(
                f"repeats_header is only allowed when boundary is "
                f"{ItemBoundary.RESUMED.value} or {ItemBoundary.BOTH.value}."
            )

        return self

    @model_validator(mode="after")
    def validate_row_and_col_spans(self) -> Self:
        """Validate row/col span consistency across the table grid.

        Checks enforced:

        1. Every row has at least one cell.
        2. A cell's row_span cannot run past the bottom of the table.
        3. Spanned cells (row_span > 1 or col_span > 1) must carry content
            (text != null).
        4. If n_cols is set:
           - No individual cell may have col_span > n_cols.
           - No row may exceed n_cols (sum of col_spans).
           - At least one row must reach n_cols.

        Returns
        -------
        Self
            The passed in Table.

        Raises
        ------
        ValueError
            If any span check fails.
        """

        n_rows = len(self.rows)
        row_widths: list[int] = []

        for r, row in enumerate(self.rows):
            current_row_width = 0

            for c, cell in enumerate(row.cells):
                # Bounds: row_span can't run off the table.
                if r + cell.row_span > n_rows:
                    raise ValueError(
                        f"row_span exceeds table bounds at rows[{r}].cells[{c}]: "
                        f"row_span={cell.row_span} but only {n_rows - r} rows remain."
                    )

                # Empty merges: spanned cells should carry content.
                if (cell.row_span > 1 or cell.col_span > 1) and cell.text is None:
                    raise ValueError(
                        f"Spanned cell must not have text=null at rows[{r}].cells[{c}] "
                        f"(row_span={cell.row_span}, col_span={cell.col_span})."
                    )

                # If n_cols is known, individual cell col_span can't exceed it.
                if self.n_cols is not None and cell.col_span > self.n_cols:
                    raise ValueError(
                        f"col_span exceeds n_cols at rows[{r}].cells[{c}]: "
                        f"col_span={cell.col_span}, n_cols={self.n_cols}."
                    )

                current_row_width += cell.col_span

            # Validate total row width against n_cols.
            if self.n_cols is not None and current_row_width > self.n_cols:
                raise ValueError(
                    f"Row exceeds n_cols at rows[{r}]: "
                    f"sum(col_span)={current_row_width}, n_cols={self.n_cols}."
                )

            row_widths.append(current_row_width)

        # Global table check: at least one row must reach n_cols.
        if self.n_cols is not None and all(w < self.n_cols for w in row_widths):
            raise ValueError(
                f"Table.n_cols={self.n_cols} but no row reaches that width. "
                f"Row widths={row_widths}. This usually indicates missing cells "
                f"or wrong n_cols."
            )

        return self


class ListItem(BaseSchema):
    """A single item in a list or outline."""

    marker: Optional[str] = Field(
        None,
        description="The bullet/numbering marker (e.g., '1.', '•', 'a)', '3.9.4'). Null if there is no explicit marker (e.g., TOC dot-leader entries). Extract verbatim.",
    )
    text: TextUnit = Field(..., description="The text content of the list item.")

    @model_validator(mode="after")
    def validate_marker_not_whitespace_only(self) -> Self:
        """Validate that marker, when present, is not whitespace-only.

        Returns
        -------
        Self
            The passed in ListItem.

        Raises
        ------
        ValueError
            If marker is a whitespace-only or empty string.
        """

        if self.marker is not None and not self.marker.strip():
            raise ValueError(
                "List item marker must be null or a non-whitespace string."
            )

        return self

    @model_validator(mode="after")
    def validate_text_not_whitespace_only(self) -> Self:
        """Validate that list item text is not whitespace-only.

        Returns
        -------
        Self
            The passed in ListItem.

        Raises
        ------
        ValueError
            If text is whitespace-only.
        """

        if not self.text.text.strip():
            raise ValueError("List item text must not be whitespace-only.")

        return self


class FigureUnit(BaseSchema):
    """Non-semantic metadata about a diagram/figure region.

    NB: This is NOT a full diagram parse. It is only enough to:

    1. Preserve the presence of the figure.
    2. Support later optional diagram interpretation.
    3. Keep strong provenance via bbox.
    """

    alt_text: Optional[str] = Field(
        None,
        description="Very short, non-semantic description of what the figure is (e.g., 'flowchart with arrows', 'pyramid diagram'). Do NOT interpret meaning. Null if unknown.",
        max_length=500,
    )
    caption: Optional[TextUnit] = Field(
        None,
        description="Caption text if it is clearly attached to this figure (e.g., 'Figure 2: ...').",
    )
    contains_text: Optional[bool] = Field(
        None,
        description="True if there is visible text inside the figure region (not including nearby captions). Null if unknown.",
    )
    embedded_text: Optional[TextUnit] = Field(
        None,
        description="Best-effort verbatim text visible INSIDE the figure region (excluding nearby captions). Populate when contains_text=true; null otherwise.",
    )
    figure_kind: FigureKind = Field(
        FigureKind.UNKNOWN,
        description=f"Coarse type label for the figure region. Keep conservative; use '{FigureKind.UNKNOWN.value}' if unsure.",
    )

    @model_validator(mode="after")
    def validate_alt_text_required_and_non_empty(self) -> Self:
        """Validate that alt_text is present and non-empty.

        Returns
        -------
        Self
            The passed in FigureUnit.

        Raises
        ------
        ValueError
            If alt_text is None, empty, or whitespace-only.
        """

        if self.alt_text is None or not self.alt_text.strip():
            raise ValueError(
                "figure.alt_text must be present and non-empty. "
                "Provide a short surface description (e.g., 'bar chart', "
                "'geometry diagram with labels')."
            )

        return self

    @model_validator(mode="after")
    def validate_caption_not_whitespace_only(self) -> Self:
        """Validate that caption text, when present, is not whitespace-only.

        Returns
        -------
        Self
            The passed in FigureUnit.

        Raises
        ------
        ValueError
            If caption.text is whitespace-only.
        """

        if self.caption is not None and not self.caption.text.strip():
            raise ValueError(
                "figure.caption.text must not be whitespace-only. "
                "Set caption=null or extract the real caption text."
            )

        return self

    @model_validator(mode="after")
    def validate_contains_text_requires_embedded_text(self) -> Self:
        """Validate consistency between contains_text and embedded_text.

        Returns
        -------
        Self
            The passed in FigureUnit.

        Raises
        ------
        ValueError
            If validation fails.
        """

        if self.contains_text is True:
            if self.embedded_text is None:
                raise ValueError(
                    "figure.contains_text=true requires figure.embedded_text "
                    "(best-effort transcription)."
                )
            if not self.embedded_text.text.strip():
                raise ValueError(
                    "figure.contains_text=true but embedded_text is whitespace-only. "
                    "Populate embedded_text with best-effort verbatim text, or set "
                    "contains_text=false and embedded_text=null."
                )

        if self.contains_text is False and self.embedded_text is not None:
            raise ValueError(
                "figure.contains_text=false requires figure.embedded_text=null."
            )

        return self

    @model_validator(mode="after")
    def validate_equation_requires_text(self) -> Self:
        """Validate that equation figures contain text.

        Returns
        -------
        Self
            The passed in FigureUnit.
        """

        if self.figure_kind == FigureKind.EQUATION and self.contains_text is not True:
            raise ValueError(
                "figure.figure_kind='equation' requires figure.contains_text=true "
                "(and therefore figure.embedded_text must be provided). "
                "If unsure, use figure_kind='unknown' or 'diagram' instead."
            )

        return self


class Block(BaseSchema):
    """A grouping of text content (paragraph, heading, list, etc.)."""

    bbox: BBox
    block_type: BlockType = Field(..., description="The visual structure of the block.")
    boundary: ItemBoundary = Field(
        ItemBoundary.COMPLETE,
        description=(
            f"Semantic continuity of this block across page boundaries: "
            f"'{ItemBoundary.RESUMED.value}' if it continues from the previous page; "
            f"'{ItemBoundary.TRUNCATED.value}' if it continues onto the next page; "
            f"'{ItemBoundary.BOTH.value}' if both; "
            f"otherwise '{ItemBoundary.COMPLETE.value}'."
        ),
    )
    figure: Optional[FigureUnit] = Field(
        None,
        description=f"Figure/diagram metadata. Must be null unless block_type='{BlockType.FIGURE.value}'. This does NOT parse internal structure; it only preserves the region and light hints.",
    )
    kind: Literal["block"] = Field(..., description="Must be 'block'.")
    list_items: Optional[list[ListItem]] = Field(
        None,
        description="The items (for lists). Null for headings/paragraphs. Must be null unless block_type indicates a list.",
    )
    local_code: Optional[str] = Field(
        None,
        description="Explicit code if present (e.g., '3.9.4.1', 'SECTION 1'). Extract verbatim and preserve punctuation exactly (dots/hyphens/slashes).",
    )
    text: Optional[TextUnit] = Field(
        None,
        description="The text content (for headings/paragraphs/captions). Must be null when block_type indicates a list or a figure.",
    )

    @model_validator(mode="after")
    def validate_block_type_figure(self) -> Self:
        """Validate figure block types.

        Returns
        -------
        Self
            The passed in Block.

        Raises
        ------
        ValueError
            If validation fails.
        """

        bt = self.block_type

        # Figure blocks: figure required; text/list_items must be null.
        if bt == BlockType.FIGURE:
            if self.figure is None:
                raise ValueError(f"Block block_type='{bt}' requires figure metadata.")
            if self.text is not None:
                raise ValueError(f"Block block_type='{bt}' requires text=null.")
            if self.list_items is not None:
                raise ValueError(f"Block block_type='{bt}' requires list_items=null.")

        return self

    @model_validator(mode="after")
    def validate_block_type_list(self) -> Self:
        """Validate list block types.

        Returns
        -------
        Self
            The passed in Block.

        Raises
        ------
        ValueError
            If validation fails.
        """

        bt = self.block_type

        # List blocks: list_items required; text/figure must be null.
        if bt == BlockType.LIST:
            if not self.list_items:
                raise ValueError(
                    f"Block block_type='{bt}' requires non-empty list_items."
                )
            if self.text is not None:
                raise ValueError(f"Block block_type='{bt}' requires text=null.")
            if self.figure is not None:
                raise ValueError(f"Block block_type='{bt}' requires figure=null.")

        return self

    @model_validator(mode="after")
    def validate_block_type_other(self) -> Self:
        """Validate other block types.

        Returns
        -------
        Self
            The passed in Block.

        Raises
        ------
        ValueError
            If validation fails.
        """

        bt = self.block_type

        if bt in (BlockType.FIGURE, BlockType.LIST):
            return self

        # Everything else (artifact/caption/heading/paragraph): text required;
        # list_items/figure must be null.
        if self.text is None or not self.text.text.strip():
            raise ValueError(f"Block block_type='{bt}' requires non-empty text.")
        if self.list_items is not None:
            raise ValueError(f"Block block_type='{bt}' requires list_items=null.")
        if self.figure is not None:
            raise ValueError(f"Block block_type='{bt}' requires figure=null.")

        return self


# Schemas for extraction.
class PageIR(BaseSchema):
    """Intermediate Representation of a single PDF page."""

    # Filled in deterministically.
    coord_space: Literal["px"] = Field(
        "px",
        description="Coordinate space used for all bounding boxes. 'px' indicates pixel coordinates in the rendered page image with origin at the top-left.",
    )
    doc_key: Optional[str] = Field(
        None,
        description="Deterministic hash key of the source PDF bytes (e.g., SHA-256 hex). This should be populated by the Python pipeline; it may be null during extraction.",
    )
    dpi: Optional[int] = Field(
        default=None,
        description="DPI used to render the page image that these pixel bboxes refer to. Populated by Python; may be null during extraction.",
    )
    image_height: Optional[int] = Field(
        None,
        description="Height of the source image in pixels. This should be populated by the Python pipeline; it may be null during extraction.",
    )
    image_width: Optional[int] = Field(
        None,
        description="Width of the source image in pixels. This should be populated by the Python pipeline; it may be null during extraction.",
    )
    page_index: Optional[int] = Field(
        None,
        description="0-based index of the page in the PDF. This should be populated by the Python pipeline; it may be null during extraction.",
    )
    pdf_name: Optional[str] = Field(
        None,
        description="Source PDF filename (no path). This should be populated by the Python pipeline; it may be null during extraction.",
    )

    # Filled in by vision model.
    boundary_state: PageBoundaryState = Field(
        default=PageBoundaryState.STANDALONE,
        description="Overall continuity of the page. Derived from item boundaries in Python.",
    )
    items: list[Block | Table] = Field(
        ...,
        description="Ordered list of content items found on the page, sorted by visual reading order (e.g., multi-column left-to-right, then down)",
    )
