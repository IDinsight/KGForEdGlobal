"""This module contains functionalities related to validating CanonicalIR information."""

# Standard Library
import re
import unicodedata

from typing import Any, Optional

# Package Library
from skg.canonical_ir.schemas import SegmentDecision
from skg.document_ir.schemas import Segment
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
_LEAF_BODY_CODE_PREFIX_RE = re.compile(
    r"^\s*([A-Za-z]?\d+(?:[.\-]\d+){1,})\s*[\)\.:\-]?\s+"
)


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

    Rule: If segment_payload.chunking.is_first_chunk == False and
    prior_context_groupings[] is non-empty, then decision.context_groupings[] must
    match prior_context_groupings[] (role and title after normalization).

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
    is_first_chunk = bool(chunking.get("is_first_chunk", False))

    # Only enforce for non-first chunks.
    if is_first_chunk:
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
            f"Chunked table context drift detected: context_groupings[] does not match prior_context_groupings[].\n"
            f"segment_id={segment.segment_id}\n"
            f"decision_id={segment_decision.decision_id}\n"
            f"chunk_row_range_start={chunking.get('row_range_start')}, chunk_row_range_end={chunking.get('row_range_end')}\n"
            f"prior_context_groupings={prior_fp}\n"
            f"decision_context_groupings={decision_fp}"
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

    # Allow context_groupings=[] if the decision emits a strong "outer anchor" grouping
    # (e.g., SUBJECT/GRADE/STAGE/THEME/UNIT/WEEK) in groupings[] or row groupings[].
    outer_anchor_roles = {
        NodeRole.GRADE_LEVEL,
        NodeRole.STAGE,
        NodeRole.LEARNING_AREA,
        NodeRole.SUBJECT,
        NodeRole.THEME,
        NodeRole.UNIT,
        NodeRole.WEEK,
    }

    emits_outer_anchor_grouping = any(
        (g.role in outer_anchor_roles) for g in (segment_decision.groupings or [])
    ) or any(
        (g.role in outer_anchor_roles)
        for r in (segment_decision.rows or [])
        for g in (r.groupings or [])
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
    section_path = payload.get("section_path") or []
    headings: list[str] = []

    for h in section_path:
        if not isinstance(h, (dict, str)):
            continue

        t = (h.get("text", "") or "") if isinstance(h, dict) else h
        tn = _normalize_text(t)

        if not tn or tn in NonArtifacts:
            continue

        headings.append(t)

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
        if title not in evidence_blob:
            raise QualityError(
                f"context_groupings title not supported by OUTER evidence (section_path/caption/header_rows). "
                f"Fix: REMOVE this grouping from context_groupings[] or change it to a title that appears verbatim in "
                f"section_path/caption/header_rows.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  unsupported_title: {g.title}\n"
                f"  section_path_headings: {headings}\n"
                f"  header_rows_canonical: {payload.get('header_rows_canonical')}\n"
                f"  has_caption_text: {bool((caption or '').strip())}"
            )


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
    """Reject NodeRole.SECTION groupings that are actually document-prose headings
    like Vision/Introduction/Assessment/Time Allocation/etc. If the model wants to
    preserve these, it should emit NodeRole.PROSE instead.

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
    *, decisions_for_segment: list[SegmentDecision], segment: Segment
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
    decisions_for_segment
        All SegmentDecision objects whose segment_id == segment.segment_id.
    segment
        The DocumentIR segment (must be a table segment).

    Raises
    ------
    QualityError
        If any chunk coverage / overlap checks fail.
    """

    assert segment.kind == "table", f"Segment kind must be 'table'. Got: {segment.kind}"

    # Decisions with explicit chunk ranges.
    chunk_decisions = [
        d
        for d in (decisions_for_segment or [])
        if d.row_range_start is not None and d.row_range_end is not None
    ]

    # Not a chunked table (either unchunked decision or no decisions yet).
    if not chunk_decisions:
        return

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
