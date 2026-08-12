"""This module contains the AS+LC KG bundle merge for KG creation.

Composes the final Academic Standards bundle, verbatim, with the LC layer
into one self-contained `AcademicStandardsLCKGBundle` plus flat
node/relationship projections for bulk graph loaders. The Academic
Standards bundle file is left untouched.

Sibling LC modules mirror the sfi_* layout: lc_selection.py (LC-source
selection), lc_generation.py (requests + LLM decomposition), lc_dedup.py
(duplicate grouping), lc_finalization.py (mint nodes, supports edges,
validate/summarize).
"""

# Standard Library
import json

from collections import Counter
from typing import Any, Sequence

# Third Party Library
from loguru import logger
from pydantic import ValidationError

# Package Library
from skg.kgs.lc_finalization import LC_ENTITY_PROVENANCE_FN
from skg.kgs.lc_generation import LC_GENERATION_FAILURES_FN
from skg.kgs.schemas import (
    AcademicStandardsKGBundle,
    AcademicStandardsLCExportSummary,
    AcademicStandardsLCKGBundle,
    AcademicStandardsLCUnresolvedItems,
    AcademicStandardsValidationReport,
    LCGenerationFailure,
    LCGenerationSummary,
    LCUnresolvedItems,
    LearningComponent,
    Relationship,
)
from skg.kgs.sfi_export import (
    _fingerprint_jsonable,
    _get_learning_commons_export_schema_version,
)
from skg.kgs.utils import KGDirs, make_dir
from skg.utils.general import write_to_json

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


def _write_flat_projections(
    *, bundle: AcademicStandardsLCKGBundle, kg_dirs: KGDirs
) -> None:
    """Write flat node/relationship projections of the merged bundle.

    Every node and every relationship of the bundle, one JSON record per
    line: node lines carry an injected `entity_type` discriminator
    (StandardsFramework, StandardsFrameworkItem, LearningComponent);
    relationship lines are the Relationship records verbatim.

    Parameters
    ----------
    bundle
        The compiled merged bundle.
    kg_dirs
        KG artifact directories; projections are written under
        ``kg_dirs.root``.
    """

    node_lines = [
        {
            "entity_type": "StandardsFramework",
            **bundle.framework.model_dump(mode="json"),
        },
        *(
            {"entity_type": "StandardsFrameworkItem", **item.model_dump(mode="json")}
            for item in bundle.items
        ),
        *(
            {"entity_type": "LearningComponent", **component.model_dump(mode="json")}
            for component in bundle.learning_components
        ),
    ]
    relationship_lines = [
        relationship.model_dump(mode="json")
        for relationship in (
            *bundle.relationships_has_child,
            *bundle.relationships_supports,
        )
    ]

    with (kg_dirs.root / "as_lc_nodes.jsonl").open("w", encoding="utf-8") as f:
        for node_line in node_lines:
            f.write(json.dumps(node_line, ensure_ascii=False, sort_keys=True) + "\n")
    with (kg_dirs.root / "as_lc_relationships.jsonl").open("w", encoding="utf-8") as f:
        for relationship_line in relationship_lines:
            f.write(
                json.dumps(relationship_line, ensure_ascii=False, sort_keys=True) + "\n"
            )


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
    validates the merged graph, and writes the bundle plus flat
    node/relationship projections. With ``overwrite=False`` an existing
    bundle whose input fingerprints match the freshly computed ones is
    returned as-is (projections are rewritten from it).

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
                _write_flat_projections(bundle=existing, kg_dirs=kg_dirs)
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
    _write_flat_projections(bundle=bundle, kg_dirs=kg_dirs)

    if errors:
        raise ValueError(
            f"AS+LC merge: merged-graph validation failed with "
            f"{len(errors)} errors (artifacts written for inspection); "
            f"first: {errors[0]}"
        )

    logger.success(
        f"Compiled AS+LC KG bundle: nodes={bundle.summary.total_node_count}; "
        f"relationships={bundle.summary.total_relationship_count}; "
        f"lcs={len(learning_components)}; supports={len(supports_edges)}"
    )
    return bundle
