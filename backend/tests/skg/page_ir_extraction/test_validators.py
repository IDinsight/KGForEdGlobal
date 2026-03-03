"""This is the main module for testing page_ir_extraction/validators.py."""

# Standard Library
from collections.abc import Callable
from typing import Any

# Third Party Library
import pytest

# Package Library
from skg.page_ir_extraction.schemas import (
    Block,
    FigureUnit,
    ListItem,
    PageIR,
    Table,
    TableCell,
    TableRow,
    TextUnit,
)
from skg.page_ir_extraction.validators import (
    PageIRExtractionQualityCtx,
    QualityError,
    _is_full_page_bbox,
    compute_boundary_state_from_items,
    validate_artifacts_are_true_artifacts,
    validate_basic_block_invariants,
    validate_continuity_for_extraction,
    validate_extraction_text_constraints,
    validate_figure_blocks_are_well_formed,
    validate_footnote_blocks_are_plausible,
    validate_full_page_bboxes,
    validate_full_page_figure_requires_double_check,
    validate_gross_reading_order,
    validate_image_dimensions,
    validate_item_bboxes_required_and_in_bounds,
    validate_no_duplicate_item_bboxes,
    validate_placeholder_bboxes,
    validate_table_cells_text_en,
    validate_table_collapse_by_header_body,
    validate_table_has_any_text,
    validate_table_inconsistent_widths,
    validate_table_integrity,
    validate_table_n_cols,
    validate_text_en_is_none,
)
from skg.utils.constants import (
    BlockType,
    FigureKind,
    ItemBoundary,
    PageBoundaryState,
)


@pytest.fixture(name="make_block")
def fixture_make_block(make_text_unit: Callable[..., TextUnit]) -> Callable[..., Block]:
    """Factory fixture for creating Block instances across block types.

    Parameters
    ----------
    make_text_unit
        A factory function for creating TextUnit instances, injected as a dependency.

    Returns
    -------
    Callable[..., Block]
        A factory function that can be called with block parameters to create Block
        instances.
    """

    def _make(
        *,
        bbox: list[float],
        block_type: BlockType,
        boundary: ItemBoundary = ItemBoundary.COMPLETE,
        figure: FigureUnit | None = None,
        list_items: list[ListItem] | None = None,
        local_code: str | None = None,
        text: TextUnit | None = None,
    ) -> Block:
        """Create a Block instance with sensible defaults for text, list_items, and
        figure based on block_type.

        Parameters
        ----------
        bbox
            The bounding box for the block, as a list of [x0, y0, x1, y1].
        block_type
            The type of the block, which determines which fields are relevant.
        boundary
            The boundary state of the block (default is COMPLETE).
        figure
            The FigureUnit for FIGURE blocks (ignored for other block types).
        list_items
            The list of ListItem for LIST blocks (ignored for other block types).
        local_code
            Optional local code for the block, which may be used for certain block
            types.
        text
            The TextUnit for text-containing blocks (ignored for block types that don't
            contain text).

        Returns
        -------
        Block
            A Block instance with the specified parameters and sensible defaults for
            fields not relevant to the block_type.
        """

        # Provide sensible defaults that satisfy schema validators.
        if block_type in {
            BlockType.ARTIFACT,
            BlockType.CAPTION,
            BlockType.FOOTNOTE,
            BlockType.HEADING,
            BlockType.PARAGRAPH,
        }:
            if text is None:
                text = make_text_unit(text="Some text")

            list_items = None
            figure = None

        if block_type == BlockType.LIST:
            if list_items is None:
                list_items = [ListItem(marker="•", text=make_text_unit(text="Item"))]

            text = None
            figure = None

        if block_type == BlockType.FIGURE:
            if figure is None:
                figure = FigureUnit(
                    alt_text="diagram",
                    contains_text=False,
                    figure_kind=FigureKind.DIAGRAM,
                )

            text = None
            list_items = None

        return Block(
            bbox=bbox,
            block_type=block_type,
            boundary=boundary,
            figure=figure,
            kind="block",
            list_items=list_items,
            local_code=local_code,
            text=text,
        )

    return _make


@pytest.fixture(name="make_table")
def fixture_make_table(make_text_unit: Callable[..., TextUnit]) -> Callable[..., Table]:
    """Factory fixture for creating Table instances for validation tests.

    Parameters
    ----------
    make_text_unit
        A factory function for creating TextUnit instances, injected as a dependency.

    Returns
    -------
    Callable[..., Table]
        A factory function that can be called with table parameters to create Table
        instances with sensible defaults for rows and cells.
    """

    def _make(
        *,
        bbox: list[float],
        boundary: ItemBoundary = ItemBoundary.COMPLETE,
        header_row_count: int = 0,
        n_cols: int | None = None,
        rows: list[list[tuple[str | None, int]]] | None = None,
    ) -> Table:
        """Create a Table instance with sensible defaults for rows and cells.

        Parameters
        ----------
        bbox
            The bounding box for the table, as a list of [x0, y0, x1, y1].
        boundary
            The boundary state of the table (default is COMPLETE).
        header_row_count
            The number of header rows in the table (default is 0).
        n_cols
            The number of columns in the table (if None, it will be inferred from rows).
        rows
            A list of rows, where each row is a list of tuples containing cell text (or
            None) and column span. If None, a default 2x2 table with simple text will
            be created.

        Returns
        -------
        Table
            A Table instance with the specified parameters and sensible defaults for
            rows and cells.
        """

        # rows: list of rows; each row is list of (text_or_none, col_span).
        if rows is None:
            rows = [
                [("A1", 1), ("B1", 1)],
                [("A2", 1), ("B2", 1)],
            ]

        table_rows: list[TableRow] = []

        for row in rows:
            cells: list[TableCell] = []

            for cell_text, col_span in row:
                cells.append(
                    TableCell(
                        col_span=col_span,
                        row_span=1,
                        text=(
                            None
                            if cell_text is None
                            else make_text_unit(text=cell_text)
                        ),
                    )
                )

            table_rows.append(TableRow(cells=cells))

        return Table(
            bbox=bbox,
            boundary=boundary,
            header_row_count=header_row_count,
            kind="table",
            n_cols=n_cols,
            rows=table_rows,
        )

    return _make


@pytest.fixture(name="make_text_unit")
def fixture_make_text_unit() -> Callable[..., TextUnit]:
    """Factory fixture for creating TextUnit instances with minimal boilerplate.

    Returns
    -------
    Callable[..., TextUnit]
        A factory function that can be called with text and optional language/text_en
        to create TextUnit instances.
    """

    def _make(
        *, language: str = "en", text: str, text_en: str | None = None
    ) -> TextUnit:
        """Create a TextUnit with the given text and optional language/text_en.

        Parameters
        ----------
        language
            The language code for the text (default is "en").
        text
            The main text content for the TextUnit.
        text_en
            The English text content for the TextUnit, if different from text.

        Returns
        -------
        TextUnit
            A TextUnit instance with the specified text and language.
        """

        return TextUnit(language=language, text=text, text_en=text_en)

    return _make


def _make_ctx(
    *,
    image_height: int,
    image_width: int,
    items: list[Any],
    page_index: int = 0,
    tol: float = 1e-3,
) -> PageIRExtractionQualityCtx:
    """Build a PageIRExtractionQualityCtx without importing the dataclass type directly.

    We keep this helper local to avoid coupling tests to the dataclass import location.

    Parameters
    ----------
    image_height
        The height of the page image, used for validating item bboxes.
    image_width
        The width of the page image, used for validating item bboxes.
    items
        The list of items (blocks, tables, etc.) on the page, used for various
        validators.
    page_index
        The index of the page in the document, used for error messages and certain
        page-specific validations (default is 0).
    tol
        The tolerance for bbox coordinate validation, used in validators that check
        whether item bboxes are within image bounds or match the full page bbox
        (default is 1e-3).

    Returns
    -------
    PageIRExtractionQualityCtx
        A context object containing all the provided parameters and derived fields like
        non_artifact_items and page_ir, which can be passed to validators for testing.
    """

    page_ir = PageIR(
        boundary_state=PageBoundaryState.STANDALONE,
        image_height=image_height,
        image_width=image_width,
        items=items,
        page_index=page_index,
    )
    non_artifact_items = [
        (i, item)
        for i, item in enumerate(items)
        if item.kind != "block" or item.block_type != BlockType.ARTIFACT
    ]
    return PageIRExtractionQualityCtx(
        image_height=image_height,
        image_width=image_width,
        items=items,
        non_artifact_items=non_artifact_items,
        page_bbox=(0.0, 0.0, float(image_width), float(image_height)),
        page_ir=page_ir,
        tol=tol,
        top_level_bboxes=[],
    )


def test__is_full_page_bbox_respects_tolerance() -> None:
    """_is_full_page_bbox should treat small coordinate noise within tol as full-page."""

    page_bbox = (0.0, 0.0, 1000.0, 1000.0)

    assert (
        _is_full_page_bbox(bbox=(0.5, 0.0, 1000.0, 999.6), page_bbox=page_bbox, tol=1.0)
        is True
    )
    assert (
        _is_full_page_bbox(
            bbox=(2.0, 0.0, 1000.0, 1000.0), page_bbox=page_bbox, tol=1.0
        )
        is False
    )


def test_compute_boundary_state_from_items_ignores_artifacts(
    make_block: Callable[..., Block],
) -> None:
    """compute_boundary_state_from_items should ignore ARTIFACT blocks when deriving
    state.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    page_ir = PageIR(
        boundary_state=PageBoundaryState.STANDALONE,
        items=[
            make_block(
                bbox=[0, 0, 100, 50],
                block_type=BlockType.ARTIFACT,
                boundary=ItemBoundary.RESUMED,
            ),
            make_block(
                bbox=[0, 60, 300, 120],
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.COMPLETE,
            ),
        ],
        page_index=0,
    )

    assert compute_boundary_state_from_items(page_ir) == PageBoundaryState.STANDALONE


def test_compute_boundary_state_from_items_detects_from_prev_to_next(
    make_block: Callable[..., Block],
) -> None:
    """compute_boundary_state_from_items should return BOTH when resumed and truncated
    exist.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    page_ir = PageIR(
        boundary_state=PageBoundaryState.STANDALONE,
        items=[
            make_block(
                bbox=[0, 0, 300, 60],
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.RESUMED,
            ),
            make_block(
                bbox=[0, 900, 300, 980],
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.TRUNCATED,
            ),
        ],
        page_index=0,
    )

    assert compute_boundary_state_from_items(page_ir) == PageBoundaryState.BOTH


def test_validate_artifacts_are_true_artifacts_allows_true_artifacts(
    make_block: Callable[..., Block],
) -> None:
    """validate_artifacts_are_true_artifacts should not over-trigger on genuine
    artifacts like page numbers.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(
                bbox=[0, 0, 120, 25],
                block_type=BlockType.ARTIFACT,
                text=TextUnit(language="en", text="Page 3", text_en=None),
            )
        ],
    )

    validate_artifacts_are_true_artifacts(ctx)


def test_validate_artifacts_are_true_artifacts_rejects_local_code(
    make_block: Callable[..., Block],
) -> None:
    """validate_artifacts_are_true_artifacts should reject artifact blocks with
    local_code set.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(
                bbox=[0, 0, 200, 30],
                block_type=BlockType.ARTIFACT,
                local_code="SECTION 1",
                text=TextUnit(language="en", text="Section 1", text_en=None),
            )
        ],
    )

    with pytest.raises(QualityError, match="local_code"):
        validate_artifacts_are_true_artifacts(ctx)


def test_validate_artifacts_are_true_artifacts_rejects_non_artifact_text(
    make_block: Callable[..., Block],
) -> None:
    """validate_artifacts_are_true_artifacts should reject common structural headings
    mislabeled as ARTIFACT.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(
                bbox=[0, 0, 200, 30],
                block_type=BlockType.ARTIFACT,
                text=TextUnit(language="en", text="Contents", text_en=None),
            )
        ],
    )

    with pytest.raises(QualityError, match="Section titles"):
        validate_artifacts_are_true_artifacts(ctx)


def test_validate_artifacts_are_true_artifacts_rejects_section_regex(
    make_block: Callable[..., Block],
) -> None:
    """validate_artifacts_are_true_artifacts should treat 'Section X' patterns as
    headings, not artifacts.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(
                bbox=[0, 0, 400, 40],
                block_type=BlockType.ARTIFACT,
                text=TextUnit(language="en", text="Section Two", text_en=None),
            )
        ],
    )

    with pytest.raises(QualityError, match="section heading"):
        validate_artifacts_are_true_artifacts(ctx)


def test_validate_basic_block_invariants_allows_short_list_item_with_marker(
    make_block: Callable[..., Block], make_text_unit: Callable[..., TextUnit]
) -> None:
    """validate_basic_block_invariants should allow short list items when a marker is
    present.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    make_text_unit
        Factory fixture for creating TextUnit instances.
    """

    list_item = ListItem(marker="•", text=make_text_unit(text="a"))
    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(
                bbox=[0, 100, 400, 200],
                block_type=BlockType.LIST,
                list_items=[list_item],
            )
        ],
    )

    validate_basic_block_invariants(ctx)


def test_validate_basic_block_invariants_rejects_short_list_item_without_marker(
    make_block: Callable[..., Block], make_text_unit: Callable[..., TextUnit]
) -> None:
    """validate_basic_block_invariants should reject markerless list items with
    extremely short text.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    make_text_unit
        Factory fixture for creating TextUnit instances.
    """

    list_item = ListItem(marker=None, text=make_text_unit(text="a"))
    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(
                bbox=[0, 100, 400, 200],
                block_type=BlockType.LIST,
                list_items=[list_item],
            )
        ],
    )

    with pytest.raises(QualityError, match="insufficient text"):
        validate_basic_block_invariants(ctx)


def test_validate_basic_block_invariants_rejects_text_en_in_list_items(
    make_block: Callable[..., Block], make_text_unit: Callable[..., TextUnit]
) -> None:
    """validate_basic_block_invariants should enforce text_en=null for list item text
    during extraction.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    make_text_unit
        Factory fixture for creating TextUnit instances.
    """

    list_item = ListItem(
        marker="1.", text=make_text_unit(text="Alpha", text_en="Alpha (EN)")
    )
    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(
                bbox=[0, 100, 400, 200],
                block_type=BlockType.LIST,
                list_items=[list_item],
            )
        ],
    )

    with pytest.raises(QualityError, match="text_en must be null"):
        validate_basic_block_invariants(ctx)


def test_validate_continuity_for_extraction_accepts_both_with_markers_near_edges(
    make_block: Callable[..., Block],
) -> None:
    """validate_continuity_for_extraction should accept BOTH when markers are placed
    near the edges.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    items = [
        make_block(
            bbox=[0, 10, 300, 60],
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.RESUMED,
        ),
        make_block(
            bbox=[0, 100, 300, 160],
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.COMPLETE,
        ),
        make_block(
            bbox=[0, 920, 300, 980],
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.TRUNCATED,
        ),
    ]
    ctx = _make_ctx(image_height=1000, image_width=1000, items=items)

    validate_continuity_for_extraction(ctx)


def test_validate_figure_blocks_are_well_formed_rejects_caption_text_en(
    make_block: Callable[..., Block], make_text_unit: Callable[..., TextUnit]
) -> None:
    """validate_figure_blocks_are_well_formed should enforce text_en=null for figure
    captions.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    make_text_unit
        Factory fixture for creating TextUnit instances.
    """

    fig = FigureUnit(
        alt_text="diagram with labels",
        caption=make_text_unit(text="Figure 1", text_en="Figure one"),
        contains_text=False,
        figure_kind=FigureKind.DIAGRAM,
    )
    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(bbox=[0, 0, 1000, 1000], block_type=BlockType.FIGURE, figure=fig)
        ],
    )

    with pytest.raises(QualityError, match="figure.caption"):
        validate_figure_blocks_are_well_formed(ctx)


def test_validate_continuity_for_extraction_rejects_inconsistent_ctx_non_artifact_items(
    make_block: Callable[..., Block],
) -> None:
    """validate_continuity_for_extraction should fail loudly if ctx.non_artifact_items
    contradicts page_ir.items.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    items = [
        make_block(
            bbox=[0, 0, 300, 60],
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.RESUMED,
        )
    ]
    page_ir = PageIR(
        boundary_state=PageBoundaryState.STANDALONE, items=items, page_index=0
    )
    ctx = PageIRExtractionQualityCtx(
        image_height=1000,
        image_width=1000,
        items=items,
        non_artifact_items=[],  # Inconsistent on purpose
        page_bbox=(0.0, 0.0, 1000.0, 1000.0),
        page_ir=page_ir,
        tol=1e-3,
        top_level_bboxes=[],
    )

    with pytest.raises(QualityError, match="no non-artifact items"):
        validate_continuity_for_extraction(ctx)


def test_validate_continuity_for_extraction_rejects_missing_resumed_in_first_items(
    make_block: Callable[..., Block],
) -> None:
    """validate_continuity_for_extraction should require resumed items near the top for
    from-prev pages.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    items = [
        make_block(
            bbox=[0, 50 * i, 300, 50 * i + 40],
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.COMPLETE,
        )
        for i in range(6)
    ]
    items[-1] = make_block(
        bbox=[0, 250, 300, 290],
        block_type=BlockType.PARAGRAPH,
        boundary=ItemBoundary.RESUMED,
    )
    ctx = _make_ctx(image_height=1000, image_width=1000, items=items)

    with pytest.raises(QualityError, match="no resumed boundary found"):
        validate_continuity_for_extraction(ctx)


def test_validate_extraction_text_constraints_rejects_text_en_on_block_text(
    make_block: Callable[..., Block], make_text_unit: Callable[..., TextUnit]
) -> None:
    """validate_extraction_text_constraints should enforce text_en=null on block text
    during extraction.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    make_text_unit
        Factory fixture for creating TextUnit instances.
    """

    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(
                bbox=[0, 0, 300, 60],
                block_type=BlockType.PARAGRAPH,
                text=make_text_unit(text="Bonjour", text_en="Hello"),
            )
        ],
    )

    with pytest.raises(QualityError, match="items\\[0\\]\\.text"):
        validate_extraction_text_constraints(ctx)


def test_validate_extraction_text_constraints_rejects_text_en_on_figure_caption(
    make_block: Callable[..., Block], make_text_unit: Callable[..., TextUnit]
) -> None:
    """validate_extraction_text_constraints should enforce text_en=null on figure
    captions during extraction.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    make_text_unit
        Factory fixture for creating TextUnit instances.
    """

    fig = FigureUnit(
        alt_text="diagram",
        caption=make_text_unit(text="Figure 2", text_en="Figure two"),
        contains_text=False,
        figure_kind=FigureKind.DIAGRAM,
    )
    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(bbox=[10, 10, 900, 900], block_type=BlockType.FIGURE, figure=fig)
        ],
    )

    with pytest.raises(QualityError, match="figure\\.caption"):
        validate_extraction_text_constraints(ctx)


def test_validate_footnote_blocks_are_plausible_accepts_plausible_footnote(
    make_block: Callable[..., Block],
) -> None:
    """validate_footnote_blocks_are_plausible should allow bottom-of-page, reasonably
    sized footnotes.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(
                bbox=[0, 820, 1000, 930],
                block_type=BlockType.FOOTNOTE,
                boundary=ItemBoundary.COMPLETE,
            )
        ],
    )

    validate_footnote_blocks_are_plausible(ctx)


def test_validate_figure_blocks_are_well_formed_rejects_embedded_text_text_en(
    make_block: Callable[..., Block], make_text_unit: Callable[..., TextUnit]
) -> None:
    """validate_figure_blocks_are_well_formed should enforce text_en=null for
    embedded_text.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    make_text_unit
        Factory fixture for creating TextUnit instances.
    """

    fig = FigureUnit(
        alt_text="equation-like image",
        contains_text=True,
        embedded_text=make_text_unit(text="E = mc^2", text_en="E equals mc squared"),
        figure_kind=FigureKind.IMAGE,
    )
    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(bbox=[0, 0, 1000, 1000], block_type=BlockType.FIGURE, figure=fig)
        ],
    )

    with pytest.raises(QualityError, match="embedded_text"):
        validate_figure_blocks_are_well_formed(ctx)


def test_validate_footnote_blocks_are_plausible_rejects_footnote_too_high(
    make_block: Callable[..., Block],
) -> None:
    """validate_footnote_blocks_are_plausible should reject footnotes that start too
    high on the page.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(
                bbox=[0, 400, 1000, 480],
                block_type=BlockType.FOOTNOTE,
                boundary=ItemBoundary.COMPLETE,
            )
        ],
    )

    with pytest.raises(QualityError, match="too high"):
        validate_footnote_blocks_are_plausible(ctx)


def test_validate_footnote_blocks_are_plausible_rejects_non_complete_boundary(
    make_block: Callable[..., Block],
) -> None:
    """validate_footnote_blocks_are_plausible should reject footnotes marked as
    resumed/truncated.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(
                bbox=[0, 800, 1000, 950],
                block_type=BlockType.FOOTNOTE,
                boundary=ItemBoundary.TRUNCATED,
            )
        ],
    )

    with pytest.raises(
        QualityError, match="Footnote blocks should usually have boundary"
    ):
        validate_footnote_blocks_are_plausible(ctx)


def test_validate_footnote_blocks_are_plausible_rejects_unusually_tall_footnote(
    make_block: Callable[..., Block],
) -> None:
    """validate_footnote_blocks_are_plausible should reject oversized blocks labeled as
    footnotes.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(
                bbox=[0, 600, 1000, 990],
                block_type=BlockType.FOOTNOTE,
                boundary=ItemBoundary.COMPLETE,
            )
        ],
    )

    with pytest.raises(QualityError, match="unusually tall"):
        validate_footnote_blocks_are_plausible(ctx)


def test_validate_full_page_bboxes_allows_single_full_page_figure_when_valid(
    make_block: Callable[..., Block],
) -> None:
    """validate_full_page_bboxes should allow a single true full-page figure with
    explicit contains_text=false.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    fig = FigureUnit(
        alt_text="photo", contains_text=False, figure_kind=FigureKind.IMAGE
    )
    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(bbox=[0, 0, 1000, 1000], block_type=BlockType.FIGURE, figure=fig)
        ],
    )
    validate_item_bboxes_required_and_in_bounds(ctx)

    validate_full_page_bboxes(ctx)


def test_validate_full_page_bboxes_rejects_contains_text_null(
    make_block: Callable[..., Block],
) -> None:
    """validate_full_page_bboxes should require contains_text to be explicitly
    true/false for full-page figures.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    fig = FigureUnit(alt_text="image", contains_text=None, figure_kind=FigureKind.IMAGE)
    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(bbox=[0, 0, 1000, 1000], block_type=BlockType.FIGURE, figure=fig)
        ],
    )
    validate_item_bboxes_required_and_in_bounds(ctx)

    with pytest.raises(QualityError, match="contains_text is explicitly"):
        validate_full_page_bboxes(ctx)


def test_validate_full_page_bboxes_rejects_contains_text_true(
    make_block: Callable[..., Block], make_text_unit: Callable[..., TextUnit]
) -> None:
    """validate_full_page_bboxes should reject full-page figures that claim to contain
    text.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    make_text_unit
        Factory fixture for creating TextUnit instances.
    """

    fig = FigureUnit(
        alt_text="diagram with lots of text",
        contains_text=True,
        embedded_text=make_text_unit(text="Some text"),
        figure_kind=FigureKind.DIAGRAM,
    )
    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(bbox=[0, 0, 1000, 1000], block_type=BlockType.FIGURE, figure=fig)
        ],
    )
    validate_item_bboxes_required_and_in_bounds(ctx)

    with pytest.raises(QualityError, match="contains_text=true"):
        validate_full_page_bboxes(ctx)


def test_validate_full_page_bboxes_rejects_multiple_full_page_items(
    make_block: Callable[..., Block],
) -> None:
    """validate_full_page_bboxes should reject placeholder full-page bboxes when there
    are multiple items.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    items = [
        make_block(bbox=[0, 0, 1000, 1000], block_type=BlockType.PARAGRAPH),
        make_block(bbox=[0, 0, 1000, 1000], block_type=BlockType.PARAGRAPH),
    ]
    ctx = _make_ctx(image_height=1000, image_width=1000, items=items)
    validate_item_bboxes_required_and_in_bounds(ctx)

    with pytest.raises(QualityError, match="Full-page bbox used as a placeholder"):
        validate_full_page_bboxes(ctx)


def test_validate_full_page_bboxes_rejects_unknown_figure_kind(
    make_block: Callable[..., Block],
) -> None:
    """validate_full_page_bboxes should reject full-page figures with
    figure_kind='unknown'.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    fig = FigureUnit(
        alt_text="image", contains_text=False, figure_kind=FigureKind.UNKNOWN
    )
    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(bbox=[0, 0, 1000, 1000], block_type=BlockType.FIGURE, figure=fig)
        ],
    )
    validate_item_bboxes_required_and_in_bounds(ctx)

    with pytest.raises(QualityError, match="figure_kind='unknown'"):
        validate_full_page_bboxes(ctx)


def test_validate_full_page_figure_requires_double_check_allows_non_scan_like_figure_on_retry(
    make_block: Callable[..., Block],
) -> None:
    """validate_full_page_figure_requires_double_check should allow non-scan-like
    figures on retries.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    fig = FigureUnit(
        alt_text="photo of a map", contains_text=False, figure_kind=FigureKind.MAP
    )
    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(bbox=[0, 0, 1000, 1000], block_type=BlockType.FIGURE, figure=fig)
        ],
    )
    validate_item_bboxes_required_and_in_bounds(ctx)

    validate_full_page_figure_requires_double_check(attempt=1, ctx=ctx)


def test_validate_full_page_figure_requires_double_check_allows_scan_like_when_caption_present(
    make_block: Callable[..., Block], make_text_unit: Callable[..., TextUnit]
) -> None:
    """validate_full_page_figure_requires_double_check should allow scan-like pages if
    a caption provides text evidence.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    make_text_unit
        Factory fixture for creating TextUnit instances.
    """

    fig = FigureUnit(
        alt_text="scanned worksheet",
        caption=make_text_unit(text="Worksheet page"),
        contains_text=False,
        figure_kind=FigureKind.IMAGE,
    )
    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(bbox=[0, 0, 1000, 1000], block_type=BlockType.FIGURE, figure=fig)
        ],
    )
    validate_item_bboxes_required_and_in_bounds(ctx)

    validate_full_page_figure_requires_double_check(attempt=1, ctx=ctx)


def test_validate_full_page_figure_requires_double_check_forces_retry_on_attempt_zero(
    make_block: Callable[..., Block],
) -> None:
    """validate_full_page_figure_requires_double_check should always force a retry on
    attempt 0 for a full-page figure.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    fig = FigureUnit(
        alt_text="image", contains_text=False, figure_kind=FigureKind.IMAGE
    )
    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(bbox=[0, 0, 1000, 1000], block_type=BlockType.FIGURE, figure=fig)
        ],
        page_index=12,
    )
    validate_item_bboxes_required_and_in_bounds(ctx)

    with pytest.raises(
        QualityError, match="Force one retry|Full-page figures are rare"
    ):
        validate_full_page_figure_requires_double_check(attempt=0, ctx=ctx)


def test_validate_full_page_figure_requires_double_check_rejects_scan_like_contains_text_null(
    make_block: Callable[..., Block],
) -> None:
    """validate_full_page_figure_requires_double_check should reject scan-like pages
    with contains_text=null and no other text evidence.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    fig = FigureUnit(
        alt_text="scanned document", contains_text=None, figure_kind=FigureKind.IMAGE
    )
    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(bbox=[0, 0, 1000, 1000], block_type=BlockType.FIGURE, figure=fig)
        ],
        page_index=7,
    )
    validate_item_bboxes_required_and_in_bounds(ctx)

    with pytest.raises(QualityError, match="contains_text is null"):
        validate_full_page_figure_requires_double_check(attempt=1, ctx=ctx)


def test_validate_full_page_figure_requires_double_check_rejects_scan_like_without_text_evidence(
    make_block: Callable[..., Block],
) -> None:
    """validate_full_page_figure_requires_double_check should reject scan-like pages
    with contains_text=false and no caption/embedded text.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    fig = FigureUnit(
        alt_text="scanned page", contains_text=False, figure_kind=FigureKind.IMAGE
    )
    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(bbox=[0, 0, 1000, 1000], block_type=BlockType.FIGURE, figure=fig)
        ],
        page_index=3,
    )
    validate_item_bboxes_required_and_in_bounds(ctx)

    with pytest.raises(QualityError, match="missed text-as-image"):
        validate_full_page_figure_requires_double_check(attempt=1, ctx=ctx)


def test_validate_gross_reading_order_allows_column_shift(
    make_block: Callable[..., Block],
) -> None:
    """validate_gross_reading_order should tolerate upward movement when there is a
    clear column shift right.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    items = [
        make_block(bbox=[0, 100, 300, 150], block_type=BlockType.PARAGRAPH),
        make_block(bbox=[0, 350, 300, 400], block_type=BlockType.PARAGRAPH),
        make_block(bbox=[300, 50, 600, 90], block_type=BlockType.PARAGRAPH),
    ]
    ctx = _make_ctx(image_height=1000, image_width=1000, items=items)

    validate_gross_reading_order(ctx)


def test_validate_gross_reading_order_detects_large_upward_backjump(
    make_block: Callable[..., Block],
) -> None:
    """validate_gross_reading_order should catch obvious reading-order backjumps in a
    single column.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    items = [
        make_block(bbox=[0, 100, 300, 150], block_type=BlockType.PARAGRAPH),
        make_block(bbox=[0, 350, 300, 400], block_type=BlockType.PARAGRAPH),
        make_block(bbox=[0, 50, 300, 90], block_type=BlockType.PARAGRAPH),
    ]
    ctx = _make_ctx(image_height=1000, image_width=1000, items=items)

    with pytest.raises(QualityError, match="reading-order violation"):
        validate_gross_reading_order(ctx)


def test_validate_image_dimensions_rejects_non_positive() -> None:
    """validate_image_dimensions should reject non-positive image dimensions."""

    ctx = _make_ctx(image_height=0, image_width=1000, items=[])

    with pytest.raises(QualityError, match="Invalid image dimensions"):
        validate_image_dimensions(ctx)


def test_validate_item_bboxes_required_and_in_bounds_appends_float_tuples(
    make_block: Callable[..., Block],
) -> None:
    """validate_item_bboxes_required_and_in_bounds should append float tuples in item
    order for later validators.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(bbox=[0, 0, 100, 100], block_type=BlockType.PARAGRAPH),
            make_block(bbox=[10, 200, 300, 260], block_type=BlockType.PARAGRAPH),
        ],
    )

    validate_item_bboxes_required_and_in_bounds(ctx)

    assert ctx.top_level_bboxes == [
        (0.0, 0.0, 100.0, 100.0),
        (10.0, 200.0, 300.0, 260.0),
    ]


def test_validate_no_duplicate_item_bboxes_rejects_exact_duplicates(
    make_block: Callable[..., Block],
) -> None:
    """validate_no_duplicate_item_bboxes should reject any exact duplicate item-level
    bbox.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[
            make_block(bbox=[0, 0, 100, 100], block_type=BlockType.PARAGRAPH),
            make_block(bbox=[0, 0, 100, 100], block_type=BlockType.PARAGRAPH),
        ],
    )
    validate_item_bboxes_required_and_in_bounds(ctx)

    with pytest.raises(QualityError, match="Duplicate item bboxes"):
        validate_no_duplicate_item_bboxes(ctx)


def test_validate_item_bboxes_required_and_in_bounds_rejects_out_of_bounds(
    make_block: Callable[..., Block],
) -> None:
    """validate_item_bboxes_required_and_in_bounds should reject bboxes that exceed
    image bounds beyond tol.

    Parameters
    ----------
    make_block
        Factory fixture for creating Block instances.
    """

    ctx = _make_ctx(
        image_height=1000,
        image_width=1000,
        items=[make_block(bbox=[-10, 0, 100, 100], block_type=BlockType.PARAGRAPH)],
        tol=1e-3,
    )

    with pytest.raises(QualityError, match="Out-of-bounds bbox"):
        validate_item_bboxes_required_and_in_bounds(ctx)


def test_validate_placeholder_bboxes_rejects_near_full_page_placeholders() -> None:
    """validate_placeholder_bboxes should reject a frequently reused near-full-page
    bbox.

    NB: The validator only checks the *most common* bbox. So we ensure the near-full
    bbox is the most common entry and that all other bboxes are unique.
    """

    ctx = _make_ctx(image_height=1000, image_width=1000, items=[])
    near_full = (10.0, 10.0, 990.0, 990.0)  # area_frac ~= 0.96 >= 0.85

    # Make near_full the most common bbox with frac = 5 / 20 = 0.25 >= 0.20. All others
    # are unique so nothing else becomes "most common".
    other_bboxes = [
        (float(i), float(i + 1), float(i + 20), float(i + 30)) for i in range(15)
    ]
    ctx.top_level_bboxes = [near_full] * 5 + other_bboxes

    with pytest.raises(QualityError, match=r"near-full-page bbox"):
        validate_placeholder_bboxes(ctx)


def test_validate_placeholder_bboxes_rejects_origin_anchored_placeholders() -> None:
    """validate_placeholder_bboxes should reject a frequently reused origin-anchored bbox.

    NB: The validator only checks the *most common* bbox. So we ensure the origin bbox
    is the most common entry and that all other bboxes are unique.
    """

    ctx = _make_ctx(image_height=1000, image_width=1000, items=[])
    origin_bbox = (0.0, 0.0, 100.0, 100.0)

    # Make origin_bbox the most common bbox with frac = 7 / 20 = 0.35 >= 0.30. All
    # others are unique and do *not* start at origin.
    other_bboxes = [
        (float(i + 1), float(i + 2), float(i + 30), float(i + 45)) for i in range(13)
    ]
    ctx.top_level_bboxes = [origin_bbox] * 7 + other_bboxes

    with pytest.raises(QualityError, match=r"origin-anchored bbox"):
        validate_placeholder_bboxes(ctx)


def test_validate_table_cells_text_en_rejects_any_translation(
    make_text_unit: Callable[..., TextUnit],
) -> None:
    """validate_table_cells_text_en should reject any table cell where text_en is
    populated.

    Parameters
    ----------
    make_text_unit
        Factory fixture for creating TextUnit instances.
    """

    rows = [
        TableRow(
            cells=[
                TableCell(
                    col_span=1, row_span=1, text=make_text_unit(text="A", text_en="A")
                )
            ]
        ),
    ]

    with pytest.raises(QualityError, match="cells\\[0\\]"):
        validate_table_cells_text_en(index=0, rows=rows)


def test_validate_table_collapse_by_header_body_detects_likely_collapse() -> None:
    """validate_table_collapse_by_header_body should flag tables whose body collapses
    into single-cell rows.
    """

    cell_counts = [3, 1, 1, 1, 1, 2]
    eff_widths = [3, 1, 1, 1, 1, 2]

    with pytest.raises(QualityError, match="likely collapsed"):
        validate_table_collapse_by_header_body(
            cell_counts=cell_counts, eff_widths=eff_widths, header_row_count=1, index=2
        )


def test_validate_table_has_any_text_rejects_all_empty_cells() -> None:
    """validate_table_has_any_text should reject tables where all cells are text=null
    or whitespace.
    """

    rows = [
        TableRow(cells=[TableCell(col_span=1, row_span=1, text=None)]),
        TableRow(cells=[TableCell(col_span=1, row_span=1, text=None)]),
    ]

    with pytest.raises(QualityError, match="contains no text content"):
        validate_table_has_any_text(index=0, rows=rows)


def test_validate_table_inconsistent_widths_detects_mostly_single_column_when_max_eff_large() -> (
    None
):
    """validate_table_inconsistent_widths should flag tables with max_eff>=4 but most
    rows width=1.
    """

    eff_widths = [4, 1, 1, 1, 1, 1, 1, 1]

    with pytest.raises(QualityError, match="mostly single-column"):
        validate_table_inconsistent_widths(eff_widths=eff_widths, index=0, max_eff=4)


def test_validate_table_integrity_passes_on_well_formed_table(
    make_table: Callable[..., Table],
) -> None:
    """validate_table_integrity should accept a reasonable table with text and
    consistent widths.

    Parameters
    ----------
    make_table
        Factory fixture for creating Table instances.
    """

    table = make_table(
        bbox=[10, 100, 990, 600],
        header_row_count=0,
        n_cols=2,
        rows=[
            [("H1", 1), ("H2", 1)],
            [("A", 1), ("B", 1)],
            [("C", 1), ("D", 1)],
        ],
    )
    ctx = _make_ctx(image_height=1000, image_width=1000, items=[table])

    validate_table_integrity(ctx)


def test_validate_table_integrity_surfaces_collapse_errors(
    make_table: Callable[..., Table],
) -> None:
    """validate_table_integrity should surface collapse-by-header/body failures on
    collapsed grids.

    Parameters
    ----------
    make_table
        Factory fixture for creating Table instances.
    """

    table = make_table(
        bbox=[10, 100, 990, 600],
        header_row_count=1,
        n_cols=None,
        rows=[
            [("H1", 1), ("H2", 1), ("H3", 1)],
            [("A B C", 1)],
            [("D E F", 1)],
            [("G H I", 1)],
            [("J K L", 1)],
        ],
    )
    ctx = _make_ctx(image_height=1000, image_width=1000, items=[table])

    with pytest.raises(QualityError, match="likely collapsed"):
        validate_table_integrity(ctx)


def test_validate_table_n_cols_rejects_implausibly_large() -> None:
    """validate_table_n_cols should reject suspiciously large column counts."""

    with pytest.raises(QualityError, match="Suspicious n_cols"):
        validate_table_n_cols(index=0, n_cols=51)


def test_validate_table_n_cols_rejects_non_int() -> None:
    """validate_table_n_cols should reject non-int values (defense-in-depth vs. schema
    drift).
    """

    with pytest.raises(QualityError, match="n_cols must be an int"):
        validate_table_n_cols(index=0, n_cols="3")


def test_validate_text_en_is_none_allows_none_text_unit() -> None:
    """validate_text_en_is_none should no-op when the TextUnit is None."""

    validate_text_en_is_none(text=None, where_="items[0].text")


def test_validate_text_en_is_none_rejects_populated_translation(
    make_text_unit: Callable[..., TextUnit],
) -> None:
    """validate_text_en_is_none should reject populated text_en with a useful pointer.

    Parameters
    ----------
    make_text_unit
        Factory fixture for creating TextUnit instances.
    """

    with pytest.raises(QualityError, match="text_en must be null"):
        validate_text_en_is_none(
            text=make_text_unit(text="Hola", text_en="Hello"), where_="items[0].text"
        )
