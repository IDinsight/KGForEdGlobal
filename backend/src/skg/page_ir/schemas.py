"""This module contains schemas used for extracting page Intermediate Representations
(IRs).
"""

# Future Library
from __future__ import annotations

# Standard Library
from typing import Annotated, Literal, Optional, Union

# Third Party Library
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

# Package Library
from skg.utils.constants import (
    BlockType,
    FigureKind,
    ItemBoundary,
    PageBoundaryState,
    PageContinuationKind,
)
from skg.utils.general import validate_bcp47


def validate_bbox_order(bbox: list[float]) -> list[float]:
    """Ensure bbox is well-ordered: [x0, y0, x1, y1] with x0 < x1 and y0 < y1.

    Parameters
    ----------
    bbox
        The bounding box to validate.

    Returns
    -------
    list[float]
        The validated bounding box.

    Raises
    ------
    ValueError
        If the bbox is not well-ordered.
    """

    if len(bbox) != 4:
        raise ValueError("bbox must have exactly 4 numbers: [x0, y0, x1, y1].")

    x0, y0, x1, y1 = bbox

    # Auto-correct inverted or zero-dimension axes.
    if x0 >= x1:
        # If inverted (x0 > x1), swap. If equal, add 1 px.
        if x0 > x1:
            x0, x1 = x1, x0
        else:
            x1 = x0 + 1.0
    if y0 >= y1:
        if y0 > y1:
            y0, y1 = y1, y0
        else:
            y1 = y0 + 1.0

    return [x0, y0, x1, y1]


# Common fields with descriptions.
BBox = Annotated[
    list[float],
    AfterValidator(validate_bbox_order),
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
        description=(
            "Semantic continuity of this table across page boundaries. "
            "'resumed' if this table is a continuation from the previous page; "
            "'truncated' if it continues onto the next page; "
            "'both' if it continues from prev and to next; "
            "'complete' if fully contained on this page. "
            "Do NOT rely on whether borders are drawn—many PDFs repeat headers/borders on continuation pages."
        ),
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
    n_cols: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Intended number of visual columns in the table grid if clearly inferable "
            "from the ruling/grid. Omit or set null if unsure."
        ),
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
        min_length=1,  # Enforce at least one row to avoid empty table hallucinations
    )

    @model_validator(mode="after")
    def validate_header_row_count(self) -> CurriculumTable:
        """Validate that header_row_count does not exceed number of rows.

        Returns
        -------
        CurriculumTable
            The passed in CurriculumTable.

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
    def validate_repeats_header_consistency(self) -> CurriculumTable:
        """Validate that repeats_header is only set when boundary is resumed/both.

        Returns
        -------
        CurriculumTable
            The passed in CurriculumTable.

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
                "repeats_header is only allowed when table.boundary is resumed/both."
            )

        return self


class ListItem(BaseModelPageIR):
    """A single item in a list or outline."""

    marker: str = Field(
        ...,
        description="The bullet or numbering marker (e.g., '1.', '•', 'a)', '3.9.4'). Extract verbatim.",
    )
    text: TextUnit = Field(..., description="The content of the list item.")


class FigureUnit(BaseModelPageIR):
    """Non-semantic metadata about a diagram/figure region.

    NB: This is NOT a full diagram parse. It is only enough to:
        - preserve the presence of the figure,
        - support later optional diagram interpretation,
        - keep strong provenance via bbox.
    """

    alt_text: Optional[str] = Field(
        None,
        description=(
            "Very short, non-semantic description of what the figure is "
            "(e.g., 'flowchart with arrows', 'pyramid diagram'). "
            "Do NOT interpret meaning. Null if unknown."
        ),
        max_length=200,
    )
    caption: Optional[TextUnit] = Field(
        None,
        description=(
            "Caption text if it is clearly attached to this figure "
            "(e.g., 'Figure 2: ...'). Prefer to extract captions as separate CAPTION "
            "blocks; only populate here when unambiguous."
        ),
    )
    contains_text: Optional[bool] = Field(
        None,
        description=(
            "True if there is visible text inside the figure region (not including nearby captions). "
            "Null if unknown."
        ),
    )
    figure_kind: FigureKind = Field(
        FigureKind.UNKNOWN,
        description=(
            "Coarse type label for the figure region. Keep conservative; use 'unknown' if unsure."
        ),
    )


class CurriculumBlock(BaseModelPageIR):
    """A grouping of text content (paragraph, heading, or list)."""

    bbox: BBox
    block_type: BlockType = Field(..., description="The visual structure of the block.")
    boundary: ItemBoundary = Field(
        ItemBoundary.COMPLETE,
        description=(
            "Semantic continuity of this block across page boundaries. "
            "'resumed' if it continues from the previous page; "
            "'truncated' if it continues onto the next page; "
            "'both' if both; otherwise 'complete'."
        ),
    )
    figure: Optional[FigureUnit] = Field(
        None,
        description=(
            "Figure/diagram metadata. Must be null unless block_type='figure'. This "
            "does NOT parse internal structure; it only preserves the region and light "
            "hints."
        ),
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
        description="The text content (for headings/paragraphs/captions). Must be null when block_type indicates a list or a figure.",
    )

    @model_validator(mode="after")
    def validate_cross_field_invariants(  # pylint:disable=R0912,R1260
        self,
    ) -> CurriculumBlock:
        """Validate cross-field variants.

        Returns
        -------
        CurriculumBlock
            The passed in CurriculumBlock.

        Raises
        ------
        ValueError
            If a cross-field validation fails.
        """

        bt = self.block_type

        # List blocks: list_items required; text/figure must be null.
        if bt == BlockType.LIST:
            if not self.list_items:
                raise ValueError(
                    "CurriculumBlock block_type='list' requires non-empty list_items."
                )
            if self.text is not None:
                raise ValueError(
                    "CurriculumBlock block_type='list' requires text=null."
                )
            if self.figure is not None:
                raise ValueError(
                    "CurriculumBlock block_type='list' requires figure=null."
                )
            return self

        # Figure blocks: figure required; text/list_items must be null.
        if bt == BlockType.FIGURE:
            if self.figure is None:
                raise ValueError(
                    "CurriculumBlock block_type='figure' requires figure metadata."
                )
            if self.text is not None:
                raise ValueError(
                    "CurriculumBlock block_type='figure' requires text=null."
                )
            if self.list_items is not None:
                raise ValueError(
                    "CurriculumBlock block_type='figure' requires list_items=null."
                )
            return self

        # Artifact blocks: allow empty/null text; list_items/figure must be null.
        if bt == BlockType.ARTIFACT:
            if self.list_items is not None:
                raise ValueError(
                    "CurriculumBlock block_type='artifact' requires list_items=null."
                )
            if self.figure is not None:
                raise ValueError(
                    "CurriculumBlock block_type='artifact' requires figure=null."
                )
            return self

        # All other block types: text required; list_items/figure must be null.
        if self.text is None or not self.text.text.strip():
            raise ValueError(
                f"CurriculumBlock block_type='{bt}' requires non-empty text."
            )
        if self.list_items is not None:
            raise ValueError(
                f"CurriculumBlock block_type='{bt}' requires list_items=null."
            )
        if self.figure is not None:
            raise ValueError(f"CurriculumBlock block_type='{bt}' requires figure=null.")

        return self


# Schemas for extraction.
class PageIR(BaseModelPageIR):
    """Intermediate Representation of a single PDF page."""

    boundary_state: PageBoundaryState = Field(
        default=PageBoundaryState.STANDALONE,
        description="Overall continuity of the page. Derived from item boundaries in Python.",
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

    @model_validator(mode="after")
    def clamp_bboxes_within_image(self) -> PageIR:
        """Clamp item bboxes into image bounds. Vision bboxes often drift by a few
        pixels; clamping makes extraction robust while still preserving usable
        provenance.

        Returns
        -------
        PageIR
            The passed in PageIR with clamped bboxes.
        """

        if self.image_width is None or self.image_height is None:
            return self

        image_width = float(self.image_width)
        image_height = float(self.image_height)

        for item in self.items:
            x0, y0, x1, y1 = item.bbox

            x0 = max(0.0, min(float(x0), image_width))
            y0 = max(0.0, min(float(y0), image_height))
            x1 = max(0.0, min(float(x1), image_width))
            y1 = max(0.0, min(float(y1), image_height))

            # Keep bbox well-ordered after clamping (rare edge cases).
            if x1 <= x0:
                x1 = min(image_width, x0 + 1.0)
            if y1 <= y0:
                y1 = min(image_height, y0 + 1.0)

            item.bbox = [x0, y0, x1, y1]

        return self


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
    set_prev_item_boundary: Optional[ItemBoundary] = Field(
        None, description="Boundary state for the last item on the previous page."
    )
    set_next_item_boundary: Optional[ItemBoundary] = Field(
        None, description="Boundary state for the first item on the next page."
    )
    set_next_table_repeats_header: Optional[bool] = Field(
        None,
        description=(
            "If the continuation_kind is 'table' and the next page contains a repeated header, "
            "set this to true. Set to false if the header is not repeated. Null means leave as-is."
        ),
    )

    @model_validator(mode="after")
    def validate_boundary_edit_consistency(self) -> PageIRContinuityVerdict:
        """Validate consistency of suggested boundary edits with is_continuation.

        Returns
        -------
        PageIRContinuityVerdict
            The passed in PageIRContinuityVerdict.

        Raises
        ------
        ValueError
            If the suggested edits are inconsistent with is_continuation.
        """

        if self.is_continuation:
            # If continuing, suggested item boundaries (if provided) must be compatible.
            if self.set_prev_item_boundary in {
                ItemBoundary.COMPLETE,
                ItemBoundary.RESUMED,
            }:
                raise ValueError(
                    "When continuation=true, prev item cannot be complete/resumed."
                )
            if self.set_next_item_boundary in {
                ItemBoundary.COMPLETE,
                ItemBoundary.TRUNCATED,
            }:
                raise ValueError(
                    "When continuation=true, next item cannot be complete/truncated."
                )
            if (
                self.continuation_kind != PageContinuationKind.TABLE
                and self.set_next_table_repeats_header is not None
            ):
                raise ValueError(
                    "set_next_table_repeats_header only allowed for table continuations."
                )
        else:
            if (
                self.set_prev_item_boundary is not None
                and self.set_prev_item_boundary != ItemBoundary.COMPLETE
            ):
                raise ValueError(
                    "When is_continuation=false, set_prev_item_boundary (if provided) must be 'complete'."
                )
            if (
                self.set_next_item_boundary is not None
                and self.set_next_item_boundary != ItemBoundary.COMPLETE
            ):
                raise ValueError(
                    "When is_continuation=false, set_next_item_boundary (if provided) must be 'complete'."
                )
            if self.set_next_table_repeats_header is not None:
                raise ValueError(
                    "set_next_table_repeats_header must be null when is_continuation=false."
                )

        return self

    @model_validator(mode="after")
    def validate_continuation_consistency(self) -> PageIRContinuityVerdict:
        """Validate consistency between is_continuation and continuation_kind.

        Returns
        -------
        PageIRContinuityVerdict
            The passed in PageIRContinuityVerdict.

        Raises
        ------
        ValueError
            If the continuation fields are inconsistent.
        """

        # If not a continuation, kind must be NONE.
        if (
            not self.is_continuation
            and self.continuation_kind != PageContinuationKind.NONE
        ):
            raise ValueError(
                "If is_continuation=false, continuation_kind must be 'none'."
            )

        # If it is a continuation, kind must not be NONE.
        if self.is_continuation and self.continuation_kind == PageContinuationKind.NONE:
            raise ValueError(
                "If is_continuation=true, continuation_kind must not be 'none'."
            )

        return self
