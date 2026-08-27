"""Schemas for the Learning Components evaluation."""

# Future Library
from __future__ import annotations

# Standard Library
from typing import Literal, Optional

# Third Party Library
from pydantic import Field

# Package Library
from kgfeg.schemas import BaseSchema

Corruption = Literal[
    "bundled_components",
    "collapsed_set",
    "cross_strand_swap",
    "forced_redundancy",
    "invented_specificity",
    "truncation",
    "under_decomposition",
]
Coverage = Literal["complete", "partial", "poor"]
EdgeDirection = Literal["component_to_standards", "standard_to_components"]
EdgeDistance = Literal["asserted", "distant", "near", "sibling"]
Faithfulness = Literal["extrapolated", "grounded", "unsupported"]
Granularity = Literal["appropriate", "too_coarse", "too_fine"]
Stratum = Literal["one_component", "three_plus_components", "two_components"]


class Ancestor(BaseSchema):
    """One ancestor level giving a standard its curricular context."""

    description: str = Field(description="Ancestor label text.")
    statement_type: str = Field(
        description="Source-facing statement type of the ancestor, e.g. `Strand`."
    )


class Component(BaseSchema):
    """One learning component as presented to the judge."""

    component_id: str = Field(
        description="Identifier used by judge verdicts to reference this component."
    )
    text: str = Field(description="Component text shown to the judge.")


class ComponentVerdict(BaseSchema):
    """Judge verdict for one component."""

    atomicity: bool = Field(
        description="True when the component describes exactly one skill."
    )
    component_id: str = Field(description="Identifier of the component being judged.")
    faithfulness: Faithfulness = Field(
        description=(
            "Whether every element of the component is supported by the standard and "
            "its ancestor path."
        )
    )
    well_formedness: bool = Field(
        description=(
            "True when the component is a complete, teachable skill rather than an "
            "activity, example, assessment task, or truncated fragment."
        )
    )


class ControlDetection(BaseSchema):
    """Detection outcome for one negative-control type."""

    corruption: str = Field(description="Corruption type.")
    detected: int = Field(description="Controls flagged on the targeted criterion.")
    detection_rate: float = Field(description="Detected divided by total.")
    targeted_criterion: str = Field(
        description="Criterion the corruption is designed to trip."
    )
    total: int = Field(description="Controls of this type that were judged.")


class CriterionScore(BaseSchema):
    """Aggregate score for one criterion over one slice of items."""

    ci_high: float = Field(description="Upper bound of the bootstrap interval.")
    ci_low: float = Field(description="Lower bound of the bootstrap interval.")
    criterion: str = Field(description="Criterion being scored.")
    n: int = Field(description="Items contributing to the score.")
    pass_rate: float = Field(
        description="Proportion of items taking the criterion's passing value."
    )
    sd: float = Field(
        default=0.0,
        description=(
            "Standard deviation of the pass rate across replicates. Zero when the "
            "evaluation ran once, since a single pass cannot show judge variability."
        ),
    )
    value_counts: dict[str, int] = Field(
        default_factory=dict, description="Count of each verdict value observed."
    )


class EdgeCandidate(BaseSchema):
    """One option offered in a discrimination item."""

    ancestor_path: list[Ancestor] = Field(
        default_factory=list,
        description=(
            "Curricular context for this candidate, empty when the candidate is a "
            "component."
        ),
    )
    candidate_id: str = Field(description="Identifier the judge selects by.")
    distance: EdgeDistance = Field(
        default="asserted",
        description=(
            "How far the distractor sits from the anchor in the framework. "
            "`distant` options act as negative controls: no sensible reading attaches "
            "them, so acceptance there means the judge is over-accepting and every "
            "precision figure is inflated."
        ),
    )
    is_true: bool = Field(
        description="Whether the pipeline actually asserts this pairing."
    )
    text: str = Field(description="Candidate text shown to the judge.")


class EdgeItem(BaseSchema):
    """One discrimination item: an anchor plus true options and hard negatives."""

    anchor_statement_type: str = Field(
        default="",
        description=(
            "Framework statement type of the anchor, empty when the anchor is a "
            "component."
        ),
    )
    anchor_text: str = Field(
        description="Standard text or component text the candidates are judged against."
    )
    ancestor_path: list[Ancestor] = Field(
        default_factory=list, description="Curricular context for the anchor."
    )
    candidates: list[EdgeCandidate] = Field(
        description="Options shown to the judge, true options mixed with distractors."
    )
    curriculum: str = Field(description="Curriculum the anchor belongs to.")
    direction: EdgeDirection = Field(description="Which way the item is posed.")
    item_id: str = Field(description="Deterministic identifier used to cache verdicts.")
    source_id: str = Field(description="Identifier of the anchor entity.")
    supports_multiple_standards: bool = Field(
        default=False,
        description=(
            "For component anchors, whether the component supports more than one "
            "standard. These arise from dedup merges, not from framework fan-in."
        ),
    )


class EdgeJudgement(BaseSchema):
    """One judge verdict for one discrimination item."""

    item_id: str = Field(description="Item the verdict belongs to.")
    judge_model: str = Field(description="Model that produced the verdict.")
    replicate: int = Field(
        default=0,
        description=(
            "Zero-based replicate index. Defaults to zero so verdicts written before "
            "replication was supported load as the first replicate."
        ),
    )
    selected_candidate_ids: list[str] = Field(
        default_factory=list, description="Candidates the judge accepted."
    )


class EdgeReport(BaseSchema):
    """Aggregate report for the bidirectional discrimination evaluation."""

    baseline_scores: dict[str, list[EdgeScore]] = Field(
        default_factory=dict,
        description=(
            "Scores per direction for deterministic lexical baselines that use no "
            "model at all. A judge that does not clearly beat these is measuring word "
            "overlap rather than pedagogical judgement."
        ),
    )

    distractor_acceptance: dict[str, dict[str, int]] = Field(
        default_factory=dict,
        description=(
            "Accepted and offered counts per distractor distance tier. The `distant` "
            "tier is the negative control: acceptance there means the judge is "
            "over-accepting and precision on the closer tiers is inflated."
        ),
    )
    edge_agreement: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Cross-direction agreement per asserted edge: counts of edges confirmed by "
            "both directions, by one only, or by neither."
        ),
    )
    judge_model: str = Field(description="Model that produced the verdicts.")
    scores_by_direction: dict[str, list[EdgeScore]] = Field(
        default_factory=dict,
        description="Scores per direction, sliced by curriculum and by parent count.",
    )


class EdgeScore(BaseSchema):
    """Precision, recall, and agreement over one slice of discrimination items."""

    exact_match_rate: float = Field(
        description="Items where the selected set equals the true set exactly."
    )
    f1: float = Field(description="Harmonic mean of precision and recall.")
    n: int = Field(description="Items in the slice.")
    precision: float = Field(
        description="Selected pairings that the pipeline also asserts."
    )
    recall: float = Field(description="Asserted pairings the judge also selected.")
    slice_name: str = Field(description="Slice the score covers.")
    standard_deviations: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Standard deviation of each metric across replicates. Empty when the "
            "evaluation ran once."
        ),
    )


class RubricItem(BaseSchema):
    """One standard and its component set, forming a single unit of judgement."""

    ancestor_path: list[Ancestor] = Field(
        default_factory=list,
        description="Ancestor levels giving the standard its curricular context.",
    )
    components: list[Component] = Field(
        description="Components shown to the judge, after any corruption is applied."
    )
    corruption: Optional[Corruption] = Field(
        default=None,
        description=(
            "Corruption applied to this item, or None for a real item. Items with a "
            "corruption are negative controls with known-bad ground truth."
        ),
    )
    curriculum: str = Field(description="Curriculum the standard belongs to.")
    item_id: str = Field(
        description="Deterministic identifier used to cache judge verdicts."
    )
    language: str = Field(description="Language tag of the source standard.")
    sfi_uuid: str = Field(description="Final SFI UUID of the source standard.")
    standard_text: str = Field(description="Source standard text.")
    statement_type: str = Field(description="Source-facing statement type.")
    stratum: Stratum = Field(
        description="Component-count stratum the item was sampled from."
    )


class RubricJudgement(BaseSchema):
    """One judge replicate for one item, as persisted to the verdict cache."""

    item_id: str = Field(description="Item the verdict belongs to.")
    judge_model: str = Field(description="Model that produced the verdict.")
    replicate: int = Field(
        description="Zero-based replicate index within the self-consistency pass."
    )
    verdict: RubricVerdict = Field(description="Parsed judge verdict.")


class RubricReport(BaseSchema):
    """Aggregate evaluation report."""

    baseline_scores: list[CriterionScore] = Field(
        default_factory=list,
        description=(
            "Scores for baseline items whose single component is the standard text "
            "verbatim, establishing what no decomposition at all scores."
        ),
    )
    control_detections: list[ControlDetection] = Field(
        default_factory=list,
        description="Detection rate per negative-control type.",
    )
    judge_model: str = Field(description="Model that produced the verdicts.")
    replicates: int = Field(description="Replicates judged per item.")
    scores_by_curriculum: dict[str, list[CriterionScore]] = Field(
        default_factory=dict, description="Criterion scores per curriculum."
    )
    scores_by_stratum: dict[str, list[CriterionScore]] = Field(
        default_factory=dict,
        description="Criterion scores per component-count stratum.",
    )
    scores_overall: list[CriterionScore] = Field(
        default_factory=list, description="Criterion scores over all real items."
    )
    seed: int = Field(description="Sampling seed.")
    unstable_item_count: int = Field(
        default=0,
        description=(
            "Real items whose replicates disagreed on at least one criterion, "
            "excluded from headline scores."
        ),
    )


class RubricSample(BaseSchema):
    """A sampled evaluation item set with the provenance needed to reproduce it."""

    control_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Number of negative-control items generated per corruption type.",
    )
    items: list[RubricItem] = Field(description="All items to be judged.")
    population_counts: dict[str, dict[str, int]] = Field(
        default_factory=dict,
        description="Population size per curriculum and stratum before sampling.",
    )
    seed: int = Field(description="Seed used for all sampling decisions.")
    shortfalls: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Requested-minus-available counts per curriculum and stratum where the "
            "population was smaller than the sampling target."
        ),
    )
    stratum_counts: dict[str, dict[str, int]] = Field(
        default_factory=dict,
        description="Number of real items sampled per curriculum and stratum.",
    )


class SetVerdict(BaseSchema):
    """Judge verdict for one standard's component set as a whole."""

    coverage: Coverage = Field(
        description="Whether the components collectively capture the standard."
    )
    granularity: Granularity = Field(
        description="Component size relative to the fixed target grain."
    )
    non_redundancy: bool = Field(
        description="True when no two components describe the same skill."
    )


class RubricVerdict(BaseSchema):
    """Complete judge output for one item."""

    component_verdicts: list[ComponentVerdict] = Field(
        description="One verdict per component shown to the judge."
    )
    notes: str = Field(
        default="",
        description="Brief free-text justification for the set-level verdict.",
    )
    set_verdict: SetVerdict = Field(
        description="Verdict for the component set as a whole."
    )
