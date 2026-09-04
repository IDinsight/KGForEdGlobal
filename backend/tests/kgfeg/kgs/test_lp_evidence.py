"""Red-team on-demand LP evidence without treating nomination as semantic truth."""

# Future Library
from __future__ import annotations

# Standard Library
import builtins
import json
import socket

from copy import deepcopy
from dataclasses import fields
from itertools import combinations
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

# Third Party Library
import pytest

# Package Library
from kgfeg.kgs import lp_evidence
from kgfeg.kgs.lp_candidates import LPCandidateFilter
from kgfeg.kgs.lp_evidence import (
    LPEvidenceExtractor,
    LPPairEvidence,
    build_lp_evidence_extractor,
)
from kgfeg.kgs.schemas import (
    AcademicStandardsLCKGBundle,
    LPCandidateEvidence,
    StandardsFrameworkItem,
)
from kgfeg.schemas import CreateKGConfig
from tests.constants import PACKAGE_PATH, PARAM
from tests.fixtures.lp.loader import LP_FIXTURES_DIR, load_lp_regression_fixture

_DOC_KEY = "synthetic-selection-document"


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
    """Reject external connections during every evidence test.

    Parameters
    ----------
    monkeypatch
        Restoring network guards.
    """

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
                        **deepcopy(item.metadata),
                        "uninterpreted": {"source_windows": ["window-99"]},
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


def _component(
    *, description: str = "!!!", number: int, sfis: tuple[int, ...], tags: Any = None
) -> dict[str, Any]:
    """Build a synthetic LC with explicit support provenance.

    Parameters
    ----------
    description
        Authoritative component text.
    number
        Unique LC UUID integer.
    sfis
        Supporting endpoint UUID integers.
    tags
        Optional metadata deliberately left unvalidated.

    Returns
    -------
    dict[str, Any]
        Complete LC payload.
    """

    return {
        **_COMMON,
        "description": description,
        "identifier": str(UUID(int=number)),
        "metadata": {
            "source_sfi_uuids": [str(UUID(int=n)) for n in sfis],
            "tags": tags,
        },
    }


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


def _extractor(
    *, bundle: AcademicStandardsLCKGBundle, config: CreateKGConfig | None = None
) -> LPEvidenceExtractor:
    """Build the public extractor through real validation and selection.

    Parameters
    ----------
    bundle
        Unfiltered upstream bundle.
    config
        Optional profile; defaults to the simple tree profile.

    Returns
    -------
    LPEvidenceExtractor
        Run-scoped evaluator.
    """

    return build_lp_evidence_extractor(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=_config() if config is None else config,
    )


def _features(result: LPPairEvidence) -> dict[str, LPCandidateEvidence]:
    """Index observed features while enforcing explainability and intrinsic shape.

    Parameters
    ----------
    result
        Public extraction result.

    Returns
    -------
    dict[str, LPCandidateEvidence]
        Unique, alphabetically ordered named evidence records.
    """

    names = [feature.evidence_type for feature in result.evidence]
    assert names == sorted(set(names))
    for feature in result.evidence:
        assert feature.triggering_values
        assert feature.references == sorted(set(feature.references))
        assert (
            LPCandidateEvidence.model_validate_json(feature.model_dump_json())
            == feature
        )
    return {feature.evidence_type: feature for feature in result.evidence}


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


def _item(
    *,
    metadata: dict[str, Any] | None = None,
    normalized_statement_type: str = "Standard",
    number: int = 1,
    statement_type: str = "Performance Objective",
) -> StandardsFrameworkItem:
    """Create an SFI whose CASE key differs from its other identifier.

    Parameters
    ----------
    metadata
        Unfiltered metadata, defaulting to no optional evidence.
    normalized_statement_type
        Exported normalized type, independent of the local statement type.
    number
        Deterministic CASE UUID suffix.
    statement_type
        Local AS type whose LP permission must come from configuration.

    Returns
    -------
    StandardsFrameworkItem
        Authoritative node with a CASE key distinct from its other identifier.
    """

    case_uuid = UUID(int=number)
    return StandardsFrameworkItem.model_validate(
        {
            **_COMMON,
            "case_identifier_uri": f"urn:uuid:{case_uuid}",
            "case_identifier_uuid": case_uuid,
            "description": "!!!",
            "grade_level": ["12"],
            "identifier": UUID(int=number + 2**112),
            "jurisdiction": "Synthetic jurisdiction",
            "metadata": deepcopy(metadata if metadata is not None else {}),
            "normalized_statement_type": normalized_statement_type,
            "statement_code": None,
            "statement_type": statement_type,
        }
    )


def _pair(
    *, extractor: LPEvidenceExtractor, first: int = 1, second: int = 2
) -> LPPairEvidence:
    """Query one expected-admissible synthetic pair without deriving its evidence.

    Parameters
    ----------
    extractor
        Public evaluator under test.
    first
        Encountered first CASE UUID integer.
    second
        Encountered second CASE UUID integer.

    Returns
    -------
    LPPairEvidence
        Required non-null result.
    """

    result = extractor.extract_pair(
        first_sfi_uuid=UUID(int=first), second_sfi_uuid=UUID(int=second)
    )
    assert result is not None
    return result


def _reject(*args: Any, **kwargs: Any) -> None:
    """Reject a forbidden side effect or speculative population operation.

    Parameters
    ----------
    args
        Unexpected positional arguments.
    kwargs
        Unexpected keyword arguments.

    Raises
    ------
    AssertionError
        Every call violates the isolated evidence contract.
    """

    raise AssertionError("Evidence must remain local, on demand and nonpublishing.")


def _support(*, component: int, sfi: int) -> dict[str, Any]:
    """Create a complete LC support relationship with independent identity.

    Parameters
    ----------
    component
        Source LC UUID integer.
    sfi
        Target SFI CASE UUID integer.

    Returns
    -------
    dict[str, Any]
        Schema-valid support payload.
    """

    edge = _edge(
        source=UUID(int=component),
        source_entity="LearningComponent",
        target=UUID(int=sfi),
    )
    edge.update(relationship_type="supports", source_entity_key="identifier")
    return edge


@PARAM(
    argnames="common,first,second",
    argvalues=(
        (["a", "1"], "A.1.2", "a-1_3"),
        (["a"], "Ａ.2", "a/3"),
        (["é"], "É.1", "é.2"),
        ([], "A.1", "B.1"),
        ([], "A1", "A10"),
        ([], None, "A.1"),
        ([], "---", "___"),
    ),
)
def test_code_prefix_uses_original_leading_segments(
    *, common: list[str], first: str | None, second: str
) -> None:
    """Codes provide only weak leading-segment evidence, never a coordinate.

    Parameters
    ----------
    common
        Independently specified common leading segments.
    first
        First original source code.
    second
        Second original source code.
    """

    bundle = _bundle(items=(_item(), _item(number=2)))
    bundle.items[0].statement_code = first
    bundle.items[1].statement_code = second
    for item in bundle.items:
        item.metadata["normalized_statement_code"] = "fabricated.shared.code"
        item.alternate_statement_code = "fabricated.shared.code"
    result = _pair(extractor=_extractor(bundle=bundle))
    actual = _features(result)
    assert set(actual) == ({"source_code_prefix"} if common else set())
    if common:
        assert actual["source_code_prefix"].triggering_values == {
            "common_segments": common,
            "first_code": first,
            "second_code": second,
            "strength": "weak",
        }
        assert actual["source_code_prefix"].nominated_relationships == ["relatesTo"]
    assert result.admissibility.first_sfi.coordinate.rank is None


@PARAM(argnames="location", argvalues=("metadata", "provenance", "both"))
def test_code_warnings_remain_available_and_cannot_certify_progression(
    location: str,
) -> None:
    """Conflicting-code and merge audits survive with raw codes and weak evidence.

    Parameters
    ----------
    location
        Source location carrying the known code and merge audits.
    """

    bundle = _bundle(items=(_item(), _item(number=2)))
    audit = {
        "audit_flags": ["same_code_different_content", "source_merge_conflict"],
        "audit_notes": ["Source-visible descriptions differ despite the same code."],
    }
    for item in bundle.items:
        item.statement_code = "B5.1.2.1.1"
        if location in ("metadata", "both"):
            item.metadata.update(deepcopy(audit))
        if location in ("provenance", "both"):
            bundle.entity_provenance["items"][str(item.case_identifier_uuid)].update(
                deepcopy(audit)
            )
    result = _pair(extractor=_extractor(bundle=bundle))
    assert set(_features(result)) == {"source_code_prefix"}
    assert result.evidence[0].triggering_values["strength"] == "weak"
    for original, retained in zip(
        bundle.items, (result.admissibility.first_sfi, result.admissibility.second_sfi)
    ):
        assert retained.sfi == original
        assert (
            retained.source_provenance
            == bundle.entity_provenance["items"][str(original.case_identifier_uuid)]
        )
    assert {field.name for field in fields(result)} == {"admissibility", "evidence"}
    assert {
        decision.decision for decision in result.admissibility.admissible_decisions
    } == {"relatesTo", "no_relation", "needs_review"}


def test_combined_signals_keep_identity_permissions_and_independent_values() -> None:
    """Simultaneous evidence neither loses reasons nor selects a semantic outcome."""

    bundle = _bundle(
        components=(
            _component(description="odd even", number=20, sfis=(1, 2), tags=["parity"]),
        ),
        edges=(
            _edge(source=UUID(int=3), target=UUID(int=1)),
            _edge(source=UUID(int=3), target=UUID(int=2)),
        ),
        items=tuple(
            _item(
                metadata={
                    "identity_scope_values": {"Grade": "PRIMARY ONE"},
                    "source_page_indexes": [4],
                },
                number=n,
            )
            for n in (1, 2, 3)
        ),
        supports=(_support(component=20, sfi=1), _support(component=20, sfi=2)),
    )
    for item in bundle.items:
        item.description = "odd even"
        item.statement_code = "P.1"
    saved = bundle.model_dump(mode="json")
    extractor = _extractor(bundle=bundle)
    result = _pair(extractor=extractor)
    assert _pair(extractor=extractor, first=2, second=1) == result
    actual = _features(result)
    assert set(actual) == {
        "hierarchy_context",
        "lc_tag_token_overlap",
        "lc_text_token_overlap",
        "local_rank_proximity",
        "sfi_text_token_overlap",
        "sfi_text_trigram_overlap",
        "shared_learning_components",
        "source_code_prefix",
        "source_page_proximity",
    }
    assert result.admissibility.pair_id == "4e319395-a183-5315-9838-072975f2aa22"
    assert {
        (d.decision, d.direction) for d in result.admissibility.admissible_decisions
    } == {
        ("buildsTowards", "first_to_second"),
        ("buildsTowards", "second_to_first"),
        ("relatesTo", None),
        ("no_relation", None),
        ("needs_review", None),
    }
    assert all(
        f.nominated_relationships == ["buildsTowards", "relatesTo"]
        for f in actual.values()
    )
    assert actual["source_page_proximity"].triggering_values["page_gap"] == 0
    assert actual["local_rank_proximity"].triggering_values["rank_gap"] == 0
    assert actual["lc_text_token_overlap"].triggering_values["shared_values"] == [
        "even",
        "odd",
    ]
    assert actual["lc_tag_token_overlap"].triggering_values["shared_values"] == [
        "parity"
    ]
    assert bundle.model_dump(mode="json") == saved


@PARAM(
    argnames="endpoint",
    argvalues=("foreign", "framework", "lc", "other_identifier", "malformed"),
)
@PARAM(argnames="reverse", argvalues=(False, True))
def test_containment_is_enforced_before_any_feature(
    *, endpoint: str, monkeypatch: pytest.MonkeyPatch, reverse: bool
) -> None:
    """Foreign and non-SFI keys fail before their apparent evidence can be inspected.

    Parameters
    ----------
    endpoint
        Invalid endpoint category.
    monkeypatch
        Restoring sentinel for the first feature's text access.
    reverse
        Whether the invalid key is encountered first.
    """

    bundle = _bundle(
        components=(_component(number=20, sfis=(1,)),),
        items=(_item(), _item(number=2)),
        supports=(_support(component=20, sfi=1),),
    )
    extractor = _extractor(bundle=bundle)
    bad = {
        "foreign": str(UUID(int=999)),
        "framework": str(_FRAMEWORK_UUID),
        "lc": str(UUID(int=20)),
        "other_identifier": str(bundle.items[0].identifier),
        "malformed": "not-a-uuid",
    }[endpoint]
    with monkeypatch.context() as guard:
        guard.setattr(name="_word_tokens", target=lp_evidence, value=_reject)
        with pytest.raises(expected_exception=ValueError):
            extractor.extract_pair(
                first_sfi_uuid=bad if reverse else UUID(int=1),
                second_sfi_uuid=UUID(int=1) if reverse else bad,
            )


@PARAM(argnames="reverse_edges", argvalues=(False, True))
def test_dag_all_branches_and_shortest_distances_survive(reverse_edges: bool) -> None:
    """Every shared ancestor across unequal multi-parent branches stays explainable.

    Parameters
    ----------
    reverse_edges
        Opposite input relationship order must preserve the full result.
    """

    edges = tuple(
        _edge(source=UUID(int=source), target=UUID(int=target))
        for source, target in (
            (3, 1),
            (4, 1),
            (3, 2),
            (5, 2),
            (6, 3),
            (6, 4),
            (4, 5),
            (7, 6),
        )
    )
    bundle = _bundle(
        edges=edges[::-1] if reverse_edges else edges,
        items=tuple(_item(number=n) for n in range(1, 8)),
    )
    result = _pair(extractor=_extractor(bundle=bundle))
    feature = _features(result)["hierarchy_context"]
    assert len(result.evidence) == 1
    assert feature.triggering_values == {
        "shared_ancestors": [
            {
                "first_distance": first,
                "second_distance": second,
                "sfi_uuid": str(UUID(int=n)),
                "statement_type": "Performance Objective",
            }
            for n, first, second in ((3, 1, 1), (4, 1, 2), (6, 2, 2), (7, 3, 3))
        ]
    }
    assert feature.references == [f"sfi:{UUID(int=n)}" for n in (1, 2, 3, 4, 6, 7)]
    assert result.admissibility.first_sfi.parent_sfi_uuids == (UUID(int=3), UUID(int=4))
    assert result.admissibility.second_sfi.parent_sfi_uuids == (
        UUID(int=3),
        UUID(int=5),
    )


@PARAM(argnames="parent", argvalues=(1, 2))
def test_endpoint_ancestry_is_context_without_implied_direction(parent: int) -> None:
    """An allowed parent/descendant pair records ancestry without declaring progression.

    Parameters
    ----------
    parent
        Canonical endpoint acting as ancestor, independent of UUID orientation.
    """

    child = 3 - parent
    result = _pair(
        extractor=_extractor(
            bundle=_bundle(
                edges=(
                    _edge(source=UUID(int=parent), target=UUID(int=3)),
                    _edge(source=UUID(int=3), target=UUID(int=child)),
                ),
                items=tuple(_item(number=n) for n in (1, 2, 3)),
            )
        )
    )
    assert _features(result)["hierarchy_context"].triggering_values == {
        (
            "first_is_ancestor_of_second_distance"
            if parent == 1
            else "second_is_ancestor_of_first_distance"
        ): 2
    }
    assert {
        (d.decision, d.direction) for d in result.admissibility.admissible_decisions
    } == {("relatesTo", None), ("no_relation", None), ("needs_review", None)}


@PARAM(argnames="fallback", argvalues=(False, True))
def test_framework_placement_never_becomes_hierarchy_evidence(fallback: bool) -> None:
    """Shared framework attachment alone supplies no curricular match.

    Parameters
    ----------
    fallback
        Whether both framework edges carry explicit unresolved fallback flags.
    """

    bundle = _bundle(
        edges=tuple(
            _edge(
                fallback=fallback,
                source=_FRAMEWORK_UUID,
                source_entity="StandardsFramework",
                target=UUID(int=n),
            )
            for n in (1, 2)
        ),
        items=(_item(), _item(number=2)),
    )
    result = _pair(extractor=_extractor(bundle=bundle))
    assert not result.evidence
    assert result.admissibility.first_sfi.unresolved_ancestry is fallback
    assert result.admissibility.first_sfi.parent_sfi_uuids == ()
    assert len(result.admissibility.first_sfi.root_fallback_relationship_uuids) == int(
        fallback
    )
    if fallback:
        assert any("unresolved" in warning for warning in result.admissibility.warnings)


@PARAM(argnames="kind", argvalues=("self", "cross_type", "unresolved"))
def test_hard_exclusions_cannot_be_bypassed_by_matching_text(
    *, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even identical descriptions cannot create evidence for a prohibited pair.

    Parameters
    ----------
    kind
        Structural or configured pair exclusion.
    monkeypatch
        Restoring feature-evaluation sentinel.
    """

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
                number=2,
                statement_type=(
                    "Topic" if kind == "cross_type" else "Performance Objective"
                ),
            ),
        ),
    )
    for item in bundle.items:
        item.description = "identical text"
        item.statement_code = "A.1"
    payload = _config().model_dump(mode="json")
    if kind == "unresolved":
        payload["lp"]["unresolved_participation"] = "exclude_unresolved"
    extractor = _extractor(bundle=bundle, config=CreateKGConfig.model_validate(payload))
    with monkeypatch.context() as guard:
        guard.setattr(name="_word_tokens", target=lp_evidence, value=_reject)
        assert (
            extractor.extract_pair(
                first_sfi_uuid=UUID(int=1),
                second_sfi_uuid=UUID(int=1 if kind == "self" else 2),
            )
            is None
        )


def test_lc_alignment_uses_supports_edges_not_global_text_or_metadata_claims() -> None:
    """An unrelated LC cannot contribute through untrusted source-SFI metadata alone."""

    bundle = _bundle(
        components=(
            _component(
                description="matching", number=20, sfis=(1, 2), tags=["matching"]
            ),
        ),
        items=tuple(_item(number=n) for n in (1, 2, 3)),
        supports=(_support(component=20, sfi=3),),
    )
    assert not _pair(extractor=_extractor(bundle=bundle)).evidence


@PARAM(argnames="signal", argvalues=("exact", "text", "tags"))
def test_lc_signals_work_independently_of_each_other(signal: str) -> None:
    """LC identity, description and tag overlap each carry their own triggering values.

    Parameters
    ----------
    signal
        Isolated component evidence family.
    """

    components: tuple[dict[str, Any], ...]
    if signal == "exact":
        components = (_component(number=20, sfis=(1, 2)),)
        supports = (_support(component=20, sfi=1), _support(component=20, sfi=2))
        name = "shared_learning_components"
        shared, first_count, second_count, union_count = [str(UUID(int=20))], 1, 1, 1
    else:
        components = (
            _component(
                description="odd even" if signal == "text" else "!!!",
                number=20,
                sfis=(1,),
                tags=["ＰＡＲＩＴＹ", "integer"] if signal == "tags" else None,
            ),
            _component(
                description="EVEN prime even" if signal == "text" else "!!!",
                number=21,
                sfis=(2,),
                tags=["parity", "factor"] if signal == "tags" else None,
            ),
        )
        supports = (_support(component=20, sfi=1), _support(component=21, sfi=2))
        name = "lc_text_token_overlap" if signal == "text" else "lc_tag_token_overlap"
        shared, first_count, second_count, union_count = (
            ["even" if signal == "text" else "parity"],
            2,
            2,
            3,
        )
    result = _pair(
        extractor=_extractor(
            bundle=_bundle(
                components=components,
                items=(_item(), _item(number=2)),
                supports=supports,
            )
        )
    )
    actual = _features(result)
    assert set(actual) == {name}
    assert actual[name].triggering_values == {
        "first_count": first_count,
        "second_count": second_count,
        "shared_count": 1,
        "shared_values": shared,
        "union_count": union_count,
        "jaccard": 1 / union_count,
    }
    assert actual[name].references == [
        f"lc:{UUID(int=n)}" for n in ((20,) if signal == "exact" else (20, 21))
    ]
    assert actual[name].nominated_relationships == ["relatesTo"]


@PARAM(
    argnames="tags",
    argvalues=(
        None,
        "parity",
        {"parity": True},
        ["parity", 7],
        ["parity", None],
        [False],
        [],
        [" "],
    ),
)
def test_malformed_lc_tags_do_not_stringify_or_suppress_exact_support(
    tags: Any,
) -> None:
    """Absent or malformed tags supply no tag match while valid LC identities remain.

    Parameters
    ----------
    tags
        Untrusted optional tag metadata.
    """

    bundle = _bundle(
        components=(_component(number=20, sfis=(1, 2), tags=tags),),
        items=(_item(), _item(number=2)),
        supports=(_support(component=20, sfi=1), _support(component=20, sfi=2)),
    )
    result = _pair(extractor=_extractor(bundle=bundle))
    assert set(_features(result)) == {"shared_learning_components"}


def test_new_construction_reads_changed_evidence_without_reminting_pair() -> None:
    """A fresh run observes current text and page evidence rather than an old snapshot."""

    bundle = _bundle(items=(_item(), _item(number=2)))
    old = _extractor(bundle=bundle)
    initial = _pair(extractor=old)
    for item in bundle.items:
        item.description = "changed tokens"
        item.metadata["source_page_indexes"] = [7]
    current = _pair(extractor=_extractor(bundle=bundle))
    assert set(_features(current)) == {
        "sfi_text_token_overlap",
        "sfi_text_trigram_overlap",
        "source_page_proximity",
    }
    assert (
        _features(current)["source_page_proximity"].triggering_values[
            "first_page_index"
        ]
        == 7
    )
    assert current.admissibility.pair_id == initial.admissibility.pair_id
    assert _pair(extractor=old) == initial
    assert not initial.evidence


def test_no_match_is_distinct_from_pair_exclusion() -> None:
    """Permitted pairs with no clues remain empty evidence, never negative judgments."""

    result = _pair(
        extractor=_extractor(bundle=_bundle(items=(_item(), _item(number=2))))
    )
    assert not result.evidence
    assert result.admissibility.pair_id == "4e319395-a183-5315-9838-072975f2aa22"
    assert len(result.admissibility.admissible_decisions) == 3


def test_no_pair_enumeration_or_file_io_and_no_cross_call_budget_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Construction remains per-node and repeated extraction processes only supplied pairs.

    Parameters
    ----------
    monkeypatch
        Restoring I/O and pair-enumeration guards.
    """

    bundle = _bundle(items=tuple(_item(number=n) for n in range(1, 10)))
    payload = _config().model_dump(mode="json")
    payload["lp"]["candidate_policy"]["budgets"] = {
        "max_candidates_per_sfi": 1,
        "max_total_candidates": 1,
    }
    config = CreateKGConfig.model_validate(payload)
    with monkeypatch.context() as guard:
        guard.setattr(name="open", target=builtins, value=_reject)
        guard.setattr(name="open", target=Path, value=_reject)
        guard.setattr(name="mkdir", target=Path, value=_reject)
        guard.setattr(name="filter_pair", target=LPCandidateFilter, value=_reject)
        extractor = _extractor(bundle=bundle, config=config)
    with monkeypatch.context() as guard:
        guard.setattr(name="open", target=builtins, value=_reject)
        guard.setattr(name="open", target=Path, value=_reject)
        first = _pair(extractor=extractor)
        pairs = [_pair(extractor=extractor, first=1, second=n) for n in range(2, 10)]
        assert len({pair.admissibility.pair_id for pair in pairs}) == 8
        assert _pair(extractor=extractor) == first


def test_pratham_indicator_peer_preserves_both_real_ancestor_branches() -> None:
    """A synthetic same-grain peer exposes every ancestor of the reduced source DAG."""

    original = _fixture_bundle("pratham_science")
    indicator_uuid = UUID("069c360b-a0fd-5f3d-b653-4ed9d3e71c44")
    parent_uuid = UUID("003d05f8-ce74-53ec-9ef3-ef19b8f3ad80")
    indicator = next(
        item for item in original.items if item.case_identifier_uuid == indicator_uuid
    )
    peer = _item(
        metadata=deepcopy(indicator.metadata), number=1, statement_type="Indicator"
    )
    bundle = _bundle(
        components=tuple(
            component.model_dump(mode="json")
            for component in original.learning_components
        ),
        edges=(
            *tuple(
                edge.model_dump(mode="json")
                for edge in original.relationships_has_child
            ),
            _edge(source=parent_uuid, target=peer.case_identifier_uuid),
        ),
        framework_uuid=original.framework.case_identifier_uuid,
        items=(*original.items, peer),
        supports=tuple(
            edge.model_dump(mode="json") for edge in original.relationships_supports
        ),
    )
    result = _pair(
        extractor=_extractor(bundle=bundle, config=_config("pratham_science")),
        second=indicator_uuid.int,
    )
    assert _features(result)["hierarchy_context"].triggering_values == {
        "shared_ancestors": [
            {
                "first_distance": distance,
                "second_distance": distance,
                "sfi_uuid": identifier,
                "statement_type": kind,
            }
            for identifier, distance, kind in (
                (str(parent_uuid), 1, "Content Domain Specific Learning Outcome"),
                ("76575690-87b8-5435-bce2-d3d8dc477b54", 2, "Chapter"),
                ("855aefba-808d-5896-8519-cf4c80d9c97d", 2, "NCERT Learning Outcome"),
            )
        ]
    }


@PARAM(
    argnames="first_rank,second_rank",
    argvalues=(
        (0, 0),
        (0, 1),
        (1, 0),
        (0, 2),
        (2, 0),
        (None, 0),
        (0, None),
        (None, None),
    ),
)
@PARAM(argnames="profile", argvalues=tuple(_PROFILES))
def test_rank_proximity_follows_each_local_order_without_narrowing_permissions(
    *, first_rank: int | None, profile: str, second_rank: int | None
) -> None:
    """Missing ranks provide no positive evidence; long forward gaps stay admissible.

    Parameters
    ----------
    first_rank
        First endpoint's configured rank, or missing coordinate.
    profile
        Reviewed curriculum coordinate vocabulary and statement grain.
    second_rank
        Second endpoint's configured rank, or missing coordinate.
    """

    _, coordinate, order, allowed, _ = _PROFILES[profile]
    if first_rank == 2 or second_rank == 2:
        # The two-value curriculum exercises its largest possible forward gap.
        first_rank = min(first_rank, len(order) - 1) if first_rank is not None else None
        second_rank = (
            min(second_rank, len(order) - 1) if second_rank is not None else None
        )
    items = tuple(
        _item(
            metadata=(
                {}
                if rank is None
                else {"identity_scope_values": {coordinate: order[rank]}}
            ),
            number=n,
            statement_type=allowed[0],
        )
        for n, rank in ((1, first_rank), (2, second_rank))
    )
    result = _pair(
        extractor=_extractor(bundle=_bundle(items=items), config=_config(profile))
    )
    actual = _features(result)
    gap = (
        None
        if first_rank is None or second_rank is None
        else abs(first_rank - second_rank)
    )
    assert set(actual) == (
        {"local_rank_proximity"} if gap is not None and gap <= 1 else set()
    )
    if actual:
        assert first_rank is not None and second_rank is not None
        assert actual["local_rank_proximity"].triggering_values == {
            "coordinate_statement_type": coordinate,
            "first_rank": first_rank,
            "first_value": order[first_rank],
            "rank_gap": gap,
            "second_rank": second_rank,
            "second_value": order[second_rank],
            "strength": "weak",
        }
    expected: set[tuple[str, str | None]] = {
        ("relatesTo", None),
        ("no_relation", None),
        ("needs_review", None),
    }
    if first_rank is not None and second_rank is not None:
        if first_rank <= second_rank:
            expected.add(("buildsTowards", "first_to_second"))
        if second_rank <= first_rank:
            expected.add(("buildsTowards", "second_to_first"))
    assert {
        (d.decision, d.direction) for d in result.admissibility.admissible_decisions
    } == expected


@PARAM(argnames="relation", argvalues=("buildsTowards", "relatesTo"))
def test_relation_specific_evidence_never_restores_a_forbidden_relation(
    relation: str,
) -> None:
    """A matching clue is attributed only to relationships allowed for this pair.

    Parameters
    ----------
    relation
        The sole relationship permitted for the synthetic pair.
    """

    payload = _config().model_dump(mode="json")
    if relation == "buildsTowards":
        payload["lp"]["relates_to"]["allowed_statement_type_pairs"] = [
            {"first_statement_type": "Topic", "second_statement_type": "Topic"}
        ]
    else:
        payload["lp"]["builds_towards"]["allowed_statement_type_pairs"] = [
            {"source_statement_type": "Topic", "target_statement_type": "Topic"}
        ]
    bundle = _bundle(
        items=tuple(
            _item(metadata={"identity_scope_values": {"Grade": grade}}, number=n)
            for n, grade in ((1, "PRIMARY THREE"), (2, "PRIMARY ONE"))
        )
    )
    for item in bundle.items:
        item.description = "same text"
        item.statement_code = "A.1"
    result = _pair(
        extractor=_extractor(
            bundle=bundle, config=CreateKGConfig.model_validate(payload)
        )
    )
    assert result.evidence
    assert all(
        feature.nominated_relationships == [relation] for feature in result.evidence
    )
    expected: set[tuple[str, str | None]] = {
        ("no_relation", None),
        ("needs_review", None),
    }
    expected.add((relation, "second_to_first" if relation == "buildsTowards" else None))
    assert {
        (d.decision, d.direction) for d in result.admissibility.admissible_decisions
    } == expected


@PARAM(argnames="profile", argvalues=tuple(_PROFILES))
def test_six_reduced_curricula_preserve_context_and_reordered_results(
    profile: str,
) -> None:
    """Reduced source shapes retain exact audit context under graph permutation.

    Parameters
    ----------
    profile
        Approved reduced curriculum projection, never a semantic gold label.
    """

    bundle = _fixture_bundle(profile)
    config = _config(profile)
    original = bundle.model_dump(mode="json")
    extractor = _extractor(bundle=bundle, config=config)
    reordered = bundle.model_copy(deep=True)
    for name in (
        "items",
        "learning_components",
        "relationships_has_child",
        "relationships_supports",
    ):
        getattr(reordered, name).reverse()
    reordered.entity_provenance["items"] = dict(
        reversed(list(reordered.entity_provenance["items"].items()))
    )
    second = _extractor(bundle=reordered, config=config)
    observed = []
    for first, last in combinations(bundle.items, 2):
        result = extractor.extract_pair(
            first_sfi_uuid=first.case_identifier_uuid,
            second_sfi_uuid=last.case_identifier_uuid,
        )
        assert (
            second.extract_pair(
                first_sfi_uuid=last.case_identifier_uuid.hex.upper(),
                second_sfi_uuid=first.case_identifier_uuid.urn,
            )
            == result
        )
        if result is None:
            continue
        actual = _features(result)
        observed.append(result)
        for record in (result.admissibility.first_sfi, result.admissibility.second_sfi):
            assert record.sfi == next(
                item
                for item in bundle.items
                if item.case_identifier_uuid == record.sfi.case_identifier_uuid
            )
            assert (
                record.source_provenance
                == bundle.entity_provenance["items"][
                    str(record.sfi.case_identifier_uuid)
                ]
            )
        if profile == "madhi_math":
            assert result.admissibility.first_sfi.coordinate.statement_type == "Class"
        if profile == "rwanda_math" and "shared_learning_components" in actual:
            assert actual["shared_learning_components"].triggering_values[
                "shared_values"
            ] == ["511d6eb6-64ed-5e15-850e-a3fb82ebc093"]
            assert {field.name for field in fields(result)} == {
                "admissibility",
                "evidence",
            }
    assert len(observed) == sum(
        first.statement_type == second.statement_type
        and first.statement_type in _PROFILES[profile][3]
        for first, second in combinations(bundle.items, 2)
    )
    if profile == "rwanda_math":
        assert any("shared_learning_components" in _features(pair) for pair in observed)
    if profile == "ghana_math":
        assert any(
            any("unresolved" in warning for warning in pair.admissibility.warnings)
            for pair in observed
        )
    assert bundle.model_dump(mode="json") == original


@PARAM(argnames="mutation", argvalues=("bundle", "result"))
def test_snapshot_isolates_all_nested_evidence_and_preserves_fresh_calls(
    mutation: str,
) -> None:
    """Mutable graph inputs and returned values cannot rewrite a run's evidence snapshot.

    Parameters
    ----------
    mutation
        Whether the caller corrupts original inputs or a returned result.
    """

    bundle = _bundle(
        components=(
            _component(
                description="odd even", number=20, sfis=(1, 2, 3), tags=["parity"]
            ),
        ),
        edges=tuple(_edge(source=UUID(int=4), target=UUID(int=n)) for n in (1, 2, 3)),
        items=tuple(
            _item(
                metadata={
                    "identity_scope_values": {"Grade": "PRIMARY ONE"},
                    "source_page_indexes": [2],
                },
                number=n,
            )
            for n in (1, 2, 3, 4)
        ),
        supports=tuple(_support(component=20, sfi=n) for n in (1, 2, 3)),
    )
    for item in bundle.items:
        item.statement_code = "A.1"
        item.description = "same tokens"
    config = _config()
    input_before = bundle.model_dump(mode="json")
    config_before = config.model_dump(mode="json")
    extractor = _extractor(bundle=bundle, config=config)
    first = _pair(extractor=extractor)
    sibling = _pair(extractor=extractor, second=3)
    saved, sibling_saved = deepcopy(first), deepcopy(sibling)
    assert bundle.model_dump(mode="json") == input_before
    assert config.model_dump(mode="json") == config_before
    if mutation == "bundle":
        bundle.items[0].metadata["source_page_indexes"].append(999)
        bundle.items[0].description = "changed"
        bundle.items[0].statement_code = "changed"
        bundle.items[3].statement_type = "Topic"
        bundle.entity_provenance["items"][str(UUID(int=1))][
            "source_page_indexes"
        ].clear()
        bundle.learning_components[0].description = "changed"
        bundle.learning_components[0].metadata["tags"].clear()
        bundle.relationships_has_child.clear()
        bundle.relationships_supports.clear()
        config.learning_progressions.developmental_coordinate.ordered_values.reverse()
        config.learning_progressions.builds_towards.allowed_statement_type_pairs.clear()
        config.learning_progressions.relates_to.allowed_statement_type_pairs.clear()
    else:
        for feature in first.evidence:
            feature.references.clear()
            for value in feature.triggering_values.values():
                if isinstance(value, list):
                    value.clear()
            feature.triggering_values.clear()
            feature.nominated_relationships.clear()
        first.admissibility.first_sfi.sfi.description = "changed"
        first.admissibility.first_sfi.sfi.metadata["source_page_indexes"].clear()
        first.admissibility.second_sfi.source_provenance["source_page_indexes"].clear()
        first.admissibility.admissible_decisions[0].decision = "no_relation"
        assert bundle.model_dump(mode="json") == input_before
    assert sibling == sibling_saved
    assert _pair(extractor=extractor, first=2, second=1) == saved
    assert _pair(extractor=extractor, second=3) == sibling_saved


@PARAM(argnames="location", argvalues=("metadata", "provenance", "both"))
@PARAM(
    argnames="expected,first,second",
    argvalues=(
        ((0, 0, 0), [0], [0]),
        ((1, 1, 2), [8, 1, 1], [9, 2]),
        ((1, 2, 1), [2], [3, 1]),
        ((0, 8, 8), [1, 8, 5], [9, 8]),
        (None, [1], [3]),
        (None, [], [1]),
    ),
)
def test_source_page_proximity_uses_nearest_valid_positions(
    *,
    expected: tuple[int, int, int] | None,
    first: list[int],
    location: str,
    second: list[int],
) -> None:
    """Page matches preserve sorted unique inputs and deterministic nearest-page ties.

    Parameters
    ----------
    expected
        Expected gap and canonical first/second nearest positions, or no match.
    first
        First endpoint page indexes.
    location
        Authoritative location carrying the page indexes.
    second
        Second endpoint page indexes.
    """

    bundle = _bundle(items=(_item(), _item(number=2)))
    for n, pages in ((0, first), (1, second)):
        if location in ("metadata", "both"):
            bundle.items[n].metadata["source_page_indexes"] = pages
        if location in ("provenance", "both"):
            bundle.entity_provenance["items"][str(UUID(int=n + 1))][
                "source_page_indexes"
            ] = list(reversed(pages))
    result = _pair(extractor=_extractor(bundle=bundle))
    actual = _features(result)
    assert set(actual) == ({"source_page_proximity"} if expected else set())
    if expected:
        gap, first_page, second_page = expected
        assert actual["source_page_proximity"].triggering_values == {
            "first_page_index": first_page,
            "first_source_page_indexes": sorted(set(first)),
            "page_gap": gap,
            "second_page_index": second_page,
            "second_source_page_indexes": sorted(set(second)),
            "strength": "weak",
        }
        assert actual["source_page_proximity"].nominated_relationships == ["relatesTo"]


@PARAM(
    argnames="bad",
    argvalues=(
        None,
        "1",
        1,
        True,
        {},
        [True],
        [-1],
        [1.0],
        ["1"],
        [1, None],
        [1, "2"],
        [],
        [99],
    ),
)
@PARAM(argnames="location", argvalues=("metadata", "provenance"))
def test_source_pages_reject_malformed_or_conflicting_copies(
    *, bad: Any, location: str
) -> None:
    """A good copy cannot rescue malformed or disagreeing optional source positions.

    Parameters
    ----------
    bad
        Invalid shape, empty contradiction, or differing page population.
    location
        Copy replaced while the other retains a valid page.
    """

    bundle = _bundle(
        items=tuple(
            _item(metadata={"source_page_indexes": [1]}, number=n) for n in (1, 2)
        )
    )
    source = (
        bundle.items[0].metadata
        if location == "metadata"
        else bundle.entity_provenance["items"][str(UUID(int=1))]
    )
    source["source_page_indexes"] = bad
    result = _pair(extractor=_extractor(bundle=bundle))
    assert not result.evidence
    assert result.admissibility.first_sfi.sfi.metadata == bundle.items[0].metadata
    assert (
        result.admissibility.first_sfi.source_provenance
        == bundle.entity_provenance["items"][str(UUID(int=1))]
    )


@PARAM(
    argnames="key",
    argvalues=(
        "source_pages",
        "page_index",
        "source_window_ids",
        "source",
        "source_order",
    ),
)
def test_source_positions_are_not_invented_from_unrelated_metadata(key: str) -> None:
    """Arbitrary metadata and display order are retained without guessing page indexes.

    Parameters
    ----------
    key
        Non-authoritative source-order-looking key.
    """

    bundle = _bundle(items=tuple(_item(metadata={key: [1]}, number=n) for n in (1, 2)))
    assert not _pair(extractor=_extractor(bundle=bundle)).evidence


@PARAM(
    argnames="expected_tokens,expected_trigrams,first,second",
    argvalues=(
        (["a"], [], "a", "A!"),
        ([], ["abc"], "abcd", "abce"),
        (["ab", "cd"], ["b c"], "ab cd", "AB_CD"),
        (["é", "二"], ["é 二"], "E\u0301 二", "é 二"),
        ([], [], "ab", "cd"),
        ([], [], "---", "___"),
    ),
)
def test_text_tokens_and_trigrams_are_independent_unicode_lexical_evidence(
    *, expected_tokens: list[str], expected_trigrams: list[str], first: str, second: str
) -> None:
    """Lexical overlap records exact normalized tokens and character evidence.

    Parameters
    ----------
    expected_tokens
        Independently enumerated shared word tokens.
    expected_trigrams
        Independently enumerated shared three-character features.
    first
        First authoritative description.
    second
        Second authoritative description.
    """

    bundle = _bundle(items=(_item(), _item(number=2)))
    bundle.items[0].description = first
    bundle.items[1].description = second
    result = _pair(extractor=_extractor(bundle=bundle))
    actual = _features(result)
    expected = {}
    if expected_tokens:
        expected["sfi_text_token_overlap"] = expected_tokens
    if expected_trigrams:
        expected["sfi_text_trigram_overlap"] = expected_trigrams
    assert set(actual) == set(expected)
    for name, shared in expected.items():
        assert actual[name].triggering_values["shared_values"] == shared
        assert actual[name].triggering_values["shared_count"] == len(shared)
        assert actual[name].references == [f"sfi:{UUID(int=n)}" for n in (1, 2)]


def test_token_counts_use_sets_instead_of_repeated_word_frequency() -> None:
    """Repeated words do not inflate overlap or its exact Jaccard inputs."""

    bundle = _bundle(items=(_item(), _item(number=2)))
    bundle.items[0].description = "a a b"
    bundle.items[1].description = "b c c c"
    result = _pair(extractor=_extractor(bundle=bundle))
    feature = _features(result)["sfi_text_token_overlap"]
    assert feature.triggering_values == {
        "first_count": 2,
        "second_count": 2,
        "shared_count": 1,
        "shared_values": ["b"],
        "union_count": 3,
        "jaccard": 1 / 3,
    }


def test_unresolved_branch_keeps_trustworthy_context_and_explicit_warning() -> None:
    """Fallback taint propagates without erasing the valid SFI branches beneath it."""

    fallback = _edge(
        fallback=True,
        source=_FRAMEWORK_UUID,
        source_entity="StandardsFramework",
        target=UUID(int=3),
    )
    bundle = _bundle(
        edges=(
            fallback,
            _edge(source=UUID(int=3), target=UUID(int=1)),
            _edge(source=UUID(int=4), target=UUID(int=1)),
            _edge(source=UUID(int=4), target=UUID(int=2)),
        ),
        items=tuple(_item(number=n) for n in (1, 2, 3, 4)),
    )
    result = _pair(extractor=_extractor(bundle=bundle))
    assert result.admissibility.first_sfi.unresolved_ancestry is True
    assert result.admissibility.second_sfi.unresolved_ancestry is False
    assert result.admissibility.first_sfi.parent_sfi_uuids == (UUID(int=3), UUID(int=4))
    assert any("unresolved" in warning for warning in result.admissibility.warnings)
    actual = _features(result)["hierarchy_context"]
    assert actual.triggering_values == {
        "shared_ancestors": [
            {
                "first_distance": 1,
                "second_distance": 1,
                "sfi_uuid": str(UUID(int=4)),
                "statement_type": "Performance Objective",
            }
        ]
    }
    assert f"sfi:{_FRAMEWORK_UUID}" not in actual.references
    parent_pair = _pair(extractor=_extractor(bundle=bundle), first=1, second=3)
    assert parent_pair.admissibility.second_sfi.root_fallback_relationship_uuids == (
        fallback["identifier"],
    )
    assert _features(parent_pair)["hierarchy_context"].triggering_values == {
        "second_is_ancestor_of_first_distance": 1
    }


@PARAM(
    argnames="defect",
    argvalues=(
        "failed_report",
        "report_errors",
        "foreign_document",
        "foreign_framework",
        "dangling_edge",
        "cycle",
        "missing_provenance",
        "invalid_coordinate",
        "conflicting_coordinate",
    ),
)
def test_upstream_integrity_failures_prevent_extractor_construction(
    defect: str,
) -> None:
    """No feature snapshot is available from invalid or incorrectly identified inputs.

    Parameters
    ----------
    defect
        Invalid upstream validation, graph, provenance, or coordinate state.
    """

    bundle = _bundle(items=(_item(), _item(number=2)))
    if defect == "failed_report":
        bundle.validation_report.passed = False
    elif defect == "report_errors":
        bundle.validation_report.errors.append("Synthetic upstream failure")
    elif defect == "foreign_document":
        bundle.framework.metadata["doc_key"] = "other-document"
    elif defect == "foreign_framework":
        bundle.relationships_has_child[0].source_entity_value = str(UUID(int=999))
    elif defect == "dangling_edge":
        bundle.relationships_has_child[0].target_entity_value = str(UUID(int=999))
    elif defect == "missing_provenance":
        bundle.entity_provenance["items"].pop(str(UUID(int=1)))
    elif defect == "invalid_coordinate":
        bundle.items[0].metadata["identity_scope_values"] = {"Grade": "UNKNOWN"}
    elif defect == "conflicting_coordinate":
        bundle.items[0].metadata["identity_scope_values"] = {"Grade": "PRIMARY ONE"}
        bundle.items[0].metadata["identity_scope_values"]["Class"] = "PRIMARY TWO"
    else:
        bundle = _bundle(
            edges=(
                _edge(source=UUID(int=1), target=UUID(int=2)),
                _edge(source=UUID(int=2), target=UUID(int=1)),
            ),
            items=(_item(), _item(number=2)),
        )
    with pytest.raises(expected_exception=ValueError):
        _extractor(bundle=bundle)
