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
    ItemBoundary,
    LinkType,
    PageBoundaryState,
    TextStyle,
)


# Schemas for primitives.
class BaseIRModel(BaseModel):
    """Base model that enforces 'additionalProperties: false' in JSON schema for
    compatibility with OpenAI Structured Outputs.
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class KeyValuePair(BaseIRModel):
    """Pydantic model for a generic key-value pair. Used for metadata fields to avoid
    loose 'dict' typing.
    """

    key: str = Field(..., description="The key name for the metadata item.")
    value: str = Field(..., description="The string value for the metadata item.")


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
    language: str = Field(
        default="en",
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
    has_header_row: bool = Field(
        False,
        description="True if the first row appears to be a header (e.g., bold text, shaded background).",
    )
    kind: Literal["table"] = "table"
    local_code: Optional[str] = Field(
        None, description="Explicit curriculum code if present (e.g., 'Table 1.2')."
    )
    local_id: str = Field(
        ..., description="Unique ID for this table on this page (e.g., 'p5_t1')."
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
    block_type: str = Field(
        ..., description="Structural role: 'heading', 'paragraph', or 'list'."
    )
    boundary: ItemBoundary = Field(
        ItemBoundary.COMPLETE,
        description="Continuity status. 'truncated' if the text cuts off at the page margin.",
    )
    kind: Literal["block"] = "block"
    list_items: Optional[list[ListItem]] = Field(
        None, description="The items (for lists). Null for headings/paragraphs."
    )
    local_code: Optional[str] = Field(
        None,
        description="Explicit curriculum code if present (e.g., '3.9.4.1', 'SECTION 1'). Extract verbatim.",
    )
    local_id: str = Field(
        ..., description="Unique ID for this block on this page (e.g., 'p5_b1')."
    )
    text: Optional[TextUnit] = Field(
        None, description="The text content (for headings/paragraphs). Null for lists."
    )


# Schemas for top-leve IR models.
class PageIR(BaseIRModel):
    """Intermediate Representation of a single PDF page."""

    boundary_state: PageBoundaryState = Field(
        ...,
        description="Overall continuity of the page. 'to_next' if content bleeds off bottom; 'from_prev' if it resumes at top.",
    )
    image_height: int = Field(..., description="Height of the source image in pixels.")
    image_width: int = Field(..., description="Width of the source image in pixels.")
    items: list[Union[CurriculumTable, CurriculumBlock]] = Field(
        ...,
        description="Ordered list of content items found on the page, sorted top-to-bottom.",
    )
    page_index: int = Field(..., description="0-based index of the page in the PDF.")


class StitchLink(BaseIRModel):
    """Represents a logical connection between two items on different pages."""

    child_id: str = Field(
        ...,
        description="The 'local_id' of the item on the current page (the resumed child).",
    )
    confidence: float = Field(ge=0, le=1, description="Model confidence in this link.")
    link_type: LinkType = Field(
        ..., description="The nature of the connection (merge table, join text, etc.)."
    )
    parent_id: str = Field(
        ...,
        description="The 'local_id' of the item on the previous page (the truncated parent).",
    )
