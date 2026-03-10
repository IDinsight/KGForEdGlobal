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
from tests.constants import PARAM


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


def test_apply_edge_verdicts_applies_confident_edge_and_skips_low_confidence_edge() -> (
    None
):
    """It should mutate state for eligible edges and preserve state for skipped edges."""

    applied_record = make_edge_verdict_record(
        confidence=0.96,
        continuation_kind=PageContinuationKind.TABLE,
        is_continuation=True,
        next_item_index=0,
        next_page_index=1,
        prev_item_index=2,
        prev_page_index=0,
        set_next_table_repeats_header=False,
    )
    skipped_record = make_edge_verdict_record(
        confidence=0.61,
        continuation_kind=PageContinuationKind.TEXT,
        is_continuation=True,
        next_item_index=0,
        next_page_index=2,
        prev_item_index=1,
        prev_page_index=1,
        set_next_table_repeats_header=None,
    )
    bools = {
        (0, 2): [False, False],
        (1, 0): [False, False],
        (1, 1): [True, True],
        (2, 0): [True, True],
    }
    dirty_keys: set[tuple[int, int]] = set()
    effective_local_codes = {
        (0, 2): "TAB-4",
        (1, 0): None,
        (1, 1): None,
        (2, 0): None,
    }
    local_code_conflicts: list[dict[str, object]] = []
    local_code_propagation_conflicts: list[dict[str, object]] = []
    local_code_patch: dict[tuple[int, int], str] = {}
    repeats_header_patch: dict[tuple[int, int], bool] = {}

    applied_edges = compile_continuity._apply_edge_verdicts(
        bools=bools,
        dirty_keys=dirty_keys,
        effective_local_codes=effective_local_codes,
        local_code_conflicts=local_code_conflicts,
        local_code_propagation_conflicts=local_code_propagation_conflicts,
        local_code_patch=local_code_patch,
        min_confidence_to_patch=0.8,
        repeats_header_patch=repeats_header_patch,
        sorted_edge_records=[applied_record, skipped_record],
    )

    assert applied_edges == [
        {
            "applied": True,
            "confidence": 0.96,
            "continuation_kind": "table",
            "eligible_by_confidence": True,
            "is_continuation": True,
            "next_index": 0,
            "next_page": 1,
            "prev_index": 2,
            "prev_page": 0,
            "set_next_table_repeats_header": False,
            "skipped": False,
        },
        {
            "applied": False,
            "confidence": 0.61,
            "continuation_kind": "text",
            "eligible_by_confidence": False,
            "is_continuation": True,
            "next_index": 0,
            "next_page": 2,
            "prev_index": 1,
            "prev_page": 1,
            "set_next_table_repeats_header": None,
            "skip_reason": "below_confidence_threshold",
            "skipped": True,
        },
    ]
    assert bools == {
        (0, 2): [False, True],
        (1, 0): [True, False],
        (1, 1): [True, True],
        (2, 0): [True, True],
    }
    assert dirty_keys == {(0, 2), (1, 0)}
    assert effective_local_codes == {
        (0, 2): "TAB-4",
        (1, 0): "TAB-4",
        (1, 1): None,
        (2, 0): None,
    }
    assert not local_code_conflicts
    assert local_code_patch == {(1, 0): "TAB-4"}
    assert not local_code_propagation_conflicts
    assert repeats_header_patch == {(1, 0): False}


def test_apply_edge_verdicts_raises_for_missing_candidate_keys() -> None:
    """It should raise when an edge verdict references a missing candidate key."""

    missing_key_record = make_edge_verdict_record(
        confidence=0.94,
        continuation_kind=PageContinuationKind.TEXT,
        is_continuation=True,
        next_item_index=7,
        next_page_index=1,
        prev_item_index=0,
        prev_page_index=0,
        set_next_table_repeats_header=None,
    )

    bools = {(0, 0): [False, False]}
    dirty_keys: set[tuple[int, int]] = set()
    effective_local_codes = {(0, 0): None}
    local_code_conflicts: list[dict[str, object]] = []
    local_code_propagation_conflicts: list[dict[str, object]] = []
    local_code_patch: dict[tuple[int, int], str] = {}
    repeats_header_patch: dict[tuple[int, int], bool] = {}

    with pytest.raises(
        ValueError,
        match="Edge verdict references candidate item\\(s\\) that do not exist",
    ):
        compile_continuity._apply_edge_verdicts(
            bools=bools,
            dirty_keys=dirty_keys,
            effective_local_codes=effective_local_codes,
            local_code_conflicts=local_code_conflicts,
            local_code_propagation_conflicts=local_code_propagation_conflicts,
            local_code_patch=local_code_patch,
            min_confidence_to_patch=0.8,
            repeats_header_patch=repeats_header_patch,
            sorted_edge_records=[missing_key_record],
        )

    assert bools == {(0, 0): [False, False]}
    assert dirty_keys == set()
    assert effective_local_codes == {(0, 0): None}
    assert not local_code_conflicts
    assert not local_code_patch
    assert not local_code_propagation_conflicts
    assert not repeats_header_patch


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


def test_mutate_for_edge_clears_only_directional_connection_for_negative_edge() -> None:
    """It should clear only prev.to_next and next.from_prev for a negative edge."""

    bools = {(3, 1): [True, True], (4, 0): [True, True]}
    dirty_keys: set[tuple[int, int]] = set()
    effective_local_codes = {(3, 1): "FIG-2", (4, 0): "FIG-2"}
    local_code_conflicts: list[dict[str, object]] = []
    local_code_propagation_conflicts: list[dict[str, object]] = []
    local_code_patch: dict[tuple[int, int], str] = {}
    record = make_edge_verdict_record(
        confidence=0.97,
        continuation_kind=PageContinuationKind.NONE,
        is_continuation=False,
        next_item_index=0,
        next_page_index=4,
        prev_item_index=1,
        prev_page_index=3,
        set_next_table_repeats_header=None,
    )
    repeats_header_patch: dict[tuple[int, int], bool] = {}

    compile_continuity._mutate_for_edge(
        bools=bools,
        dirty_keys=dirty_keys,
        effective_local_codes=effective_local_codes,
        local_code_conflicts=local_code_conflicts,
        local_code_propagation_conflicts=local_code_propagation_conflicts,
        local_code_patch=local_code_patch,
        next_key=(4, 0),
        prev_key=(3, 1),
        record=record,
        repeats_header_patch=repeats_header_patch,
    )

    assert bools == {(3, 1): [True, False], (4, 0): [False, True]}
    assert dirty_keys == {(3, 1), (4, 0)}
    assert effective_local_codes == {(3, 1): "FIG-2", (4, 0): "FIG-2"}
    assert not local_code_conflicts
    assert not local_code_patch
    assert not local_code_propagation_conflicts
    assert not repeats_header_patch


def test_mutate_for_edge_sets_boundary_bits_and_table_header_patch_for_positive_edge() -> (
    None
):
    """It should set boundary bits, propagate codes, and stage a table-header patch."""

    bools = {(0, 4): [False, False], (1, 2): [False, False]}
    dirty_keys: set[tuple[int, int]] = set()
    effective_local_codes = {(0, 4): "TAB-9", (1, 2): None}
    local_code_conflicts: list[dict[str, object]] = []
    local_code_propagation_conflicts: list[dict[str, object]] = []
    local_code_patch: dict[tuple[int, int], str] = {}
    record = make_edge_verdict_record(
        confidence=0.93,
        continuation_kind=PageContinuationKind.TABLE,
        is_continuation=True,
        next_item_index=2,
        next_page_index=1,
        prev_item_index=4,
        prev_page_index=0,
        set_next_table_repeats_header=True,
    )
    repeats_header_patch: dict[tuple[int, int], bool] = {}

    compile_continuity._mutate_for_edge(
        bools=bools,
        dirty_keys=dirty_keys,
        effective_local_codes=effective_local_codes,
        local_code_conflicts=local_code_conflicts,
        local_code_propagation_conflicts=local_code_propagation_conflicts,
        local_code_patch=local_code_patch,
        next_key=(1, 2),
        prev_key=(0, 4),
        record=record,
        repeats_header_patch=repeats_header_patch,
    )

    assert bools == {(0, 4): [False, True], (1, 2): [True, False]}
    assert dirty_keys == {(0, 4), (1, 2)}
    assert effective_local_codes == {(0, 4): "TAB-9", (1, 2): "TAB-9"}
    assert not local_code_conflicts
    assert local_code_patch == {(1, 2): "TAB-9"}
    assert not local_code_propagation_conflicts
    assert repeats_header_patch == {(1, 2): True}


@PARAM(
    ("continuation_kind", "should_propagate"),
    [
        (PageContinuationKind.TEXT, False),
        (PageContinuationKind.FIGURE, True),
        (PageContinuationKind.TABLE, True),
    ],
)
def test_propagate_local_codes_only_runs_for_table_and_figure_continuations(
    *, continuation_kind: PageContinuationKind, should_propagate: bool
) -> None:
    """It should propagate codes only for table and figure continuations.

    Parameters
    ----------
    continuation_kind
        The semantic continuation kind to test, which determines whether code
        propagation should occur.
    should_propagate
        Whether the test expects code propagation to occur for the given continuation
        kind. This is used to verify that propagation only happens for the appropriate
        kinds.
    """

    effective_local_codes = {(0, 1): "K-1", (1, 0): None}
    local_code_conflicts: list[dict[str, object]] = []
    local_code_propagation_conflicts: list[dict[str, object]] = []
    local_code_patch: dict[tuple[int, int], str] = {}
    record = make_edge_verdict_record(
        confidence=0.9,
        continuation_kind=continuation_kind,
        is_continuation=True,
        next_item_index=0,
        next_page_index=1,
        prev_item_index=1,
        prev_page_index=0,
        set_next_table_repeats_header=None,
    )

    compile_continuity._propagate_local_codes(
        effective_local_codes=effective_local_codes,
        local_code_conflicts=local_code_conflicts,
        local_code_propagation_conflicts=local_code_propagation_conflicts,
        local_code_patch=local_code_patch,
        next_key=(1, 0),
        prev_key=(0, 1),
        record=record,
        verdict=record.verdict,
    )

    expected_next_code = "K-1" if should_propagate else None
    expected_patch = {(1, 0): "K-1"} if should_propagate else {}

    assert effective_local_codes == {(0, 1): "K-1", (1, 0): expected_next_code}
    assert not local_code_conflicts
    assert local_code_patch == expected_patch
    assert not local_code_propagation_conflicts


def test_propagate_local_codes_propagates_from_next_to_prev_when_prev_code_is_missing() -> (
    None
):
    """It should propagate the next-side code backward when the previous side is blank."""

    effective_local_codes = {(5, 0): None, (6, 3): "FIG-7"}
    local_code_conflicts: list[dict[str, object]] = []
    local_code_propagation_conflicts: list[dict[str, object]] = []
    local_code_patch: dict[tuple[int, int], str] = {}
    record = make_edge_verdict_record(
        confidence=0.88,
        continuation_kind=PageContinuationKind.FIGURE,
        is_continuation=True,
        next_item_index=3,
        next_page_index=6,
        prev_item_index=0,
        prev_page_index=5,
        set_next_table_repeats_header=None,
    )

    compile_continuity._propagate_local_codes(
        effective_local_codes=effective_local_codes,
        local_code_conflicts=local_code_conflicts,
        local_code_propagation_conflicts=local_code_propagation_conflicts,
        local_code_patch=local_code_patch,
        next_key=(6, 3),
        prev_key=(5, 0),
        record=record,
        verdict=record.verdict,
    )

    assert effective_local_codes == {(5, 0): "FIG-7", (6, 3): "FIG-7"}
    assert not local_code_conflicts
    assert local_code_patch == {(5, 0): "FIG-7"}
    assert not local_code_propagation_conflicts


def test_propagate_local_codes_records_conflict_when_both_sides_have_different_codes() -> (
    None
):
    """It should record a conflict instead of propagating when both sides disagree."""

    effective_local_codes = {(2, 4): "TAB-A", (3, 0): "TAB-B"}
    local_code_conflicts: list[dict[str, object]] = []
    local_code_propagation_conflicts: list[dict[str, object]] = []
    local_code_patch: dict[tuple[int, int], str] = {}
    record = make_edge_verdict_record(
        confidence=0.91,
        continuation_kind=PageContinuationKind.TABLE,
        is_continuation=True,
        next_item_index=0,
        next_page_index=3,
        prev_item_index=4,
        prev_page_index=2,
        set_next_table_repeats_header=None,
    )

    compile_continuity._propagate_local_codes(
        effective_local_codes=effective_local_codes,
        local_code_conflicts=local_code_conflicts,
        local_code_propagation_conflicts=local_code_propagation_conflicts,
        local_code_patch=local_code_patch,
        next_key=(3, 0),
        prev_key=(2, 4),
        record=record,
        verdict=record.verdict,
    )

    assert effective_local_codes == {(2, 4): "TAB-A", (3, 0): "TAB-B"}
    assert local_code_conflicts == [
        {
            "continuation_kind": "table",
            "next_code": "TAB-B",
            "next_index": 0,
            "next_page": 3,
            "prev_code": "TAB-A",
            "prev_index": 4,
            "prev_page": 2,
        }
    ]
    assert not local_code_patch
    assert not local_code_propagation_conflicts


def test_try_propagate_code_keeps_earlier_patch_when_new_code_conflicts() -> None:
    """It should preserve the earlier patch and record a propagation conflict."""

    effective_local_codes = {(0, 0): "TAB-OLD", (1, 2): "TAB-OLD"}
    local_code_patch = {(1, 2): "TAB-OLD"}
    local_code_propagation_conflicts: list[dict[str, object]] = []

    compile_continuity._try_propagate_code(
        code="TAB-NEW",
        effective_local_codes=effective_local_codes,
        local_code_patch=local_code_patch,
        local_code_propagation_conflicts=local_code_propagation_conflicts,
        source_key=(0, 0),
        target_key=(1, 2),
    )

    assert effective_local_codes == {(0, 0): "TAB-OLD", (1, 2): "TAB-OLD"}
    assert local_code_patch == {(1, 2): "TAB-OLD"}
    assert local_code_propagation_conflicts == [
        {
            "existing_code": "TAB-OLD",
            "incoming_code": "TAB-NEW",
            "kept_code": "TAB-OLD",
            "reason": "propagation_conflict_keep_earlier",
            "source_index": 0,
            "source_page": 0,
            "target_index": 2,
            "target_page": 1,
        }
    ]


def test_try_propagate_code_sets_effective_code_and_patch_for_new_target() -> None:
    """It should propagate the code when the target has not been patched yet."""

    effective_local_codes = {(0, 0): "TAB-3", (1, 1): None}
    local_code_patch: dict[tuple[int, int], str] = {}
    local_code_propagation_conflicts: list[dict[str, object]] = []

    compile_continuity._try_propagate_code(
        code="TAB-3",
        effective_local_codes=effective_local_codes,
        local_code_patch=local_code_patch,
        local_code_propagation_conflicts=local_code_propagation_conflicts,
        source_key=(0, 0),
        target_key=(1, 1),
    )

    assert effective_local_codes == {(0, 0): "TAB-3", (1, 1): "TAB-3"}
    assert local_code_patch == {(1, 1): "TAB-3"}
    assert not local_code_propagation_conflicts
