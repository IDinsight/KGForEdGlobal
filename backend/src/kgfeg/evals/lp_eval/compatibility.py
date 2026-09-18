"""Strict, read-only interpretations of completed historical LP snapshots.

These readers never migrate artifacts or authorize production checkpoint reuse.
Projection and execution formats are independent; all comparisons retain JSON types.
"""

# Standard Library
from typing import Any, ClassVar, Literal

# Third Party Library
from pydantic import Field

# Package Library
from kgfeg.kgs.lp_checkpoints import _LPFailure
from kgfeg.kgs.lp_requests import canonical_lp_json
from kgfeg.kgs.schemas import AcademicStandardsLCLPKGBundle
from kgfeg.schemas import CreateKGConfig, _CreateKGLearningProgressionsConfig

_FLAT_ALIASES = {
    "academic_subject": "academicSubject",
    "adoption_status": "adoptionStatus",
    "alternate_statement_code": "alternateStatementCode",
    "attribution_statement": "attributionStatement",
    "case_identifier_uri": "caseIdentifierURI",
    "case_identifier_uuid": "caseIdentifierUUID",
    "date_created": "dateCreated",
    "date_modified": "dateModified",
    "entity_type": "entityType",
    "grade_level": "gradeLevel",
    "in_language": "inLanguage",
    "is_current": "isCurrent",
    "normalized_statement_type": "normalizedStatementType",
    "relationship_type": "relationshipType",
    "source_entity": "sourceEntity",
    "source_entity_key": "sourceEntityKey",
    "source_entity_value": "sourceEntityValue",
    "statement_code": "statementCode",
    "statement_type": "statementType",
    "target_entity": "targetEntity",
    "target_entity_key": "targetEntityKey",
    "target_entity_value": "targetEntityValue",
}
ProjectionFormat = Literal[
    "historical_flat_snake_case",
    "historical_flat_camel_case",
    "learning_commons_wire",
]


class _CapturedHistoricalLPConfig(_CreateKGLearningProgressionsConfig):
    """Exact pre-concurrency schema; capacity is neither a field nor a default."""

    # Pydantic explicitly supports removing an inherited field with ClassVar.
    max_concurrent_requests: ClassVar[int]  # type: ignore[misc]


class _HistoricalKGConfig(CreateKGConfig):
    """Captured configuration validated without adding a concurrency policy."""

    learning_progressions: _CapturedHistoricalLPConfig = Field(alias="lp")


def _converted_flat(row: dict[str, Any]) -> dict[str, Any]:
    """Apply only the approved top-level aliases and endpoint-key enum mapping.

    Parameters
    ----------
    row
        Authoritative historical flat record, including unchanged nested metadata.

    Returns
    -------
    dict[str, Any]
        Exact expected converted record; no recursive renaming or field removal.
    """

    return {
        _FLAT_ALIASES.get(key, key): (
            "caseIdentifierUUID"
            if key in {"source_entity_key", "target_entity_key"}
            and value == "case_identifier_uuid"
            else value
        )
        for key, value in row.items()
    }


def read_captured_config(raw: dict[str, Any]) -> CreateKGConfig:
    """Validate an exact captured schema, selecting only by recorded capacity.

    Parameters
    ----------
    raw
        Complete recorded configuration, including the hash-bound overwrite flag.

    Returns
    -------
    CreateKGConfig
        Validated current or historical policy retaining its exact serialized shape.

    Raises
    ------
    ValueError
        A field is missing, unknown, coerced, defaulted or invalid.
    """

    if not isinstance(raw, dict) or not isinstance(raw.get("lp"), dict):
        raise ValueError("Captured configuration requires an LP namespace")

    if "max_concurrent_requests" in raw["lp"]:
        capacity = raw["lp"]["max_concurrent_requests"]

        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("Captured concurrency capacity must be a positive integer")

        config = CreateKGConfig.model_validate(raw)
    else:
        config = _HistoricalKGConfig.model_validate(raw)

    if canonical_lp_json(config.model_dump(mode="json")) != canonical_lp_json(raw):
        raise ValueError("Captured effective configuration changed during validation")

    return config


def read_snapshot_projections(
    *,
    bundle: AcademicStandardsLCLPKGBundle,
    edges: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    wire_records: tuple[list[dict[str, Any]], list[dict[str, Any]]],
) -> ProjectionFormat:
    """Reconcile exactly one complete projection contract against bundle authority.

    Parameters
    ----------
    bundle
        Independently authenticated combined graph with complete internal metadata.
    edges
        Actual decoded relationship rows, preserving order and JSON types.
    nodes
        Actual decoded node rows, preserving order and JSON types.
    wire_records
        Exact expected current wire records from the shared production serializer.

    Returns
    -------
    ProjectionFormat
        Unique matched interpretation; execution format plays no part in selection.

    Raises
    ------
    ValueError
        Neither or multiple contracts match, including mixed casing, dropped metadata,
        unknown fields, alias collisions, changed endpoints or reordered populations.
    """

    material = bundle.model_dump(mode="json")
    flat_nodes = [
        {**material["framework"], "entity_type": "StandardsFramework"},
        *(
            {**item, "entity_type": "StandardsFrameworkItem"}
            for item in sorted(
                material["items"], key=lambda item: item["case_identifier_uuid"]
            )
        ),
        *(
            {**item, "entity_type": "LearningComponent"}
            for item in sorted(
                material["learning_components"], key=lambda item: item["identifier"]
            )
        ),
    ]
    flat_edges = [
        row
        for name in (
            "relationships_has_child",
            "relationships_supports",
            "relationships_builds_towards",
            "relationships_relates_to",
        )
        for row in sorted(material[name], key=lambda row: row["identifier"])
    ]
    expected: dict[
        ProjectionFormat, tuple[list[dict[str, Any]], list[dict[str, Any]]]
    ] = {
        "historical_flat_snake_case": (flat_nodes, flat_edges),
        "historical_flat_camel_case": (
            [_converted_flat(row) for row in flat_nodes],
            [_converted_flat(row) for row in flat_edges],
        ),
        "learning_commons_wire": wire_records,
    }
    actual = canonical_lp_json([nodes, edges])
    matches = [
        name
        for name, records in expected.items()
        if actual == canonical_lp_json(list(records))
    ]

    if len(matches) != 1:
        raise ValueError(
            "Combined node projection/edge projection pair does not match exactly one supported bundle-derived format"
        )

    return matches[0]


def validate_historical_failures(
    *, failures: list[_LPFailure], retry_limits: dict[str, int]
) -> None:
    """Apply the historical serial failure ordering, retry and stop contracts.

    Parameters
    ----------
    failures
        Schema-validated failure rows; request and completion binding is checked by the
        snapshot reader separately.
    retry_limits
        Exact captured producer/checker retry limits.

    Raises
    ------
    ValueError
        Attempts have gaps, duplicates, invalid exhaustion, or continue after the
        exhausted failure that ended a historical serial execution run, or an exhausted
        failure lacks recovery in a later run, or failures for one request disagree
        about its recovery run across attempts, stages or execution runs.
    """

    seen: set[tuple[int, int, str, int]] = set()
    exhausted_runs: set[int] = set()
    previous_order = (0, -1, -1, 0)
    recovery_runs: dict[int, int | None] = {}

    for failure in failures:
        key = (
            failure.run_number,
            failure.request_index,
            failure.stage,
            failure.attempt,
        )
        predecessor = (*key[:3], failure.attempt - 1)
        order = (
            failure.run_number,
            failure.request_index,
            0 if failure.stage == "draft" else 1,
            failure.attempt,
        )
        maximum = retry_limits[failure.stage] + 1

        if (
            order <= previous_order
            or failure.run_number in exhausted_runs
            or failure.attempt > maximum
            or failure.exhausted != (failure.attempt == maximum)
            or key in seen
            or (failure.attempt > 1 and predecessor not in seen)
        ):
            raise ValueError("Historical failure order or retry contract differs")

        previous_order = order
        seen.add(key)

        # Appending the final response resolves every outstanding failure together.
        # Completed requests are skipped on resume, so all stages and attempts for one
        # request must share that single recovery run.
        recovery_run = recovery_runs.setdefault(
            failure.request_index, failure.resolved_run_number
        )

        if failure.resolved_run_number != recovery_run:
            raise ValueError(
                "Historical failures for one request disagree on the recovery run"
            )

        if failure.exhausted:
            if (
                failure.resolved_run_number is None
                or failure.resolved_run_number <= failure.run_number
            ):
                raise ValueError(
                    "Exhausted historical failure requires recovery in a later run"
                )

            exhausted_runs.add(failure.run_number)
