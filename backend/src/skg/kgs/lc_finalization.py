"""This module contains LC finalization for KG creation (steps 16-18).

Step 16 mints one LearningComponent node per canonical skill text within
its dedup scope: content-addressed UUIDv5 identity, representative
surface-form description, attribution inherited from the claiming SFIs,
and full per-claim provenance in metadata. Steps 17-18 (supports edges,
validation/summary) are wired in as they are built.

Sibling LC modules mirror the sfi_* per-step layout: lc_selection.py
(steps 11-12), lc_generation.py (steps 13-14), lc_dedup.py (step 15),
lc_export.py (step 19).
"""

# Standard Library
from collections import Counter
from typing import Any, Sequence
from uuid import UUID, uuid5

# Third Party Library
from loguru import logger

# Package Library
from skg.config import Settings
from skg.document_ir.schemas import DocumentIR
from skg.kgs.lc_dedup import _normalize_skill_text, _scope_key_for
from skg.kgs.schemas import (
    AcademicStandardsKGBundle,
    LCDedupGroups,
    LCGenerationRequest,
    LCGenerationResponse,
    LCRequestSFI,
    LearningComponent,
    SFIFinalRecord,
)
from skg.kgs.sfi_export import _fingerprint_jsonable
from skg.kgs.sfi_finalization import _hash_text
from skg.kgs.utils import KGDirs, append_jsonl_model, make_dir, reset_output_files
from skg.schemas import CreateKGConfig

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

    Deterministic, no LLM: every claim maps to its step-15 canonical text
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
