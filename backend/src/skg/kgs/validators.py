"""This module contains validators and validator type aliases for Learning Components
and Learning Progressions KG inference. These validators are reused by both the initial
inference agents and the second-pass validation agents.
"""

# Standard Library
import re
import unicodedata

from typing import Any, Callable, TypeAlias
from uuid import UUID

# Package Library
from skg.kgs.schemas import AtomicSkillsResponse, ProgressionEdgesResponse
from skg.kgs.utils import canon_str_pair
from skg.page_ir_extraction.validators import QualityError

AtomicSkillsValidator: TypeAlias = Callable[[AtomicSkillsResponse], None]
"""Callable signature for validating an AtomicSkillsResponse."""

ProgressionEdgesValidator: TypeAlias = Callable[[ProgressionEdgesResponse], None]
"""Callable signature for validating a ProgressionEdgesResponse."""


def _check_common_edge_invariants(
    *, directed: bool, response: ProgressionEdgesResponse
) -> None:
    """Check invariants shared by all progression edge validators.

    Parameters
    ----------
    directed
        If True, treat (A, B) and (B, A) as distinct edges (buildsTowards). If False,
        canonicalize pairs so (A, B) and (B, A) are considered duplicates (relatesTo).
    response
        The response containing edges to validate.

    Raises
    ------
    QualityError
        If any edge has out-of-range confidence or if duplicate edges are detected.
    """

    seen: set[tuple[str, str]] = set()

    for e in response.edges:
        # Confidence bounds.
        try:
            conf = float(e.confidence)
        except (TypeError, ValueError) as exc:
            raise QualityError(
                f"Edge confidence is not a valid number: {e.confidence!r}"
            ) from exc

        if conf < 0.0 or conf > 1.0:
            raise QualityError(f"Edge confidence must be between 0 and 1, got {conf}.")

        # Duplicate detection.
        if directed:
            pair = (e.source_sfi_uuid, e.target_sfi_uuid)
        else:
            pair = canon_str_pair(e.source_sfi_uuid, e.target_sfi_uuid)

        if pair in seen:
            raise QualityError(
                f"Duplicate edge detected: {pair[0]} -> {pair[1]}. "
                f"Each (source, target) pair must appear at most once."
            )

        seen.add(pair)


def _normalize_quality_text(text: str) -> str:
    """Normalize text for heuristic quality checks.

    This intentionally performs more aggressive normalization than the runtime export
    pipeline because it is only used for validator-side similarity and duplicate
    heuristics.

    Parameters
    ----------
    text
        The text to normalize.

    Returns
    -------
    str
        A normalized version of the text suitable for quality heuristics.
    """

    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[\u00ad\u200b\u200c\u200d\ufeff]+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _validate_batch_coverage(
    *, allowed_sfi_uuids: set[UUID], returned_sfi_uuids: set[UUID]
) -> None:
    """Validate that exactly the expected SFIs were returned in the batch.

    Parameters
    ----------
    allowed_sfi_uuids
        The set of SFI UUIDs expected in the output.
    returned_sfi_uuids
        The set of SFI UUIDs actually processed.

    Raises
    ------
    QualityError
        If there are unexpected or missing SFI UUIDs.
    """

    missing = allowed_sfi_uuids - returned_sfi_uuids
    extra = returned_sfi_uuids - allowed_sfi_uuids

    if extra:
        raise QualityError(
            f"Atomic skills output contains unexpected SFIs: {sorted(map(str, extra))}"
        )

    if missing:
        raise QualityError(
            f"Atomic skills output is missing {len(missing)} SFI(s): {sorted(map(str, missing))}"
        )


def _validate_sfi_skills(
    *,
    max_per_sfi: int,
    min_per_sfi: int,
    require_rationale: bool,
    sfi_uuid: UUID,
    skills: list[Any],
) -> None:
    """Validate the list of skills for a given SFI.

    Parameters
    ----------
    max_per_sfi
        The maximum number of skills allowed per SFI.
    min_per_sfi
        The minimum number of skills required per SFI.
    require_rationale
        If True, each skill must have a non-empty rationale.
    sfi_uuid
        The UUID of the SFI being validated.
    skills
        The list of skills to validate.

    Raises
    ------
    QualityError
        If the skills list length is out of bounds or if any individual skill is
        invalid.
    """

    if len(skills) < int(min_per_sfi) or len(skills) > int(max_per_sfi):
        raise QualityError(
            f"sfi_uuid {sfi_uuid} must have between {min_per_sfi} and {max_per_sfi} skills; "
            f"got {len(skills)}."
        )

    desc_seen: set[str] = set()

    for skill in skills:
        _validate_single_skill(
            desc_seen=desc_seen,
            require_rationale=require_rationale,
            sfi_uuid=sfi_uuid,
            skill=skill,
        )


def _validate_single_skill(
    *, desc_seen: set[str], require_rationale: bool, sfi_uuid: UUID, skill: Any
) -> None:
    """Validate a single skill's properties and check for duplicates.

    Parameters
    ----------
    desc_seen
        A set of validator-normalized descriptions already seen for this SFI.
    require_rationale
        Whether a non-empty rationale is required.
    sfi_uuid
        The UUID of the SFI this skill belongs to.
    skill
        The skill object to validate.

    Raises
    ------
    QualityError
        If the skill description or rationale violates quality rules.
    """

    description = (skill.description or "").strip()
    rationale = (skill.rationale or "").strip() if skill.rationale is not None else ""

    if not description:
        raise QualityError(f"sfi_uuid {sfi_uuid} has a skill with empty description.")

    # Use the stronger validator-side normalizer so duplicate detection is robust to
    # accent, punctuation, and minor formatting differences.
    norm_desc = _normalize_quality_text(description)
    norm_desc = re.sub(r"[^\w]+", " ", norm_desc, flags=re.UNICODE)
    norm_desc = re.sub(r"\s+", " ", norm_desc).strip()

    if norm_desc in desc_seen:
        raise QualityError(
            f"sfi_uuid {sfi_uuid} has duplicate skill descriptions (normalized)."
        )

    desc_seen.add(norm_desc)

    if require_rationale and not rationale:
        raise QualityError(
            f"sfi_uuid {sfi_uuid} has a skill missing required rationale."
        )


def validate_atomic_skills(
    parsed: AtomicSkillsResponse,
    *,
    allowed_sfi_uuids: set[UUID],
    min_per_sfi: int,
    max_per_sfi: int,
    require_rationale: bool,
) -> None:
    """Validate AtomicSkillsResponse for a given batch of SFIs.

    In addition to structural checks, this validator applies lightweight response-level
    quality checks such as non-empty skill descriptions, duplicate normalized skill
    descriptions within an SFI, and rationale presence when required. It does not
    validate semantic grounding against the source text.

    Parameters
    ----------
    parsed
        The parsed AtomicSkillsResponse to validate.
    allowed_sfi_uuids
        The set of SFI UUIDs that are allowed to appear in the response (must match
        the batch provided to the model).
    min_per_sfi
        The minimum number of skills required per SFI.
    max_per_sfi
        The maximum number of skills allowed per SFI.
    require_rationale
        If True, each skill must have a non-empty rationale. If False, rationales are
        optional and can be empty.

    Raises
    ------
    QualityError
        If any validation rule is violated, such as unknown SFI UUIDs, duplicate
        `sfi_uuid` entries, out-of-bounds skill counts, duplicate normalized skill
        descriptions within an SFI, missing descriptions, or missing required
        rationales.
    """

    if not parsed.items:
        raise QualityError("AtomicSkillsResponse.items is empty.")

    returned: set[UUID] = set()
    seen: set[UUID] = set()

    for item in parsed.items:
        sfi_uuid = item.sfi_uuid
        returned.add(sfi_uuid)

        if sfi_uuid not in allowed_sfi_uuids:
            raise QualityError(
                f"Unknown sfi_uuid in atomic skills output: {sfi_uuid}. "
                f"Use only provided UUIDs."
            )

        if sfi_uuid in seen:
            raise QualityError(f"Duplicate sfi_uuid in output: {sfi_uuid}.")

        seen.add(sfi_uuid)

        _validate_sfi_skills(
            max_per_sfi=max_per_sfi,
            min_per_sfi=min_per_sfi,
            require_rationale=require_rationale,
            sfi_uuid=sfi_uuid,
            skills=item.skills or [],
        )

    _validate_batch_coverage(
        allowed_sfi_uuids=allowed_sfi_uuids, returned_sfi_uuids=returned
    )


def validate_cross_grade_builds_towards(
    response: ProgressionEdgesResponse, allowed_lo: set[str], allowed_hi: set[str]
) -> None:
    """Validate cross-grade buildsTowards.

    Parameters
    ----------
    response
        The response containing the buildsTowards edges to validate.
    allowed_lo
        The set of allowed source SFI UUIDs (must be in LOWER grade list).
    allowed_hi
        The set of allowed target SFI UUIDs (must be in UPPER grade list).

    Raises
    ------
    QualityError
        If any edge violates validation rules (unknown UUIDs, self-edges, etc).
    """

    _check_common_edge_invariants(directed=True, response=response)

    for e in response.edges:
        if e.source_sfi_uuid not in allowed_lo:
            raise QualityError(
                "Cross-grade buildsTowards source must be in LOWER grade list."
            )
        if e.target_sfi_uuid not in allowed_hi:
            raise QualityError(
                "Cross-grade buildsTowards target must be in UPPER grade list."
            )
        if e.source_sfi_uuid == e.target_sfi_uuid:
            raise QualityError("Self-edge is not allowed.")


def validate_cross_grade_relates_to(
    response: ProgressionEdgesResponse,
    allowed_lo: set[str],
    allowed_hi: set[str],
    forbidden_pairs: set[tuple[str, str]],
) -> None:
    """Validate cross-grade relatesTo edges. Ensures edges connect items between
    adjacent grades and do not duplicate existing buildsTowards relationships.

    Parameters
    ----------
    response
        The response containing the relatesTo edges to validate.
    allowed_lo
        The set of allowed SFI UUIDs for the lower grade.
    allowed_hi
        The set of allowed SFI UUIDs for the upper grade.
    forbidden_pairs
        The set of undirected (uuid_a, uuid_b) pairs that are not allowed (e.g.,
        because they already have a buildsTowards relationship). Pairs are
        canonicalized by sorting the two UUID strings.

    Raises
    ------
    QualityError
        If edges are self-referential, fail to cross grades, or are forbidden.
    """

    _check_common_edge_invariants(directed=False, response=response)

    for e in response.edges:
        if e.source_sfi_uuid == e.target_sfi_uuid:
            raise QualityError("Self-edge is not allowed.")

        in_lo_src = e.source_sfi_uuid in allowed_lo
        in_hi_src = e.source_sfi_uuid in allowed_hi
        in_lo_tgt = e.target_sfi_uuid in allowed_lo
        in_hi_tgt = e.target_sfi_uuid in allowed_hi

        # Valid scenarios: (Src in Low AND Tgt in High) OR (Src in High AND Tgt in Low)
        if not ((in_lo_src and in_hi_tgt) or (in_hi_src and in_lo_tgt)):
            raise QualityError(
                "Cross-grade relatesTo must connect one lower-grade and one "
                "upper-grade item."
            )

        # Treat forbidden_pairs as undirected (canonicalized pairs).
        pair = canon_str_pair(e.source_sfi_uuid, e.target_sfi_uuid)

        if pair in forbidden_pairs:
            raise QualityError("Edge is in forbidden_pairs (already buildsTowards).")


def validate_within_grade_builds_towards(
    response: ProgressionEdgesResponse,
    allowed_uuids: set[str],
    uuid_positions: dict[str, int],
) -> None:
    """Validate within-grade buildsTowards edges against constraints.

    Parameters
    ----------
    response
        The response containing the buildsTowards edges to validate.
    allowed_uuids
        The set of allowed SFI UUIDs (must be in the provided list).
    uuid_positions
        A mapping of SFI UUIDs to their positions in the original list (for ordering
        checks in buildsTowards).

    Raises
    ------
    QualityError
        If edges reference unknown UUIDs, are self-referential, or violate
        list ordering.
    """

    _check_common_edge_invariants(directed=True, response=response)

    for e in response.edges:
        if (
            e.source_sfi_uuid not in allowed_uuids
            or e.target_sfi_uuid not in allowed_uuids
        ):
            raise QualityError("Edge references an unknown SFI UUID.")

        if e.source_sfi_uuid == e.target_sfi_uuid:
            raise QualityError("Self-edge is not allowed.")

        # Ensure the source appears before the target in the original list
        if uuid_positions[e.source_sfi_uuid] >= uuid_positions[e.target_sfi_uuid]:
            raise QualityError(
                "Within-grade buildsTowards must follow the provided order."
            )


def validate_within_grade_relates_to(
    response: ProgressionEdgesResponse,
    allowed_uuids_a: set[str],
    allowed_uuids_b: set[str],
) -> None:
    """Validate within-grade relatesTo edges. Ensures edges connect items across the
    two distinct threads provided.

    Parameters
    ----------
    response
        The response containing the relatesTo edges to validate.
    allowed_uuids_a
        The set of allowed SFI UUIDs for thread A.
    allowed_uuids_b
        The set of allowed SFI UUIDs for thread B.

    Raises
    ------
    QualityError
        If edges refer to unknown items, are self-referential, or fail to
        bridge the two different threads.
    """

    _check_common_edge_invariants(directed=False, response=response)

    all_allowed = allowed_uuids_a | allowed_uuids_b

    for e in response.edges:
        if e.source_sfi_uuid == e.target_sfi_uuid:
            raise QualityError("Self-edge is not allowed.")

        # Reject hallucinated UUIDs before checking the bridge constraint so that the
        # error message is specific rather than the generic "must connect one item from
        # each thread" which obscures the real problem.
        if e.source_sfi_uuid not in all_allowed:
            raise QualityError(
                f"Source UUID {e.source_sfi_uuid} not found in either thread."
            )
        if e.target_sfi_uuid not in all_allowed:
            raise QualityError(
                f"Target UUID {e.target_sfi_uuid} not found in either thread."
            )

        # Check membership.
        src_in_a = e.source_sfi_uuid in allowed_uuids_a
        src_in_b = e.source_sfi_uuid in allowed_uuids_b
        tgt_in_a = e.target_sfi_uuid in allowed_uuids_a
        tgt_in_b = e.target_sfi_uuid in allowed_uuids_b

        # Valid scenarios: (source in A AND target in B) OR (source in B AND target in
        # A).
        if not ((src_in_a and tgt_in_b) or (src_in_b and tgt_in_a)):
            raise QualityError(
                "Within-grade relatesTo must connect one item from each thread."
            )
