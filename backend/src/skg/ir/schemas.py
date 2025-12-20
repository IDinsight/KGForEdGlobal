"""This module contains schemas used for the Intermediate Representation (IR) of
the document extraction process.
"""

# Standard Library
from typing import Literal, Optional, Union

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field

# Package Library
from skg.utils.constants import (
    BBox,
    BlockType,
    ContentRole,
    ItemBoundary,
    LanguageField,
    PageBoundaryState,
    TextStyle,
)


# Schemas for primitives.
class BaseIRModel(BaseModel):
    """Base model that enforces 'additionalProperties: false' in JSON schema for
    compatibility with OpenAI Structured Outputs.
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)


# Schemas for component models.
class TextUnit(BaseIRModel):
    """The atomic unit of text extraction. Represents a span of text with consistent
    styling.
    """

    bbox: Optional[BBox] = None
    ends_with_terminal_punctuation: bool = Field(
        True,
        description="True if the text ends with ., ?, or !. False if it ends with a hyphen or mid-sentence.",
    )
    language: LanguageField = Field(
        default="unk",
        description="BCP-47 language code detected for this specific text block.",
    )
    styles: list[TextStyle] = Field(
        default_factory=list,
        description="Visual styles applied to this text. Use 'bold' for headers/emphasis.",
    )
    text: str = Field(
        ...,
        description="Verbatim text content from the page. Do NOT fix typos or complete cut-off sentences.",
    )


class TableCell(BaseIRModel):
    """A single cell within a table grid."""

    bbox: Optional[BBox] = None
    col_span: int = Field(1, ge=1, description="Number of columns this cell spans.")
    row_span: int = Field(1, ge=1, description="Number of rows this cell spans.")
    text: Optional[TextUnit] = Field(
        None, description="The content of the cell. Null if empty."
    )


class TableRow(BaseIRModel):
    """A single horizontal row in a table."""

    cells: list[TableCell] = Field(
        ..., description="Ordered list of cells in this row, from left to right."
    )


class CurriculumTable(BaseIRModel):
    """Represents a tabular grid extracted from the page."""

    bbox: Optional[BBox] = None
    boundary: ItemBoundary = Field(
        ItemBoundary.COMPLETE,
        description="Status of the table's vertical continuity. 'truncated' if bottom border is missing; 'resumed' if top border is missing.",
    )
    header_row_count: int = Field(
        0,
        ge=0,
        description="Number of rows at the top that function as headers. 0 if none.",
    )
    kind: Literal["table"] = Field(
        ..., description="Discriminator for ContentItem union. Must be 'table'."
    )
    local_code: Optional[str] = Field(
        None, description="Explicit curriculum code if present (e.g., 'Table 1.2')."
    )
    repeats_header: Optional[bool] = Field(
        None,
        description=(
            "For tables that continue from a previous page, indicates whether the "
            "header rows are visibly repeated on this page. Null if unknown."
        ),
    )
    rows: list[TableRow] = Field(
        ...,
        description="All visual rows. Do NOT separate headers; extract the grid exactly as seen.",
    )


class ListItem(BaseIRModel):
    """A single item in a list or outline."""

    marker: str = Field(
        ...,
        description="The bullet or numbering marker (e.g., '1.', '•', 'a)', '3.9.4'). Extract verbatim.",
    )
    text: TextUnit = Field(..., description="The content of the list item.")


class CurriculumBlock(BaseIRModel):
    """A grouping of text content (paragraph, heading, or list)."""

    bbox: Optional[BBox] = None
    block_type: BlockType = Field(..., description="The visual structure of the block.")
    boundary: ItemBoundary = Field(
        ItemBoundary.COMPLETE,
        description="Continuity status. 'truncated' if the text cuts off at the page margin.",
    )
    kind: Literal["block"] = Field(
        ..., description="Discriminator for ContentItem union. Must be 'block'."
    )
    list_items: Optional[list[ListItem]] = Field(
        None,
        description="The items (for lists). Null for headings/paragraphs. Must be null unless block_type indicates a list.",
    )
    local_code: Optional[str] = Field(
        None,
        description="Explicit curriculum code if present (e.g., '3.9.4.1', 'SECTION 1'). Extract verbatim.",
    )
    role_hint: Optional[ContentRole] = Field(
        None,
        description=(
            "Non-authoritative hint about the logical role of this block (e.g., 'section_header', "
            "'teacher_guidance', 'list_item'). Leave null if unsure."
        ),
    )
    role_confidence: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Confidence for role_hint in [0,1]. Null if role_hint is null.",
    )
    text: Optional[TextUnit] = Field(
        None,
        description="The text content (for headings/paragraphs). Must be null when block_type indicates a list.",
    )


# Schemas for top-level IR models.
class PageIR(BaseIRModel):
    """Intermediate Representation of a single PDF page."""

    boundary_state: PageBoundaryState = Field(
        ...,
        description="Overall continuity of the page. 'to_next' if content bleeds off bottom; 'from_prev' if it resumes at top.",
    )
    coord_space: Literal["px"] = Field(
        "px",
        description=(
            "Coordinate space used for all bounding boxes. 'px' indicates pixel coordinates "
            "in the rendered page image with origin at the top-left."
        ),
    )
    doc_key: Optional[str] = Field(
        None,
        description=(
            "Deterministic hash key of the source PDF bytes (e.g., SHA-256 hex). "
            "This should be populated by the Python pipeline; it may be null during extraction."
        ),
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
    items: list[Union[CurriculumTable, CurriculumBlock]] = Field(
        ...,
        description="Ordered list of content items found on the page, sorted by visual reading order (e.g., multi-column left-to-right, then down)",
    )
    page_index: Optional[int] = Field(
        None,
        description="0-based index of the page in the PDF. This should be populated by the Python pipeline; it may be null during extraction.",
    )
    pdf_name: Optional[str] = Field(
        None,
        description=(
            "Source PDF filename (no path). This should be populated by the Python pipeline; "
            "it may be null during extraction."
        ),
    )
