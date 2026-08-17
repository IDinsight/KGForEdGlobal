"""Population loading, stratified sampling, and negative-control generation.

Sampling is seeded per curriculum and stratum so that adding a curriculum does not
change which items are drawn for the others.
"""

# Standard Library
import hashlib
import json
import random

from collections import defaultdict
from functools import partial
from pathlib import Path
from typing import Optional, Sequence

# Package Library
from skg.evals.lc_eval.schemas import (
    Ancestor,
    Component,
    Corruption,
    EdgeCandidate,
    EdgeDistance,
    EdgeItem,
    RubricItem,
    RubricSample,
    Stratum,
)
from skg.utils.general import write_to_json

_DISTRACTOR_COUNT = 6
_TIER_QUOTAS: dict[str, int] = {"distant": 2, "near": 2, "sibling": 2}
_CONTROL_TYPES: tuple[Corruption, ...] = (
    "cross_strand_swap",
    "forced_redundancy",
    "invented_specificity",
    "truncation",
    "under_decomposition",
)
_INVENTED_SPECIFICITY_SUFFIXES: tuple[str, ...] = (
    " using base-ten blocks",
    " to three decimal places",
    " within a two-minute time limit",
    " in groups of exactly four pupils",
    " using a metre rule marked in millimetres",
)
_STRATUM_ALLOCATION: dict[Stratum, int] = {
    "one_component": 25,
    "three_plus_components": 40,
    "two_components": 35,
}


def _ancestors_for(standards: dict, uuid: str) -> list[Ancestor]:
    """Build ancestor levels for one standard.

    Parameters
    ----------
    standards
        Standards for one curriculum, keyed by final SFI UUID.
    uuid
        Final SFI UUID of the standard.

    Returns
    -------
    list[Ancestor]
        Ancestor levels, empty when the standard is unknown.
    """

    return [
        Ancestor(description=a["description"], statement_type=a["statement_type"])
        for a in (standards.get(uuid, {}).get("ancestor_path") or [])
    ]


def _assemble_options(
    *,
    distractors: Sequence[tuple[EdgeDistance, str]],
    rng: random.Random,
    true_texts: Sequence[str],
) -> list[EdgeCandidate]:
    """Combine asserted options with tiered distractors into a shuffled option list.

    Parameters
    ----------
    distractors
        Candidate distractor texts paired with their distance from the anchor.
    rng
        Seeded random source controlling tier sampling and presentation order.
    true_texts
        Texts of the pairings the pipeline asserts.

    Returns
    -------
    list[EdgeCandidate]
        Options in presentation order with sequential identifiers assigned.
    """

    options = [
        EdgeCandidate(candidate_id="", is_true=True, text=text) for text in true_texts
    ] + [
        EdgeCandidate(candidate_id="", distance=tier, is_true=False, text=text)
        for tier, text in _pick_tiered(pool=distractors, rng=rng)
    ]
    rng.shuffle(options)
    return [
        option.model_copy(update={"candidate_id": f"o{index + 1}"})
        for index, option in enumerate(options)
    ]


def _build_edge_item_id(
    *, candidates: Sequence[EdgeCandidate], direction: str, source_id: str
) -> str:
    """Build the deterministic identifier for one discrimination item.

    The candidate texts are part of the basis so that changing distractor selection
    changes the identifier. Without this, cached verdicts would be reused against a
    different set of options.

    Parameters
    ----------
    candidates
        Options shown to the judge, in presentation order.
    direction
        Direction the item is posed in.
    source_id
        Identifier of the anchor entity.

    Returns
    -------
    str
        Deterministic item identifier.
    """

    options = "|".join(f"{c.is_true}:{c.text}" for c in candidates)
    basis = f"{direction}|{source_id}|{options}"
    return "lc_eval_edge_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _build_item_id(
    *,
    components: Sequence[Component],
    corruption: Optional[str],
    curriculum: str,
    sfi_uuid: str,
) -> str:
    """Build the deterministic identifier used to cache one item's verdicts.

    Component text is part of the basis, so regenerating a standard's components
    yields a new identifier rather than reusing a verdict judged against the previous
    ones.

    Parameters
    ----------
    components
        Components shown to the judge for this item.
    corruption
        Corruption applied to the item, or None for a real item.
    curriculum
        Curriculum the standard belongs to.
    sfi_uuid
        Final SFI UUID of the source standard.

    Returns
    -------
    str
        Deterministic item identifier.
    """

    texts = "|".join(component.text for component in components)
    basis = f"{curriculum}|{sfi_uuid}|{corruption or 'none'}|{texts}"
    return "lc_eval_item_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _corrupt_cross_strand_swap(
    *, item: RubricItem, rng: random.Random, sources: Sequence[RubricItem]
) -> Optional[RubricItem]:
    """Replace one component with a component from an unrelated strand.

    Parameters
    ----------
    item
        Item to corrupt.
    rng
        Seeded random source.
    sources
        Items the replacement component may be drawn from.

    Returns
    -------
    Optional[RubricItem]
        Corrupted item, or None when no unrelated-strand donor is available.
    """

    strand = _strand_key(item)
    donors = [
        other for other in sources if other.components and _strand_key(other) != strand
    ]
    if not donors:
        return None

    donor = rng.choice(donors)
    replacement = rng.choice(donor.components)
    components = list(item.components)
    index = rng.randrange(len(components))
    components[index] = Component(
        component_id=components[index].component_id, text=replacement.text
    )
    return _rebuild(components=components, corruption="cross_strand_swap", item=item)


def _corrupt_forced_redundancy(
    *, item: RubricItem, rng: random.Random
) -> Optional[RubricItem]:
    """Append a verbatim duplicate of one existing component.

    The duplicate is verbatim rather than reworded so that the control tests
    redundancy alone. Rewording risks producing ungrammatical text, which a judge
    would flag as malformed rather than redundant, contaminating the criterion this
    control is meant to isolate.

    Parameters
    ----------
    item
        Item to corrupt.
    rng
        Seeded random source.

    Returns
    -------
    Optional[RubricItem]
        Corrupted item, or None when the item has no components.
    """

    if not item.components:
        return None

    source = rng.choice(item.components)
    components = list(item.components) + [
        Component(component_id=f"c{len(item.components) + 1}", text=source.text)
    ]
    return _rebuild(components=components, corruption="forced_redundancy", item=item)


def _corrupt_invented_specificity(
    *, item: RubricItem, rng: random.Random
) -> Optional[RubricItem]:
    """Append fabricated specificity that the source standard does not contain.

    Parameters
    ----------
    item
        Item to corrupt.
    rng
        Seeded random source.

    Returns
    -------
    Optional[RubricItem]
        Corrupted item, or None when the item has no components.
    """

    if not item.components:
        return None

    index = rng.randrange(len(item.components))
    suffix = _INVENTED_SPECIFICITY_SUFFIXES[
        rng.randrange(len(_INVENTED_SPECIFICITY_SUFFIXES))
    ]
    components = list(item.components)
    components[index] = Component(
        component_id=components[index].component_id,
        text=components[index].text.rstrip(".") + suffix,
    )
    return _rebuild(components=components, corruption="invented_specificity", item=item)


def _corrupt_truncation(
    *, item: RubricItem, rng: random.Random
) -> Optional[RubricItem]:
    """Cut one component mid-clause.

    Parameters
    ----------
    item
        Item to corrupt.
    rng
        Seeded random source.

    Returns
    -------
    Optional[RubricItem]
        Corrupted item, or None when no component is long enough to truncate.
    """

    candidates = [c for c in item.components if len(c.text.split()) >= 5]
    if not candidates:
        return None

    target = rng.choice(candidates)
    words = target.text.split()
    truncated = " ".join(words[: max(2, int(len(words) * 0.6))])
    components = [
        (
            Component(component_id=c.component_id, text=truncated)
            if c.component_id == target.component_id
            else c
        )
        for c in item.components
    ]
    return _rebuild(components=components, corruption="truncation", item=item)


def _corrupt_under_decomposition(*, item: RubricItem) -> Optional[RubricItem]:
    """Drop all but the first component of a multi-component set.

    Parameters
    ----------
    item
        Item to corrupt.

    Returns
    -------
    Optional[RubricItem]
        Corrupted item, or None when the item has fewer than three components.
    """

    if len(item.components) < 3:
        return None

    return _rebuild(
        components=item.components[:1], corruption="under_decomposition", item=item
    )


def _distance(*, anchor: Sequence[Ancestor], other: Sequence[Ancestor]) -> EdgeDistance:
    """Classify how far one standard sits from another in the framework.

    Parameters
    ----------
    anchor
        Ancestor levels of the anchor standard.
    other
        Ancestor levels of the candidate standard.

    Returns
    -------
    EdgeDistance
        `sibling` under the same immediate parent, `near` elsewhere in the same
        top-level branch, otherwise `distant`.
    """

    if anchor and other and anchor[-1].description == other[-1].description:
        return "sibling"
    if anchor and other and anchor[0].description == other[0].description:
        return "near"

    return "distant"


def _pick_tiered(
    *, pool: Sequence[tuple[EdgeDistance, str]], rng: random.Random
) -> list[tuple[EdgeDistance, str]]:
    """Draw distractors across all three distance tiers.

    Each tier is sampled to its quota; any shortfall is backfilled from the remaining
    pool so every item carries the same number of options regardless of how large its
    local family is. Distant options are the negative controls, so a tier that cannot
    be filled is worth knowing about rather than silently substituting.

    Parameters
    ----------
    pool
        Candidate identifiers paired with their distance from the anchor.
    rng
        Seeded random source.

    Returns
    -------
    list[tuple[EdgeDistance, str]]
        Selected distractors with their tiers.
    """

    by_tier: dict[str, list[tuple[EdgeDistance, str]]] = defaultdict(list)
    for entry in pool:
        by_tier[entry[0]].append(entry)

    chosen: list[tuple[EdgeDistance, str]] = []
    for tier, quota in _TIER_QUOTAS.items():
        available = list(by_tier.get(tier, []))
        rng.shuffle(available)
        chosen.extend(available[:quota])

    if len(chosen) < _DISTRACTOR_COUNT:
        remaining = [entry for entry in pool if entry not in chosen]
        rng.shuffle(remaining)
        chosen.extend(remaining[: _DISTRACTOR_COUNT - len(chosen)])

    return chosen[:_DISTRACTOR_COUNT]


def _rebuild(
    *,
    components: Sequence[Component],
    corruption: Corruption,
    item: RubricItem,
) -> RubricItem:
    """Build a corrupted copy of one item with a corruption-specific identifier.

    Parameters
    ----------
    components
        Component set after corruption.
    corruption
        Corruption that was applied.
    item
        Item the corrupted copy derives from.

    Returns
    -------
    RubricItem
        Corrupted item.
    """

    return item.model_copy(
        update={
            "components": list(components),
            "corruption": corruption,
            "item_id": _build_item_id(
                components=components,
                corruption=corruption,
                curriculum=item.curriculum,
                sfi_uuid=item.sfi_uuid,
            ),
        }
    )


def _resolve_run_dir(*, curriculum: str, results_root: Path) -> Path:
    """Resolve the single run directory holding LC artifacts for one curriculum.

    Parameters
    ----------
    curriculum
        Curriculum directory name under the results root.
    results_root
        Root directory containing per-curriculum run directories.

    Returns
    -------
    Path
        The `kgs` directory for the curriculum's LC run.

    Raises
    ------
    ValueError
        If zero or more than one run directory contains LC artifacts.
    """

    candidates = sorted(
        p.parent
        for p in (results_root / curriculum).glob("*/kgs/learning_components.jsonl")
    )
    if len(candidates) != 1:
        raise ValueError(
            f"Expected exactly one run directory with LC artifacts for "
            f"curriculum={curriculum!r} under {results_root}, found "
            f"{len(candidates)}: {[str(c) for c in candidates]}."
        )

    return candidates[0]


def _strand_key(item: RubricItem) -> str:
    """Build the key identifying which branch of the framework a standard sits in.

    Uses the deepest ancestor whose statement type mentions a strand, falling back to
    the shallowest ancestor when no strand level is present.

    Parameters
    ----------
    item
        Item whose branch should be identified.

    Returns
    -------
    str
        Branch key, or the empty string when the item has no ancestors.
    """

    if not item.ancestor_path:
        return ""

    strand_levels = [
        ancestor
        for ancestor in item.ancestor_path
        if "strand" in ancestor.statement_type.casefold()
    ]
    if strand_levels:
        return f"{item.curriculum}|{strand_levels[0].description}"

    return f"{item.curriculum}|{item.ancestor_path[0].description}"


def _stratum_for(component_count: int) -> Stratum:
    """Map a component count to its sampling stratum.

    Parameters
    ----------
    component_count
        Number of components attached to one standard.

    Returns
    -------
    Stratum
        The stratum the standard belongs to.

    Raises
    ------
    ValueError
        If the count is not positive.
    """

    if component_count < 1:
        raise ValueError(f"Component count must be positive, got {component_count}.")
    if component_count == 1:
        return "one_component"
    if component_count == 2:
        return "two_components"

    return "three_plus_components"


def build_baseline_items(
    *, per_curriculum: int, population: Sequence[RubricItem], seed: int
) -> list[RubricItem]:
    """Build baseline items whose single component is the standard text verbatim.

    Baseline items establish what "no decomposition at all" scores, making the real
    scores interpretable. They are drawn only from multi-component standards, where a
    single verbatim component is genuinely a failure to decompose rather than the
    correct answer.

    Parameters
    ----------
    per_curriculum
        Number of baseline items to build per curriculum.
    population
        Real items available to derive baselines from.
    seed
        Seed controlling which standards are used.

    Returns
    -------
    list[RubricItem]
        Baseline items, each carrying a `baseline` corruption marker so they are
        excluded from real-item scoring.
    """

    by_curriculum: dict[str, list[RubricItem]] = defaultdict(list)
    for item in population:
        if len(item.components) > 1:
            by_curriculum[item.curriculum].append(item)

    baselines: list[RubricItem] = []
    for curriculum, candidates in sorted(by_curriculum.items()):
        ordered = sorted(candidates, key=lambda item: item.item_id)
        random.Random(f"{seed}|baseline|{curriculum}").shuffle(ordered)
        for item in ordered[:per_curriculum]:
            components = [Component(component_id="c1", text=item.standard_text)]
            baselines.append(
                item.model_copy(
                    update={
                        "components": components,
                        "item_id": _build_item_id(
                            components=components,
                            corruption="baseline",
                            curriculum=item.curriculum,
                            sfi_uuid=item.sfi_uuid,
                        ),
                    }
                )
            )

    return baselines


def build_edge_items(
    *, curricula: Sequence[str], results_root: Path, seed: int
) -> list[EdgeItem]:
    """Build discrimination items in both directions over every asserted edge.

    Parameters
    ----------
    curricula
        Curriculum directory names under the results root.
    results_root
        Root directory containing per-curriculum run directories.
    seed
        Seed controlling distractor selection and option ordering.

    Returns
    -------
    list[EdgeItem]
        Items in both directions.

    Raises
    ------
    ValueError
        If a curriculum's artifacts are missing.
    """

    items: list[EdgeItem] = []

    for curriculum in curricula:
        candidates = sorted(
            p.parent
            for p in (results_root / curriculum).glob("*/kgs/learning_components.jsonl")
        )
        if len(candidates) != 1:
            raise ValueError(
                f"Expected exactly one run directory for curriculum={curriculum!r}, "
                f"found {len(candidates)}."
            )
        run_dir = candidates[0]

        component_text = {
            r["identifier"]: r["description"]
            for r in (
                json.loads(line)
                for line in (run_dir / "learning_components.jsonl")
                .read_text()
                .splitlines()
                if line.strip()
            )
        }
        standard: dict[str, dict] = {}
        for line in (run_dir / "lc_generation_requests.jsonl").read_text().splitlines():
            if not line.strip():
                continue
            for sfi in json.loads(line)["sfis"]:
                standard[sfi["final_sfi_uuid"]] = sfi

        components_by_standard: dict[str, list[str]] = defaultdict(list)
        standards_by_component: dict[str, list[str]] = defaultdict(list)
        for edge in json.loads((run_dir / "lc_supports_edges.json").read_text()):
            components_by_standard[edge["target_entity_value"]].append(
                edge["source_entity_value"]
            )
            standards_by_component[edge["source_entity_value"]].append(
                edge["target_entity_value"]
            )

        ancestors_for = partial(_ancestors_for, standard)

        for standard_uuid, component_ids in sorted(components_by_standard.items()):
            ancestors = ancestors_for(standard_uuid)
            tiered = [
                (_distance(anchor=ancestors, other=ancestors_for(other_uuid)), cid)
                for other_uuid, other_cids in components_by_standard.items()
                if other_uuid != standard_uuid
                for cid in other_cids
                if cid not in component_ids
            ]
            options = _assemble_options(
                distractors=[(tier, component_text[cid]) for tier, cid in tiered],
                rng=random.Random(f"{seed}|a|{standard_uuid}"),
                true_texts=[component_text[cid] for cid in sorted(set(component_ids))],
            )
            items.append(
                EdgeItem(
                    anchor_text=standard[standard_uuid]["description"],
                    ancestor_path=ancestors,
                    candidates=options,
                    curriculum=curriculum,
                    direction="standard_to_components",
                    item_id=_build_edge_item_id(
                        candidates=options,
                        direction="standard_to_components",
                        source_id=standard_uuid,
                    ),
                    source_id=standard_uuid,
                )
            )

        for component_id, parent_uuids in sorted(standards_by_component.items()):
            parents = sorted(set(parent_uuids))
            ancestors = ancestors_for(parents[0])
            tiered = [
                (_distance(anchor=ancestors, other=ancestors_for(uuid)), uuid)
                for uuid in components_by_standard
                if uuid not in parents and uuid in standard
            ]
            options = _assemble_options(
                distractors=[
                    (tier, standard[uuid]["description"]) for tier, uuid in tiered
                ],
                rng=random.Random(f"{seed}|b|{component_id}"),
                true_texts=[
                    standard[p]["description"] for p in parents if p in standard
                ],
            )
            if not any(c.is_true for c in options):
                continue
            items.append(
                EdgeItem(
                    anchor_text=component_text[component_id],
                    ancestor_path=ancestors,
                    candidates=options,
                    curriculum=curriculum,
                    direction="component_to_standards",
                    is_multi_parent=len(parents) > 1,
                    item_id=_build_edge_item_id(
                        candidates=options,
                        direction="component_to_standards",
                        source_id=component_id,
                    ),
                    source_id=component_id,
                )
            )

    return items


def build_negative_controls(
    *,
    population: Sequence[RubricItem],
    per_type: int,
    seed: int,
) -> list[RubricItem]:
    """Build negative-control items by corrupting real items.

    Each control derives from a distinct source standard so that no standard appears
    twice under different corruptions.

    Parameters
    ----------
    population
        Real items available to corrupt.
    per_type
        Number of controls to build per corruption type.
    seed
        Seed controlling which items are corrupted.

    Returns
    -------
    list[RubricItem]
        Corrupted items, each carrying its corruption type.

    Raises
    ------
    ValueError
        If any corruption type cannot reach `per_type` items.
    """

    controls: list[RubricItem] = []
    used: set[str] = set()

    for corruption in _CONTROL_TYPES:
        rng = random.Random(f"{seed}|control|{corruption}")
        pool = [item for item in population if item.sfi_uuid not in used]
        rng.shuffle(pool)
        built = 0

        for item in pool:
            if corruption == "cross_strand_swap":
                corrupted = _corrupt_cross_strand_swap(
                    item=item, rng=rng, sources=population
                )
            elif corruption == "forced_redundancy":
                corrupted = _corrupt_forced_redundancy(item=item, rng=rng)
            elif corruption == "invented_specificity":
                corrupted = _corrupt_invented_specificity(item=item, rng=rng)
            elif corruption == "truncation":
                corrupted = _corrupt_truncation(item=item, rng=rng)
            else:
                corrupted = _corrupt_under_decomposition(item=item)

            if corrupted is None:
                continue

            controls.append(corrupted)
            used.add(item.sfi_uuid)
            built += 1
            if built == per_type:
                break

        if built < per_type:
            raise ValueError(
                f"Could only build {built} of {per_type} requested {corruption!r} "
                f"negative controls from {len(population)} standards. Lower "
                f"control_per_type or evaluate more curricula; corruption types draw "
                f"from one shared pool, so the workable value is above {built}."
            )

    return controls


def discover_curricula(*, results_root: Path) -> list[str]:
    """Return every curriculum under the results root that has generated components.

    Presence of `learning_components.jsonl` is the criterion, so curricula whose LC
    phase has not run are skipped without needing to be listed anywhere.

    Parameters
    ----------
    results_root
        Root directory containing per-curriculum run directories.

    Returns
    -------
    list[str]
        Curriculum directory names in alphabetical order.

    Raises
    ------
    ValueError
        If no curriculum under the results root has generated components.
    """

    found = sorted(
        {
            path.parents[2].name
            for path in results_root.glob("*/*/kgs/learning_components.jsonl")
        }
    )
    if not found:
        raise ValueError(
            f"No curriculum under {results_root} has a learning_components.jsonl "
            f"artifact; run the LC pipeline before evaluating it."
        )

    return found


def load_population(
    *, curricula: Sequence[str], results_root: Path
) -> list[RubricItem]:
    """Load every standard and its component set across the given curricula.

    Parameters
    ----------
    curricula
        Curriculum directory names under the results root.
    results_root
        Root directory containing per-curriculum run directories.

    Returns
    -------
    list[RubricItem]
        One item per standard that has at least one component.

    Raises
    ------
    ValueError
        If a curriculum's artifacts are missing or internally inconsistent.
    """

    population: list[RubricItem] = []

    for curriculum in curricula:
        run_dir = _resolve_run_dir(curriculum=curriculum, results_root=results_root)

        component_text_by_id = {
            record["identifier"]: record["description"]
            for record in (
                json.loads(line)
                for line in (run_dir / "learning_components.jsonl")
                .read_text()
                .splitlines()
                if line.strip()
            )
        }
        component_ids_by_sfi: dict[str, list[str]] = defaultdict(list)
        for edge in json.loads((run_dir / "lc_supports_edges.json").read_text()):
            component_ids_by_sfi[edge["target_entity_value"]].append(
                edge["source_entity_value"]
            )

        for line in (run_dir / "lc_generation_requests.jsonl").read_text().splitlines():
            if not line.strip():
                continue
            for sfi in json.loads(line)["sfis"]:
                sfi_uuid = sfi["final_sfi_uuid"]
                component_ids = sorted(component_ids_by_sfi.get(sfi_uuid, []))
                if not component_ids:
                    continue

                missing = [c for c in component_ids if c not in component_text_by_id]
                if missing:
                    raise ValueError(
                        f"Supports edges for curriculum={curriculum!r} reference "
                        f"components absent from learning_components.jsonl: "
                        f"{missing[:3]}."
                    )

                components = [
                    Component(
                        component_id=f"c{index + 1}",
                        text=component_text_by_id[component_id],
                    )
                    for index, component_id in enumerate(component_ids)
                ]
                population.append(
                    RubricItem(
                        ancestor_path=[
                            Ancestor(
                                description=ancestor["description"],
                                statement_type=ancestor["statement_type"],
                            )
                            for ancestor in (sfi.get("ancestor_path") or [])
                        ],
                        components=components,
                        curriculum=curriculum,
                        item_id=_build_item_id(
                            components=components,
                            corruption=None,
                            curriculum=curriculum,
                            sfi_uuid=sfi_uuid,
                        ),
                        language=sfi.get("language") or "",
                        sfi_uuid=sfi_uuid,
                        standard_text=sfi["description"],
                        statement_type=sfi.get("statement_type") or "",
                        stratum=_stratum_for(len(component_ids)),
                    )
                )

    if not population:
        raise ValueError(
            f"No standards with components found for curricula={list(curricula)} "
            f"under {results_root}."
        )

    return population


def sample_rubric_items(
    *,
    control_per_type: int,
    population: Sequence[RubricItem],
    seed: int,
) -> RubricSample:
    """Draw a stratified sample and attach negative controls.

    Allocation deliberately oversamples multi-component strata, which carry more
    signal than single-component standards. Where a stratum holds fewer items than
    the allocation requests, every available item is taken and the shortfall is
    recorded rather than reallocated.

    Parameters
    ----------
    control_per_type
        Number of negative controls to build per corruption type.
    population
        All real items available to sample from.
    seed
        Seed controlling every sampling decision.

    Returns
    -------
    RubricSample
        Sampled items, negative controls, and the counts needed to reproduce them.
    """

    by_cell: dict[tuple[str, str], list[RubricItem]] = defaultdict(list)
    for item in population:
        by_cell[(item.curriculum, item.stratum)].append(item)

    population_counts: dict[str, dict[str, int]] = defaultdict(dict)
    sampled: list[RubricItem] = []
    shortfalls: dict[str, int] = {}
    stratum_counts: dict[str, dict[str, int]] = defaultdict(dict)

    for (curriculum, stratum), items in sorted(by_cell.items()):
        target = _STRATUM_ALLOCATION[stratum]  # type: ignore[index]
        ordered = sorted(items, key=lambda item: item.item_id)
        rng = random.Random(f"{seed}|{curriculum}|{stratum}")
        rng.shuffle(ordered)
        taken = ordered[:target]

        population_counts[curriculum][stratum] = len(items)
        stratum_counts[curriculum][stratum] = len(taken)
        if len(taken) < target:
            shortfalls[f"{curriculum}/{stratum}"] = target - len(taken)
        sampled.extend(taken)

    controls = build_negative_controls(
        population=population, per_type=control_per_type, seed=seed
    )

    return RubricSample(
        control_counts={c: control_per_type for c in _CONTROL_TYPES},
        items=sampled + controls,
        population_counts=dict(population_counts),
        seed=seed,
        shortfalls=shortfalls,
        stratum_counts=dict(stratum_counts),
    )


def write_edge_items(*, items: Sequence[EdgeItem], output_dir: Path) -> None:
    """Write the discrimination item set.

    Parameters
    ----------
    items
        Items to persist.
    output_dir
        Directory to write into.
    """

    write_to_json(
        fp=output_dir / "lc_eval_edge_items.jsonl",
        json_info=[item.model_dump(mode="json") for item in items],
    )


def write_rubric_sample(*, output_dir: Path, sample: RubricSample) -> None:
    """Write the sampled item set and its provenance summary.

    Parameters
    ----------
    output_dir
        Directory to write `lc_eval_items.jsonl` and `lc_eval_sample.json` into.
    sample
        Sample to persist.
    """

    write_to_json(
        fp=output_dir / "lc_eval_items.jsonl",
        json_info=[item.model_dump(mode="json") for item in sample.items],
    )
    write_to_json(
        fp=output_dir / "lc_eval_sample.json",
        json_info=sample.model_dump(mode="json", exclude={"items"}),
    )
