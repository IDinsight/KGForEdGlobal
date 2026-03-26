"""This is the main module for testing document_ir/compute_page_break_links.py."""

# Standard Library
from dataclasses import dataclass
from typing import Any

# Third Party Library
import pytest

# Package Library
from skg.document_ir import compute_page_break_links
from skg.page_ir_extraction.schemas import (
    Block,
    FigureUnit,
    PageIR,
    Table,
    TableCell,
    TableRow,
    TextUnit,
)
from skg.utils.constants import (
    BlockType,
    FigureKind,
    ItemBoundary,
    PageBoundaryState,
    PageContinuationKind,
)


@dataclass
class FakeVerdict:
    """Minimal verdict object for page-break-link tests."""

    confidence: float
    continuation_kind: PageContinuationKind
    is_continuation: bool
    set_next_table_repeats_header: bool | None = None


@dataclass
class FakeEdgeVerdictRecord:
    """Minimal edge-record object for page-break-link tests."""

    next_item_index: int
    prev_item_index: int
    verdict: FakeVerdict


def make_artifact_block(
    *,
    bbox: list[float],
    boundary: ItemBoundary = ItemBoundary.COMPLETE,
    text: str = "artifact",
) -> Block:
    """Create an artifact block."""

    return Block(
        bbox=bbox,
        block_type=BlockType.ARTIFACT,
        boundary=boundary,
        kind="block",
        local_code=None,
        text=make_text_unit(text=text),
    )


def make_block(
    *,
    bbox: list[float],
    block_type: BlockType,
    boundary: ItemBoundary = ItemBoundary.COMPLETE,
    local_code: str | None = None,
    text: str = "text",
) -> Block:
    """Create a non-list, non-figure block with text."""

    return Block(
        bbox=bbox,
        block_type=block_type,
        boundary=boundary,
        kind="block",
        local_code=local_code,
        text=make_text_unit(text=text),
    )


def make_figure_block(
    *,
    alt_text: str = "figure",
    bbox: list[float],
    boundary: ItemBoundary = ItemBoundary.COMPLETE,
    local_code: str | None = None,
) -> Block:
    """Create a figure block."""

    return Block(
        bbox=bbox,
        block_type=BlockType.FIGURE,
        boundary=boundary,
        figure=FigureUnit(
            alt_text=alt_text,
            caption=None,
            contains_text=None,
            embedded_text=None,
            figure_kind=FigureKind.UNKNOWN,
        ),
        kind="block",
        local_code=local_code,
    )


def make_indexed_items(
    *,
    items: list[Block | Table],
    start: int = 0,
) -> list[tuple[int, Block | Table]]:
    """Create an indexed item list matching normalize_page_items() output shape."""

    return [(index + start, item) for index, item in enumerate(items)]


def make_list_block(
    *,
    bbox: list[float],
    boundary: ItemBoundary = ItemBoundary.COMPLETE,
    local_code: str | None = None,
    text: str = "item",
) -> Block:
    """Create a list block."""

    # Package Library
    from skg.page_ir_extraction.schemas import ListItem

    return Block(
        bbox=bbox,
        block_type=BlockType.LIST,
        boundary=boundary,
        kind="block",
        list_items=[ListItem(marker=None, text=make_text_unit(text=text))],
        local_code=local_code,
    )


def make_page_ir(
    *,
    boundary_state: PageBoundaryState = PageBoundaryState.STANDALONE,
    image_height: int = 1000,
    image_width: int = 800,
    items: list[Block | Table] | None = None,
    page_index: int = 0,
) -> PageIR:
    """Create a PageIR for testing."""

    return PageIR(
        boundary_state=boundary_state,
        coord_space="px",
        doc_key="doc",
        dpi=200,
        image_height=image_height,
        image_width=image_width,
        items=[] if items is None else items,
        page_index=page_index,
        pdf_name="test.pdf",
    )


def make_table(
    *,
    bbox: list[float],
    boundary: ItemBoundary = ItemBoundary.COMPLETE,
    header_row_count: int = 1,
    local_code: str | None = None,
    n_cols: int | None = 2,
    repeats_header: bool | None = None,
    rows: list[TableRow] | None = None,
) -> Table:
    """Create a table."""

    return Table(
        bbox=bbox,
        boundary=boundary,
        header_row_count=header_row_count,
        kind="table",
        local_code=local_code,
        n_cols=n_cols,
        repeats_header=repeats_header,
        rows=(
            [make_table_row(values=["Header A", "Header B"])] if rows is None else rows
        ),
    )


def make_table_row(
    *,
    values: list[str],
) -> TableRow:
    """Create a table row from plain strings."""

    return TableRow(
        cells=[
            TableCell(
                col_span=1,
                row_span=1,
                synthetic=False,
                text=make_text_unit(text=value),
            )
            for value in values
        ]
    )


def make_text_unit(
    *,
    language: str = "en",
    text: str,
    text_en: str | None = None,
) -> TextUnit:
    """Create a TextUnit."""

    return TextUnit(
        language=language,
        text=text,
        text_en=text_en,
    )


def make_verdict_record(
    *,
    confidence: float,
    continuation_kind: PageContinuationKind,
    is_continuation: bool,
    next_item_index: int = 0,
    prev_item_index: int = 0,
    set_next_table_repeats_header: bool | None = None,
) -> FakeEdgeVerdictRecord:
    """Create a fake edge verdict record."""

    return FakeEdgeVerdictRecord(
        next_item_index=next_item_index,
        prev_item_index=prev_item_index,
        verdict=FakeVerdict(
            confidence=confidence,
            continuation_kind=continuation_kind,
            is_continuation=is_continuation,
            set_next_table_repeats_header=set_next_table_repeats_header,
        ),
    )


def test__apply_page_boundary_state_guardrails_allows_matching_states() -> None:
    """Guardrails should pass through candidates when both page states allow continuity."""

    current_page_ir = make_page_ir(
        boundary_state=PageBoundaryState.CONTINUES_TO_NEXT,
        page_index=0,
    )
    next_page_ir = make_page_ir(
        boundary_state=PageBoundaryState.CONTINUES_FROM_PREV,
        page_index=1,
    )
    prev_page_items = make_indexed_items(
        items=[
            make_table(bbox=[0.0, 800.0, 100.0, 980.0], boundary=ItemBoundary.TRUNCATED)
        ],
    )
    next_page_items = make_indexed_items(
        items=[
            make_table(bbox=[0.0, 10.0, 100.0, 200.0], boundary=ItemBoundary.RESUMED)
        ],
    )
    warnings: list[str] = []

    filtered_prev, filtered_next, success = (
        compute_page_break_links._apply_page_boundary_state_guardrails(
            current_page_ir=current_page_ir,
            next_candidate_indices=[0],
            next_page_ir=next_page_ir,
            next_page_items=next_page_items,
            prev_candidate_indices=[0],
            prev_page_items=prev_page_items,
            warnings=warnings,
        )
    )

    assert filtered_prev == [0]
    assert filtered_next == [0]
    assert success is True
    assert not warnings


def test__apply_page_boundary_state_guardrails_blocks_without_common_codes() -> None:
    """Guardrails should block stitching when page states disagree and no table code rescues it."""

    current_page_ir = make_page_ir(
        boundary_state=PageBoundaryState.STANDALONE,
        page_index=0,
    )
    next_page_ir = make_page_ir(
        boundary_state=PageBoundaryState.STANDALONE,
        page_index=1,
    )
    prev_page_items = make_indexed_items(
        items=[
            make_table(bbox=[0.0, 800.0, 100.0, 980.0], boundary=ItemBoundary.TRUNCATED)
        ],
    )
    next_page_items = make_indexed_items(
        items=[
            make_table(bbox=[0.0, 10.0, 100.0, 200.0], boundary=ItemBoundary.RESUMED)
        ],
    )
    warnings: list[str] = []

    filtered_prev, filtered_next, success = (
        compute_page_break_links._apply_page_boundary_state_guardrails(
            current_page_ir=current_page_ir,
            next_candidate_indices=[0],
            next_page_ir=next_page_ir,
            next_page_items=next_page_items,
            prev_candidate_indices=[0],
            prev_page_items=prev_page_items,
            warnings=warnings,
        )
    )

    assert not filtered_prev
    assert not filtered_next
    assert success is False
    assert len(warnings) == 1
    assert "guardrail blocked stitching" in warnings[0]


def test__apply_page_boundary_state_guardrails_filters_to_common_table_codes() -> None:
    """Guardrails should rescue only table candidates that share a strong local code."""

    current_page_ir = make_page_ir(
        boundary_state=PageBoundaryState.STANDALONE,
        page_index=0,
    )
    next_page_ir = make_page_ir(
        boundary_state=PageBoundaryState.STANDALONE,
        page_index=1,
    )
    prev_page_items = make_indexed_items(
        items=[
            make_table(
                bbox=[0.0, 800.0, 100.0, 980.0],
                boundary=ItemBoundary.TRUNCATED,
                local_code="Table 4",
            ),
            make_block(
                bbox=[120.0, 820.0, 200.0, 980.0],
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.TRUNCATED,
                text="tail",
            ),
        ],
    )
    next_page_items = make_indexed_items(
        items=[
            make_table(
                bbox=[0.0, 10.0, 100.0, 200.0],
                boundary=ItemBoundary.RESUMED,
                local_code="table 4",
            ),
            make_block(
                bbox=[120.0, 10.0, 200.0, 200.0],
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.RESUMED,
                text="head",
            ),
        ],
    )
    warnings: list[str] = []

    filtered_prev, filtered_next, success = (
        compute_page_break_links._apply_page_boundary_state_guardrails(
            current_page_ir=current_page_ir,
            next_candidate_indices=[0, 1],
            next_page_ir=next_page_ir,
            next_page_items=next_page_items,
            prev_candidate_indices=[0, 1],
            prev_page_items=prev_page_items,
            warnings=warnings,
        )
    )

    assert filtered_prev == [0]
    assert filtered_next == [0]
    assert success is True
    assert not warnings


def test__apply_verification_verdict_creates_link_and_sets_repeats_header() -> None:
    """Verdict application should link the resolved items and mutate repeats_header when requested."""

    current_page_ir = make_page_ir(page_index=2)
    next_page_ir = make_page_ir(page_index=3)
    link_debug: list[dict[str, Any]] = []
    page_pair_debug: list[dict[str, Any]] = []
    prev_page_items = make_indexed_items(
        items=[
            make_table(
                bbox=[0.0, 800.0, 200.0, 980.0],
                boundary=ItemBoundary.TRUNCATED,
                local_code="Table 7",
            ),
        ],
        start=5,
    )
    next_table = make_table(
        bbox=[0.0, 10.0, 200.0, 200.0],
        boundary=ItemBoundary.RESUMED,
        local_code="Table 7",
        repeats_header=None,
    )
    next_page_items = make_indexed_items(
        items=[next_table],
        start=9,
    )
    edge_record = make_verdict_record(
        confidence=0.97,
        continuation_kind=PageContinuationKind.TABLE,
        is_continuation=True,
        next_item_index=9,
        prev_item_index=5,
        set_next_table_repeats_header=True,
    )

    links = compute_page_break_links._apply_verification_verdict(
        current_page_ir=current_page_ir,
        edge_record=edge_record,
        link_debug=link_debug,
        next_page_ir=next_page_ir,
        next_page_items=next_page_items,
        page_pair_debug=page_pair_debug,
        prev_page_items=prev_page_items,
    )

    assert links == {(2, 5): (3, 9)}
    assert next_table.repeats_header is True
    assert link_debug[0]["note"] == "verdict_override"
    assert page_pair_debug[0]["note"] == "verdict_accepted"


def test__apply_verification_verdict_raises_for_kind_mismatch() -> None:
    """Verdict application should assert when resolved items do not match the verdict kind."""

    current_page_ir = make_page_ir(page_index=0)
    edge_record = make_verdict_record(
        confidence=0.99,
        continuation_kind=PageContinuationKind.TABLE,
        is_continuation=True,
        next_item_index=0,
        prev_item_index=0,
    )
    next_page_ir = make_page_ir(page_index=1)
    next_page_items = make_indexed_items(
        items=[make_block(bbox=[0.0, 0.0, 50.0, 40.0], block_type=BlockType.PARAGRAPH)],
    )
    prev_page_items = make_indexed_items(
        items=[
            make_block(bbox=[0.0, 960.0, 50.0, 990.0], block_type=BlockType.PARAGRAPH)
        ],
    )

    with pytest.raises(ValueError, match="continuation_kind"):
        compute_page_break_links._apply_verification_verdict(
            current_page_ir=current_page_ir,
            edge_record=edge_record,
            link_debug=[],
            next_page_ir=next_page_ir,
            next_page_items=next_page_items,
            page_pair_debug=[],
            prev_page_items=prev_page_items,
        )


def test__caption_anchor_falls_back_to_caption_text() -> None:
    """Caption anchors should be derived from caption text when local_code is absent."""

    item = make_block(
        bbox=[0.0, 0.0, 50.0, 20.0],
        block_type=BlockType.CAPTION,
        text="Table 4: Results",
    )

    assert compute_page_break_links._caption_anchor(item) == "table 4"


def test__caption_anchor_prefers_local_code() -> None:
    """Caption anchors should prefer normalized local_code over parsed text."""

    item = make_block(
        bbox=[0.0, 0.0, 50.0, 20.0],
        block_type=BlockType.CAPTION,
        local_code=" Figure 10 ",
        text="Table 4: Results",
    )

    assert compute_page_break_links._caption_anchor(item) == "figure 10"


def test__column_signature_rejects_invalid_mode() -> None:
    """Column signature should assert on unsupported modes."""

    table = make_table(bbox=[0.0, 0.0, 200.0, 100.0])

    with pytest.raises(AssertionError, match="Invalid mode"):
        compute_page_break_links._column_signature(mode="medium", table=table)


def test__column_signature_respects_mode() -> None:
    """Column signatures should use header_row_count for strong mode and the first row for weak mode."""

    table = make_table(
        bbox=[0.0, 0.0, 200.0, 100.0],
        header_row_count=2,
        rows=[
            make_table_row(values=[" Topic ", "Specific Competence"]),
            make_table_row(values=["Expected", "Standard"]),
            make_table_row(values=["A", "B"]),
        ],
    )

    assert (
        compute_page_break_links._column_signature(mode="strong", table=table)
        == "topic|specific competence||expected|standard"
    )
    assert (
        compute_page_break_links._column_signature(mode="weak", table=table)
        == "topic|specific competence"
    )


def test__edge_window_indices_ignores_artifacts_overlays_and_ignorable_blocks() -> None:
    """Edge-window selection should skip ignorable edge clutter and overlay figures."""

    table = make_table(bbox=[0.0, 700.0, 200.0, 980.0])
    overlay_figure = make_figure_block(bbox=[10.0, 750.0, 40.0, 780.0])
    heading = make_block(
        bbox=[0.0, 980.0, 200.0, 995.0],
        block_type=BlockType.HEADING,
        text="Heading",
    )
    artifact = make_artifact_block(bbox=[0.0, 995.0, 200.0, 1000.0])
    paragraph = make_block(
        bbox=[0.0, 650.0, 200.0, 690.0],
        block_type=BlockType.PARAGRAPH,
        text="Paragraph",
    )
    items = make_indexed_items(
        items=[paragraph, table, overlay_figure, heading, artifact]
    )

    indices = compute_page_break_links._edge_window_indices(
        from_end=True,
        items=items,
        max_window_size=2,
    )

    assert indices == {0, 1}


def test__edge_window_indices_returns_all_indices_for_nonpositive_window() -> None:
    """Edge-window selection should return all indices when max_window_size is nonpositive."""

    items = make_indexed_items(
        items=[
            make_block(bbox=[0.0, 0.0, 50.0, 20.0], block_type=BlockType.PARAGRAPH),
            make_block(bbox=[0.0, 30.0, 50.0, 50.0], block_type=BlockType.PARAGRAPH),
        ],
    )

    assert compute_page_break_links._edge_window_indices(
        from_end=False, items=items, max_window_size=0
    ) == {
        0,
        1,
    }


def test__find_paired_candidates_marks_valid_boundary_pair() -> None:
    """Candidate discovery should keep compatible edge-boundary items with only ignorable content between them and the edge."""

    prev_items = make_indexed_items(
        items=[
            make_block(
                bbox=[0.0, 940.0, 200.0, 980.0],
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.TRUNCATED,
                text="Tail text",
            ),
            make_block(
                bbox=[0.0, 981.0, 200.0, 995.0],
                block_type=BlockType.HEADING,
                text="Footer heading",
            ),
        ],
    )
    next_items = make_indexed_items(
        items=[
            make_block(
                bbox=[0.0, 0.0, 200.0, 15.0],
                block_type=BlockType.CAPTION,
                text="Table 2",
            ),
            make_block(
                bbox=[0.0, 20.0, 200.0, 60.0],
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.RESUMED,
                text="Head text",
            ),
        ],
    )

    prev_rejected, prev_valid, _, next_rejected, next_valid, _ = (
        compute_page_break_links._find_paired_candidates(
            next_items=next_items, prev_items=prev_items
        )
    )

    assert not prev_rejected
    assert prev_valid == [0]
    assert not next_rejected
    assert next_valid == [1]


def test__find_paired_candidates_rejects_when_nonignorable_content_intervenes() -> None:
    """Candidate discovery should reject a boundary item when real content blocks the page edge."""

    prev_items = make_indexed_items(
        items=[
            make_block(
                bbox=[0.0, 900.0, 200.0, 940.0],
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.TRUNCATED,
                text="Tail text",
            ),
            make_block(
                bbox=[0.0, 945.0, 200.0, 985.0],
                block_type=BlockType.PARAGRAPH,
                text="Intervening paragraph",
            ),
        ],
    )
    next_items = make_indexed_items(
        items=[
            make_block(
                bbox=[0.0, 0.0, 200.0, 40.0],
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.RESUMED,
                text="Head text",
            ),
        ],
    )

    prev_rejected, prev_valid, _, next_rejected, next_valid, _ = (
        compute_page_break_links._find_paired_candidates(
            next_items=next_items, prev_items=prev_items
        )
    )

    assert prev_rejected == [0]
    assert not prev_valid
    assert not next_rejected
    assert next_valid == [0]


def test__is_embedded_overlay_figure_detects_complete_figure_inside_table() -> None:
    """Embedded overlay detection should return true for a complete figure inside a table bbox."""

    item = make_figure_block(bbox=[10.0, 10.0, 30.0, 30.0])
    tables = [make_table(bbox=[0.0, 0.0, 100.0, 100.0])]

    assert (
        compute_page_break_links._is_embedded_overlay_figure(item=item, tables=tables)
        is True
    )


def test__is_embedded_overlay_figure_rejects_nonfigure_or_noncomplete_item() -> None:
    """Embedded overlay detection should ignore non-figure and non-complete items."""

    noncomplete_figure = make_figure_block(
        bbox=[10.0, 10.0, 30.0, 30.0],
        boundary=ItemBoundary.RESUMED,
    )
    paragraph = make_block(
        bbox=[10.0, 10.0, 30.0, 30.0],
        block_type=BlockType.PARAGRAPH,
        text="text",
    )
    tables = [make_table(bbox=[0.0, 0.0, 100.0, 100.0])]

    assert (
        compute_page_break_links._is_embedded_overlay_figure(
            item=noncomplete_figure, tables=tables
        )
        is False
    )
    assert (
        compute_page_break_links._is_embedded_overlay_figure(
            item=paragraph, tables=tables
        )
        is False
    )


def test__is_vertical_continuation_checks_page_edges() -> None:
    """Vertical continuation should require the previous item near the bottom and the next item near the top."""

    assert (
        compute_page_break_links._is_vertical_continuation(
            edge_frac=0.20,
            next_bbox=[0.0, 5.0, 100.0, 40.0],
            next_page_h=1000,
            prev_bbox=[0.0, 810.0, 100.0, 995.0],
            prev_page_h=1000,
        )
        is True
    )
    assert (
        compute_page_break_links._is_vertical_continuation(
            edge_frac=0.20,
            next_bbox=[0.0, 250.0, 100.0, 300.0],
            next_page_h=1000,
            prev_bbox=[0.0, 810.0, 100.0, 995.0],
            prev_page_h=1000,
        )
        is False
    )


def test__safe_to_ignore_between_pages_handles_expected_item_types() -> None:
    """Between-page ignoring should accept artifacts and ignorable complete blocks but not tables or paragraphs."""

    artifact = make_artifact_block(bbox=[0.0, 0.0, 10.0, 10.0])
    heading = make_block(
        bbox=[0.0, 10.0, 50.0, 20.0],
        block_type=BlockType.HEADING,
        text="Heading",
    )
    paragraph = make_block(
        bbox=[0.0, 20.0, 50.0, 40.0],
        block_type=BlockType.PARAGRAPH,
        text="Paragraph",
    )
    table = make_table(bbox=[0.0, 40.0, 100.0, 80.0])

    assert compute_page_break_links._safe_to_ignore_between_pages(artifact) is True
    assert compute_page_break_links._safe_to_ignore_between_pages(heading) is True
    assert compute_page_break_links._safe_to_ignore_between_pages(paragraph) is False
    assert compute_page_break_links._safe_to_ignore_between_pages(table) is False


def test__safe_to_ignore_between_pages_relative_allows_overlay_figure_inside_table() -> (
    None
):
    """Relative ignoring should allow complete figure overlays inside candidate tables."""

    anchor = make_table(bbox=[0.0, 0.0, 100.0, 100.0])
    item = make_figure_block(bbox=[10.0, 10.0, 20.0, 20.0])

    assert (
        compute_page_break_links._safe_to_ignore_between_pages_relative(
            anchor=anchor, item=item
        )
        is True
    )


def test__safe_to_ignore_between_pages_relative_rejects_nonignorable_outside_item() -> (
    None
):
    """Relative ignoring should reject nonignorable items outside the anchor's contained-overlay exception."""

    anchor = make_table(bbox=[0.0, 0.0, 100.0, 100.0])
    item = make_figure_block(bbox=[110.0, 10.0, 140.0, 20.0])

    assert (
        compute_page_break_links._safe_to_ignore_between_pages_relative(
            anchor=anchor, item=item
        )
        is False
    )


def test__score_block_match_adds_caption_anchor_bonus() -> None:
    """Block scoring should heavily reward matching caption anchors."""

    next_item = make_block(
        bbox=[0.0, 0.0, 100.0, 20.0],
        block_type=BlockType.CAPTION,
        text="Table 4: continued",
    )
    prev_item = make_block(
        bbox=[0.0, 980.0, 100.0, 999.0],
        block_type=BlockType.CAPTION,
        text="Table 4",
    )

    score = compute_page_break_links._score_block_match(
        next_item=next_item,
        next_page_h=1000,
        prev_item=prev_item,
        prev_page_h=1000,
    )

    assert score == pytest.approx(7.0)


def test__score_block_match_scores_textlike_boundary_alignment() -> None:
    """Block scoring should reward textlike type compatibility, boundary alignment, and geometry."""

    next_item = make_list_block(
        bbox=[0.0, 0.0, 100.0, 40.0],
        boundary=ItemBoundary.RESUMED,
        text="continued",
    )
    prev_item = make_block(
        bbox=[0.0, 940.0, 100.0, 999.0],
        block_type=BlockType.PARAGRAPH,
        boundary=ItemBoundary.TRUNCATED,
        text="start",
    )

    score = compute_page_break_links._score_block_match(
        next_item=next_item,
        next_page_h=1000,
        prev_item=prev_item,
        prev_page_h=1000,
    )

    assert score == pytest.approx(4.0)


def test__score_table_match_rewards_local_code_structure_and_geometry() -> None:
    """Table scoring should combine local-code, edge, structure, boundary, and width signals."""

    next_item = make_table(
        bbox=[0.0, 0.0, 100.0, 200.0],
        boundary=ItemBoundary.RESUMED,
        local_code="table 4",
        n_cols=2,
    )
    prev_item = make_table(
        bbox=[0.0, 800.0, 100.0, 999.0],
        boundary=ItemBoundary.TRUNCATED,
        local_code="Table 4",
        n_cols=2,
    )

    score = compute_page_break_links._score_table_match(
        next_item=next_item,
        next_page_h=1000,
        prev_item=prev_item,
        prev_page_h=1000,
    )

    assert score == pytest.approx(8.5)


def test__score_table_match_uses_column_signature_without_local_codes() -> None:
    """Table scoring should use header signatures when local codes are unavailable."""

    rows = [
        make_table_row(values=["Topic", "Specific Competence"]),
        make_table_row(values=["A", "B"]),
    ]
    next_item = make_table(
        bbox=[0.0, 0.0, 120.0, 200.0],
        boundary=ItemBoundary.RESUMED,
        local_code=None,
        n_cols=2,
        rows=rows,
    )
    prev_item = make_table(
        bbox=[0.0, 800.0, 120.0, 999.0],
        boundary=ItemBoundary.TRUNCATED,
        local_code=None,
        n_cols=2,
        rows=rows,
    )

    score = compute_page_break_links._score_table_match(
        next_item=next_item,
        next_page_h=1000,
        prev_item=prev_item,
        prev_page_h=1000,
    )

    assert score == pytest.approx(7.0)


def test_bbox_contains_obeys_tolerance() -> None:
    """BBox containment should respect the configured tolerance."""

    assert (
        compute_page_break_links.bbox_contains(
            inner=[1.0, 1.0, 9.0, 9.0], outer=[0.0, 0.0, 10.0, 10.0], tol=0.0
        )
        is True
    )
    assert (
        compute_page_break_links.bbox_contains(
            inner=[-1.0, 1.0, 9.0, 9.0], outer=[0.0, 0.0, 10.0, 10.0], tol=0.0
        )
        is False
    )
    assert (
        compute_page_break_links.bbox_contains(
            inner=[-1.0, 1.0, 9.0, 9.0], outer=[0.0, 0.0, 10.0, 10.0], tol=1.1
        )
        is True
    )


def test_compute_page_break_links_combines_heuristic_and_verdict_paths() -> None:
    """Top-level link computation should merge links across page pairs and respect verdict overrides."""

    page_0_items = [
        make_block(
            bbox=[0.0, 940.0, 100.0, 999.0],
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.TRUNCATED,
            text="Page 0 tail",
        ),
    ]
    page_1_items = [
        make_block(
            bbox=[0.0, 0.0, 100.0, 50.0],
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.RESUMED,
            text="Page 1 head",
        ),
    ]
    page_2_items = [
        make_block(
            bbox=[0.0, 0.0, 100.0, 50.0],
            block_type=BlockType.PARAGRAPH,
            text="Standalone",
        ),
    ]
    page_irs = [
        make_page_ir(
            boundary_state=PageBoundaryState.CONTINUES_TO_NEXT,
            items=page_0_items,
            page_index=0,
        ),
        make_page_ir(
            boundary_state=PageBoundaryState.BOTH,
            items=page_1_items,
            page_index=1,
        ),
        make_page_ir(
            boundary_state=PageBoundaryState.CONTINUES_FROM_PREV,
            items=page_2_items,
            page_index=2,
        ),
    ]
    items_mapping = {
        0: make_indexed_items(items=page_0_items),
        1: make_indexed_items(items=page_1_items),
        2: make_indexed_items(items=page_2_items),
    }
    verdicts = {
        (0, 1): make_verdict_record(
            confidence=0.10,
            continuation_kind=PageContinuationKind.NONE,
            is_continuation=False,
        ),
        (1, 2): make_verdict_record(
            confidence=0.95,
            continuation_kind=PageContinuationKind.NONE,
            is_continuation=False,
        ),
    }
    link_debug: list[dict[str, Any]] = []
    page_pair_debug: list[dict[str, Any]] = []
    warnings: list[str] = []

    links = compute_page_break_links.compute_page_break_links(
        items_mapping=items_mapping,
        link_debug=link_debug,
        min_link_score=3.0,
        page_irs=page_irs,
        page_pair_debug=page_pair_debug,
        verdict_confidence_threshold=0.90,
        verdicts=verdicts,
        warnings=warnings,
    )

    assert links == {(0, 0): (1, 0)}
    assert len(page_pair_debug) == 2
    assert page_pair_debug[1]["note"] == "verdict_no_continuation"


def test_match_candidates_picks_best_unused_matches() -> None:
    """Candidate matching should greedily assign the best unused next candidate to each previous candidate."""

    current_page_ir = make_page_ir(page_index=0)
    next_page_ir = make_page_ir(page_index=1)
    prev_page_items = make_indexed_items(
        items=[
            make_block(
                bbox=[0.0, 940.0, 100.0, 999.0],
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.TRUNCATED,
                text="Paragraph tail",
            ),
            make_table(
                bbox=[120.0, 760.0, 260.0, 999.0],
                boundary=ItemBoundary.TRUNCATED,
                local_code="Table 5",
                n_cols=2,
            ),
        ],
    )
    next_page_items = make_indexed_items(
        items=[
            make_block(
                bbox=[0.0, 0.0, 100.0, 40.0],
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.RESUMED,
                text="Paragraph head",
            ),
            make_table(
                bbox=[120.0, 0.0, 260.0, 240.0],
                boundary=ItemBoundary.RESUMED,
                local_code="Table 5",
                n_cols=2,
            ),
        ],
    )
    pair_debug: dict[str, Any] = {"chosen_links": []}
    warnings: list[str] = []

    links = compute_page_break_links.match_candidates(
        current_page_ir=current_page_ir,
        link_debug=[],
        min_link_score=3.0,
        next_candidate_indices=[0, 1],
        next_page_ir=next_page_ir,
        next_page_items=next_page_items,
        pair_debug=pair_debug,
        prev_candidate_indices=[0, 1],
        prev_page_items=prev_page_items,
        warnings=warnings,
    )

    assert links == {(0, 0): (1, 0), (0, 1): (1, 1)}
    assert len(pair_debug["chosen_links"]) == 2
    assert not warnings


def test_match_candidates_rejects_weak_match_below_threshold() -> None:
    """Candidate matching should refuse links whose best score is below the threshold."""

    current_page_ir = make_page_ir(page_index=0)
    next_page_ir = make_page_ir(page_index=1)
    prev_page_items = make_indexed_items(
        items=[
            make_block(
                bbox=[0.0, 940.0, 100.0, 999.0],
                block_type=BlockType.PARAGRAPH,
                text="Paragraph tail",
            ),
        ],
    )
    next_page_items = make_indexed_items(
        items=[
            make_block(
                bbox=[0.0, 200.0, 100.0, 250.0],
                block_type=BlockType.PARAGRAPH,
                text="Paragraph head",
            ),
        ],
    )
    pair_debug: dict[str, Any] = {"chosen_links": []}
    warnings: list[str] = []

    links = compute_page_break_links.match_candidates(
        current_page_ir=current_page_ir,
        link_debug=[],
        min_link_score=3.0,
        next_candidate_indices=[0],
        next_page_ir=next_page_ir,
        next_page_items=next_page_items,
        pair_debug=pair_debug,
        prev_candidate_indices=[0],
        prev_page_items=prev_page_items,
        warnings=warnings,
    )

    assert not links
    assert len(warnings) == 1
    assert "Rejected weak continuation match" in warnings[0]


def test_process_page_pair_returns_empty_when_guardrails_fail() -> None:
    """Page-pair processing should return no links when page-level guardrails block stitching."""

    current_page_ir = make_page_ir(
        boundary_state=PageBoundaryState.STANDALONE,
        image_height=1000,
        page_index=0,
    )
    edge_record = make_verdict_record(
        confidence=0.10,
        continuation_kind=PageContinuationKind.NONE,
        is_continuation=False,
    )
    next_page_ir = make_page_ir(
        boundary_state=PageBoundaryState.STANDALONE,
        image_height=1000,
        page_index=1,
    )
    next_page_items = make_indexed_items(
        items=[
            make_block(
                bbox=[0.0, 0.0, 100.0, 40.0],
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.RESUMED,
                text="Head text",
            ),
        ],
    )
    prev_page_items = make_indexed_items(
        items=[
            make_block(
                bbox=[0.0, 940.0, 100.0, 999.0],
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.TRUNCATED,
                text="Tail text",
            ),
        ],
    )
    warnings: list[str] = []

    links = compute_page_break_links.process_page_pair(
        current_page_ir=current_page_ir,
        edge_record=edge_record,
        link_debug=[],
        min_link_score=3.0,
        next_page_ir=next_page_ir,
        next_page_items=next_page_items,
        page_pair_debug=[],
        prev_page_items=prev_page_items,
        verdict_confidence_threshold=0.90,
        warnings=warnings,
    )

    assert not links
    assert len(warnings) == 1
    assert "guardrail blocked stitching" in warnings[0]


def test_process_page_pair_returns_heuristic_link_for_valid_candidates() -> None:
    """Page-pair processing should produce heuristic links when the verdict is below threshold."""

    current_page_ir = make_page_ir(
        boundary_state=PageBoundaryState.CONTINUES_TO_NEXT,
        image_height=1000,
        page_index=0,
    )
    edge_record = make_verdict_record(
        confidence=0.10,
        continuation_kind=PageContinuationKind.NONE,
        is_continuation=False,
    )
    next_page_ir = make_page_ir(
        boundary_state=PageBoundaryState.CONTINUES_FROM_PREV,
        image_height=1000,
        page_index=1,
    )
    next_page_items = make_indexed_items(
        items=[
            make_block(
                bbox=[0.0, 0.0, 100.0, 40.0],
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.RESUMED,
                text="Head text",
            ),
        ],
    )
    page_pair_debug: list[dict[str, Any]] = []
    prev_page_items = make_indexed_items(
        items=[
            make_block(
                bbox=[0.0, 940.0, 100.0, 999.0],
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.TRUNCATED,
                text="Tail text",
            ),
        ],
    )

    links = compute_page_break_links.process_page_pair(
        current_page_ir=current_page_ir,
        edge_record=edge_record,
        link_debug=[],
        min_link_score=3.0,
        next_page_ir=next_page_ir,
        next_page_items=next_page_items,
        page_pair_debug=page_pair_debug,
        prev_page_items=prev_page_items,
        verdict_confidence_threshold=0.90,
        warnings=[],
    )

    assert links == {(0, 0): (1, 0)}
    assert len(page_pair_debug) == 1
    assert page_pair_debug[0]["chosen_links"][0]["score"] == pytest.approx(4.0)


def test_process_page_pair_short_circuits_high_confidence_negative_verdict() -> None:
    """Page-pair processing should skip heuristics when a strong negative verdict is present."""

    current_page_ir = make_page_ir(page_index=0)
    edge_record = make_verdict_record(
        confidence=0.99,
        continuation_kind=PageContinuationKind.NONE,
        is_continuation=False,
    )
    next_page_ir = make_page_ir(page_index=1)
    page_pair_debug: list[dict[str, Any]] = []

    links = compute_page_break_links.process_page_pair(
        current_page_ir=current_page_ir,
        edge_record=edge_record,
        link_debug=[],
        min_link_score=3.0,
        next_page_ir=next_page_ir,
        next_page_items=make_indexed_items(items=[]),
        page_pair_debug=page_pair_debug,
        prev_page_items=make_indexed_items(items=[]),
        verdict_confidence_threshold=0.90,
        warnings=[],
    )

    assert not links
    assert page_pair_debug[0]["note"] == "verdict_no_continuation"
