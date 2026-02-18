"""This module contains functionalities related to validating CanonicalIR information."""

# Standard Library
import re
import unicodedata

from typing import Any, Optional

# Package Library
from skg.canonical_ir.schemas import (
    GroupingCanonicalizationKey,
    GroupingCanonicalizationMap,
    GroupingDecision,
    RowDecision,
    SegmentDecision,
    SegmentDecisionSet,
)
from skg.canonical_ir.utils import _DASH_RE
from skg.document_ir.schemas import DocumentIR, Segment
from skg.page_ir_extraction.validators import QualityError
from skg.utils.constants import (
    OUTER_ANCHOR_ROLES,
    BlockType,
    FrontMatterHeadings,
    NodeRole,
    NonArtifacts,
    SegmentDecisionType,
    StatementRole,
)

# Compiled regexes.
_CODE_LIKE_RE = re.compile(r"^[A-Za-z]?\d+(?:[.\-]\d+){1,}$")
_CURRICULUM_HINT_RE = re.compile(
    r"\b("
    r"grade|standard|class|form|stage|level|"
    r"subject|learning area|"
    r"theme|sub[- ]?theme|strand|topic|sub[- ]?topic|"
    r"unit|module|chapter|week|term|section|"
    # French curriculum keywords
    r"palier|[eéèë]tape|niveau|semaine|comp[eé]tence|"
    r"apprentissages?|activit[eé]s?|planification|domaine|"
    # Wolof curriculum keywords (Senegal bilingual)
    r"j[eéë]ego|tolluwaay"
    r")\b"
    r"|\bp\d+\b"  # P1/P2 style
    r"|\b[ivx]{1,7}\b"  # I–VII roman numerals
    r"|\bce\s*\d+\b",  # CE1/CE2 Senegalese grade levels
    flags=re.IGNORECASE,
)
_GRADE_MARKER_RE = re.compile(
    r"\b(?:p\.?|primary|grade|class|std\.?|standard)\s*([0-9]{1,2}|[ivx]{1,5})\b",
    re.IGNORECASE,
)
_LEAF_BODY_CODE_PREFIX_RE = re.compile(
    r"^\s*([A-Za-z]?\d+(?:[.\-]\d+){1,})\s*[\)\.:\-]?\s+"
)
_ROMAN_NUMERAL_RE = re.compile(r"^[ivx]{1,5}$", re.IGNORECASE)
_TERM_MARKER_RE = re.compile(
    r"\b(?:term|semester|trimester)\s*([0-9]{1,2})\b", re.IGNORECASE
)
_WEEK_MARKER_RE = re.compile(r"\b(?:week|wk)\s*([0-9]{1,2})\b", re.IGNORECASE)


def _build_header_text_by_col(payload: dict[str, Any]) -> dict[int, str]:
    """Build {col_index: normalized_header_blob} from header_rows_canonical.

    Each header blob is the concatenation (in order) of all header row strings for that
    column.

    Parameters
    ----------
    payload
        The segment payload dictionary, expected to contain "header_rows_canonical" as
        a list of header row lists of cell dictionaries.

    Returns
    -------
    dict[int, str]
        A mapping from column index to the concatenated, normalized header text blob.
    """

    hdr = payload.get("header_rows_canonical") or []

    if not isinstance(hdr, list) or not hdr:
        return {}

    ncols = 0

    for row in hdr:
        if isinstance(row, list):
            ncols = max(ncols, len(row))

    if ncols <= 0:
        return {}

    out: dict[int, str] = {}

    for ci in range(ncols):
        parts: list[str] = []

        for hr in hdr:
            if isinstance(hr, list) and ci < len(hr):
                parts.append(str(hr[ci] or ""))

        out[ci] = _normalize_text(" \n ".join(parts))

    return out


def _build_row_cell_text_map(rows: list[dict[str, Any]]) -> dict[int, list[str]]:
    """Parse payload rows to build a map of
    {abs_row_index: [normalized_cell_text_by_col]}.

    This is used when a RowDecision is column-anchored via RowDecision.col_index.

    Parameters
    ----------
    rows
        The list of row dictionaries from the segment payload.

    Returns
    -------
    dict[int, list[str]]
        A mapping from absolute row index to the list of normalized cell texts by
        column.
    """

    row_cells_by_abs_index: dict[int, list[str]] = {}

    for r in rows:
        abs_i = r.get("abs_row_index")

        if abs_i is None:
            continue

        cells = r.get("cells") or []
        col_texts: list[str] = []

        for c in cells:
            if not isinstance(c, dict):
                continue

            t = c.get("text")

            if isinstance(t, dict):
                col_texts.append(_normalize_text(str(t.get("text", "") or "")))
            else:
                col_texts.append(_normalize_text(str(t or "")))

        row_cells_by_abs_index[int(abs_i)] = col_texts

    return row_cells_by_abs_index


def _build_row_text_map(rows: list[dict[str, Any]]) -> dict[int, str]:
    """Parse payload rows to build a map of {abs_row_index: normalized_text_blob}.

    Parameters
    ----------
    rows
        The list of row dictionaries from the segment payload.

    Returns
    -------
    dict[int, str]
        A mapping from absolute row index to the concatenated, normalized text.
    """

    row_text_by_abs_index: dict[int, str] = {}

    for r in rows:
        abs_i = r.get("abs_row_index")

        if abs_i is None:
            continue

        # Extract text from all cells in the row.
        cells = r.get("cells") or []
        parts = [
            str(c.get("text", {}).get("text", "") or "")
            for c in cells
            if isinstance(c.get("text"), dict)
        ]

        row_text_by_abs_index[int(abs_i)] = _normalize_text(" \n ".join(parts))

    return row_text_by_abs_index


def _extract_marker_numbers(
    *, allow_roman: bool, regex: re.Pattern, text_norm: str
) -> set[int]:
    """Extract numeric designators (e.g., grade/week/term numbers) from normalized text.

    Parameters
    ----------
    allow_roman
        Whether to allow roman numerals as valid numbers.
    regex
        The compiled regex pattern to use for extraction.
    text_norm
        The normalized text to search.

    Returns
    -------
    set[int]
        The set of extracted numeric designators.
    """

    output: set[int] = set()

    if not text_norm:
        return output

    for m in regex.finditer(text_norm):
        raw = (m.group(1) or "").strip()

        if not raw:
            continue

        if raw.isdigit():
            output.add(int(raw))
            continue

        if allow_roman and _ROMAN_NUMERAL_RE.match(raw):
            val = _roman_to_int(raw)
            if val is not None:
                output.add(val)

    return output


def _extract_valid_headings(payload: dict[str, Any]) -> list[str]:
    """Extract valid headings from section_path, filtering out non-artifact headings.

    Parameters
    ----------
    payload
        The segment payload dictionary.

    Returns
    -------
    list[str]
        A list of valid heading texts from section_path.
    """

    section_path = payload.get("section_path") or []
    headings: list[str] = []

    for h in section_path:
        if not isinstance(h, (dict, str)):
            continue

        t = (h.get("text", "") or "") if isinstance(h, dict) else h
        tn = _normalize_text(t)

        if tn and tn not in NonArtifacts:
            headings.append(t)

    return headings


def _find_hierarchical_pairs(ids: list[str]) -> list[tuple[str, str]]:
    """Identify pairs of IDs where one is a hierarchical prefix of the other.

    Parameters
    ----------
    ids
        The list of list/code identifiers.

    Returns
    -------
    list[tuple[str, str]]
        A list of (parent_id, child_id) tuples where child_id is a hierarchical
        sub-code of parent_id.
    """

    parents_with_children = []

    # Sort by length to check shorter codes first.
    uniq_ids = sorted(set(ids), key=len)

    for parent_id in uniq_ids:
        for child_id in uniq_ids:
            if parent_id == child_id:
                continue

            if _is_hierarchical_prefix(child_id=child_id, parent_id=parent_id):
                parents_with_children.append((parent_id, child_id))

    return parents_with_children


def _fingerprint_groupings_for_compare(
    groupings: list[dict[str, str]] | None,
) -> list[tuple[str, str]]:
    """Convert grouping dicts from payload into a comparable fingerprint.

    Parameters
    ----------
    groupings
        The list of grouping dictionaries from the segment payload.

    Returns
    -------
    list[tuple[str, str]]
        A list of (role, title) tuples for comparison.
    """

    fps: list[tuple[str, str]] = []

    for g in groupings or []:
        role = str(g.get("role", "")).strip().lower()
        title = _normalize_text(str(g.get("title", "")))

        if role and title:
            fps.append((role, title))

    return fps


def _fingerprint_groupings_models_for_compare(
    groupings: list[Any] | None,
) -> list[tuple[str, str]]:
    """Convert grouping models from SegmentDecision into a comparable fingerprint.

    Parameters
    ----------
    groupings
        The list of grouping models from the SegmentDecision.

    Returns
    -------
    list[tuple[str, str]]
        A list of (role, title) tuples for comparison.
    """

    fps: list[tuple[str, str]] = []

    for g in groupings or []:
        # Normalize role to its string value.
        raw_role = getattr(g, "role", "")
        role = (
            str(raw_role.value if hasattr(raw_role, "value") else raw_role)
            .strip()
            .lower()
        )
        title = _normalize_text(str(getattr(g, "title", "")))

        if role and title:
            fps.append((role, title))

    return fps


def _is_hierarchical_prefix(*, child_id: str, parent_id: str) -> bool:
    """Return True if child_id is a hierarchical sub-code of parent_id.

    Examples:
        - 1.1 -> 1.1.1
        - 3-2 -> 3-2-1

    Parameters
    ----------
    child_id
        The child list/code identifier.
    parent_id
        The parent list/code identifier.

    Returns
    -------
    bool
        True if child_id is a hierarchical sub-code of parent_id.
    """

    if not parent_id or not child_id:
        return False

    # Prefer dot hierarchy. Fall back to dash.
    if child_id.startswith(parent_id + "."):
        return True
    if child_id.startswith(parent_id + "-"):
        return True

    return False


def _looks_like_curriculum_code(value: Optional[str]) -> bool:
    """Return True if `value` looks like a stable curriculum code (e.g., 3.9.4.1).

    Parameters
    ----------
    value
        The input list/code identifier.

    Returns
    -------
    bool
        True if the value looks like a curriculum code.
    """

    return (
        False if not value else bool(_CODE_LIKE_RE.match(_normalize_list_marker(value)))
    )


def _looks_like_front_matter_heading(title: str) -> bool:
    """Return True if `title` looks like a document-structure heading.

    Parameters
    ----------
    title
        The input heading title.

    Returns
    -------
    bool
        True if the title looks like a front-matter heading.
    """

    front_matter_headings = {h.value for h in FrontMatterHeadings}

    tn = _normalize_text(title)

    if not tn:
        return False

    # Direct match.
    if tn in front_matter_headings:
        return True

    # Substring match (handles extra punctuation/formatting).
    for phrase in front_matter_headings:
        if phrase in tn:
            return True

    return False


def _normalize_list_marker(list_marker: Optional[str]) -> str:
    """Normalize list/bullet marker tokens for comparisons (and occasional prefix
    checks).

    Parameters
    ----------
    list_marker
        The input list/bullet marker.

    Returns
    -------
    str
        The normalized list marker.
    """

    if not list_marker:
        return ""

    s = unicodedata.normalize("NFKC", str(list_marker)).strip()
    s = _DASH_RE.sub("-", s)

    # Drop common trailing punctuation.
    s = s.rstrip(". ")

    return s


def _normalize_text(text: Optional[str]) -> str:
    """Normalize text for comparisons (grounding + substring checks).

    Applies NFKC compatibility normalization, strips diacritical marks (so that e.g.
    "Mathématiques" and "MATHEMATIQUES" compare equal), folds dashes, collapses
    whitespace, and casefolds.

    Parameters
    ----------
    text
        The input text to normalize.

    Returns
    -------
    str
        The normalized text.
    """

    if not text:
        return ""

    # Normalize unicode forms (compatibility normalization).
    text = unicodedata.normalize("NFKC", text)

    # Strip diacritical/combining marks so that accented and unaccented variants of the
    # same word match (common in French uppercase typography where accents are
    # routinely dropped, e.g. "DEUXIEME" vs "deuxième").
    text = "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )

    # Normalize dash variants to ASCII hyphen.
    text = _DASH_RE.sub("-", text)

    # Collapse whitespace and casefold.
    return re.sub(r"\s+", " ", text).strip().casefold()


def _outer_evidence_supports_title(
    *,
    allow_prior_titles: bool,
    evidence_blob_norm: str,
    prior_titles_norm: set[str],
    role: NodeRole,
    title_norm: str,
) -> bool:
    """Return True if the context grouping title is supported by outer evidence.

    Default policy: strict substring match.

    Mild normalization policy:
      - For GRADE_LEVEL: allow numeric equivalence across aliases
        (P1 ~ Primary 1 ~ Grade 1 ~ Std I).
      - Optionally for WEEK/TERM: allow numeric equivalence (Week 1 ~ Wk 1).

    Parameters
    ----------
    allow_prior_titles
        Whether to allow support from prior context grouping titles.
    evidence_blob_norm
        The normalized outer evidence text blob.
    prior_titles_norm
        The set of normalized prior context grouping titles.
    role
        The NodeRole of the context grouping.
    title_norm
        The normalized title of the context grouping.

    Returns
    -------
    bool
        True if the title is supported by outer evidence.
    """

    # 1. Strict support: title appears verbatim in evidence (normalized substring).
    if title_norm and title_norm in evidence_blob_norm:
        return True

    # 2. Optionally allow support from prior_context_groupings (carry-forward).
    #
    # Enabled for blocks, unchunked tables, and non-first chunks of chunked tables.
    # Disabled ONLY for the first chunk of a chunked table, which must anchor strictly
    # from outer evidence to establish the stable context stack.
    if allow_prior_titles and title_norm and title_norm in prior_titles_norm:
        return True

    # 3. Mild role-aware numeric equivalence.
    if role == NodeRole.GRADE_LEVEL:
        title_nums = _extract_marker_numbers(
            allow_roman=True, regex=_GRADE_MARKER_RE, text_norm=title_norm
        )
        evidence_nums = _extract_marker_numbers(
            allow_roman=True, regex=_GRADE_MARKER_RE, text_norm=evidence_blob_norm
        )

        # Accept if they share at least one grade number (very conservative).
        return bool(title_nums and evidence_nums and (title_nums & evidence_nums))

    if role == NodeRole.WEEK:
        title_nums = _extract_marker_numbers(
            allow_roman=False, regex=_WEEK_MARKER_RE, text_norm=title_norm
        )
        evidence_nums = _extract_marker_numbers(
            allow_roman=False, regex=_WEEK_MARKER_RE, text_norm=evidence_blob_norm
        )

        return bool(title_nums and evidence_nums and (title_nums & evidence_nums))

    if role == NodeRole.TERM:
        title_nums = _extract_marker_numbers(
            allow_roman=False, regex=_TERM_MARKER_RE, text_norm=title_norm
        )
        evidence_nums = _extract_marker_numbers(
            allow_roman=False, regex=_TERM_MARKER_RE, text_norm=evidence_blob_norm
        )

        return bool(title_nums and evidence_nums and (title_nums & evidence_nums))

    return False


def _roman_to_int(s: str) -> int | None:
    """Convert small roman numerals to int. Supports I to XII (usually enough for
    primary).

    Parameters
    ----------
    s
        The roman numeral string.

    Returns
    -------
    int | None
        The integer value of the roman numeral, or None if input is empty or invalid.
    """

    if not s:
        return None

    s_norm = _normalize_text(s).replace(" ", "")
    mapping = {
        "i": 1,
        "ii": 2,
        "iii": 3,
        "iv": 4,
        "v": 5,
        "vi": 6,
        "vii": 7,
        "viii": 8,
        "ix": 9,
        "x": 10,
        "xi": 11,
        "xii": 12,
    }

    return mapping.get(s_norm)


def _validate_and_fingerprint_row(
    *,
    dup_fingerprints: list[tuple[int, str]],
    max_row_index: int,
    n_cols: int | None,
    rd: RowDecision,
    row_range_end: int | None,
    row_range_start: int | None,
    seen_fingerprints: set[str],
    segment: Segment,
    segment_decision: SegmentDecision,
    table_rows_count: int,
) -> None:
    """Validate a single RowDecision and check for exact duplicates via fingerprinting.

    This helper function performs bounds checking on row and column indices and
    calculates a fingerprint for the row decision to detect exact duplicates within the
    parent decision block.

    Parameters
    ----------
    dup_fingerprints
        A mutable list to collect data on duplicate fingerprints found. Appends
        (row_index, fingerprint_string).
    max_row_index
        The maximum allowable row index for the table (0-based).
    n_cols
        The number of columns in the table, or None if unknown.
    rd
        The specific RowDecision object to validate.
    row_range_end
        The exclusive end index of the current decision chunk, or None.
    row_range_start
        The inclusive start index of the current decision chunk, or None.
    seen_fingerprints
        A mutable set of fingerprints already processed in this batch.
    segment
        The parent Segment object (used for error context).
    segment_decision
        The parent SegmentDecision object (used for error context).
    table_rows_count
        The total number of rows in the table (used for error messages).

    Raises
    ------
    QualityError
        If row_index or col_index constraints are violated.
    """

    # Global range check.
    if rd.row_index < 0 or rd.row_index > max_row_index:
        raise QualityError(
            f"RowDecision.row_index out of range.\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}\n"
            f"  row_index: {rd.row_index}\n"
            f"  allowed: 0..{max_row_index}\n"
            f"  table_rows: {table_rows_count}"
        )

    if (
        row_range_start is not None
        and not row_range_start <= rd.row_index < row_range_end
    ):
        raise QualityError(
            f"RowDecision.row_index outside decision chunk boundaries.\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}\n"
            f"  row_index: {rd.row_index}\n"
            f"  allowed_chunk: [{row_range_start}, {row_range_end})"
        )

    # Optional col_index validation.
    col_i = getattr(rd, "col_index", None)

    if col_i is not None:
        try:
            col_int = int(col_i)
        except Exception as exc:  # pylint: disable=broad-except
            raise QualityError(
                f"RowDecision.col_index must be an integer when provided.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_index: {rd.row_index}\n"
                f"  col_index: {col_i}"
            ) from exc

        if n_cols is not None and (col_int < 0 or col_int >= int(n_cols)):
            raise QualityError(
                f"RowDecision.col_index out of range for table.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_index: {rd.row_index}\n"
                f"  col_index: {col_int}\n"
                f"  allowed: 0..{int(n_cols) - 1}"
            )

    # Fingerprint the *entire* row decision so we can allow sibling fanout while still
    # catching accidental repeats. Sort components so exact-duplicate detection is
    # robust to ordering.
    leaf_fp = "|".join(
        sorted(
            f"{leaf.role.value}:{_normalize_list_marker(getattr(leaf, 'list_marker', None))}:{_normalize_list_marker(getattr(leaf, 'local_code', None))}:{_normalize_text(leaf.body)}"
            for leaf in (rd.leaves or [])
        )
    )
    group_fp = "|".join(
        sorted(
            f"{g.role.value}:{_normalize_text(g.title)}:{_normalize_list_marker(getattr(g, 'local_code', None))}"
            for g in (rd.groupings or [])
        )
    )

    fp = f"row={rd.row_index}::col={getattr(rd, 'col_index', None)}::g=[{group_fp}]::l=[{leaf_fp}]"

    if fp in seen_fingerprints:
        dup_fingerprints.append((rd.row_index, fp))
    else:
        seen_fingerprints.add(fp)


def _validate_chunk_sequence(
    *,
    expected_end: int,
    expected_start: int,
    intervals: list[tuple[int, int]],
    segment: Segment,
) -> None:
    """Helper to validate that sorted intervals cover the range
    [expected_start, expected_end) contiguously without overlaps or gaps.

    Parameters
    ----------
    expected_end
        The expected exclusive end of the covered range.
    expected_start
        The expected inclusive start of the covered range.
    intervals
        The list of (start, end) intervals to validate.
    segment
        The Segment being validated.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    cursor = expected_start

    for start, end in intervals:
        if start >= end:
            raise QualityError(
                f"Invalid chunk interval (start must be < end).\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  interval: [{start}, {end})"
            )

        if start < expected_start:
            raise QualityError(
                f"Chunk interval begins before the table body rows (likely includes header rows).\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  header_row_count: {segment.header_row_count}\n"
                f"  body_row_range: [{expected_start}, {expected_end})\n"
                f"  interval: [{start}, {end})"
            )

        if end > expected_end:
            raise QualityError(
                f"Chunk interval ends past the end of the table rows.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  table_row_count: {expected_end}\n"
                f"  interval: [{start}, {end})"
            )

        if start < cursor:
            raise QualityError(
                f"Overlapping chunk intervals detected.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  overlap_at: row_index={start}\n"
                f"  previous_end: {cursor}\n"
                f"  interval: [{start}, {end})"
            )

        if start > cursor:
            raise QualityError(
                f"Gap between chunk intervals detected (missing coverage).\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  missing_row_range: [{cursor}, {start})\n"
                f"  next_interval: [{start}, {end})"
            )

        cursor = end

    if cursor != expected_end:
        raise QualityError(
            f"Chunk intervals do not fully cover the table body rows.\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  covered_end: {cursor}\n"
            f"  expected_end: {expected_end}\n"
            f"  body_row_range: [{expected_start}, {expected_end})"
        )


def _validate_decision_types(
    *, all_decisions: list[Any], has_chunks: bool, segment_id: str
) -> None:
    """Ensure no mixing of chunked/unchunked decisions and no malformed ranges.

    Parameters
    ----------
    all_decisions
        The list of all SegmentDecisions for the segment.
    has_chunks
        Whether any of the decisions are chunked (have non-None row_range_start/end).
    segment_id
        The ID of the segment being validated (used for error messages).

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    # If we have chunks, we cannot have any "unchunked" (both None) decisions.
    has_unchunked = any(
        (d.row_range_start is None and d.row_range_end is None) for d in all_decisions
    )

    if has_chunks and has_unchunked:
        chunk_count = sum(
            1
            for d in all_decisions
            if d.row_range_start is not None and d.row_range_end is not None
        )
        raise QualityError(
            f"Chunked + unchunked SegmentDecisions detected for the same table segment. "
            f"This can happen if you generated chunked decisions with one config and later "
            f"generated an unchunked decision (or vice-versa).\n"
            f"  segment_id: {segment_id}\n"
            f"  chunk_decision_count: {chunk_count}"
        )

    # Half-Chunked (one none, one not none).
    has_half_chunked = any(
        (d.row_range_start is None) != (d.row_range_end is None) for d in all_decisions
    )

    if has_half_chunked:
        raise QualityError(
            f"Half-chunked SegmentDecision detected "
            f"(one of row_range_start/end is None, the other is not).\n"
            f"  segment_id: {segment_id}"
        )


def _validate_grouping_grounding(
    *,
    decision_id: str,
    header_text_by_col: dict[int, str],
    n_cols: int | None,
    row_decisions: list[Any],
    row_text_map: dict[Any, str],
    segment_id: str,
) -> None:
    """Validate that grouping titles in row decisions are supported by visible text.

    It checks if the title exists in the row's cell text (preferred) or, if a column
    index is provided, in the specific column header.

    Parameters
    ----------
    decision_id
        The ID of the decision (for error reporting).
    header_text_by_col
        A mapping of column indices to their normalized header text.
    n_cols
        The total number of columns in the table, if known.
    row_decisions
        The list of RowDecision objects to validate.
    row_text_map
        A mapping of row indices to their normalized text content.
    segment_id
        The ID of the segment (for error reporting).

    Raises
    ------
    QualityError
        If a grouping title is empty, a column index is invalid, or the title cannot be
        found in the row text or the specified header.
    """

    for rd in (r for r in row_decisions if r.groupings):
        row_blob = row_text_map.get(rd.row_index)
        col_i = getattr(rd, "col_index", None)

        # If we can't find the row blob (e.g., payload without abs_row_index), skip
        # strict enforcement. We still allow header grounding when col_index is
        # provided.
        for g in rd.groupings:
            title = _normalize_text(g.title)

            if not title:
                raise QualityError(
                    f"RowDecision.groupings contains an empty title.\n"
                    f"  segment_id: {segment_id}\n"
                    f"  decision_id: {decision_id}\n"
                    f"  row_index: {rd.row_index}"
                )

            # Row-cell grounding first (preferred).
            if row_blob and title in row_blob:
                continue

            # Column-header grounding (requires col_index).
            if col_i is not None:
                try:
                    col_int = int(col_i)
                except Exception as exc:  # pylint: disable=broad-except
                    raise QualityError(
                        f"RowDecision.col_index must be an integer when provided.\n"
                        f"  segment_id: {segment_id}\n"
                        f"  decision_id: {decision_id}\n"
                        f"  row_index: {rd.row_index}\n"
                        f"  col_index: {col_i}"
                    ) from exc

                if n_cols is not None and (col_int < 0 or col_int >= int(n_cols)):
                    raise QualityError(
                        f"RowDecision.col_index out of range for table.\n"
                        f"  segment_id: {segment_id}\n"
                        f"  decision_id: {decision_id}\n"
                        f"  row_index: {rd.row_index}\n"
                        f"  col_index: {col_int}\n"
                        f"  allowed: 0..{int(n_cols) - 1}"
                    )

                header_blob = header_text_by_col.get(col_int)

                if header_blob and title in header_blob:
                    continue

            # No valid grounding.
            raise QualityError(
                f"RowDecision grouping title not supported by visible row cell text or column header.\n"
                f"  segment_id: {segment_id}\n"
                f"  decision_id: {decision_id}\n"
                f"  row_index: {rd.row_index}\n"
                f"  col_index: {col_i}\n"
                f"  unsupported_title: {g.title}"
            )


def _validate_groupings_against_evidence(
    *,
    allow_prior_titles: bool,
    block_text_norm: str,
    caption: str,
    evidence_blob_base: str,
    header_rows_canonical: list[Any] | None,
    headings: list[str],
    prior_titles_norm: set[str],
    seg_kind: str | None,
    segment: Segment,
    segment_decision: SegmentDecision,
) -> None:
    """Validate that each context grouping is supported by the collected evidence.

    Parameters
    ----------
    allow_prior_titles
        Whether to allow support from prior context grouping titles.
    block_text_norm
        The normalized text blob of the block.
    caption
        The caption text.
    evidence_blob_base
        The base normalized evidence blob (e.g., section_path + caption + header_rows).
    header_rows_canonical
        The canonical header rows from the payload, if any.
    headings
        The valid section_path headings extracted from the payload.
    prior_titles_norm
        The set of normalized prior context grouping titles.
    seg_kind
        The kind of the segment (e.g., "block", "table", etc.) being validated.
    segment
        The Segment being validated.
    segment_decision
        The SegmentDecision whose context groupings are being validated.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    for g in segment_decision.context_groupings:
        title = _normalize_text(g.title)

        if not title:
            raise QualityError(
                f"context_groupings contains an empty title.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}"
            )

        # Front-matter headings (Preface, Contents, etc.) are intentionally filtered
        # out of OUTER evidence to prevent them from becoming curricular context.
        if title in NonArtifacts:
            raise QualityError(
                f"context_groupings contains a FRONT-MATTER title (non-curricular): '{g.title}'. "
                f"Fix: REMOVE this grouping from context_groupings[] and attach directly under the framework root "
                f"(or under a real curricular grouping like Grade/Subject if present).\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  front_matter_title: {g.title}\n"
                f"  section_path_headings: {headings}"
            )

        # Block-text evidence expansion.
        #
        # A heading block's own text IS the curriculum evidence it introduces: subject,
        # stage, grade, strand, etc. are all grounded by the heading itself. Accept the
        # block's text as outer evidence for ALL grouping roles when the segment is a
        # heading.
        #
        # For non-heading blocks (paragraphs, lists) we keep the original conservative
        # policy: only GRADE_LEVEL may be grounded by block text (handles inline grade
        # cues like "(niveau 1: CE1)").
        evidence_blob = evidence_blob_base
        is_heading_block = (
            seg_kind == "block" and segment_decision.block_type == BlockType.HEADING
        )

        if (
            block_text_norm
            and seg_kind == "block"
            and (is_heading_block or g.role == NodeRole.GRADE_LEVEL)
            and segment_decision.decision_type
            in (
                SegmentDecisionType.EMIT_GROUPINGS_ONLY,
                SegmentDecisionType.EMIT_GROUPINGS_AND_LEAVES,
            )
        ):
            evidence_blob = _normalize_text(evidence_blob + " \n " + block_text_norm)

        if not _outer_evidence_supports_title(
            allow_prior_titles=allow_prior_titles,
            evidence_blob_norm=evidence_blob,
            prior_titles_norm=prior_titles_norm,
            role=g.role,
            title_norm=title,
        ):
            raise QualityError(
                f"context_groupings title not supported by OUTER evidence (section_path/caption/header_rows) "
                f"or prior_context_groupings. "
                f"Fix: REMOVE this grouping from context_groupings[] or change it to a title supported by "
                f"section_path/caption/header_rows (or ensure it is a stable prior_context_grouping).\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  unsupported_title: {g.title}\n"
                f"  role: {g.role.value}\n"
                f"  section_path_headings: {headings}\n"
                f"  header_rows_canonical: {header_rows_canonical}\n"
                f"  has_caption_text: {bool((caption or '').strip())}\n"
                f"  prior_context_titles: {sorted(list(prior_titles_norm))[:10]}"
            )


def _validate_interval_uniqueness(
    *, chunk_decisions: list[Any], segment_id: str
) -> None:
    """Ensure no two decisions claim the exact same row interval.

    Parameters
    ----------
    chunk_decisions
        The list of chunked SegmentDecisions for the segment.
    segment_id
        The ID of the segment being validated.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    interval_to_ids: dict[tuple[int, int], list[str]] = {}

    for d in chunk_decisions:
        interval = (int(d.row_range_start), int(d.row_range_end))
        interval_to_ids.setdefault(interval, []).append(d.decision_id)

    duplicate_intervals = {k: v for k, v in interval_to_ids.items() if len(v) > 1}

    if duplicate_intervals:
        interval_sample = list(duplicate_intervals.items())[:5]
        raise QualityError(
            f"Duplicate chunk intervals detected for the same table segment.\n"
            f"  segment_id: {segment_id}\n"
            f"  duplicates(sample): {interval_sample}"
        )


def _validate_row_anchors(
    *,
    n_cols: int | None,
    row_cells_map: dict[int, list[str]],
    segment: Segment,
    segment_decision: SegmentDecision,
) -> None:
    """Validate that row decisions with column anchors are well-formed and grounded.

    Check that col_index is a valid integer within range and that leaf text exists
    within the visible text of the referenced cell.

    Parameters
    ----------
    n_cols
        The number of columns in the table (if known).
    row_cells_map
        A mapping of row indices to a list of cell text strings.
    segment
        The Segment being decided on (used for error context).
    segment_decision
        The SegmentDecision containing the rows to validate.

    Raises
    ------
    QualityError
        If col_index is invalid or leaf text is not found in the cell.
    """

    for rd in segment_decision.rows:
        col_i = getattr(rd, "col_index", None)

        if col_i is None or not rd.leaves:
            continue

        try:
            col_int = int(col_i)
        except Exception as exc:  # pylint: disable=broad-except
            raise QualityError(
                f"RowDecision.col_index must be an integer when provided.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_index: {rd.row_index}\n"
                f"  col_index: {col_i}"
            ) from exc

        if n_cols is not None and (col_int < 0 or col_int >= int(n_cols)):
            raise QualityError(
                f"RowDecision.col_index out of range for table.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_index: {rd.row_index}\n"
                f"  col_index: {col_int}\n"
                f"  allowed: 0..{int(n_cols) - 1}"
            )

        col_texts = row_cells_map.get(rd.row_index)

        # If we can't find the cell texts (e.g., payload lacks abs_row_index), skip
        # strict enforcement.
        if not col_texts or col_int >= len(col_texts):
            continue

        cell_blob = col_texts[col_int]

        for leaf in rd.leaves:
            leaf_norm = _normalize_text(getattr(leaf, "body", "") or "")

            if leaf_norm and leaf_norm not in cell_blob:
                raise QualityError(
                    f"RowDecision leaf body not supported by visible cell text for col_index.\n"
                    f"  segment_id: {segment.segment_id}\n"
                    f"  decision_id: {segment_decision.decision_id}\n"
                    f"  row_index: {rd.row_index}\n"
                    f"  col_index: {col_int}\n"
                    f"  leaf_preview: {(getattr(leaf, 'body', '') or '')[:160]}"
                )


def _validate_single_table_segment(*, decisions: list[Any], segment: Any) -> None:
    """Perform comprehensive validation of SegmentDecisions for a single TABLE segment,
    ensuring consistent chunking, valid intervals, and contiguous coverage of the table
    body rows.

    Parameters
    ----------
    decisions
        The list of SegmentDecisions for the segment.
    segment
        The Segment being validated.
    """

    # Filter for explicit chunk decisions.
    chunk_decisions = [
        d
        for d in decisions
        if d.row_range_start is not None and d.row_range_end is not None
    ]

    # If the table is not chunked (no chunk decisions found), we skip validation.
    if not chunk_decisions:
        return

    _validate_decision_types(
        all_decisions=decisions, has_chunks=True, segment_id=segment.segment_id
    )
    _validate_interval_uniqueness(
        chunk_decisions=chunk_decisions, segment_id=segment.segment_id
    )
    _validate_chunk_sequence(
        expected_end=len(segment.rows),
        expected_start=segment.header_row_count,
        intervals=sorted(
            ((int(d.row_range_start), int(d.row_range_end)) for d in chunk_decisions),
            key=lambda t: t,
        ),
        segment=segment,
    )


def validate_chunked_table_context_matches_prior_context(
    *,
    segment: Segment,
    segment_decision: SegmentDecision,
    segment_payload: dict[str, Any] | None,
    table_chunking: dict[str, Any] | None = None,
) -> None:
    """For chunked table segments, enforce that non-first chunks reuse the exact same
    outer context stack.

    Rule: ONLY enforce for truly chunked table payloads (i.e. chunk payloads that
    include context_rows_before/after metadata). For those:
      - If chunking.is_first_chunk == False and prior_context_groupings[] is non-empty,
        then decision.context_groupings[] must match prior_context_groupings[].

    This prevents context drift across table chunks.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.
    segment_payload
        The payload dictionary for the Segment being decided on.
    table_chunking
        Optional pre-parsed chunking dictionary derived from segment payload builders.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment_payload is None and table_chunking is None:
        return

    chunking = table_chunking or {}

    # NB: Only enforce this rule for chunked-table payloads (i.e. payloads that were
    # produced by make_table_chunk_payload). Full-table payloads also include a
    # lightweight chunking object for absolute indices, but they are not "chunked".
    if not bool(chunking.get("is_chunked", False)):
        return

    # Require explicit is_first_chunk flag. The fallback heuristic
    # (row_range_start == 0) is unreliable because chunk boundaries start at
    # header_row_count, not 0. If is_first_chunk is missing, skip enforcement rather
    # than silently misidentifying the first chunk.
    is_first_chunk = chunking.get("is_first_chunk", None)

    if is_first_chunk is None:
        return

    # Only enforce for non-first chunks.
    if bool(is_first_chunk):
        return

    prior = segment_payload.get("prior_context_groupings") if segment_payload else None

    if prior is None:
        prior = []

    prior_fp = _fingerprint_groupings_for_compare(prior)

    # If the prior context is empty (chunk 0 failed to establish stable context and the
    # external fallback was also empty), skip enforcement. This avoids a deadlock where
    # validate_context_groupings_required_for_emit demands non-empty context (evidence
    # exists) but this validator demands empty context (matching the empty prior). Let
    # the other validators govern context quality independently.
    if not prior_fp:
        return

    decision_fp = _fingerprint_groupings_models_for_compare(
        segment_decision.context_groupings
    )

    if decision_fp != prior_fp:
        raise QualityError(
            f"chunked_table_context_must_match_prior_exactly\n"
            f"segment_id={segment.segment_id}\n"
            f"decision_id={segment_decision.decision_id}\n"
            f"chunk_row_range_start={chunking.get('row_range_start')}, "
            f"chunk_row_range_end={chunking.get('row_range_end')}\n"
            f"prior_context_groupings={prior_fp}\n"
            f"decision_context_groupings={decision_fp}\n"
            "Fix: repeat prior_context_groupings exactly OR mark unresolved if contradictory."
        )


def validate_chunked_table_first_chunk_must_not_ignore_or_unresolved(
    *,
    segment: Segment,
    segment_decision: SegmentDecision,
    segment_payload: dict[str, Any] | None,
    table_chunking: dict[str, Any] | None = None,
) -> None:
    """For chunked table segments, enforce that the first chunk must not be IGNORE or
    UNRESOLVED, since later chunks must reuse the first chunk's context.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.
    segment_payload
        The payload dictionary for the Segment being decided on.
    table_chunking
        Optional pre-parsed chunking dictionary derived from segment payload builders.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment_payload is None and table_chunking is None:
        return

    chunking = table_chunking or {}

    if not bool(chunking.get("is_chunked", False)):
        return

    # Require explicit is_first_chunk flag.
    is_first_chunk = chunking.get("is_first_chunk", None)

    if is_first_chunk is None:
        return

    if not bool(is_first_chunk):
        return

    if segment_decision.decision_type in (
        SegmentDecisionType.IGNORE,
        SegmentDecisionType.UNRESOLVED,
    ):
        raise QualityError(
            f"chunked_table_first_chunk_must_not_be_ignore_or_unresolved\n"
            f"segment_id={segment.segment_id}\n"
            f"decision_id={segment_decision.decision_id}\n"
            f"chunk_row_range_start={chunking.get('row_range_start')}, "
            f"chunk_row_range_end={chunking.get('row_range_end')}\n"
            f"decision_type={segment_decision.decision_type}\n"
            f"Fix: Use decision_type=emit_flagged_unresolved (preferred) and include "
            f"best-guess context_groupings[] for audit review. NB: flagged_unresolved "
            f"context is NOT propagated to later chunks; they will inherit the "
            f"segment-level context_hint as a stable fallback."
        )


def validate_chunked_table_outer_anchors_in_context_groupings(
    *,
    outer_context_roles: list[str],
    segment_decision: SegmentDecision,
    segment_payload: dict[str, Any] | None,
    table_chunking: dict[str, Any] | None = None,
) -> None:
    """For CHUNKED table segments, require table-wide OUTER anchor groupings to be
    expressed in `context_groupings[]`, not segment-level `groupings[]`.

    The reason is because:

    1. Chunk #1 is the only safe moment to decide table-wide context.
    2. Later chunks must reuse the exact same context stack to prevent drift.

    We only enforce this for truly chunked payloads (chunking.is_chunked == True).

    Parameters
    ----------
    outer_context_roles
        The list of NodeRoles that are considered outer context for chunked tables. For
        chunked tables, groupings with these roles MUST go in context_groupings[] (not
        segment-level groupings[]) so that all chunks share a stable context stack.
    segment_decision
        The SegmentDecision to validate.
    segment_payload
        The payload dictionary for the Segment being decided on.
    table_chunking
        Optional pre-parsed chunking dictionary derived from segment payload builders.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment_payload is None and table_chunking is None:
        return

    chunking = table_chunking or {}

    if not bool(chunking.get("is_chunked", False)):
        return

    bad = [
        g
        for g in (segment_decision.groupings or [])
        if getattr(g, "role", None) in outer_context_roles
    ]

    if not bad:
        return

    examples = ", ".join([f"{g.role.value}:{g.title}" for g in bad[:5]])

    raise QualityError(
        f"Chunked table emitted table-wide OUTER anchor(s) in segment-level groupings[]. "
        f"Move these into context_groupings[] instead so chunked tables have stable context. "
        f"Found: {examples}"
    )


def validate_context_groupings_no_duplicate_roles(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Disallow duplicate NodeRoles in context_groupings[]. This is especially
    important once LEARNING_AREA exists (prevents double SUBJECT stacks).

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment_decision.decision_type in (
        SegmentDecisionType.IGNORE,
        SegmentDecisionType.UNRESOLVED,
    ):
        return

    seen: set[NodeRole] = set()

    for g in segment_decision.context_groupings or []:
        if g.role in seen:
            raise QualityError(
                f"Duplicate NodeRole in context_groupings[].\n"
                f"segment_id={segment.segment_id}\n"
                f"decision_id={segment_decision.decision_id}\n"
                f"role={g.role.value}\n"
                f"title={g.title}"
            )

        seen.add(g.role)


def validate_context_groupings_required_for_emit(
    *,
    segment: Segment,
    segment_decision: SegmentDecision,
    segment_payload: dict[str, Any] | None,
) -> None:
    """Require non-empty context_groupings[] only when it matters. We intentionally DO
    NOT force context_groupings[] to be non-empty for *groupings-only* HEADING
    decisions, because cover pages/front-matter often contain only institutional
    headings (country/ministry/publisher) that should not become parents in the
    curriculum hierarchy.

    We DO require non-empty context_groupings[] when:
        - The segment is a TABLE (outer context anchors row statements), OR
        - The decision emits any leaf statements (expectations/descriptors/guidance)

    In those cases, we only enforce non-empty context when there is some curriculum-ish
    outer evidence available:
        - caption_text exists, OR
        - section_path contains curriculum structure cues
            (grade/standard/stage/subject/unit/etc.)

    NB: We ignore common document furniture headings (Table of Contents, References,
        etc.) via `NonArtifacts`.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.
    segment_payload
        The payload dictionary for the Segment being decided on.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment_decision.decision_type in (
        SegmentDecisionType.IGNORE,
        SegmentDecisionType.UNRESOLVED,
    ):
        return

    # Only enforce non-empty context when we emit leaves OR for table segments.
    emits_any_leaves = bool(segment_decision.leaves) or any(
        (r.leaves for r in (segment_decision.rows or []))
    )

    if segment.kind != "table" and not emits_any_leaves:
        return

    payload = segment_payload or {}

    # Determine whether section_path contains curriculum-ish context.
    section_path = payload.get("section_path") or []
    meaningful_heading_texts: list[str] = []

    # Positive curriculum structure cues (general, country-agnostic). Avoid treating
    # institutional front-matter headings as meaningful hierarchy context
    for h in section_path:
        t = ""

        if isinstance(h, dict):
            t = h.get("text", "") or ""
        elif isinstance(h, str):
            t = h  # Defensive: some payloads may serialize as strings

        tn = _normalize_text(t)

        if not tn or tn in NonArtifacts:
            continue

        # Only count headings that look like curriculum structure.
        if not _CURRICULUM_HINT_RE.search(tn):
            continue

        meaningful_heading_texts.append(t)

    has_meaningful_section_path = bool(meaningful_heading_texts)
    has_caption = bool((payload.get("caption_text") or "").strip())

    # Check if any outer anchor grouping is emitted.
    emits_outer_anchor_grouping = (
        any(
            g.role in OUTER_ANCHOR_ROLES
            for g in (segment_decision.context_groupings or [])
        )
        or any(g.role in OUTER_ANCHOR_ROLES for g in (segment_decision.groupings or []))
        or any(
            g.role in OUTER_ANCHOR_ROLES
            for rd in (segment_decision.rows or [])
            for g in (rd.groupings or [])
        )
    )

    if (
        (has_meaningful_section_path or has_caption)
        and not segment_decision.context_groupings
        and not emits_outer_anchor_grouping
    ):
        anchor_names = "/".join(sorted(r.value for r in OUTER_ANCHOR_ROLES))
        raise QualityError(
            f"Emitting decision must include non-empty context_groupings[] when "
            f"meaningful section_path or caption_text evidence exists, UNLESS the "
            f"decision emits an outer anchor grouping ({anchor_names}).\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}\n"
            f"  has_meaningful_section_path: {has_meaningful_section_path}\n"
            f"  has_caption_text: {has_caption}\n"
            f"  emits_outer_anchor_grouping: {emits_outer_anchor_grouping}\n"
            f"  section_path_headings: {meaningful_heading_texts}"
        )


def validate_context_groupings_role_order(
    *,
    context_groupings_role_dict: dict[NodeRole, int],
    segment: Segment,
    segment_decision: SegmentDecision,
) -> None:
    """Enforce stable outer -> inner ordering of context_groupings[] using a fixed role
    precedence. This prevents drift like: stage -> subject -> grade -> subject -> strand
    and ensures chunked tables keep identical context stacks.

    Parameters
    ----------
    context_groupings_role_dict
        A dictionary mapping NodeRoles to their precedence index.
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment_decision.decision_type in (
        SegmentDecisionType.IGNORE,
        SegmentDecisionType.UNRESOLVED,
    ):
        return

    roles = [g.role for g in (segment_decision.context_groupings or [])]

    if not roles:
        return

    # Disallow roles that should never appear in context_groupings[]. FRAMEWORK is
    # implicit (root); UNRESOLVED is a bucket, not a context node.
    for g in segment_decision.context_groupings or []:
        if g.role in (NodeRole.FRAMEWORK, NodeRole.UNRESOLVED):
            raise QualityError(
                f"Invalid NodeRole in context_groupings[].\n"
                f"segment_id={segment.segment_id}\n"
                f"decision_id={segment_decision.decision_id}\n"
                f"role={g.role.value}\n"
                f"title={g.title}"
            )

    # Convert NodeRole -> precedence index for *ranked* roles only. Roles omitted from
    # the configured precedence are treated as unranked and do not participate in
    # ordering checks.
    indices: list[int] = [
        context_groupings_role_dict[r]
        for r in roles
        if r in context_groupings_role_dict
    ]

    # If the configured precedence excludes all roles in context_groupings[], there is
    # nothing to validate.
    if not indices:
        return

    # Must be non-decreasing outer -> inner.
    for i in range(1, len(indices)):
        if indices[i] < indices[i - 1]:
            pretty = [
                (g.role.value, g.title)
                for g in (segment_decision.context_groupings or [])
            ]
            raise QualityError(
                f"context_groupings[] roles are out of order (must be outer→inner).\n"
                f"segment_id={segment.segment_id}\n"
                f"decision_id={segment_decision.decision_id}\n"
                f"context_groupings={pretty}"
            )


def validate_context_groupings_supported_by_outer_evidence(
    *,
    segment: Segment,
    segment_decision: SegmentDecision,
    segment_payload: dict[str, Any] | None,
    table_chunking: dict[str, Any] | None = None,
) -> None:
    """`context_groupings[]` must be supported by OUTER evidence:

    - section_path texts
    - caption_text
    - header_rows_canonical

    This discourages the model from promoting row-local values into outer context.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.
    segment_payload
        The payload dictionary for the Segment being decided on.
    table_chunking
        Optional pre-parsed chunking dictionary derived from segment payload builders.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if (
        segment_decision.decision_type
        in (
            SegmentDecisionType.IGNORE,
            SegmentDecisionType.UNRESOLVED,
        )
        or not segment_decision.context_groupings
    ):
        return

    payload = segment_payload or {}
    headings = _extract_valid_headings(payload)
    caption = payload.get("caption_text") or ""

    # Note: We capture the raw value for the error message, but use a default for
    # processing.
    raw_header_rows = payload.get("header_rows_canonical")

    header_rows = raw_header_rows or []
    header_strings = [
        c for r in header_rows for c in r if isinstance(c, str) and c.strip()
    ]

    # Include heading-role-hint patterns as supplementary outer evidence. These
    # patterns are document-specific config entries that the LLM prompt uses as FIXED
    # role assignments. Accepting them as evidence prevents the validator from
    # rejecting context titles that the LLM correctly derived from a hint-matched
    # heading.
    hint_patterns: list[str] = payload.get("_heading_role_hint_patterns") or []

    # Include the segment's RAW section_path heading texts as supplementary evidence.
    # _filter_section_path_for_llm correctly drops level-0 headings from the LLM
    # payload (they are non-structural and would confuse the model), but their text
    # still constitutes valid document evidence. Without this, segments whose
    # section_path consists entirely of level-0 headings have no heading evidence at
    # all, causing the validator to reject titles that the LLM legitimately derived
    # from prior context or from table content matching those headings.
    raw_section_path_texts: list[str] = []
    raw_sp = getattr(segment, "section_path", None) or []

    for sp_entry in raw_sp:
        sp_text = (
            getattr(sp_entry, "text", None)
            if hasattr(sp_entry, "text")
            else sp_entry.get("text", "") if isinstance(sp_entry, dict) else ""
        )

        sp_norm = _normalize_text(sp_text)

        if sp_norm and sp_norm not in NonArtifacts:
            raw_section_path_texts.append(str(sp_text or ""))

    evidence_blob_base = _normalize_text(
        " \n ".join(
            [
                *headings,
                *raw_section_path_texts,
                caption,
                *header_strings,
                *hint_patterns,
            ]
        )
    )

    # If there is NO outer evidence at all, we can't enforce this strictly.
    if not evidence_blob_base.strip():
        return

    # For certain wrapper-style BLOCK segments, allow grade-level refinements to be
    # grounded in the block's own text (e.g., "(niveau 1: CE1)").
    seg_kind = (
        payload.get("segment_kind")
        or getattr(segment, "segment_kind", None)
        or getattr(segment, "kind", None)
    )

    block_text_norm = ""

    if seg_kind == "block":
        bt = (
            payload.get("block_text")
            or payload.get("combined_text")
            or payload.get("text")
            or ""
        )

        if isinstance(bt, dict):
            bt = bt.get("text") or ""

        block_text_norm = _normalize_text(str(bt or ""))

    # Allow carry-forward from prior_context_groupings[] for ALL segment types EXCEPT
    # the first chunk of a chunked table (which must anchor strictly from outer
    # evidence to establish the stable context for later chunks).
    #
    # EXCEPTION: If the table's header rows contain NO curriculum keywords (e.g.,
    # ["Langues", "L1", "L2", "L1", "L2"]), the header evidence is too thin to anchor
    # from. In that case, allow carry-forward from prior context even on the first
    # chunk--the heading-based section_path context is the only meaningful anchor
    # available.
    prior = payload.get("prior_context_groupings") or []
    chunking = table_chunking or {}
    is_first_chunk = bool(chunking.get("is_first_chunk", False))
    is_chunked = bool(chunking.get("is_chunked", False))
    is_first_chunk_of_chunked = is_chunked and is_first_chunk

    # Detect whether header_rows_canonical contains any curriculum-relevant keywords.
    _headers_blob = _normalize_text(" ".join(header_strings))
    _headers_have_curriculum_cues = bool(
        _headers_blob and _CURRICULUM_HINT_RE.search(_headers_blob)
    )

    # Only block carry-forward on first chunk when headers provide real curriculum
    # evidence. If headers are non-curricular (language labels, generic column names,
    # etc.), the prior context is the only usable anchor.
    allow_prior_titles = bool(prior) and not (
        is_first_chunk_of_chunked and _headers_have_curriculum_cues
    )

    prior_titles_norm: set[str] = set()

    for pg in prior:
        if isinstance(pg, dict):
            pt = _normalize_text(pg.get("title", ""))

            if pt:
                prior_titles_norm.add(pt)

    _validate_groupings_against_evidence(
        allow_prior_titles=allow_prior_titles,
        block_text_norm=block_text_norm,
        caption=caption,
        evidence_blob_base=evidence_blob_base,
        header_rows_canonical=raw_header_rows,
        headings=headings,
        prior_titles_norm=prior_titles_norm,
        seg_kind=seg_kind,
        segment=segment,
        segment_decision=segment_decision,
    )


def validate_emit_flagged_unresolved_confidence(
    *,
    segment: Segment,
    segment_decision: SegmentDecision,
    segment_decision_conf_threshold: float,
) -> None:
    """Validate that emit_flagged_unresolved is only used when confidence is low. If
    the LLM chose flagged_unresolved despite high confidence, this raises a
    QualityError to force a retry loop that commits to a proper emit type.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.
    segment_decision_conf_threshold
        The confidence threshold. emit_flagged_unresolved is only valid when
        confidence is below this threshold.

    Raises
    ------
    QualityError
        If the decision type is EMIT_FLAGGED_UNRESOLVED but the confidence
        score meets or exceeds the threshold.
    """

    if segment_decision.decision_type == SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED:
        conf = getattr(segment_decision, "confidence", None)

        if conf is not None and conf >= segment_decision_conf_threshold:
            raise QualityError(
                f"emit_flagged_unresolved_above_threshold\n"
                f"segment_id={segment.segment_id}\n"
                f"decision_id={segment_decision.decision_id}\n"
                f"confidence={conf}, threshold={segment_decision_conf_threshold}\n"
                f"Fix: your confidence ({conf}) is >= the threshold "
                f"({segment_decision_conf_threshold}). You MUST commit to a proper "
                f"emit type (emit_leaves_only, emit_groupings_only, or "
                f"emit_groupings_and_leaves). Do NOT use emit_flagged_unresolved "
                f"when confidence is above threshold. Note any concerns in rationale "
                f"instead."
            )


def validate_emitted_statements_have_outer_anchor(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """If a decision emits ANY leaves (block leaves or table row leaves), require at
    least one outer anchor role (grade/stage/subject/etc.) to exist in
    context_groupings OR emitted groupings. This prevents 'floating strands/topics'
    attached directly to the framework root.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    # NB: For BLOCK segments, framework-root attachment is allowed when the PDF lacks
    # anchors. We only strictly enforce this for TABLE segments to prevent floating
    # table-derived leaves.
    if (
        segment_decision.decision_type
        in (
            SegmentDecisionType.IGNORE,
            SegmentDecisionType.UNRESOLVED,
        )
        or segment.kind != "table"
    ):
        return

    # Check if leaves are emitted.
    emits_block_leaves = bool(segment_decision.leaves)
    emits_row_leaves = any((r.leaves or []) for r in (segment_decision.rows or []))

    if not (emits_block_leaves or emits_row_leaves):
        return  # groupings-only is fine

    def has_anchor(groupings: list[GroupingDecision]) -> bool:
        """Check if any grouping has an outer anchor role.

        Parameters
        ----------
        groupings
            The list of grouping models to check.

        Returns
        -------
        bool
            True if any grouping has an outer anchor role.
        """

        return any((g.role in OUTER_ANCHOR_ROLES) for g in (groupings or []))

    if (
        not has_anchor(segment_decision.context_groupings)
        and not has_anchor(segment_decision.groupings)
        and not any(has_anchor(r.groupings) for r in (segment_decision.rows or []))
    ):
        anchor_names = ", ".join(sorted(r.value for r in OUTER_ANCHOR_ROLES))
        raise QualityError(
            f"emitted_leaves_missing_outer_anchor\n"
            f"segment_id={segment.segment_id}\n"
            f"decision_id={segment_decision.decision_id}\n"
            f"Fix: include at least one of: {anchor_names} "
            f"in context_groupings or emitted groupings (or mark unresolved)."
        )


def validate_established_canonicals(
    *, known_canonicals_list: list[dict[str, str]], mapping: GroupingCanonicalizationMap
) -> None:
    """Validate that a CanonicalizationMap only emits established canonical titles,
    with exact matching.

    Parameters
    ----------
    known_canonicals_list
        The list of established canonical keys (role + title).
    mapping
        The GroupingCanonicalizationMap to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    known_exact: set[tuple[str, str]] = set()
    known_norm: dict[str, dict[str, str]] = {}  # role -> norm -> exact_title

    def _canon_title_key(s: str) -> str:
        """Normalize a title for canonical matching.

        Parameters
        ----------
        s
            The input title.

        Returns
        -------
        str
            The normalized title key.
        """

        s = (s or "").strip().lower()
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r"[.·•:;,_-]+$", "", s)  # trailing punctuation only

        return s

    for k in known_canonicals_list:
        role = k["role"]
        title = k["title"]
        known_exact.add((role, title))
        known_norm.setdefault(role, {})[_canon_title_key(title)] = title

    violations: list[str] = []

    for item in mapping.items:
        for out in item.output:
            role = out.role.value
            title = out.title

            if role not in known_norm:
                continue

            # If it's exactly established, OK
            if (role, title) in known_exact:
                continue

            # If it normalizes to an established title, that's a violation
            norm = _canon_title_key(title)
            if norm in known_norm[role]:
                established_title = known_norm[role][norm]
                violations.append(
                    f"Output [{role}] '{title}' should match established canonical exactly: '{established_title}'"
                )

    if violations:
        raise QualityError(" ".join(violations))


def validate_grouping_canonicalization_coverage(
    *,
    grouping_keys: list[GroupingCanonicalizationKey],
    mapping: GroupingCanonicalizationMap,
) -> None:
    """Validate that a CanonicalizationMap covers all input grouping keys exactly.

    Parameters
    ----------
    grouping_keys
        The list of input GroupingCanonicalizationKey.
    mapping
        The GroupingCanonicalizationMap to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    # NB: GroupingCanonicalizationKey's model_validator strips
    # title/local_code/source_label, so .strip() here is defensive. This keeps tuple
    # construction consistent with utils._gkey_tuple, which also strips, in case a
    # future code path bypasses Pydantic validation (e.g., model_construct()).
    expected = [
        (
            k.role.value,
            (k.title or "").strip(),
            (k.local_code or "").strip(),
            (k.source_label or "").strip(),
        )
        for k in grouping_keys
    ]
    got = [
        (
            i.input.role.value,
            (i.input.title or "").strip(),
            (i.input.local_code or "").strip(),
            (i.input.source_label or "").strip(),
        )
        for i in mapping.items
    ]

    if len(got) != len(expected):
        raise QualityError(
            f"CanonicalizationMap.items size mismatch: got={len(got)} expected={len(expected)}"
        )

    # Exact set match.
    if set(got) != set(expected):
        missing = set(expected) - set(got)
        extra = set(got) - set(expected)

        raise QualityError(
            f"CanonicalizationMap coverage mismatch. missing={missing} extra={extra}"
        )

    # Require same order as inputs for determinism.
    if got != expected:
        raise QualityError(
            "CanonicalizationMap.items are not in the same order as input grouping_keys"
        )


def validate_groupings_not_outer_than_context(
    *,
    context_groupings_role_dict: dict[NodeRole, int],
    segment: Segment,
    segment_decision: SegmentDecision,
) -> None:
    """Since groupings[] are children under the context stack tip, they must not be
    OUTER than the deepest role in context_groupings[]. This prevents SUBJECT -> GRADE
    inversions like: context=[SUBJECT], groupings=[GRADE_LEVEL].

    Parameters
    ----------
    context_groupings_role_dict
        A dictionary mapping NodeRoles to their precedence index.
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if (
        segment_decision.decision_type
        in (
            SegmentDecisionType.IGNORE,
            SegmentDecisionType.UNRESOLVED,
        )
        or not segment_decision.context_groupings
    ):
        return

    context_roles = [g.role for g in (segment_decision.context_groupings or [])]
    ranked_context_roles = [
        r for r in context_roles if r in context_groupings_role_dict
    ]

    if not ranked_context_roles:
        return

    context_max = max(context_groupings_role_dict[r] for r in ranked_context_roles)

    def check(groupings: list[GroupingDecision], where: str) -> None:
        """Check that no grouping is outer than context.

        Parameters
        ----------
        groupings
            The list of grouping models to check.
        where
            The location description for error messages.

        Raises
        ------
        QualityError
            If any grouping is outer than context.
        """

        for g in groupings or []:
            if g.role not in context_groupings_role_dict:
                continue

            if context_groupings_role_dict[g.role] < context_max:
                raise QualityError(
                    f"grouping_outer_than_context\n"
                    f"segment_id={segment.segment_id}\n"
                    f"decision_id={segment_decision.decision_id}\n"
                    f"where={where}\n"
                    f"context_roles={[r.value for r in context_roles]}\n"
                    f"bad_grouping={(g.role.value, g.title)}\n"
                    f"Fix: move this grouping into context_groupings OR reorder so outer roles are emitted first."
                )

    check(segment_decision.groupings, "decision.groupings")

    for r in segment_decision.rows or []:
        check(r.groupings, f"row[{r.row_index}].groupings")


def validate_heading_segments_emit_groupings(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Ensure HEADING blocks don't become no-op context only. If the stitching step
    classified a segment as a HEADING block and the LLM decision emits anything (i.e.,
    is not IGNORE/UNRESOLVED), then we require it to emit at least one grouping node in
    `groupings[]`. Without this, the model can put all structure into
    `context_groupings[]` and skip emitting the actual heading node, which causes
    hierarchy/order drift. The decision can still be IGNORE/UNRESOLVED (e.g.,
    false-positive running headers).

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment.kind != "block":
        return

    # Only enforce for true heading blocks from DocumentIR.
    if getattr(segment, "block_type", None) != BlockType.HEADING:
        return

    if segment_decision.decision_type in (
        SegmentDecisionType.IGNORE,
        SegmentDecisionType.UNRESOLVED,
    ):
        return

    if not segment_decision.groupings:
        raise QualityError(
            f"Heading segment emitted a decision but did not emit any grouping nodes.\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}\n"
            f"  decision_type: {segment_decision.decision_type.value}"
        )


def validate_ignore_unresolved_emit_nothing(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """If decision_type is IGNORE or UNRESOLVED, it must not emit
    groupings/leaves/rows.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment_decision.decision_type not in (
        SegmentDecisionType.IGNORE,
        SegmentDecisionType.UNRESOLVED,
    ):
        return

    if segment_decision.context_groupings:
        raise QualityError(
            f"ignore_or_unresolved_must_have_empty_context_groupings\n"
            f"segment_id={segment.segment_id}\n"
            f"decision_id={segment_decision.decision_id}\n"
            f"decision_type={segment_decision.decision_type.value}\n"
            f"context_groupings_count={len(segment_decision.context_groupings or [])}"
        )

    has_groupings = bool(segment_decision.groupings)
    has_leaves = bool(segment_decision.leaves)
    has_rows = bool(segment_decision.rows)

    if has_groupings or has_leaves or has_rows:
        raise QualityError(
            f"ignore_or_unresolved_must_not_emit_nodes\n"
            f"segment_id={segment.segment_id}\n"
            f"decision_id={segment_decision.decision_id}\n"
            f"decision_type={segment_decision.decision_type.value}\n"
            f"groupings_count={len(segment_decision.groupings or [])}\n"
            f"leaves_count={len(segment_decision.leaves or [])}\n"
            f"rows_count={len(segment_decision.rows or [])}"
        )


def validate_leaf_codes_use_local_code(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Check that official curriculum codes (e.g., 3.9.4.1) are placed in
    LeafDecision.local_code and not embedded in LeafDecision.body.

    NB:

    1. list_marker is ONLY for bullets/enumerators (a), i), 1.).
    2. local_code is for stable document identifiers like 3.9.4.1.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment_decision.decision_type in (
        SegmentDecisionType.IGNORE,
        SegmentDecisionType.UNRESOLVED,
    ):
        return

    leaves: list[Any] = []
    leaves.extend(segment_decision.leaves or [])

    for rd in segment_decision.rows or []:
        leaves.extend(rd.leaves or [])

    for leaf in leaves:
        body = getattr(leaf, "body", "") or ""
        local_code = getattr(leaf, "local_code", None)

        # If local_code is already present, we don't care what the body starts with.
        if local_code:
            continue

        # Detect "3.9.4.1 ..." at the start of body and force it into local_code.
        m = _LEAF_BODY_CODE_PREFIX_RE.match(body)
        if m and _looks_like_curriculum_code(m.group(1)):
            code = m.group(1)
            raise QualityError(
                f"LeafDecision.body appears to start with an official curriculum code. "
                f"Move the code into LeafDecision.local_code and remove it from body.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  detected_code: {code}\n"
                f"  body: {body[:200]}"
            )


def validate_leaf_list_marker_not_code(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Check that list_marker is ONLY for bullets/enumerators (a), i, 1.). Codes must
    be in local_code.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment_decision.decision_type in (
        SegmentDecisionType.IGNORE,
        SegmentDecisionType.UNRESOLVED,
    ):
        return

    leaves: list[Any] = []
    leaves.extend(segment_decision.leaves or [])

    for rd in segment_decision.rows or []:
        leaves.extend(rd.leaves or [])

    for leaf in leaves:
        lm = getattr(leaf, "list_marker", None)
        if _looks_like_curriculum_code(lm):
            raise QualityError(
                f"LeafDecision.list_marker looks like an official curriculum code. "
                f"Move this value to LeafDecision.local_code.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  list_marker: {lm}\n"
                f"  body: {getattr(leaf,'body','')[:200]}"
            )


def validate_row_groupings_no_duplicate_roles(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Prevent a single RowDecision from encoding sibling fanout as a single hierarchy
    path. For example, strand and multiple subjects in one row must be split into
    multiple RowDecisions.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment.kind != "table" or not segment_decision.rows:
        return

    allow_duplicates = {NodeRole.SECTION}  # Safe default

    for row in segment_decision.rows:
        roles = [g.role for g in (row.groupings or [])]
        dup_roles = {
            r for r in roles if roles.count(r) > 1 and r not in allow_duplicates
        }

        if dup_roles:
            dup_roles_str = sorted(r.value for r in dup_roles)
            raise QualityError(
                f"Row {row.row_index} contains duplicate grouping roles {dup_roles_str}. "
                f"This usually means the row contains multiple siblings (e.g. multiple subjects). "
                f"Split into multiple RowDecision entries with the SAME row_index, one per sibling."
            )


def validate_row_groupings_supported_by_row_cells(
    *,
    segment: Segment,
    segment_decision: SegmentDecision,
    segment_payload: dict[str, Any] | None,
    table_chunking: dict[str, Any] | None = None,
) -> None:
    """Row-local groupings must be grounded in the row's visible cell text. This
    prevents hallucinated Topic/Subtopic/Code values. Only enforced for TABLE segments
    where segment_payload includes rows.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.
    segment_payload
        The payload dictionary for the Segment being decided on.
    table_chunking
        Optional pre-parsed chunking dictionary derived from segment payload builders;
        for chunked tables it may also be present as `segment_payload["chunking"]`.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if (
        segment.kind != "table"
        or (
            segment_decision.decision_type
            in (
                SegmentDecisionType.IGNORE,
                SegmentDecisionType.UNRESOLVED,
            )
        )
        or not segment_decision.rows
    ):
        return

    payload = segment_payload or {}
    row_text_map = _build_row_text_map(payload.get("rows") or [])
    header_text_by_col = _build_header_text_by_col(payload)
    n_cols = getattr(segment, "n_cols", None)

    # Validate each row decision grouping title appears in that row's visible cell
    # text. If RowDecision.col_index is provided, allow grounding against the header
    # text for that specific column (header_rows_canonical[*][col_index]) as well.
    _validate_grouping_grounding(
        decision_id=segment_decision.decision_id,
        header_text_by_col=header_text_by_col,
        n_cols=n_cols,
        row_decisions=segment_decision.rows,
        row_text_map=row_text_map,
        segment_id=segment.segment_id,
    )

    chunking = table_chunking or {}

    if chunking.get("row_index_is_absolute") and segment_decision.rows:
        missing = sorted(
            {
                rd.row_index
                for rd in segment_decision.rows
                if rd.row_index not in row_text_map
            }
        )

        if missing:
            raise QualityError(
                f"row_index_not_absolute_or_not_in_payload\n"
                f"segment_id={segment.segment_id}\n"
                f"decision_id={segment_decision.decision_id}\n"
                f"missing_row_indices={missing[:20]}\n"
                f"hint=RowDecision.row_index MUST equal row.abs_row_index values shown in the payload."
            )


def validate_row_leaf_hierarchy_not_flattened(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Detect a common table error: emitting a higher-level container statement as a
    leaf expectation in the same row as its more specific sub-statements. We enforce
    this only when we observe hierarchical local_code patterns like:
    parent="1.1" and child="1.1.1" (or dash hierarchy).

    In these cases, the parent should usually be represented as a RowDecision grouping
    (STRAND/TOPIC/SECTION) rather than as an EXPECTATION leaf.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if (
        segment.kind != "table"
        or not segment_decision.rows
        or (
            segment_decision.decision_type
            in (
                SegmentDecisionType.IGNORE,
                SegmentDecisionType.UNRESOLVED,
            )
        )
    ):
        return

    for rd in segment_decision.rows:
        leaves = rd.leaves or []

        if len(leaves) < 2:
            continue

        # Build normalized local_code -> leaf map for this row.
        local_codes: list[str] = []
        local_code_to_leaf = {}

        for leaf in leaves:
            local_code = _normalize_list_marker(getattr(leaf, "local_code", None))

            if not local_code:
                continue

            local_codes.append(local_code)
            local_code_to_leaf[local_code] = leaf

        if len(local_codes) < 2:
            continue

        # Find any parent code that has at least one child code in the same row.
        parents_with_children = _find_hierarchical_pairs(local_codes)

        # If the parent is emitted as a leaf expectation, flag it.
        for parent_code, child_code in parents_with_children:
            parent_leaf = local_code_to_leaf.get(parent_code)
            child_leaf = local_code_to_leaf.get(child_code)

            if (
                parent_leaf
                and child_leaf
                and parent_leaf.role == StatementRole.EXPECTATION
                and child_leaf.role == StatementRole.EXPECTATION
            ):
                raise QualityError(
                    f"RowDecision appears to flatten a hierarchical code structure into leaves. "
                    f"When a parent code (e.g., '1.1') and child code (e.g., '1.1.1') occur in the same row, "
                    f"treat the parent item as a grouping (RowDecision.groupings) and emit only the child items as leaf expectations.\n"
                    f"  segment_id: {segment.segment_id}\n"
                    f"  decision_id: {segment_decision.decision_id}\n"
                    f"  row_index: {rd.row_index}\n"
                    f"  parent_local_code: {parent_code}\n"
                    f"  child_local_code: {child_code}"
                )


def validate_row_leaves_supported_by_cell(
    *,
    segment: Segment,
    segment_decision: SegmentDecision,
    segment_payload: dict[str, Any] | None,
    table_chunking: dict[str, Any] | None = None,
) -> None:
    """When a RowDecision is column-anchored (RowDecision.col_index is set), all
    emitted leaves must be grounded in the visible text of that specific cell.

    This protects against using col_index only to justify groupings from column headers
    while accidentally emitting leaves from a different column.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.
    segment_payload
        The payload dictionary for the Segment being decided on.
    table_chunking
        Optional chunking metadata used to enforce absolute row_index grounding.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if (
        segment.kind != "table"
        or (
            segment_decision.decision_type
            in (
                SegmentDecisionType.IGNORE,
                SegmentDecisionType.UNRESOLVED,
            )
        )
        or not segment_decision.rows
    ):
        return

    payload = segment_payload or {}
    row_cells_map = _build_row_cell_text_map(payload.get("rows") or [])
    n_cols = getattr(segment, "n_cols", None)

    _validate_row_anchors(
        n_cols=n_cols,
        row_cells_map=row_cells_map,
        segment=segment,
        segment_decision=segment_decision,
    )

    chunking = table_chunking or {}

    if chunking.get("row_index_is_absolute") and segment_decision.rows:
        missing = sorted(
            {
                rd.row_index
                for rd in segment_decision.rows
                if getattr(rd, "col_index", None) is not None
                and rd.row_index not in row_cells_map
            }
        )

        if missing:
            raise QualityError(
                f"row_index_not_absolute_or_not_in_payload\n"
                f"segment_id={segment.segment_id}\n"
                f"decision_id={segment_decision.decision_id}\n"
                f"missing_row_indices={missing[:20]}\n"
                f"hint=RowDecision.row_index MUST equal row.abs_row_index values shown in the payload."
            )


def validate_section_titles_not_front_matter(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Reject NodeRole.SECTION groupings that are actually document-prose headings like
    Vision/Introduction/Assessment/Time Allocation/etc. If the model wants to preserve
    these, it should emit NodeRole.PROSE instead.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment_decision.decision_type in (
        SegmentDecisionType.IGNORE,
        SegmentDecisionType.UNRESOLVED,
    ):
        return

    # Check all grouping locations where SECTION might appear.
    grouping_lists = []
    grouping_lists.append(segment_decision.context_groupings or [])
    grouping_lists.append(segment_decision.groupings or [])
    for rd in segment_decision.rows or []:
        grouping_lists.append(rd.groupings or [])

    for groupings in grouping_lists:
        for g in groupings:
            if g.role == NodeRole.SECTION and _looks_like_front_matter_heading(g.title):
                raise QualityError(
                    f"NodeRole.SECTION used for a document front-matter heading.\n"
                    f"Use NodeRole.PROSE for document structure (or IGNORE).\n"
                    f"  segment_id: {segment.segment_id}\n"
                    f"  decision_id: {segment_decision.decision_id}\n"
                    f"  title: {g.title}"
                )


def validate_segment_kind_coherence(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Ensure the decision structure matches the actual segment kind.

    1. Block segments must not include rows[]
    2. Table segments must not include block_type
    3. Block segments must have block_type equal to DocumentIR's segment.block_type
      (block_type is set deterministically by the pipeline)

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment.kind == "block":
        if segment_decision.rows:
            raise QualityError(
                f"Block segment decision must not include rows[].\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}"
            )

        if segment_decision.block_type != segment.block_type:
            raise QualityError(
                f"Block segment decision has mismatched block_type.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  expected: {segment.block_type}\n"
                f"  got: {segment_decision.block_type}"
            )

    if segment.kind == "table":
        if segment_decision.block_type is not None:
            raise QualityError(
                f"Table segment decision must not include block_type.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  block_type: {segment_decision.block_type}"
            )


def validate_table_chunk_coverage_and_overlap(
    *, document_ir: DocumentIR, segment_decisions: SegmentDecisionSet
) -> None:
    """Validate that chunked-table SegmentDecisions cover the whole table body with no
    overlaps.

    NB: This is a **decision-set-level** validator intended to run *after* all chunk
    decisions for a table segment have been produced, but *before* compilation.

    It checks:

    1. No mixing of chunked and unchunked decisions for the same table segment.
    2. Chunk intervals are within the table row bounds and do not include header rows.
    3. Chunk intervals do not overlap.
    4. Chunk intervals fully cover the table body rows: [header_row_count, len(rows)).

    Parameters
    ----------
    document_ir
        The DocumentIR containing table segments.
    segment_decisions
        The SegmentDecisionSet containing all SegmentDecisions.

    Raises
    ------
    QualityError
        If any chunk coverage/overlap checks fail.
    """

    decisions_by_segment_id: dict[str, list[Any]] = {}

    for d in segment_decisions.decisions:
        assert isinstance(d.segment_id, str), f"Decision missing segment_id: {d}"
        decisions_by_segment_id.setdefault(d.segment_id, []).append(d)

    for segment in document_ir.segments:
        if segment.kind != "table":
            continue

        decisions = decisions_by_segment_id.get(segment.segment_id, [])
        _validate_single_table_segment(decisions=decisions, segment=segment)


def validate_table_context_groupings_exclude_row_local_roles(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """For table segments, forbid row-local roles from appearing in
    `context_groupings[]`. Row-local roles (e.g., TOPIC/SUBTOPIC) should be emitted in
    `RowDecision.groupings[]` so they can vary per row without corrupting the outer
    context stack.
    """

    if (
        segment_decision.decision_type
        in (
            SegmentDecisionType.IGNORE,
            SegmentDecisionType.UNRESOLVED,
        )
        or segment.kind != "table"
    ):
        return

    bad = [
        g
        for g in (segment_decision.context_groupings or [])
        if g.role in {NodeRole.TOPIC, NodeRole.SUBTOPIC}
    ]

    if bad:
        roles = ", ".join(sorted({g.role.value for g in bad}))
        raise QualityError(
            f"table_context_groupings_contains_row_local_roles\n"
            f"segment_id={segment.segment_id}\n"
            f"decision_id={segment_decision.decision_id}\n"
            f"forbidden_roles={roles}\n"
            f"Fix: move these roles into RowDecision.groupings[] (row-level), not context_groupings."
        )


def validate_table_row_index(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Validate that if a SegmentDecision includes row decisions for a table segment,
    then any declared chunking (row_range_start/end) is well-formed and within the
    table row bounds.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment.kind != "table" or not segment_decision.rows:
        return

    start = segment_decision.row_range_start
    end = segment_decision.row_range_end

    # If chunking is declared, it must be well-formed.
    if (start is None) != (end is None):
        raise QualityError(
            f"Chunked table decision must include both row_range_start and row_range_end (exclusive).\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}\n"
            f"  row_range_start: {start}\n"
            f"  row_range_end: {end}"
        )

    is_chunked = start is not None
    table_rows = segment.rows

    if is_chunked:
        if start < 0 or end < 0 or start >= end:
            raise QualityError(
                f"Invalid chunk boundaries for table decision. Expected start < end and both >= 0.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_range: [{start}, {end})"
            )

        if end > len(table_rows):
            raise QualityError(
                f"Chunk boundary row_range_end exceeds table length.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_range: [{start}, {end})\n"
                f"  table_rows: {len(table_rows)}"
            )


def validate_table_header_rows_not_emitted(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Ensure RowDecision.row_index does not point into header rows. This prevents the
    model from interpreting table headers as real curriculum rows, especially for
    unchunked table decisions where chunk boundaries aren't present.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any RowDecision.row_index points into header rows.
    """

    if segment.kind != "table" or not segment_decision.rows:
        return

    header_n = segment.header_row_count or 0

    if header_n <= 0:
        return

    bad = sorted(
        {rd.row_index for rd in segment_decision.rows if rd.row_index < header_n}
    )
    if bad:
        raise QualityError(
            f"RowDecision.row_index includes header rows; header rows must not be emitted.\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}\n"
            f"  header_row_count: {header_n}\n"
            f"  bad_row_indices: {bad}"
        )


def validate_table_split_explosion(
    *, max_leaves_per_row: int = 25, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Heuristic guardrail: prevent the LLM from hallucinating/splitting excessively.

    Parameters
    ----------
    max_leaves_per_row
        The maximum allowed number of LeafDecisions per RowDecision.
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any RowDecision contains more than max_leaves_per_row LeafDecisions.
    """

    if segment.kind != "table":
        return

    for rd in segment_decision.rows or []:
        if len(rd.leaves) > max_leaves_per_row:
            raise QualityError(
                f"RowDecision produced too many leaves (>{max_leaves_per_row}).\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_index: {rd.row_index}\n"
                f"  leaves_count: {len(rd.leaves)}"
            )


def validate_unique_table_rows(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Validate basic row index constraints and prevent *exact* duplicate RowDecisions.

    We intentionally **allow** multiple RowDecision entries to share the same
    `row_index`. This is required when a single table row contains *sibling fanout*
    (e.g., multiple subjects/strands/topics encoded in one row). In those cases, the
    correct representation is to emit **multiple** RowDecision objects with the
    **same** row_index (one per sibling path).

    What we disallow is emitting the *same* RowDecision more than once (exact
    duplicates), because that creates unstable downstream counts and ordering.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment.kind != "table" or not segment_decision.rows:
        return

    table_rows = segment.rows
    max_row_index = len(table_rows) - 1
    start = segment_decision.row_range_start
    end = segment_decision.row_range_end
    n_cols = getattr(segment, "n_cols", None)

    # Allow repeated row_index values, but disallow exact duplicate RowDecision entries
    # (same row_index + same groupings + same leaves).
    seen_fingerprints: set[str] = set()
    dup_fingerprints: list[tuple[int, str]] = []

    for rd in segment_decision.rows:
        _validate_and_fingerprint_row(
            dup_fingerprints=dup_fingerprints,
            max_row_index=max_row_index,
            n_cols=n_cols,
            rd=rd,
            row_range_end=end,
            row_range_start=start,
            seen_fingerprints=seen_fingerprints,
            segment=segment,
            segment_decision=segment_decision,
            table_rows_count=len(table_rows),
        )

    if dup_fingerprints:
        dup_row_indices = sorted({ri for (ri, _) in dup_fingerprints})
        raise QualityError(
            f"Exact duplicate RowDecision entries detected (duplicates are not allowed).\n"
            f"  NOTE: Multiple RowDecisions with the SAME row_index are allowed when representing sibling fanout;\n"
            f"        but they must differ in groupings and/or leaves.\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}\n"
            f"  affected_row_indices: {dup_row_indices}\n"
            f"  duplicate_count: {len(dup_fingerprints)}"
        )
