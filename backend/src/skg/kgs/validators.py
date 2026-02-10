"""This module contains functionalities related to validating Learning Progressions KG
information.
"""

# Package Library
from skg.kgs.schemas import ProgressionEdgesResponse
from skg.page_ir_extraction.validators import QualityError


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
        a, b = sorted([e.source_sfi_uuid, e.target_sfi_uuid])

        if (a, b) in forbidden_pairs:
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

    for e in response.edges:
        if e.source_sfi_uuid == e.target_sfi_uuid:
            raise QualityError("Self-edge is not allowed.")

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
