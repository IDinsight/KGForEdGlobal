"""This module contains LC generation request building and LLM decomposition
for KG creation.

- Deterministic LC generation requests (ancestor-path + framework context).
- Sequential, resumable LLM decomposition of LC-source SFIs into atomic
  skills; isolated failures are recorded and guarded by lc_max_failure_rate.

Sibling LC modules mirror the sfi_* layout: lc_selection.py (LC-source
selection), lc_dedup.py (duplicate grouping), lc_finalization.py (mint
nodes, supports edges, validate/summarize), lc_export.py (AS+LC bundle
merge).
"""

# Standard Library
import hashlib

from pathlib import Path
from typing import Optional, Sequence, TypeVar
from uuid import UUID

# Third Party Library
from loguru import logger
from pydantic import ValidationError

# Package Library
from skg.kgs.llm import (
    KGUsageTracker,
    LCGenerationRun,
    generate_learning_components_for_request,
)
from skg.kgs.schemas import (
    AcademicStandardsKGBundle,
    LCAncestorPathStatus,
    LCContextSFI,
    LCFrameworkContext,
    LCGenerationFailure,
    LCGenerationRequest,
    LCGenerationResponse,
    LCGenerationValidationVerdict,
    LCRequestSFI,
    SFIFinalRecord,
    SFIHasChildEdge,
)
from skg.kgs.utils import (
    KGDirs,
    append_jsonl_model,
    make_dir,
    normalize_text,
    reset_output_files,
)
from skg.kgs.validators import verify_lc_generation_quality
from skg.page_ir_extraction.validators import QualityError
from skg.schemas import _CreateKGLearningComponentsConfig
from skg.utils.general import write_to_json

LC_GENERATION_FAILURES_FN = "lc_generation_failures.json"

_LCArtifactModelT = TypeVar(
    "_LCArtifactModelT", LCGenerationResponse, LCGenerationValidationVerdict
)


def _build_ancestor_path(
    *,
    edges_by_child: dict[UUID, list[SFIHasChildEdge]],
    records_by_uuid: dict[UUID, SFIFinalRecord],
    seed_uuid: UUID,
) -> tuple[list[LCContextSFI], LCAncestorPathStatus]:
    """Recover one seed's hasChild ancestors from the resolved edges.

    Every distinct SFI reachable by walking seed -> framework root is
    collected, so a seed whose hierarchy branches carries the ancestors of
    all of its branches. The result is ordered by longest hasChild distance
    from the framework root; ancestors at equal distance are co-equal and are
    ordered by UUID so that no branch is preferred over another. Any
    unresolved root-fallback edge on any walked branch marks the path
    unresolved (only reachable when the manual-review override admits such
    seeds).

    Parameters
    ----------
    edges_by_child
        Final hasChild edges keyed by their child final SFI UUID.
    records_by_uuid
        Final SFI records keyed by final SFI UUID.
    seed_uuid
        Final SFI UUID of the LC-source seed.

    Returns
    -------
    tuple[list[LCContextSFI], LCAncestorPathStatus]
        Ancestor context ordered framework root first, nearest ancestors
        last, and the path status.

    Raises
    ------
    ValueError
        If a walked SFI has no hasChild edge, or an ancestor has no final
        record.
    """

    ancestor_uuids: set[UUID] = set()
    status: LCAncestorPathStatus = "resolved"
    frontier = [seed_uuid]
    walked: set[UUID] = set()
    while frontier:
        current = frontier.pop()
        if current in walked:
            continue
        walked.add(current)
        edges = edges_by_child.get(current)
        if not edges:
            raise ValueError(
                f"LC request building: SFI {current} on the ancestor path of "
                f"seed {seed_uuid} has no hasChild edge; every final SFI "
                f"must be attached to the hierarchy."
            )
        for edge in edges:
            if edge.unresolved_root_fallback:
                status = "unresolved_ancestor_path"
            parent_uuid = edge.parent_final_sfi_uuid
            if parent_uuid is None:
                continue
            if parent_uuid not in records_by_uuid:
                raise ValueError(
                    f"LC request building: ancestor SFI {parent_uuid} of "
                    f"seed {seed_uuid} has no final SFI record."
                )
            ancestor_uuids.add(parent_uuid)
            frontier.append(parent_uuid)

    root_distances = _longest_root_distances(
        ancestor_uuids=ancestor_uuids,
        edges_by_child=edges_by_child,
        seed_uuid=seed_uuid,
    )
    ancestors = [
        _build_context_sfi(
            edges_by_child=edges_by_child, record=records_by_uuid[ancestor_uuid]
        )
        for ancestor_uuid in sorted(
            ancestor_uuids,
            key=lambda ancestor_uuid: (
                root_distances[ancestor_uuid],
                str(ancestor_uuid),
            ),
        )
    ]
    return ancestors, status


def _build_context_sfi(
    *,
    edges_by_child: dict[UUID, list[SFIHasChildEdge]],
    record: SFIFinalRecord,
) -> LCContextSFI:
    """Build one disambiguation-only context entry from a final SFI record.

    Parameters
    ----------
    edges_by_child
        Final hasChild edges keyed by their child final SFI UUID.
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
        parent_uuids=_parent_uuids(
            edges_by_child=edges_by_child, sfi_uuid=record.final_sfi_uuid
        ),
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
    edges_by_child: dict[UUID, list[SFIHasChildEdge]],
    records_by_uuid: dict[UUID, SFIFinalRecord],
    seed_uuid: UUID,
) -> list[LCContextSFI]:
    """Collect sibling SFIs under every hasChild parent of the seed.

    A seed with more than one parent contributes the children of all of its
    parents. Each sibling appears once, in parent-UUID order and then in edge
    order within a parent.

    Parameters
    ----------
    child_uuids_by_parent
        Child final SFI UUIDs per parent (None = framework root), in edge
        order.
    edges_by_child
        Final hasChild edges keyed by their child final SFI UUID, each list
        ordered by parent UUID.
    records_by_uuid
        Final SFI records keyed by final SFI UUID.
    seed_uuid
        Final SFI UUID of the LC-source seed.

    Returns
    -------
    list[LCContextSFI]
        Sibling context entries in edge order, excluding the seed itself.
    """

    seen: set[UUID] = {seed_uuid}
    siblings: list[LCContextSFI] = []
    for edge in edges_by_child[seed_uuid]:
        for sibling_uuid in child_uuids_by_parent[edge.parent_final_sfi_uuid]:
            if sibling_uuid in seen:
                continue
            seen.add(sibling_uuid)
            siblings.append(
                _build_context_sfi(
                    edges_by_child=edges_by_child,
                    record=records_by_uuid[sibling_uuid],
                )
            )
    return siblings


def _load_lc_jsonl_prefix(
    *, fp: Path, model_type: type[_LCArtifactModelT]
) -> list[_LCArtifactModelT]:
    """Load the valid leading prefix of one LC generation JSONL artifact.

    A truncated or invalid trailing line ends the prefix with a warning.

    Parameters
    ----------
    fp
        Path to the JSONL artifact.
    model_type
        Model class each line is validated against.

    Returns
    -------
    list[_LCArtifactModelT]
        Parsed models from the valid leading prefix.
    """

    if not fp.exists() or fp.stat().st_size == 0:
        return []

    loaded: list[_LCArtifactModelT] = []
    with fp.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                loaded.append(model_type.model_validate_json(stripped))
            except ValidationError:
                logger.warning(
                    f"Dropping invalid/truncated LC generation artifact line at "
                    f"{fp}:{line_number}; resuming from the {len(loaded)} valid "
                    f"entries before it."
                )
                break
    return loaded


def _load_resumable_lc_generation_runs(
    *,
    draft_responses_fp: Path,
    lc_config: _CreateKGLearningComponentsConfig,
    lc_generation_requests: Sequence[LCGenerationRequest],
    responses_fp: Path,
    verdicts_fp: Path,
) -> dict[str, LCGenerationRun]:
    """Load completed LC generation runs that remain valid for this run.

    A run is reusable only when its draft response, validation verdict, and
    final response are all present, agree on the request ID, and pass the
    quality checks against the current request. Any stale, duplicate,
    inconsistent, or invalid entry discards ALL progress with a warning so the
    run restarts cleanly and the three artifacts never disagree.

    Parameters
    ----------
    draft_responses_fp
        Path to the LC generation draft responses JSONL artifact.
    lc_config
        Learning Components runtime configuration.
    lc_generation_requests
        Current deterministic LC generation requests.
    responses_fp
        Path to the accepted LC generation responses JSONL artifact.
    verdicts_fp
        Path to the LC generation validation verdicts JSONL artifact.

    Returns
    -------
    dict[str, LCGenerationRun]
        Valid completed runs keyed by request ID (empty when no progress is
        reusable).
    """

    drafts = _load_lc_jsonl_prefix(
        fp=draft_responses_fp, model_type=LCGenerationResponse
    )
    finals = _load_lc_jsonl_prefix(fp=responses_fp, model_type=LCGenerationResponse)
    verdicts = _load_lc_jsonl_prefix(
        fp=verdicts_fp, model_type=LCGenerationValidationVerdict
    )

    reusable = min(len(drafts), len(finals), len(verdicts))
    if reusable == 0:
        return {}
    if not len(drafts) == len(finals) == len(verdicts):
        logger.warning(
            f"LC generation artifacts disagree in length "
            f"(drafts={len(drafts)}, verdicts={len(verdicts)}, "
            f"responses={len(finals)}); resuming from the first {reusable} "
            f"complete runs."
        )

    requests_by_id = {request.request_id: request for request in lc_generation_requests}
    completed: dict[str, LCGenerationRun] = {}
    for index in range(reusable):
        draft, verdict, final = drafts[index], verdicts[index], finals[index]
        request_id = final.request_id
        if draft.request_id != request_id or verdict.request_id != request_id:
            logger.warning(
                f"LC generation artifacts disagree at position {index} "
                f"(draft={draft.request_id!r}, verdict={verdict.request_id!r}, "
                f"response={request_id!r}); discarding all saved progress."
            )
            return {}
        request = requests_by_id.get(request_id)
        if request is None or request_id in completed:
            logger.warning(
                f"LC generation artifacts contain a stale or duplicate "
                f"request_id {request_id!r}; discarding all saved progress."
            )
            return {}
        try:
            verify_lc_generation_quality(
                lc_config=lc_config,
                lc_generation_request=request,
                lc_generation_response=final,
            )
        except QualityError as e:
            logger.warning(
                f"Saved LC generation response for request {request_id!r} no "
                f"longer passes quality checks ({str(e)[:200]}); discarding all "
                f"saved progress."
            )
            return {}
        completed[request_id] = LCGenerationRun(
            draft_response=draft, final_response=final, validation_verdict=verdict
        )
    return completed


def _longest_root_distances(
    *,
    ancestor_uuids: set[UUID],
    edges_by_child: dict[UUID, list[SFIHasChildEdge]],
    seed_uuid: UUID,
) -> dict[UUID, int]:
    """Measure each ancestor's longest hasChild distance from the framework root.

    The longest distance is used rather than the shortest so that an SFI
    reachable at several depths still sorts behind every one of its own
    ancestors.

    Parameters
    ----------
    ancestor_uuids
        Final SFI UUIDs of the ancestors to measure.
    edges_by_child
        Final hasChild edges keyed by their child final SFI UUID.
    seed_uuid
        Final SFI UUID of the LC-source seed.

    Returns
    -------
    dict[UUID, int]
        Longest root distance keyed by ancestor final SFI UUID.

    Raises
    ------
    ValueError
        If the ancestors contain a hasChild cycle.
    """

    root_distances: dict[UUID, int] = {}
    pending = set(ancestor_uuids)
    while pending:
        resolved_this_pass = False
        for ancestor_uuid in sorted(pending, key=str):
            parent_uuids = [
                edge.parent_final_sfi_uuid
                for edge in edges_by_child[ancestor_uuid]
                if edge.parent_final_sfi_uuid is not None
            ]
            if any(parent_uuid not in root_distances for parent_uuid in parent_uuids):
                continue
            root_distances[ancestor_uuid] = (
                1 + max(root_distances[parent_uuid] for parent_uuid in parent_uuids)
                if parent_uuids
                else 0
            )
            pending.discard(ancestor_uuid)
            resolved_this_pass = True
        if not resolved_this_pass:
            raise ValueError(
                f"LC request building: hasChild cycle detected among the "
                f"ancestors of seed {seed_uuid}; "
                f"{sorted(str(pending_uuid) for pending_uuid in pending)} have no "
                f"path to the framework root."
            )
    return root_distances


def _parent_uuids(
    *, edges_by_child: dict[UUID, list[SFIHasChildEdge]], sfi_uuid: UUID
) -> list[UUID]:
    """List one SFI's direct hasChild parents, excluding the framework root.

    Co-parents are ordered by UUID so that no branch of the hierarchy is
    preferred over another.

    Parameters
    ----------
    edges_by_child
        Final hasChild edges keyed by their child final SFI UUID.
    sfi_uuid
        Final SFI UUID whose direct parents are listed.

    Returns
    -------
    list[UUID]
        Direct parent final SFI UUIDs, empty at the framework root.
    """

    return sorted(
        {
            edge.parent_final_sfi_uuid
            for edge in edges_by_child[sfi_uuid]
            if edge.parent_final_sfi_uuid is not None
        },
        key=str,
    )


def build_lc_generation_requests(
    *,
    academic_standards_bundle: AcademicStandardsKGBundle,
    has_child_edges: Sequence[SFIHasChildEdge],
    kg_dirs: KGDirs,
    lc_config: _CreateKGLearningComponentsConfig,
    lc_eligible_sfis: Sequence[SFIFinalRecord],
    sfi_final_records: Sequence[SFIFinalRecord],
) -> list[LCGenerationRequest]:
    """Build the deterministic LC generation requests.

    Eligible seeds are chunked in selection order
    into batches of `lc_request_batch_size` (default 1, hasChild parity); each
    request carries a deterministic request ID, the framework context, and
    per seed its source text, language tag, direct parents, and hasChild
    ancestor path (disambiguation-only context and the authoritative source of
    grade/curriculum scope). A seed or ancestor may have several hasChild
    parents; every parent's ancestors are carried, and each context entry
    keeps its own parent UUIDs so the hierarchy stays reconstructible. Sibling
    context is added only when `lc_include_sibling_context` is configured.
    Requests never carry statement codes as decomposition input.

    Parameters
    ----------
    academic_standards_bundle
        Compiled Academic Standards KG bundle (framework context).
    has_child_edges
        Final resolved hasChild edges (ancestor and sibling computation).
    kg_dirs
        KG artifact directories; artifacts are written under ``kg_dirs.root``.
    lc_config
        Learning Components runtime configuration.
    lc_eligible_sfis
        Eligible LC-source SFIs, in selection order.
    sfi_final_records
        All final SFI records (ancestor/sibling context lookup).

    Returns
    -------
    list[LCGenerationRequest]
        The LC generation requests, in batch order.

    Raises
    ------
    ValueError
        If an ancestor path cannot be recovered (missing edge, missing record,
        or cycle).
    """

    framework = academic_standards_bundle.framework
    framework_context = LCFrameworkContext(
        academic_subject=framework.academic_subject,
        in_language=framework.in_language,
        jurisdiction=framework.jurisdiction,
        name=framework.name,
    )

    child_uuids_by_parent: dict[Optional[UUID], list[UUID]] = {}
    edges_by_child: dict[UUID, list[SFIHasChildEdge]] = {}
    for edge in has_child_edges:
        edges_by_child.setdefault(edge.child_final_sfi_uuid, []).append(edge)
        child_uuids_by_parent.setdefault(edge.parent_final_sfi_uuid, []).append(
            edge.child_final_sfi_uuid
        )
    for child_edges in edges_by_child.values():
        child_edges.sort(key=lambda edge: str(edge.parent_final_sfi_uuid))

    records_by_uuid = {record.final_sfi_uuid: record for record in sfi_final_records}

    request_sfis: list[LCRequestSFI] = []
    for record in lc_eligible_sfis:
        ancestor_path, ancestor_path_status = _build_ancestor_path(
            edges_by_child=edges_by_child,
            records_by_uuid=records_by_uuid,
            seed_uuid=record.final_sfi_uuid,
        )
        siblings = (
            _collect_siblings(
                child_uuids_by_parent=child_uuids_by_parent,
                edges_by_child=edges_by_child,
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
                parent_uuids=_parent_uuids(
                    edges_by_child=edges_by_child, sfi_uuid=record.final_sfi_uuid
                ),
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
    write_to_json(fp=kg_dirs.root / "lc_generation_requests.jsonl", json_info=requests)

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


def decompose_lc_source_sfis(
    *,
    kg_dirs: KGDirs,
    lc_config: _CreateKGLearningComponentsConfig,
    lc_generation_requests: Sequence[LCGenerationRequest],
    overwrite: bool,
    usage_tracker: KGUsageTracker,
) -> list[LCGenerationResponse]:
    """Decompose the LC-source SFIs into atomic skills via LLM.

    Requests run sequentially in request order, one LLM call at a time; each
    validated response is appended to the responses artifact as it completes,
    so a stopped run resumes from its completed responses (keyed by
    request_id; previously failed requests are retried). A request that still
    fails after model retries is recorded in the failures artifact and the
    run continues; the run raises only when the failed fraction of LC-source
    SFIs exceeds ``lc_max_failure_rate``.

    Parameters
    ----------
    kg_dirs
        KG artifact directories; artifacts are written under ``kg_dirs.root``.
    lc_config
        Learning Components runtime configuration.
    lc_generation_requests
        Deterministic LC generation requests, in request order.
    overwrite
        When True, discard saved responses and regenerate from scratch.
    usage_tracker
        Tracker to accumulate LLM token usage (``lc_generation`` bucket).

    Returns
    -------
    list[LCGenerationResponse]
        Validated responses for all successfully decomposed requests, in
        request order.

    Raises
    ------
    ValueError
        If the failed fraction of LC-source SFIs exceeds
        ``lc_max_failure_rate`` (raised after all artifacts are written).
    """

    draft_responses_fp = kg_dirs.root / "lc_generation_draft_responses.jsonl"
    failures_fp = kg_dirs.root / LC_GENERATION_FAILURES_FN
    responses_fp = kg_dirs.root / "lc_generation_responses.jsonl"
    verdicts_fp = kg_dirs.root / "lc_generation_validation_verdicts.jsonl"
    corrected_count = 0

    if overwrite:
        logger.info("Starting LC generation from scratch because overwrite=True.")
        reset_output_files(
            output_fps=[draft_responses_fp, failures_fp, responses_fp, verdicts_fp]
        )
        completed: dict[str, LCGenerationRun] = {}
    else:
        completed = _load_resumable_lc_generation_runs(
            draft_responses_fp=draft_responses_fp,
            lc_config=lc_config,
            lc_generation_requests=lc_generation_requests,
            responses_fp=responses_fp,
            verdicts_fp=verdicts_fp,
        )
        reset_output_files(
            output_fps=[draft_responses_fp, failures_fp, responses_fp, verdicts_fp]
        )
        for completed_run in completed.values():
            append_jsonl_model(
                fp=draft_responses_fp, model=completed_run.draft_response
            )
            append_jsonl_model(fp=verdicts_fp, model=completed_run.validation_verdict)
            append_jsonl_model(fp=responses_fp, model=completed_run.final_response)

    failures: list[LCGenerationFailure] = []
    responses: list[LCGenerationResponse] = []
    for request in lc_generation_requests:
        cached_run = completed.get(request.request_id)
        if cached_run is not None:
            responses.append(cached_run.final_response)
            if not cached_run.validation_verdict.passed:
                corrected_count += 1
            continue
        try:
            generation_run = generate_learning_components_for_request(
                lc_config=lc_config,
                lc_generation_request=request,
                usage_tracker=usage_tracker,
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.error(
                f"LC generation failed for request {request.request_id}: "
                f"{str(e)[:500]}"
            )
            failures.append(
                LCGenerationFailure(
                    error_message=str(e)[:1000],
                    error_type=e.__class__.__name__,
                    request_id=request.request_id,
                    sfi_uuids=[
                        request_sfi.final_sfi_uuid for request_sfi in request.sfis
                    ],
                )
            )
            continue
        response = generation_run.final_response
        append_jsonl_model(fp=draft_responses_fp, model=generation_run.draft_response)
        append_jsonl_model(fp=verdicts_fp, model=generation_run.validation_verdict)
        append_jsonl_model(fp=responses_fp, model=response)
        responses.append(response)
        if not generation_run.validation_verdict.passed:
            corrected_count += 1

    make_dir(kg_dirs.root)
    write_to_json(
        fp=failures_fp,
        json_info=[failure.model_dump(mode="json") for failure in failures],
    )

    failed_sfi_count = sum(len(failure.sfi_uuids) for failure in failures)
    total_sfi_count = sum(len(request.sfis) for request in lc_generation_requests)
    skill_count = sum(
        len(item.skills) for response in responses for item in response.items
    )
    logger.success(
        f"Decomposed LC-source SFIs: requests={len(lc_generation_requests)}; "
        f"resumed={len(completed)}; "
        f"succeeded={len(responses)}; "
        f"failed_requests={len(failures)}; "
        f"failed_sfis={failed_sfi_count}; "
        f"validator_corrected={corrected_count}; "
        f"atomic_skills={skill_count}"
    )

    if (
        total_sfi_count
        and failed_sfi_count / total_sfi_count > lc_config.lc_max_failure_rate
    ):
        raise ValueError(
            f"LC generation failure rate "
            f"{failed_sfi_count / total_sfi_count:.3f} "
            f"({failed_sfi_count}/{total_sfi_count} LC-source SFIs) exceeds "
            f"lc_max_failure_rate={lc_config.lc_max_failure_rate}. See "
            f"{failures_fp} for per-request errors; re-run without overwrite "
            f"to retry only the failed requests."
        )

    return responses
