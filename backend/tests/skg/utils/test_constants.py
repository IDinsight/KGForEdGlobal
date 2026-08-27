"""This is the main module for testing utils/constants.py.

These tests are intentionally *strict* "snapshot" checks: they fail if any public
constant changes, so that we don't silently drift compatibility across the codebase.

If we add or rename constants, update this file *deliberately* alongside the change.
"""

# Standard Library
import enum
import typing as t

# Package Library
from skg.utils import constants


def _assert_str_enum_snapshot(
    enum_cls: type[enum.Enum], expected: tuple[tuple[str, str], ...]
) -> None:
    """Verify that a string-backed Enum exactly matches an expected snapshot.

    Checks membership, ordering, uniqueness, string subclass behaviour, and that every
    value is lowercase and stripped of whitespace.

    Parameters
    ----------
    enum_cls
        The Enum class under test.
    expected
        A tuple of `(name, value)` pairs representing the exact expected members in
        order.
    """

    assert issubclass(enum_cls, enum.Enum)
    assert issubclass(enum_cls, str), f"{enum_cls.__name__} should be a str Enum"

    # Exact member snapshot.
    assert _enum_as_name_value_pairs(enum_cls=enum_cls) == expected

    # Useful invariants (not just snapshots).
    values: list[str] = [v for _, v in expected]
    assert len(values) == len(set(values)), f"{enum_cls.__name__} has duplicate values"

    for name, value in expected:
        member = enum_cls[name]
        assert isinstance(member, str)
        assert member.value == value

        # Str-enum convenience: compare directly to the value.
        assert member == value

        # Normalize strings for robust downstream matching.
        assert value == value.strip()
        assert value == value.lower()


def _enum_as_name_value_pairs(enum_cls: type[enum.Enum]) -> tuple[tuple[str, str], ...]:
    """Return the `(name, value)` pairs of an Enum as a tuple of tuples.

    Parameters
    ----------
    enum_cls
        The Enum class whose members should be extracted.

    Returns
    -------
    tuple[tuple[str, str], ...]
        A tuple of `(member.name, member.value)` pairs preserving declaration order.
    """

    return tuple((m.name, t.cast(str, m.value)) for m in enum_cls)


def test_block_type_enum_snapshot() -> None:
    """Snapshot test for the `BlockType` string enum."""

    _assert_str_enum_snapshot(
        enum_cls=constants.BlockType,
        expected=(
            ("ARTIFACT", "artifact"),
            ("CAPTION", "caption"),
            ("FIGURE", "figure"),
            ("FOOTNOTE", "footnote"),
            ("HEADING", "heading"),
            ("LIST", "list"),
            ("PARAGRAPH", "paragraph"),
        ),
    )


def test_caption_prefix_tuples_snapshot() -> None:
    """Snapshot test for `CaptionFigurePrefixes` and `CaptionTablePrefixes`.

    Also validates that every prefix is a unique, lowercase, stripped string.
    """

    assert constants.CaptionFigurePrefixes == (
        "diagramme",
        "fig",
        "fig.",
        "figure",
        "kielelezo",
        "mchoro",
        "schéma",
    )
    assert constants.CaptionTablePrefixes == (
        "jedwali",
        "tab",
        "tab.",
        "table",
        "tableau",
        "tbl",
        "tbl.",
    )

    # Non-superficial: keep matching data clean and predictable.
    for prefixes in (constants.CaptionFigurePrefixes, constants.CaptionTablePrefixes):
        assert isinstance(prefixes, tuple)
        assert len(prefixes) == len(set(prefixes)), "Prefixes should be unique"

        for p in prefixes:
            assert isinstance(p, str)
            assert p == p.strip()
            assert p == p.lower()


def test_figure_kind_enum_snapshot() -> None:
    """Snapshot test for the `FigureKind` string enum."""

    _assert_str_enum_snapshot(
        enum_cls=constants.FigureKind,
        expected=(
            ("BARCODE", "barcode"),
            ("CHART", "chart"),
            ("DIAGRAM", "diagram"),
            ("EQUATION", "equation"),
            ("FLOWCHART", "flowchart"),
            ("GRAPH", "graph"),
            ("ILLUSTRATION", "illustration"),
            ("IMAGE", "image"),
            ("LOGO", "logo"),
            ("MAP", "map"),
            ("OTHER", "other"),
            ("SCHEMATIC", "schematic"),
            ("TIMELINE", "timeline"),
            ("UNKNOWN", "unknown"),
        ),
    )


def test_item_boundary_enum_snapshot() -> None:
    """Snapshot test for the `ItemBoundary` string enum."""

    _assert_str_enum_snapshot(
        enum_cls=constants.ItemBoundary,
        expected=(
            ("BOTH", "both"),
            ("COMPLETE", "complete"),
            ("RESUMED", "resumed"),
            ("TRUNCATED", "truncated"),
        ),
    )


def test_node_role_enum_snapshot() -> None:
    """Snapshot test for the `NodeRole` string enum."""

    _assert_str_enum_snapshot(
        enum_cls=constants.NodeRole,
        expected=(
            ("FRAMEWORK", "framework"),
            ("GRADE_LEVEL", "grade_level"),
            ("LEARNING_AREA", "learning_area"),
            ("PROSE", "prose"),
            ("SECTION", "section"),
            ("STAGE", "stage"),
            ("SUBSTAGE", "substage"),
            ("STRAND", "strand"),
            ("SUBJECT", "subject"),
            ("SUBSTRAND", "substrand"),
            ("SUBTHEME", "subtheme"),
            ("SUBTOPIC", "subtopic"),
            ("TERM", "term"),
            ("THEME", "theme"),
            ("TOPIC", "topic"),
            ("UNIT", "unit"),
            ("UNRESOLVED", "unresolved"),
            ("WEEK", "week"),
        ),
    )


def test_page_boundary_state_enum_snapshot() -> None:
    """Snapshot test for the `PageBoundaryState` string enum."""

    _assert_str_enum_snapshot(
        enum_cls=constants.PageBoundaryState,
        expected=(
            ("BOTH", "both"),
            ("CONTINUES_FROM_PREV", "from_prev"),
            ("CONTINUES_TO_NEXT", "to_next"),
            ("STANDALONE", "standalone"),
        ),
    )


def test_page_continuation_kind_enum_snapshot() -> None:
    """Snapshot test for the `PageContinuationKind` string enum."""

    _assert_str_enum_snapshot(
        enum_cls=constants.PageContinuationKind,
        expected=(
            ("FIGURE", "figure"),
            ("NONE", "none"),
            ("TABLE", "table"),
            ("TEXT", "text"),
        ),
    )
