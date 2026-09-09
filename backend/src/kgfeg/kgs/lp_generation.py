"""Bounded LP requests and reconciled, complete pre-call artifact populations.

Requests contain only explicit endpoint permissions and bounded upstream evidence. They
grant no relationship. The artifact writer returns only after the entire
candidate/request population has been read back and matched against current material
inputs. Sequential execution validates and checkpoints independent producer/checker
stages, preserving failed attempts for safe resume. Processing completion and
structural reconciliation are not semantic validation.
"""

# Future Library
from __future__ import annotations

# Standard Library
import fcntl
import hashlib

from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

# Third Party Library
from pydantic import JsonValue
from pydantic_ai.usage import RunUsage

# Package Library
from kgfeg.config import Settings
from kgfeg.kgs import prompts
from kgfeg.kgs.agents import (
    create_lp_generation_agent,
    create_lp_generation_validation_agent,
)
from kgfeg.kgs.lp_candidates import (
    LPCandidatePopulation,
    build_lp_candidates,
    validate_lp_candidate_population,
)
from kgfeg.kgs.lp_checkpoints import (
    LPGenerationCheckpoints,
    archive_lp_generation_artifacts,
)
from kgfeg.kgs.lp_checkpoints import content_hash as checkpoint_content_hash
from kgfeg.kgs.lp_checkpoints import (
    reconciled_response,
)
from kgfeg.kgs.lp_coordinates import LPDevelopmentalCoordinate
from kgfeg.kgs.lp_index import LPGraphIndex, build_lp_graph_index
from kgfeg.kgs.lp_requests import (
    LPBoundedText,
    LPContextSFI,
    LPCoordinateContext,
    LPGenerationRequest,
    LPLearningComponentContext,
    LPNominationContext,
    LPRequestManifest,
    LPRequestPair,
    LPRequestPopulation,
    LPRequestSFI,
    LPSourceEvidence,
    build_lp_request_id,
    canonical_lp_json,
    lp_material_content_hash,
    lp_text_content_hash,
)
from kgfeg.kgs.lp_selection import LPSFIEligibility, build_lp_selection
from kgfeg.kgs.prompts import (
    build_lp_generation_prompt,
    validate_lp_generation_response,
)
from kgfeg.kgs.schemas import (
    AcademicStandardsLCKGBundle,
    LPCandidatePair,
    LPCandidateSummary,
    LPGenerationResponse,
    LPGenerationValidationVerdict,
)
from kgfeg.kgs.utils import KGDirs
from kgfeg.kgs.validators import (
    verify_lp_generation_response_integrity,
    verify_lp_generation_validation_integrity,
)
from kgfeg.model_registry import ModelConfig
from kgfeg.schemas import CreateKGConfig

if TYPE_CHECKING:
    # Package Library
    from kgfeg.kgs.llm import KGUsageTracker

_MANIFEST_FILENAME = "lp_generation_requests_manifest.json"
_REQUESTS_FILENAME = "lp_generation_requests.jsonl"


class LPGenerationFailed(RuntimeError):
    """A request exhausted its stage retries; LP processing cannot report success."""


def _artifact_payloads(
    *, candidates: LPCandidatePopulation, requests: tuple[LPGenerationRequest, ...]
) -> dict[str, bytes]:
    """Serialize the complete deterministic candidate and request files.

    Parameters
    ----------
    candidates
        Validated ranked candidates and their summary.
    requests
        Complete bounded request sequence.

    Returns
    -------
    dict[str, bytes]
        Canonical UTF-8 payloads, with one newline after each JSONL row.
    """

    return {
        "lp_candidate_pairs.jsonl": "".join(
            canonical_lp_json(candidate.model_dump(mode="json")) + "\n"
            for candidate in candidates.candidates
        ).encode("utf-8"),
        "lp_candidate_summary.json": (
            canonical_lp_json(candidates.summary.model_dump(mode="json")) + "\n"
        ).encode("utf-8"),
        _REQUESTS_FILENAME: "".join(
            canonical_lp_json(request.model_dump(mode="json")) + "\n"
            for request in requests
        ).encode("utf-8"),
    }


def _audit_context(record: LPSFIEligibility) -> dict[str, JsonValue]:
    """Preserve complete known AS audit, code-normalization, and merge fields.

    Parameters
    ----------
    record
        Authoritative SFI and separately preserved upstream item provenance.

    Returns
    -------
    dict[str, JsonValue]
        Origin-qualified audit fields, including disagreements between upstream copies.
    """

    return {
        f"{origin}.{key}": value
        for origin, mapping in (
            ("metadata", record.sfi.model_dump(mode="json")["metadata"]),
            ("provenance", record.source_provenance),
        )
        for key, value in sorted(mapping.items())
        if key.startswith(("audit", "merge"))
        or key in {"normalized_statement_code", "statement_value_canonicalization"}
    }


def _bounded_text(*, max_characters: int, text: str) -> LPBoundedText:
    """Bound a text value while identifying the original material exactly.

    Parameters
    ----------
    max_characters
        Nonnegative excerpt length in Unicode characters.
    text
        Complete original text, never synthesized from a source PDF.

    Returns
    -------
    LPBoundedText
        Prefix excerpt with explicit original length, hash, and truncation status.
    """

    return LPBoundedText(
        content_hash=lp_text_content_hash(text),
        original_characters=len(text),
        text=text[:max_characters],
        truncated=len(text) > max_characters,
    )


def _call_lp_stage(
    *,
    draft: LPGenerationResponse | None,
    kg_config: CreateKGConfig,
    model_config: ModelConfig,
    request: LPGenerationRequest,
    usage_tracker: KGUsageTracker,
) -> LPGenerationResponse | LPGenerationValidationVerdict:
    """Execute one bounded agent attempt with orchestration-owned retry accounting.

    Parameters
    ----------
    draft
        Validated draft for checker execution, absent for a producer attempt.
    kg_config
        Effective curriculum instructions.
    model_config
        Captured shared KG model and actual Learning Progressions settings.
    request
        One request from the complete reconciled on-disk population.
    usage_tracker
        Existing producer/checker token accounting buckets.

    Returns
    -------
    LPGenerationResponse or LPGenerationValidationVerdict
        Untrusted stage output for deterministic validation before checkpointing.
    """

    config = kg_config.learning_progressions

    if draft is None:
        prompt = build_lp_generation_prompt(
            lp_generation_request=request,
            producer_instructions=config.producer_instructions,
        )
        agent = create_lp_generation_agent(
            instructions=prompt.system_message, max_retries=0, model_config=model_config
        )
        bucket = usage_tracker.lp_generation
    else:
        prompt = validate_lp_generation_response(
            checker_instructions=config.checker_instructions,
            draft_response=draft,
            lp_generation_request=request,
            producer_instructions=config.producer_instructions,
        )
        agent = create_lp_generation_validation_agent(
            draft_response=draft,
            instructions=prompt.system_message,
            lp_generation_request=request,
            max_retries=0,
            model_config=model_config,
            verify_integrity_fn=verify_lp_generation_validation_integrity,
        )
        bucket = usage_tracker.lp_generation_validation

    usage = RunUsage()

    try:
        run = agent.run_sync(usage=usage, user_prompt=prompt.user_message)
        return run.output
    finally:
        bucket.add_run_usage(usage)


def _context_sfi(*, max_characters: int, record: LPSFIEligibility) -> LPContextSFI:
    """Project SFI text and preserve audit state without unbounded metadata copying.

    Complete audit state is mandatory. If it exceeds the configured text ceiling,
    construction fails rather than hiding known anomalies behind an excerpt.

    Parameters
    ----------
    max_characters
        Configured per-SFI text ceiling, also applied to each descriptive field.
    record
        Authoritative endpoint or ancestor context.

    Returns
    -------
    LPContextSFI
        Bounded SFI descriptions and exact audit warnings.

    Raises
    ------
    ValueError
        If complete mandatory audit context cannot fit the configured ceiling.
    """

    audit = _audit_context(record)

    if audit and len(canonical_lp_json(audit)) > max_characters:
        raise ValueError(
            f"LP SFI {record.sfi.case_identifier_uuid} audit context exceeds "
            f"max_source_evidence_characters_per_sfi; increase the evidence bound."
        )

    warnings = list(record.warnings)

    if any(value for value in audit.values()):
        warnings.append(
            f"SFI {record.sfi.case_identifier_uuid} carries upstream audit/merge/code "
            f"context; inspect audit_context before interpreting its evidence."
        )

    description = _bounded_text(
        max_characters=max_characters, text=record.sfi.description
    )

    if description.truncated:
        warnings.append(
            f"SFI {record.sfi.case_identifier_uuid} description is truncated."
        )

    return LPContextSFI(
        alternate_statement_code=(
            _bounded_text(
                max_characters=max_characters, text=record.sfi.alternate_statement_code
            )
            if record.sfi.alternate_statement_code is not None
            else None
        ),
        audit_context=audit,
        description=description,
        normalized_statement_type=record.sfi.normalized_statement_type,
        root_fallback_relationship_uuids=record.root_fallback_relationship_uuids,
        sfi_uuid=record.sfi.case_identifier_uuid,
        statement_code=(
            _bounded_text(max_characters=max_characters, text=record.sfi.statement_code)
            if record.sfi.statement_code is not None
            else None
        ),
        statement_type=record.statement_type,
        upstream_sfi_content_hash=lp_material_content_hash(
            record.sfi.model_dump(mode="json")
        ),
        unresolved_ancestry=record.unresolved_ancestry,
        warnings=tuple(sorted(set(warnings))),
    )


def _coordinate_context(
    *, coordinate: LPDevelopmentalCoordinate, max_characters: int, max_items: int
) -> LPCoordinateContext:
    """Bound coordinate origins without changing resolved values or local ranks.

    Parameters
    ----------
    coordinate
        Authoritative resolved or missing coordinate with complete ordered origins.
    max_characters
        Aggregate Unicode character ceiling for retained origin excerpts.
    max_items
        Maximum number of retained origin references, including excerpt metadata.

    Returns
    -------
    LPCoordinateContext
        Unchanged canonical semantics and explicitly bounded provenance linked to the
        complete original references, including those omitted from the request.
    """

    references: list[LPBoundedText] = []
    remaining = max_characters

    for source_field in coordinate.source_fields[:max_items]:
        if remaining == 0:
            break

        reference = _bounded_text(max_characters=remaining, text=source_field)
        references.append(reference)
        remaining -= len(reference.text)

    return LPCoordinateContext(
        canonical_value=coordinate.canonical_value,
        omitted_source_field_count=len(coordinate.source_fields) - len(references),
        rank=coordinate.rank,
        sfi_uuid=coordinate.sfi_uuid,
        source_fields=tuple(references),
        source_fields_content_hash=lp_material_content_hash(coordinate.source_fields),
        statement_type=coordinate.statement_type,
        status=coordinate.status,
    )


def _execution_material(
    *, kg_config: CreateKGConfig, population: LPRequestPopulation
) -> dict[str, Any]:
    """Capture actual configuration, model, schemas, and prompt definition material.

    Parameters
    ----------
    kg_config
        Complete effective KG configuration, including LP retry budgets.
    population
        Reconciled upstream, candidate, and request content identities.

    Returns
    -------
    dict[str, Any]
        Code-owned execution fingerprint inputs, with no manual version selectors.
    """

    model = Settings.llm_config("kgs")
    return {
        "checker_instructions": kg_config.learning_progressions.checker_instructions,
        "config_content_hash": checkpoint_content_hash(
            kg_config.model_dump(by_alias=True, mode="json")
        ),
        "model_config": model.model_dump(mode="json"),
        "model_settings": dict(model.kgs_settings("learning_progressions")),
        "producer_instructions": kg_config.learning_progressions.producer_instructions,
        "prompt_definitions_content_hash": hashlib.sha256(
            Path(prompts.__file__).read_bytes()
        ).hexdigest(),
        "request_manifest": population.manifest.model_dump(mode="json"),
        "response_schema_content_hash": checkpoint_content_hash(
            LPGenerationResponse.model_json_schema()
        ),
        "retry_limits": {
            "draft": kg_config.learning_progressions.retry.producer_max_retries,
            "verdict": kg_config.learning_progressions.retry.checker_max_retries,
        },
        "verdict_schema_content_hash": checkpoint_content_hash(
            LPGenerationValidationVerdict.model_json_schema()
        ),
    }


def _finish_lp_request(
    *,
    kg_config: CreateKGConfig,
    model_config: ModelConfig,
    population: LPRequestPopulation,
    request_index: int,
    store: LPGenerationCheckpoints,
    usage_tracker: KGUsageTracker,
) -> None:
    """Resume one request at its earliest unfinished validated stage.

    Parameters
    ----------
    kg_config
        Captured effective policy and independent stage retry counts.
    model_config
        Captured shared KG model configuration.
    population
        Complete materialized request sequence.
    request_index
        First incomplete request position.
    store
        Validated locked checkpoint store.
    usage_tracker
        Existing LP token accounting buckets.
    """

    request = population.requests[request_index]
    retries = kg_config.learning_progressions.retry

    for stage, maximum in (
        ("draft", retries.producer_max_retries),
        ("verdict", retries.checker_max_retries),
    ):
        if request_index < len(store.rows[stage]):
            continue

        for attempt in range(1, maximum + 2):
            _verify_execution_material(
                kg_config=kg_config, population=population, store=store
            )
            draft = (
                None
                if stage == "draft"
                else LPGenerationResponse.model_validate(
                    store.rows["draft"][request_index].payload
                )
            )

            try:
                output = _call_lp_stage(
                    draft=draft,
                    kg_config=kg_config,
                    model_config=model_config,
                    request=request.model_copy(deep=True),
                    usage_tracker=usage_tracker,
                )

                if stage == "draft":
                    response = LPGenerationResponse.model_validate(
                        output.model_dump(mode="python")
                    )
                    verify_lp_generation_response_integrity(
                        lp_generation_request=request, lp_generation_response=response
                    )
                    validated: LPGenerationResponse | LPGenerationValidationVerdict = (
                        response
                    )
                else:
                    if draft is None:
                        raise ValueError("LP checker execution requires a valid draft.")

                    verdict = LPGenerationValidationVerdict.model_validate(
                        output.model_dump(mode="python")
                    )
                    verify_lp_generation_validation_integrity(
                        draft_response=draft,
                        lp_generation_request=request,
                        validation_verdict=verdict,
                    )
                    validated = verdict

            except Exception as error:  # pylint: disable=broad-exception-caught
                _verify_execution_material(
                    kg_config=kg_config, population=population, store=store
                )
                exhausted = attempt == maximum + 1
                store.record_failure(
                    attempt=attempt,
                    error=error,
                    exhausted=exhausted,
                    request_index=request_index,
                    stage=stage,
                )

                if exhausted:
                    raise LPGenerationFailed(
                        f"LP {stage} failed for request {request.request_id} after "
                        f"{attempt} attempts; processing halted. Inspect "
                        f"lp_generation_failures.json."
                    ) from None

                continue

            _verify_execution_material(
                kg_config=kg_config, population=population, store=store
            )
            store.append(payload=validated, request_index=request_index, stage=stage)
            break

    draft = LPGenerationResponse.model_validate(
        store.rows["draft"][request_index].payload
    )
    verdict = LPGenerationValidationVerdict.model_validate(
        store.rows["verdict"][request_index].payload
    )
    store.append(
        payload=reconciled_response(draft=draft, verdict=verdict),
        request_index=request_index,
        stage="response",
    )


def _nomination_context(
    *,
    candidate: LPCandidatePair,
    max_characters: int,
    sfis: tuple[LPRequestSFI, LPRequestSFI],
) -> LPNominationContext:
    """Project nomination references through the retained LC and ancestor context.

    Candidate aggregates still describe the original nomination, not a recomputed
    signal. Omitted LC/ancestor references and their identity-bearing values are
    removed explicitly so nomination excerpts cannot bypass per-SFI context limits.

    Parameters
    ----------
    candidate
        Complete original nomination record, preserved separately on disk.
    max_characters
        Aggregate character ceiling for the projected nomination excerpt.
    sfis
        The two independently bounded endpoint contexts for this pair.

    Returns
    -------
    LPNominationContext
        Original evidence hash and a bounded, explicitly partial projection.
    """

    lc_ids = {
        str(component.identifier)
        for sfi in sfis
        for component in sfi.learning_components
    }
    shared_lc_ids = {
        str(component.identifier) for component in sfis[0].learning_components
    } & {str(component.identifier) for component in sfis[1].learning_components}
    sfi_ids = {
        str(node.sfi_uuid) for sfi in sfis for node in (sfi.context, *sfi.ancestors)
    }
    allowed_references = {
        *(f"lc:{identifier}" for identifier in lc_ids),
        *(f"sfi:{identifier}" for identifier in sfi_ids),
    }
    original = [evidence.model_dump(mode="json") for evidence in candidate.evidence]
    projected = []
    truncated = False

    for evidence in original:
        retained = dict(evidence)
        retained["aggregate_scope"] = "complete_candidate_nomination"
        references = evidence["references"]
        retained["references"] = [
            ref for ref in references if ref in allowed_references
        ]
        retained["omitted_reference_count"] = len(references) - len(
            retained["references"]
        )
        truncated |= bool(retained["omitted_reference_count"])
        values = dict(evidence["triggering_values"])

        if evidence["evidence_type"] == "shared_learning_components":
            shared = values["shared_values"]
            values["shared_values"] = [
                identifier for identifier in shared if identifier in shared_lc_ids
            ]
            retained["omitted_shared_value_count"] = len(shared) - len(
                values["shared_values"]
            )
            truncated |= bool(retained["omitted_shared_value_count"])

        if "shared_ancestors" in values:
            ancestors = values["shared_ancestors"]
            values["shared_ancestors"] = [
                ancestor for ancestor in ancestors if ancestor["sfi_uuid"] in sfi_ids
            ]
            retained["omitted_shared_ancestor_count"] = len(ancestors) - len(
                values["shared_ancestors"]
            )
            truncated |= bool(retained["omitted_shared_ancestor_count"])

        retained["triggering_values"] = values
        projected.append(retained)

    return LPNominationContext(
        complete_evidence_content_hash=lp_material_content_hash(original),
        evidence=_bounded_text(
            max_characters=max_characters, text=canonical_lp_json(projected)
        ),
        evidence_types=tuple(evidence.evidence_type for evidence in candidate.evidence),
        references_truncated=truncated,
    )


def _read_population(
    *, expected: LPRequestPopulation, root: Path
) -> LPRequestPopulation:
    """Read every complete artifact and reconcile bytes, schemas, counts, and content.

    Parameters
    ----------
    expected
        Independently constructed population from current authoritative inputs.
    root
        Directory containing the complete materialized population.

    Returns
    -------
    LPRequestPopulation
        Round-trip validated records read from disk, matching current inputs exactly.

    Raises
    ------
    OSError
        If a required artifact cannot be read.
    ValueError
        If bytes, identities, order, schemas, counts, or material inputs differ.
    """

    payloads = _artifact_payloads(
        candidates=expected.candidates, requests=expected.requests
    )
    actual = {filename: (root / filename).read_bytes() for filename in payloads}

    for filename, payload in actual.items():
        if payload != payloads[filename]:
            raise ValueError(
                f"LP pre-call artifact differs from current material: {filename}."
            )

    candidates = tuple(
        LPCandidatePair.model_validate_json(line)
        for line in actual["lp_candidate_pairs.jsonl"].splitlines()
    )
    summary = LPCandidateSummary.model_validate_json(
        actual["lp_candidate_summary.json"]
    )
    requests = tuple(
        LPGenerationRequest.model_validate_json(line)
        for line in actual[_REQUESTS_FILENAME].splitlines()
    )
    manifest_bytes = (root / _MANIFEST_FILENAME).read_bytes()
    manifest = LPRequestManifest.model_validate_json(manifest_bytes)

    if manifest != expected.manifest or manifest_bytes != (
        canonical_lp_json(expected.manifest.model_dump(mode="json")) + "\n"
    ).encode("utf-8"):
        raise ValueError(
            "LP request manifest does not match current material and counts."
        )

    if manifest.artifact_byte_hashes != {
        filename: hashlib.sha256(payload).hexdigest()
        for filename, payload in actual.items()
    }:
        raise ValueError(
            "LP request manifest byte hashes do not match materialized files."
        )

    if (
        len(candidates) != manifest.total_candidate_pairs
        or len(requests) != manifest.total_requests
        or tuple(pair.pair_id for request in requests for pair in request.pairs)
        != tuple(candidate.pair_id for candidate in candidates)
    ):
        raise ValueError("LP materialized candidate/request counts or coverage differ.")

    return LPRequestPopulation(
        candidates=LPCandidatePopulation(candidates=candidates, summary=summary),
        manifest=manifest,
        requests=requests,
    )


def _request_pair(
    *,
    candidate: LPCandidatePair,
    max_characters: int,
    sfis: tuple[LPRequestSFI, LPRequestSFI],
) -> LPRequestPair:
    """Retain candidate identity and permissions without bypassing evidence bounds.

    Parameters
    ----------
    candidate
        One full, validated candidate from the ranked population.
    max_characters
        Maximum characters in the combined nomination-evidence excerpt.
    sfis
        Both bounded endpoint contexts used to restrict nomination references.

    Returns
    -------
    LPRequestPair
        Exact candidate linkage and bounded named nomination evidence.
    """

    return LPRequestPair(
        admissible_decisions=tuple(candidate.admissible_decisions),
        candidate_content_hash=lp_material_content_hash(
            candidate.model_dump(mode="json")
        ),
        first_sfi_uuid=candidate.first_sfi_uuid,
        nomination=_nomination_context(
            candidate=candidate, max_characters=max_characters, sfis=sfis
        ),
        pair_id=candidate.pair_id,
        second_sfi_uuid=candidate.second_sfi_uuid,
        warnings=tuple(candidate.warnings),
    )


def _request_sfi(
    *,
    graph_index: LPGraphIndex,
    kg_config: CreateKGConfig,
    records: dict[UUID, LPSFIEligibility],
    sfi_uuid: UUID,
) -> LPRequestSFI:
    """Build bounded endpoint context without dropping a direct-parent DAG branch.

    Parameters
    ----------
    graph_index
        Validated DAG and support adjacency, excluding root fallbacks as evidence.
    kg_config
        Current explicit evidence limits.
    records
        Complete selection records, including ancestors that are not LP endpoints.
    sfi_uuid
        Exact candidate endpoint to project.

    Returns
    -------
    LPRequestSFI
        Bounded SFI, all retained-depth parent paths, supporting LCs, and source text.

    Raises
    ------
    ValueError
        If the path-count ceiling would omit a branch or audit context cannot fit.
    """

    limits = kg_config.learning_progressions.evidence_limits
    max_characters = limits.max_source_evidence_characters_per_sfi
    record = records[sfi_uuid]
    paths = graph_index.ancestor_paths(
        max_depth=limits.max_ancestor_path_depth,
        max_paths=limits.max_ancestor_paths_per_sfi,
        sfi_uuid=sfi_uuid,
    )

    if paths.paths_truncated:
        raise ValueError(
            f"LP SFI {sfi_uuid} has more DAG paths than max_ancestor_paths_per_sfi; "
            f"increase the bound to preserve every parent branch."
        )

    context = _context_sfi(max_characters=max_characters, record=record)
    ancestors = tuple(
        _context_sfi(max_characters=max_characters, record=records[ancestor_uuid])
        for ancestor_uuid in sorted(
            {ancestor for path in paths.paths for ancestor in path.ancestor_sfi_uuids},
            key=str,
        )
    )
    components = graph_index.learning_components_by_sfi_uuid[sfi_uuid]
    retained_components = tuple(
        LPLearningComponentContext(
            description=_bounded_text(
                max_characters=max_characters, text=component.description
            ),
            identifier=component.identifier,
            metadata=_bounded_text(
                max_characters=max_characters,
                text=canonical_lp_json(component.model_dump(mode="json")["metadata"]),
            ),
            upstream_content_hash=lp_material_content_hash(
                component.model_dump(mode="json")
            ),
        )
        for component in components[: limits.max_learning_components_per_sfi]
    )
    coordinate = _coordinate_context(
        coordinate=record.coordinate,
        max_characters=max_characters,
        max_items=limits.max_source_evidence_items_per_sfi,
    )
    source_values = _source_values(record)
    source_evidence: list[LPSourceEvidence] = []
    remaining = max_characters

    for reference, value in source_values[: limits.max_source_evidence_items_per_sfi]:
        if remaining == 0:
            break

        excerpt = _bounded_text(max_characters=remaining, text=value)
        source_evidence.append(
            LPSourceEvidence(
                excerpt=excerpt,
                reference=_bounded_text(max_characters=max_characters, text=reference),
            )
        )
        remaining -= len(excerpt.text)

    warnings = set(context.warnings)
    warnings.update(warning for ancestor in ancestors for warning in ancestor.warnings)
    omitted_components = len(components) - len(retained_components)
    omitted_source = len(source_values) - len(source_evidence)

    if any(path.depth_truncated for path in paths.paths):
        warnings.add(
            f"SFI {sfi_uuid} ancestor paths are depth-truncated; all bounded branches remain."
        )

    if omitted_components or any(
        component.description.truncated or component.metadata.truncated
        for component in retained_components
    ):
        warnings.add(f"SFI {sfi_uuid} supporting LC evidence is truncated.")

    if omitted_source or any(
        item.excerpt.truncated or item.reference.truncated for item in source_evidence
    ):
        warnings.add(f"SFI {sfi_uuid} source evidence is truncated.")

    if coordinate.omitted_source_field_count or any(
        reference.truncated for reference in coordinate.source_fields
    ):
        warnings.add(f"SFI {sfi_uuid} coordinate source references are truncated.")

    return LPRequestSFI(
        ancestor_paths=paths.paths,
        ancestors=ancestors,
        context=context,
        coordinate=coordinate,
        learning_components=retained_components,
        learning_components_content_hash=lp_material_content_hash(
            [component.model_dump(mode="json") for component in components]
        ),
        omitted_learning_component_count=omitted_components,
        omitted_source_evidence_count=omitted_source,
        parent_sfi_uuids=record.parent_sfi_uuids,
        source_evidence=tuple(source_evidence),
        source_evidence_content_hash=lp_material_content_hash(source_values),
        warnings=tuple(sorted(warnings)),
    )


def _source_values(record: LPSFIEligibility) -> list[tuple[str, str]]:
    """Enumerate preserved source values with stable origin and item references.

    Source text is prioritized before other metadata. Audit fields have their own
    complete context and are not competing with excerpts for source item slots.

    Parameters
    ----------
    record
        Final SFI metadata and provenance to project without accessing a PDF.

    Returns
    -------
    list[tuple[str, str]]
        Deterministic source references and complete text for later bounded selection.
    """

    values: dict[str, str] = {}
    audit_keys = set(_audit_context(record))

    for origin, mapping in (
        ("metadata", record.sfi.model_dump(mode="json")["metadata"]),
        ("provenance", record.source_provenance),
    ):
        for key, value in sorted(mapping.items()):
            if f"{origin}.{key}" in audit_keys:
                continue

            items = value if isinstance(value, list) else [value]

            for index, item in enumerate(items):
                values[f"{origin}.{key}[{index}]"] = (
                    item if isinstance(item, str) else canonical_lp_json(item)
                )

    return sorted(
        values.items(),
        key=lambda item: ("candidate_source_texts[" not in item[0], item[0]),
    )


def _verify_execution_material(
    *,
    kg_config: CreateKGConfig,
    population: LPRequestPopulation,
    store: LPGenerationCheckpoints,
) -> None:
    """Reconcile population and checkpoint material immediately around every call.

    Parameters
    ----------
    kg_config
        Captured effective configuration.
    population
        Complete authoritative request population.
    store
        Current validated checkpoint state and execution material.

    Raises
    ------
    ValueError
        If the LP prompt, model, or material changed during execution.
    """

    _read_population(expected=population, root=store.root)

    if (
        _execution_material(kg_config=kg_config, population=population)
        != store.material
    ):
        raise ValueError("LP prompt or model material changed during execution.")

    store.verify_bytes()


def build_lp_generation_requests(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
) -> LPRequestPopulation:
    """Construct the complete bounded request population without file or model access.

    Candidate ranking order is preserved exactly through contiguous batches. Every
    endpoint appears once per request, and every candidate appears once overall.
    Request identities include all current input hashes and actual bounded context.

    Parameters
    ----------
    as_lc_bundle
        Final validated AS+LC bundle for one framework.
    doc_key
        Document key matching authoritative framework identity.
    kg_config
        Validated curriculum policy, request batch size, and evidence bounds.

    Returns
    -------
    LPRequestPopulation
        Complete in-memory population; call the artifact writer before any execution.

    Raises
    ------
    ValueError
        If current material, candidate reconciliation, or context bounds are invalid.
    """

    bundle = AcademicStandardsLCKGBundle.model_validate_json(
        as_lc_bundle.model_dump_json()
    )
    config = CreateKGConfig.model_validate_json(
        kg_config.model_dump_json(by_alias=True)
    )
    population = validate_lp_candidate_population(
        as_lc_bundle=bundle,
        doc_key=doc_key,
        kg_config=config,
        population=build_lp_candidates(
            as_lc_bundle=bundle, doc_key=doc_key, kg_config=config
        ),
    )
    summary = population.summary
    doc_key = doc_key.strip()
    graph_index = build_lp_graph_index(bundle)
    selection = build_lp_selection(as_lc_bundle=bundle, kg_config=config)
    records = {record.sfi.case_identifier_uuid: record for record in selection.sfis}
    endpoint_uuids = sorted(
        {
            endpoint
            for pair in population.candidates
            for endpoint in (pair.first_sfi_uuid, pair.second_sfi_uuid)
        },
        key=str,
    )
    contexts = {
        sfi_uuid: _request_sfi(
            graph_index=graph_index,
            kg_config=config,
            records=records,
            sfi_uuid=sfi_uuid,
        )
        for sfi_uuid in endpoint_uuids
    }
    batch_size = config.learning_progressions.request_batch_size
    max_characters = (
        config.learning_progressions.evidence_limits.max_source_evidence_characters_per_sfi
    )
    summary_hash = lp_material_content_hash(summary.model_dump(mode="json"))
    requests: list[LPGenerationRequest] = []

    for start in range(0, len(population.candidates), batch_size):
        batch = population.candidates[start : start + batch_size]
        pairs = tuple(
            _request_pair(
                candidate=candidate,
                max_characters=2 * max_characters,
                sfis=(
                    contexts[candidate.first_sfi_uuid],
                    contexts[candidate.second_sfi_uuid],
                ),
            )
            for candidate in batch
        )
        sfis = tuple(
            contexts[sfi_uuid]
            for sfi_uuid in sorted(
                {
                    endpoint
                    for pair in batch
                    for endpoint in (pair.first_sfi_uuid, pair.second_sfi_uuid)
                },
                key=str,
            )
        )
        warnings = {warning for sfi in sfis for warning in sfi.warnings}
        warnings.update(warning for pair in pairs for warning in pair.warnings)
        warnings.update(
            f"Pair {pair.pair_id} nomination evidence is truncated; complete evidence remains in the candidate artifact."
            for pair in pairs
            if pair.nomination.evidence.truncated
            or pair.nomination.references_truncated
        )
        material = {
            "candidate_pairs_content_hash": summary.candidate_pairs_content_hash,
            "candidate_summary_content_hash": summary_hash,
            "config_content_hash": summary.config_content_hash,
            "doc_key": doc_key,
            "eligible_sfis_content_hash": summary.eligible_sfis_content_hash,
            "framework_title": _bounded_text(
                max_characters=max_characters, text=bundle.framework.name
            ).model_dump(mode="json"),
            "framework_uuid": str(summary.framework_uuid),
            "pairs": [pair.model_dump(mode="json") for pair in pairs],
            "request_index": len(requests),
            "sfis": [sfi.model_dump(mode="json") for sfi in sfis],
            "upstream_content_hash": summary.upstream_content_hash,
            "warnings": sorted(warnings),
        }
        content_hash = lp_material_content_hash(material)
        requests.append(
            LPGenerationRequest.model_validate(
                {
                    **material,
                    "request_content_hash": content_hash,
                    "request_id": build_lp_request_id(content_hash),
                }
            )
        )
    request_rows = tuple(requests)

    if tuple(
        pair.pair_id for request in request_rows for pair in request.pairs
    ) != tuple(candidate.pair_id for candidate in population.candidates):
        raise ValueError(
            "LP request population does not exactly cover candidates in order."
        )

    if len({request.request_id for request in request_rows}) != len(request_rows):
        raise ValueError("LP request population contains an identifier collision.")

    payloads = _artifact_payloads(candidates=population, requests=request_rows)
    manifest = LPRequestManifest(
        artifact_byte_hashes={
            filename: hashlib.sha256(payload).hexdigest()
            for filename, payload in payloads.items()
        },
        candidate_pairs_content_hash=summary.candidate_pairs_content_hash,
        candidate_summary_content_hash=summary_hash,
        config_content_hash=summary.config_content_hash,
        doc_key=doc_key,
        eligible_sfis_content_hash=summary.eligible_sfis_content_hash,
        framework_uuid=summary.framework_uuid,
        pair_ids=tuple(candidate.pair_id for candidate in population.candidates),
        request_ids=tuple(request.request_id for request in request_rows),
        requests_content_hash=lp_material_content_hash(
            [request.model_dump(mode="json") for request in request_rows]
        ),
        total_candidate_pairs=len(population.candidates),
        total_requests=len(request_rows),
        upstream_content_hash=summary.upstream_content_hash,
    )
    return LPRequestPopulation(
        candidates=population, manifest=manifest, requests=request_rows
    )


def generate_learning_progressions(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
    overwrite: bool,
    usage_tracker: KGUsageTracker,
) -> tuple[LPGenerationResponse, ...]:
    """Materialize, execute, and resume bounded LP producer/checker adjudication.

    Each request finishes before another starts. Valid completed calls are reused; a
    saved draft survives checker failure. Every failed attempt is retained outside
    successful prefixes, with later completion linked to the resumed run ordinal. No
    relationship finalization, graph validation, or release status is performed.

    Parameters
    ----------
    as_lc_bundle
        Final validated upstream AS+LC bundle for this document.
    doc_key
        Authoritative document identity.
    kg_config
        Effective curriculum policy, evidence bounds, and stage retry counts.
    kg_dirs
        Directory receiving the complete request and checkpoint artifacts.
    overwrite
        Explicit regeneration archives prior evidence before replacing affected files.
        False requires exact current material and validated stage-prefix reuse.
    usage_tracker
        Existing LP producer/checker token accounting buckets.

    Returns
    -------
    tuple[LPGenerationResponse, ...]
        Complete ordered accepted/corrected responses, including normal negative and
        ambiguous judgments. Processing completion does not establish semantic truth.

    Raises
    ------
    LPGenerationFailed
        If any request exhausts producer or checker retries; no partial success returns.
    ValueError
        If persisted material is malformed, stale, truncated, or misaligned.
    OSError
        If the directory is locked by another writer or persistence fails.
    """

    if not isinstance(overwrite, bool):
        raise ValueError("LP overwrite must be an explicit boolean.")

    bundle = AcademicStandardsLCKGBundle.model_validate_json(
        as_lc_bundle.model_dump_json()
    )
    config = CreateKGConfig.model_validate_json(
        kg_config.model_dump_json(by_alias=True)
    )

    # Complete deterministic construction must succeed before replacing any evidence.
    expected = build_lp_generation_requests(
        as_lc_bundle=bundle, doc_key=doc_key, kg_config=config
    )
    kg_dirs.root.mkdir(exist_ok=True, parents=True)

    with (kg_dirs.root / ".lp_generation.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        if overwrite:
            archive_lp_generation_artifacts(kg_dirs.root)

        input_names = (*expected.manifest.artifact_byte_hashes, _MANIFEST_FILENAME)
        execution_names = (
            "lp_generation_checkpoint_manifest.json",
            "lp_generation_checkpoint_transaction.json",
            "lp_generation_failures.json",
            "lp_generation_draft_responses.jsonl",
            "lp_generation_validation_verdicts.jsonl",
            "lp_generation_responses.jsonl",
        )

        if any(
            (kg_dirs.root / name).exists() for name in (*input_names, *execution_names)
        ):
            population = _read_population(expected=expected, root=kg_dirs.root)
        else:
            population = write_lp_generation_request_artifacts(
                as_lc_bundle=bundle, doc_key=doc_key, kg_config=config, kg_dirs=kg_dirs
            )

        material = _execution_material(kg_config=config, population=population)
        store = LPGenerationCheckpoints(
            material=material, population=population, root=kg_dirs.root
        )
        store.begin_run()
        model = ModelConfig.model_validate(material["model_config"])

        for index in range(len(store.rows["response"]), len(population.requests)):
            _finish_lp_request(
                kg_config=config,
                model_config=model,
                population=population,
                request_index=index,
                store=store,
                usage_tracker=usage_tracker,
            )

        _verify_execution_material(kg_config=config, population=population, store=store)

        if any(failure.resolved_run_number is None for failure in store.failures):
            raise LPGenerationFailed("LP processing failures remain unresolved.")

        return tuple(
            LPGenerationResponse.model_validate(row.payload)
            for row in store.rows["response"]
        )


def validate_lp_request_artifacts(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
) -> LPRequestPopulation:
    """Fail closed unless complete on-disk populations match current material inputs.

    This validates the pre-call inputs only. It does not read execution checkpoints,
    reuse responses, repair files, or make external calls.

    Parameters
    ----------
    as_lc_bundle
        Current authoritative, validated upstream bundle.
    doc_key
        Current document identity.
    kg_config
        Current effective configuration.
    kg_dirs
        Directory containing all candidate, request, and manifest files.

    Returns
    -------
    LPRequestPopulation
        Complete population read from disk after current-material reconciliation.

    Raises
    ------
    OSError
        If a required file is unavailable.
    ValueError
        If a file is incomplete, malformed, stale, reordered, or inconsistent.
    """

    return _read_population(
        expected=build_lp_generation_requests(
            as_lc_bundle=as_lc_bundle, doc_key=doc_key, kg_config=kg_config
        ),
        root=kg_dirs.root,
    )


def write_lp_generation_request_artifacts(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
) -> LPRequestPopulation:
    """Materialize and reconcile the entire candidate/request population before calls.

    All validation and serialization complete before output files are changed. A
    previous manifest is invalidated before population replacement; the new manifest is
    written only after all population bytes match. Any failed or interrupted write
    leaves no validated return value. The caller must use the complete writer result or
    the on-disk validator as its pre-call gate.

    Parameters
    ----------
    as_lc_bundle
        Final validated AS+LC bundle.
    doc_key
        Document key matching authoritative upstream identity.
    kg_config
        Current explicit curriculum policy and operational evidence limits.
    kg_dirs
        Directory receiving candidate JSONL, candidate summary, request JSONL, and the
        request materialization manifest.

    Returns
    -------
    LPRequestPopulation
        Exact complete population read back from the materialized artifacts.

    Raises
    ------
    ValueError
        If validation, serialization, or read-back reconciliation fails.
    """

    population = build_lp_generation_requests(
        as_lc_bundle=as_lc_bundle, doc_key=doc_key, kg_config=kg_config
    )
    payloads = _artifact_payloads(
        candidates=population.candidates, requests=population.requests
    )
    manifest_bytes = (
        canonical_lp_json(population.manifest.model_dump(mode="json")) + "\n"
    ).encode("utf-8")
    kg_dirs.root.mkdir(exist_ok=True, parents=True)
    (kg_dirs.root / _MANIFEST_FILENAME).unlink(missing_ok=True)

    for filename, payload in payloads.items():
        (kg_dirs.root / filename).write_bytes(payload)

    for filename, payload in payloads.items():
        if (kg_dirs.root / filename).read_bytes() != payload:
            raise ValueError(
                f"LP pre-call artifact failed write reconciliation: {filename}."
            )

    (kg_dirs.root / _MANIFEST_FILENAME).write_bytes(manifest_bytes)
    return _read_population(expected=population, root=kg_dirs.root)
