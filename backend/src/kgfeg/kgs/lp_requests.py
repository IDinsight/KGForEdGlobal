"""Bounded LP request models, construction, and materialized artifact reconciliation.

Requests and their complete artifact population depend on upstream graph records and
configuration. Prompts, agents, validators, checkpoints, and orchestration share this
module without introducing a dependency on LLM execution.
"""

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import json

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid5

# Third Party Library
from pydantic import Field, JsonValue, model_validator

# Package Library
from kgfeg.config import Settings
from kgfeg.kgs.lp_candidates import (
    LPCandidatePopulation,
    build_lp_candidates,
    validate_lp_candidate_population,
)
from kgfeg.kgs.lp_coordinates import LPDevelopmentalCoordinate
from kgfeg.kgs.lp_index import LPAncestorPath, LPGraphIndex, build_lp_graph_index
from kgfeg.kgs.lp_selection import LPSFIEligibility, build_lp_selection
from kgfeg.kgs.schemas import (
    AcademicStandardsLCKGBundle,
    LPAdmissibleDecision,
    LPCandidatePair,
    LPCandidateSummary,
)
from kgfeg.kgs.utils import KGDirs
from kgfeg.schemas import BaseSchema, CreateKGConfig

_REQUESTS_FILENAME = "lp_generation_requests.jsonl"
MANIFEST_FILENAME = "lp_generation_requests_manifest.json"


class LPBoundedText(BaseSchema):
    """Explicit text excerpt with a hash and size of its complete source text."""

    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_characters: int = Field(ge=0, strict=True)
    text: str
    truncated: bool = Field(strict=True)

    @model_validator(mode="after")
    def _validate_extent(self) -> LPBoundedText:
        """Require truthful excerpt lengths and complete-text hashes.

        Returns
        -------
        LPBoundedText
            Consistent bounded text record.

        Raises
        ------
        ValueError
            If the LP text excerpt has inconsistent truncation counts or does not match
            its complete-text hash.
        """

        if self.original_characters < len(self.text) or self.truncated != (
            self.original_characters > len(self.text)
        ):
            raise ValueError("LP text excerpt has inconsistent truncation counts.")

        if not self.truncated and self.content_hash != lp_text_content_hash(self.text):
            raise ValueError("LP text excerpt does not match its complete-text hash.")

        return self


class LPContextSFI(BaseSchema):
    """Bounded authoritative SFI text with complete audit and fallback warnings."""

    alternate_statement_code: LPBoundedText | None
    audit_context: dict[str, JsonValue]
    description: LPBoundedText
    normalized_statement_type: str
    root_fallback_relationship_uuids: tuple[UUID, ...]
    sfi_uuid: UUID
    statement_code: LPBoundedText | None
    statement_type: str
    upstream_sfi_content_hash: str
    unresolved_ancestry: bool
    warnings: tuple[str, ...]


class LPCoordinateContext(BaseSchema):
    """Canonical coordinate semantics with bounded origin references.

    Origin excerpts share the configured per-SFI character ceiling and source-item
    ceiling independently of source-value excerpts. Full-origin lengths and hashes, the
    complete ordered-origin hash, and omission counts retain material linkage.
    """

    canonical_value: str | None
    omitted_source_field_count: int = Field(ge=0, strict=True)
    rank: int | None
    sfi_uuid: UUID
    source_fields: tuple[LPBoundedText, ...]
    source_fields_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    statement_type: str
    status: Literal["missing", "resolved"]


class LPGenerationRequest(BaseSchema):
    """One exact ordered batch with the shared bounded producer/checker evidence.

    The content hash covers every field except itself and the derived request UUID.
    Prompt and model identities belong to execution records, not this evidence record.
    """

    candidate_pairs_content_hash: str
    candidate_summary_content_hash: str
    config_content_hash: str
    doc_key: str
    eligible_sfis_content_hash: str
    framework_title: LPBoundedText
    framework_uuid: UUID
    pairs: tuple[LPRequestPair, ...] = Field(min_length=1)
    request_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: UUID
    request_index: int = Field(ge=0, strict=True)
    sfis: tuple[LPRequestSFI, ...] = Field(min_length=2)
    upstream_content_hash: str
    warnings: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_identity_and_coverage(self) -> LPGenerationRequest:
        """Require unique pair coverage, exact endpoint context, and material identity.

        Returns
        -------
        LPGenerationRequest
            Request whose identifiers match its actual bounded evidence.

        Raises
        ------
        ValueError
            If the request has duplicate pair IDs, does not contain exactly its ordered
            endpoints, does not match its material, or the request ID does not match
            its material hash.
        """

        pair_ids = [pair.pair_id for pair in self.pairs]

        if len(pair_ids) != len(set(pair_ids)):
            raise ValueError("LP request contains duplicate pair IDs.")

        endpoints = sorted(
            {
                endpoint
                for pair in self.pairs
                for endpoint in (pair.first_sfi_uuid, pair.second_sfi_uuid)
            },
            key=str,
        )

        if [sfi.context.sfi_uuid for sfi in self.sfis] != endpoints:
            raise ValueError(
                "LP request context must contain exactly its ordered endpoints."
            )

        material = self.model_dump(
            exclude={"request_content_hash", "request_id"}, mode="json"
        )

        if self.request_content_hash != lp_material_content_hash(material):
            raise ValueError("LP request content hash does not match its material.")

        if self.request_id != build_lp_request_id(self.request_content_hash):
            raise ValueError("LP request ID does not match its material hash.")

        return self


class LPLearningComponentContext(BaseSchema):
    """One supporting LC with bounded text and metadata, never an LP endpoint."""

    description: LPBoundedText
    identifier: UUID
    metadata: LPBoundedText
    upstream_content_hash: str


class LPNominationContext(BaseSchema):
    """Bounded excerpt of complete nomination values stored in the candidate file."""

    complete_evidence_content_hash: str
    evidence: LPBoundedText
    evidence_types: tuple[str, ...]
    references_truncated: bool


class LPRequestManifest(BaseSchema):
    """Material identities and counts for the complete pre-call file population.

    File hashes are SHA-256 of actual UTF-8 bytes, including JSONL newlines. The
    request population hash identifies the canonical ordered JSON array. Empty
    candidate/request populations have explicit zero counts and empty-array hashes.
    """

    artifact_byte_hashes: dict[str, str]
    candidate_pairs_content_hash: str
    candidate_summary_content_hash: str
    config_content_hash: str
    doc_key: str
    eligible_sfis_content_hash: str
    framework_uuid: UUID
    pair_ids: tuple[str, ...]
    request_ids: tuple[UUID, ...]
    requests_content_hash: str
    total_candidate_pairs: int = Field(ge=0, strict=True)
    total_requests: int = Field(ge=0, strict=True)
    upstream_content_hash: str


class LPRequestPair(BaseSchema):
    """Exact candidate identity and permissions, with bounded nomination context."""

    admissible_decisions: tuple[LPAdmissibleDecision, ...]
    candidate_content_hash: str
    first_sfi_uuid: UUID
    nomination: LPNominationContext
    pair_id: str
    second_sfi_uuid: UUID
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LPRequestPopulation:
    """Complete candidate/request sequence and its material reconciliation receipt.

    Attributes
    ----------
    candidates
        Full bounded candidate population and original candidate summary.
    manifest
        Exact file hashes, ordered identities, population hashes, and counts.
    requests
        Candidate-ranking-ordered request batches. Building this value alone does not
        prove persistence; use the writer or on-disk validator before execution.
    """

    candidates: LPCandidatePopulation
    manifest: LPRequestManifest
    requests: tuple[LPGenerationRequest, ...]


class LPSourceEvidence(BaseSchema):
    """Bounded source value and origin, each identifying its complete upstream text.

    Origin labels contain upstream metadata keys and are therefore untrusted text,
    subject to the same individual excerpt ceiling as descriptive context fields.
    Source-value excerpts additionally share the per-SFI aggregate character budget.
    """

    excerpt: LPBoundedText
    reference: LPBoundedText


class LPRequestSFI(BaseSchema):
    """Endpoint evidence with all direct parents and depth-bounded DAG paths.

    Path-count overflow fails construction rather than choosing a single branch. Source
    excerpts share one character budget; LC and path counts are independently bounded.
    Hashes identify complete omitted source material without uploading it.
    """

    ancestor_paths: tuple[LPAncestorPath, ...]
    ancestors: tuple[LPContextSFI, ...]
    context: LPContextSFI
    coordinate: LPCoordinateContext
    learning_components: tuple[LPLearningComponentContext, ...]
    learning_components_content_hash: str
    omitted_learning_component_count: int = Field(ge=0, strict=True)
    omitted_source_evidence_count: int = Field(ge=0, strict=True)
    parent_sfi_uuids: tuple[UUID, ...]
    source_evidence: tuple[LPSourceEvidence, ...]
    source_evidence_content_hash: str
    warnings: tuple[str, ...]


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


def build_lp_request_id(content_hash: str) -> UUID:
    """Mint request identity from all bounded request material.

    Parameters
    ----------
    content_hash
        Digest of the ordered request payload and its upstream material identities.

    Returns
    -------
    UUID
        Deterministic UUIDv5 in the existing canonical KG namespace.
    """

    return uuid5(
        name=f"lc:lp_generation_request:{content_hash}",
        namespace=Settings.LC_CANONICAL_NAMESPACE_UUID,
    )


def canonical_lp_json(value: Any) -> str:
    """Serialize actual material with stable keys and strict finite JSON values.

    Parameters
    ----------
    value
        JSON-compatible material to serialize.

    Returns
    -------
    str
        Canonical Unicode JSON.
    """

    return json.dumps(
        allow_nan=False,
        ensure_ascii=False,
        obj=value,
        separators=(",", ":"),
        sort_keys=True,
    )


def lp_material_content_hash(value: Any) -> str:
    """Identify canonical JSON material by its SHA-256 digest.

    Parameters
    ----------
    value
        JSON-compatible material to identify.

    Returns
    -------
    str
        SHA-256 hexadecimal digest.
    """

    return lp_text_content_hash(canonical_lp_json(value))


def lp_text_content_hash(text: str) -> str:
    """Identify exact UTF-8 text bytes without a manual implementation version.

    Parameters
    ----------
    text
        Complete text to identify.

    Returns
    -------
    str
        SHA-256 hexadecimal digest of the text bytes.
    """

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_lp_request_population(
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
    manifest_bytes = (root / MANIFEST_FILENAME).read_bytes()
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

    return read_lp_request_population(
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
    (kg_dirs.root / MANIFEST_FILENAME).unlink(missing_ok=True)

    for filename, payload in payloads.items():
        (kg_dirs.root / filename).write_bytes(payload)

    for filename, payload in payloads.items():
        if (kg_dirs.root / filename).read_bytes() != payload:
            raise ValueError(
                f"LP pre-call artifact failed write reconciliation: {filename}."
            )

    (kg_dirs.root / MANIFEST_FILENAME).write_bytes(manifest_bytes)
    return read_lp_request_population(expected=population, root=kg_dirs.root)
