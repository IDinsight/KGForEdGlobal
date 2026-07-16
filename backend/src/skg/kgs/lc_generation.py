"""This module contains LC generation request building and LLM decomposition
for KG creation (steps 13-14).

- 13: deterministic LC generation requests (ancestor-path + framework context).
- 14: LLM decomposition of LC-source SFIs into atomic skills (not yet built;
  gated on reviewed per-curriculum generation_instructions).

Sibling LC modules mirror the sfi_* per-step layout: lc_selection.py
(steps 11-12), lc_finalization.py (steps 15-17), lc_export.py (step 18).
"""

# Standard Library
import hashlib

from typing import Optional, Sequence
from uuid import UUID

# Third Party Library
from loguru import logger

# Package Library
from skg.kgs.schemas import (
    AcademicStandardsKGBundle,
    LCAncestorPathStatus,
    LCContextSFI,
    LCFrameworkContext,
    LCGenerationRequest,
    LCRequestSFI,
    SFIFinalRecord,
    SFIHasChildEdge,
)
from skg.kgs.utils import KGDirs, make_dir, normalize_text
from skg.schemas import _CreateKGLearningComponentsConfig
from skg.utils.general import write_to_json

LC_GENERATION_REQUESTS_FN = "lc_generation_requests.jsonl"


def _build_ancestor_path(
    *,
    edge_by_child: dict[UUID, SFIHasChildEdge],
    records_by_uuid: dict[UUID, SFIFinalRecord],
    seed_uuid: UUID,
) -> tuple[list[LCContextSFI], LCAncestorPathStatus]:
    """Recover one seed's hasChild ancestor path from the resolved edges.

    The path is walked seed -> framework root and returned root-first. Any
    unresolved root-fallback edge on the walk marks the path unresolved (only
    reachable when the manual-review override admits such seeds).

    Parameters
    ----------
    edge_by_child
        Final hasChild edge keyed by its child final SFI UUID.
    records_by_uuid
        Final SFI records keyed by final SFI UUID.
    seed_uuid
        Final SFI UUID of the LC-source seed.

    Returns
    -------
    tuple[list[LCContextSFI], LCAncestorPathStatus]
        Ancestor context ordered framework root first, direct parent last,
        and the path status.

    Raises
    ------
    ValueError
        If a walked SFI has no hasChild edge, an ancestor has no final
        record, or the walk revisits an SFI (cycle).
    """

    ancestors: list[LCContextSFI] = []
    status: LCAncestorPathStatus = "resolved"
    current = seed_uuid
    seen = {seed_uuid}
    while True:
        edge = edge_by_child.get(current)
        if edge is None:
            raise ValueError(
                f"LC request building (step 13): SFI {current} on the ancestor "
                f"path of seed {seed_uuid} has no hasChild edge; every final "
                "SFI must be attached to the hierarchy after step 10."
            )
        if edge.unresolved_root_fallback:
            status = "unresolved_ancestor_path"
        parent_uuid = edge.parent_final_sfi_uuid
        if parent_uuid is None:
            break
        if parent_uuid in seen:
            raise ValueError(
                f"LC request building (step 13): hasChild cycle detected at "
                f"SFI {parent_uuid} while walking the ancestor path of seed "
                f"{seed_uuid}."
            )
        seen.add(parent_uuid)
        parent_record = records_by_uuid.get(parent_uuid)
        if parent_record is None:
            raise ValueError(
                f"LC request building (step 13): ancestor SFI {parent_uuid} of "
                f"seed {seed_uuid} has no final SFI record."
            )
        ancestors.append(_build_context_sfi(parent_record))
        current = parent_uuid
    ancestors.reverse()
    return ancestors, status


def _build_context_sfi(record: SFIFinalRecord) -> LCContextSFI:
    """Build one disambiguation-only context entry from a final SFI record.

    Parameters
    ----------
    record
        The final SFI record to project.

    Returns
    -------
    LCContextSFI
        The ancestor/sibling context entry.
    """

    return LCContextSFI(
        case_identifier_uuid=record.case_identifier_uuid,
        description=record.description,
        statement_type=record.statement_type,
    )


def _build_request_id(sfi_uuids: Sequence[UUID]) -> str:
    """Build the deterministic request ID for one batch of LC-source SFIs.

    Hashes the ordered SFI UUIDs; with batch size 1 this reduces to the
    hasChild hash-of-the-single-uuid pattern.

    Parameters
    ----------
    sfi_uuids
        Final SFI UUIDs in the batch, in batch order.

    Returns
    -------
    str
        The deterministic request ID.
    """

    return (
        "lc_generation_request_"
        + hashlib.sha256(
            normalize_text("|".join(str(sfi_uuid) for sfi_uuid in sfi_uuids)).encode(
                "utf-8"
            )
        ).hexdigest()[:16]
    )


def _collect_siblings(
    *,
    child_uuids_by_parent: dict[Optional[UUID], list[UUID]],
    edge_by_child: dict[UUID, SFIHasChildEdge],
    records_by_uuid: dict[UUID, SFIFinalRecord],
    seed_uuid: UUID,
) -> list[LCContextSFI]:
    """Collect sibling SFIs under the seed's hasChild parent.

    Parameters
    ----------
    child_uuids_by_parent
        Child final SFI UUIDs per parent (None = framework root), in edge
        order.
    edge_by_child
        Final hasChild edge keyed by its child final SFI UUID.
    records_by_uuid
        Final SFI records keyed by final SFI UUID.
    seed_uuid
        Final SFI UUID of the LC-source seed.

    Returns
    -------
    list[LCContextSFI]
        Sibling context entries in edge order, excluding the seed itself.
    """

    parent_uuid = edge_by_child[seed_uuid].parent_final_sfi_uuid
    return [
        _build_context_sfi(records_by_uuid[sibling_uuid])
        for sibling_uuid in child_uuids_by_parent.get(parent_uuid, [])
        if sibling_uuid != seed_uuid
    ]


def build_lc_generation_requests(
    *,
    academic_standards_bundle: AcademicStandardsKGBundle,
    has_child_edges: Sequence[SFIHasChildEdge],
    kg_dirs: KGDirs,
    lc_config: _CreateKGLearningComponentsConfig,
    lc_eligible_sfis: Sequence[SFIFinalRecord],
    sfi_final_records: Sequence[SFIFinalRecord],
) -> list[LCGenerationRequest]:
    """Run step 13: build deterministic LC generation requests.

    Fully deterministic, no LLM. Eligible seeds are chunked in selection order
    into batches of `lc_request_batch_size` (default 1, hasChild parity); each
    request carries a deterministic request ID, the framework context, and
    per seed its source text, language tag, and hasChild ancestor path
    (disambiguation-only context and the authoritative source of grade/
    curriculum scope). Sibling context is added only when
    `lc_include_sibling_context` is configured. Requests never carry
    statement codes as decomposition input.

    Parameters
    ----------
    academic_standards_bundle
        Compiled step-10 Academic Standards KG bundle (framework context).
    has_child_edges
        Final resolved hasChild edges (ancestor and sibling computation).
    kg_dirs
        KG artifact directories; artifacts are written under ``kg_dirs.root``.
    lc_config
        Learning Components runtime configuration.
    lc_eligible_sfis
        Eligible LC-source SFIs from step 12, in selection order.
    sfi_final_records
        All final SFI records (ancestor/sibling context lookup).

    Returns
    -------
    list[LCGenerationRequest]
        The LC generation requests, in batch order.

    Raises
    ------
    ValueError
        If a final SFI is the child of more than one hasChild edge, or an
        ancestor path cannot be recovered (missing edge, missing record, or
        cycle).
    """

    framework = academic_standards_bundle.framework
    framework_context = LCFrameworkContext(
        academic_subject=framework.academic_subject,
        in_language=framework.in_language,
        jurisdiction=framework.jurisdiction,
        name=framework.name,
    )

    child_uuids_by_parent: dict[Optional[UUID], list[UUID]] = {}
    edge_by_child: dict[UUID, SFIHasChildEdge] = {}
    for edge in has_child_edges:
        if edge.child_final_sfi_uuid in edge_by_child:
            raise ValueError(
                f"LC request building (step 13): SFI "
                f"{edge.child_final_sfi_uuid} is the child of more than one "
                "hasChild edge; ancestor paths require a single parent per "
                "SFI."
            )
        edge_by_child[edge.child_final_sfi_uuid] = edge
        child_uuids_by_parent.setdefault(edge.parent_final_sfi_uuid, []).append(
            edge.child_final_sfi_uuid
        )

    records_by_uuid = {record.final_sfi_uuid: record for record in sfi_final_records}

    request_sfis: list[LCRequestSFI] = []
    for record in lc_eligible_sfis:
        ancestor_path, ancestor_path_status = _build_ancestor_path(
            edge_by_child=edge_by_child,
            records_by_uuid=records_by_uuid,
            seed_uuid=record.final_sfi_uuid,
        )
        siblings = (
            _collect_siblings(
                child_uuids_by_parent=child_uuids_by_parent,
                edge_by_child=edge_by_child,
                records_by_uuid=records_by_uuid,
                seed_uuid=record.final_sfi_uuid,
            )
            if lc_config.lc_include_sibling_context
            else []
        )
        request_sfis.append(
            LCRequestSFI(
                ancestor_path=ancestor_path,
                ancestor_path_status=ancestor_path_status,
                description=record.description,
                final_sfi_uuid=record.final_sfi_uuid,
                language=record.language,
                siblings=siblings,
                statement_type=record.statement_type,
            )
        )

    batch_size = lc_config.lc_request_batch_size
    requests = [
        LCGenerationRequest(
            framework_context=framework_context,
            request_id=_build_request_id(
                [request_sfi.final_sfi_uuid for request_sfi in batch]
            ),
            sfis=batch,
        )
        for batch in (
            request_sfis[start : start + batch_size]
            for start in range(0, len(request_sfis), batch_size)
        )
    ]

    make_dir(kg_dirs.root)
    write_to_json(fp=kg_dirs.root / LC_GENERATION_REQUESTS_FN, json_info=requests)

    unresolved_path_count = sum(
        request_sfi.ancestor_path_status == "unresolved_ancestor_path"
        for request_sfi in request_sfis
    )
    logger.success(
        f"Built LC generation requests: requests={len(requests)}; "
        f"sfis={len(request_sfis)}; "
        f"batch_size={batch_size}; "
        f"sibling_context={lc_config.lc_include_sibling_context}; "
        f"unresolved_ancestor_paths={unresolved_path_count}"
    )
    return requests
