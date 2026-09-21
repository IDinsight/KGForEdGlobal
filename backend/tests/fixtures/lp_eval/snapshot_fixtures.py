"""Build small synthetic evaluator inputs entirely inside pytest temporary storage.

Upstream records are explicit test data, not curriculum execution evidence. Real LP
processing uses scripted model responses; snapshot validation itself is never mocked.
No fixture reads generated results or requires Git or a recorded evaluation invocation.
"""

# Future Library
from __future__ import annotations

# Standard Library
import json

from pathlib import Path
from typing import Any
from uuid import UUID

# Third Party Library
import pytest

# Package Library
from kgfeg.config import Settings
from kgfeg.document_ir.schemas import DocumentIR, DocumentPageMeta
from kgfeg.kgs import (
    lc_export,
    lp_artifacts,
    lp_export,
    lp_finalization,
    lp_generation,
    sfi_export,
)
from kgfeg.kgs.llm import KGUsageTracker
from kgfeg.kgs.schemas import (
    SFIFinalRecord,
    SFIFinalSummary,
    SFIHasChildEdge,
    SFIHasChildResolutionSummary,
)
from kgfeg.kgs.utils import KGDirs
from tests.kgfeg.kgs import test_lp_evidence as evidence
from tests.kgfeg.kgs import test_lp_finalization as claims
from tests.kgfeg.kgs import test_lp_selection as selection


def _dump(value: Any) -> str:
    """Encode fixture material with the canonical content-hash convention.

    Parameters
    ----------
    value
        JSON-compatible synthetic material.

    Returns
    -------
    str
        Stable compact JSON.
    """
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _write(root: Path, name: str, value: Any) -> None:
    """Persist synthetic JSON or JSONL below an isolated run.

    Parameters
    ----------
    root
        Temporary run directory.
    name
        Run-relative artifact name.
    value
        Synthetic artifact content.
    """
    payload = (
        "".join(_dump(row) + "\n" for row in value)
        if name.endswith(".jsonl")
        else _dump(value) + "\n"
    )
    (root / name).write_text(payload, encoding="utf-8")


def build_snapshot(
    *,
    capacity: int | None = None,
    count: int,
    identity: int,
    overwrite: bool = False,
    root: Path,
    title: str | None = None,
) -> Path:
    """Create a complete small current-format run with four scripted outcomes.

    Parameters
    ----------
    capacity
        Optional explicit runtime capacity; omission exercises the runtime default.
    count
        Eligible same-rank SFI count; every unordered pair is admissible.
    identity
        Distinct framework identity for generic multi-curriculum discovery.
    overwrite
        Effective overwrite value omitted by execution metadata and recovered by hash.
    root
        Temporary parent directory, never the repository results directory.
    title
        Optional unfamiliar framework title for consumer presentation tests.

    Returns
    -------
    Path
        Completed synthetic kgs directory suitable for real snapshot validation.
    """
    directory = root / f"synthetic-{identity}" / "kgs"
    directory.mkdir(parents=True)
    harness = claims._Harness(batch=3, count=count, root=directory)
    runtime = harness.config.model_dump(mode="json")
    runtime["lp"].pop("max_concurrent_requests")
    if capacity is not None:
        runtime["lp"]["max_concurrent_requests"] = capacity
    runtime["overwrite"] = overwrite
    harness.config = type(harness.config).model_validate(runtime)
    if title is not None:
        harness.config = harness.config.model_copy(
            update={
                "metadata": harness.config.metadata.model_copy(
                    update={"framework_title": title}
                )
            }
        )
    harness.decisions = {
        (1, 2): ("buildsTowards", "first_to_second"),
        (1, 3): ("relatesTo", None),
        (1, 4): ("needs_review", None),
    }
    doc_key = f"synthetic-evaluator-{identity}"
    document = DocumentIR(
        coord_space="px",
        doc_key=doc_key,
        dpi=72,
        page_count=1,
        pages=[
            DocumentPageMeta(dpi=72, image_height=100, image_width=100, page_index=0)
        ],
        pdf_name="synthetic.pdf",
        segments=[],
    )
    manifest = {
        "doc_key": doc_key,
        "document_ir_fp": str(directory.parent / "document_ir.json"),
        "framework_title": harness.config.metadata.framework_title,
        "kg_run_dir": str(directory),
        "page_count": 1,
        "pdf_name": "synthetic.pdf",
    }
    _write(directory.parent, "document_ir.json", document.model_dump(mode="json"))
    _write(directory, "kg_run_manifest.json", manifest)
    framework_uuid = sfi_export.build_standards_framework_uuid(doc_key)
    records = []
    hierarchy = []
    for number in range(1, count + 1):
        uid = UUID(int=number)
        candidate = f"synthetic-candidate-{number}"
        records.append(
            SFIFinalRecord(
                **selection._COMMON,
                case_identifier_uri=f"urn:uuid:{uid}",
                case_identifier_uuid=uid,
                code_resolution_method="no_source_code",
                code_resolution_reason="Synthetic no-code source.",
                confidence_max=1.0,
                confidence_min=1.0,
                description=f"Synthetic counting skill number {number}.",
                final_sfi_uuid=uid,
                identifier=uid,
                identity_key=candidate,
                identity_scope_key="synthetic-grade-one",
                identity_scope_values={"Grade": "PRIMARY ONE"},
                jurisdiction="Synthetic jurisdiction",
                language="en",
                merge_decision="singleton",
                merge_group_id=candidate,
                merge_reason="Synthetic singleton source.",
                normalized_statement_type="Standard",
                representative_candidate_id=candidate,
                source_context_keys=[candidate],
                source_normalized_statement_codes=[],
                source_registry_candidate_ids=[candidate],
                source_statement_codes=[],
                statement_type="Performance Objective",
            )
        )
        hierarchy.append(
            SFIHasChildEdge(
                child_final_sfi_uuid=uid,
                is_root_edge=True,
                llm_reason="Scripted synthetic root membership.",
                parent_endpoint_id=str(framework_uuid),
                relationship_id=UUID(int=2000 + number),
                source_entity="StandardsFramework",
                source_entity_uuid=framework_uuid,
                target_sfi_uuid=uid,
            )
        )
    for name, payload in {
        "sfi_final_records.json": [
            record.model_dump(mode="json") for record in records
        ],
        "sfi_final_summary.json": SFIFinalSummary(final_sfi_count=count).model_dump(
            mode="json"
        ),
        "has_child_edges_final.json": [
            edge.model_dump(mode="json") for edge in hierarchy
        ],
        "has_child_unresolved_edges.json": [],
        "has_child_resolution_summary.json": SFIHasChildResolutionSummary(
            edge_count=count,
            final_sfi_count=count,
            root_edge_count=count,
        ).model_dump(mode="json"),
    }.items():
        _write(directory, name, payload)
    academic = sfi_export.compile_academic_standards_kg(
        document_ir=document,
        has_child_edges=hierarchy,
        kg_config=harness.config,
        kg_dirs=KGDirs(root=directory),
        overwrite=False,
    )
    initial = selection._bundle(
        components=(
            evidence._component(
                description="Synthetic shared counting skill",
                number=1000,
                sfis=tuple(range(1, count + 1)),
            ),
        ),
        framework_uuid=framework_uuid,
        items=tuple(academic.items),
        supports=tuple(
            evidence._support(component=1000, sfi=n) for n in range(1, count + 1)
        ),
    )
    initial.learning_components[0].metadata["identity"] = {
        "identity_key": "synthetic-shared-skill"
    }
    initial.entity_provenance["learning_components"][str(UUID(int=1000))][
        "identity"
    ] = {"identity_key": "synthetic-shared-skill"}
    for name, payload in {
        "lc_generation_summary.json": initial.summary.learning_components.model_dump(
            mode="json"
        ),
        "lc_generation_failures.json": [],
        "lc_entity_provenance.json": {
            "doc_key": doc_key,
            "learning_components": initial.entity_provenance["learning_components"],
        },
        "learning_components.jsonl": [
            row.model_dump(mode="json") for row in initial.learning_components
        ],
        "lc_supports_edges.json": [
            row.model_dump(mode="json") for row in initial.relationships_supports
        ],
    }.items():
        _write(directory, name, payload)
    harness.bundle = lc_export.compile_as_lc_kg(
        academic_standards_bundle=academic,
        kg_dirs=KGDirs(root=directory),
        lc_generation_summary=initial.summary.learning_components,
        learning_components=initial.learning_components,
        overwrite=False,
        supports_edges=initial.relationships_supports,
    )
    kwargs: dict[str, Any] = {
        "as_lc_bundle": harness.bundle,
        "doc_key": doc_key,
        "kg_config": harness.config,
        "kg_dirs": KGDirs(root=directory),
    }
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Settings, "LLM_KG_MODEL", "openai:gpt-5.2")
        monkeypatch.setattr(
            lp_generation, "generate_learning_progressions_for_request", harness._call
        )
        lp_generation.generate_learning_progressions(
            **kwargs, overwrite=False, usage_tracker=KGUsageTracker()
        )
        lp_finalization.finalize_learning_progressions(**kwargs)
        relationships = lp_finalization.build_lp_relationships(**kwargs)
        lp_artifacts.write_lp_artifacts(**kwargs, relationships=relationships)
        lp_export.compile_as_lc_lp_kg(**kwargs, overwrite=False)
    config = harness.config.model_dump(mode="json")
    _write(
        directory,
        "kg_run.json",
        {
            "run_id": str(UUID(int=identity)),
            "started_at": "2026-01-01T00:00:00Z",
            "completed_at": "2026-01-01T01:00:00Z",
            "extra": {
                **{key: config[key] for key in ("as", "lc", "lp", "metadata")},
                "status": "success",
            },
        },
    )
    return directory
