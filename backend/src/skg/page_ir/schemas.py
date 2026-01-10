"""This module contains schemas used for extracting page Intermediate Representations
(IRs).
"""

# Future Library
from __future__ import annotations

# Standard Library
import re

from typing import Annotated, Literal, Optional, Union

# Third Party Library
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

# Package Library
from skg.page_ir.utils import validate_bbox_order
from skg.utils.constants import (
    BlockType,
    FigureKind,
    ItemBoundary,
    PageBoundaryState,
    PageContinuationKind,
)
from skg.utils.general import validate_bcp47

# Common fields with descriptions.
BBox = Annotated[
    list[float],
    AfterValidator(validate_bbox_order),
    Field(
        description="Bounding box [x0, y0, x1, y1] in absolute pixels (px) relative to the image dimensions.",
        max_length=4,
        min_length=4,
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

    col_span: int = Field(1, description="Number of columns this cell spans.", ge=1)
    row_span: int = Field(1, description="Number of rows this cell spans.", ge=1)
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
            f"Semantic continuity of this table across page boundaries: "
            f"'{ItemBoundary.RESUMED.value}' if this table is a continuation from the previous page; "
            f"'{ItemBoundary.TRUNCATED.value}' if it continues onto the next page; "
            f"'{ItemBoundary.BOTH.value}' if it continues from prev and to next; "
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
        None, description="Explicit curriculum code if present (e.g., 'Table 1.2')."
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

        if self.header_row_count < 0:
            raise ValueError(
                f"header_row_count ({self.header_row_count}) cannot be negative."
            )
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
                f"repeats_header is only allowed when boundary is "
                f"{ItemBoundary.RESUMED.value}/{ItemBoundary.BOTH.value}."
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
        - Preserve the presence of the figure,
        - Support later optional diagram interpretation,
        - Keep strong provenance via bbox.
    """

    alt_text: Optional[str] = Field(
        None,
        description="Very short, non-semantic description of what the figure is (e.g., 'flowchart with arrows', 'pyramid diagram'). Do NOT interpret meaning. Null if unknown.",
        max_length=200,
    )
    caption: Optional[TextUnit] = Field(
        None,
        description="Caption text if it is clearly attached to this figure (e.g., 'Figure 2: ...'). Prefer to extract captions as separate CAPTION blocks; only populate here when unambiguous.",
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
    def validate_contains_text_requires_embedded_text(self) -> FigureUnit:
        """Validate consistency between contains_text and embedded_text.

        Returns
        -------
        FigureUnit
            The passed in FigureUnit.

        Raises
        ------
        ValueError
            If validation fails.
        """

        if self.contains_text is True and self.embedded_text is None:
            raise ValueError(
                "figure.contains_text=true requires figure.embedded_text (best-effort transcription)."
            )
        if self.contains_text is False and self.embedded_text is not None:
            raise ValueError(
                "figure.contains_text=false requires figure.embedded_text=null."
            )

        return self


class CurriculumBlock(BaseModelPageIR):
    """A grouping of text content (paragraph, heading, list, etc.)."""

    bbox: BBox
    block_type: BlockType = Field(..., description="The visual structure of the block.")
    boundary: ItemBoundary = Field(
        ItemBoundary.COMPLETE,
        description=(
            f"Semantic continuity of this block across page boundaries: "
            f"'{ItemBoundary.RESUMED.value}' if it continues from the previous page; "
            f"'{ItemBoundary.TRUNCATED.value}' if it continues onto the next page; "
            f"'{ItemBoundary.BOTH.value}' if both; otherwise '{ItemBoundary.COMPLETE.value}'."
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
        description="Explicit curriculum code if present (e.g., '3.9.4.1', 'SECTION 1'). Extract verbatim.",
    )
    text: Optional[TextUnit] = Field(
        None,
        description="The text content (for headings/paragraphs/captions). Must be null when block_type indicates a list or a figure.",
    )

    @model_validator(mode="after")
    def validate_block_type_figure(self) -> CurriculumBlock:
        """Validate figure block types.

        Returns
        -------
        CurriculumBlock
            The passed in CurriculumBlock.

        Raises
        ------
        ValueError
            If validation fails.
        """

        bt = self.block_type

        # Figure blocks: figure required; text/list_items must be null.
        if bt == BlockType.FIGURE:
            if self.figure is None:
                raise ValueError(
                    f"CurriculumBlock block_type='{bt}' requires figure metadata."
                )
            if self.text is not None:
                raise ValueError(
                    f"CurriculumBlock block_type='{bt}' requires text=null."
                )
            if self.list_items is not None:
                raise ValueError(
                    f"CurriculumBlock block_type='{bt}' requires list_items=null."
                )
            if (
                isinstance(self.figure.alt_text, str)
                and not self.figure.alt_text.strip()
            ):
                raise ValueError(
                    f"CurriculumBlock block_type='{bt}' has figure.alt_text that is an "
                    f"empty string (or whitespace only)."
                )
            if self.figure.caption is not None and not self.figure.caption.text.strip():
                raise ValueError(
                    f"CurriculumBlock block_type='{bt}' has figure.caption.text that "
                    f"is an empty string (or whitespace only)."
                )

        return self

    @model_validator(mode="after")
    def validate_block_type_list(self) -> CurriculumBlock:
        """Validate list block types.

        Returns
        -------
        CurriculumBlock
            The passed in CurriculumBlock.

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
                    f"CurriculumBlock block_type='{bt}' requires non-empty list_items."
                )
            if self.text is not None:
                raise ValueError(
                    f"CurriculumBlock block_type='{bt}' requires text=null."
                )
            if self.figure is not None:
                raise ValueError(
                    f"CurriculumBlock block_type='{bt}' requires figure=null."
                )

        return self

    @model_validator(mode="after")
    def validate_block_type_other(self) -> CurriculumBlock:
        """Validate other block types.

        Returns
        -------
        CurriculumBlock
            The passed in CurriculumBlock.

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
        description="Source PDF filename (no path). This should be populated by the Python pipeline; it may be null during extraction.",
    )

    @model_validator(mode="after")
    def clamp_bboxes_within_image(self) -> PageIR:
        """Clamp item bounding boxes into image bounds. Bounding boxes from vision
        models often drift by a few pixels; clamping makes extraction reliable while
        still preserving usable provenance.

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

    @model_validator(mode="after")
    def propagate_table_codes(self) -> PageIR:
        """Propagate table codes from caption blocks to the subsequent table if
        missing.

        Returns
        -------
        PageIR
            The passed in PageIR with propagated table codes.
        """

        # If a caption block carries a Table code (e.g., "Table 5") and the next
        # non-artifact item is a table, copy that code onto the table itself.
        table_code_re = re.compile(r"(?i)^\s*table\s+\d+(?:\.\d+)*\b")

        for i, cur in enumerate(self.items):
            if (
                not isinstance(cur, CurriculumBlock)
                or cur.block_type != BlockType.CAPTION
            ):
                continue

            code = (cur.local_code or "").strip()
            if not code or not table_code_re.match(code):
                continue

            # Look ahead to the next non-artifact item (sometimes a page number sits
            # between).
            j = i + 1
            while j < len(self.items):
                nxt = self.items[j]
                if (
                    isinstance(nxt, CurriculumBlock)
                    and nxt.block_type == BlockType.ARTIFACT
                ):
                    j += 1
                    continue
                break

            if j >= len(self.items):
                continue

            nxt = self.items[j]
            if isinstance(nxt, CurriculumTable):
                if not (nxt.local_code or "").strip():
                    nxt.local_code = code

        return self


# Schemas for verification.
class PageIRContinuityVerdict(BaseModelPageIR):
    """Schema for page IR continuity verification between two pages."""

    clamped_confidence: float | None = Field(
        None,
        description="Postprocess/effective confidence used for applying edits. If omitted, defaults to `confidence` (clamped to [0, 1]).",
        ge=0.0,
        le=1.0,
    )
    next_page_index: int = Field(..., description="0-based index of the next page.")
    prev_page_index: int = Field(..., description="0-based index of the previous page.")

    # What the model thinks.
    confidence: float = Field(
        ...,
        description="Page continuation confidence score (0.0 to 1.0).",
        ge=0.0,
        le=1.0,
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
    set_next_item_boundary: Optional[ItemBoundary] = Field(
        None,
        description="Boundary state for the first item on the next page. In pairwise verification, do not set 'both'—only 'resumed' (or null).",
    )
    set_next_table_repeats_header: Optional[bool] = Field(
        None,
        description="If continuation_kind is 'table' and the next page contains a repeated header, set this to true. Set to false if the header is not repeated. Null means leave as-is.",
    )
    set_prev_item_boundary: Optional[ItemBoundary] = Field(
        None,
        description="Boundary state for the last item on the previous page. In pairwise verification, do not set 'both'—only 'truncated' (or null).",
    )

    @model_validator(mode="after")
    def inject_default_clamped_confidence(self) -> PageIRContinuityVerdict:
        """Inject default clamped confidence if not provided. Here, we just hard clamp
        into [0, 1] (this is just range-safety, not the veto clamp) so that it is
        always populated for reports and consistent downstream comparisons.

        Returns
        -------
        PageIRContinuityVerdict
            The passed in PageIRContinuityVerdict with clamped_confidence populated.
        """

        if self.clamped_confidence is None:
            self.clamped_confidence = max(0.0, min(1.0, float(self.confidence)))

        return self

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
                ItemBoundary.BOTH,
                ItemBoundary.RESUMED,
            }:
                raise ValueError(
                    f"When is_continuation=true, set_prev_item_boundary cannot be "
                    f"{ItemBoundary.COMPLETE.value}/{ItemBoundary.RESUMED.value}/{ItemBoundary.BOTH.value}."
                )
            if self.set_next_item_boundary in {
                ItemBoundary.COMPLETE,
                ItemBoundary.BOTH,
                ItemBoundary.TRUNCATED,
            }:
                raise ValueError(
                    f"When is_continuation=true, set_next_item_boundary cannot be "
                    f"{ItemBoundary.COMPLETE.value}/{ItemBoundary.TRUNCATED.value}/{ItemBoundary.BOTH.value}."
                )
            if (
                self.continuation_kind != PageContinuationKind.TABLE
                and self.set_next_table_repeats_header is not None
            ):
                raise ValueError(
                    "set_next_table_repeats_header only allowed for table continuations."
                )
        elif (
            self.set_prev_item_boundary is not None
            or self.set_next_item_boundary is not None
            or self.set_next_table_repeats_header is not None
        ):
            raise ValueError(
                "When is_continuation=false, all set_* fields must be null."
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

    @model_validator(mode="after")
    def validate_page_indices(self) -> PageIRContinuityVerdict:
        """Validate page index ordering and adjacency.

        Returns
        -------
        PageIRContinuityVerdict
            The passed in PageIRContinuityVerdict.

        Raises
        ------
        ValueError
            If page indices are not in valid order or not adjacent.
        """

        if self.prev_page_index >= self.next_page_index:
            raise ValueError(
                f"Invalid page index order: prev={self.prev_page_index}, next={self.next_page_index}"
            )
        if self.next_page_index != self.prev_page_index + 1:
            raise ValueError(
                f"Continuity verdict must be for adjacent pages: prev={self.prev_page_index}, next={self.next_page_index}"
            )

        return self
