"""This module contains the AS+LC KG bundle merge for KG creation.

Composes the final Academic Standards bundle, verbatim, with the LC layer
into one self-contained `AcademicStandardsLCKGBundle`, then writes the
Learning Commons-shaped delivery pair carrying both layers. The Academic
Standards bundle file is left untouched.

Sibling LC modules mirror the sfi_* layout: lc_selection.py (LC-source
selection), lc_generation.py (requests + LLM decomposition), lc_dedup.py
(duplicate grouping), lc_finalization.py (mint nodes, supports edges,
validate/summarize).
"""

# Standard Library
import json

from collections import Counter
from pathlib import Path
from typing import Any, Sequence

# Third Party Library
from loguru import logger
from pydantic import ValidationError

# Package Library
from kgfeg.kgs.lc_finalization import LC_ENTITY_PROVENANCE_FN
from kgfeg.kgs.lc_generation import LC_GENERATION_FAILURES_FN
from kgfeg.kgs.schemas import (
    AcademicStandardsKGBundle,
    AcademicStandardsLCExportSummary,
    AcademicStandardsLCKGBundle,
    AcademicStandardsLCUnresolvedItems,
    AcademicStandardsValidationReport,
    LCGenerationFailure,
    LCGenerationSummary,
    LCUnresolvedItems,
    LearningCommonsLearningComponentProperties,
    LearningCommonsNode,
    LearningComponent,
    Relationship,
)
from kgfeg.kgs.sfi_export import (
    _build_learning_commons_relationships,
    _fingerprint_jsonable,
    _get_learning_commons_export_schema_version,
)
from kgfeg.kgs.utils import KGDirs, append_jsonl_model, make_dir
from kgfeg.utils.general import write_to_json

_MERGED_VALIDATION_CHECKS = [
    "as_bundle_validation_gate",
    "supports_source_existence",
    "supports_target_existence",
    "every_lc_has_primary_edge",
    "supports_count_matches_claiming_sfis",
    "identifier_collision_absence",
    "lc_provenance_presence",
    "summary_count_alignment",
]


def _build_merged_entity_provenance(
    *,
    academic_standards_bundle: AcademicStandardsKGBundle,
    lc_entity_provenance: dict[str, Any],
) -> dict[str, Any]:
    """Merge AS and LC entity provenance without key collisions.

    Parameters
    ----------
    academic_standards_bundle
        Final AS bundle carrying its entity provenance.
    lc_entity_provenance
        LC entity-provenance artifact.

    Returns
    -------
    dict[str, Any]
        Merged provenance with the LC entries under `learning_components`.

    Raises
    ------
    ValueError
        If the AS provenance already carries a `learning_components` key
        or the two artifacts disagree on the document key.
    """

    as_provenance = academic_standards_bundle.entity_provenance
    if "learning_components" in as_provenance:
        raise ValueError(
            "AS+LC merge: AS entity provenance already contains a "
            "'learning_components' key; refusing to overwrite it."
        )
    as_doc_key = as_provenance.get("framework", {}).get("doc_key")
    if as_doc_key != lc_entity_provenance.get("doc_key"):
        raise ValueError(
            f"AS+LC merge: provenance doc_key mismatch: AS "
            f"{as_doc_key!r} vs LC {lc_entity_provenance.get('doc_key')!r}."
        )
    return {
        **as_provenance,
        "learning_components": lc_entity_provenance["learning_components"],
    }


def _validate_merged_graph(
    *,
    academic_standards_bundle: AcademicStandardsKGBundle,
    lc_generation_summary: LCGenerationSummary,
    learning_components: Sequence[LearningComponent],
    merged_entity_provenance: dict[str, Any],
    supports_edges: Sequence[Relationship],
) -> list[str]:
    """Validate the merged AS+LC graph as a whole.

    Parameters
    ----------
    academic_standards_bundle
        Final AS bundle (items are the supports targets).
    lc_generation_summary
        LC generation phase summary (count alignment).
    learning_components
        Minted LearningComponent nodes.
    merged_entity_provenance
        Merged provenance (every LC must have an entry).
    supports_edges
        Primary supports edges.

    Returns
    -------
    list[str]
        Validation errors; empty when the merged graph is sound.
    """

    errors: list[str] = []
    lc_ids = {str(component.identifier) for component in learning_components}
    item_uuids = {
        str(item.case_identifier_uuid) for item in academic_standards_bundle.items
    }

    for edge in supports_edges:
        if edge.source_entity_value not in lc_ids:
            errors.append(
                f"supports edge {edge.identifier} sources unknown LC "
                f"{edge.source_entity_value}."
            )
        if edge.target_entity_value not in item_uuids:
            errors.append(
                f"supports edge {edge.identifier} targets "
                f"{edge.target_entity_value}, which is not a bundle item."
            )

    sources_with_edges = {edge.source_entity_value for edge in supports_edges}
    for lc_id in sorted(lc_ids - sources_with_edges):
        errors.append(f"LC {lc_id} has no primary supports edge.")

    expected_edge_count = sum(
        len(component.metadata["source_sfi_uuids"]) for component in learning_components
    )
    if len(supports_edges) != expected_edge_count:
        errors.append(
            f"supports edge count {len(supports_edges)} does not equal the "
            f"claiming-SFI total {expected_edge_count}."
        )

    identifier_counts = Counter(
        [
            str(academic_standards_bundle.framework.case_identifier_uuid),
            *(
                str(item.case_identifier_uuid)
                for item in academic_standards_bundle.items
            ),
            *(str(component.identifier) for component in learning_components),
            *(
                str(relationship.identifier)
                for relationship in academic_standards_bundle.relationships_has_child
            ),
            *(str(edge.identifier) for edge in supports_edges),
        ]
    )
    if duplicated := sorted(
        identifier for identifier, count in identifier_counts.items() if count > 1
    ):
        errors.append(f"identifier collisions across the merged graph: {duplicated}.")

    lc_provenance = merged_entity_provenance.get("learning_components", {})
    for lc_id in sorted(lc_ids - set(lc_provenance)):
        errors.append(f"LC {lc_id} has no entity-provenance entry.")

    if lc_generation_summary.total_lcs != len(learning_components):
        errors.append(
            f"summary total_lcs {lc_generation_summary.total_lcs} does not "
            f"match compiled LC count {len(learning_components)}."
        )
    if lc_generation_summary.total_supports_edges != len(supports_edges):
        errors.append(
            f"summary total_supports_edges "
            f"{lc_generation_summary.total_supports_edges} does not match "
            f"compiled edge count {len(supports_edges)}."
        )
    return errors


def _build_learning_component_nodes(
    *, learning_components: Sequence[LearningComponent]
) -> list[LearningCommonsNode]:
    """Build slim Learning Commons-shaped node records for the LC layer.

    A LearningComponent has no CASE identity by design, so no CASE properties are
    emitted and `supports` edges key this endpoint on `identifier` instead. `tags` is
    omitted rather than emitted empty, keeping absent and empty distinguishable.

    Parameters
    ----------
    learning_components
        Minted LearningComponent entities in deterministic export order.

    Returns
    -------
    list[LearningCommonsNode]
        Learning Commons node records for every component.
    """

    nodes: list[LearningCommonsNode] = []
    for component in learning_components:
        identifier = str(component.identifier)
        tags = component.metadata.get("tags")
        nodes.append(
            LearningCommonsNode(
                identifier=identifier,
                labels=["LearningComponent"],
                properties=LearningCommonsLearningComponentProperties(
                    academic_subject=component.academic_subject,
                    attribution_statement=component.attribution_statement,
                    author=component.author,
                    description=component.description,
                    identifier=identifier,
                    identity_key=component.metadata["identity"]["identity_key"],
                    in_language=component.in_language,
                    license=component.license,
                    provider=component.provider,
                    tags=(
                        json.dumps(tags, ensure_ascii=False, separators=(",", ":"))
                        if tags
                        else None
                    ),
                ),
            )
        )
    return nodes


def _write_delivery_projection(
    *, bundle: AcademicStandardsLCKGBundle, kg_dirs: KGDirs
) -> tuple[Path, Path]:
    """Write the combined Learning Commons-shaped JSONL artifacts.

    Academic Standards lines are copied verbatim from the Academic Standards artifacts
    rather than rebuilt, so those records cannot drift and the LC layer is provably
    additive. The Academic Standards artifacts themselves are left untouched.

    Parameters
    ----------
    bundle
        The compiled merged bundle.
    kg_dirs
        KG artifact directories; artifacts are read from and written under
        ``kg_dirs.root``.

    Returns
    -------
    tuple[Path, Path]
        Paths of the written node and relationship delivery files.

    Raises
    ------
    ValueError
        If an Academic Standards wire artifact is missing.
    """

    as_nodes_fp = kg_dirs.root / "as_nodes.jsonl"
    as_relationships_fp = kg_dirs.root / "as_relationships.jsonl"

    for fp in (as_nodes_fp, as_relationships_fp):
        if not fp.exists():
            raise ValueError(
                f"Delivery projection: {fp.name} is absent; the Academic Standards "
                f"export must run before the merged delivery pair can be written."
            )

    as_node_lines = as_nodes_fp.read_text(encoding="utf-8").splitlines()
    as_relationship_lines = as_relationships_fp.read_text(encoding="utf-8").splitlines()

    as_nodes = [LearningCommonsNode.model_validate_json(line) for line in as_node_lines]
    component_nodes = _build_learning_component_nodes(
        learning_components=bundle.learning_components
    )
    supports_records = _build_learning_commons_relationships(
        nodes=[*as_nodes, *component_nodes],
        relationships=bundle.relationships_supports,
    )

    nodes_fp = kg_dirs.root / "as_lc_nodes.jsonl"
    relationships_fp = kg_dirs.root / "as_lc_relationships.jsonl"

    nodes_fp.write_text(
        "".join(f"{line}\n" for line in as_node_lines), encoding="utf-8"
    )
    for node in component_nodes:
        append_jsonl_model(
            by_alias=True, compact=True, exclude_none=True, fp=nodes_fp, model=node
        )

    relationships_fp.write_text(
        "".join(f"{line}\n" for line in as_relationship_lines), encoding="utf-8"
    )
    for record in supports_records:
        append_jsonl_model(
            by_alias=True,
            compact=True,
            exclude_none=True,
            fp=relationships_fp,
            model=record,
        )

    logger.success(
        f"Wrote delivery projection: {nodes_fp.name} "
        f"({len(as_node_lines)} AS + {len(component_nodes)} LC nodes); "
        f"{relationships_fp.name} "
        f"({len(as_relationship_lines)} hasChild + {len(supports_records)} supports)"
    )
    return nodes_fp, relationships_fp


def compile_as_lc_kg(
    *,
    academic_standards_bundle: AcademicStandardsKGBundle,
    kg_dirs: KGDirs,
    lc_generation_summary: LCGenerationSummary,
    learning_components: Sequence[LearningComponent],
    overwrite: bool,
    supports_edges: Sequence[Relationship],
) -> AcademicStandardsLCKGBundle:
    """Merge the AS bundle and the LC layer into one bundle.

    Requires a passed, error-free AS bundle validation report. Reads the
    LC generation failures and LC entity-provenance artifacts from
    ``kg_dirs.root``, composes the merged `AcademicStandardsLCKGBundle`,
    validates the merged graph, and writes the bundle plus the Learning
    Commons-shaped delivery pair. With ``overwrite=False`` an existing
    bundle whose input fingerprints match the freshly computed ones is
    returned as-is (the delivery pair is rewritten from it).

    Parameters
    ----------
    academic_standards_bundle
        Final AS bundle.
    kg_dirs
        KG artifact directories; artifacts are read from and written
        under ``kg_dirs.root``.
    lc_generation_summary
        LC generation phase summary.
    learning_components
        Minted LearningComponent nodes.
    overwrite
        When True, recompile even if a fingerprint-matching bundle exists.
    supports_edges
        Primary supports edges.

    Returns
    -------
    AcademicStandardsLCKGBundle
        The compiled (or reused) merged bundle.

    Raises
    ------
    ValueError
        If the AS bundle failed validation, the provenance merge
        collides, or the merged graph fails validation (artifacts are
        written before the raise).
    """

    report = academic_standards_bundle.validation_report
    if not report.passed or report.errors:
        raise ValueError(
            f"AS+LC merge: the AS bundle failed validation "
            f"(passed={report.passed}, errors={report.errors[:3]}); refusing "
            f"to merge an invalid standards KG."
        )

    lc_generation_failures = [
        LCGenerationFailure.model_validate(entry)
        for entry in json.loads((kg_dirs.root / LC_GENERATION_FAILURES_FN).read_text())
    ]
    lc_entity_provenance = json.loads(
        (kg_dirs.root / LC_ENTITY_PROVENANCE_FN).read_text()
    )

    input_fingerprints = {
        "academic_standards_kg_bundle": _fingerprint_jsonable(
            academic_standards_bundle.model_dump(mode="json")
        ),
        "lc_entity_provenance": _fingerprint_jsonable(lc_entity_provenance),
        "lc_generation_failures": _fingerprint_jsonable(
            [failure.model_dump(mode="json") for failure in lc_generation_failures]
        ),
        "lc_generation_summary": _fingerprint_jsonable(
            lc_generation_summary.model_dump(mode="json")
        ),
        "lc_supports_edges": _fingerprint_jsonable(
            [edge.model_dump(mode="json") for edge in supports_edges]
        ),
        "learning_components": _fingerprint_jsonable(
            [component.model_dump(mode="json") for component in learning_components]
        ),
    }

    bundle_fp = kg_dirs.root / "as_lc_kg_bundle.json"
    if not overwrite and bundle_fp.exists():
        try:
            existing = AcademicStandardsLCKGBundle.model_validate_json(
                bundle_fp.read_text()
            )
        except ValidationError:
            logger.warning(
                f"Existing {bundle_fp} is invalid; recompiling the merged "
                f"bundle from scratch."
            )
        else:
            if existing.validation_report.input_fingerprints == input_fingerprints:
                logger.info("Reusing existing AS+LC bundle: input fingerprints match.")
                make_dir(kg_dirs.root)
                _write_delivery_projection(bundle=existing, kg_dirs=kg_dirs)
                return existing
            logger.info(
                "Existing AS+LC bundle is stale (input fingerprints differ); "
                "recompiling."
            )

    merged_entity_provenance = _build_merged_entity_provenance(
        academic_standards_bundle=academic_standards_bundle,
        lc_entity_provenance=lc_entity_provenance,
    )
    errors = _validate_merged_graph(
        academic_standards_bundle=academic_standards_bundle,
        lc_generation_summary=lc_generation_summary,
        learning_components=learning_components,
        merged_entity_provenance=merged_entity_provenance,
        supports_edges=supports_edges,
    )

    bundle = AcademicStandardsLCKGBundle(
        entity_provenance=merged_entity_provenance,
        framework=academic_standards_bundle.framework,
        items=academic_standards_bundle.items,
        learning_components=list(learning_components),
        relationships_has_child=academic_standards_bundle.relationships_has_child,
        relationships_supports=list(supports_edges),
        summary=AcademicStandardsLCExportSummary(
            academic_standards=academic_standards_bundle.summary,
            learning_components=lc_generation_summary,
            total_node_count=(
                1 + len(academic_standards_bundle.items) + len(learning_components)
            ),
            total_relationship_count=(
                len(academic_standards_bundle.relationships_has_child)
                + len(supports_edges)
            ),
        ),
        unresolved_items=AcademicStandardsLCUnresolvedItems(
            academic_standards=academic_standards_bundle.unresolved_items,
            learning_components=LCUnresolvedItems(
                lc_generation_failures=lc_generation_failures,
                lc_source_exclusion_reason_counts=(
                    lc_generation_summary.lc_source_exclusion_reason_counts
                ),
            ),
        ),
        validation_report=AcademicStandardsValidationReport(
            errors=errors,
            input_fingerprints=input_fingerprints,
            learning_commons_export_schema_version=(
                _get_learning_commons_export_schema_version()
            ),
            object_counts={
                "frameworks": 1,
                "learning_components": len(learning_components),
                "relationships_has_child": len(
                    academic_standards_bundle.relationships_has_child
                ),
                "relationships_supports": len(supports_edges),
                "standards_framework_items": len(academic_standards_bundle.items),
            },
            passed=not errors,
            validation_checks=list(_MERGED_VALIDATION_CHECKS),
        ),
    )

    make_dir(kg_dirs.root)
    write_to_json(fp=bundle_fp, json_info=bundle.model_dump(mode="json"))

    if errors:
        raise ValueError(
            f"AS+LC merge: merged-graph validation failed with "
            f"{len(errors)} errors (artifacts written for inspection); "
            f"first: {errors[0]}"
        )

    _write_delivery_projection(bundle=bundle, kg_dirs=kg_dirs)

    logger.success(
        f"Compiled AS+LC KG bundle: nodes={bundle.summary.total_node_count}; "
        f"relationships={bundle.summary.total_relationship_count}; "
        f"lcs={len(learning_components)}; supports={len(supports_edges)}"
    )
    return bundle
