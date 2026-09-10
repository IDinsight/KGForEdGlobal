"""Compile an additive AS+LC+LP bundle and its complete internal JSONL projections.

The compiler preserves upstream content and complete audit payloads. Compilation
validates structural/process integrity and does not establish pedagogical correctness.
"""

# Standard Library
import fcntl
import hashlib

from pathlib import Path
from typing import Any

# Package Library
from kgfeg.kgs.lp_artifacts import (
    LPStandaloneArtifacts,
    _atomic_write,
    read_lp_artifacts,
)
from kgfeg.kgs.lp_checkpoints import content_hash
from kgfeg.kgs.lp_requests import canonical_lp_json
from kgfeg.kgs.schemas import (
    AcademicStandardsLCKGBundle,
    AcademicStandardsLCLPKGBundle,
)
from kgfeg.kgs.utils import KGDirs
from kgfeg.schemas import CreateKGConfig

_BUNDLE = "as_lc_lp_kg_bundle.json"
_LP_PROVENANCE_KEYS = ("relationships_builds_towards", "relationships_relates_to")
_OPTIONAL_INPUTS = ("lp_eligibility_report.json", "lp_eligible_sfis.json")
_VALIDATION_CHECKS = [
    "as_lc_validation_gate",
    "authenticated_standalone_lp_artifacts",
    "lp_validation_gate",
    "upstream_content_preservation",
    "additive_relationship_provenance",
    "combined_count_reconciliation",
    "identifier_collision_absence",
    "material_content_hash_alignment",
    "structural_scope_only",
]


def _artifact_byte_hashes(artifacts: LPStandaloneArtifacts) -> dict[str, str]:
    """Collect every authenticated input and standalone artifact byte digest.

    Parameters
    ----------
    artifacts
        Current-material-validated standalone records.

    Returns
    -------
    dict[str, str]
        Complete file bindings, including the summary itself.

    Raises
    ------
    ValueError
        If two material records give different hashes for the same file.
    """

    hashes = dict(artifacts.summary.input_artifact_byte_hashes)

    for name, digest in artifacts.summary.artifact_byte_hashes.items():
        if name in hashes and hashes[name] != digest:
            raise ValueError(f"LP artifact byte hashes disagree for {name}.")

        hashes[name] = digest

    hashes["lp_generation_summary.json"] = content_hash(
        artifacts.summary.model_dump(mode="json")
    )
    return hashes


def _combined_counts(material: dict[str, Any]) -> dict[str, int]:
    """Count actual node and relationship populations without trusting summaries.

    Parameters
    ----------
    material
        Complete combined bundle payload.

    Returns
    -------
    dict[str, int]
        Actual graph populations across all three layers.
    """

    counts = {
        "frameworks": 1,
        "standards_framework_items": len(material["items"]),
        "learning_components": len(material["learning_components"]),
        **{
            name: len(material[name])
            for name in (
                "relationships_has_child",
                "relationships_supports",
                *_LP_PROVENANCE_KEYS,
            )
        },
    }
    counts["total_node_count"] = (
        1 + counts["standards_framework_items"] + counts["learning_components"]
    )
    counts["total_relationship_count"] = sum(
        count for name, count in counts.items() if name.startswith("relationships_")
    )
    return counts


def _complete_export(
    *, bundle: AcademicStandardsLCLPKGBundle, root: Path
) -> AcademicStandardsLCLPKGBundle:
    """Verify bundle authority, rewrite projections, and recheck locked material.

    Parameters
    ----------
    bundle
        Freshly authenticated expected combined graph.
    root
        Artifact directory whose exclusive generation lock the caller holds.

    Returns
    -------
    AcademicStandardsLCLPKGBundle
        Read-back-verified graph after both projections reconcile.

    Raises
    ------
    ValueError
        If evidence, bundle, or projection bytes change or fail reconciliation.
    """

    hashes = bundle.validation_report.artifact_byte_hashes
    path = root / _BUNDLE
    _verify_artifact_bytes(hashes=hashes, root=root)
    restored = _read_bundle(expected=bundle, path=path)
    _write_projections(bundle=restored, root=root)
    _verify_artifact_bytes(hashes=hashes, root=root)
    return _read_bundle(expected=bundle, path=path)


def _merge_material(
    *, artifacts: LPStandaloneArtifacts, upstream: dict[str, Any]
) -> dict[str, Any]:
    """Compose complete upstream content with non-colliding LP records.

    Parameters
    ----------
    artifacts
        Authenticated complete LP payloads.
    upstream
        Detached JSON snapshot of the authoritative AS+LC bundle.

    Returns
    -------
    dict[str, Any]
        Combined graph payload, before its validation report is attached.

    Raises
    ------
    ValueError
        If an LP provenance namespace would overwrite upstream content.
    """

    provenance = dict(upstream["entity_provenance"])
    lp_provenance = artifacts.relationships.relationship_provenance
    additions: dict[str, Any] = {}

    for name in _LP_PROVENANCE_KEYS:
        if name in provenance:
            raise ValueError(
                f"AS+LC+LP merge: upstream provenance already contains {name!r}."
            )

        rows = getattr(artifacts.relationships, name)
        additions[name] = [row.model_dump(mode="json") for row in rows]
        provenance[name] = {
            str(row.identifier): lp_provenance[str(row.identifier)].model_dump(
                mode="json"
            )
            for row in rows
        }

    material = {
        **{key: value for key, value in upstream.items() if key != "validation_report"},
        **additions,
        "entity_provenance": provenance,
        "summary": {
            **upstream["summary"],
            "as_lc_summary": upstream["summary"],
            "learning_progressions": artifacts.summary.model_dump(mode="json"),
        },
        "unresolved_items": {
            **upstream["unresolved_items"],
            "learning_progressions": artifacts.unresolved_items.model_dump(mode="json"),
        },
    }
    counts = _combined_counts(material)
    material["summary"].update(
        total_node_count=counts["total_node_count"],
        total_relationship_count=counts["total_relationship_count"],
    )
    return material


def _prepare_bundle(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
) -> AcademicStandardsLCLPKGBundle:
    """Reconstruct the exact combined authority from current inputs and stored evidence.

    The standalone reader authenticates complete candidate/request populations, aligned
    producer/checker/response checkpoints, failure dispositions, actual prompt/model
    material, and finalization inputs before the merge is considered. Stored passed
    flags and self-consistent hashes alone are insufficient authority.

    Parameters
    ----------
    as_lc_bundle
        Complete authoritative upstream graph.
    doc_key
        Current source document identity.
    kg_config
        Current effective curriculum configuration.
    kg_dirs
        Directory containing complete standalone and generation evidence.

    Returns
    -------
    AcademicStandardsLCLPKGBundle
        Detached, validated expected graph with complete material bindings.

    Raises
    ------
    ValueError
        If upstream, standalone, checkpoint, or combined integrity fails.
    """

    upstream_bundle = AcademicStandardsLCKGBundle.model_validate_json(
        as_lc_bundle.model_dump_json()
    )
    config = CreateKGConfig.model_validate_json(
        kg_config.model_dump_json(by_alias=True)
    )
    upstream = upstream_bundle.model_dump(mode="json")
    report = upstream_bundle.validation_report

    if not report.passed or report.errors:
        raise ValueError("AS+LC+LP merge requires a passed, error-free AS+LC bundle.")

    artifacts = read_lp_artifacts(
        as_lc_bundle=upstream_bundle, doc_key=doc_key, kg_config=config, kg_dirs=kg_dirs
    )
    lp_report = artifacts.validation_report

    if not lp_report.passed or lp_report.errors:
        raise ValueError(
            "AS+LC+LP merge: standalone LP validation failed; inspect lp_validation_report.json."
        )

    if artifacts.summary.object_counts["unresolved_failed_pairs"]:
        raise ValueError("AS+LC+LP merge: unresolved LP processing failures remain.")

    material = _merge_material(artifacts=artifacts, upstream=upstream)
    _validate_combined_material(
        artifacts=artifacts, material=material, upstream=upstream
    )
    byte_hashes = _artifact_byte_hashes(artifacts)
    input_hashes = {
        **artifacts.summary.input_content_hashes,
        "combined_graph": content_hash(material),
        "lp_generation_summary": content_hash(
            artifacts.summary.model_dump(mode="json")
        ),
        "lp_unresolved_items": content_hash(
            artifacts.unresolved_items.model_dump(mode="json")
        ),
        "lp_validation_report": content_hash(lp_report.model_dump(mode="json")),
    }

    if input_hashes["as_lc_bundle"] != content_hash(upstream) or input_hashes[
        "effective_config"
    ] != content_hash(config.model_dump(by_alias=True, mode="json")):
        raise ValueError(
            "AS+LC+LP merge: LP input hashes differ from current material."
        )

    material["validation_report"] = {
        "as_lc_validation_report": upstream["validation_report"],
        "artifact_byte_hashes": byte_hashes,
        "errors": [],
        "input_content_hashes": input_hashes,
        "lp_validation_report": lp_report.model_dump(mode="json"),
        "object_counts": _combined_counts(material),
        "passed": True,
        "pedagogical_correctness_established": lp_report.pedagogical_correctness_established,
        "semantic_scope_notice": lp_report.semantic_scope_notice,
        "semantic_validation_performed": lp_report.semantic_validation_performed,
        "validation_checks": list(_VALIDATION_CHECKS),
        "warnings": list(lp_report.warnings),
    }
    bundle = AcademicStandardsLCLPKGBundle.model_validate(material)

    if bundle.model_dump(mode="json") != material:
        raise ValueError("AS+LC+LP bundle schema changed the compiled material.")

    return bundle


def _read_bundle(
    *, expected: AcademicStandardsLCLPKGBundle, path: Path
) -> AcademicStandardsLCLPKGBundle:
    """Require the stored bundle to equal freshly authenticated combined material.

    Parameters
    ----------
    expected
        Current-material-validated graph, counts, provenance, and validation report.
    path
        Existing combined bundle, never repaired by this reader.

    Returns
    -------
    AcademicStandardsLCLPKGBundle
        Detached model parsed from the exact canonical saved bytes.

    Raises
    ------
    ValueError
        If the bundle is invalid, noncanonical, stale, or changed during export.
    """

    observed = path.read_bytes()
    restored = AcademicStandardsLCLPKGBundle.model_validate_json(observed)
    payload = (canonical_lp_json(expected.model_dump(mode="json")) + "\n").encode(
        "utf-8"
    )

    if observed != payload or restored != expected:
        raise ValueError(
            "AS+LC+LP bundle differs from current validated material; "
            "regenerate affected artifacts before reuse."
        )

    return restored


def _validate_combined_material(
    *,
    artifacts: LPStandaloneArtifacts,
    material: dict[str, Any],
    upstream: dict[str, Any],
) -> None:
    """Reject changed upstream content, inconsistent counts, or graph collisions.

    Parameters
    ----------
    artifacts
        Authenticated LP relationship and summary authority.
    material
        Complete combined graph payload without its report.
    upstream
        Exact authoritative AS+LC snapshot.

    Raises
    ------
    ValueError
        If preservation, population accounting, or identifier uniqueness fails.
    """

    recovered = {
        **{key: material[key] for key in upstream if key != "validation_report"},
        "entity_provenance": {
            key: value
            for key, value in material["entity_provenance"].items()
            if key not in _LP_PROVENANCE_KEYS
        },
        "summary": material["summary"]["as_lc_summary"],
        "unresolved_items": {
            key: value
            for key, value in material["unresolved_items"].items()
            if key != "learning_progressions"
        },
        "validation_report": upstream["validation_report"],
    }

    if recovered != upstream or any(
        material["summary"][name] != upstream["summary"][name]
        for name in ("academic_standards", "learning_components")
    ):
        raise ValueError("AS+LC+LP merge changed upstream content.")

    counts = _combined_counts(material)
    summary = upstream["summary"]
    as_summary = summary["academic_standards"]
    lc_summary = summary["learning_components"]
    expected_counts = (
        (summary["total_node_count"], counts["total_node_count"]),
        (
            summary["total_relationship_count"],
            counts["relationships_has_child"] + counts["relationships_supports"],
        ),
        (as_summary["framework_count"], 1),
        (as_summary["final_sfi_count"], counts["standards_framework_items"]),
        (as_summary["has_child_relationship_count"], counts["relationships_has_child"]),
        (lc_summary["total_lcs"], counts["learning_components"]),
        (lc_summary["total_supports_edges"], counts["relationships_supports"]),
        (
            artifacts.summary.object_counts["builds_towards_relationships"],
            counts["relationships_builds_towards"],
        ),
        (
            artifacts.summary.object_counts["relates_to_relationships"],
            counts["relationships_relates_to"],
        ),
    )

    if any(observed != expected for observed, expected in expected_counts):
        raise ValueError(
            "AS+LC+LP merge: summary counts differ from actual populations."
        )

    identifiers = [
        material["framework"]["case_identifier_uuid"],
        *(item["case_identifier_uuid"] for item in material["items"]),
        *(node["identifier"] for node in material["learning_components"]),
        *(
            edge["identifier"]
            for name in (
                "relationships_has_child",
                "relationships_supports",
                *_LP_PROVENANCE_KEYS,
            )
            for edge in material[name]
        ),
    ]

    if len(identifiers) != len(set(identifiers)):
        raise ValueError(
            "AS+LC+LP merge: identifiers collide across the combined graph."
        )


def _verify_artifact_bytes(*, hashes: dict[str, str], root: Path) -> None:
    """Reject evidence changes between standalone authentication and bundle writing.

    Parameters
    ----------
    hashes
        Expected byte hashes from the authenticated standalone population.
    root
        Locked generation and standalone artifact directory.

    Raises
    ------
    ValueError
        If generation is unfinished or any authenticated input changed.
    """

    if (root / "lp_generation_checkpoint_transaction.json").exists():
        raise ValueError("LP generation transaction is unfinished; resume generation.")

    for name, digest in hashes.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"LP artifact changed during bundle compilation: {name}.")

    for name in _OPTIONAL_INPUTS:
        if name not in hashes and (root / name).exists():
            raise ValueError(f"LP input appeared during bundle compilation: {name}.")


def _write_projections(*, bundle: AcademicStandardsLCLPKGBundle, root: Path) -> None:
    """Write and read back complete internal projections of a validated bundle.

    Nodes are grouped as framework, standards items, then learning components;
    relationships as hasChild, supports, buildsTowards, then relatesTo. Each group is
    sorted by its identifier without changing the authoritative bundle order. Every
    internal field is retained, including nulls and relationship metadata. The caller
    holds the artifact lock throughout writing and verification.

    Parameters
    ----------
    bundle
        Successfully compiled and read-back-verified combined graph.
    root
        Locked output directory for the two combined JSONL projections.

    Raises
    ------
    ValueError
        If projection counts disagree with the bundle or persisted bytes change.
    """

    material = bundle.model_dump(mode="json")
    nodes = [
        {**material["framework"], "entity_type": "StandardsFramework"},
        *(
            {**item, "entity_type": "StandardsFrameworkItem"}
            for item in sorted(
                material["items"], key=lambda item: item["case_identifier_uuid"]
            )
        ),
        *(
            {**component, "entity_type": "LearningComponent"}
            for component in sorted(
                material["learning_components"],
                key=lambda component: component["identifier"],
            )
        ),
    ]
    relationships = [
        edge
        for name in (
            "relationships_has_child",
            "relationships_supports",
            *_LP_PROVENANCE_KEYS,
        )
        for edge in sorted(material[name], key=lambda edge: edge["identifier"])
    ]

    if (
        len(nodes) != bundle.summary.total_node_count
        or len(relationships) != bundle.summary.total_relationship_count
    ):
        raise ValueError("AS+LC+LP projection counts differ from the bundle.")

    payloads = {
        "as_lc_lp_nodes.jsonl": "".join(
            canonical_lp_json(row) + "\n" for row in nodes
        ).encode("utf-8"),
        "as_lc_lp_relationships.jsonl": "".join(
            canonical_lp_json(row) + "\n" for row in relationships
        ).encode("utf-8"),
    }

    for name, payload in payloads.items():
        _atomic_write(path=root / name, payload=payload)

    for name, payload in payloads.items():
        if (root / name).read_bytes() != payload:
            raise ValueError(f"AS+LC+LP projection round-trip changed {name}.")


def compile_as_lc_lp_kg(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
    overwrite: bool = False,
) -> AcademicStandardsLCLPKGBundle:
    """Compile or exactly reuse a validated bundle and rewrite its projections.

    Every invocation authenticates current inputs and complete checkpoint authority.
    With overwrite disabled, an existing bundle must exactly match that authority;
    stale or invalid bundles raise without replacing evidence. Explicit overwrite
    replaces the bundle only after current standalone artifacts validate. Missing
    bundles are compiled from those same validated artifacts.

    Parameters
    ----------
    as_lc_bundle
        Complete validated upstream bundle returned by the AS+LC compiler.
    doc_key
        Current source document identity.
    kg_config
        Current effective curriculum configuration.
    kg_dirs
        Existing KG directory containing complete standalone LP artifacts.
    overwrite
        Whether to replace an existing final bundle after validating current inputs.

    Returns
    -------
    AcademicStandardsLCLPKGBundle
        Verified combined graph with complete upstream content and additive LP data.

    Raises
    ------
    ValueError
        If material or validation fails, or an existing bundle is stale or invalid
        while overwrite is disabled.
    """

    if not isinstance(overwrite, bool):
        raise ValueError("LP overwrite must be an explicit boolean.")

    bundle = _prepare_bundle(
        as_lc_bundle=as_lc_bundle, doc_key=doc_key, kg_config=kg_config, kg_dirs=kg_dirs
    )
    path = kg_dirs.root / _BUNDLE

    with (kg_dirs.root / ".lp_generation.lock").open("rb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _verify_artifact_bytes(
            hashes=bundle.validation_report.artifact_byte_hashes, root=kg_dirs.root
        )

        if overwrite or not (path.exists() or path.is_symlink()):
            payload = (canonical_lp_json(bundle.model_dump(mode="json")) + "\n").encode(
                "utf-8"
            )
            _atomic_write(path=path, payload=payload)

        return _complete_export(bundle=bundle, root=kg_dirs.root)


def reuse_as_lc_lp_kg(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
) -> AcademicStandardsLCLPKGBundle | None:
    """Reuse an existing final graph only after complete current-material validation.

    Absence returns None so normal generation may resume. Any existing but invalid,
    stale, incomplete, or misaligned release fails closed. Reuse does not initialize,
    recover, or advance checkpoints, and never rewrites the bundle or standalone
    evidence. Both flat projections are regenerated from the validated saved bundle.

    Parameters
    ----------
    as_lc_bundle
        Authoritative bundle returned by current AS+LC compilation.
    doc_key
        Current source document identity.
    kg_config
        Current effective configuration, including LP policy and retry counts.
    kg_dirs
        Directory holding the final graph and its complete evidence chain.

    Returns
    -------
    AcademicStandardsLCLPKGBundle | None
        Verified saved graph, or None when no final bundle exists.
    """

    path = kg_dirs.root / _BUNDLE

    if not (path.exists() or path.is_symlink()):
        return None

    bundle = _prepare_bundle(
        as_lc_bundle=as_lc_bundle, doc_key=doc_key, kg_config=kg_config, kg_dirs=kg_dirs
    )

    with (kg_dirs.root / ".lp_generation.lock").open("rb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _complete_export(bundle=bundle, root=kg_dirs.root)
