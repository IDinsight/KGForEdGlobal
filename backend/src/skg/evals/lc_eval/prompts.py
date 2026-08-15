"""Prompt content for the Learning Components evaluation judges."""

# Future Library
from __future__ import annotations

# Standard Library
from typing import Sequence

# Package Library
from skg.evals.lc_eval.schemas import Component, EdgeItem, RubricItem

EDGE_SYSTEM_MESSAGE = """\
You decide which of several options genuinely pair with an anchor item from a school \
mathematics curriculum.

You will be shown either a curriculum standard and several candidate learning \
components, or a learning component and several candidate standards. Select every \
option that genuinely pairs with the anchor, and only those.

A learning component pairs with a standard when mastering that component is part of \
what the standard requires. A component may legitimately pair with more than one \
standard, for instance when the same skill recurs across grades. Select as many or as \
few options as genuinely apply; there is no fixed number of correct answers, and \
selecting none is valid if none apply.

The options include deliberately close distractors drawn from neighbouring parts of \
the same curriculum. Being on the same topic is not sufficient. Return the ids of the \
options you accept.
"""

RUBRIC_SYSTEM_MESSAGE = """\
You evaluate whether a curriculum standard has been correctly decomposed into atomic \
learning components.

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

ATOMICITY — does the component describe exactly one skill? It fails when it joins \
distinct actions with a conjunction, or bundles a skill with a separable context.

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
  too_coarse: a component leaves bundled what the standard states separately, such as \
distinct actions, objects, or listed cases named in its text.
  too_fine: a component splits below what the standard states, so it cannot stand on \
its own without a neighbouring component, or one stated competency was split into \
separate understanding and application components.
  appropriate: everything else.
A standard that is itself a single skill is correctly rendered as one component. \
Decomposition must not invent structure the standard does not carry, so judge \
splitting against what the standard states, never against how finely the skill could \
in principle be taught. A one-component set is not too_coarse merely for having one \
component, and curricula whose standards are terse or unusually detailed are not \
thereby too_coarse or too_fine.

Return a verdict for every component you were shown, using the component ids given.
"""


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
    options = "\n".join(f"  [{c.candidate_id}] {c.text}" for c in item.candidates)
    return (
        f"CURRICULAR CONTEXT:\n{path}\n\n"
        f"{anchor_label}:\n  {item.anchor_text}\n\n"
        f"{option_label}:\n{options}\n"
    )


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
