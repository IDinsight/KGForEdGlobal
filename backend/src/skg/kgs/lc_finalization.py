"""This module contains LC finalization for KG creation (steps 16-18).

Step 16 mints one LearningComponent node per canonical skill text within
its dedup scope: content-addressed UUIDv5 identity, representative
surface-form description, attribution inherited from the claiming SFIs,
and full per-claim provenance in metadata. Step 17 emits one primary
supports edge per (LearningComponent, claiming SFI) pair with a
deterministic edge identity mirroring the hasChild scheme. Step 18
(validation/summary) is wired in as it is built.

Sibling LC modules mirror the sfi_* per-step layout: lc_selection.py
(steps 11-12), lc_generation.py (steps 13-14), lc_dedup.py (step 15),
lc_export.py (step 19).
"""

# Standard Library
import json

from collections import Counter
from typing import Any, Sequence
from uuid import UUID, uuid5

# Third Party Library
from loguru import logger

# Package Library
from skg.config import Settings
from skg.document_ir.schemas import DocumentIR
from skg.kgs.lc_dedup import _normalize_skill_text, _scope_key_for
from skg.kgs.lc_generation import LC_GENERATION_FAILURES_FN
from skg.kgs.schemas import (
    AcademicStandardsKGBundle,
    LCDedupGroups,
    LCEligibilityReport,
    LCGenerationFailure,
    LCGenerationRequest,
    LCGenerationResponse,
    LCGenerationSummary,
    LCRequestSFI,
    LearningComponent,
    Relationship,
    SFIFinalRecord,
)
from skg.kgs.sfi_export import _fingerprint_jsonable
from skg.kgs.sfi_finalization import _hash_text
from skg.kgs.utils import KGDirs, append_jsonl_model, make_dir, reset_output_files
from skg.schemas import CreateKGConfig, _CreateKGLearningComponentsConfig
from skg.utils.general import write_to_json

LC_ENTITY_PROVENANCE_FN = "lc_entity_provenance.json"
LC_GENERATION_SUMMARY_FN = "lc_generation_summary.json"
LC_SUPPORTS_EDGES_FN = "lc_supports_edges.json"
LEARNING_COMPONENTS_FN = "learning_components.jsonl"

_ATTRIBUTION_FIELDS = (
    "academic_subject",
    "attribution_statement",
    "author",
    "in_language",
    "license",
    "provider",
)


class _LCAccumulator:
    """Accumulates every claim of one canonical skill text within a scope."""

    def __init__(self) -> None:
        """Initialize an empty accumulator."""

        self.claims: list[dict[str, Any]] = []
        self.surface_forms: list[str] = []
        self.tags: set[str] = set()


def _assert_uniform_attribution(
    *, canonical_text: str, records: Sequence[SFIFinalRecord]
) -> SFIFinalRecord:
    """Verify all claiming SFIs share one attribution and return it.

    Attribution fields are document-level KG metadata, so claimants within
    one document must agree; a divergence means the inputs are inconsistent
    and inheritance would be ambiguous.

    Parameters
    ----------
    canonical_text
        Canonical skill text of the LC being minted (for error context).
    records
        Final records of every claiming SFI.

    Returns
    -------
    SFIFinalRecord
        The record whose attribution the LC inherits.

    Raises
    ------
    ValueError
        If claiming SFIs disagree on any attribution field.
    """

    representative = records[0]
    for record in records[1:]:
        for field in _ATTRIBUTION_FIELDS:
            if getattr(record, field) != getattr(representative, field):
                raise ValueError(
                    f"LC minting (step 16): claiming SFIs for "
                    f"{canonical_text!r} disagree on {field}: "
                    f"{getattr(representative, field)!r} vs "
                    f"{getattr(record, field)!r}."
                )
    return representative


def _build_canonical_map(lc_dedup_groups: LCDedupGroups) -> dict[tuple[str, str], str]:
    """Map every grouped member text to its canonical text, per scope.

    Parameters
    ----------
    lc_dedup_groups
        Step-15 duplicate groups.

    Returns
    -------
    dict[tuple[str, str], str]
        Canonical text keyed by (scope key, member text).
    """

    return {
        (group.scope_key, member): group.canonical_text
        for group in lc_dedup_groups.groups
        for member in group.member_texts
    }


def _build_lc_entity_provenance(
    *, document_ir: DocumentIR, learning_components: Sequence[LearningComponent]
) -> dict[str, Any]:
    """Build the LearningComponent entity-provenance artifact.

    Mirrors the step-10 entity-provenance shape so the step-19 merge can
    compose both without translation.

    Parameters
    ----------
    document_ir
        Source document IR (doc key, pdf name).
    learning_components
        Minted step-16 LearningComponent nodes.

    Returns
    -------
    dict[str, Any]
        Deterministic provenance entries keyed by LC identifier.
    """

    return {
        "doc_key": document_ir.doc_key,
        "learning_components": {
            str(component.identifier): {
                "description": component.description,
                "identity_key": component.metadata["identity"]["identity_key"],
                "in_language": component.in_language,
                "source_context_keys": component.metadata["source_context_keys"],
                "source_page_indexes": component.metadata["source_page_indexes"],
                "source_segment_ids": component.metadata["source_segment_ids"],
                "source_sfi_uuids": component.metadata["source_sfi_uuids"],
                "source_window_ids": component.metadata["source_window_ids"],
            }
            for component in learning_components
        },
        "pdf_name": document_ir.pdf_name,
    }


def _build_lc_identity_key(*, doc_key: str, scope_key: str, text: str) -> str:
    """Build the content-addressed identity key for one LearningComponent.

    Parameters
    ----------
    doc_key
        Source document key.
    scope_key
        Dedup scope segment the LC was merged within.
    text
        Canonical normalized skill text.

    Returns
    -------
    str
        The deterministic identity key.
    """

    text_hash = _hash_text(n_hex=32, value=text)
    return f"lc:curriculum:{doc_key}:{scope_key}:{text_hash}"


def _build_lc_metadata(
    *,
    academic_standards_bundle: AcademicStandardsKGBundle,
    accumulator: _LCAccumulator,
    bundle_fingerprint: str,
    document_ir: DocumentIR,
    identity_key: str,
    kg_config: CreateKGConfig,
    records: Sequence[SFIFinalRecord],
    scope_key: str,
) -> dict[str, Any]:
    """Assemble one LearningComponent's provenance metadata.

    Parameters
    ----------
    academic_standards_bundle
        Final step-10 AS bundle (source framework UUID).
    accumulator
        Accumulated claims of this canonical text.
    bundle_fingerprint
        Deterministic fingerprint of the step-10 bundle.
    document_ir
        Source document IR (doc key, pdf name).
    identity_key
        The canonical identity string that minted this LC's UUID.
    kg_config
        Country/document-specific KG configuration.
    records
        Final records of every claiming SFI, in claim order.
    scope_key
        Dedup scope segment the LC was merged within.

    Returns
    -------
    dict[str, Any]
        JSON-serializable LC provenance metadata.
    """

    confidences = [claim["confidence"] for claim in accumulator.claims]
    return {
        "claims": sorted(
            accumulator.claims,
            key=lambda claim: (claim["source_sfi_uuid"], claim["request_id"]),
        ),
        "confidence_max": max(confidences),
        "confidence_min": min(confidences),
        "country": kg_config.metadata.country,
        "doc_key": document_ir.doc_key,
        "framework_title": kg_config.metadata.framework_title,
        "generated_from_step10_bundle_fingerprint": bundle_fingerprint,
        "identity": {
            "identity_key": identity_key,
            "namespace_uuid": str(Settings.LC_CANONICAL_NAMESPACE_UUID),
            "scope_key": scope_key,
        },
        "pdf_name": document_ir.pdf_name,
        "primary_language": kg_config.metadata.primary_language,
        "source_context_keys": sorted(
            {key for record in records for key in record.source_context_keys}
        ),
        "source_framework_uuid": str(
            academic_standards_bundle.framework.case_identifier_uuid
        ),
        "source_page_indexes": sorted(
            {index for record in records for index in record.source_page_indexes}
        ),
        "source_segment_ids": sorted(
            {segment for record in records for segment in record.source_segment_ids}
        ),
        "source_sfi_confidence_max": max(record.confidence_max for record in records),
        "source_sfi_confidence_min": min(record.confidence_min for record in records),
        "source_sfi_uuids": sorted(
            {str(record.case_identifier_uuid) for record in records}
        ),
        "source_window_ids": sorted(
            {window for record in records for window in record.source_window_ids}
        ),
        "statement_types": sorted({record.statement_type for record in records}),
        "tags": sorted(accumulator.tags),
    }


def _collect_lc_claims(
    *,
    canonical_by_member: dict[tuple[str, str], str],
    lc_dedup_scope: str,
    lc_generation_requests: Sequence[LCGenerationRequest],
    lc_generation_responses: Sequence[LCGenerationResponse],
) -> dict[tuple[str, str], _LCAccumulator]:
    """Group every generated skill claim under its canonical text and scope.

    Parameters
    ----------
    canonical_by_member
        Step-15 canonical text keyed by (scope key, member text).
    lc_dedup_scope
        Configured merge scope (framework | top_ancestor | parent | none).
    lc_generation_requests
        Step-13 requests (ancestor paths for scope keys and provenance).
    lc_generation_responses
        Step-14 responses carrying the generated skills.

    Returns
    -------
    dict[tuple[str, str], _LCAccumulator]
        Accumulated claims keyed by (scope key, canonical text).

    Raises
    ------
    ValueError
        If a response claims an SFI absent from the requests.
    """

    request_sfis: dict[UUID, LCRequestSFI] = {
        request_sfi.final_sfi_uuid: request_sfi
        for request in lc_generation_requests
        for request_sfi in request.sfis
    }

    accumulators: dict[tuple[str, str], _LCAccumulator] = {}
    for response in lc_generation_responses:
        for item in response.items:
            request_sfi = request_sfis.get(item.sfi_uuid)
            if request_sfi is None:
                raise ValueError(
                    f"LC minting (step 16): response for request "
                    f"{response.request_id} claims SFI {item.sfi_uuid}, which "
                    "is absent from the step-13 requests."
                )
            scope_key = _scope_key_for(
                lc_dedup_scope=lc_dedup_scope, request_sfi=request_sfi
            )
            for skill in item.skills:
                normalized = _normalize_skill_text(skill.description)
                canonical = canonical_by_member.get((scope_key, normalized), normalized)
                accumulator = accumulators.setdefault(
                    (scope_key, canonical), _LCAccumulator()
                )
                accumulator.claims.append(
                    {
                        "ancestor_path_uuids": [
                            str(ancestor.case_identifier_uuid)
                            for ancestor in request_sfi.ancestor_path
                        ],
                        "confidence": skill.confidence,
                        "request_id": response.request_id,
                        "skill_text": normalized,
                        "source_sfi_uuid": str(item.sfi_uuid),
                        "statement_type": request_sfi.statement_type,
                        "tags": sorted(tag.lower().strip() for tag in skill.tags),
                    }
                )
                if normalized == canonical:
                    accumulator.surface_forms.append(skill.description.strip())
                accumulator.tags.update(tag.lower().strip() for tag in skill.tags)
    return accumulators


def _confidence_histogram(confidences: Sequence[float]) -> dict[str, int]:
    """Bucket claim confidences by their first decimal.

    Parameters
    ----------
    confidences
        Decomposition confidences of every claim.

    Returns
    -------
    dict[str, int]
        Counts per bucket ("0.0".."0.9", "1.0"), sorted by bucket key.
    """

    buckets: Counter[str] = Counter(
        "1.0" if confidence >= 1.0 else f"{int(confidence * 10) / 10:.1f}"
        for confidence in confidences
    )
    return dict(sorted(buckets.items()))


def _representative_description(
    *, canonical_text: str, surface_forms: Sequence[str]
) -> str:
    """Choose the display description among the canonical's surface forms.

    Most frequent original-casing spelling wins; ties break to the
    lexicographically smallest.

    Parameters
    ----------
    canonical_text
        Canonical normalized skill text (fallback when no surface exists).
    surface_forms
        Original spellings whose normalized form equals the canonical text.

    Returns
    -------
    str
        The representative description.
    """

    if not surface_forms:
        return canonical_text
    counts = Counter(surface_forms)
    return min(counts, key=lambda surface: (-counts[surface], surface))


def _validate_lc_coverage(
    *,
    lc_eligible_sfis: Sequence[SFIFinalRecord],
    lc_generation_failures: Sequence[LCGenerationFailure],
    learning_components: Sequence[LearningComponent],
) -> None:
    """Verify every eligible SFI is claimed or failed, exactly once.

    Claimed and failed are disjoint and together cover the eligible set:
    no eligible SFI silently vanishes, and none is covered by a
    fabricated LC.

    Parameters
    ----------
    lc_eligible_sfis
        Final records of the eligible LC-source SFIs.
    lc_generation_failures
        Step-14 per-request decomposition failures.
    learning_components
        Minted step-16 LearningComponent nodes.

    Raises
    ------
    ValueError
        If an eligible SFI is unaccounted for, both claimed and failed,
        or a claim/failure references a non-eligible SFI.
    """

    eligible = {str(record.final_sfi_uuid) for record in lc_eligible_sfis}
    claimed = {
        claim["source_sfi_uuid"]
        for component in learning_components
        for claim in component.metadata["claims"]
    }
    failed = {
        str(sfi_uuid)
        for failure in lc_generation_failures
        for sfi_uuid in failure.sfi_uuids
    }

    if overlap := sorted(claimed & failed):
        raise ValueError(
            f"LC summary (step 18): SFIs both claimed and failed: {overlap}."
        )
    if stray := sorted((claimed | failed) - eligible):
        raise ValueError(
            f"LC summary (step 18): claims/failures reference non-eligible "
            f"SFIs: {stray}."
        )
    if unaccounted := sorted(eligible - claimed - failed):
        raise ValueError(
            f"LC summary (step 18): eligible SFIs neither claimed by any LC "
            f"nor recorded as failed: {unaccounted}."
        )


def _validate_lc_nodes_and_edges(
    *,
    academic_standards_bundle: AcademicStandardsKGBundle,
    learning_components: Sequence[LearningComponent],
    supports_edges: Sequence[Relationship],
) -> None:
    """Verify run-level LC node and supports-edge invariants.

    Parameters
    ----------
    academic_standards_bundle
        Final step-10 AS bundle (edge targets must be real items).
    learning_components
        Minted step-16 LearningComponent nodes.
    supports_edges
        Step-17 primary supports edges.

    Raises
    ------
    ValueError
        If identifiers duplicate or fail recomputation, an edge endpoint
        is unknown, a (source, target) pair repeats, or an LC has no edge.
    """

    lc_ids = [str(component.identifier) for component in learning_components]
    if len(set(lc_ids)) != len(lc_ids):
        raise ValueError("LC summary (step 18): duplicate LC identifiers.")
    for component in learning_components:
        identity_key = component.metadata["identity"]["identity_key"]
        if uuid5(Settings.LC_CANONICAL_NAMESPACE_UUID, identity_key) != (
            component.identifier
        ):
            raise ValueError(
                f"LC summary (step 18): identifier of LC "
                f"{component.identifier} does not recompute from its "
                f"identity key {identity_key!r}."
            )

    edge_ids = [str(edge.identifier) for edge in supports_edges]
    if len(set(edge_ids)) != len(edge_ids):
        raise ValueError("LC summary (step 18): duplicate edge identifiers.")
    pairs = [
        (edge.source_entity_value, edge.target_entity_value) for edge in supports_edges
    ]
    if len(set(pairs)) != len(pairs):
        raise ValueError("LC summary (step 18): duplicate (LC, SFI) supports pairs.")

    lc_id_set = set(lc_ids)
    item_uuids = {
        str(item.case_identifier_uuid) for item in academic_standards_bundle.items
    }
    for edge in supports_edges:
        if edge.source_entity_value not in lc_id_set:
            raise ValueError(
                f"LC summary (step 18): edge {edge.identifier} sources "
                f"unknown LC {edge.source_entity_value}."
            )
        if edge.target_entity_value not in item_uuids:
            raise ValueError(
                f"LC summary (step 18): edge {edge.identifier} targets "
                f"{edge.target_entity_value}, which is not a final bundle "
                "item."
            )

    if without_edges := sorted(lc_id_set - {pair[0] for pair in pairs}):
        raise ValueError(
            f"LC summary (step 18): LCs without any primary supports edge: "
            f"{without_edges}."
        )


def build_lc_supports_edges(
    *,
    document_ir: DocumentIR,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
    lc_eligible_sfis: Sequence[SFIFinalRecord],
    learning_components: Sequence[LearningComponent],
) -> list[Relationship]:
    """Run step 17: emit one primary supports edge per (LC, claiming SFI).

    Each LearningComponent gets exactly one `supports` relationship to
    every SFI whose decomposition claimed it,
    with a deterministic UUIDv5 edge identity mirroring the hasChild
    scheme. `support_confidence` is that SFI's own claim confidence; when
    one SFI claimed the LC through several merged wordings, the minimum of
    its claim confidences is used (never overstate support).

    Parameters
    ----------
    document_ir
        Source document IR (doc key scopes edge identities).
    kg_config
        Country/document-specific KG configuration (edge attribution).
    kg_dirs
        KG artifact directories; the artifact is written under
        ``kg_dirs.root``.
    lc_eligible_sfis
        Final records of the eligible LC-source SFIs (edge targets).
    learning_components
        Minted step-16 LearningComponent nodes.

    Returns
    -------
    list[Relationship]
        Primary supports edges, ordered by identifier.

    Raises
    ------
    ValueError
        If an LC claims an SFI absent from the eligible records or two
        edges collide on one identifier.
    """

    metadata = kg_config.metadata
    records_by_uuid = {record.final_sfi_uuid: record for record in lc_eligible_sfis}

    edges: dict[UUID, Relationship] = {}
    for component in learning_components:
        claims_by_sfi: dict[str, list[dict[str, Any]]] = {}
        for claim in component.metadata["claims"]:
            claims_by_sfi.setdefault(claim["source_sfi_uuid"], []).append(claim)
        for sfi_uuid_str in sorted(claims_by_sfi):
            record = records_by_uuid.get(UUID(sfi_uuid_str))
            if record is None:
                raise ValueError(
                    f"LC supports (step 17): LC {component.identifier} "
                    f"claims SFI {sfi_uuid_str}, which is absent from the "
                    "eligible records."
                )
            sfi_claims = claims_by_sfi[sfi_uuid_str]
            relationship_key = (
                f"lc:curriculum:{document_ir.doc_key}:relationship:supports:"
                f"{component.identifier}:{record.case_identifier_uuid}"
            )
            identifier = uuid5(Settings.LC_CANONICAL_NAMESPACE_UUID, relationship_key)
            if identifier in edges:
                raise ValueError(
                    f"LC supports (step 17): duplicate edge identifier for "
                    f"{relationship_key!r}."
                )
            edges[identifier] = Relationship(
                attribution_statement=metadata.attribution_statement,
                author=metadata.author,
                description="",
                identifier=identifier,
                license=metadata.license,
                metadata={
                    "doc_key": document_ir.doc_key,
                    "relationship_identity_key": relationship_key,
                    "request_ids": sorted(
                        {claim["request_id"] for claim in sfi_claims}
                    ),
                    "source_framework_uuid": component.metadata[
                        "source_framework_uuid"
                    ],
                    "support_confidence": min(
                        claim["confidence"] for claim in sfi_claims
                    ),
                    "support_role": "primary",
                    "target_sfi_statement_type": record.statement_type,
                },
                provider=metadata.provider,
                relationship_type="supports",
                source_entity="LearningComponent",
                source_entity_key="identifier",
                source_entity_value=str(component.identifier),
                target_entity="StandardsFrameworkItem",
                target_entity_key="case_identifier_uuid",
                target_entity_value=str(record.case_identifier_uuid),
            )

    supports_edges = [edges[identifier] for identifier in sorted(edges, key=str)]

    make_dir(kg_dirs.root)
    write_to_json(
        fp=kg_dirs.root / LC_SUPPORTS_EDGES_FN,
        json_info=[edge.model_dump(mode="json") for edge in supports_edges],
    )

    logger.success(
        f"Emitted LC supports edges: edges={len(supports_edges)}; "
        f"lcs={len(learning_components)}; "
        f"multi_parent_lcs="
        f"{sum(1 for c in learning_components if len(c.metadata['source_sfi_uuids']) > 1)}"
    )
    return supports_edges


def mint_learning_components(
    *,
    academic_standards_bundle: AcademicStandardsKGBundle,
    document_ir: DocumentIR,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
    lc_dedup_groups: LCDedupGroups,
    lc_eligible_sfis: Sequence[SFIFinalRecord],
    lc_generation_requests: Sequence[LCGenerationRequest],
    lc_generation_responses: Sequence[LCGenerationResponse],
) -> list[LearningComponent]:
    """Run step 16: mint one LearningComponent per canonical skill text.

    Every claim maps to its step-15 canonical text
    (its own normalized text when ungrouped), each canonical mints a
    content-addressed UUIDv5 within its dedup scope, and duplicate claims
    collapse into one node carrying per-claim provenance. Attribution is
    inherited from the claiming SFIs, which must agree.

    Parameters
    ----------
    academic_standards_bundle
        Final step-10 AS bundle (source framework UUID, fingerprint input).
    document_ir
        Source document IR (doc key, pdf name).
    kg_config
        Country/document-specific KG configuration.
    kg_dirs
        KG artifact directories; the artifact is written under
        ``kg_dirs.root``.
    lc_dedup_groups
        Step-15 duplicate groups with canonical texts.
    lc_eligible_sfis
        Final records of the eligible LC-source SFIs.
    lc_generation_requests
        Deterministic step-13 requests.
    lc_generation_responses
        Validated step-14 responses.

    Returns
    -------
    list[LearningComponent]
        Minted LearningComponent nodes, ordered by identifier.

    Raises
    ------
    ValueError
        If a claim references an unknown SFI, claiming SFIs disagree on
        attribution, or two canonicals collide on one identifier.
    """

    lc_config = kg_config.learning_components
    records_by_uuid = {record.final_sfi_uuid: record for record in lc_eligible_sfis}
    accumulators = _collect_lc_claims(
        canonical_by_member=_build_canonical_map(lc_dedup_groups),
        lc_dedup_scope=lc_config.lc_dedup_scope,
        lc_generation_requests=lc_generation_requests,
        lc_generation_responses=lc_generation_responses,
    )
    bundle_fingerprint = _fingerprint_jsonable(
        academic_standards_bundle.model_dump(mode="json")
    )

    learning_components: dict[UUID, LearningComponent] = {}
    for (scope_key, canonical_text), accumulator in sorted(accumulators.items()):
        claim_sfi_uuids = sorted(
            {UUID(claim["source_sfi_uuid"]) for claim in accumulator.claims}, key=str
        )
        missing = [str(u) for u in claim_sfi_uuids if u not in records_by_uuid]
        if missing:
            raise ValueError(
                f"LC minting (step 16): claiming SFIs {missing} for "
                f"{canonical_text!r} are absent from the eligible records."
            )
        records = [records_by_uuid[sfi_uuid] for sfi_uuid in claim_sfi_uuids]
        attribution = _assert_uniform_attribution(
            canonical_text=canonical_text, records=records
        )
        identity_key = _build_lc_identity_key(
            doc_key=document_ir.doc_key, scope_key=scope_key, text=canonical_text
        )
        identifier = uuid5(Settings.LC_CANONICAL_NAMESPACE_UUID, identity_key)
        if identifier in learning_components:
            raise ValueError(
                f"LC minting (step 16): identifier collision between "
                f"{canonical_text!r} and "
                f"{learning_components[identifier].description!r}."
            )
        learning_components[identifier] = LearningComponent(
            academic_subject=attribution.academic_subject,
            attribution_statement=attribution.attribution_statement,
            author=attribution.author,
            description=_representative_description(
                canonical_text=canonical_text,
                surface_forms=accumulator.surface_forms,
            ),
            identifier=identifier,
            in_language=attribution.in_language,
            license=attribution.license,
            metadata=_build_lc_metadata(
                academic_standards_bundle=academic_standards_bundle,
                accumulator=accumulator,
                bundle_fingerprint=bundle_fingerprint,
                document_ir=document_ir,
                identity_key=identity_key,
                kg_config=kg_config,
                records=records,
                scope_key=scope_key,
            ),
            provider=attribution.provider,
        )

    minted = [
        learning_components[identifier]
        for identifier in sorted(learning_components, key=str)
    ]

    make_dir(kg_dirs.root)
    components_fp = kg_dirs.root / LEARNING_COMPONENTS_FN
    reset_output_files(output_fps=[components_fp])
    for component in minted:
        append_jsonl_model(fp=components_fp, model=component)

    total_claims = sum(len(accumulator.claims) for accumulator in accumulators.values())
    logger.success(
        f"Minted LearningComponents: claims={total_claims}; "
        f"nodes={len(minted)}; "
        f"multi_claim_nodes="
        f"{sum(1 for a in accumulators.values() if len(a.claims) > 1)}"
    )
    return minted


def summarize_learning_components(
    *,
    academic_standards_bundle: AcademicStandardsKGBundle,
    document_ir: DocumentIR,
    kg_dirs: KGDirs,
    lc_config: _CreateKGLearningComponentsConfig,
    lc_dedup_groups: LCDedupGroups,
    lc_eligibility_report: LCEligibilityReport,
    lc_eligible_sfis: Sequence[SFIFinalRecord],
    lc_generation_requests: Sequence[LCGenerationRequest],
    lc_generation_responses: Sequence[LCGenerationResponse],
    learning_components: Sequence[LearningComponent],
    supports_edges: Sequence[Relationship],
) -> LCGenerationSummary:
    """Run step 18: validate LC artifacts and persist the phase summary.

    Verifies run-level invariants (identifier recomputation, edge
    endpoint reality, one primary edge per pair, eligible-SFI coverage),
    then writes the `LCGenerationSummary` and the LC entity-provenance
    artifact consumed by the step-19 merge.

    Parameters
    ----------
    academic_standards_bundle
        Final step-10 AS bundle (edge targets must be real items).
    document_ir
        Source document IR (doc key, pdf name).
    kg_dirs
        KG artifact directories; step-14 failures are read from and the
        summary artifacts written under ``kg_dirs.root``.
    lc_config
        Learning Components runtime configuration (override record).
    lc_dedup_groups
        Step-15 duplicate groups (dedup counters).
    lc_eligibility_report
        Step-12 eligibility report (selection counters, warnings).
    lc_eligible_sfis
        Final records of the eligible LC-source SFIs.
    lc_generation_requests
        Deterministic step-13 requests (LLM accounting).
    lc_generation_responses
        Validated step-14 responses (splits and confidence counters).
    learning_components
        Minted step-16 LearningComponent nodes.
    supports_edges
        Step-17 primary supports edges.

    Returns
    -------
    LCGenerationSummary
        The persisted phase summary.

    Raises
    ------
    ValueError
        If any run-level invariant fails.
    """

    failures_fp = kg_dirs.root / LC_GENERATION_FAILURES_FN
    lc_generation_failures = [
        LCGenerationFailure.model_validate(entry)
        for entry in json.loads(failures_fp.read_text())
    ]

    _validate_lc_nodes_and_edges(
        academic_standards_bundle=academic_standards_bundle,
        learning_components=learning_components,
        supports_edges=supports_edges,
    )
    _validate_lc_coverage(
        lc_eligible_sfis=lc_eligible_sfis,
        lc_generation_failures=lc_generation_failures,
        learning_components=learning_components,
    )

    splits = [
        len(item.skills)
        for response in lc_generation_responses
        for item in response.items
    ]
    statement_type_counts: Counter[str] = Counter(
        statement_type
        for component in learning_components
        for statement_type in component.metadata["statement_types"]
    )
    failed_sfis = {
        str(sfi_uuid)
        for failure in lc_generation_failures
        for sfi_uuid in failure.sfi_uuids
    }
    warnings = list(lc_eligibility_report.warnings)
    if failed_sfis:
        warnings.append(
            f"{len(failed_sfis)} eligible SFIs failed LLM decomposition and "
            "have no LearningComponents."
        )

    summary = LCGenerationSummary(
        lc_confidence_distribution=_confidence_histogram(
            [
                skill.confidence
                for response in lc_generation_responses
                for item in response.items
                for skill in item.skills
            ]
        ),
        lc_count_by_language=dict(
            sorted(
                Counter(
                    component.in_language for component in learning_components
                ).items()
            )
        ),
        lc_count_by_source_statement_type=dict(sorted(statement_type_counts.items())),
        lc_dedup_candidate_pair_count=lc_dedup_groups.candidate_pair_count,
        lc_dedup_conflict_count=lc_dedup_groups.conflict_count,
        lc_dedup_judged_same_count=lc_dedup_groups.judged_same_count,
        lc_generation_failed_sfis_count=len(failed_sfis),
        lc_max_splits_observed=max(splits, default=0),
        lc_multi_claim_lc_count=sum(
            1
            for component in learning_components
            if len(component.metadata["claims"]) > 1
        ),
        lc_multi_parent_lc_count=sum(
            1
            for component in learning_components
            if len(component.metadata["source_sfi_uuids"]) > 1
        ),
        lc_selection_mode=lc_eligibility_report.lc_selection_mode,
        lc_source_exclusion_reason_counts=(
            lc_eligibility_report.lc_source_exclusion_reason_counts
        ),
        lc_splits_distribution=dict(
            sorted(
                Counter(str(count) for count in splits).items(),
                key=lambda kv: int(kv[0]),
            )
        ),
        llm_request_count=len(lc_generation_requests),
        llm_response_count=len(lc_generation_responses),
        manual_review_overrides=lc_config.lc_manual_review_overrides,
        total_lc_claims=sum(
            len(component.metadata["claims"]) for component in learning_components
        ),
        total_lc_source_sfis_considered=(
            lc_eligibility_report.total_lc_source_sfis_considered
        ),
        total_lc_source_sfis_eligible=(
            lc_eligibility_report.total_lc_source_sfis_eligible
        ),
        total_lc_source_sfis_empty_text=(
            lc_eligibility_report.total_lc_source_sfis_empty_text
        ),
        total_lc_source_sfis_excluded=(
            lc_eligibility_report.total_lc_source_sfis_excluded
        ),
        total_lcs=len(learning_components),
        total_supports_edges=len(supports_edges),
        warnings=warnings,
    )

    make_dir(kg_dirs.root)
    write_to_json(
        fp=kg_dirs.root / LC_GENERATION_SUMMARY_FN,
        json_info=summary.model_dump(mode="json"),
    )
    write_to_json(
        fp=kg_dirs.root / LC_ENTITY_PROVENANCE_FN,
        json_info=_build_lc_entity_provenance(
            document_ir=document_ir, learning_components=learning_components
        ),
    )

    logger.success(
        f"Summarized LC generation: lcs={summary.total_lcs}; "
        f"edges={summary.total_supports_edges}; "
        f"failed_sfis={summary.lc_generation_failed_sfis_count}; "
        f"warnings={len(summary.warnings)}"
    )
    return summary
