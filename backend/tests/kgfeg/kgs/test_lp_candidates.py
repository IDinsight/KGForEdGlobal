"""Red-team canonical LP pair permissions over validated curriculum inputs."""

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
from kgfeg.kgs.lp_candidates import (
    LPCandidateFilter,
    LPPairAdmissibility,
    build_lp_pair_filter,
    build_lp_pair_id,
)
from kgfeg.kgs.schemas import AcademicStandardsLCKGBundle, StandardsFrameworkItem
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
        Unfiltered metadata, defaulting to one valid coordinate.
    normalized_statement_type
        Exported normalized type, independent of the local statement type.
    number
        Deterministic CASE UUID suffix.
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
            "description": "A synthetic skill; display PRIMARY THREE / Grade 12.",
            "grade_level": ["12"],
            "identifier": UUID(int=number + 2**112),
            "jurisdiction": "Synthetic jurisdiction",
            "metadata": deepcopy(
                metadata
                if metadata is not None
                else {"identity_scope_values": {"Grade": "PRIMARY ONE"}}
            ),
            "normalized_statement_type": normalized_statement_type,
            "statement_code": "PRIMARY THREE.99",
            "statement_type": statement_type,
        }
    )


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
