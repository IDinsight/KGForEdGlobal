"""This is the main module for testing page_ir_verification/compile_continuity.py."""

# Third Party Library
import pytest

# Package Library
from skg.page_ir_extraction.schemas import (
    Block,
    PageIR,
    Table,
    TableCell,
    TableRow,
    TextUnit,
)
from skg.page_ir_verification import compile_continuity
from skg.page_ir_verification.schemas import PageIRContinuityVerdict
from skg.page_ir_verification.utils import EdgeVerdictRecord
from skg.utils.constants import BlockType, ItemBoundary, PageContinuationKind


def make_block(*, boundary: ItemBoundary, local_code: str | None, text: str) -> Block:
    """Build a minimal valid text block for continuity compilation tests.

    Parameters
    ----------
    boundary
        The boundary type to assign to the block.
    local_code
        The local code to assign to the block, which will be normalized by
        _initialize_states.
    text
        The text content of the block, which is required for it to be valid.

    Returns
    -------
    Block
        A Block instance with the specified properties and a fixed bounding box.
    """

    return Block(
        bbox=(0.0, 0.0, 100.0, 40.0),
        block_type=BlockType.PARAGRAPH,
        boundary=boundary,
        kind="block",
        local_code=local_code,
        text=TextUnit(language="en", text=text),
    )


def make_edge_verdict_record(
    *,
    confidence: float,
    continuation_kind: PageContinuationKind,
    is_continuation: bool,
    next_item_index: int,
    next_page_index: int,
    prev_item_index: int,
    prev_page_index: int,
    set_next_table_repeats_header: bool | None,
) -> EdgeVerdictRecord:
    """Build a minimal valid edge verdict record for continuity compile tests.

    Parameters
    ----------
    confidence
        The verification confidence to assign to the verdict.
    continuation_kind
        The semantic continuation kind for the verdict.
    is_continuation
        Whether the verdict marks the page pair as a continuation.
    next_item_index
        The candidate item index on the next page.
    next_page_index
        The next page index for the edge.
    prev_item_index
        The candidate item index on the previous page.
    prev_page_index
        The previous page index for the edge.
    set_next_table_repeats_header
        Optional table header patch carried by the verdict.

    Returns
    -------
    EdgeVerdictRecord
        A minimal valid record suitable for deduplication and sorting tests.
    """

    verdict = PageIRContinuityVerdict(
        confidence=confidence,
        continuation_kind=continuation_kind,
        is_continuation=is_continuation,
        next_page_index=next_page_index,
        prev_page_index=prev_page_index,
        rationale=(
            "This rationale is intentionally long enough to satisfy the schema and "
            "keeps the test focused on compile-time record handling behavior."
        ),
        set_next_table_repeats_header=set_next_table_repeats_header,
    )
    return EdgeVerdictRecord(
        next_item_index=next_item_index,
        next_page_index=next_page_index,
        prev_item_index=prev_item_index,
        prev_page_index=prev_page_index,
        verdict=verdict,
    )


def make_page_ir(*, items: list[Block | Table], page_index: int) -> PageIR:
    """Build a minimal PageIR containing the provided items.

    Parameters
    ----------
    items
        A list of Block and Table items to include in the PageIR. Each item should have
        its own boundary and local_code properties set as needed for testing.
    page_index
        The page index to assign to the PageIR, which is used for sorting and keying in
        _initialize_states.

    Returns
    -------
    PageIR
        A PageIR instance containing the provided items and a fixed boundary state of
        "standalone". The items will be enumerated in the order they appear in the list
        when processed by _initialize_states.
    """

    return PageIR(boundary_state="standalone", items=items, page_index=page_index)


def make_table(*, boundary: ItemBoundary, local_code: str | None) -> Table:
    """Build a minimal valid table for continuity compilation tests.

    Parameters
    ----------
    boundary
        The boundary type to assign to the table.
    local_code
        The local code to assign to the table, which will be normalized by
        _initialize_states.

    Returns
    -------
    Table
        A Table instance with the specified properties, a fixed bounding box, and a
        single cell with placeholder text. The cell content is not relevant for
        continuity testing, but the presence of at least one cell is necessary for the
        table to be considered valid in the context of the PageIR schema.
    """

    return Table(
        bbox=(0.0, 40.0, 100.0, 100.0),
        boundary=boundary,
        header_row_count=0,
        kind="table",
        local_code=local_code,
        rows=[TableRow(cells=[TableCell(text=TextUnit(language="en", text="cell"))])],
    )


def test_deduplicate_and_sort_edge_records_collapses_exact_duplicates() -> None:
    """It should keep one record and report a resolution for exact duplicates."""

    duplicate_a = make_edge_verdict_record(
        confidence=0.91,
        continuation_kind=PageContinuationKind.TEXT,
        is_continuation=True,
        next_item_index=0,
        next_page_index=1,
        prev_item_index=2,
        prev_page_index=0,
        set_next_table_repeats_header=None,
    )
    duplicate_b = make_edge_verdict_record(
        confidence=0.91,
        continuation_kind=PageContinuationKind.TEXT,
        is_continuation=True,
        next_item_index=0,
        next_page_index=1,
        prev_item_index=2,
        prev_page_index=0,
        set_next_table_repeats_header=None,
    )

    sorted_edge_records, boundary_duplicate_resolutions = (
        compile_continuity._deduplicate_and_sort_edge_records(
            edge_records=[duplicate_a, duplicate_b]
        )
    )
    assert sorted_edge_records == [duplicate_a]
    assert boundary_duplicate_resolutions == [
        {
            "discarded_count": 1,
            "next_page": 1,
            "prev_page": 0,
            "reason": "collapsed_exact_duplicate_edge_records",
            "selected": {
                "confidence": 0.91,
                "continuation_kind": "text",
                "is_continuation": True,
                "next_index": 0,
                "prev_index": 2,
                "set_next_table_repeats_header": None,
            },
        }
    ]


def test_deduplicate_and_sort_edge_records_raises_for_conflicting_duplicates() -> None:
    """It should raise when one boundary has multiple non-identical records."""

    lower_confidence = make_edge_verdict_record(
        confidence=0.82,
        continuation_kind=PageContinuationKind.TEXT,
        is_continuation=True,
        next_item_index=1,
        next_page_index=4,
        prev_item_index=3,
        prev_page_index=3,
        set_next_table_repeats_header=None,
    )
    higher_confidence = make_edge_verdict_record(
        confidence=0.95,
        continuation_kind=PageContinuationKind.TEXT,
        is_continuation=True,
        next_item_index=1,
        next_page_index=4,
        prev_item_index=3,
        prev_page_index=3,
        set_next_table_repeats_header=None,
    )

    with pytest.raises(ValueError, match="Conflicting edge records detected"):
        compile_continuity._deduplicate_and_sort_edge_records(
            edge_records=[higher_confidence, lower_confidence]
        )


def test_deduplicate_and_sort_edge_records_raises_for_non_adjacent_pages() -> None:
    """It should reject retained records whose page indexes are not adjacent."""

    non_adjacent = make_edge_verdict_record(
        confidence=0.9,
        continuation_kind=PageContinuationKind.TEXT,
        is_continuation=True,
        next_item_index=0,
        next_page_index=3,
        prev_item_index=1,
        prev_page_index=0,
        set_next_table_repeats_header=None,
    )

    with pytest.raises(ValueError, match=r"Non-adjacent edge record\(s\) detected"):
        compile_continuity._deduplicate_and_sort_edge_records(
            edge_records=[non_adjacent]
        )


def test_initialize_states_normalizes_local_codes_for_blocks_and_tables() -> None:
    """It should strip local_code whitespace and coerce blank values to None."""

    page_irs = {
        0: make_page_ir(
            items=[
                make_block(
                    boundary=ItemBoundary.COMPLETE,
                    local_code="  SEC-1  ",
                    text="heading",
                ),
                make_table(boundary=ItemBoundary.RESUMED, local_code="   "),
                make_block(
                    boundary=ItemBoundary.TRUNCATED, local_code=None, text="body"
                ),
            ],
            page_index=0,
        ),
    }
    bools, normalized_local_codes = compile_continuity._initialize_states(
        page_irs=page_irs
    )

    assert bools == {
        (0, 0): [False, False],
        (0, 1): [True, False],
        (0, 2): [False, True],
    }
    assert normalized_local_codes == {(0, 0): "SEC-1", (0, 1): None, (0, 2): None}


def test_initialize_states_processes_pages_in_sorted_page_index_order() -> None:
    """It should enumerate items using sorted page indexes regardless of input dict order."""

    page_irs = {
        2: make_page_ir(
            items=[
                make_block(
                    boundary=ItemBoundary.TRUNCATED,
                    local_code="  B-2 ",
                    text="later page",
                ),
            ],
            page_index=2,
        ),
        0: make_page_ir(
            items=[
                make_block(
                    boundary=ItemBoundary.COMPLETE,
                    local_code=" A-1 ",
                    text="earlier page",
                ),
            ],
            page_index=0,
        ),
    }

    bools, normalized_local_codes = compile_continuity._initialize_states(
        page_irs=page_irs
    )

    assert list(bools.keys()) == [(0, 0), (2, 0)]
    assert list(normalized_local_codes.keys()) == [(0, 0), (2, 0)]
    assert bools[(0, 0)] == [False, False]
    assert bools[(2, 0)] == [False, True]
    assert normalized_local_codes[(0, 0)] == "A-1"
    assert normalized_local_codes[(2, 0)] == "B-2"


def test_initialize_states_returns_boundary_flags_for_each_item() -> None:
    """It should convert each item boundary into the expected boolean flag pair."""

    page_irs = {
        0: make_page_ir(
            items=[
                make_block(
                    boundary=ItemBoundary.COMPLETE, local_code=None, text="complete"
                ),
                make_block(
                    boundary=ItemBoundary.RESUMED, local_code=None, text="resumed"
                ),
                make_block(
                    boundary=ItemBoundary.TRUNCATED, local_code=None, text="truncated"
                ),
                make_table(boundary=ItemBoundary.BOTH, local_code=None),
            ],
            page_index=0,
        ),
    }
    bools, normalized_local_codes = compile_continuity._initialize_states(
        page_irs=page_irs
    )

    assert bools == {
        (0, 0): [False, False],
        (0, 1): [True, False],
        (0, 2): [False, True],
        (0, 3): [True, True],
    }
    assert normalized_local_codes == {
        (0, 0): None,
        (0, 1): None,
        (0, 2): None,
        (0, 3): None,
    }
