"""Red-team canonical LP pair permissions over validated curriculum inputs."""

# Future Library
from __future__ import annotations

# Standard Library
import ast
import builtins
import hashlib
import json
import socket
import subprocess
import sys

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import fields
from itertools import combinations, permutations
from operator import itemgetter
from pathlib import Path
from textwrap import dedent
from typing import Any
from unittest.mock import MagicMock
from uuid import NAMESPACE_URL, UUID, uuid5

# Third Party Library
import pytest

# Package Library
from kgfeg.kgs import lp_admissibility, lp_candidates, lp_evidence
from kgfeg.kgs.lp_admissibility import (
    LPCandidateFilter,
    LPPairAdmissibility,
    build_lp_pair_filter,
    build_lp_pair_id,
)
from kgfeg.kgs.lp_candidates import (
    LPCandidatePopulation,
    build_lp_candidates,
    write_lp_candidate_artifacts,
)
from kgfeg.kgs.lp_evidence import LPPairEvidence
from kgfeg.kgs.lp_selection import build_lp_selection
from kgfeg.kgs.schemas import (
    AcademicStandardsLCKGBundle,
    LPCandidateEvidence,
    LPCandidatePair,
    LPCandidateSummary,
    StandardsFrameworkItem,
)
from kgfeg.kgs.utils import KGDirs
from kgfeg.schemas import CreateKGConfig
from tests.constants import PACKAGE_PATH, PARAM
from tests.fixtures.lp.loader import LP_FIXTURES_DIR, load_lp_regression_fixture

_DOC_KEY = "synthetic-selection-document"
_NONPUBLISHING: set[tuple[str, str | None]] = {
    ("no_relation", None),
    ("needs_review", None),
}
_POLICIES = ("exclude_unresolved", "include_unresolved_with_warnings")


_COMMON = {
    "academic_subject": "Synthetic subject",
    "attribution_statement": "Synthetic attribution",
    "author": "Synthetic authority",
    "in_language": "en",
    "license": "Synthetic license",
    "provider": "Synthetic provider",
}


_FRAMEWORK_UUID = UUID("00000000-0000-4000-8000-000000000100")


_PROFILES = {
    "ghana_english": (
        "examples/ghana/config_english_curriculum.json",
        "Grade",
        ("BASIC 1", "BASIC 2", "BASIC 3"),
        ("Content Standard", "Indicator"),
        ("Grade", "Strand", "Sub-Strand"),
    ),
    "ghana_math": (
        "examples/ghana/config_math_curriculum.json",
        "Grade",
        ("BASIC 4", "BASIC 5", "BASIC 6"),
        ("Content Standard", "Indicator"),
        ("Grade", "Strand", "Sub-Strand"),
    ),
    "madhi_math": (
        "examples/india/madhi/config_math.json",
        "Class",
        ("Class-1", "Class-2", "Class-3", "Class-4", "Class-5"),
        ("Content",),
        ("Curricular Goal", "Competency", "Class"),
    ),
    "nigeria_math": (
        "examples/nigeria/config_math_curriculum_1_3.json",
        "Grade",
        ("PRIMARY ONE", "PRIMARY TWO", "PRIMARY THREE"),
        ("Performance Objective",),
        ("Grade", "Theme", "Sub-Theme", "Topic"),
    ),
    "pratham_science": (
        "examples/india/pratham/config_science.json",
        "Class",
        ("Class IX", "Class X"),
        (
            "NCERT Learning Outcome",
            "Content Domain Specific Learning Outcome",
            "Indicator",
        ),
        ("Class", "Content Domain", "Chapter"),
    ),
    "rwanda_math": (
        "examples/rwanda/config_math_curriculum_p1_p3.json",
        "Grade",
        ("P1", "P2", "P3"),
        (
            "Grade Key Competence",
            "Key Unit Competence",
            "Knowledge Objective",
            "Skills Objective",
            "Attitudes and Values Objective",
        ),
        ("Grade", "Topic Area", "Sub-Topic Area", "Unit"),
    ),
}


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject network connections during deterministic selection tests.

    Parameters
    ----------
    monkeypatch
        Fixture that restores socket behavior after each test.
    """

    def _reject(*args: Any, **kwargs: Any) -> None:
        """Fail any attempted external connection.

        Parameters
        ----------
        args
            Positional connection arguments.
        kwargs
            Named connection arguments.

        Raises
        ------
        AssertionError
            Whenever a connection is attempted.
        """

        raise AssertionError("Eligibility tests must not access the network.")

    monkeypatch.setattr(name="connect", target=socket.socket, value=_reject)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=_reject)
    monkeypatch.setattr(name="create_connection", target=socket, value=_reject)


def _bundle(
    *,
    components: tuple[dict[str, Any], ...] = (),
    edges: tuple[dict[str, Any], ...] = (),
    framework_uuid: UUID = _FRAMEWORK_UUID,
    items: tuple[StandardsFrameworkItem, ...],
    supports: tuple[dict[str, Any], ...] = (),
) -> AcademicStandardsLCKGBundle:
    """Create complete synthetic upstream inputs without preselecting SFIs.

    Parameters
    ----------
    components
        Optional complete Learning Component payloads.
    edges
        Positive or fallback hierarchy relationships.
    framework_uuid
        Framework identifier, including the reduced fixture's identifier.
    items
        Final SFIs with exact metadata under test.
    supports
        Optional complete LC support payloads.

    Returns
    -------
    AcademicStandardsLCKGBundle
        Schema-valid bundle with complete roots and reconciled population counts.
    """

    hierarchy = list(edges)
    attached = {str(edge["target_entity_value"]) for edge in hierarchy}
    for item in items:
        if str(item.case_identifier_uuid) not in attached:
            hierarchy.append(
                _edge(
                    source=framework_uuid,
                    source_entity="StandardsFramework",
                    target=item.case_identifier_uuid,
                )
            )
    unresolved = [
        deepcopy(edge)
        for edge in hierarchy
        if edge.get("metadata", {}).get("unresolved_root_fallback") is True
    ]
    supported = {str(edge["target_entity_value"]) for edge in supports}
    lc_summary: dict[str, Any] = {
        name: 0
        for name in (
            "lc_dedup_candidate_pair_count",
            "lc_dedup_conflict_count",
            "lc_dedup_judged_same_count",
            "lc_generation_failed_sfis_count",
            "lc_multi_claim_lc_count",
            "llm_request_count",
            "llm_response_count",
            "total_lc_source_sfis_empty_text",
        )
    }
    lc_summary.update(
        lc_max_splits_observed=1 if components else 0,
        lc_multi_parent_lc_count=sum(
            len(component["metadata"]["source_sfi_uuids"]) > 1
            for component in components
        ),
        lc_selection_mode="explicit_allowlist",
        total_lc_claims=len(supports),
        total_lc_source_sfis_considered=len(items),
        total_lc_source_sfis_eligible=len(supported),
        total_lc_source_sfis_excluded=len(items) - len(supported),
        total_lcs=len(components),
        total_supports_edges=len(supports),
    )
    return AcademicStandardsLCKGBundle.model_validate(
        {
            "entity_provenance": {
                "framework": {"doc_key": "synthetic-selection-document"},
                "items": {
                    str(item.case_identifier_uuid): {
                        "audit": {"flags": ["source-code-anomaly", "merged-source"]},
                        "source": deepcopy(item.metadata),
                        "source_pages": [9, 2],
                    }
                    for item in items
                },
                "kg_run_manifest": {"run": "synthetic-local-test"},
                "learning_components": {
                    str(component["identifier"]): deepcopy(component["metadata"])
                    for component in components
                },
                "relationships_has_child": {
                    str(edge["identifier"]): deepcopy(edge.get("metadata", {}))
                    for edge in hierarchy
                },
            },
            "framework": {
                **_COMMON,
                "adoption_status": "Adopted",
                "case_identifier_uri": f"urn:uuid:{framework_uuid}",
                "case_identifier_uuid": framework_uuid,
                "identifier": framework_uuid,
                "jurisdiction": "Synthetic jurisdiction",
                "metadata": {"doc_key": "synthetic-selection-document"},
                "name": "Synthetic framework",
            },
            "items": items,
            "learning_components": components,
            "relationships_has_child": hierarchy,
            "relationships_supports": supports,
            "summary": {
                "academic_standards": {
                    "final_sfi_count": len(items),
                    "framework_count": 1,
                    "has_child_relationship_count": len(hierarchy),
                    "learning_commons_node_count": 1 + len(items),
                    "learning_commons_relationship_count": len(hierarchy),
                    "learning_commons_unresolved_fallback_relationship_count": len(
                        unresolved
                    ),
                    "relationship_unresolved_edge_count": len(unresolved),
                },
                "learning_components": lc_summary,
                "total_node_count": 1 + len(items) + len(components),
                "total_relationship_count": len(hierarchy) + len(supports),
            },
            "unresolved_items": {
                "academic_standards": {"relationship_unresolved_edges": unresolved},
                "learning_components": {},
            },
            "validation_report": {
                "learning_commons_export_schema_version": "synthetic-test",
                "passed": True,
            },
        }
    )


def _candidate_evidence(
    *, evidence_types: tuple[str, ...], pair: LPPairAdmissibility
) -> tuple[LPCandidateEvidence, ...]:
    """Create controlled named evidence without changing pair permissions.

    Parameters
    ----------
    evidence_types
        Fixed built-in signal names to attach to the pair.
    pair
        Real hard-filter result supplying admissible relationship outcomes.

    Returns
    -------
    tuple[LPCandidateEvidence, ...]
        Deterministic evidence records for ranking and union tests.
    """

    relationships = sorted(
        {
            decision.decision
            for decision in pair.admissible_decisions
            if decision.decision in {"buildsTowards", "relatesTo"}
        }
    )
    return tuple(
        LPCandidateEvidence(
            evidence_type=evidence_type,
            nominated_relationships=relationships,
            references=[
                f"sfi:{pair.first_sfi_uuid}",
                f"sfi:{pair.second_sfi_uuid}",
            ],
            triggering_values={"controlled_match": evidence_type},
        )
        for evidence_type in evidence_types
    )


def _canonical_hash(value: Any) -> str:
    """Compute an independent canonical JSON content digest.

    Parameters
    ----------
    value
        JSON-compatible material whose exact serialized content is identified.

    Returns
    -------
    str
        SHA-256 digest of stable compact Unicode JSON.
    """

    serialized = json.dumps(
        allow_nan=False,
        ensure_ascii=False,
        obj=value,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _config(profile: str = "nigeria_math") -> CreateKGConfig:
    """Read and cross-validate a real profile without executing its pipeline.

    Parameters
    ----------
    profile
        Name of the profile pinned in the independent policy table.

    Returns
    -------
    CreateKGConfig
        Complete validated KG configuration.
    """

    return CreateKGConfig.model_validate(
        json.loads((PACKAGE_PATH / _PROFILES[profile][0]).read_text())["kgs"]
    )


def _config_with_budgets(
    *,
    max_candidates_per_sfi: int,
    max_total_candidates: int,
    profile: str = "nigeria_math",
) -> CreateKGConfig:
    """Return one real profile with only its explicit budgets changed.

    Parameters
    ----------
    max_candidates_per_sfi
        Maximum retained incident pairs for one eligible SFI.
    max_total_candidates
        Maximum retained pairs for the complete candidate population.
    profile
        Real cross-validated curriculum profile to clone.

    Returns
    -------
    CreateKGConfig
        Complete validated configuration with the requested positive budgets.
    """

    payload = _config(profile).model_dump(mode="json", by_alias=True)
    payload["lp"]["candidate_policy"]["budgets"] = {
        "max_candidates_per_sfi": max_candidates_per_sfi,
        "max_total_candidates": max_total_candidates,
    }
    return CreateKGConfig.model_validate(payload)


def _corrupt_population(
    *, defect: str, original: LPCandidatePopulation
) -> LPCandidatePopulation:
    """Create schema-valid material disagreement for the artifact writer to reject.

    Parameters
    ----------
    defect
        Row or summary inconsistency to inject.
    original
        Real population whose material was independently constructed.

    Returns
    -------
    LPCandidatePopulation
        Intrinsically valid rows and summary that disagree with each other or inputs.
    """

    rows = [row.model_dump(mode="json") for row in original.candidates]
    summary = original.summary.model_dump(mode="json")
    assert len(rows) == summary["total_candidate_pairs_with_warnings"] == 5
    if defect == "decisions":
        rows[0]["admissible_decisions"] = rows[0]["admissible_decisions"][1:]
    elif defect == "duplicate_row":
        rows = [deepcopy(rows[0]), deepcopy(rows[0])]
        summary["total_candidate_pairs_dropped_by_per_sfi_budget"] += 3
        _match_summary_to_rows(rows=rows, summary=summary)
    elif defect == "pair_id":
        rows[0]["pair_id"] = str(UUID(int=999))
    elif defect == "row_count":
        rows.pop()
    elif defect == "row_order":
        rows.reverse()
    elif defect in {"invented_row_warnings", "row_warnings"}:
        rows[0]["warnings"] = {
            "invented_row_warnings": ["Invented warning absent from endpoint context."],
            "row_warnings": [],
        }[defect]
        _match_summary_to_rows(rows=rows, summary=summary)
    else:
        summary.update(_summary_corruptions(summary)[defect])
    if defect != "candidate_hash":
        summary["candidate_pairs_content_hash"] = _canonical_hash(rows)
    return LPCandidatePopulation(
        candidates=tuple(LPCandidatePair.model_validate(row) for row in rows),
        summary=LPCandidateSummary.model_validate(summary),
    )


def _decisions(pair: LPPairAdmissibility | None) -> set[tuple[str, str | None]]:
    """Read the exact distinct outcome set without deriving its expected permissions.

    Parameters
    ----------
    pair
        Filter result, or a normal policy exclusion.

    Returns
    -------
    set[tuple[str, str | None]]
        Actual decision and direction tuples; duplicates are rejected.
    """

    if pair is None:
        return set()
    actual = {
        (option.decision, option.direction) for option in pair.admissible_decisions
    }
    assert len(actual) == len(pair.admissible_decisions)
    return actual


def _edge(
    *,
    fallback: bool = False,
    source: UUID,
    source_entity: str = "StandardsFrameworkItem",
    target: UUID,
) -> dict[str, Any]:
    """Build a synthetic hierarchy edge with independently distinguishable identity.

    Parameters
    ----------
    fallback
        Whether the framework attachment is unresolved.
    source
        Source CASE UUID.
    source_entity
        Framework or SFI entity type.
    target
        Target SFI CASE UUID.

    Returns
    -------
    dict[str, Any]
        Complete relationship input including the explicit fallback marker.
    """

    return {
        **{
            key: value
            for key, value in _COMMON.items()
            if key not in {"academic_subject", "in_language"}
        },
        "identifier": uuid5(
            name=f"selection-test:{source}:{target}", namespace=NAMESPACE_URL
        ),
        "metadata": {"unresolved_root_fallback": fallback},
        "relationship_type": "hasChild",
        "source_entity": source_entity,
        "source_entity_key": "case_identifier_uuid",
        "source_entity_value": str(source),
        "target_entity": "StandardsFrameworkItem",
        "target_entity_key": "case_identifier_uuid",
        "target_entity_value": str(target),
    }


def _filter(
    *, bundle: AcademicStandardsLCKGBundle, config: CreateKGConfig | None = None
) -> LPCandidateFilter:
    """Build the public filter with the synthetic document identity.

    Parameters
    ----------
    bundle
        Unfiltered upstream material.
    config
        Optional complete configuration; defaults to the explicit tree profile.

    Returns
    -------
    LPCandidateFilter
        Filter built through real graph indexing, coordinate resolution and selection.
    """

    return build_lp_pair_filter(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=_config() if config is None else config,
    )


def _fixture_bundle(profile: str) -> AcademicStandardsLCKGBundle:
    """Promote an approved reduced projection while retaining its structural evidence.

    Parameters
    ----------
    profile
        Reduced fixture and matching profile name.

    Returns
    -------
    AcademicStandardsLCKGBundle
        Full schema envelope with synthetic required attribution fields.
    """

    fixture = load_lp_regression_fixture(LP_FIXTURES_DIR / f"{profile}.json")
    edge_common = {
        key: value
        for key, value in _COMMON.items()
        if key not in {"academic_subject", "in_language"}
    }
    return _bundle(
        components=tuple(
            {**_COMMON, **component.model_dump(mode="json")}
            for component in fixture.learning_components
        ),
        edges=tuple(
            {**edge_common, **edge.model_dump(mode="json")}
            for edge in fixture.relationships_has_child
        ),
        framework_uuid=fixture.framework.case_identifier_uuid,
        items=tuple(
            _item(
                metadata=item.metadata,
                normalized_statement_type=item.normalized_statement_type,
                number=item.case_identifier_uuid.int,
                statement_type=item.statement_type,
            ).model_copy(
                update={
                    "description": item.description,
                    "statement_code": item.statement_code,
                }
            )
            for item in fixture.items
        ),
        supports=tuple(
            {**edge_common, **edge.model_dump(mode="json")}
            for edge in fixture.relationships_supports
        ),
    )


def _independent_upstream_hash(
    bundle: AcademicStandardsLCKGBundle,
) -> str:
    """Hash the authoritative bundle after semantic population ordering.

    Parameters
    ----------
    bundle
        Complete AS+LC bundle whose graph encounter order is not material.

    Returns
    -------
    str
        Independent upstream material content digest.
    """

    material = bundle.model_dump(mode="json")
    for field_name, identifier_name in (
        ("items", "case_identifier_uuid"),
        ("learning_components", "identifier"),
        ("relationships_has_child", "identifier"),
        ("relationships_supports", "identifier"),
    ):
        material[field_name] = sorted(
            material[field_name], key=itemgetter(identifier_name)
        )
    return _canonical_hash(material)


def _install_evidence_map(
    *,
    bundle: AcademicStandardsLCKGBundle,
    config: CreateKGConfig,
    evidence_by_endpoints: Mapping[frozenset[UUID], tuple[str, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> MagicMock:
    """Install a deterministic evidence seam around the real hard pair filter.

    Parameters
    ----------
    bundle
        Complete upstream graph used to derive real eligibility and permissions.
    config
        Validated curriculum policy and budgets.
    evidence_by_endpoints
        Exact built-in signal names for selected unordered endpoint pairs.
    monkeypatch
        Fixture that restores the approved evidence constructor after the test.

    Returns
    -------
    MagicMock
        Extractor spy whose call count exposes pair-population scale.
    """

    pair_filter = _filter(bundle=bundle, config=config)
    extractor = MagicMock()

    def _extract_pair(
        *, first_sfi_uuid: UUID | str, second_sfi_uuid: UUID | str
    ) -> LPPairEvidence | None:
        """Return real permission plus controlled evidence for one logical pair.

        Parameters
        ----------
        first_sfi_uuid
            One final SFI CASE UUID.
        second_sfi_uuid
            The other final SFI CASE UUID.

        Returns
        -------
        LPPairEvidence | None
            Real hard-filter outcome with only the mapped evidence signals.
        """

        pair = pair_filter.filter_pair(
            first_sfi_uuid=first_sfi_uuid, second_sfi_uuid=second_sfi_uuid
        )
        if pair is None:
            return None

        key = frozenset((pair.first_sfi_uuid, pair.second_sfi_uuid))
        return LPPairEvidence(
            admissibility=pair,
            evidence=_candidate_evidence(
                evidence_types=evidence_by_endpoints.get(key, ()), pair=pair
            ),
        )

    extractor.extract_pair.side_effect = _extract_pair
    monkeypatch.setattr(
        name="build_lp_evidence_extractor",
        target=lp_candidates,
        value=MagicMock(return_value=extractor),
    )
    return extractor


def _item(
    *,
    description: str = "A synthetic skill; display PRIMARY THREE / Grade 12.",
    metadata: dict[str, Any] | None = None,
    normalized_statement_type: str = "Standard",
    number: int = 1,
    statement_code: str = "PRIMARY THREE.99",
    statement_type: str = "Performance Objective",
) -> StandardsFrameworkItem:
    """Create an SFI whose CASE key differs from its other identifier.

    Parameters
    ----------
    description
        Authoritative SFI text used by the built-in lexical evidence rules.
    metadata
        Unfiltered metadata, defaulting to one valid coordinate.
    normalized_statement_type
        Exported normalized type, independent of the local statement type.
    number
        Deterministic CASE UUID suffix.
    statement_code
        Source-authoritative code used by the weak generic prefix feature.
    statement_type
        Local AS type whose LP permission must come from configuration.

    Returns
    -------
    StandardsFrameworkItem
        Authoritative node with deliberately misleading display grade and code.
    """

    case_uuid = UUID(int=number)
    return StandardsFrameworkItem.model_validate(
        {
            **_COMMON,
            "case_identifier_uri": f"urn:uuid:{case_uuid}",
            "case_identifier_uuid": case_uuid,
            "description": description,
            "grade_level": ["12"],
            "identifier": UUID(int=number + 2**112),
            "jurisdiction": "Synthetic jurisdiction",
            "metadata": deepcopy(
                metadata
                if metadata is not None
                else {"identity_scope_values": {"Grade": "PRIMARY ONE"}}
            ),
            "normalized_statement_type": normalized_statement_type,
            "statement_code": statement_code,
            "statement_type": statement_type,
        }
    )


def _match_summary_to_rows(
    *, rows: list[dict[str, Any]], summary: dict[str, Any]
) -> None:
    """Keep retained counts consistent so another defect cannot mask the target rule.

    Parameters
    ----------
    rows
        Deliberately corrupted candidate rows that still satisfy intrinsic schemas.
    summary
        Summary updated in place to agree with all retained-row counts.
    """

    incidence = Counter(
        endpoint
        for row in rows
        for endpoint in (row["first_sfi_uuid"], row["second_sfi_uuid"])
    )
    summary.update(
        candidate_pairs_per_sfi={
            endpoint: incidence[endpoint]
            for endpoint in summary["candidate_pairs_per_sfi"]
        },
        candidate_warning_counts=dict(
            Counter(warning for row in rows for warning in row["warnings"])
        ),
        evidence_type_counts=dict(
            Counter(
                evidence["evidence_type"]
                for row in rows
                for evidence in row["evidence"]
            )
        ),
        total_candidate_pairs=len(rows),
        total_candidate_pairs_with_warnings=sum(bool(row["warnings"]) for row in rows),
    )


def _observe_extractions(monkeypatch: pytest.MonkeyPatch) -> list[tuple[UUID, UUID]]:
    """Count actual evidence evaluations without replacing nomination or filtering.

    Parameters
    ----------
    monkeypatch
        Restoring observer around the production class method.

    Returns
    -------
    list[tuple[UUID, UUID]]
        Mutable log populated by subsequent production candidate builds.
    """

    evaluations: list[tuple[UUID, UUID]] = []
    original = lp_evidence.LPEvidenceExtractor.extract_pair

    def _extract_pair(
        self: lp_evidence.LPEvidenceExtractor,
        *,
        first_sfi_uuid: UUID | str,
        second_sfi_uuid: UUID | str,
    ) -> LPPairEvidence | None:
        """Observe one real extraction while preserving its complete behavior.

        Parameters
        ----------
        self
            Real run-scoped extractor.
        first_sfi_uuid
            First proposed CASE endpoint.
        second_sfi_uuid
            Second proposed CASE endpoint.

        Returns
        -------
        LPPairEvidence | None
            Unmodified real evidence result.
        """

        evaluations.append((UUID(str(first_sfi_uuid)), UUID(str(second_sfi_uuid))))
        return original(
            first_sfi_uuid=first_sfi_uuid,
            second_sfi_uuid=second_sfi_uuid,
            self=self,
        )

    monkeypatch.setattr(
        name="extract_pair", target=lp_evidence.LPEvidenceExtractor, value=_extract_pair
    )
    return evaluations


def _observe_nomination_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[tuple[UUID, UUID], ...]]:
    """Observe every real family window and enforce its configured proposal cap.

    Parameters
    ----------
    monkeypatch
        Restoring observer around the real bounded bucket stream.

    Returns
    -------
    list[tuple[tuple[UUID, UUID], ...]]
        Mutable log of per-family proposals from subsequent production builds.
    """

    original = lp_evidence._bounded_pairs_from_buckets
    contributions: list[tuple[tuple[UUID, UUID], ...]] = []

    def _nominate(**kwargs: Any) -> tuple[tuple[UUID, UUID], ...]:
        """Observe each unmodified family proposal population.

        Parameters
        ----------
        kwargs
            Real cohort buckets, budget, and fixed offset.

        Returns
        -------
        tuple[tuple[UUID, UUID], ...]
            Unmodified proposals from the production bucket stream.
        """

        pairs = original(**kwargs)
        assert len(pairs) <= kwargs["pair_limit"]
        contributions.append(pairs)
        return pairs

    monkeypatch.setattr(
        name="_bounded_pairs_from_buckets", target=lp_evidence, value=_nominate
    )
    return contributions


def _signal_bundle(
    *, count: int, endpoints: tuple[int, ...], signals: tuple[str, ...]
) -> AcademicStandardsLCKGBundle:
    """Supply selected real signals amid otherwise evidence-free eligible endpoints.

    Parameters
    ----------
    count
        Total number of eligible endpoints.
    endpoints
        UUID integers that share each requested signal.
    signals
        Evidence families represented by concrete upstream content.

    Returns
    -------
    AcademicStandardsLCKGBundle
        Validated synthetic graph without inherited lexical, rank, or page overlap.
    """

    items = [
        _item(description=chr(0x4E00 + n), metadata={}, number=n, statement_code="")
        for n in range(1, count + 1)
    ]
    edges: list[dict[str, Any]] = []
    if "hierarchy_context" in signals:
        items.append(
            _item(
                description="!",
                metadata={},
                normalized_statement_type="Standard Grouping",
                number=10000,
                statement_code="",
                statement_type="Topic",
            )
        )
        edges.extend(
            _edge(source=UUID(int=10000), target=UUID(int=n)) for n in endpoints
        )
    for n in endpoints:
        item = items[n - 1]
        if "sfi_text_token_overlap" in signals:
            item.description = "x"
        if "sfi_text_trigram_overlap" in signals:
            item.description = (
                f"abc{chr(0x4E00 + n)}"
                if "sfi_text_token_overlap" not in signals
                else "common text"
            )
        if "source_code_prefix" in signals:
            item.statement_code = f"A.{n}"
        if "local_rank_proximity" in signals:
            item.metadata["identity_scope_values"] = {"Grade": "PRIMARY ONE"}
        if "source_page_proximity" in signals:
            item.metadata["source_page_indexes"] = [1]
    components, supports = _signal_components(endpoints=endpoints, signals=signals)
    bundle = _bundle(
        components=tuple(components),
        edges=tuple(edges),
        items=tuple(items),
        supports=tuple(supports),
    )
    bundle.entity_provenance["items"] = {
        str(item.case_identifier_uuid): {} for item in items
    }
    return bundle


def _signal_components(
    *, endpoints: tuple[int, ...], signals: tuple[str, ...]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create exact-support or lexical component evidence with complete support rows.

    Parameters
    ----------
    endpoints
        UUID integers that share the requested component signals.
    signals
        Concrete component signal families to supply.

    Returns
    -------
    tuple[list[dict[str, Any]], list[dict[str, Any]]]
        Synthetic components and their support relationships.
    """

    components: list[dict[str, Any]] = []
    supports: list[dict[str, Any]] = []
    lc_signals = {
        "shared_learning_components",
        "lc_text_token_overlap",
        "lc_tag_token_overlap",
    }
    if lc_signals.intersection(signals):
        groups = (
            (endpoints,)
            if "shared_learning_components" in signals
            else tuple((n,) for n in endpoints)
        )
        for index, group in enumerate(groups):
            component_id = 20000 + index
            components.append(
                {
                    **_COMMON,
                    "description": (
                        "common component"
                        if "lc_text_token_overlap" in signals
                        else "!"
                    ),
                    "identifier": str(UUID(int=component_id)),
                    "metadata": {
                        "source_sfi_uuids": [str(UUID(int=n)) for n in group],
                        "tags": (
                            ["common tag"] if "lc_tag_token_overlap" in signals else []
                        ),
                    },
                }
            )
            for n in group:
                edge = _edge(
                    source=UUID(int=component_id),
                    source_entity="LearningComponent",
                    target=UUID(int=n),
                )
                edge.update(
                    relationship_type="supports", source_entity_key="identifier"
                )
                supports.append(edge)
    return components, supports


def _summary_corruptions(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build explicit, internally consistent counterexamples for summary reconciliation.

    Parameters
    ----------
    summary
        Valid five-row summary over ten endpoints with warning-bearing rows.

    Returns
    -------
    dict[str, dict[str, Any]]
        Named field updates that preserve intrinsic schemas but contradict material.
    """

    counts = summary["candidate_pairs_per_sfi"]
    low = min(counts, key=lambda key: counts[key])
    high = max(counts, key=lambda key: counts[key])
    assert counts[low] == 0 < counts[high]
    extra = str(UUID(int=999))
    warning = next(iter(summary["candidate_warning_counts"]))
    return {
        "candidate_hash": {"candidate_pairs_content_hash": "0" * 64},
        "config_hash": {"config_content_hash": "0" * 64},
        "eligible_hash": {"eligible_sfis_content_hash": "0" * 64},
        "eligible_population": {
            "candidate_pairs_per_sfi": {
                **{key: count for key, count in counts.items() if key != low},
                extra: 0,
            }
        },
        "eligible_total": {
            "candidate_pairs_per_sfi": {**counts, extra: 0},
            "total_eligible_sfis": 11,
            "total_unordered_pairs_considered": 55,
        },
        "evaluation_bound": {
            "candidate_pair_evaluation_bound": summary[
                "candidate_pair_evaluation_bound"
            ]
            - 1
        },
        "evidence_counts": {
            "evidence_type_counts": dict(
                list(summary["evidence_type_counts"].items())[1:]
            )
        },
        "framework": {"framework_uuid": extra},
        "incidence": {
            "candidate_pairs_per_sfi": {**counts, low: counts[high], high: counts[low]}
        },
        "max_per_sfi": {"max_candidates_per_sfi": 3},
        "max_total": {"candidate_pair_bound": 6, "max_total_candidates": 6},
        "summary_count": {
            "candidate_pairs_per_sfi": {key: 0 for key in counts},
            "candidate_warning_counts": {},
            "evidence_type_counts": {},
            "total_candidate_pairs": 0,
            "total_candidate_pairs_dropped_by_per_sfi_budget": summary[
                "total_candidate_pairs_dropped_by_per_sfi_budget"
            ]
            + 5,
            "total_candidate_pairs_with_warnings": 0,
        },
        "upstream_hash": {"upstream_content_hash": "0" * 64},
        "warning_counts": {
            "candidate_warning_counts": {
                **summary["candidate_warning_counts"],
                warning: summary["candidate_warning_counts"][warning] + 1,
            }
        },
        "warning_rows": {"total_candidate_pairs_with_warnings": 4},
    }


@PARAM(argnames="profile", argvalues=tuple(_PROFILES))
def test_aliases_preserve_permissions_and_authoritative_endpoint_records(
    profile: str,
) -> None:
    """Canonicalized type and coordinate aliases retain the unmodified source SFI.

    Parameters
    ----------
    profile
        Reviewed profile whose configured aliases supply equivalent inputs.
    """

    config = _config(profile)
    _, coordinate_type, order, allowed, _ = _PROFILES[profile]
    coordinate_policy = next(
        p
        for p in config.academic_standards.statement_type_policy
        if p.statement_type == coordinate_type
    )
    controlled = next(
        v for v in coordinate_policy.controlled_values if v.canonical_value == order[0]
    )
    for policy in config.academic_standards.statement_type_policy:
        if policy.statement_type not in allowed:
            continue
        for label in (policy.statement_type, *policy.aliases):
            first = _item(
                metadata={
                    "identity_scope_values": {
                        (coordinate_policy.aliases or [coordinate_type])[0]: (
                            controlled.aliases or [order[0]]
                        )[0]
                    }
                },
                statement_type=label,
            )
            second = _item(
                metadata={"identity_scope_values": {coordinate_type: order[0]}},
                number=2,
                statement_type=policy.statement_type,
            )
            result = _filter(
                bundle=_bundle(items=(first, second)), config=config
            ).filter_pair(
                first_sfi_uuid=first.case_identifier_uuid,
                second_sfi_uuid=second.case_identifier_uuid,
            )
            assert _decisions(result) == _NONPUBLISHING | {
                ("buildsTowards", "first_to_second"),
                ("buildsTowards", "second_to_first"),
                ("relatesTo", None),
            }
            assert result is not None
            assert result.first_sfi.statement_type == policy.statement_type
            assert result.first_sfi.sfi == first
            assert result.first_sfi.coordinate.rank == 0


@PARAM(argnames="existing", argvalues=(False, True))
@PARAM(
    argnames="defect",
    argvalues=(
        "candidate_hash",
        "config_hash",
        "decisions",
        "duplicate_row",
        "eligible_hash",
        "eligible_population",
        "eligible_total",
        "evaluation_bound",
        "evidence_counts",
        "framework",
        "incidence",
        "invented_row_warnings",
        "max_per_sfi",
        "max_total",
        "pair_id",
        "row_count",
        "row_order",
        "row_warnings",
        "summary_count",
        "upstream_hash",
        "warning_counts",
        "warning_rows",
    ),
)
def test_artifact_reconciliation_rejects_material_mismatch_before_any_mutation(
    *, defect: str, existing: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Internally valid but false candidate material cannot create or replace artifacts.

    Parameters
    ----------
    defect
        Independent material inconsistency to inject after candidate construction.
    existing
        Protect existing sentinel artifacts or an absent destination directory.
    monkeypatch
        Restoring population injection and real filesystem-call observers.
    tmp_path
        Isolated artifact destination.
    """

    config = _config_with_budgets(max_candidates_per_sfi=2, max_total_candidates=5)
    items = tuple(_item(number=n) for n in range(1, 11))
    bundle = _bundle(
        edges=tuple(
            _edge(
                fallback=True,
                source=_FRAMEWORK_UUID,
                source_entity="StandardsFramework",
                target=item.case_identifier_uuid,
            )
            for item in items
        ),
        items=items,
    )
    original = build_lp_candidates(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
    )
    false_population = _corrupt_population(defect=defect, original=original)
    monkeypatch.setattr(
        name="build_lp_candidates",
        target=lp_candidates,
        value=MagicMock(return_value=false_population),
    )
    mkdir = MagicMock(wraps=lp_candidates.make_dir)
    write = MagicMock(wraps=lp_candidates.write_to_json)
    monkeypatch.setattr(name="make_dir", target=lp_candidates, value=mkdir)
    monkeypatch.setattr(name="write_to_json", target=lp_candidates, value=write)
    root = tmp_path / "candidate-artifacts"
    sentinels = {
        "lp_candidate_pairs.jsonl": b"prior rows\n",
        "lp_candidate_summary.json": b"prior summary\n",
        "unrelated.txt": b"preserve user work\n",
    }
    if existing:
        root.mkdir()
        for name, payload in sentinels.items():
            (root / name).write_bytes(payload)
    with pytest.raises(expected_exception=ValueError):
        write_lp_candidate_artifacts(
            as_lc_bundle=bundle,
            doc_key=_DOC_KEY,
            kg_config=config,
            kg_dirs=KGDirs(root=root),
        )
    mkdir.assert_not_called()
    write.assert_not_called()
    if existing:
        assert {path.name: path.read_bytes() for path in root.iterdir()} == sentinels
    else:
        assert not root.exists()


def test_artifact_writer_rejects_summary_count_mismatch_before_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Candidate rows and summary counts must reconcile before either artifact changes.

    Parameters
    ----------
    monkeypatch
        Fixture that injects a structurally valid but materially false population.
    tmp_path
        Isolated candidate artifact destination.
    """

    config = _config_with_budgets(max_candidates_per_sfi=1, max_total_candidates=1)
    bundle = _bundle(items=(_item(number=1), _item(number=2)))
    population = build_lp_candidates(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
    )
    summary_payload = population.summary.model_dump(mode="json")
    summary_payload.update(
        {
            "candidate_pairs_per_sfi": {
                sfi_uuid: 0 for sfi_uuid in summary_payload["candidate_pairs_per_sfi"]
            },
            "evidence_type_counts": {},
            "total_candidate_pairs": 0,
            "total_candidate_pairs_dropped_by_per_sfi_budget": 1,
        }
    )
    false_summary = LPCandidateSummary.model_validate(summary_payload)
    assert len(population.candidates) == 1
    assert false_summary.total_candidate_pairs == 0
    monkeypatch.setattr(
        lp_candidates,
        "build_lp_candidates",
        MagicMock(
            return_value=LPCandidatePopulation(
                candidates=population.candidates, summary=false_summary
            )
        ),
    )
    kg_dirs = KGDirs(root=tmp_path / "candidate-artifacts")

    with pytest.raises(expected_exception=ValueError):
        write_lp_candidate_artifacts(
            as_lc_bundle=bundle,
            doc_key=_DOC_KEY,
            kg_config=config,
            kg_dirs=kg_dirs,
        )

    assert not kg_dirs.root.exists()
    assert not (kg_dirs.root / "lp_candidate_pairs.jsonl").exists()
    assert not (kg_dirs.root / "lp_candidate_summary.json").exists()


def test_artifacts_are_deterministic_and_hash_actual_candidate_rows(
    tmp_path: Path,
) -> None:
    """Input permutations produce byte-stable, round-trippable candidate artifacts.

    Parameters
    ----------
    tmp_path
        Isolated destinations for independently generated artifacts.
    """

    config = _config_with_budgets(max_candidates_per_sfi=10, max_total_candidates=10)
    items = tuple(_item(number=number) for number in range(1, 5))
    first_dirs = KGDirs(root=tmp_path / "first")
    second_dirs = KGDirs(root=tmp_path / "second")
    first = write_lp_candidate_artifacts(
        as_lc_bundle=_bundle(items=items),
        doc_key=_DOC_KEY,
        kg_config=config,
        kg_dirs=first_dirs,
    )
    second = write_lp_candidate_artifacts(
        as_lc_bundle=_bundle(items=tuple(reversed(items))),
        doc_key=_DOC_KEY,
        kg_config=config,
        kg_dirs=second_dirs,
    )

    for artifact_name in (
        "lp_candidate_pairs.jsonl",
        "lp_candidate_summary.json",
    ):
        assert (first_dirs.root / artifact_name).read_bytes() == (
            second_dirs.root / artifact_name
        ).read_bytes()

    rows = [
        json.loads(line)
        for line in (first_dirs.root / "lp_candidate_pairs.jsonl")
        .read_text()
        .splitlines()
    ]
    summary_payload = json.loads(
        (first_dirs.root / "lp_candidate_summary.json").read_text()
    )
    summary = LPCandidateSummary.model_validate(summary_payload)
    incidence = Counter(
        endpoint
        for row in rows
        for endpoint in (row["first_sfi_uuid"], row["second_sfi_uuid"])
    )

    assert first == second
    assert [row["pair_id"] for row in rows] == sorted(row["pair_id"] for row in rows)
    assert len(rows) == summary.total_candidate_pairs
    assert summary.candidate_pairs_content_hash == _canonical_hash(rows)
    assert summary.candidate_pairs_per_sfi == {
        UUID(sfi_uuid): incidence[sfi_uuid]
        for sfi_uuid in summary_payload["candidate_pairs_per_sfi"]
    }


@PARAM(argnames="eligible_count", argvalues=(10, 48))
def test_bounded_nomination_does_not_evaluate_the_complete_pair_matrix(
    *, eligible_count: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real evidence nomination stays bounded even when the matrix fits its ceiling.

    Parameters
    ----------
    eligible_count
        Moderate or large eligible population with identical evidence streams.
    monkeypatch
        Restoring observer around the real production extractor.
    """

    config = _config_with_budgets(max_candidates_per_sfi=2, max_total_candidates=5)
    bundle = _bundle(
        items=tuple(_item(number=number) for number in range(1, eligible_count + 1))
    )
    evaluations = _observe_extractions(monkeypatch)
    population = build_lp_candidates(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
    )
    complete_pair_matrix_size = eligible_count * (eligible_count - 1) // 2

    # Four identical signal streams use successive five-pair windows, producing
    # eight distinct proposals before evidence extraction and final incidence limits.
    assert len(evaluations) == len(set(evaluations)) == 8
    assert len(evaluations) < complete_pair_matrix_size
    assert population.summary.candidate_pair_evaluation_bound == 45
    assert population.summary.total_pair_evaluations == len(evaluations)
    assert population.summary.total_candidate_pairs == 5
    assert population.summary.total_policy_disallowed_pairs == 0
    assert max(population.summary.candidate_pairs_per_sfi.values()) == 2
    assert sum(population.summary.candidate_pairs_per_sfi.values()) == 10


@PARAM(
    argnames=("expected", "item_count", "total_budget"),
    argvalues=((0, 1, 1), (1, 2, 1), (3, 3, 3), (5, 4, 5)),
)
def test_budget_boundaries_cover_empty_one_limit_and_limit_plus_one(
    *, expected: int, item_count: int, total_budget: int
) -> None:
    """Empty, one-pair, exact-limit, and limit-plus-one populations are bounded.

    Parameters
    ----------
    expected
        Expected retained candidate count.
    item_count
        Eligible SFI population size.
    total_budget
        Configured run-wide candidate cap.
    """

    config = _config_with_budgets(
        max_candidates_per_sfi=max(1, item_count),
        max_total_candidates=total_budget,
    )
    population = build_lp_candidates(
        as_lc_bundle=_bundle(
            items=tuple(_item(number=number) for number in range(1, item_count + 1))
        ),
        doc_key=_DOC_KEY,
        kg_config=config,
    )
    possible = item_count * (item_count - 1) // 2

    assert len(population.candidates) == expected
    assert population.summary.candidate_pair_bound == min(possible, total_budget)
    assert population.summary.total_candidate_pairs == expected
    assert population.summary.total_candidate_pairs_dropped_by_total_budget == max(
        0, possible - total_budget
    )


@PARAM(argnames="budget", argvalues=(1, 3, 10))
def test_budgets_do_not_nominate_trim_or_accumulate_permission_results(
    budget: int,
) -> None:
    """Permission checks stay on demand regardless of nomination budget values.

    Parameters
    ----------
    budget
        Valid per-endpoint and total nomination cap.
    """

    payload = _config().model_dump(mode="json")
    payload["lp"]["candidate_policy"]["budgets"] = {
        "max_candidates_per_sfi": budget,
        "max_total_candidates": budget,
    }
    config = CreateKGConfig.model_validate(payload)
    bundle = _bundle(items=tuple(_item(number=n) for n in range(1, 6)))
    pair_filter = _filter(bundle=bundle, config=config)
    before = [
        (field.name, repr(getattr(pair_filter, field.name)))
        for field in fields(pair_filter)
    ]
    observed = set()
    for first, second in combinations(range(1, 6), 2):
        pair = pair_filter.filter_pair(
            first_sfi_uuid=UUID(int=first), second_sfi_uuid=UUID(int=second)
        )
        assert pair is not None
        observed.add(pair.pair_id)
    assert len(observed) == 10
    assert [
        (field.name, repr(getattr(pair_filter, field.name)))
        for field in fields(pair_filter)
    ] == before


def test_candidate_ranking_uses_fixed_precedence_and_pair_id_total_ties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ranking uses signal breadth, fixed precedence, then stable pair identity.

    Parameters
    ----------
    monkeypatch
        Fixture that installs controlled built-in signal combinations.
    """

    config = _config_with_budgets(max_candidates_per_sfi=10, max_total_candidates=10)
    items = tuple(_item(number=number) for number in range(1, 5))
    bundle = _bundle(items=items)
    evidence_by_endpoints = {
        frozenset((UUID(int=1), UUID(int=2))): (
            "local_rank_proximity",
            "sfi_text_token_overlap",
        ),
        frozenset((UUID(int=1), UUID(int=3))): ("shared_learning_components",),
        frozenset((UUID(int=1), UUID(int=4))): ("hierarchy_context",),
        frozenset((UUID(int=2), UUID(int=3))): ("local_rank_proximity",),
        frozenset((UUID(int=2), UUID(int=4))): ("local_rank_proximity",),
    }
    _install_evidence_map(
        bundle=bundle,
        config=config,
        evidence_by_endpoints=evidence_by_endpoints,
        monkeypatch=monkeypatch,
    )

    candidates = build_lp_candidates(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
    ).candidates
    pair_id_by_endpoints = {
        frozenset((candidate.first_sfi_uuid, candidate.second_sfi_uuid)): (
            candidate.pair_id
        )
        for candidate in candidates
    }
    tied_pair_ids = sorted(
        (
            pair_id_by_endpoints[frozenset((UUID(int=2), UUID(int=3)))],
            pair_id_by_endpoints[frozenset((UUID(int=2), UUID(int=4)))],
        )
    )
    expected_pair_ids = [
        pair_id_by_endpoints[frozenset((UUID(int=1), UUID(int=2)))],
        pair_id_by_endpoints[frozenset((UUID(int=1), UUID(int=3)))],
        pair_id_by_endpoints[frozenset((UUID(int=1), UUID(int=4)))],
        *tied_pair_ids,
    ]

    assert [candidate.pair_id for candidate in candidates] == expected_pair_ids


def test_candidate_summary_reconciles_union_evidence_and_incidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Endpoint shortlist union keeps all evidence once and enforces final degree.

    Parameters
    ----------
    monkeypatch
        Fixture that installs a three-edge controlled nomination union.
    """

    config = _config_with_budgets(max_candidates_per_sfi=2, max_total_candidates=10)
    bundle = _bundle(items=tuple(_item(number=number) for number in range(1, 5)))
    broad_pair = frozenset((UUID(int=1), UUID(int=2)))
    evidence_by_endpoints = {
        broad_pair: ("local_rank_proximity", "shared_learning_components"),
        frozenset((UUID(int=1), UUID(int=3))): ("local_rank_proximity",),
        frozenset((UUID(int=1), UUID(int=4))): ("local_rank_proximity",),
    }
    _install_evidence_map(
        bundle=bundle,
        config=config,
        evidence_by_endpoints=evidence_by_endpoints,
        monkeypatch=monkeypatch,
    )

    population = build_lp_candidates(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
    )
    summary = population.summary
    broad = next(
        candidate
        for candidate in population.candidates
        if frozenset((candidate.first_sfi_uuid, candidate.second_sfi_uuid))
        == broad_pair
    )
    incidence = Counter(
        endpoint
        for candidate in population.candidates
        for endpoint in (candidate.first_sfi_uuid, candidate.second_sfi_uuid)
    )
    evidence_counts = Counter(
        evidence.evidence_type
        for candidate in population.candidates
        for evidence in candidate.evidence
    )

    assert [item.evidence_type for item in broad.evidence] == [
        "local_rank_proximity",
        "shared_learning_components",
    ]
    assert len({candidate.pair_id for candidate in population.candidates}) == 2
    assert summary.candidate_pairs_per_sfi == {
        item.case_identifier_uuid: incidence[item.case_identifier_uuid]
        for item in bundle.items
    }
    assert summary.evidence_type_counts == dict(sorted(evidence_counts.items()))
    assert summary.total_candidate_pairs == 2
    assert summary.total_candidate_pairs_dropped_by_per_sfi_budget == 1
    assert summary.total_candidate_shortlist_entries == 5
    assert summary.total_candidate_union_pairs == 3
    assert summary.total_duplicate_shortlist_entries == 2


def test_content_hashes_identify_actual_material_without_policy_version() -> None:
    """Candidate summaries hash actual config, eligibility, upstream, and row content."""

    config = _config_with_budgets(max_candidates_per_sfi=5, max_total_candidates=5)
    items = (
        _item(description="Add fractions using models.", number=1),
        _item(description="Compare fractions using models.", number=2),
    )
    bundle = _bundle(items=items)
    selection = build_lp_selection(as_lc_bundle=bundle, kg_config=config)
    population = build_lp_candidates(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
    )
    candidate_payload = [
        candidate.model_dump(mode="json") for candidate in population.candidates
    ]
    eligible_payload = [
        record.model_dump(mode="json") for record in selection.eligible_sfis
    ]
    summary = population.summary

    assert summary.candidate_pairs_content_hash == _canonical_hash(candidate_payload)
    assert summary.config_content_hash == _canonical_hash(
        config.model_dump(mode="json")
    )
    assert summary.eligible_sfis_content_hash == _canonical_hash(eligible_payload)
    assert summary.upstream_content_hash == _independent_upstream_hash(bundle)

    changed_bundle = _bundle(
        items=(
            _item(description="Rotate triangles around a point.", number=1),
            items[1],
        )
    )
    changed = build_lp_candidates(
        as_lc_bundle=changed_bundle, doc_key=_DOC_KEY, kg_config=config
    ).summary

    assert changed.candidate_pairs_content_hash != summary.candidate_pairs_content_hash
    assert changed.eligible_sfis_content_hash != summary.eligible_sfis_content_hash
    assert changed.upstream_content_hash != summary.upstream_content_hash
    assert not any(
        "version" in name
        for name in LPCandidateSummary.model_json_schema()["properties"]
    )


def test_d1_d2_d4_permissions_survive_candidate_population_budgeting() -> None:
    """Population selection preserves type, coordinate, and unordered-pair rules."""

    config = _config_with_budgets(max_candidates_per_sfi=10, max_total_candidates=20)
    items = (
        _item(
            metadata={"identity_scope_values": {"Grade": "PRIMARY ONE"}},
            number=1,
        ),
        _item(
            metadata={"identity_scope_values": {"Grade": "PRIMARY THREE"}},
            number=2,
        ),
        _item(metadata={"identity_scope_values": {}}, number=3),
        _item(number=4, statement_type="Topic"),
        _item(
            metadata={"identity_scope_values": {"Grade": "PRIMARY ONE"}},
            number=5,
        ),
    )
    population = build_lp_candidates(
        as_lc_bundle=_bundle(items=items), doc_key=_DOC_KEY, kg_config=config
    )
    candidate_by_endpoints = {
        frozenset((candidate.first_sfi_uuid, candidate.second_sfi_uuid)): candidate
        for candidate in population.candidates
    }

    assert all(UUID(int=4) not in endpoints for endpoints in candidate_by_endpoints)
    assert len(candidate_by_endpoints) == len(population.candidates)
    assert {
        (decision.decision, decision.direction)
        for decision in candidate_by_endpoints[
            frozenset((UUID(int=1), UUID(int=2)))
        ].admissible_decisions
    } == _NONPUBLISHING | {
        ("buildsTowards", "first_to_second"),
        ("relatesTo", None),
    }
    assert {
        (decision.decision, decision.direction)
        for decision in candidate_by_endpoints[
            frozenset((UUID(int=1), UUID(int=3)))
        ].admissible_decisions
    } == _NONPUBLISHING | {("relatesTo", None)}
    assert {
        (decision.decision, decision.direction)
        for decision in candidate_by_endpoints[
            frozenset((UUID(int=1), UUID(int=5)))
        ].admissible_decisions
    } == _NONPUBLISHING | {
        ("buildsTowards", "first_to_second"),
        ("buildsTowards", "second_to_first"),
        ("relatesTo", None),
    }


def test_d3_d12_limitations_disclose_unmeasured_candidate_and_semantic_quality() -> (
    None
):
    """Candidate audit text acknowledges recall and semantic limits without metrics."""

    population = build_lp_candidates(
        as_lc_bundle=_bundle(items=(_item(number=1), _item(number=2))),
        doc_key=_DOC_KEY,
        kg_config=_config_with_budgets(
            max_candidates_per_sfi=1, max_total_candidates=1
        ),
    )
    disclosure = " ".join(population.summary.limitations).casefold()
    summary_fields = set(type(population.summary).model_fields)

    assert "candidate recall is unmeasured" in disclosure
    assert "non-embedding" in disclosure
    assert "do not establish pedagogical correctness" in disclosure
    assert not summary_fields.intersection(
        {
            "accepted_edge_precision",
            "candidate_recall_rate",
            "gold_set_version",
            "semantic_passed",
            "semantic_threshold",
        }
    )


@PARAM(argnames="missing", argvalues=(False, True))
@PARAM(argnames="policy", argvalues=_POLICIES)
def test_dag_unresolved_self_and_descendants_preserve_context_or_exclude(
    *, missing: bool, policy: str
) -> None:
    """An unresolved branch taints shared descendants without erasing either parent.

    Parameters
    ----------
    missing
        Whether the descendant also lacks its developmental coordinate.
    policy
        Explicit profile-wide inclusion or exclusion state.
    """

    payload = _config().model_dump(mode="json")
    payload["lp"]["unresolved_participation"] = policy
    config = CreateKGConfig.model_validate(payload)
    items = tuple(
        _item(metadata={} if missing and n == 3 else None, number=n)
        for n in range(1, 6)
    )
    fallback = _edge(
        fallback=True,
        source=_FRAMEWORK_UUID,
        source_entity="StandardsFramework",
        target=UUID(int=1),
    )
    bundle = _bundle(
        edges=(
            fallback,
            _edge(source=UUID(int=1), target=UUID(int=3)),
            _edge(source=UUID(int=2), target=UUID(int=3)),
            _edge(source=UUID(int=3), target=UUID(int=4)),
        ),
        items=items,
    )
    pair_filter = _filter(bundle=bundle, config=config)
    for number in (1, 3, 4):
        pair = pair_filter.filter_pair(
            first_sfi_uuid=UUID(int=number), second_sfi_uuid=UUID(int=5)
        )
        if policy == "exclude_unresolved":
            assert pair is None
            continue
        assert pair is not None
        record = pair.first_sfi
        assert record.unresolved_ancestry is True
        expected_warning = f"SFI {UUID(int=number)} has unresolved self or ancestry. Framework-root fallback must not be used as positive hierarchy, topology, domain, or placement evidence."
        assert expected_warning in record.warnings
        assert expected_warning in pair.warnings
        assert (
            record.source_provenance
            == bundle.entity_provenance["items"][str(UUID(int=number))]
        )
        assert record.sfi == items[number - 1]
        if number == 1:
            assert record.parent_sfi_uuids == ()
            assert record.root_fallback_relationship_uuids == (
                UUID(str(fallback["identifier"])),
            )
        elif number == 3:
            assert record.parent_sfi_uuids == (UUID(int=1), UUID(int=2))
            assert record.root_fallback_relationship_uuids == ()
        else:
            assert record.parent_sfi_uuids == (UUID(int=3),)
        expected = _NONPUBLISHING | {("relatesTo", None)}
        if not (missing and number == 3):
            expected |= {
                ("buildsTowards", "first_to_second"),
                ("buildsTowards", "second_to_first"),
            }
        assert _decisions(pair) == expected
    assert (
        pair_filter.filter_pair(first_sfi_uuid=UUID(int=2), second_sfi_uuid=UUID(int=5))
        is not None
    )


@PARAM(argnames="doc_key", argvalues=("", " \t\n", None, 17))
def test_document_identity_rejects_blank_or_nonstring_keys(doc_key: Any) -> None:
    """A document-less pair cannot receive a reusable identity or filter.

    Parameters
    ----------
    doc_key
        Invalid identity supplied to both public entry points.
    """

    with pytest.raises(expected_exception=ValueError, match="doc_key"):
        build_lp_pair_id(
            doc_key=doc_key, first_sfi_uuid=UUID(int=1), second_sfi_uuid=UUID(int=2)
        )
    with pytest.raises(expected_exception=ValueError, match="doc_key"):
        build_lp_pair_filter(
            as_lc_bundle=_bundle(items=(_item(), _item(number=2))),
            doc_key=doc_key,
            kg_config=_config(),
        )


@PARAM(
    argnames="change",
    argvalues=(
        "caller",
        "metadata_missing",
        "metadata_other",
        "provenance_other",
        "provenance_not_mapping",
    ),
)
def test_document_identity_rejects_conflicting_or_missing_authority(
    change: str,
) -> None:
    """Run identity cannot silently disagree with authoritative framework material.

    Parameters
    ----------
    change
        Independent document boundary or malformed provenance to inject.
    """

    bundle = _bundle(items=(_item(), _item(number=2)))
    if change == "metadata_missing":
        bundle.framework.metadata.clear()
    elif change == "metadata_other":
        bundle.framework.metadata["doc_key"] = "other-document"
    elif change == "provenance_other":
        bundle.entity_provenance["framework"]["doc_key"] = "other-document"
    elif change == "provenance_not_mapping":
        bundle.entity_provenance["framework"] = []
    with pytest.raises(expected_exception=ValueError, match="doc_key|provenance"):
        build_lp_pair_filter(
            as_lc_bundle=bundle,
            doc_key="other-document" if change == "caller" else _DOC_KEY,
            kg_config=_config(),
        )


@PARAM(argnames="omit_provenance_key", argvalues=(False, True))
def test_document_key_whitespace_and_optional_provenance_agree(
    omit_provenance_key: bool,
) -> None:
    """Whitespace cleanup preserves the document key and optional provenance contract.

    Parameters
    ----------
    omit_provenance_key
        Whether the optional provenance copy of the document key is absent.
    """

    bundle = _bundle(items=(_item(), _item(number=2)))
    if omit_provenance_key:
        bundle.entity_provenance["framework"].pop("doc_key")
    pair_filter = build_lp_pair_filter(
        as_lc_bundle=bundle, doc_key=f"  {_DOC_KEY}\n", kg_config=_config()
    )
    pair = pair_filter.filter_pair(
        first_sfi_uuid=UUID(int=1), second_sfi_uuid=UUID(int=2)
    )
    assert pair is not None
    assert pair.pair_id == "4e319395-a183-5315-9838-072975f2aa22"


def test_empty_upstream_population_cannot_supply_endpoints() -> None:
    """A valid empty population stays empty and rejects invented endpoint keys."""

    pair_filter = _filter(bundle=_bundle(items=()))
    with pytest.raises(expected_exception=ValueError, match="Unknown SFI"):
        pair_filter.filter_pair(first_sfi_uuid=UUID(int=1), second_sfi_uuid=UUID(int=2))


@PARAM(argnames="bad_first", argvalues=(False, True))
@PARAM(argnames="bad_uuid", argvalues=("not-a-uuid", "", None, 9))
def test_endpoint_uuid_syntax_is_rejected_at_both_public_boundaries(
    *, bad_first: bool, bad_uuid: Any
) -> None:
    """Malformed endpoints must fail even before pair-policy consideration.

    Parameters
    ----------
    bad_first
        Whether the malformed value is encountered first.
    bad_uuid
        Invalid endpoint representation.
    """

    endpoints = {
        "first_sfi_uuid": bad_uuid if bad_first else UUID(int=1),
        "second_sfi_uuid": UUID(int=2) if bad_first else bad_uuid,
    }
    with pytest.raises(expected_exception=ValueError):
        build_lp_pair_id(doc_key=_DOC_KEY, **endpoints)
    pair_filter = _filter(bundle=_bundle(items=(_item(), _item(number=2))))
    with pytest.raises(expected_exception=ValueError):
        pair_filter.filter_pair(**endpoints)


@PARAM(argnames="profile", argvalues=tuple(_PROFILES))
def test_exact_six_profile_rank_matrix_includes_same_missing_and_all_forward_gaps(
    profile: str,
) -> None:
    """The entire rank product follows local order regardless of UUID and display order.

    Parameters
    ----------
    profile
        Pinned coordinate order, including the scope-only Class profile.
    """

    _, coordinate_type, order, allowed, _ = _PROFILES[profile]
    coordinates = (*order, None)
    items = tuple(
        _item(
            metadata=(
                {}
                if value is None
                else {"identity_scope_values": {coordinate_type: value}}
            ),
            number=number + 1,
            statement_type=allowed[0],
        )
        for number, value in enumerate(
            value for value in reversed(coordinates) for _ in range(2)
        )
    )
    rank_by_uuid = {
        item.case_identifier_uuid: rank
        for item, rank in zip(
            items,
            (rank for rank in reversed((*range(len(order)), None)) for _ in range(2)),
            strict=True,
        )
    }
    pair_filter = _filter(bundle=_bundle(items=items), config=_config(profile))
    for first, second in combinations(items, 2):
        expected = _NONPUBLISHING | {("relatesTo", None)}
        first_rank, second_rank = (
            rank_by_uuid[first.case_identifier_uuid],
            rank_by_uuid[second.case_identifier_uuid],
        )
        if first_rank is not None and second_rank is not None:
            if first_rank <= second_rank:
                expected.add(("buildsTowards", "first_to_second"))
            if second_rank <= first_rank:
                expected.add(("buildsTowards", "second_to_first"))
        pair = pair_filter.filter_pair(
            first_sfi_uuid=first.case_identifier_uuid,
            second_sfi_uuid=second.case_identifier_uuid,
        )
        assert _decisions(pair) == expected, (profile, first_rank, second_rank)
        assert pair is not None
        assert pair.first_sfi.coordinate.rank == first_rank
        assert pair.second_sfi.coordinate.rank == second_rank
        assert (
            pair_filter.filter_pair(
                first_sfi_uuid=second.case_identifier_uuid,
                second_sfi_uuid=first.case_identifier_uuid,
            )
            == pair
        )
        for record, rank in (
            (pair.first_sfi, first_rank),
            (pair.second_sfi, second_rank),
        ):
            if rank is None:
                assert record.exclusion_reasons == {
                    "buildsTowards": "missing_coordinate"
                }
                assert record.coordinate.source_fields == ()
                assert any(
                    "Missing rank is not positive evidence" in warning
                    for warning in record.warnings
                )


@PARAM(argnames="profile", argvalues=tuple(_PROFILES))
def test_exact_six_profile_type_matrices_do_not_expand_participating_type_sets(
    profile: str,
) -> None:
    """Every configured grain permits only its own exact pair; groupings stay excluded.

    Parameters
    ----------
    profile
        Complete reviewed type matrix to test independently of normalized grain.
    """

    _, coordinate_type, order, allowed, excluded = _PROFILES[profile]
    types = (*allowed, *excluded)
    items = tuple(
        _item(
            metadata={"identity_scope_values": {coordinate_type: order[0]}},
            number=number + 1,
            statement_type=statement_type,
        )
        for number, statement_type in enumerate(
            kind for kind in types for _ in range(2)
        )
    )
    pair_filter = _filter(bundle=_bundle(items=items), config=_config(profile))
    ids = set()
    for first, second in combinations(items, 2):
        result = pair_filter.filter_pair(
            first_sfi_uuid=first.case_identifier_uuid,
            second_sfi_uuid=second.case_identifier_uuid,
        )
        permitted = (
            first.statement_type == second.statement_type
            and first.statement_type in allowed
        )
        if not permitted:
            assert result is None, (
                profile,
                first.statement_type,
                second.statement_type,
            )
            continue
        assert result is not None
        assert _decisions(result) == _NONPUBLISHING | {
            ("buildsTowards", "first_to_second"),
            ("buildsTowards", "second_to_first"),
            ("relatesTo", None),
        }
        ids.add(result.pair_id)
    assert len(ids) == len(allowed)


@PARAM(argnames="profile", argvalues=tuple(_PROFILES))
def test_fixture_permutations_retain_id_decisions_and_complete_endpoint_provenance(
    profile: str,
) -> None:
    """Reduced graphs plus a synthetic same-grain neighbor are invariant to input order.

    Parameters
    ----------
    profile
        Approved reduced fixture promoted without changing its structural evidence.
    """

    bundle = _fixture_bundle(profile)
    seed = next(
        item for item in bundle.items if item.statement_type == _PROFILES[profile][3][0]
    )
    neighbor = _item(
        metadata=seed.metadata, number=999, statement_type=seed.statement_type
    )
    bundle = _bundle(
        components=tuple(
            component.model_dump(mode="json")
            for component in bundle.learning_components
        ),
        edges=tuple(
            edge.model_dump(mode="json") for edge in bundle.relationships_has_child
        ),
        framework_uuid=bundle.framework.case_identifier_uuid,
        items=(*bundle.items, neighbor),
        supports=tuple(
            edge.model_dump(mode="json") for edge in bundle.relationships_supports
        ),
    )
    config = _config(profile)
    original = bundle.model_dump(mode="json")
    reversed_bundle = bundle.model_copy(deep=True)
    for field in (
        "items",
        "learning_components",
        "relationships_has_child",
        "relationships_supports",
    ):
        getattr(reversed_bundle, field).reverse()
    reversed_bundle.entity_provenance = dict(
        reversed(list(reversed_bundle.entity_provenance.items()))
    )
    config_payload = config.model_dump(mode="json")
    config_payload["as"]["statement_type_policy"].reverse()
    for relation in ("builds_towards", "relates_to"):
        config_payload["lp"][relation]["allowed_statement_type_pairs"].reverse()
    first_filter = _filter(bundle=bundle, config=config)
    second_filter = _filter(
        bundle=reversed_bundle, config=CreateKGConfig.model_validate(config_payload)
    )
    by_uuid = {item.case_identifier_uuid: item for item in bundle.items}
    identifiers = set()
    for first, second in combinations(sorted(by_uuid, key=str), 2):
        pair = first_filter.filter_pair(first_sfi_uuid=first, second_sfi_uuid=second)
        reordered = second_filter.filter_pair(
            first_sfi_uuid=second, second_sfi_uuid=first
        )
        assert pair == reordered
        if pair is None:
            continue
        identifiers.add(pair.pair_id)
        for record in (pair.first_sfi, pair.second_sfi):
            assert record.sfi == by_uuid[record.sfi.case_identifier_uuid]
            assert (
                record.source_provenance
                == original["entity_provenance"]["items"][
                    str(record.sfi.case_identifier_uuid)
                ]
            )
        assert pair.warnings == tuple(
            sorted(set(pair.first_sfi.warnings + pair.second_sfi.warnings))
        )
    assert identifiers, profile
    assert bundle.model_dump(mode="json") == original


def test_future_unconfigured_standard_type_cannot_enter_pairs() -> None:
    """Adding an AS Standard grain never silently expands the LP matrices."""

    payload = _config().model_dump(mode="json")
    payload["as"]["statement_type_policy"].append(
        {
            "aliases": [],
            "description": "Synthetic future standard grain",
            "normalized_statement_type": "Standard",
            "statement_type": "Future Learning Target",
        }
    )
    payload["as"]["sfi_has_child_parent_policy"]["Future Learning Target"] = []
    bundle = _bundle(
        items=(
            _item(),
            _item(number=2, statement_type="Future Learning Target"),
            _item(number=3, statement_type="Future Learning Target"),
        )
    )
    pair_filter = _filter(bundle=bundle, config=CreateKGConfig.model_validate(payload))
    for first, second in ((1, 2), (2, 3)):
        assert (
            pair_filter.filter_pair(
                first_sfi_uuid=UUID(int=first), second_sfi_uuid=UUID(int=second)
            )
            is None
        )


@PARAM(
    argnames="metadata",
    argvalues=(
        {"identity_scope_values": {"Grade": "UNLISTED"}},
        {"identity_scope_values": {"Grade": ["PRIMARY ONE", "PRIMARY TWO"]}},
        {"identity_scope_values": {"Grade": "PRIMARY ONE", "Class": "PRIMARY TWO"}},
    ),
)
@PARAM(argnames="omitted", argvalues=(False, True))
@PARAM(argnames="policy", argvalues=_POLICIES)
def test_invalid_coordinate_anywhere_prevents_filter_construction(
    *, metadata: dict[str, Any], omitted: bool, policy: str
) -> None:
    """Even unqueried and policy-excluded SFIs must pass complete coordinate validation.

    Parameters
    ----------
    metadata
        Invalid, ambiguous or conflicting coordinate evidence.
    omitted
        Whether the invalid item has an unconfigured LP grain.
    policy
        Unresolved participation state, including exclusion before missing-rank rules.
    """

    payload = _config().model_dump(mode="json")
    payload["lp"]["unresolved_participation"] = policy
    bundle = _bundle(
        edges=(
            _edge(
                fallback=True,
                source=_FRAMEWORK_UUID,
                source_entity="StandardsFramework",
                target=UUID(int=3),
            ),
        ),
        items=(
            _item(),
            _item(number=2),
            _item(
                metadata=metadata,
                number=3,
                statement_type="Topic" if omitted else "Performance Objective",
            ),
        ),
    )
    with pytest.raises(expected_exception=ValueError, match="coordinate"):
        _filter(bundle=bundle, config=CreateKGConfig.model_validate(payload))


@PARAM(argnames="mixed", argvalues=(False, True))
def test_large_alias_population_uses_canonical_nomination_cohorts(
    *, mixed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configured aliases preserve bounded nomination for canonical type cohorts.

    Parameters
    ----------
    mixed
        Mix canonical and aliased labels or use aliases on every endpoint.
    monkeypatch
        Real extraction observer.
    """

    config = _config_with_budgets(max_candidates_per_sfi=2, max_total_candidates=5)
    policy = next(
        p
        for p in config.academic_standards.statement_type_policy
        if p.statement_type == "Performance Objective"
    )
    assert policy.aliases
    canonical = _bundle(items=tuple(_item(number=n) for n in range(1, 65)))
    aliased = canonical.model_copy(deep=True)
    for index, item in enumerate(aliased.items):
        if not mixed or index % 2 == 0:
            item.statement_type = policy.aliases[0]
    evaluations = _observe_extractions(monkeypatch)
    expected = build_lp_candidates(
        as_lc_bundle=canonical, doc_key=_DOC_KEY, kg_config=config
    )
    canonical_evaluations = tuple(evaluations)
    evaluations.clear()
    actual = build_lp_candidates(
        as_lc_bundle=aliased, doc_key=_DOC_KEY, kg_config=config
    )

    assert actual.candidates == expected.candidates
    assert tuple(evaluations) == canonical_evaluations
    assert len(evaluations) == 8
    assert actual.summary.total_eligible_sfis == 64
    assert actual.summary.total_candidate_pairs == 5
    assert actual.summary.total_policy_disallowed_pairs == 0
    assert (
        actual.summary.upstream_content_hash != expected.summary.upstream_content_hash
    )
    assert (
        actual.summary.eligible_sfis_content_hash
        != expected.summary.eligible_sfis_content_hash
    )


@PARAM(argnames="interleaved", argvalues=(False, True))
def test_large_mixed_types_never_evaluate_excluded_cross_type_pairs(
    *, interleaved: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closed-world cohorts nominate both grains without consuming excluded pairs.

    Parameters
    ----------
    interleaved
        Alternate grains by UUID or place each grain in a contiguous block.
    monkeypatch
        Real extraction observer.
    """

    config = _config_with_budgets(
        max_candidates_per_sfi=2, max_total_candidates=5, profile="ghana_math"
    )
    bundle = _bundle(
        items=tuple(
            _item(
                metadata={"identity_scope_values": {"Grade": "BASIC 4"}},
                number=n,
                statement_type=(
                    "Content Standard"
                    if (n % 2 == 0 if interleaved else n <= 64)
                    else "Indicator"
                ),
            )
            for n in range(1, 129)
        )
    )
    types = {item.case_identifier_uuid: item.statement_type for item in bundle.items}
    evaluations = _observe_extractions(monkeypatch)
    first = build_lp_candidates(as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config)
    observed = tuple(evaluations)
    assert observed and len(observed) == len(set(observed)) <= 45
    assert all(types[left] == types[right] for left, right in observed)
    assert {types[left] for left, _ in observed} == {"Content Standard", "Indicator"}
    assert {types[row.first_sfi_uuid] for row in first.candidates} == {
        "Content Standard",
        "Indicator",
    }
    assert first.summary.total_policy_disallowed_pairs == 0
    assert first.summary.total_candidate_pairs == 5
    assert first.summary.total_pair_evaluations == len(observed)
    assert first.summary.candidate_pair_evaluation_bound == 45
    assert max(first.summary.candidate_pairs_per_sfi.values()) <= 2

    bundle.items.reverse()
    bundle.relationships_has_child.reverse()
    evaluations.clear()
    second = build_lp_candidates(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
    )
    assert second == first
    assert tuple(evaluations) == observed


def test_module_boundary_uses_top_level_canonical_imports() -> None:
    """Pair services have one owner and no local-import or re-export workaround."""

    dependencies = {
        "lp_admissibility": {"lp_coordinates", "lp_selection"},
        "lp_candidates": {"lp_admissibility", "lp_evidence", "lp_selection"},
        "lp_evidence": {"lp_admissibility", "lp_index", "lp_selection"},
    }
    for module in (lp_admissibility, lp_candidates, lp_evidence):
        assert module.__file__ is not None
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        assert all(node in tree.body for node in imports)
        assert {
            node.module.rsplit(".", 1)[-1]
            for node in imports
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("kgfeg.kgs.lp_")
        } == dependencies[module.__name__.rsplit(".", 1)[-1]]
        assert "__all__" not in vars(module)

    assert not hasattr(lp_candidates, "LPPairAdmissibility")
    candidate_imports = ast.parse(
        Path(lp_candidates.__file__).read_text(encoding="utf-8")
    )
    assert all(
        alias.asname != alias.name
        for node in ast.walk(candidate_imports)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    )


@PARAM(
    argnames="module_order",
    argvalues=tuple(permutations(("lp_admissibility", "lp_evidence", "lp_candidates"))),
)
def test_modules_import_independently_in_every_order(
    module_order: tuple[str, ...],
) -> None:
    """Fresh interpreters import every boundary order with one canonical service owner.

    Parameters
    ----------
    module_order
        One permutation of admissibility, evidence, and candidate modules.
    """

    script = dedent(
        """
        import importlib
        import sys

        def _reject_network(event, args):
            if event.startswith(("socket.connect", "socket.getaddrinfo")):
                raise AssertionError("Importing LP modules must not access the network.")

        sys.addaudithook(_reject_network)
        sys.path.insert(0, sys.argv[1])
        order = sys.argv[2:]
        for name in order:
            importlib.import_module("kgfeg.kgs." + name)
            if name == order[0] and name == "lp_admissibility":
                assert "kgfeg.kgs.lp_evidence" not in sys.modules
                assert "kgfeg.kgs.lp_candidates" not in sys.modules
            if name == order[0] and name == "lp_evidence":
                assert "kgfeg.kgs.lp_candidates" not in sys.modules

        admissibility = sys.modules["kgfeg.kgs.lp_admissibility"]
        candidates = sys.modules["kgfeg.kgs.lp_candidates"]
        evidence = sys.modules["kgfeg.kgs.lp_evidence"]
        for name in (
            "LPCandidateFilter", "LPPairAdmissibility",
            "build_lp_pair_filter", "build_lp_pair_id",
        ):
            assert getattr(admissibility, name).__module__ == admissibility.__name__
        assert evidence.LPCandidateFilter is admissibility.LPCandidateFilter
        assert evidence.LPPairAdmissibility is admissibility.LPPairAdmissibility
        assert evidence.build_lp_pair_filter is admissibility.build_lp_pair_filter
        assert candidates.build_lp_pair_filter is admissibility.build_lp_pair_filter
        assert candidates.build_lp_pair_id is admissibility.build_lp_pair_id
        assert candidates.LPEvidenceExtractor is evidence.LPEvidenceExtractor
        assert candidates.build_lp_evidence_extractor is evidence.build_lp_evidence_extractor
        assert not hasattr(candidates, "LPPairAdmissibility")
        assert not hasattr(candidates, "__all__")
        assert admissibility.build_lp_pair_id(
            doc_key="synthetic-selection-document",
            first_sfi_uuid="00000000-0000-0000-0000-000000000001",
            second_sfi_uuid="00000000-0000-0000-0000-000000000002",
        ) == "4e319395-a183-5315-9838-072975f2aa22"
        """
    )
    completed = subprocess.run(
        args=[
            sys.executable,
            "-B",
            "-I",
            "-c",
            script,
            str(PACKAGE_PATH / "backend" / "src"),
            *module_order,
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_no_file_access_or_pair_enumeration_occurs_while_building_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pure on-demand construction never loads stale artifacts or enumerates pairs.

    Parameters
    ----------
    monkeypatch
        Restoring guards for file access and pair assessment.
    """

    bundle = _bundle(items=tuple(_item(number=n) for n in range(1, 12)))
    config = _config()

    def _reject(*args: Any, **kwargs: Any) -> None:
        """Reject an unexpected file operation or pair enumeration.

        Parameters
        ----------
        args
            Unexpected positional call arguments.
        kwargs
            Unexpected named call arguments.

        Raises
        ------
        AssertionError
            Any call violates the pure construction contract.
        """

        raise AssertionError(
            "Pair permission construction must not perform I/O or enumerate pairs."
        )

    with monkeypatch.context() as guard:
        guard.setattr(name="open", target=builtins, value=_reject)
        guard.setattr(name="open", target=Path, value=_reject)
        guard.setattr(name="mkdir", target=Path, value=_reject)
        guard.setattr(name="filter_pair", target=LPCandidateFilter, value=_reject)
        pair_filter = _filter(bundle=bundle, config=config)
    with monkeypatch.context() as guard:
        guard.setattr(name="open", target=builtins, value=_reject)
        guard.setattr(name="open", target=Path, value=_reject)
        pair = pair_filter.filter_pair(
            first_sfi_uuid=UUID(int=1), second_sfi_uuid=UUID(int=2)
        )
    assert pair is not None
    assert {field.name for field in fields(pair)} == {
        "admissible_decisions",
        "first_sfi",
        "pair_id",
        "second_sfi",
        "warnings",
    }


@PARAM(
    argnames="doc_key,expected,second",
    argvalues=(
        (_DOC_KEY, "4e319395-a183-5315-9838-072975f2aa22", 2),
        (_DOC_KEY, "89f42ea3-5a07-5d9b-b22c-3018f54ee764", 3),
        ("other-document", "5a66e75d-d1c5-5ea0-bcc0-3280d9bf1bf3", 2),
    ),
)
def test_pair_ids_match_independently_pinned_uuidv5_vectors(
    *, doc_key: str, expected: str, second: int
) -> None:
    """Document and CASE endpoints, not encounter order, determine canonical pair UUIDs.

    Parameters
    ----------
    doc_key
        Synthetic source document identity.
    expected
        Independently calculated fixed namespace-and-name UUIDv5 vector.
    second
        Endpoint varied to prove endpoint-sensitive identity.
    """

    assert (
        build_lp_pair_id(
            doc_key=doc_key,
            first_sfi_uuid=UUID(int=1),
            second_sfi_uuid=UUID(int=second),
        )
        == expected
    )
    assert (
        build_lp_pair_id(
            doc_key=doc_key,
            first_sfi_uuid=UUID(int=second),
            second_sfi_uuid=UUID(int=1),
        )
        == expected
    )
    assert UUID(expected).version == 5
    bundle = _bundle(items=(_item(), _item(number=second)))
    bundle.framework.metadata["doc_key"] = doc_key
    bundle.entity_provenance["framework"]["doc_key"] = doc_key
    pair = build_lp_pair_filter(
        as_lc_bundle=bundle, doc_key=doc_key, kg_config=_config()
    ).filter_pair(first_sfi_uuid=UUID(int=second), second_sfi_uuid=UUID(int=1))
    assert pair is not None
    assert pair.pair_id == expected
    assert (pair.first_sfi_uuid, pair.second_sfi_uuid) == (
        UUID(int=1),
        UUID(int=second),
    )
    assert pair.first_sfi_uuid != pair.first_sfi.sfi.identifier


def test_policy_surface_contains_budgets_without_ranking_or_version_selectors() -> None:
    """Runtime and candidate artifacts expose no selectable ranking or policy version."""

    config = _config()
    candidate_policy_fields = set(
        type(config.learning_progressions.candidate_policy).model_fields
    )
    budget_fields = set(
        type(config.learning_progressions.candidate_policy.budgets).model_fields
    )
    candidate_fields = set(LPCandidateSummary.model_fields)

    assert candidate_policy_fields == {"budgets"}
    assert budget_fields == {"max_candidates_per_sfi", "max_total_candidates"}
    assert not candidate_fields.intersection(
        {
            "algorithm_version",
            "candidate_policy_version",
            "enabled_signals",
            "ranking",
            "ranking_inputs",
            "strategies",
            "technology",
            "tie_breaking",
        }
    )


@PARAM(argnames="reverse_matrix", argvalues=(False, True))
def test_relates_to_exact_unordered_permission_is_independent_of_builds_towards(
    reverse_matrix: bool,
) -> None:
    """An unordered conceptual matrix admits either encounter order but no extra pairs.

    Parameters
    ----------
    reverse_matrix
        Stored orientation of the same synthetic unordered permission.
    """

    payload = _config("pratham_science").model_dump(mode="json")
    first, second = ("NCERT Learning Outcome", "Indicator")
    payload["lp"]["builds_towards"]["allowed_statement_type_pairs"] = [
        {
            "source_statement_type": "Content Domain Specific Learning Outcome",
            "target_statement_type": "Content Domain Specific Learning Outcome",
        }
    ]
    payload["lp"]["relates_to"]["allowed_statement_type_pairs"] = [
        {
            "first_statement_type": second if reverse_matrix else first,
            "second_statement_type": first if reverse_matrix else second,
        }
    ]
    items = tuple(
        _item(metadata={}, number=n, statement_type=kind)
        for n, kind in ((1, first), (2, second), (3, first), (4, second))
    )
    pair_filter = _filter(
        bundle=_bundle(items=items), config=CreateKGConfig.model_validate(payload)
    )
    expected = _NONPUBLISHING | {("relatesTo", None)}
    for one, two in ((1, 2), (2, 1), (3, 4)):
        assert (
            _decisions(
                pair_filter.filter_pair(
                    first_sfi_uuid=UUID(int=one), second_sfi_uuid=UUID(int=two)
                )
            )
            == expected
        )
    for one, two in ((1, 3), (2, 4)):
        assert (
            pair_filter.filter_pair(
                first_sfi_uuid=UUID(int=one), second_sfi_uuid=UUID(int=two)
            )
            is None
        )


@PARAM(argnames="first_rank,second_rank", argvalues=((0, 0), (0, 1), (1, 0)))
@PARAM(argnames="reverse_type_permission", argvalues=(False, True))
def test_relation_specific_directional_type_permission_intersects_coordinate_direction(
    *, first_rank: int, reverse_type_permission: bool, second_rank: int
) -> None:
    """Directional permissions are neither symmetrized nor replaced by endpoint sets.

    Parameters
    ----------
    first_rank
        Rank of the first canonical endpoint.
    reverse_type_permission
        Whether the synthetic directed matrix permits only the reverse type order.
    second_rank
        Rank of the second canonical endpoint.
    """

    payload = _config("pratham_science").model_dump(mode="json")
    source, target = (
        ("Indicator", "NCERT Learning Outcome")
        if reverse_type_permission
        else ("NCERT Learning Outcome", "Indicator")
    )
    payload["lp"]["builds_towards"]["allowed_statement_type_pairs"] = [
        {"source_statement_type": source, "target_statement_type": target}
    ]
    payload["lp"]["relates_to"]["allowed_statement_type_pairs"] = [
        {
            "first_statement_type": "Content Domain Specific Learning Outcome",
            "second_statement_type": "Content Domain Specific Learning Outcome",
        }
    ]
    items = tuple(
        _item(
            metadata={
                "identity_scope_values": {"Class": ("Class IX", "Class X")[rank]}
            },
            number=n,
            statement_type=kind,
        )
        for n, kind, rank in (
            (1, "NCERT Learning Outcome", first_rank),
            (2, "Indicator", second_rank),
            (3, "NCERT Learning Outcome", first_rank),
            (4, "Indicator", second_rank),
            (5, "Content Domain Specific Learning Outcome", 0),
            (6, "Content Domain Specific Learning Outcome", 1),
        )
    )
    pair_filter = _filter(
        bundle=_bundle(items=items), config=CreateKGConfig.model_validate(payload)
    )
    expected = set()
    if (
        (second_rank <= first_rank)
        if reverse_type_permission
        else (first_rank <= second_rank)
    ):
        expected = _NONPUBLISHING | {
            (
                "buildsTowards",
                "second_to_first" if reverse_type_permission else "first_to_second",
            )
        }
    assert (
        _decisions(
            pair_filter.filter_pair(
                first_sfi_uuid=UUID(int=1), second_sfi_uuid=UUID(int=2)
            )
        )
        == expected
    )
    for first, second in ((1, 3), (2, 4), (1, 5), (2, 5)):
        assert (
            pair_filter.filter_pair(
                first_sfi_uuid=UUID(int=first), second_sfi_uuid=UUID(int=second)
            )
            is None
        )
    assert _decisions(
        pair_filter.filter_pair(first_sfi_uuid=UUID(int=5), second_sfi_uuid=UUID(int=6))
    ) == _NONPUBLISHING | {("relatesTo", None)}


@PARAM(argnames="change", argvalues=("coordinate", "matrix", "order", "unresolved"))
def test_repeated_construction_recomputes_changed_material_permissions(
    change: str,
) -> None:
    """A new filter observes material changes instead of trusting an earlier snapshot.

    Parameters
    ----------
    change
        Authoritative input or permission change between constructions.
    """

    payload = _config().model_dump(mode="json")
    bundle = _bundle(
        edges=(
            _edge(
                fallback=True,
                source=_FRAMEWORK_UUID,
                source_entity="StandardsFramework",
                target=UUID(int=1),
            ),
        ),
        items=(
            _item(),
            _item(
                metadata={"identity_scope_values": {"Grade": "PRIMARY THREE"}}, number=2
            ),
        ),
    )
    old_filter = _filter(bundle=bundle, config=CreateKGConfig.model_validate(payload))
    old = old_filter.filter_pair(
        first_sfi_uuid=UUID(int=1), second_sfi_uuid=UUID(int=2)
    )
    assert _decisions(old) == _NONPUBLISHING | {
        ("buildsTowards", "first_to_second"),
        ("relatesTo", None),
    }
    if change == "coordinate":
        bundle.items[0].metadata.clear()
    elif change == "matrix":
        payload["lp"]["builds_towards"]["allowed_statement_type_pairs"] = [
            {"source_statement_type": "Topic", "target_statement_type": "Topic"}
        ]
    elif change == "order":
        payload["lp"]["developmental_coordinate"]["ordered_values"].reverse()
    else:
        payload["lp"]["unresolved_participation"] = "exclude_unresolved"
    new = _filter(
        bundle=bundle, config=CreateKGConfig.model_validate(payload)
    ).filter_pair(first_sfi_uuid=UUID(int=1), second_sfi_uuid=UUID(int=2))
    expected = (
        set() if change == "unresolved" else _NONPUBLISHING | {("relatesTo", None)}
    )
    if change == "order":
        expected.add(("buildsTowards", "second_to_first"))
    assert _decisions(new) == expected
    if new is not None:
        assert old is not None
        assert new.pair_id == old.pair_id
    assert (
        old_filter.filter_pair(first_sfi_uuid=UUID(int=1), second_sfi_uuid=UUID(int=2))
        == old
    )


@PARAM(argnames="endpoint", argvalues=("first_sfi", "second_sfi"))
def test_result_mutation_is_isolated_from_other_results_and_future_calls(
    endpoint: str,
) -> None:
    """Nested endpoint and outcome mutations never leak back into the filter snapshot.

    Parameters
    ----------
    endpoint
        Endpoint result whose nested mutable records are corrupted by a consumer.
    """

    bundle = _bundle(items=(_item(), _item(number=2), _item(number=3)))
    original = bundle.model_dump(mode="json")
    pair_filter = _filter(bundle=bundle)
    pair = pair_filter.filter_pair(
        first_sfi_uuid=UUID(int=1), second_sfi_uuid=UUID(int=2)
    )
    sibling = pair_filter.filter_pair(
        first_sfi_uuid=UUID(int=1), second_sfi_uuid=UUID(int=3)
    )
    assert pair is not None and sibling is not None
    saved = deepcopy(pair)
    sibling_saved = deepcopy(sibling)
    record = getattr(pair, endpoint)
    record.eligibility_reasons.clear()
    record.exclusion_reasons.clear()
    record.source_provenance["source_pages"].append(999)
    record.source_provenance["audit"]["flags"].clear()
    record.sfi.metadata["identity_scope_values"]["Grade"] = "UNLISTED"
    record.sfi.case_identifier_uuid = UUID(int=999)
    pair.admissible_decisions[0].decision = "no_relation"
    assert sibling == sibling_saved
    assert (
        pair_filter.filter_pair(first_sfi_uuid=UUID(int=2), second_sfi_uuid=UUID(int=1))
        == saved
    )
    assert bundle.model_dump(mode="json") == original


@PARAM(
    argnames="same", argvalues=("known", "excluded", "unknown", "alternate_spelling")
)
def test_self_pairs_are_never_permissions_or_identities(same: str) -> None:
    """Self-pair rejection survives exclusion and equivalent UUID spellings.

    Parameters
    ----------
    same
        Membership or representation variant for the repeated endpoint.
    """

    bundle = _bundle(items=(_item(number=10), _item(number=11, statement_type="Topic")))
    pair_filter = _filter(bundle=bundle)
    endpoint = UUID(int=11 if same == "excluded" else 12 if same == "unknown" else 10)
    second = endpoint.hex.upper() if same == "alternate_spelling" else endpoint
    with pytest.raises(expected_exception=ValueError, match="self-pair"):
        build_lp_pair_id(
            doc_key=_DOC_KEY, first_sfi_uuid=endpoint, second_sfi_uuid=second
        )
    if same == "unknown":
        with pytest.raises(expected_exception=ValueError, match="Unknown SFI"):
            pair_filter.filter_pair(first_sfi_uuid=endpoint, second_sfi_uuid=second)
    else:
        assert (
            pair_filter.filter_pair(first_sfi_uuid=endpoint, second_sfi_uuid=second)
            is None
        )


def test_snapshot_isolates_nested_bundle_and_configuration_mutations() -> None:
    """Once built, a filter owns its coordinate, type-policy and provenance snapshot."""

    bundle = _bundle(items=(_item(), _item(number=2)))
    config = _config()
    initial_bundle = bundle.model_dump(mode="json")
    initial_config = config.model_dump(mode="json")
    pair_filter = _filter(bundle=bundle, config=config)
    saved = pair_filter.filter_pair(
        first_sfi_uuid=UUID(int=1), second_sfi_uuid=UUID(int=2)
    )
    assert saved is not None
    assert bundle.model_dump(mode="json") == initial_bundle
    assert config.model_dump(mode="json") == initial_config
    bundle.items[0].metadata["identity_scope_values"]["Grade"] = "UNLISTED"
    bundle.entity_provenance["items"][str(UUID(int=1))]["audit"]["flags"].clear()
    bundle.items.clear()
    bundle.framework.metadata["doc_key"] = "changed-document"
    config.learning_progressions.builds_towards.allowed_statement_type_pairs.clear()
    config.learning_progressions.relates_to.allowed_statement_type_pairs.clear()
    config.learning_progressions.developmental_coordinate.ordered_values.reverse()
    config.learning_progressions.unresolved_participation = "exclude_unresolved"
    assert (
        pair_filter.filter_pair(first_sfi_uuid=UUID(int=1), second_sfi_uuid=UUID(int=2))
        == saved
    )


@PARAM(argnames="endpoint_count", argvalues=(2, 3))
@PARAM(
    argnames="signal",
    argvalues=(
        "hierarchy_context",
        "lc_tag_token_overlap",
        "lc_text_token_overlap",
        "local_rank_proximity",
        "sfi_text_token_overlap",
        "sfi_text_trigram_overlap",
        "shared_learning_components",
        "source_code_prefix",
        "source_page_proximity",
    ),
)
def test_sparse_nonadjacent_signal_streams_survive_offsets(
    *, endpoint_count: int, monkeypatch: pytest.MonkeyPatch, signal: str
) -> None:
    """Each real evidence family retains its only sparse, nonadjacent proposals.

    Parameters
    ----------
    endpoint_count
        Two or three distant endpoints sharing the sole signal.
    monkeypatch
        Real extraction observer.
    signal
        Fixed family supplied by concrete upstream content.
    """

    endpoints = (1, 31, 64)[:endpoint_count]
    bundle = _signal_bundle(count=64, endpoints=endpoints, signals=(signal,))
    evaluations = _observe_extractions(monkeypatch)
    population = build_lp_candidates(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=_config_with_budgets(
            max_candidates_per_sfi=2, max_total_candidates=5
        ),
    )
    expected = {
        (UUID(int=left), UUID(int=right)) for left, right in combinations(endpoints, 2)
    }

    assert set(evaluations) == expected
    assert len(evaluations) == len(expected)
    assert {
        (row.first_sfi_uuid, row.second_sfi_uuid) for row in population.candidates
    } == expected
    assert population.summary.total_pair_evaluations == len(expected)
    assert population.summary.evidence_type_counts == {signal: len(expected)}
    assert all(
        {entry.evidence_type for entry in row.evidence} == {signal}
        for row in population.candidates
    )


@PARAM(argnames="item_count", argvalues=(3, 4, 6))
def test_tight_population_diversifies_overlapping_evidence_windows(
    *, item_count: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Overlapping bounded families contribute enough distinct pairs at tight limits.

    Parameters
    ----------
    item_count
        Small boundary population whose evidence families share the same bucket.
    monkeypatch
        Real family-output and extraction observers.
    """

    contributions = _observe_nomination_windows(monkeypatch)
    evaluations = _observe_extractions(monkeypatch)
    population = build_lp_candidates(
        as_lc_bundle=_bundle(
            items=tuple(_item(number=n) for n in range(1, item_count + 1))
        ),
        doc_key=_DOC_KEY,
        kg_config=_config_with_budgets(
            max_candidates_per_sfi=2, max_total_candidates=5
        ),
    )
    union = {pair for contribution in contributions for pair in contribution}
    expected_retained = {3: 3, 4: 4, 6: 5}[item_count]

    assert len(contributions) == 9
    assert len(evaluations) == len(union)
    assert set(evaluations) == union
    assert len(union) > max(map(len, contributions)) or item_count == 3
    assert len(population.candidates) == expected_retained
    assert population.summary.candidate_pair_bound == min(5, item_count)
    assert max(population.summary.candidate_pairs_per_sfi.values()) <= 2


def test_ubiquitous_evidence_families_respect_each_window_and_union_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ubiquitous signals diversify a bounded union without quadratic extraction.

    Parameters
    ----------
    monkeypatch
        Real extraction observer.
    """

    signals = (
        "shared_learning_components",
        "hierarchy_context",
        "lc_text_token_overlap",
        "lc_tag_token_overlap",
        "sfi_text_token_overlap",
        "sfi_text_trigram_overlap",
        "source_code_prefix",
        "local_rank_proximity",
        "source_page_proximity",
    )
    bundle = _signal_bundle(count=64, endpoints=tuple(range(1, 65)), signals=signals)
    contributions = _observe_nomination_windows(monkeypatch)
    evaluations = _observe_extractions(monkeypatch)
    config = _config_with_budgets(max_candidates_per_sfi=2, max_total_candidates=5)
    population = build_lp_candidates(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
    )

    assert [len(pairs) for pairs in contributions] == [5] * 9
    assert len(evaluations) == len(set(evaluations)) == 13
    assert population.summary.total_pair_evaluations == 13
    assert population.summary.candidate_pair_evaluation_bound == 45
    assert population.summary.total_unordered_pairs_considered == 2016
    assert len(population.candidates) == 5
    assert max(population.summary.candidate_pairs_per_sfi.values()) <= 2
    assert population.summary.evidence_type_counts == {signal: 5 for signal in signals}
    bundle.items.reverse()
    bundle.relationships_has_child.reverse()
    bundle.learning_components.reverse()
    bundle.relationships_supports.reverse()
    evaluations.clear()
    assert (
        build_lp_candidates(as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config)
        == population
    )
    assert len(evaluations) == 13


@PARAM(argnames="bad_first", argvalues=(False, True))
@PARAM(
    argnames="kind",
    argvalues=("foreign_sfi", "framework", "learning_component", "non_case_identifier"),
)
def test_unknown_and_non_sfi_endpoints_fail_containment(
    *, bad_first: bool, kind: str
) -> None:
    """Only this bundle's final SFI CASE keys may enter pair consideration.

    Parameters
    ----------
    bad_first
        Whether the invalid endpoint is encountered first.
    kind
        Foreign SFI or valid non-endpoint entity identifier to try.
    """

    bundle = _fixture_bundle("rwanda_math")
    valid = bundle.items[0].case_identifier_uuid
    foreign = {
        "foreign_sfi": UUID(int=999),
        "framework": bundle.framework.case_identifier_uuid,
        "learning_component": bundle.learning_components[0].identifier,
        "non_case_identifier": bundle.items[0].identifier,
    }[kind]
    assert foreign not in {item.case_identifier_uuid for item in bundle.items}
    pair_filter = _filter(bundle=bundle, config=_config("rwanda_math"))
    with pytest.raises(expected_exception=ValueError, match="Unknown SFI"):
        pair_filter.filter_pair(
            first_sfi_uuid=foreign if bad_first else valid,
            second_sfi_uuid=valid if bad_first else foreign,
        )


def test_unresolved_warning_state_is_visible_in_candidate_summary() -> None:
    """A summary must retain visible unresolved-warning state for nominated pairs."""

    unresolved = _item(number=1)
    resolved = _item(number=2)
    bundle = _bundle(
        edges=(
            _edge(
                fallback=True,
                source=_FRAMEWORK_UUID,
                source_entity="StandardsFramework",
                target=unresolved.case_identifier_uuid,
            ),
        ),
        items=(unresolved, resolved),
    )
    population = build_lp_candidates(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=_config_with_budgets(
            max_candidates_per_sfi=1, max_total_candidates=1
        ),
    )
    assert len(population.candidates) == 1
    assert any("unresolved" in warning for warning in population.candidates[0].warnings)

    warnings = population.candidates[0].warnings
    assert population.summary.candidate_warning_counts == dict(Counter(warnings))
    assert population.summary.total_candidate_pairs_with_warnings == 1
    assert not set(warnings).intersection(population.summary.evidence_type_counts)
    assert all(
        warning not in entry.model_dump_json()
        for warning in warnings
        for entry in population.candidates[0].evidence
    )


def test_unresolved_warnings_survive_candidate_artifact_round_trip(
    tmp_path: Path,
) -> None:
    """Candidate JSONL preserves the exact warning union from both endpoints.

    Parameters
    ----------
    tmp_path
        Isolated candidate artifact destination.
    """

    unresolved = _item(number=1)
    resolved = _item(number=2)
    bundle = _bundle(
        edges=(
            _edge(
                fallback=True,
                source=_FRAMEWORK_UUID,
                source_entity="StandardsFramework",
                target=unresolved.case_identifier_uuid,
            ),
        ),
        items=(unresolved, resolved),
    )
    kg_dirs = KGDirs(root=tmp_path / "candidate-artifacts")
    population = write_lp_candidate_artifacts(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=_config_with_budgets(
            max_candidates_per_sfi=1, max_total_candidates=1
        ),
        kg_dirs=kg_dirs,
    )
    row = json.loads((kg_dirs.root / "lp_candidate_pairs.jsonl").read_text().strip())

    assert row["warnings"] == population.candidates[0].warnings
    assert any("Framework-root fallback" in warning for warning in row["warnings"])
    summary = json.loads((kg_dirs.root / "lp_candidate_summary.json").read_text())
    assert summary["candidate_warning_counts"] == dict(Counter(row["warnings"]))
    assert summary["total_candidate_pairs_with_warnings"] == 1
    assert summary["evidence_type_counts"] == dict(
        Counter(entry["evidence_type"] for entry in row["evidence"])
    )


@PARAM(
    argnames="defect",
    argvalues=(
        "failed_report",
        "lying_report",
        "foreign_framework_edge",
        "dangling_edge",
        "duplicate_case_uuid",
        "cycle",
    ),
)
def test_upstream_integrity_failures_prevent_any_pair_filter(defect: str) -> None:
    """A passed flag alone cannot authorize ambiguous or foreign graph material.

    Parameters
    ----------
    defect
        Invalid upstream graph or validation report deliberately supplied to construction.
    """

    bundle = _bundle(items=(_item(), _item(number=2)))
    if defect == "failed_report":
        bundle.validation_report.passed = False
    elif defect == "lying_report":
        bundle.validation_report.errors.append("Synthetic unresolved validation error")
    elif defect == "foreign_framework_edge":
        bundle.relationships_has_child[0].source_entity_value = str(UUID(int=999))
    elif defect == "dangling_edge":
        bundle.relationships_has_child[0].target_entity_value = str(UUID(int=999))
    elif defect == "duplicate_case_uuid":
        bundle.items.append(bundle.items[0].model_copy(deep=True))
    else:
        bundle = _bundle(
            edges=(
                _edge(source=UUID(int=1), target=UUID(int=2)),
                _edge(source=UUID(int=2), target=UUID(int=1)),
            ),
            items=(_item(), _item(number=2)),
        )
    with pytest.raises(expected_exception=ValueError):
        _filter(bundle=bundle)


@PARAM(
    argnames="spelling",
    argvalues=("uuid", "canonical", "uppercase", "hex", "braces", "urn"),
)
def test_uuid_spellings_and_reversed_encounters_share_one_pair(spelling: str) -> None:
    """UUID representation changes neither canonical endpoint order nor pair identity.

    Parameters
    ----------
    spelling
        Valid UUID representation used for both endpoint encounters.
    """

    first, second = UUID(int=0xAABC), UUID(int=0xDEF0)
    bundle = _bundle(items=(_item(number=first.int), _item(number=second.int)))
    pair_filter = _filter(bundle=bundle)
    original = pair_filter.filter_pair(first_sfi_uuid=first, second_sfi_uuid=second)
    representations = {
        "uuid": (first, second),
        "canonical": (str(first), str(second)),
        "uppercase": (str(first).upper(), str(second).upper()),
        "hex": (first.hex, second.hex),
        "braces": ("{" + str(first) + "}", "{" + str(second) + "}"),
        "urn": (first.urn, second.urn),
    }
    one, two = representations[spelling]
    pair = pair_filter.filter_pair(first_sfi_uuid=two, second_sfi_uuid=one)
    assert pair == original
    assert pair is not None
    assert (
        build_lp_pair_id(doc_key=_DOC_KEY, first_sfi_uuid=two, second_sfi_uuid=one)
        == pair.pair_id
    )


def test_warning_only_population_never_nominates_from_audit_text() -> None:
    """Shared unresolved warnings and framework fallback never become positive evidence."""

    items = tuple(
        _item(description=chr(0x4E00 + n), metadata={}, number=n, statement_code="")
        for n in range(1, 49)
    )
    bundle = _bundle(
        edges=tuple(
            _edge(
                fallback=True,
                source=_FRAMEWORK_UUID,
                source_entity="StandardsFramework",
                target=item.case_identifier_uuid,
            )
            for item in items
        ),
        items=items,
    )
    config = _config_with_budgets(max_candidates_per_sfi=2, max_total_candidates=5)
    selection = build_lp_selection(as_lc_bundle=bundle, kg_config=config)
    assert len(selection.eligible_sfis) == 48
    assert all(record.warnings for record in selection.eligible_sfis)
    population = build_lp_candidates(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
    )
    assert not population.candidates
    assert population.summary.total_pair_evaluations == 0
    assert population.summary.evidence_type_counts == {}
    assert population.summary.candidate_warning_counts == {}
    assert population.summary.total_candidate_pairs_with_warnings == 0
