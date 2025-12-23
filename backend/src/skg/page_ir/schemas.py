"""This module contains schemas used for extracting page Intermediate Representations
(IRs).
"""

# Standard Library
from typing import Annotated, Literal, Optional, Union

# Third Party Library
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

# Package Library
from skg.utils.constants import (
    BlockType,
    ItemBoundary,
    PageBoundaryState,
    PageContinuationKind,
)
from skg.utils.general import validate_bcp47

# Common fields with descriptions.
BBox = Annotated[
    list[float],
    Field(
        min_length=4,
        max_length=4,
        description="Bounding box [x0, y0, x1, y1] in absolute pixels (px) relative to the image dimensions.",
    ),
]
BCP47Str = Annotated[str, AfterValidator(validate_bcp47)]
LanguageField = Annotated[
    BCP47Str,
    Field(
        description="Strict BCP-47 language code (e.g., 'en', 'sw'). Use 'und' if unknown; use 'mul' if mixed languages.",
    ),
]


# Schemas for primitives.
class BaseModelPageIR(BaseModel):
    """Base model that enforces 'additionalProperties: false' in JSON schema for
    compatibility with OpenAI Structured Outputs.
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)


# Schemas for component models.
class TextUnit(BaseModelPageIR):
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


class TableCell(BaseModelPageIR):
    """A single cell within a table grid."""

    col_span: int = Field(1, ge=1, description="Number of columns this cell spans.")
    row_span: int = Field(1, ge=1, description="Number of rows this cell spans.")
    text: Optional[TextUnit] = Field(
        None, description="The content of the cell. Null if visually empty."
    )


class TableRow(BaseModelPageIR):
    """A single horizontal row in a table."""

    cells: list[TableCell] = Field(
        ..., description="Ordered list of cells in this row, from left to right."
    )


class CurriculumTable(BaseModelPageIR):
    """Represents a tabular grid extracted from the page."""

    bbox: BBox
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


class ListItem(BaseModelPageIR):
    """A single item in a list or outline."""

    marker: str = Field(
        ...,
        description="The bullet or numbering marker (e.g., '1.', '•', 'a)', '3.9.4'). Extract verbatim.",
    )
    text: TextUnit = Field(..., description="The content of the list item.")


class CurriculumBlock(BaseModelPageIR):
    """A grouping of text content (paragraph, heading, or list)."""

    bbox: BBox
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
    text: Optional[TextUnit] = Field(
        None,
        description="The text content (for headings/paragraphs). Must be null when block_type indicates a list.",
    )


# Schemas for extraction.
class PageIR(BaseModelPageIR):
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


# Schemas for verification.
class PageIRContinuityVerdict(BaseModelPageIR):
    """Model for page IR continuity verification between two pages."""

    prev_page_index: int = Field(..., description="0-based index of the previous page.")
    next_page_index: int = Field(..., description="0-based index of the next page.")

    # What the model thinks.
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Page continuation confidence score (0.0 to 1.0).",
    )
    continuation_kind: PageContinuationKind = Field(
        ..., description="Type of content continuing across the break."
    )
    is_continuation: bool = Field(
        ...,
        description="True if content clearly continues from prev page to next page.",
    )
    rationale: str = Field(..., description="Explanation for the verdict.")

    # Minimal suggested edits (null means leave as-is).
    set_prev_boundary_state: Optional[PageBoundaryState] = Field(
        None, description="Suggested page boundary state for the previous page."
    )
    set_next_boundary_state: Optional[PageBoundaryState] = Field(
        None, description="Suggested page boundary state for the next page."
    )
    set_prev_item_boundary: Optional[ItemBoundary] = Field(
        None, description="Boundary state for the last item on the previous page."
    )
    set_next_item_boundary: Optional[ItemBoundary] = Field(
        None, description="Boundary state for the first item on the next page."
    )
    set_next_table_repeats_header: Optional[bool] = None
