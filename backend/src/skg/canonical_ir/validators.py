"""This module contains functionalities related to validating CanonicalIR information."""

# Standard Library
import re
import unicodedata

from typing import Any, Optional

# Package Library
from skg.canonical_ir.schemas import (
    GroupingDecision,
    SegmentDecision,
    SegmentDecisionSet,
)
from skg.document_ir.schemas import DocumentIR, Segment
from skg.page_ir_extraction.validators import QualityError
from skg.utils.constants import (
    CONTEXT_GROUPINGS_ROLE_PRECEDENCE,
    BlockType,
    FrontMatterHeadings,
    NodeRole,
    NonArtifacts,
    SegmentDecisionType,
    StatementRole,
)

# Compiled regexes.
_CODE_LIKE_RE = re.compile(r"^[A-Za-z]?\d+(?:[.\-]\d+){1,}$")
_DASH_RE = re.compile(r"[‐-‒–—−]")  # Common unicode dash characters
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
        role = str(getattr(g, "role", "")).strip().lower()

        # Role may be Enum; normalize to value if possible
        if hasattr(getattr(g, "role", None), "value"):
            role = str(g.role.value).strip().lower()

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

    # 2. Optionally allow support from prior_context_groupings (chunk stability).
    #
    # NB: Only enable this for chunked TABLE segments AFTER the first chunk.
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


def validate_chunked_table_context_matches_prior_context(
    *,
    segment: Segment,
    segment_decision: SegmentDecision,
    segment_payload: dict[str, Any] | None,
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

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment_payload is None or segment.kind != "table":
        return

    chunking = segment_payload.get("chunking") or {}

    # NB: Only enforce this rule for chunked-table payloads (i.e. payloads that were
    # produced by make_table_chunk_payload). Full-table payloads also include a
    # lightweight chunking object for absolute indices, but they are not "chunked".
    if not bool(chunking.get("is_chunked", False)):
        return

    # Treat row_range_start==0 as first chunk if is_first_chunk is missing.
    row_range_start = chunking.get("row_range_start", None)
    is_first_chunk = chunking.get("is_first_chunk", None)

    if is_first_chunk is None and row_range_start == 0:
        is_first_chunk = True

    # Only enforce for non-first chunks.
    if bool(is_first_chunk):
        return

    prior = segment_payload.get("prior_context_groupings") or []

    if not prior:
        return

    prior_fp = _fingerprint_groupings_for_compare(prior)
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


def validate_chunked_table_outer_anchors_in_context_groupings(
    *,
    segment: Segment,
    segment_decision: SegmentDecision,
    segment_payload: dict[str, Any] | None,
) -> None:
    """For CHUNKED table segments, require table-wide OUTER anchor groupings to be
    expressed in `context_groupings[]`, not segment-level `groupings[]`.

    The reason is because:

    1. Chunk #1 is the only safe moment to decide table-wide context.
    2. Later chunks must reuse the exact same context stack to prevent drift.

    We only enforce this for truly chunked payloads (chunking.is_chunked == True).

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

    if segment_payload is None or segment.kind != "table":
        return

    chunking = segment_payload.get("chunking") or {}

    if not bool(chunking.get("is_chunked", False)):
        return

    OUTER_CONTEXT_ROLES = {
        NodeRole.GRADE_LEVEL,
        NodeRole.STAGE,
        NodeRole.LEARNING_AREA,
        NodeRole.SUBJECT,
        NodeRole.THEME,
        NodeRole.UNIT,
        NodeRole.TERM,
        NodeRole.WEEK,
        NodeRole.STRAND,
        NodeRole.SUBSTRAND,
        NodeRole.SECTION,
    }

    bad = [
        g
        for g in (segment_decision.groupings or [])
        if getattr(g, "role", None) in OUTER_CONTEXT_ROLES
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
    # institutional front-matter headings as meaningful hierarchy context.
    curriculum_hint_re = re.compile(
        r"\b("
        r"grade|standard|class|form|stage|level|"
        r"subject|learning area|"
        r"theme|sub[- ]?theme|strand|topic|sub[- ]?topic|"
        r"unit|module|chapter|week|term|section"
        r")\b"
        r"|\bp\d+\b"  # P1/P2 style
        r"|\b[ivx]{1,7}\b",  # I–VII roman numerals
        flags=re.IGNORECASE,
    )

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
        if not curriculum_hint_re.search(tn):
            continue

        meaningful_heading_texts.append(t)

    has_meaningful_section_path = bool(meaningful_heading_texts)
    has_caption = bool((payload.get("caption_text") or "").strip())

    outer_anchor_roles = {
        NodeRole.GRADE_LEVEL,
        NodeRole.STAGE,
        NodeRole.LEARNING_AREA,
        NodeRole.SUBJECT,
        NodeRole.THEME,
        NodeRole.UNIT,
        NodeRole.WEEK,
    }

    # Check if any outer anchor grouping is emitted.
    emits_outer_anchor_grouping = any(
        (g.role in outer_anchor_roles) for g in (segment_decision.groupings or [])
    )

    if (
        (has_meaningful_section_path or has_caption)
        and not segment_decision.context_groupings
        and not emits_outer_anchor_grouping
    ):
        raise QualityError(
            f"Emitting decision must include non-empty context_groupings[] when "
            f"meaningful section_path or caption_text evidence exists, UNLESS the "
            f"decision emits an outer anchor grouping (GRADE/STAGE/SUBJECT/THEME/UNIT/WEEK).\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}\n"
            f"  has_meaningful_section_path: {has_meaningful_section_path}\n"
            f"  has_caption_text: {has_caption}\n"
            f"  emits_outer_anchor_grouping: {emits_outer_anchor_grouping}\n"
            f"  section_path_headings: {meaningful_heading_texts}"
        )


def validate_context_groupings_role_order(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Enforce stable outer -> inner ordering of context_groupings[] using a fixed role
    precedence. This prevents drift like: stage -> subject -> grade -> subject -> strand
    and ensures chunked tables keep identical context stacks.

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

    # Convert NodeRole -> precedence index (unknown roles are treated as errors).
    indices: list[int] = []
    for r in roles:
        if r not in CONTEXT_GROUPINGS_ROLE_PRECEDENCE:
            raise QualityError(
                f"Unknown NodeRole in context_groupings[].\n"
                f"segment_id={segment.segment_id}\n"
                f"decision_id={segment_decision.decision_id}\n"
                f"role={getattr(r, 'value', str(r))}"
            )
        indices.append(CONTEXT_GROUPINGS_ROLE_PRECEDENCE[r])

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
    header_rows = payload.get("header_rows_canonical") or []
    header_strings = [
        c for r in header_rows for c in r if isinstance(c, str) and c.strip()
    ]
    evidence_blob = _normalize_text(" \n ".join([*headings, caption, *header_strings]))

    # If there is NO outer evidence at all, we can't enforce this strictly.
    if not evidence_blob.strip():
        return

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

        prior = payload.get("prior_context_groupings") or []
        chunking = payload.get("chunking") or {}
        is_first_chunk = bool(chunking.get("is_first_chunk", False))
        is_chunked = bool(chunking.get("is_chunked", False))
        allow_prior_titles = is_chunked and (not is_first_chunk)
        prior_titles_norm: set[str] = set()

        for pg in prior:
            if not isinstance(pg, dict):
                continue

            pt = _normalize_text(pg.get("title", ""))

            if pt:
                prior_titles_norm.add(pt)

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
                f"  header_rows_canonical: {payload.get('header_rows_canonical')}\n"
                f"  has_caption_text: {bool((caption or '').strip())}\n"
                f"  prior_context_titles: {sorted(list(prior_titles_norm))[:10]}"
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

    OUTER_ANCHORS = {
        NodeRole.GRADE_LEVEL,
        NodeRole.STAGE,
        NodeRole.LEARNING_AREA,
        NodeRole.SUBJECT,
        NodeRole.THEME,
        NodeRole.UNIT,
        NodeRole.WEEK,
    }

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

        return any((g.role in OUTER_ANCHORS) for g in (groupings or []))

    if (
        not has_anchor(segment_decision.context_groupings)
        and not has_anchor(segment_decision.groupings)
        and not any(has_anchor(r.groupings) for r in (segment_decision.rows or []))
    ):
        raise QualityError(
            f"emitted_leaves_missing_outer_anchor\n"
            f"segment_id={segment.segment_id}\n"
            f"decision_id={segment_decision.decision_id}\n"
            f"Fix: include at least one of: {NodeRole.GRADE_LEVEL.value}, {NodeRole.STAGE.value}, {NodeRole.LEARNING_AREA.value}, {NodeRole.SUBJECT.value}, {NodeRole.THEME.value}, {NodeRole.UNIT.value}, {NodeRole.WEEK.value} "
            f"in context_groupings or emitted groupings (or mark unresolved)."
        )


def validate_groupings_not_outer_than_context(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Since groupings[] are children under the context stack tip, they must not be
    OUTER than the deepest role in context_groupings[]. This prevents SUBJECT -> GRADE
    inversions like: context=[SUBJECT], groupings=[GRADE_LEVEL].

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
        segment_decision.decision_type
        in (
            SegmentDecisionType.IGNORE,
            SegmentDecisionType.UNRESOLVED,
        )
        or not segment_decision.context_groupings
    ):
        return

    context_roles = [g.role for g in (segment_decision.context_groupings or [])]
    context_max = max(CONTEXT_GROUPINGS_ROLE_PRECEDENCE[r] for r in context_roles)

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
            if g.role not in CONTEXT_GROUPINGS_ROLE_PRECEDENCE:
                continue

            if CONTEXT_GROUPINGS_ROLE_PRECEDENCE[g.role] < context_max:
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

    allow_duplicates = {"section"}  # Safe default

    for row in segment_decision.rows:
        roles = [g.role for g in row.groupings or []]
        dup_roles = {
            r for r in roles if roles.count(r) > 1 and r not in allow_duplicates
        }

        if dup_roles:
            raise QualityError(
                f"Row {row.row_index} contains duplicate grouping roles {sorted(dup_roles)}. "
                f"This usually means the row contains multiple siblings (e.g. multiple subjects). "
                f"Split into multiple RowDecision entries with the SAME row_index, one per sibling."
            )


def validate_row_groupings_supported_by_row_cells(
    *,
    segment: Segment,
    segment_decision: SegmentDecision,
    segment_payload: dict[str, Any] | None,
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

    # Validate each row decision grouping title appears in that row text.
    for rd in segment_decision.rows:
        if not rd.groupings:
            continue

        row_blob = row_text_map.get(rd.row_index)

        # If we can't find the row blob (e.g., unchunked payload without
        # abs_row_index), skip strict enforcement.
        if not row_blob:
            continue

        for g in rd.groupings:
            title = _normalize_text(g.title)

            if not title:
                raise QualityError(
                    f"RowDecision.groupings contains an empty title.\n"
                    f"  segment_id: {segment.segment_id}\n"
                    f"  decision_id: {segment_decision.decision_id}\n"
                    f"  row_index: {rd.row_index}"
                )
            if title not in row_blob:
                raise QualityError(
                    f"RowDecision grouping title not supported by visible row cell text.\n"
                    f"  segment_id: {segment.segment_id}\n"
                    f"  decision_id: {segment_decision.decision_id}\n"
                    f"  row_index: {rd.row_index}\n"
                    f"  unsupported_title: {g.title}"
                )

    chunking = payload.get("chunking") or {}
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

        decisions_for_segment = decisions_by_segment_id.get(segment.segment_id, [])

        # Decisions with explicit chunk ranges.
        chunk_decisions = [
            d
            for d in (decisions_for_segment or [])
            if d.row_range_start is not None and d.row_range_end is not None
        ]

        # Not a chunked table (either unchunked decision or no decisions yet).
        if not chunk_decisions:
            continue

        # Disallow mixing chunked + unchunked decisions for the same table segment.
        has_unchunked = any(
            (d.row_range_start is None and d.row_range_end is None)
            for d in (decisions_for_segment or [])
        )
        if has_unchunked:
            raise QualityError(
                f"Chunked + unchunked SegmentDecisions detected for the same table segment. "
                f"This can happen if you generated chunked decisions with one config and later "
                f"generated an unchunked decision (or vice-versa).\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  chunk_decision_count: {len(chunk_decisions)}"
            )

        # Build interval to decision_id list map to detect duplicates.
        interval_to_ids: dict[tuple[int, int], list[str]] = {}

        for d in chunk_decisions:
            assert d.row_range_start is not None and d.row_range_end is not None
            interval = (int(d.row_range_start), int(d.row_range_end))
            interval_to_ids.setdefault(interval, []).append(d.decision_id)

        duplicate_intervals = {k: v for k, v in interval_to_ids.items() if len(v) > 1}

        if duplicate_intervals:
            sample = list(duplicate_intervals.items())[:5]
            raise QualityError(
                f"Duplicate chunk intervals detected for the same table segment.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  duplicates(sample): {sample}"
            )

        # Sort intervals by start/end.
        intervals = sorted(interval_to_ids.keys(), key=lambda t: (t[0], t[1]))

        # Validate contiguous, non-overlapping coverage of the table body rows.
        _validate_chunk_sequence(
            intervals=intervals,
            segment=segment,
            expected_start=segment.header_row_count,
            expected_end=len(segment.rows),
        )


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
    """Validate that RowDecision.row_index values are within range and unique. If this
    SegmentDecision represents a chunked slice of a table, also enforce that all
    RowDecision.row_index values lie within [row_range_start, row_range_end), where
    row_range_end is EXCLUSIVE.

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

    for rd in segment_decision.rows:
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

    # Allow repeated row_index values, but disallow exact duplicate RowDecision entries
    # (same row_index + same groupings + same leaves).
    seen_fingerprints: set[str] = set()
    dup_fingerprints: list[tuple[int, str]] = []

    for rd in segment_decision.rows:
        # Global range check.
        if rd.row_index < 0 or rd.row_index > max_row_index:
            raise QualityError(
                f"RowDecision.row_index out of range.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_index: {rd.row_index}\n"
                f"  allowed: 0..{max_row_index}\n"
                f"  table_rows: {len(table_rows)}"
            )

        if start is not None and not start <= rd.row_index < end:
            raise QualityError(
                f"RowDecision.row_index outside decision chunk boundaries.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_index: {rd.row_index}\n"
                f"  allowed_chunk: [{start}, {end})"
            )

        # Fingerprint the *entire* row decision so we can allow sibling fanout while
        # still catching accidental repeats. Sort components so exact-duplicate
        # detection is robust to ordering.
        group_parts = sorted(
            [
                f"{g.role.value}:{_normalize_text(g.title)}:{_normalize_list_marker(getattr(g, 'local_code', None))}"
                for g in (rd.groupings or [])
            ]
        )
        leaf_parts = sorted(
            [
                f"{leaf.role.value}:{_normalize_list_marker(getattr(leaf,'list_marker',None))}:{_normalize_list_marker(getattr(leaf,'local_code',None))}:{_normalize_text(leaf.body)}"
                for leaf in (rd.leaves or [])
            ]
        )

        group_fp = "|".join(group_parts)
        leaf_fp = "|".join(leaf_parts)

        fp = f"row={rd.row_index}::g=[{group_fp}]::l=[{leaf_fp}]"

        if fp in seen_fingerprints:
            dup_fingerprints.append((rd.row_index, fp))
        else:
            seen_fingerprints.add(fp)

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
