"""Shared bounded LP request models and deterministic material identities.

These definitions depend only on upstream graph records and configuration. Prompts,
agents, validators, checkpoints, and orchestration share them without importing an
execution module.
"""

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import json

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid5

# Third Party Library
from pydantic import Field, JsonValue, model_validator

# Package Library
from kgfeg.config import Settings
from kgfeg.kgs.lp_candidates import LPCandidatePopulation
from kgfeg.kgs.lp_index import LPAncestorPath
from kgfeg.kgs.schemas import LPAdmissibleDecision
from kgfeg.schemas import BaseSchema


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
