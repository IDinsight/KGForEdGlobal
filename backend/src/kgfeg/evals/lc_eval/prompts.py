"""Prompt content for the Learning Components evaluation judges."""

# Standard Library
from typing import Sequence

# Package Library
from kgfeg.evals.lc_eval.schemas import (
    Component,
    EdgeCandidate,
    EdgeItem,
    RubricItem,
)

EDGE_SYSTEM_MESSAGE = """\
You decide which of several options a supports relationship genuinely holds for, given \
an anchor item from a school curriculum.

A Learning Component is a single, well-defined skill or concept that students are \
expected to learn: a granular unit of learning that describes instructional intent at \
the level of a lesson, activity, or assessment.

A learning component supports a standard when it contributes to the understanding, \
mastery, or achievement of the goals that standard defines.

You will be shown either a curriculum standard and several candidate learning \
components, or a learning component and several candidate standards. Select every \
option the supports relationship holds for, and only those. A component may \
legitimately support more than one standard, for instance when the same skill recurs \
across grades. Select as many or as few options as genuinely apply; there is no fixed \
number of correct answers, and selecting none is valid if none apply.

The options include deliberately close distractors drawn from neighbouring parts of \
the same curriculum. Being on the same topic is not sufficient. Judge only the text you \
are shown: do not accept an option because it is plausible for the subject, and do not \
credit an option with content it does not state.

Return the ids of the options you accept.
"""

RUBRIC_SYSTEM_MESSAGE = """\
You evaluate whether a curriculum standard has been correctly decomposed into atomic \
learning components.

A Learning Component is a single, well-defined skill or concept that students are \
expected to learn: a granular unit of learning that describes instructional intent at \
the level of a lesson, activity, or assessment, breaking a broad standard into \
teachable and measurable parts.

A component may never introduce anything its source does not carry.

You are shown one standard, the ancestor path giving its curricular context, and the \
components that were generated from it. Judge only what you are shown. Do not assume \
a component is correct because it appears plausible; check it against the standard.

Judge each component on three criteria.

FAITHFULNESS — is every element of the component supported by the standard's own text? \
The ancestor path may supply what the standard leaves elliptical, such as a missing \
verb for a bare-noun topic, but content taken from ancestors that the standard's text \
does not call for is extrapolated rather than grounded.
  grounded: fully supported.
  extrapolated: plausible, but adds specificity absent from the source, such as an \
invented numeric bound, method, manipulative, time limit, or grouping.
  unsupported: contradicts the source, or introduces content unrelated to it.

ATOMICITY — does the component describe exactly one skill or concept? It fails when it \
joins distinct actions with a conjunction, or bundles a skill with a separable context.

WELL_FORMEDNESS — is it a complete, teachable skill? It fails when it is an activity, \
resource, teacher guidance, worked example, assessment task, or a fragment cut off \
mid-clause.

Then judge the component set as a whole.

COVERAGE — do the components collectively capture the standard?
  complete: nothing meaningful in the standard is unrepresented.
  partial: at least one distinct skill named in the standard has no component.
  poor: the set represents only a small fraction of what the standard asks.
A standard that is already a single atomic skill is correctly covered by one \
component; do not mark it partial merely for having few components.

NON_REDUNDANCY — true when no two components describe the same skill. Two components \
that differ only in wording are redundant, and so is a component that restates the \
standard as a whole while other components cover its parts, since a split replaces \
the standard rather than accompanying it.

GRANULARITY — Each component should be one teachable skill, no larger than what the \
standard's own text supports.
  too_coarse: the standard's own text supports a finer decomposition than this set \
provides, so a component still covers more than one of the single, well-defined skills \
the standard names.
  too_fine: a component is smaller than a single well-defined skill: it cannot stand \
on its own without a neighbouring component, it carries too little instructional intent \
to plan a lesson or write an assessment item around, or one stated competency was split \
into separate understanding and application components.
  appropriate: everything else.
A standard that is itself a single skill is correctly rendered as one component. \
Decomposition must not invent structure the standard does not carry, so judge \
splitting against what the standard states, never against how finely the skill could \
in principle be taught. A one-component set is not too_coarse merely for having one \
component, and curricula whose standards are terse or unusually detailed are not \
thereby too_coarse or too_fine.

Return a verdict for every component you were shown, using the component ids given.
"""


def _render_candidate(candidate: EdgeCandidate) -> str:
    """Render one option, with its curricular context when it carries one.

    Parameters
    ----------
    candidate
        Option to render.

    Returns
    -------
    str
        Rendered option.
    """

    line = f"  [{candidate.candidate_id}] {candidate.text}"
    if not candidate.ancestor_path:
        return line
    context = " > ".join(
        f"{ancestor.statement_type}: {ancestor.description}"
        for ancestor in candidate.ancestor_path
    )

    return f"{line}\n      context: {context}"


def build_edge_user_message(item: EdgeItem) -> str:
    """Render one discrimination item as the judge's user message.

    Parameters
    ----------
    item
        Item to render.

    Returns
    -------
    str
        Rendered user message.
    """

    path = (
        "\n".join(f"  {a.statement_type}: {a.description}" for a in item.ancestor_path)
        or "  (none)"
    )
    anchor_label, option_label = (
        ("STANDARD", "CANDIDATE LEARNING COMPONENTS")
        if item.direction == "standard_to_components"
        else ("LEARNING COMPONENT", "CANDIDATE STANDARDS")
    )
    if item.anchor_statement_type:
        anchor_label = f"{anchor_label} ({item.anchor_statement_type})"
    options = "\n".join(_render_candidate(c) for c in item.candidates)
    anchor = f"{anchor_label}:\n  {item.anchor_text}\n\n{option_label}:\n{options}\n"
    if not item.ancestor_path:
        return anchor

    return f"CURRICULAR CONTEXT:\n{path}\n\n{anchor}"


def build_rubric_user_message(
    *, components: Sequence[Component], item: RubricItem
) -> str:
    """Render one item as the judge's user message.

    Parameters
    ----------
    components
        Components in the order they should be presented.
    item
        Item being judged.

    Returns
    -------
    str
        Rendered user message.
    """

    path = (
        "\n".join(
            f"  {ancestor.statement_type}: {ancestor.description}"
            for ancestor in item.ancestor_path
        )
        or "  (none)"
    )
    listed = "\n".join(f"  [{c.component_id}] {c.text}" for c in components)
    return (
        f"CURRICULAR CONTEXT:\n{path}\n\n"
        f"STANDARD ({item.statement_type}):\n  {item.standard_text}\n\n"
        f"GENERATED COMPONENTS:\n{listed}\n"
    )
