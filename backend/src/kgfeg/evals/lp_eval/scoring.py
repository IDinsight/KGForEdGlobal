"""Score frozen LP assessments and publish separate, immutable evaluation reports."""

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import json
import sqlite3

from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import asdict
from fractions import Fraction
from itertools import combinations
from pathlib import Path
from typing import Any

# Third Party Library
from pydantic import TypeAdapter

# Package Library
from kgfeg.evals.lp_eval.judge import (
    EvaluationSession,
    _MaterialWatch,
    _store_read,
    canonicalize_classification,
    open_evaluation_store,
)
from kgfeg.evals.lp_eval.presentation import render_evaluation_presentations
from kgfeg.evals.lp_eval.sampling import (
    _frozen_manifest,
    _frozen_output_boundary,
    _population_compatible_groups,
    build_admissible_population,
    load_frozen_lp_inputs,
    upstream_evidence_source,
)
from kgfeg.evals.lp_eval.schemas import (
    ClassificationJudgment,
    ConcernDisposition,
    EvaluationCache,
    EvaluationReport,
    EvaluationReportArtifacts,
    EvaluationSchedule,
    EvaluationStore,
    ReportInputs,
    ReportProvenance,
    ScheduledRequest,
)
from kgfeg.evals.lp_eval.utils import _publish_files, _validated_report_manifest
from kgfeg.kgs.lp_requests import canonical_lp_json, lp_material_content_hash

_LIMITATIONS = (
    "Production assertions and judge assessments are comparisons, not ground truth.",
    "Agreement and sample-conditioned coverage are not semantic precision or recall.",
    "Diagnostic oversamples are not population estimates; no weighted estimator is used.",
    "Replicates are repeated assessments of pairs, not independent curriculum samples.",
    "Confidence is uncalibrated self-report; no automatic semantic passing score applies.",
    "Controls test constructed expectations, not accuracy on real curriculum pairs.",
    "Evidence sensitivity does not identify the earliest defect or authorize production changes.",
    "Unknown usage remains unknown; token categories need not be additive.",
    "No uncertainty interval is estimated. All rates describe their listed sample members.",
    "This report describes only its selected inputs; it does not certify project completion.",
)


def _baseline_reports(
    *, inputs: ReportInputs, rows: list[dict[str, Any]], top_k: int
) -> list[dict[str, Any]]:
    """Compare constant negatives and deterministic lexical rankings with judgments.

    Parameters
    ----------
    inputs
        Exact upstream Jaccard scores for each real selected pair.
    rows
        All scheduled rows, including missing assessments.
    top_k
        Configured positive ranking cutoff, capped separately within each cohort.

    Returns
    -------
    list[dict[str, Any]]
        Rankings and per-condition/replicate comparisons without truth labels.

    Raises
    ------
    ValueError
        If score membership is missing, duplicated or outside the schedule.
    """

    scores = {
        (doc, component, pair): Fraction(num, den)
        for doc, component, pair, num, den in inputs.lexical_scores
    }
    expected = {
        (r["doc_key"], r["component"], r["pair_id"])
        for r in rows
        if r["component"] != "controls"
    }

    if len(scores) != len(inputs.lexical_scores) or set(scores) != expected:
        raise ValueError("Lexical scores do not exactly cover the real sampled pairs.")

    ranked: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    seen = set()

    for row in rows:
        key = (row["doc_key"], row["component"], row["pair_id"])

        if key not in scores or key in seen:
            continue

        seen.add(key)
        score = scores[key]
        ranked[key[:2]].append(
            {
                "endpoint_uuids": row["endpoint_uuids"],
                "pair_id": row["pair_id"],
                "jaccard": [score.numerator, score.denominator],
            }
        )

    for cohort in ranked.values():
        cohort.sort(key=lambda r: (-Fraction(*r["jaccard"]), r["endpoint_uuids"]))

    results = []

    for dimensions, members in _groups(rows=rows, strata=False):
        if (
            dimensions["component"] == "controls"
            or dimensions["task"] != "classification"
        ):
            continue

        order = ranked[(dimensions["doc_key"], dimensions["component"])]
        selected = {r["pair_id"] for r in order[:top_k]}
        valid = [r for r in members if r["assessment"] is not None]
        top = [r for r in valid if r["pair_id"] in selected]
        results.append(
            {
                "dimensions": dimensions,
                "ranking": order,
                "effective_top_k": min(top_k, len(order)),
                "constant_no_relation_agreement": _rate(
                    denominator=valid,
                    numerator=[
                        r for r in valid if r["assessment"]["decision"] == "no_relation"
                    ],
                ),
                "lexical_top_k_judge_positive": _rate(
                    denominator=top, numerator=[r for r in top if _positive(r)]
                ),
                "lexical_top_k_planned_request_ids": [
                    r["request_id"] for r in members if r["pair_id"] in selected
                ],
                "interpretation": "Cohort-wide lexical rank is neither a relationship nor a direction.",
            }
        )

    return results


def _comparison_kind(*, first: dict[str, Any], second: dict[str, Any]) -> str | None:
    """Identify required comparisons while keeping evidence conditions distinct.

    Parameters
    ----------
    first
        Earlier scheduled assessment of a pair/task.
    second
        Later scheduled assessment of the same pair/task.

    Returns
    -------
    str | None
        Comparison kind, or None for unrelated variant combinations.
    """

    if (first["condition"], first["presentation"]) == (
        second["condition"],
        second["presentation"],
    ):
        return "identical_repetition"

    roles = {first["role"], second["role"]}

    if "base" not in roles or "variant" not in roles:
        return None

    return (
        "presentation"
        if first["condition"] == second["condition"]
        else "evidence_condition"
    )


def _comparison_reports(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retain paired replicate and presentation/evidence differences without voting.

    Parameters
    ----------
    rows
        Full scheduled row population.

    Returns
    -------
    list[dict[str, Any]]
        Every required paired comparison, including unavailable comparisons.
    """

    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        groups[(row["doc_key"], row["component"], row["pair_id"], row["task"])].append(
            row
        )

    comparisons = []

    for members in groups.values():
        for first, second in combinations(members, 2):
            kind = _comparison_kind(first=first, second=second)

            if kind is None:
                continue

            available = (
                first["assessment"] is not None and second["assessment"] is not None
            )
            comparisons.append(
                {
                    "doc_key": first["doc_key"],
                    "component": first["component"],
                    "pair_id": first["pair_id"],
                    "kind": kind,
                    "first_request_id": first["request_id"],
                    "second_request_id": second["request_id"],
                    "first_condition": first["condition"],
                    "second_condition": second["condition"],
                    "first_presentation": first["presentation"],
                    "second_presentation": second["presentation"],
                    "first_replicate": first["replicate"],
                    "second_replicate": second["replicate"],
                    "first_outcome": _outcome(first),
                    "second_outcome": _outcome(second),
                    "changed": (
                        _outcome(first) != _outcome(second) if available else None
                    ),
                    "unavailable_reason": (
                        None
                        if available
                        else "One or both scheduled judgments are missing."
                    ),
                }
            )

    return comparisons


def _concern_groups(
    *, comparisons: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Group all disagreements and quality concerns with their exact memberships.

    Parameters
    ----------
    comparisons
        Paired stability and evidence assessments.
    rows
        Scored requests, with production comparisons kept outside judge responses.

    Returns
    -------
    list[dict[str, Any]]
        Stable groups requiring user disposition, never automatic semantic failure.
    """

    affected: dict[tuple[str, str], set[str]] = defaultdict(set)
    by_id = {row["request_id"]: row for row in rows}

    for row in rows:
        for category in _row_concerns(row):
            affected[(row["doc_key"], category)].add(row["request_id"])

    for comparison in comparisons:
        if comparison["changed"]:
            affected[
                (comparison["doc_key"], comparison["kind"] + "_instability")
            ].update((comparison["first_request_id"], comparison["second_request_id"]))

    groups = []

    for (doc, category), identifiers in sorted(affected.items()):
        members = [
            {
                key: by_id[identifier][key]
                for key in (
                    "request_id",
                    "pair_id",
                    "component",
                    "condition",
                    "presentation",
                    "replicate",
                )
            }
            for identifier in sorted(identifiers)
        ]
        material = {"category": category, "doc_key": doc, "members": members}
        groups.append(
            {
                **material,
                "concern_id": lp_material_content_hash(material),
                "disposition": "pending_user",
                "authority": "IDinsight project user",
            }
        )

    return groups


def _evidence_availability(request: ScheduledRequest) -> dict[str, Any]:
    """Count absent evidence separately from deliberate ablation and presentation.

    Parameters
    ----------
    request
        Frozen shown evidence and its construction audit.

    Returns
    -------
    dict[str, Any]
        Missing families for assessed endpoints plus explicit unchanged diagnostics.
    """

    audit = json.loads(request.evidence.audit_json)
    payload = json.loads(request.evidence.payload_json)
    payload = payload.get("original_request", payload)
    endpoints = {str(item) for item in request.canonical_endpoint_uuids}
    unavailable = []

    for index, endpoint in enumerate(payload.get("sfis", [])):
        if endpoint.get("context", endpoint).get("sfi_uuid") not in endpoints:
            continue

        for family in ("ancestor_paths", "learning_components", "source_evidence"):
            removed = (
                family == "learning_components"
                and request.evidence.condition == "lc_removed"
            ) or (
                family == "ancestor_paths"
                and request.evidence.condition == "hierarchy_removed"
            )

            if family in endpoint and not endpoint[family] and not removed:
                unavailable.append(f"/sfis/{index}/{family}")

        if endpoint.get("coordinate", {}).get("status") == "missing":
            unavailable.append(f"/sfis/{index}/coordinate")

    return {
        "unavailable_paths": unavailable,
        "unchanged_diagnostic": request.role == "variant"
        and bool(
            audit.get("base_audit", {}).get("unchanged_from_base")
            if request.component == "independent"
            and request.evidence.condition in {"lc_removed", "hierarchy_removed"}
            else audit.get("comparison_unchanged")
        ),
        "interpretation": "Absence and unchanged evidence do not establish good performance.",
    }


def _failure_rows(
    *, cache: EvaluationCache, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Keep failed attempts and currently missing work separate from quality concerns.

    Parameters
    ----------
    cache
        Validated append-only evidence.
    rows
        Complete scheduled membership and outcome state.

    Returns
    -------
    list[dict[str, Any]]
        Historical failures with their later resolution and unaccounted requests.
    """

    by_id = {row["request_id"]: row for row in rows}
    failures = []

    for event in cache.events:
        if event.event != "failed":
            continue

        failures.append(
            {
                **event.model_dump(mode="json"),
                "resolved_by_success": by_id[event.request_id]["assessment"]
                is not None,
            }
        )

    for row in rows:
        if row["assessment"] is None:
            failures.append(
                {
                    "request_id": row["request_id"],
                    "doc_key": row["doc_key"],
                    "component": row["component"],
                    "condition": row["condition"],
                    "event": "unaccounted_judgment",
                    "state": row["execution_state"],
                    "resolved_by_success": False,
                }
            )

    return failures


def _groups(
    *, rows: list[dict[str, Any]], strata: bool
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Partition counts by condition/replicate, retaining overlapping route strata.

    Parameters
    ----------
    rows
        Exact scheduled observations.
    strata
        Include route and diagnostic-tag cells in addition to each cohort union.

    Returns
    -------
    list[tuple[dict[str, Any], list[dict[str, Any]]]]
        Ordered groups. Overlapping strata must never be summed as a population.
    """

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    keys = (
        "doc_key",
        "component",
        "condition",
        "presentation",
        "replicate",
        "role",
        "task",
    )

    for row in rows:
        dimensions = {key: row[key] for key in keys}
        cells = [("selected_union", "all")]

        if strata:
            cells.extend(("route", value) for value in row["routes"])
            cells.extend(("tag", value) for value in row["tags"])

            if row["production"] is not None:
                cells.extend(
                    (
                        ("production_outcome", row["production"]["outcome"]),
                        ("checker_outcome", row["production"]["checker_outcome"]),
                        (
                            "producer_to_final_change",
                            str(bool(row["production"]["producer_to_final_changes"])),
                        ),
                    )
                )

            if row["control_family"] is not None:
                cells.append(("control_family", row["control_family"]))

        for kind, value in cells:
            groups[
                canonical_lp_json(
                    {**dimensions, "stratum_kind": kind, "stratum": value}
                )
            ].append(row)

    return [(json.loads(key), members) for key, members in sorted(groups.items())]


def _json(value: Any) -> str:
    """Serialize report material canonically with a trailing newline.

    Parameters
    ----------
    value
        JSON-compatible material.

    Returns
    -------
    str
        Stable UTF-8-ready text.
    """

    return canonical_lp_json(TypeAdapter(Any).dump_python(value, mode="json")) + "\n"


def _jsonl(rows: list[dict[str, Any]]) -> str:
    """Serialize one complete canonical object per line.

    Parameters
    ----------
    rows
        Ordered artifact records.

    Returns
    -------
    str
        Empty text for an empty population, otherwise newline-terminated records.
    """

    return "".join(_json(row) for row in rows)


def _markdown_metrics(report: dict[str, Any]) -> list[str]:
    """Summarize real base assessments without pooling strata or replicates.

    Parameters
    ----------
    report
        Complete scoring material.

    Returns
    -------
    list[str]
        Human-readable separate relationship, coverage and grounding tables.
    """

    lines = [
        "",
        "## Base assessments",
        "",
        "| Document | Component | Replicate | Valid/planned | Positive/valid | Agreement/valid production | Nominated/positive | Matching publication/positive |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for metric in report["metrics"]:
        dimensions = metric["dimensions"]

        if (
            dimensions["stratum_kind"] != "selected_union"
            or dimensions["role"] != "base"
        ):
            continue

        coverage = metric.get("sample_conditioned_coverage", {})
        lines.append(
            f"| {dimensions['doc_key']} | {dimensions['component']} | {dimensions['replicate']} | "
            f"{metric['valid']}/{metric['planned']} | {_markdown_rate(metric['judge_positive'])} | "
            f"{_markdown_rate(metric['production_agreement'])} | "
            f"{_markdown_rate(coverage.get('nominated'))} | "
            f"{_markdown_rate(coverage.get('published_matching_relation_direction'))} |"
        )

    lines.extend(
        [
            "",
            "## Original rationale grounding",
            "",
            "| Document | Replicate | Grounded | Partially grounded | Unsupported | Ambiguous | Valid/planned |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )

    for metric in report["metrics"]:
        dimensions = metric["dimensions"]

        if (
            dimensions["stratum_kind"] != "selected_union"
            or dimensions["role"] != "critique"
        ):
            continue

        categories = metric["rationale_grounding"]
        values = " | ".join(
            _markdown_rate(categories[key])
            for key in ("grounded", "partially_grounded", "unsupported", "ambiguous")
        )
        lines.append(
            f"| {dimensions['doc_key']} | {dimensions['replicate']} | {values} | {metric['valid']}/{metric['planned']} |"
        )

    lines.extend(
        [
            "",
            "## Constructed controls",
            "",
            "| Document | Family | Replicate | Expected/valid | Valid/planned |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
    )

    for metric in report["metrics"]:
        dimensions = metric["dimensions"]
        if dimensions["stratum_kind"] != "control_family":
            continue
        lines.append(
            f"| {dimensions['doc_key']} | {dimensions['stratum']} | {dimensions['replicate']} | "
            f"{_markdown_rate(metric['constructed_expectation_match'])} | {metric['valid']}/{metric['planned']} |"
        )

    return lines


def _markdown_operations(report: dict[str, Any]) -> list[str]:
    """Disclose shortfalls, stability and known/unknown usage in the readable report.

    Parameters
    ----------
    report
        Complete scoring material.

    Returns
    -------
    list[str]
        Operational and stability summaries with explicit denominators.
    """

    lines = [
        "",
        "## Sampling availability",
        "",
        "| Document | Route | Population | Selected/target | Shortfall |",
        "| --- | --- | ---: | ---: | ---: |",
    ]

    for curriculum in report["sampling"]:
        for cell in curriculum["cells"] + curriculum["diagnostic_cells"]:
            lines.append(
                f"| {curriculum['doc_key']} | {cell['route']} | {cell['population_count']} | "
                f"{len(cell['selected_pair_ids'])}/{cell['target']} | {cell['shortfall']} |"
            )

    lines.extend(
        [
            "",
            "## Stability comparisons",
            "",
            "| Document | Component | Kind | Changed/available | Unavailable |",
            "| --- | --- | --- | ---: | ---: |",
        ]
    )
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)

    for comparison in report["comparisons"]:
        groups[
            (comparison["doc_key"], comparison["component"], comparison["kind"])
        ].append(comparison)

    for (doc, component, kind), comparisons in sorted(groups.items()):
        available = [row for row in comparisons if row["changed"] is not None]
        changed = sum(row["changed"] for row in available)
        lines.append(
            f"| {doc} | {component} | {kind} | {changed}/{len(available)} | {len(comparisons) - len(available)} |"
        )

    usage = report["usage_summary"]
    lines.extend(
        [
            "",
            "## Usage",
            "",
            f"Actual attempts: {usage['attempts']}.",
            "",
            "| Counter | Known subtotal | Unknown attempts | Total |",
            "| --- | ---: | ---: | ---: |",
        ]
    )

    for field, values in usage["tokens"].items():
        lines.append(
            f"| {field} | {values['known_subtotal']} | {values['unknown_attempts']} | {values['total']} |"
        )

    lines.extend(
        [
            "",
            f"Observed costs by currency: `{canonical_lp_json(usage['cost'])}`.",
            "Null totals mean unavailable; known subtotals are not complete totals.",
            "",
            "All individual comparisons and baseline rankings remain in the JSON report.",
        ]
    )
    return lines


def _markdown_rate(rate: dict[str, Any] | None) -> str:
    """Show an honest raw ratio or an explicit unavailable marker.

    Parameters
    ----------
    rate
        Membership-based diagnostic, or an inapplicable metric.

    Returns
    -------
    str
        Numerator/denominator without an invented zero-denominator percentage.
    """

    if rate is None:
        return "not applicable"

    if not rate["denominator"]:
        return "unavailable (0 valid)"

    return f"{rate['numerator']}/{rate['denominator']}"


def _matches_production(row: dict[str, Any]) -> bool:
    """Compare canonical relation and direction without recoding production ambiguity.

    Parameters
    ----------
    row
        Classification with joined production metadata.

    Returns
    -------
    bool
        Exact agreement; needs_review remains distinct from evaluator ambiguous.
    """

    production = row["production"]
    return production is not None and _outcome(row) == (
        production["outcome"],
        production["direction"],
    )


def _metric_reports(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Report raw denominators, direction outcomes, support and grounding separately.

    Parameters
    ----------
    rows
        Every planned request with optional valid assessment.

    Returns
    -------
    list[dict[str, Any]]
        Stratified counts and explicit membership-based rates.
    """

    reports = []

    for dimensions, members in _groups(rows=rows, strata=True):
        valid = [r for r in members if r["assessment"] is not None]
        published = [
            r
            for r in valid
            if r["production"] is not None
            and r["production"]["published_relationship_uuid"] is not None
        ]
        report = {
            "dimensions": dimensions,
            "planned": len(members),
            "attempted": sum(r["attempts"] > 0 for r in members),
            "attempts": sum(r["attempts"] for r in members),
            "valid": len(valid),
            "failed_attempts": sum(r["failed_attempts"] for r in members),
            "missing": len(members) - len(valid),
            "unavailable_evidence_requests": sum(
                bool(r["evidence_availability"]["unavailable_paths"]) for r in members
            ),
            "unchanged_diagnostic_requests": sum(
                r["evidence_availability"]["unchanged_diagnostic"] for r in members
            ),
            "ambiguous": sum(_outcome(r)[0] == "ambiguous" for r in valid),
            "planned_request_ids": [r["request_id"] for r in members],
            "outcomes": dict(
                sorted(Counter(canonical_lp_json(_outcome(r)) for r in valid).items())
            ),
        }

        if dimensions["task"] == "classification":
            report["judge_positive"] = _rate(
                denominator=valid, numerator=[r for r in valid if _positive(r)]
            )
            production = [r for r in valid if r["production"] is not None]
            report["production_agreement"] = _rate(
                denominator=production,
                numerator=[r for r in production if _matches_production(r)],
            )
            report["published_relation_direction_support"] = _rate(
                denominator=published,
                numerator=[r for r in published if _matches_production(r)],
            )
            if dimensions["component"] == "independent":
                report["sample_conditioned_coverage"] = _nomination_coverage(valid)
        else:
            report["rationale_grounding"] = {
                outcome: _rate(
                    denominator=valid,
                    numerator=[
                        r for r in valid if r["assessment"]["grounding"] == outcome
                    ],
                )
                for outcome in (
                    "grounded",
                    "partially_grounded",
                    "unsupported",
                    "ambiguous",
                )
            }

        if dimensions["component"] == "controls":
            report["constructed_expectation_match"] = _rate(
                denominator=valid, numerator=[r for r in valid if r["control_match"]]
            )

        reports.append(report)

    return reports


def _nomination_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Use only this cohort/condition/replicate's valid judge-positive denominator.

    Parameters
    ----------
    rows
        Independent valid classifications from one evidence-condition group.

    Returns
    -------
    dict[str, Any]
        Common denominator and disjoint nomination/publication categories.
    """

    positive = [row for row in rows if _positive(row)]
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in positive:
        production = row["production"]

        if production is None:
            category = "never_nominated"
        elif production["outcome"] in {"no_relation", "needs_review"}:
            category = "nominated_" + production["outcome"]
        elif _matches_production(row):
            category = "published_matching_relation_direction"
        else:
            category = "published_differing_relation_direction"

        categories[category].append(row)

    return {
        "interpretation": "Sample-conditioned diagnostic; not curriculum-wide candidate recall.",
        "nominated": _rate(
            denominator=positive,
            numerator=[r for r in positive if r["production"] is not None],
        ),
        **{
            category: _rate(denominator=positive, numerator=categories[category])
            for category in (
                "never_nominated",
                "nominated_no_relation",
                "nominated_needs_review",
                "published_matching_relation_direction",
                "published_differing_relation_direction",
            )
        },
    }


def _outcome(row: dict[str, Any]) -> tuple[str | None, str | None]:
    """Extract canonical classification or separate grounding category.

    Parameters
    ----------
    row
        Scheduled assessment, possibly unavailable.

    Returns
    -------
    tuple[str | None, str | None]
        Outcome and optional direction; missing output is never ambiguity.
    """

    assessment = row["assessment"]

    if assessment is None:
        return None, None

    return (
        (assessment["decision"], assessment["direction"])
        if row["task"] == "classification"
        else (assessment["grounding"], None)
    )


def _positive(row: dict[str, Any]) -> bool:
    """Identify a valid positive relationship assessment.

    Parameters
    ----------
    row
        Scheduled assessment.

    Returns
    -------
    bool
        True only for buildsTowards or relatesTo.
    """

    return _outcome(row)[0] in {"buildsTowards", "relatesTo"}


def _rate(
    *, denominator: list[dict[str, Any]], numerator: list[dict[str, Any]]
) -> dict[str, Any]:
    """Retain rate membership and an explicit reason for an unavailable denominator.

    Parameters
    ----------
    denominator
        Valid assessed requests defining this diagnostic.
    numerator
        Subset meeting the named comparison.

    Returns
    -------
    dict[str, Any]
        Raw counts, IDs and ratio; no synthetic percentage for an empty denominator.
    """

    return {
        "numerator": len(numerator),
        "denominator": len(denominator),
        "value": len(numerator) / len(denominator) if denominator else None,
        "unavailable_reason": (
            None if denominator else "No valid observations in this denominator."
        ),
        "numerator_request_ids": [row["request_id"] for row in numerator],
        "denominator_request_ids": [row["request_id"] for row in denominator],
    }


def _report_files(
    *,
    inputs: ReportInputs,
    provenance: ReportProvenance,
    report: EvaluationReport,
    schedule: EvaluationSchedule,
) -> dict[str, bytes]:
    """Render the complete required artifact set without changing execution evidence.

    Parameters
    ----------
    inputs
        Upstream population and baseline bindings.
    provenance
        Explicit development/evaluation evidence purpose.
    report
        Deterministic scored ledger.
    schedule
        Full validated frozen work plan.

    Returns
    -------
    dict[str, bytes]
        Required reports plus frozen inputs; the invocation retains its schedule.
    """

    material = json.loads(report.report_json)
    material["provenance"] = provenance.model_dump(mode="json")
    frozen = _frozen_manifest(schedule.inputs)
    material["discovery"] = TypeAdapter(type(frozen.inventory)).dump_python(
        frozen.inventory, mode="json"
    )
    material["excluded_runs"] = sum(
        run.status != "completed_candidate" for run in frozen.inventory.runs
    )
    samples = []

    for curriculum in schedule.curricula:
        for plan in (curriculum.production_sample, curriculum.independent_sample):
            samples.append(
                {
                    "doc_key": curriculum.doc_key,
                    "component": plan.component,
                    "plan": TypeAdapter(type(plan)).dump_python(plan, mode="json"),
                    "correction_audits": (
                        dict(curriculum.correction_audits)
                        if plan.component == "production"
                        else {}
                    ),
                }
            )

        samples.append(
            {
                "doc_key": curriculum.doc_key,
                "component": "controls",
                "controls": TypeAdapter(Any).dump_python(
                    curriculum.controls, mode="json"
                ),
                "diagnostic_cells": TypeAdapter(Any).dump_python(
                    curriculum.diagnostic_cells, mode="json"
                ),
            }
        )

    schedule_material = TypeAdapter(EvaluationSchedule).dump_python(
        schedule, mode="json"
    )
    requests = [
        {"doc_key": curriculum["doc_key"], **request}
        for curriculum in schedule_material["curricula"]
        for request in curriculum["requests"]
    ]
    text_files = {
        "lp_eval_population.json": inputs.population_json,
        "lp_eval_sample.jsonl": _jsonl(samples),
        "lp_eval_requests.jsonl": _jsonl(requests),
        "lp_eval_judgments.jsonl": report.judgments_jsonl,
        "lp_eval_failures.jsonl": report.failures_jsonl,
        "lp_eval_usage.json": report.usage_json,
        "lp_eval_report.json": _json(material),
        "lp_eval_report.md": render_evaluation_markdown(material),
        "lp_eval_inputs.json": _json(
            TypeAdapter(type(frozen)).dump_python(frozen, mode="json")
        ),
    }
    return {name: payload.encode("utf-8") for name, payload in text_files.items()}


def _row_concerns(row: dict[str, Any]) -> list[str]:
    """Identify disclosed quality concerns without imposing a passing threshold.

    Parameters
    ----------
    row
        Valid or missing assessment with isolated comparison metadata.

    Returns
    -------
    list[str]
        All applicable categories; execution failures are excluded.
    """

    if row["assessment"] is None:
        return []

    if row["component"] == "controls":
        return [] if row["control_match"] else ["synthetic_expectation_disagreement"]

    if row["task"] == "critique":
        return (
            []
            if _outcome(row)[0] == "grounded"
            else ["rationale_" + str(_outcome(row)[0])]
        )

    concerns = []

    if row["production"] is not None and not _matches_production(row):
        concerns.append("production_judge_disagreement")

    if (
        row["component"] == "independent"
        and _positive(row)
        and not _matches_production(row)
    ):
        concerns.append("sampled_judge_positive_without_matching_publication")

    return concerns


def _rows(
    *, cache: EvaluationCache, schedule: EvaluationSchedule
) -> list[dict[str, Any]]:
    """Join frozen assessments to production only after their evidence is fixed.

    Parameters
    ----------
    cache
        Validated attempts and responses.
    schedule
        Frozen selection, views, controls and complete requests.

    Returns
    -------
    list[dict[str, Any]]
        One row per scheduled call, preserving absent responses and canonical direction.
    """

    judgments = {item.request_id: item for item in cache.judgments}
    histories: dict[str, list[Any]] = defaultdict(list)

    for event in cache.events:
        histories[event.request_id].append(event)

    rows = []

    for curriculum in schedule.curricula:
        production = {
            item.pair.pair_id: asdict(item)
            for item in curriculum.production_population.pairs
        }
        controls = {item.pair_id: item for item in curriculum.controls}
        selected = {
            (plan.component, item.pair.pair_id): item
            for plan in (curriculum.production_sample, curriculum.independent_sample)
            for item in plan.pairs
        }

        for request in curriculum.requests:
            identifier = request.prompt.request_id
            judgment = judgments.get(identifier)
            canonical = (
                canonicalize_classification(judgment=judgment, request=request)
                if isinstance(judgment, ClassificationJudgment)
                else judgment
            )
            assessment = canonical.model_dump(mode="json") if canonical else None
            history = histories[identifier]
            pair = selected.get((request.component, request.evidence.pair_id))
            control = controls.get(request.evidence.pair_id)
            expected = json.loads(control.expectation_json) if control else None
            rows.append(
                {
                    "doc_key": curriculum.doc_key,
                    "framework_uuid": str(curriculum.framework_uuid),
                    "request_id": identifier,
                    "pair_id": request.evidence.pair_id,
                    "endpoint_uuids": [
                        str(value) for value in request.canonical_endpoint_uuids
                    ],
                    "component": request.component,
                    "task": request.prompt.task,
                    "condition": request.evidence.condition,
                    "presentation": request.evidence.presentation,
                    "replicate": request.replicate,
                    "role": request.role,
                    "routes": list(pair.routes) if pair else [],
                    "tags": list(pair.tags) if pair else [],
                    "evidence_content_hash": request.evidence.material_content_hash,
                    "evidence_audit": json.loads(request.evidence.audit_json),
                    "evidence_availability": _evidence_availability(request),
                    "production": production.get(request.evidence.pair_id),
                    "assessment": assessment,
                    "displayed_assessment": (
                        judgment.model_dump(mode="json") if judgment else None
                    ),
                    "attempts": sum(event.event == "started" for event in history),
                    "failed_attempts": sum(
                        event.event == "failed" for event in history
                    ),
                    "execution_state": history[-1].event if history else "unattempted",
                    "control_family": control.family if control else None,
                    "control_expectation": expected,
                    "control_match": (
                        all(
                            assessment.get(key) == value
                            for key, value in expected.items()
                        )
                        if expected is not None and assessment is not None
                        else None
                    ),
                }
            )

    return rows


def _sampling_reports(schedule: EvaluationSchedule) -> list[dict[str, Any]]:
    """Retain empty cells, exhaustion, probabilities and overlapping selection routes.

    Parameters
    ----------
    schedule
        Frozen complete sample plans.

    Returns
    -------
    list[dict[str, Any]]
        Curriculum-specific finite selection accounting.
    """

    results = []

    for curriculum in schedule.curricula:
        production = {item.pair.pair_id for item in curriculum.production_sample.pairs}
        independent = {
            item.pair.pair_id for item in curriculum.independent_sample.pairs
        }
        results.append(
            {
                "doc_key": curriculum.doc_key,
                "overlap_pair_ids": sorted(production & independent),
                "production_selected_pairs": len(production),
                "independent_selected_pairs": len(independent),
                "cells": [
                    asdict(cell)
                    for plan in (
                        curriculum.production_sample,
                        curriculum.independent_sample,
                    )
                    for cell in plan.cells
                ],
                "diagnostic_cells": [
                    asdict(cell) for cell in curriculum.diagnostic_cells
                ],
                "interpretation": "Empty and exhausted cells are unavailable comparisons, not evidence of performance.",
            }
        )

    return results


def _usage_reports(
    *, cache: EvaluationCache, rows: list[dict[str, Any]], schedule: EvaluationSchedule
) -> dict[str, Any]:
    """Account for every actual attempt once, including missing terminal usage.

    Parameters
    ----------
    cache
        Complete validated event history.
    rows
        Request-to-curriculum and condition bindings.
    schedule
        Requested model/settings identity.

    Returns
    -------
    dict[str, Any]
        Attempt rows plus grouped known subtotals and unknown counts.
    """

    by_id = {row["request_id"]: row for row in rows}
    terminals = {
        (e.request_id, e.attempt_number): e
        for e in cache.events
        if e.event != "started"
    }
    attempts = []
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for event in cache.events:
        if event.event != "started":
            continue

        terminal = terminals.get((event.request_id, event.attempt_number))
        row = by_id[event.request_id]
        usage = (
            terminal.usage.model_dump(mode="json")
            if terminal and terminal.usage
            else {}
        )
        dimensions = {
            **{key: row[key] for key in ("doc_key", "component", "condition")},
            "requested_model": schedule.judge.model,
            "observed_model": usage.get("provider_model"),
            "attempt_kind": "initial" if event.attempt_number == 1 else "retry",
            "outcome": terminal.event if terminal else "unfinished",
        }
        attempt = {
            **dimensions,
            "request_id": event.request_id,
            "attempt_number": event.attempt_number,
            "usage": usage,
            "started_at": event.timestamp.isoformat(),
            "finished_at": terminal.timestamp.isoformat() if terminal else None,
        }
        attempts.append(attempt)
        groups[canonical_lp_json(dimensions)].append(attempt)

    return {
        "attempts": attempts,
        "totals": _usage_totals(attempts),
        "groups": [
            {"dimensions": json.loads(key), **_usage_totals(value)}
            for key, value in sorted(groups.items())
        ],
        "events": [event.model_dump(mode="json") for event in cache.events],
        "interpretation": "Known subtotals are not totals when any attempt has unknown usage.",
    }


def _usage_totals(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum only observed compatible accounting and disclose missing counters.

    Parameters
    ----------
    attempts
        Distinct attempts, never duplicated start/terminal event rows.

    Returns
    -------
    dict[str, Any]
        Token category and currency totals with explicit unknown counts.
    """

    counters = {}

    for field in (
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    ):
        known = [
            a["usage"][field] for a in attempts if a["usage"].get(field) is not None
        ]
        counters[field] = {
            "known_subtotal": sum(known) if known else None,
            "unknown_attempts": len(attempts) - len(known),
            "total": sum(known) if len(known) == len(attempts) else None,
        }

    costs: dict[str, list[float]] = defaultdict(list)

    for attempt in attempts:
        usage = attempt["usage"]

        if usage.get("cost") is not None:
            costs[usage["cost_currency"]].append(usage["cost"])

    unknown = sum(a["usage"].get("cost") is None for a in attempts)
    return {
        "attempts": len(attempts),
        "tokens": counters,
        "cost": {
            "known_subtotals_by_currency": {
                currency: sum(values) for currency, values in sorted(costs.items())
            },
            "unknown_attempts": unknown,
            "total_by_currency": (
                {currency: sum(values) for currency, values in sorted(costs.items())}
                if not unknown
                else None
            ),
        },
    }


def _validate_cache(*, cache: EvaluationCache, schedule: EvaluationSchedule) -> None:
    """Replay scheduled event integrity before any response enters scoring.

    Parameters
    ----------
    cache
        Potentially caller-supplied offline or persisted cache.
    schedule
        Corresponding frozen work plan.

    Raises
    ------
    ValueError
        If replay, response membership, ordering or unfinished evidence differs.
    """

    with closing(sqlite3.connect(":memory:")) as connection:
        replay = EvaluationSession(
            connection=connection, events=cache.events, schedule=schedule
        ).snapshot()

    if replay != cache:
        raise ValueError("Scoring cache differs from its validated event history.")


def prepare_report_inputs(schedule: EvaluationSchedule) -> ReportInputs:
    """Revalidate frozen upstream populations and compute lexical scores without calls.

    Parameters
    ----------
    schedule
        Exact frozen invocation; no rediscovery or new selection is performed.

    Returns
    -------
    ReportInputs
        Compact membership encoding and full-SFI-text token Jaccard comparisons.

    Raises
    ------
    ValueError
        If a selected population differs from its frozen sample binding.
    """

    snapshots = {
        snapshot.run.doc_key: snapshot
        for snapshot in load_frozen_lp_inputs(schedule.inputs)
    }
    populations = []
    scores = []

    for curriculum in schedule.curricula:
        source = upstream_evidence_source(snapshots[curriculum.doc_key])
        population = build_admissible_population(source)

        if (
            population.material_content_hash
            != curriculum.independent_sample.population_content_hash
        ):
            raise ValueError("Report population differs from the frozen sample.")

        groups = population._groups
        eligible = set(population.endpoint_uuids)
        allowed = [
            _population_compatible_groups(
                builder=population._builder, first=group[0], groups=groups
            )
            for group in groups
        ]
        populations.append(
            {
                "doc_key": curriculum.doc_key,
                "framework_uuid": str(curriculum.framework_uuid),
                "population_content_hash": population.material_content_hash,
                "input_content_hash": population.input_content_hash,
                "eligible_endpoint_uuids": [
                    str(item) for item in population.endpoint_uuids
                ],
                "endpoint_groups": [[str(item) for item in group] for group in groups],
                "compatible_group_indices": allowed,
                "total_admissible_pairs": population.total_pairs,
                "membership_rule": "Distinct canonical UUID pairs from compatible groups; emit only first UUID < second UUID.",
                "policy_config_json": source.config_json,
                "upstream_artifact": asdict(source.upstream_artifact.fingerprint),
                "excluded_endpoints": [
                    {
                        "endpoint_uuid": str(identifier),
                        "eligibility": record.model_dump(mode="json"),
                    }
                    for identifier, record in population._builder._records.items()
                    if identifier not in eligible
                ],
                "production_population": TypeAdapter(
                    type(curriculum.production_population)
                ).dump_python(curriculum.production_population, mode="json"),
            }
        )

        for plan in (curriculum.production_sample, curriculum.independent_sample):
            for selected in plan.pairs:
                first, second = (
                    population._features[item].tokens
                    for item in selected.pair.endpoint_uuids
                )
                score = (
                    Fraction(len(first & second), len(first | second))
                    if first | second
                    else Fraction(0)
                )
                scores.append(
                    (
                        curriculum.doc_key,
                        plan.component,
                        selected.pair.pair_id,
                        score.numerator,
                        score.denominator,
                    )
                )

    return ReportInputs(
        lexical_scores=tuple(scores),
        population_json=_json(populations),
        schedule_content_hash=schedule.material_content_hash,
    )


def record_concern_dispositions(
    *,
    dispositions: tuple[ConcernDisposition, ...],
    reference: EvaluationReportArtifacts,
) -> Path:
    """Publish user-supplied dispositions against immutable report hashes.

    This records supplied decisions; it never invents acknowledgment, waives an
    execution failure or declares project completion.

    Parameters
    ----------
    dispositions
        One record per addressed concern; partial groups remain visibly pending.
    reference
        Exact published report generation to which the decisions apply.

    Returns
    -------
    Path
        Content-addressed disposition generation manifest.

    Raises
    ------
    ValueError
        If hashes, identities, authority, duplicate groups or report files differ.
    """

    manifest = _validated_report_manifest(reference)
    report = json.loads(_store_read(reference.directory / "lp_eval_report.json"))
    concerns = {group["concern_id"]: group for group in report["concerns"]}
    records = [
        ConcernDisposition.model_validate_json(item.model_dump_json())
        for item in dispositions
    ]
    ids = [item.concern_id for item in records]

    if len(set(ids)) != len(ids) or set(ids) - set(concerns):
        raise ValueError("Disposition groups must be unique and belong to this report.")

    files = manifest["files"]

    for record in records:
        if (
            record.report_json_sha256 != files["lp_eval_report.json"]["sha256"]
            or record.report_markdown_sha256 != files["lp_eval_report.md"]["sha256"]
        ):
            raise ValueError("Disposition report hashes differ.")

    pending = sorted(set(concerns) - set(ids))
    material = {
        "report_manifest_sha256": reference.manifest_sha256,
        "records": [
            {
                "disposition": record.model_dump(mode="json"),
                "concern": concerns[record.concern_id],
            }
            for record in sorted(records, key=lambda item: item.concern_id)
        ],
        "pending_concern_ids": pending,
        "all_concerns_dispositioned": not pending,
        "execution_complete": report["execution_complete"],
        "interpretation": "Disposition does not waive missing judgments, failed execution or independent review.",
    }
    payload = _json(material).encode("utf-8")
    path = (
        reference.directory.parent.parent
        / "dispositions"
        / hashlib.sha256(payload).hexdigest()
    )
    _publish_files(directory=path, files={"lp_eval_dispositions.json": payload})
    return path / "lp_eval_dispositions.json"


def render_evaluation_markdown(report: dict[str, Any]) -> str:
    """Render a readable companion retaining explicit status and artifact pointers.

    Parameters
    ----------
    report
        Complete scored JSON material with optional execution provenance.

    Returns
    -------
    str
        Deterministic Markdown, never a semantic pass/fail certificate.
    """

    lines = [
        "# Learning Progressions evaluation",
        "",
        f"Execution: **{'complete' if report['execution_complete'] else 'incomplete'}**.",
        f"Evidence: **{report.get('provenance', {}).get('evidence_kind', 'development')}**.",
        "",
        f"Selected curricula: {len(report['curricula'])}. "
        f"Valid judgments: {report['valid_judgments']}/{report['planned_judgments']}. "
        f"Concern groups awaiting disposition: {len(report['concerns'])}.",
        "",
        "## Selected curricula",
        "",
        "| Document | Planned | Valid | Missing |",
        "| --- | ---: | ---: | ---: |",
    ]

    for curriculum in report["curricula"]:
        lines.append(
            f"| {curriculum['doc_key']} | {curriculum['planned']} | {curriculum['valid']} | {curriculum['missing']} |"
        )

    lines.extend(_markdown_metrics(report))
    lines.extend(_markdown_operations(report))
    lines.extend(
        [
            "",
            "## Reading the results",
            "",
            "The JSON report retains raw numerators, denominators and request IDs by curriculum, "
            "cohort, stratum, condition, presentation and replicate. Relationship support and "
            "rationale grounding are separate. Independent nomination coverage uses only the "
            "judge-positive denominator in the named common evidence condition.",
            "",
            "See [complete report](lp_eval_report.json), [judgments](lp_eval_judgments.jsonl), "
            "[failures](lp_eval_failures.jsonl), [usage](lp_eval_usage.json), "
            "[populations](lp_eval_population.json), [samples](lp_eval_sample.jsonl) and "
            "[requests](lp_eval_requests.jsonl). Every replicate and disagreement is retained.",
            "",
            "## Concern groups",
            "",
        ]
    )

    if not report["concerns"]:
        lines.append(
            "No concern groups were detected in available judgments. Missing work still prevents execution completion."
        )

    for concern in report["concerns"]:
        lines.append(
            f"- `{concern['concern_id']}`: {concern['category']} "
            f"({concern['doc_key']}, {len(concern['members'])} affected requests); pending user disposition."
        )

    lines.extend(
        ["", "## Limits", "", *("- " + limit for limit in report["limitations"]), ""]
    )
    return "\n".join(lines)


def score_evaluation(
    *,
    cache: EvaluationCache,
    execution_errors: tuple[str, ...] = (),
    inputs: ReportInputs,
    schedule: EvaluationSchedule,
) -> EvaluationReport:
    """Score a validated frozen schedule without file writes or model calls.

    Parameters
    ----------
    cache
        Exact successful judgments and complete attempt history.
    execution_errors
        Sanitized invocation-level failures, separate from semantic concerns.
    inputs
        Upstream population and lexical material bound to this schedule.
    schedule
        Full immutable work plan, including unavailable scheduled judgments.

    Returns
    -------
    EvaluationReport
        Deterministic report material, clearly incomplete when execution is incomplete.

    Raises
    ------
    ValueError
        If input, schedule or cache identities fail reconciliation.
    """

    if not schedule.curricula or not schedule.total_requests:
        raise ValueError("A report requires a nonempty frozen evaluation schedule.")

    if inputs.schedule_content_hash != schedule.material_content_hash:
        raise ValueError("Report inputs belong to another schedule.")

    _validate_cache(cache=cache, schedule=schedule)
    rows = _rows(cache=cache, schedule=schedule)

    if len(rows) != schedule.total_requests or len(
        {row["request_id"] for row in rows}
    ) != len(rows):
        raise ValueError("Report schedule contains duplicate or unaccounted requests.")

    comparisons = _comparison_reports(rows)
    failures = _failure_rows(cache=cache, rows=rows)
    cache_hash = lp_material_content_hash(
        TypeAdapter(EvaluationCache).dump_python(cache, mode="json")
    )
    scorer_hash = hashlib.sha256(_store_read(Path(__file__).resolve())).hexdigest()
    usage = _usage_reports(cache=cache, rows=rows, schedule=schedule)
    valid = sum(row["assessment"] is not None for row in rows)
    material = {
        "schedule_content_hash": schedule.material_content_hash,
        "cache_content_hash": cache_hash,
        "scorer_sha256": scorer_hash,
        "report_inputs_content_hash": lp_material_content_hash(asdict(inputs)),
        "settings": TypeAdapter(type(schedule.settings)).dump_python(
            schedule.settings, mode="json"
        ),
        "judge": asdict(schedule.judge),
        "planned_judgments": len(rows),
        "valid_judgments": valid,
        "missing_judgments": len(rows) - valid,
        "execution_complete": valid == len(rows) and not execution_errors,
        "execution_errors": list(execution_errors),
        "curricula": [
            {
                "doc_key": curriculum.doc_key,
                "framework_uuid": str(curriculum.framework_uuid),
                "planned": len(curriculum.requests),
                "valid": sum(
                    r["assessment"] is not None
                    for r in rows
                    if r["doc_key"] == curriculum.doc_key
                ),
                "missing": sum(
                    r["assessment"] is None
                    for r in rows
                    if r["doc_key"] == curriculum.doc_key
                ),
            }
            for curriculum in schedule.curricula
        ],
        "sampling": _sampling_reports(schedule),
        "metrics": _metric_reports(rows),
        "comparisons": comparisons,
        "assessments": rows,
        "baselines": _baseline_reports(
            inputs=inputs,
            rows=rows,
            top_k=schedule.settings.settings.lexical_baseline_top_k,
        ),
        "concerns": _concern_groups(comparisons=comparisons, rows=rows),
        "limitations": list(_LIMITATIONS),
        "usage_summary": usage["totals"],
    }
    return EvaluationReport(
        cache_content_hash=cache_hash,
        failures_jsonl=_jsonl(failures),
        judgments_jsonl=_jsonl([row for row in rows if row["assessment"] is not None]),
        report_json=_json(material),
        schedule_content_hash=schedule.material_content_hash,
        scorer_sha256=scorer_hash,
        usage_json=_json(usage),
    )


def write_evaluation_reports(
    *,
    execution_errors: tuple[str, ...] = (),
    provenance: ReportProvenance,
    reference: EvaluationStore,
) -> EvaluationReportArtifacts:
    """Validate, score and atomically publish a new immutable report generation.

    Parameters
    ----------
    execution_errors
        Sanitized preflight/dispatch failure messages, if execution stopped.
    provenance
        Explicit development/evaluation evidence kind.
    reference
        Exact frozen invocation; cached judgments are revalidated without calls.

    Returns
    -------
    EvaluationReportArtifacts
        Pinned report manifest; identical regeneration reuses identical bytes.

    Raises
    ------
    ValueError
        If frozen inputs, cache, output containment or existing reports differ.
    """

    with open_evaluation_store(reference) as session:
        watch = _MaterialWatch(reference=reference, schedule=session.schedule)
        inputs = prepare_report_inputs(session.schedule)
        report = score_evaluation(
            cache=session.snapshot(),
            execution_errors=execution_errors,
            inputs=inputs,
            schedule=session.schedule,
        )
        files = _report_files(
            inputs=inputs,
            provenance=provenance,
            report=report,
            schedule=session.schedule,
        )
        manifest = {
            "kind": "lp_evaluation_report",
            "invocation": asdict(reference),
            "inputs": asdict(session.schedule.inputs),
            "schedule_content_hash": report.schedule_content_hash,
            "cache_content_hash": report.cache_content_hash,
            "scorer_sha256": report.scorer_sha256,
            "implementation_fingerprints": TypeAdapter(Any).dump_python(
                session.schedule.implementation_fingerprints, mode="json"
            ),
            "judge": asdict(session.schedule.judge),
            "settings": TypeAdapter(Any).dump_python(
                session.schedule.settings, mode="json"
            ),
            "provenance": provenance.model_dump(mode="json"),
            "files": {
                name: {
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size_bytes": len(payload),
                }
                for name, payload in sorted(files.items())
            },
        }
        manifest_bytes = _json(manifest).encode("utf-8")
        digest = hashlib.sha256(manifest_bytes).hexdigest()
        directory = reference.manifest_path.parent / "reports" / digest
        inventory = _frozen_manifest(session.schedule.inputs).inventory
        _frozen_output_boundary(inventory=inventory, output=directory)
        files["lp_eval_manifest.json"] = manifest_bytes
        watch.check()

        if (
            hashlib.sha256(_store_read(Path(__file__).resolve())).hexdigest()
            != report.scorer_sha256
        ):
            raise ValueError("Scoring implementation changed during report generation.")

        _publish_files(directory=directory, files=files)
        artifacts = EvaluationReportArtifacts(
            directory=directory, manifest_sha256=digest
        )

    render_evaluation_presentations(report_directory=artifacts.directory)
    return artifacts
