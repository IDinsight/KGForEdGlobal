"""Test intrinsic Learning Progressions pair, evidence, and judgment schemas."""

# Standard Library
from math import inf, nan
from typing import Any

# Third Party Library
import pytest

from pydantic import BaseModel, ValidationError

# Package Library
import kgfeg.kgs.schemas as kg_schemas

from kgfeg.kgs.schemas import (
    AcademicStandardsKGBundle,
    AcademicStandardsLCKGBundle,
    LPAdmissibleDecision,
    LPCandidateEvidence,
    LPCandidatePair,
    LPPairJudgment,
)
from tests.constants import PARAM
from tests.fixtures.lp.loader import load_all_lp_regression_fixtures

_FIRST_SFI_UUID = "00000000-0000-0000-0000-000000000001"
_INVALID_EVIDENCE_VALUES: tuple[Any, ...] = (
    None,
    "",
    "   ",
    [],
    {},
    nan,
    inf,
    -inf,
)
_INVALID_STRING_EXCEPTIONS: tuple[type[Exception], ...] = (
    TypeError,
    ValidationError,
)
_SECOND_SFI_UUID = "00000000-0000-0000-0000-000000000002"


def _valid_candidate_payload(
    *,
    admissible_decisions: list[dict[str, Any]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one intrinsically valid candidate-pair payload.

    Parameters
    ----------
    admissible_decisions
        Complete decision options for the candidate pair.
    evidence
        Named evidence records that nominated the pair.

    Returns
    -------
    dict[str, Any]
        A fresh candidate-pair payload.
    """

    decisions = (
        admissible_decisions
        if admissible_decisions is not None
        else [
            {"decision": "buildsTowards", "direction": "first_to_second"},
            {"decision": "no_relation"},
            {"decision": "needs_review"},
        ]
    )
    evidence_records = (
        evidence
        if evidence is not None
        else [_valid_evidence_payload(nominated_relationships=["buildsTowards"])]
    )
    return {
        "admissible_decisions": decisions,
        "evidence": evidence_records,
        "first_sfi_uuid": _FIRST_SFI_UUID,
        "pair_id": "pair-001",
        "second_sfi_uuid": _SECOND_SFI_UUID,
        "warnings": ["Unresolved source ancestry retained for adjudication."],
    }


def _valid_evidence_payload(
    *,
    evidence_type: Any = "shared_learning_component",
    nominated_relationships: list[Any] | None = None,
    references: list[Any] | None = None,
    triggering_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one intrinsically valid candidate-evidence payload.

    Parameters
    ----------
    evidence_type
        Stable evidence-signal name.
    nominated_relationships
        Relationship outcomes nominated by the evidence.
    references
        Source-record identifiers supporting the evidence.
    triggering_values
        Concrete JSON evidence values that triggered nomination.

    Returns
    -------
    dict[str, Any]
        A fresh candidate-evidence payload.
    """

    return {
        "evidence_type": evidence_type,
        "nominated_relationships": (
            nominated_relationships
            if nominated_relationships is not None
            else ["buildsTowards"]
        ),
        "references": references if references is not None else ["lc-001"],
        "triggering_values": (
            triggering_values
            if triggering_values is not None
            else {"shared_lc_count": 1}
        ),
    }


def _valid_judgment_payload(
    *,
    confidence: Any = 0.5,
    decision: str = "buildsTowards",
    direction: str | None = "first_to_second",
    first_sfi_uuid: Any = _FIRST_SFI_UUID,
    pair_id: Any = "pair-001",
    rationale: Any = "The first standard supports success in the second standard.",
    second_sfi_uuid: Any = _SECOND_SFI_UUID,
    warnings: list[Any] | None = None,
) -> dict[str, Any]:
    """Build one intrinsically valid pair-judgment payload.

    Parameters
    ----------
    confidence
        Audit confidence for the judgment.
    decision
        Unified semantic outcome for the pair.
    direction
        Direction used only by a ``buildsTowards`` outcome.
    first_sfi_uuid
        Lower canonical endpoint UUID.
    pair_id
        Stable pair identifier copied into the judgment.
    rationale
        Evidence-grounded explanation of the judgment.
    second_sfi_uuid
        Higher canonical endpoint UUID.
    warnings
        Upstream warnings considered by the judgment.

    Returns
    -------
    dict[str, Any]
        A fresh pair-judgment payload.
    """

    return {
        "confidence": confidence,
        "decision": decision,
        "direction": direction,
        "first_sfi_uuid": first_sfi_uuid,
        "pair_id": pair_id,
        "rationale": rationale,
        "second_sfi_uuid": second_sfi_uuid,
        "warnings": (
            warnings
            if warnings is not None
            else ["Source hierarchy warning was considered."]
        ),
    }


@PARAM(
    argnames=("decision", "direction"),
    argvalues=[
        ("buildsTowards", "first_to_second"),
        ("buildsTowards", "second_to_first"),
        ("needs_review", None),
        ("no_relation", None),
        ("relatesTo", None),
    ],
)
def test_admissible_decision_accepts_valid_combinations(
    *, decision: str, direction: str | None
) -> None:
    """Every unified intrinsic decision accepts exactly its valid direction shape.

    Parameters
    ----------
    decision
        Decision outcome under test.
    direction
        Valid direction or absence of direction for the outcome.
    """

    result = LPAdmissibleDecision.model_validate(
        {"decision": decision, "direction": direction}
    )

    assert result.decision == decision
    assert result.direction == direction


@PARAM(
    argnames=("decision", "direction"),
    argvalues=[
        ("buildsTowards", None),
        ("needs_review", "first_to_second"),
        ("no_relation", "second_to_first"),
        ("relatesTo", "first_to_second"),
        ("unsupported", None),
    ],
)
def test_admissible_decision_rejects_invalid_combinations(
    *, decision: str, direction: str | None
) -> None:
    """Missing, extraneous, or unsupported decision directions fail closed.

    Parameters
    ----------
    decision
        Invalid or directionally incompatible decision outcome.
    direction
        Missing or extraneous direction paired with the decision.
    """

    with pytest.raises(expected_exception=ValidationError):
        LPAdmissibleDecision.model_validate(
            {"decision": decision, "direction": direction}
        )


@PARAM(
    argnames=("admissible_decisions", "nominated_relationships"),
    argvalues=[
        (
            [
                {"decision": "buildsTowards", "direction": "first_to_second"},
                {"decision": "no_relation"},
                {"decision": "needs_review"},
            ],
            ["buildsTowards"],
        ),
        (
            [
                {"decision": "buildsTowards", "direction": "second_to_first"},
                {"decision": "no_relation"},
                {"decision": "needs_review"},
            ],
            ["buildsTowards"],
        ),
        (
            [
                {"decision": "relatesTo"},
                {"decision": "no_relation"},
                {"decision": "needs_review"},
            ],
            ["relatesTo"],
        ),
        (
            [
                {"decision": "buildsTowards", "direction": "first_to_second"},
                {"decision": "buildsTowards", "direction": "second_to_first"},
                {"decision": "relatesTo"},
                {"decision": "no_relation"},
                {"decision": "needs_review"},
            ],
            ["buildsTowards", "relatesTo"],
        ),
    ],
)
def test_candidate_pair_accepts_each_relationship_policy_shape(
    *,
    admissible_decisions: list[dict[str, Any]],
    nominated_relationships: list[str],
) -> None:
    """Candidates support either direction, either relation, or both relations.

    Parameters
    ----------
    admissible_decisions
        Complete admissible decision set for the candidate.
    nominated_relationships
        Relation-specific outcomes nominated by its evidence.
    """

    payload = _valid_candidate_payload(
        admissible_decisions=admissible_decisions,
        evidence=[
            _valid_evidence_payload(nominated_relationships=nominated_relationships)
        ],
    )

    result = LPCandidatePair.model_validate(payload)

    assert result.first_sfi_uuid.int < result.second_sfi_uuid.int
    assert {item.decision for item in result.admissible_decisions}.issuperset(
        {"needs_review", "no_relation"}
    )


@PARAM(
    argnames=("admissible_decisions",),
    argvalues=[
        (
            [
                {"decision": "buildsTowards", "direction": "first_to_second"},
                {"decision": "buildsTowards", "direction": "first_to_second"},
                {"decision": "no_relation"},
                {"decision": "needs_review"},
            ],
        ),
        (
            [
                {"decision": "buildsTowards", "direction": "first_to_second"},
                {"decision": "needs_review"},
            ],
        ),
        (
            [
                {"decision": "buildsTowards", "direction": "first_to_second"},
                {"decision": "no_relation"},
            ],
        ),
        ([{"decision": "no_relation"}, {"decision": "needs_review"}],),
    ],
)
def test_candidate_pair_rejects_duplicate_and_incomplete_admissible_decisions(
    admissible_decisions: list[dict[str, Any]],
) -> None:
    """Candidates require unique options, both null outcomes, and a relation.

    Parameters
    ----------
    admissible_decisions
        Duplicate or incomplete decision set under test.
    """

    with pytest.raises(expected_exception=ValidationError):
        LPCandidatePair.model_validate(
            _valid_candidate_payload(admissible_decisions=admissible_decisions)
        )


def test_candidate_pair_rejects_duplicate_evidence_names() -> None:
    """Evidence signal names remain unique after surrounding whitespace is removed."""

    evidence = [
        _valid_evidence_payload(evidence_type="shared_learning_component"),
        _valid_evidence_payload(evidence_type=" shared_learning_component "),
    ]

    with pytest.raises(expected_exception=ValidationError):
        LPCandidatePair.model_validate(_valid_candidate_payload(evidence=evidence))


def test_candidate_pair_rejects_empty_evidence() -> None:
    """Every candidate carries at least one named nomination signal."""

    with pytest.raises(expected_exception=ValidationError):
        LPCandidatePair.model_validate(_valid_candidate_payload(evidence=[]))


def test_candidate_pair_rejects_evidence_nominating_non_admissible_relationship() -> (
    None
):
    """Relation-specific evidence cannot nominate a filtered-out relationship."""

    payload = _valid_candidate_payload(
        admissible_decisions=[
            {"decision": "relatesTo"},
            {"decision": "no_relation"},
            {"decision": "needs_review"},
        ],
        evidence=[_valid_evidence_payload(nominated_relationships=["buildsTowards"])],
    )

    with pytest.raises(expected_exception=ValidationError):
        LPCandidatePair.model_validate(payload)


@PARAM(
    argnames=("first_sfi_uuid", "second_sfi_uuid"),
    argvalues=[
        (_SECOND_SFI_UUID, _FIRST_SFI_UUID),
        (_FIRST_SFI_UUID, _FIRST_SFI_UUID),
        ("malformed", _SECOND_SFI_UUID),
        (_FIRST_SFI_UUID, "malformed"),
    ],
)
def test_candidate_pair_rejects_invalid_endpoint_pairs(
    *, first_sfi_uuid: Any, second_sfi_uuid: Any
) -> None:
    """Candidate endpoints must be parseable, distinct, and canonically ascending.

    Parameters
    ----------
    first_sfi_uuid
        Malformed or incorrectly ordered first endpoint.
    second_sfi_uuid
        Malformed, repeated, or incorrectly ordered second endpoint.
    """

    payload = _valid_candidate_payload()
    payload["first_sfi_uuid"] = first_sfi_uuid
    payload["second_sfi_uuid"] = second_sfi_uuid

    with pytest.raises(expected_exception=ValidationError):
        LPCandidatePair.model_validate(payload)


@PARAM(
    argnames=("pair_id", "warnings"),
    argvalues=[
        ("", ["Valid warning"]),
        ("   ", ["Valid warning"]),
        (None, ["Valid warning"]),
        (False, ["Valid warning"]),
        (42, ["Valid warning"]),
        ("pair-001", [""]),
        ("pair-001", ["   "]),
        ("pair-001", ["Duplicate", " Duplicate "]),
    ],
)
def test_candidate_pair_rejects_invalid_identifiers_and_warnings(
    *, pair_id: Any, warnings: list[Any]
) -> None:
    """Candidate identifiers and present warning entries are non-empty and unique.

    Parameters
    ----------
    pair_id
        Blank or non-string pair identifier under test.
    warnings
        Valid or invalid warning collection paired with the identifier.
    """

    payload = _valid_candidate_payload()
    payload["pair_id"] = pair_id
    payload["warnings"] = warnings

    with pytest.raises(expected_exception=_INVALID_STRING_EXCEPTIONS):
        LPCandidatePair.model_validate(payload)


def test_evidence_accepts_recursive_json_values_and_round_trips() -> None:
    """Concrete recursive JSON evidence retains strings, numbers, booleans, and shape."""

    payload = _valid_evidence_payload(
        triggering_values={
            "boolean_false": False,
            "boolean_true": True,
            "floating_point": 1.25,
            "integer": -3,
            "mapping": {"list": ["concept", 0, 2.5, True, {"nested": "value"}]},
            "string": "shared concept",
        }
    )

    evidence = LPCandidateEvidence.model_validate(payload)
    reparsed = LPCandidateEvidence.model_validate_json(evidence.model_dump_json())

    for record in (evidence, reparsed):
        assert record.triggering_values["boolean_false"] is False
        assert record.triggering_values["boolean_true"] is True
        assert isinstance(record.triggering_values["floating_point"], float)
        assert isinstance(record.triggering_values["integer"], int)
        assert not isinstance(record.triggering_values["integer"], bool)
        mapping = record.triggering_values["mapping"]
        assert isinstance(mapping, dict)
        values = mapping["list"]
        assert isinstance(values, list)
        assert values[3] is True

    assert reparsed.model_dump(mode="json") == evidence.model_dump(mode="json")
    assert reparsed.triggering_values == payload["triggering_values"]


@PARAM(
    argnames=("payload",),
    argvalues=[
        (_valid_evidence_payload(evidence_type=""),),
        (_valid_evidence_payload(evidence_type="   "),),
        (_valid_evidence_payload(evidence_type=None),),
        (_valid_evidence_payload(evidence_type=False),),
        (_valid_evidence_payload(evidence_type=42),),
        (_valid_evidence_payload(nominated_relationships=[]),),
        (_valid_evidence_payload(nominated_relationships=["relatesTo", "relatesTo"]),),
        (_valid_evidence_payload(references=[""]),),
        (_valid_evidence_payload(references=[False]),),
        (_valid_evidence_payload(references=[42]),),
        (_valid_evidence_payload(references=["reference", " reference "]),),
        (_valid_evidence_payload(triggering_values={}),),
        (_valid_evidence_payload(triggering_values={"": "value"}),),
        (_valid_evidence_payload(triggering_values={"key": 1, " key ": 2}),),
        (_valid_evidence_payload(triggering_values={"outer": {"": "value"}}),),
        (_valid_evidence_payload(triggering_values={"outer": {"key": 1, " key ": 2}}),),
    ],
)
def test_evidence_rejects_blank_empty_or_duplicate_names(
    payload: dict[str, Any],
) -> None:
    """Evidence names, nominations, references, and mapping keys fail closed.

    Parameters
    ----------
    payload
        Evidence payload containing one blank, empty, or duplicate name.
    """

    with pytest.raises(expected_exception=_INVALID_STRING_EXCEPTIONS):
        LPCandidateEvidence.model_validate(payload)


@PARAM(
    argnames=("triggering_values",),
    argvalues=[
        ({"value": invalid_value},) for invalid_value in _INVALID_EVIDENCE_VALUES
    ]
    + [({"value": [invalid_value]},) for invalid_value in _INVALID_EVIDENCE_VALUES]
    + [
        ({"value": {"nested": invalid_value}},)
        for invalid_value in _INVALID_EVIDENCE_VALUES
    ]
    + [
        ({"value": [{"nested": [invalid_value]}]},)
        for invalid_value in _INVALID_EVIDENCE_VALUES
    ],
)
def test_evidence_rejects_invalid_values_at_every_nesting_depth(
    triggering_values: dict[str, Any],
) -> None:
    """Null, blank, empty, and non-finite evidence fails at shallow or deep paths.

    Parameters
    ----------
    triggering_values
        Recursive evidence mapping containing one prohibited concrete value.
    """

    with pytest.raises(expected_exception=ValidationError):
        LPCandidateEvidence.model_validate(
            _valid_evidence_payload(triggering_values=triggering_values)
        )


def test_existing_as_and_as_lc_schema_behavior_remains_unchanged() -> None:
    """Existing AS and AS+LC field contracts and reduced fixtures still validate."""

    assert set(AcademicStandardsKGBundle.model_fields) == {
        "entity_provenance",
        "framework",
        "items",
        "relationships_has_child",
        "summary",
        "unresolved_items",
        "validation_report",
    }
    assert set(AcademicStandardsLCKGBundle.model_fields) == {
        "entity_provenance",
        "framework",
        "items",
        "learning_components",
        "relationships_has_child",
        "relationships_supports",
        "summary",
        "unresolved_items",
        "validation_report",
    }
    assert len(load_all_lp_regression_fixtures()) == 6


@PARAM(
    argnames=("confidence", "decision", "direction"),
    argvalues=[
        (0.0, "buildsTowards", "first_to_second"),
        (1.0, "buildsTowards", "second_to_first"),
        (0.25, "relatesTo", None),
        (0.5, "no_relation", None),
        (0.75, "needs_review", None),
    ],
)
def test_judgment_accepts_all_outcomes_and_confidence_boundaries(
    *, confidence: float, decision: str, direction: str | None
) -> None:
    """Judgments accept both directed outcomes and every nondirectional state.

    Parameters
    ----------
    confidence
        Valid boundary or interior confidence value.
    decision
        Valid unified decision outcome.
    direction
        Valid direction or absence of direction for the outcome.
    """

    result = LPPairJudgment.model_validate(
        _valid_judgment_payload(
            confidence=confidence, decision=decision, direction=direction
        )
    )

    assert result.confidence == confidence
    assert result.decision == decision
    assert result.direction == direction


@PARAM(
    argnames=("confidence",),
    argvalues=[
        (-0.0001,),
        (1.0001,),
        (False,),
        (True,),
        ("0.5",),
        (nan,),
        (inf,),
        (-inf,),
    ],
)
def test_judgment_rejects_invalid_confidence(confidence: Any) -> None:
    """Confidence rejects coercion, non-finite values, and values outside its range.

    Parameters
    ----------
    confidence
        Invalid confidence value under test.
    """

    with pytest.raises(expected_exception=ValidationError):
        LPPairJudgment.model_validate(_valid_judgment_payload(confidence=confidence))


@PARAM(
    argnames=("first_sfi_uuid", "second_sfi_uuid"),
    argvalues=[
        (_SECOND_SFI_UUID, _FIRST_SFI_UUID),
        (_FIRST_SFI_UUID, _FIRST_SFI_UUID),
        ("malformed", _SECOND_SFI_UUID),
        (_FIRST_SFI_UUID, "malformed"),
    ],
)
def test_judgment_rejects_invalid_endpoint_pairs(
    *, first_sfi_uuid: Any, second_sfi_uuid: Any
) -> None:
    """Judgment endpoints must be parseable, distinct, and canonically ascending.

    Parameters
    ----------
    first_sfi_uuid
        Malformed or incorrectly ordered first endpoint.
    second_sfi_uuid
        Malformed, repeated, or incorrectly ordered second endpoint.
    """

    with pytest.raises(expected_exception=ValidationError):
        LPPairJudgment.model_validate(
            _valid_judgment_payload(
                first_sfi_uuid=first_sfi_uuid,
                second_sfi_uuid=second_sfi_uuid,
            )
        )


@PARAM(
    argnames=("pair_id", "rationale", "warnings"),
    argvalues=[
        ("", "Valid rationale", ["Valid warning"]),
        ("   ", "Valid rationale", ["Valid warning"]),
        (None, "Valid rationale", ["Valid warning"]),
        (False, "Valid rationale", ["Valid warning"]),
        (42, "Valid rationale", ["Valid warning"]),
        ("pair-001", "", ["Valid warning"]),
        ("pair-001", "   ", ["Valid warning"]),
        ("pair-001", None, ["Valid warning"]),
        ("pair-001", False, ["Valid warning"]),
        ("pair-001", 42, ["Valid warning"]),
        ("pair-001", "Valid rationale", [""]),
        ("pair-001", "Valid rationale", ["   "]),
        ("pair-001", "Valid rationale", ["Duplicate", " Duplicate "]),
    ],
)
def test_judgment_rejects_invalid_identifiers_rationales_and_warnings(
    *, pair_id: Any, rationale: Any, warnings: list[Any]
) -> None:
    """Judgment text identifiers, rationales, and warnings fail closed.

    Parameters
    ----------
    pair_id
        Valid or invalid pair identifier.
    rationale
        Valid or invalid rationale.
    warnings
        Valid or invalid warning collection.
    """

    with pytest.raises(expected_exception=_INVALID_STRING_EXCEPTIONS):
        LPPairJudgment.model_validate(
            _valid_judgment_payload(
                pair_id=pair_id, rationale=rationale, warnings=warnings
            )
        )


def test_lp_models_reject_unknown_fields() -> None:
    """All new LP records inherit the shared forbidden-extra-field boundary."""

    cases: list[tuple[type[BaseModel], dict[str, Any]]] = [
        (
            LPAdmissibleDecision,
            {
                "decision": "relatesTo",
                "direction": None,
                "unknown": "forbidden",
            },
        ),
        (
            LPCandidateEvidence,
            {
                **_valid_evidence_payload(),
                "unknown": "forbidden",
            },
        ),
        (
            LPCandidatePair,
            {
                **_valid_candidate_payload(),
                "unknown": "forbidden",
            },
        ),
        (
            LPPairJudgment,
            {
                **_valid_judgment_payload(),
                "unknown": "forbidden",
            },
        ),
    ]

    for model_type, payload in cases:
        with pytest.raises(expected_exception=ValidationError) as exc_info:
            model_type.model_validate(payload)

        assert any(
            error["loc"] == ("unknown",) and error["type"] == "extra_forbidden"
            for error in exc_info.value.errors()
        )


def test_lp_models_round_trip_without_material_changes() -> None:
    """Every LP schema validates its own JSON serialization without value drift."""

    evidence_payload = _valid_evidence_payload(
        nominated_relationships=["buildsTowards", "relatesTo"],
        references=["lc-001", "sfi-source-002"],
        triggering_values={
            "features": ["shared action", {"rank_distance": 1}],
            "same_domain": True,
        },
    )
    candidate_payload = _valid_candidate_payload(
        admissible_decisions=[
            {"decision": "buildsTowards", "direction": "first_to_second"},
            {"decision": "relatesTo"},
            {"decision": "no_relation"},
            {"decision": "needs_review"},
        ],
        evidence=[evidence_payload],
    )
    models = [
        LPAdmissibleDecision.model_validate(
            {"decision": "buildsTowards", "direction": "second_to_first"}
        ),
        LPCandidateEvidence.model_validate(evidence_payload),
        LPCandidatePair.model_validate(candidate_payload),
        LPPairJudgment.model_validate(
            _valid_judgment_payload(
                confidence=1.0,
                decision="needs_review",
                direction=None,
                warnings=["Ambiguous curricular evidence."],
            )
        ),
    ]

    for model in models:
        reparsed = type(model).model_validate_json(model.model_dump_json())

        assert reparsed.model_dump(mode="json") == model.model_dump(mode="json")


def test_removed_dormant_progression_models_are_not_exported() -> None:
    """The superseded dormant edge and response schemas are fully replaced."""

    assert not hasattr(kg_schemas, "ProgressionEdge")
    assert not hasattr(kg_schemas, "ProgressionEdgesResponse")
