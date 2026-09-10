"""Persist standalone LP relationships and their structural/process audit evidence.

The summary binds every companion file to exact bytes. Readers reconstruct current
material and reject partial, edited, or stale sets. Processing failures retain their
checkpoint-owned schema and recovery history; ambiguity retains complete direct claims.
"""

# Future Library
from __future__ import annotations

# Standard Library
import fcntl
import hashlib
import json
import os
import tempfile

from collections import Counter
from pathlib import Path
from typing import Any

# Third Party Library
from pydantic import Field, model_validator

# Package Library
from kgfeg.kgs.lp_checkpoints import content_hash
from kgfeg.kgs.lp_finalization import (
    LPFinalClaim,
    LPFinalClaims,
    LPFinalizationCycleError,
    LPRelationshipProvenance,
    LPRelationships,
    validate_lp_final_claims_artifact,
)
from kgfeg.kgs.lp_requests import canonical_lp_json
from kgfeg.kgs.lp_selection import build_lp_selection
from kgfeg.kgs.lp_validation import LPValidationReport, validate_lp_graph
from kgfeg.kgs.schemas import AcademicStandardsLCKGBundle, Relationship
from kgfeg.kgs.utils import KGDirs
from kgfeg.schemas import BaseSchema, CreateKGConfig

_BUILDS = "lp_relationships_builds_towards.jsonl"
_FAILURES = "lp_generation_failures.json"
_INPUTS = (
    "lp_candidate_pairs.jsonl",
    "lp_candidate_summary.json",
    "lp_final_claims.json",
    "lp_generation_checkpoint_manifest.json",
    "lp_generation_draft_responses.jsonl",
    _FAILURES,
    "lp_generation_requests.jsonl",
    "lp_generation_requests_manifest.json",
    "lp_generation_responses.jsonl",
    "lp_generation_validation_verdicts.jsonl",
)
_OPTIONAL_INPUTS = ("lp_eligibility_report.json", "lp_eligible_sfis.json")
_PROVENANCE = "lp_relationship_provenance.json"
_RELATES = "lp_relationships_relates_to.jsonl"
_REPORT = "lp_validation_report.json"
_SUMMARY = "lp_generation_summary.json"
_TRANSACTION = "lp_generation_checkpoint_transaction.json"
_UNRESOLVED = "lp_unresolved_items.json"


class LPGenerationSummary(BaseSchema):
    """Actual populations, distributions, and exact companion-file material links.

    Eligibility counts include ordinary exclusions separately from judgment outcomes.
    Failure attempts include recovered history; unresolved failed pairs alone describe
    outstanding processing failure. A passed verdict concerns structural/process
    integrity only, as stated in the linked validation report.
    """

    artifact_byte_hashes: dict[str, str]
    content_hash: str
    decision_counts: dict[str, int]
    distributions: dict[str, dict[str, int]]
    eligibility: dict[str, Any]
    input_artifact_byte_hashes: dict[str, str]
    input_content_hashes: dict[str, str]
    object_counts: dict[str, int]
    relationships_final_claims_content_hash: str
    validation_report_content_hash: str
    validation_report_passed: bool = Field(strict=True)
    warning_counts: dict[str, int]

    @model_validator(mode="after")
    def _validate_material(self) -> LPGenerationSummary:
        """Bind the summary to its complete serialized content.

        Returns
        -------
        LPGenerationSummary
            Internally hash-consistent summary, pending current-material validation.

        Raises
        ------
        ValueError
            If the summary digest differs from its actual material.
        """

        if self.content_hash != content_hash(
            self.model_dump(exclude={"content_hash"}, mode="json")
        ):
            raise ValueError("LP generation summary content hash differs.")

        return self


class LPStandaloneArtifacts(BaseSchema):
    """Round-tripped standalone records, including an explicit validation verdict."""

    failures: tuple[dict[str, Any], ...]
    relationships: LPRelationships
    summary: LPGenerationSummary
    unresolved_items: LPUnresolvedItems
    validation_report: LPValidationReport


class LPUnresolvedItems(BaseSchema):
    """Complete ambiguous direct claims, without exclusions or processing failures."""

    claims: tuple[LPFinalClaim, ...]
    content_hash: str
    final_claims_content_hash: str
    total_needs_review: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def _validate_material(self) -> LPUnresolvedItems:
        """Require exact ambiguous-claim counts, uniqueness, and material linkage.

        Returns
        -------
        LPUnresolvedItems
            Intrinsically consistent ambiguity artifact.

        Raises
        ------
        ValueError
            If another outcome is included, claims repeat, or counts/hashes differ.
        """

        if (
            any(claim.judgment.decision != "needs_review" for claim in self.claims)
            or len({claim.judgment.pair_id for claim in self.claims})
            != len(self.claims)
            or self.total_needs_review != len(self.claims)
            or self.content_hash
            != content_hash(self.model_dump(exclude={"content_hash"}, mode="json"))
        ):
            raise ValueError(
                "LP unresolved items have inconsistent claims or material."
            )

        return self


def _atomic_write(*, path: Path, payload: bytes) -> None:
    """Durably replace one complete file in the locked artifact directory.

    Parameters
    ----------
    path
        Destination artifact.
    payload
        Complete validated canonical bytes.
    """

    temporary = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False, dir=path.parent, mode="wb", prefix=".lp_artifacts-"
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(dst=path, src=temporary)
        descriptor = os.open(flags=os.O_RDONLY, path=path.parent)

        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _canonical_bytes(value: Any) -> bytes:
    """Encode complete canonical JSON with a terminal newline.

    Parameters
    ----------
    value
        JSON-compatible artifact material.

    Returns
    -------
    bytes
        Stable UTF-8 serialization.
    """

    return (canonical_lp_json(value) + "\n").encode("utf-8")


def _companion_payloads(
    *,
    failures: bytes,
    relationships: LPRelationships,
    unresolved_items: LPUnresolvedItems,
    validation_report: LPValidationReport,
) -> dict[str, bytes]:
    """Serialize every summary-bound file without reshaping approved evidence.

    Parameters
    ----------
    failures
        Exact checkpoint-authenticated failure bytes, including recovered attempts.
    relationships
        Canonically ordered complete internal relationship rows and provenance.
    unresolved_items
        Complete ambiguous claims and their provenance.
    validation_report
        Unmodified standalone structural/process validator result.

    Returns
    -------
    dict[str, bytes]
        Six companion files; an empty relation population is a zero-byte JSONL file.
    """

    return {
        _BUILDS: b"".join(
            _canonical_bytes(edge.model_dump(mode="json"))
            for edge in relationships.relationships_builds_towards
        ),
        _FAILURES: failures,
        _PROVENANCE: _canonical_bytes(
            {
                key: value.model_dump(mode="json")
                for key, value in relationships.relationship_provenance.items()
            }
        ),
        _RELATES: b"".join(
            _canonical_bytes(edge.model_dump(mode="json"))
            for edge in relationships.relationships_relates_to
        ),
        _REPORT: _canonical_bytes(validation_report.model_dump(mode="json")),
        _UNRESOLVED: _canonical_bytes(unresolved_items.model_dump(mode="json")),
    }


def _input_snapshot(root: Path) -> dict[str, bytes | None]:
    """Capture authoritative file bytes without initializing or recovering execution.

    Parameters
    ----------
    root
        Existing generation directory.

    Returns
    -------
    dict[str, bytes | None]
        Required inputs and optional eligibility files, including observed absence.

    Raises
    ------
    ValueError
        If generation has an unfinished checkpoint transaction.
    """

    if (root / _TRANSACTION).exists():
        raise ValueError("LP generation transaction is unfinished; resume generation.")

    return {
        **{name: (root / name).read_bytes() for name in _INPUTS},
        **{
            name: (root / name).read_bytes() if (root / name).exists() else None
            for name in _OPTIONAL_INPUTS
        },
    }


def _ordered_relationships(relationships: LPRelationships) -> LPRelationships:
    """Snapshot complete rows in deterministic identifier and material order.

    Parameters
    ----------
    relationships
        Caller-supplied rows to validate, including any diagnostic invalid graph.

    Returns
    -------
    LPRelationships
        Detached models; no row, metadata, or provenance entry is dropped.
    """

    copied = LPRelationships.model_validate_json(relationships.model_dump_json())

    for name in ("relationships_builds_towards", "relationships_relates_to"):
        setattr(
            copied,
            name,
            tuple(
                sorted(
                    getattr(copied, name),
                    key=lambda edge: (
                        str(edge.identifier),
                        canonical_lp_json(edge.model_dump(mode="json")),
                    ),
                )
            ),
        )

    return copied


def _prepare_artifacts(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
    relationships: LPRelationships,
) -> tuple[LPStandaloneArtifacts, dict[str, bytes], dict[str, bytes | None]]:
    """Reconstruct current authority, validate the graph, and reconcile all payloads.

    Parameters
    ----------
    as_lc_bundle
        Current authoritative upstream bundle.
    doc_key
        Current document identity.
    kg_config
        Current effective curriculum configuration.
    kg_dirs
        Persisted generation and final-claim evidence directory.
    relationships
        Complete proposed relationship rows and provenance.

    Returns
    -------
    tuple[LPStandaloneArtifacts, dict[str, bytes], dict[str, bytes | None]]
        Validated objects, seven canonical payloads, and the authenticated input snapshot.

    Raises
    ------
    ValueError
        If material is stale, inconsistent, incomplete, or changes during validation.
    """

    inputs = _input_snapshot(kg_dirs.root)
    bundle = AcademicStandardsLCKGBundle.model_validate_json(
        as_lc_bundle.model_dump_json()
    )
    config = CreateKGConfig.model_validate_json(
        kg_config.model_dump_json(by_alias=True)
    )
    ordered = _ordered_relationships(relationships)
    report = validate_lp_graph(
        as_lc_bundle=bundle,
        doc_key=doc_key,
        kg_config=config,
        kg_dirs=kg_dirs,
        relationships=ordered,
    )

    try:
        claims = validate_lp_final_claims_artifact(
            as_lc_bundle=bundle, doc_key=doc_key, kg_config=config, kg_dirs=kg_dirs
        )
    except LPFinalizationCycleError as exc:
        claims = exc.artifact

    selection = build_lp_selection(as_lc_bundle=bundle, kg_config=config)

    for name, expected in (
        ("lp_eligibility_report.json", selection.model_dump(mode="json")),
        (
            "lp_eligible_sfis.json",
            [record.model_dump(mode="json") for record in selection.eligible_sfis],
        ),
    ):
        observed = inputs[name]

        if observed is not None and json.loads(observed) != expected:
            raise ValueError(
                f"LP eligibility artifact differs from current material: {name}."
            )

    if (
        selection.eligible_sfis_content_hash
        != claims.request_manifest.eligible_sfis_content_hash
    ):
        raise ValueError("LP eligible population differs from the request manifest.")

    unresolved_material = {
        "claims": [
            claim.model_dump(mode="json")
            for claim in claims.claims
            if claim.judgment.decision == "needs_review"
        ],
        "final_claims_content_hash": claims.content_hash,
        "total_needs_review": sum(
            claim.judgment.decision == "needs_review" for claim in claims.claims
        ),
    }
    unresolved = LPUnresolvedItems.model_validate(
        {**unresolved_material, "content_hash": content_hash(unresolved_material)}
    )
    failure_bytes = inputs[_FAILURES]

    if failure_bytes is None:
        raise ValueError("LP failure evidence is missing.")

    failures = json.loads(failure_bytes)
    payloads = _companion_payloads(
        failures=failure_bytes,
        relationships=ordered,
        unresolved_items=unresolved,
        validation_report=report,
    )
    counts = _reconciled_counts(
        claims=claims, failures=failures, relationships=ordered, report=report
    )
    eligibility = selection.model_dump(exclude={"sfis"}, mode="json")
    material = {
        "artifact_byte_hashes": {
            name: hashlib.sha256(payload).hexdigest()
            for name, payload in payloads.items()
        },
        "decision_counts": dict(claims.decision_counts),
        "distributions": {
            "checker_outcome": dict(
                Counter(claim.provenance.checker_outcome for claim in claims.claims)
            ),
            "eligible_coordinate": dict(
                Counter(
                    record.coordinate.canonical_value
                    for record in selection.eligible_sfis
                    if record.coordinate.canonical_value is not None
                )
            ),
            "eligible_statement_type": dict(
                Counter(record.statement_type for record in selection.eligible_sfis)
            ),
        },
        "eligibility": eligibility,
        "input_artifact_byte_hashes": {
            name: hashlib.sha256(payload).hexdigest()
            for name, payload in inputs.items()
            if payload is not None
        },
        "input_content_hashes": {
            **report.input_content_hashes,
            "eligibility_report": content_hash(selection.model_dump(mode="json")),
            "request_manifest": content_hash(
                claims.request_manifest.model_dump(mode="json")
            ),
        },
        "object_counts": counts,
        "relationships_final_claims_content_hash": ordered.final_claims_content_hash,
        "validation_report_content_hash": report.content_hash,
        "validation_report_passed": report.passed,
        "warning_counts": dict(
            Counter(
                warning
                for claim in claims.claims
                for warning in claim.judgment.warnings
            )
        ),
    }
    summary = LPGenerationSummary.model_validate(
        {**material, "content_hash": content_hash(material)}
    )
    artifacts = LPStandaloneArtifacts(
        failures=tuple(failures),
        relationships=ordered,
        summary=summary,
        unresolved_items=unresolved,
        validation_report=report,
    )
    payloads[_SUMMARY] = _canonical_bytes(summary.model_dump(mode="json"))
    _round_trip(expected=artifacts, payloads=payloads)
    _verify_inputs(expected=inputs, root=kg_dirs.root)
    return artifacts, payloads, inputs


def _reconciled_counts(
    *,
    claims: LPFinalClaims,
    failures: list[dict[str, Any]],
    relationships: LPRelationships,
    report: LPValidationReport,
) -> dict[str, int]:
    """Count actual material independently and compare the validator's populations.

    Parameters
    ----------
    claims
        Complete reconstructed direct claim population.
    failures
        Authenticated checkpoint failure history.
    relationships
        Actual rows and provenance to serialize.
    report
        Existing standalone validator output.

    Returns
    -------
    dict[str, int]
        Reconciled object counts; failed graph diagnostics retain unequal populations.

    Raises
    ------
    ValueError
        If writer and validator disagree about actual counts or input material.
    """

    decisions = Counter(claim.judgment.decision for claim in claims.claims)
    counts = {
        "builds_towards_claims": decisions["buildsTowards"],
        "builds_towards_relationships": len(relationships.relationships_builds_towards),
        "candidate_pairs": len(claims.request_manifest.pair_ids),
        "final_claims": len(claims.claims),
        "generation_failure_attempts": len(failures),
        "needs_review_claims": decisions["needs_review"],
        "no_relation_claims": decisions["no_relation"],
        "relationship_provenance": len(relationships.relationship_provenance),
        "relates_to_claims": decisions["relatesTo"],
        "relates_to_relationships": len(relationships.relationships_relates_to),
        "relationships": len(relationships.relationships_builds_towards)
        + len(relationships.relationships_relates_to),
        "requests": len(claims.request_manifest.request_ids),
        "unresolved_failed_pairs": len(
            {
                pair_id
                for failure in failures
                if failure["resolved_run_number"] is None
                for pair_id in failure["pair_ids"]
            }
        ),
        "unresolved_warning_pairs": sum(
            bool(claim.candidate.warnings) for claim in claims.claims
        ),
    }

    if (
        any(report.object_counts.get(name) != value for name, value in counts.items())
        or report.input_content_hashes["final_claims"] != claims.content_hash
        or report.input_content_hashes["relationships"]
        != content_hash(relationships.model_dump(mode="json"))
    ):
        raise ValueError(
            "LP artifact counts or material disagree with standalone validation."
        )

    return {
        **report.object_counts,
        **counts,
        "accepted_claims": decisions["buildsTowards"] + decisions["relatesTo"],
        "nonpublishing_claims": decisions["no_relation"] + decisions["needs_review"],
        "resolved_failure_attempts": sum(
            failure["resolved_run_number"] is not None for failure in failures
        ),
    }


def _round_trip(
    *, expected: LPStandaloneArtifacts, payloads: dict[str, bytes]
) -> LPStandaloneArtifacts:
    """Parse every artifact and require exact object and byte reconciliation.

    Parameters
    ----------
    expected
        Independently prepared current material.
    payloads
        Complete seven-file population to validate.

    Returns
    -------
    LPStandaloneArtifacts
        Models parsed from the actual serialized material.

    Raises
    ------
    ValueError
        If schemas, content, counts, hashes, or canonical bytes differ.
    """

    actual = LPStandaloneArtifacts(
        failures=tuple(json.loads(payloads[_FAILURES])),
        relationships=LPRelationships(
            final_claims_content_hash=expected.relationships.final_claims_content_hash,
            relationship_provenance={
                key: LPRelationshipProvenance.model_validate(value)
                for key, value in json.loads(payloads[_PROVENANCE]).items()
            },
            relationships_builds_towards=tuple(
                Relationship.model_validate_json(line)
                for line in payloads[_BUILDS].splitlines()
            ),
            relationships_relates_to=tuple(
                Relationship.model_validate_json(line)
                for line in payloads[_RELATES].splitlines()
            ),
        ),
        summary=LPGenerationSummary.model_validate_json(payloads[_SUMMARY]),
        unresolved_items=LPUnresolvedItems.model_validate_json(payloads[_UNRESOLVED]),
        validation_report=LPValidationReport.model_validate_json(payloads[_REPORT]),
    )
    canonical = _companion_payloads(
        failures=_canonical_bytes(list(actual.failures)),
        relationships=actual.relationships,
        unresolved_items=actual.unresolved_items,
        validation_report=actual.validation_report,
    )
    hashes = {
        name: hashlib.sha256(payload).hexdigest() for name, payload in canonical.items()
    }
    canonical[_SUMMARY] = _canonical_bytes(actual.summary.model_dump(mode="json"))

    if (
        actual != expected
        or canonical != payloads
        or hashes != actual.summary.artifact_byte_hashes
    ):
        raise ValueError("LP standalone artifact round-trip material differs.")

    return actual


def _verify_inputs(*, expected: dict[str, bytes | None], root: Path) -> None:
    """Reject any input change between reconstruction and locked artifact access.

    Parameters
    ----------
    expected
        Snapshot authenticated by the upstream validators.
    root
        Existing generation directory.

    Raises
    ------
    ValueError
        If generation or eligibility material changed during artifact processing.
    """

    if _input_snapshot(root) != expected:
        raise ValueError("LP inputs changed during standalone artifact processing.")


def read_lp_artifacts(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
) -> LPStandaloneArtifacts:
    """Validate persisted standalone artifacts against current authoritative material.

    A parsed passed flag or self-consistent hash is never sufficient authority. Invalid
    graph diagnostics may round-trip with a failed verdict. Missing, partial, stale, or
    edited material raises instead of producing a successful result.

    Parameters
    ----------
    as_lc_bundle
        Current authoritative upstream bundle.
    doc_key
        Current document identity.
    kg_config
        Current effective configuration.
    kg_dirs
        Existing standalone and generation artifact directory.

    Returns
    -------
    LPStandaloneArtifacts
        Exact disk records with a freshly reconstructed structural/process verdict.

    Raises
    ------
    ValueError
        If material is missing, stale, inconsistent, or changes during validation.
    """

    root = kg_dirs.root
    summary = LPGenerationSummary.model_validate_json((root / _SUMMARY).read_bytes())
    relationships = LPRelationships(
        final_claims_content_hash=summary.relationships_final_claims_content_hash,
        relationship_provenance=json.loads((root / _PROVENANCE).read_bytes()),
        relationships_builds_towards=tuple(
            Relationship.model_validate_json(line)
            for line in (root / _BUILDS).read_bytes().splitlines()
        ),
        relationships_relates_to=tuple(
            Relationship.model_validate_json(line)
            for line in (root / _RELATES).read_bytes().splitlines()
        ),
    )
    expected, payloads, inputs = _prepare_artifacts(
        as_lc_bundle=as_lc_bundle,
        doc_key=doc_key,
        kg_config=kg_config,
        kg_dirs=kg_dirs,
        relationships=relationships,
    )

    with (root / ".lp_generation.lock").open("rb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _verify_inputs(expected=inputs, root=root)
        actual = {name: (root / name).read_bytes() for name in payloads}

        if actual != payloads:
            raise ValueError(
                "LP standalone artifacts differ from current validated material."
            )

        return _round_trip(expected=expected, payloads=actual)


def write_lp_artifacts(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
    relationships: LPRelationships,
) -> LPStandaloneArtifacts:
    """Write standalone files after current-input and graph validation.

    The approved failure artifact is retained byte-for-byte, including resolved
    history. Unfinished processing raises before writing; its existing checkpoints and
    failures remain available for generation recovery. Loadable graph failures are
    written as diagnostics with a failed verdict. The summary is committed last and
    binds all companion bytes; an interrupted set is rejected by the reader.

    Parameters
    ----------
    as_lc_bundle
        Current authoritative validated upstream bundle.
    doc_key
        Current document identity.
    kg_config
        Current effective curriculum policy.
    kg_dirs
        Existing generation directory receiving standalone artifacts.
    relationships
        Complete internal rows and provenance from relationship finalization.

    Returns
    -------
    LPStandaloneArtifacts
        Read-back verified artifacts; inspect validation_report.passed before release.
    """

    expected, payloads, inputs = _prepare_artifacts(
        as_lc_bundle=as_lc_bundle,
        doc_key=doc_key,
        kg_config=kg_config,
        kg_dirs=kg_dirs,
        relationships=relationships,
    )
    root = kg_dirs.root

    with (root / ".lp_generation.lock").open("rb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _verify_inputs(expected=inputs, root=root)

        for name, payload in payloads.items():
            # Identical failure bytes keep the generation receipt and claims valid.
            _atomic_write(path=root / name, payload=payload)

        _verify_inputs(expected=inputs, root=root)
        return _round_trip(
            expected=expected,
            payloads={name: (root / name).read_bytes() for name in payloads},
        )
