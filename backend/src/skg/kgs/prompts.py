"""This module contains prompt templates for Learning Progressions inference."""

# Standard Library
import json

from textwrap import dedent
from typing import Any

# Third Party Library
from dotmap import DotMap


def cross_grade_builds_towards(
    *,
    lower_grade_label: str,
    lower_items: list[dict[str, Any]],
    thread_key: str,
    thread_path: str,
    upper_grade_label: str,
    upper_items: list[dict[str, Any]],
) -> DotMap:
    """Cross-grade buildsTowards between adjacent grades within a normalized thread.

    Parameters
    ----------
    lower_grade_label
        The label of the lower grade (e.g., "Grade 3").
    lower_items
        The list of items from the lower grade.
    thread_key
        The normalized thread key for context (e.g., "math_geometry_shapes").
    thread_path
        The human-readable thread path for context (e.g., "Mathematics > Geometry >
        Shapes").
    upper_grade_label
        The label of the upper grade (e.g., "Grade 4").
    upper_items
        The list of items from the upper grade.

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    system_message = dedent(
        """You are a strict curriculum learning progression analyst.

TASK (Cross-Grade buildsTowards):
You will receive standards from two ADJACENT grades that belong to the SAME conceptual thread.
Decide which lower-grade items are meaningful prerequisites for upper-grade items.

HARD RULES:
1. Use ONLY the provided items. Do NOT invent new items.
2. Direction constraint: source MUST be from the LOWER grade list, target MUST be from the UPPER grade list.
3. Do NOT emit "obvious but weak" links. Only emit when the lower item truly builds foundation.
4. Prefer fewer, higher-quality edges.

CONFIDENCE:
- >=0.85 very clear prerequisite
- 0.70–0.84 plausible prerequisite
- <0.70 should usually be omitted
        """
    )

    user_message = json.dumps(
        {
            "lower_grade_label": lower_grade_label,
            "upper_grade_label": upper_grade_label,
            "thread_key": thread_key,
            "thread_path": thread_path,
            "lower_grade_items": lower_items,
            "upper_grade_items": upper_items,
        },
        ensure_ascii=False,
        indent=2,
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )


def cross_grade_relates_to(
    *,
    forbidden_pairs: list[dict[str, str]],
    lower_grade_label: str,
    lower_items: list[dict[str, Any]],
    max_edges_per_sfi: int,
    subject_label: str,
    upper_grade_label: str,
    upper_items: list[dict[str, Any]],
) -> DotMap:
    """Cross-grade relatesTo between adjacent grades (same subject) excluding
    buildsTowards pairs.

    Parameters
    ----------
    forbidden_pairs
        A list of item pairs (dicts with "lower_sfi_uuid" and "upper_sfi_uuid") that
        are already connected by buildsTowards and MUST NOT be returned as relatesTo.
    lower_grade_label
        The label of the lower grade (e.g., "Grade 3").
    lower_items
        The list of items from the lower grade.
    max_edges_per_sfi
        A soft cap on the number of relatesTo edges per item to keep the graph sparse.
    subject_label
        The subject label for context (e.g., "Mathematics").
    upper_grade_label
        The label of the upper grade (e.g., "Grade 4").
    upper_items
        The list of items from the upper grade.

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    system_message = dedent(
        f"""You are a strict curriculum concept-connection analyst.

TASK (Cross-Grade relatesTo):
You will receive standards from two ADJACENT grades in the SAME subject.
Some pairs are already connected by buildsTowards and MUST NOT be returned.
For the remaining possibilities, decide which cross-grade item pairs are conceptually related (shared concept), but NOT a prerequisite chain.

HARD RULES:
1. Use ONLY the provided items. Do NOT invent new items.
2. Cross-grade constraint: one endpoint MUST be from the LOWER grade list and the other MUST be from the UPPER grade list.
3. Forbidden pairs: DO NOT output any pair listed in forbidden_pairs (in either direction).
4. Do NOT output weak links. Keep it sparse and teacher-usable.
5. Soft cap: do not exceed about {max_edges_per_sfi} relatesTo edges per item across your output.

CONFIDENCE:
- >=0.90 only for very strong connections
- 0.80–0.89 for solid connections
- <0.80 should usually be omitted

Note: relatesTo is conceptually UNDIRECTED; you may choose either direction in the output.
        """
    )

    user_message = json.dumps(
        {
            "lower_grade_label": lower_grade_label,
            "upper_grade_label": upper_grade_label,
            "subject_label": subject_label,
            "forbidden_pairs": forbidden_pairs,
            "lower_grade_items": lower_items,
            "upper_grade_items": upper_items,
        },
        ensure_ascii=False,
        indent=2,
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )


def cross_stage_builds_towards(
    *,
    lower_grade_label: str,
    lower_items: list[dict[str, Any]],
    thread_key: str,
    thread_path: str,
    upper_grade_label: str,
    upper_items: list[dict[str, Any]],
) -> DotMap:
    """Cross-stage buildsTowards between non-adjacent grades within a normalized
    thread.

    This is a more exploratory prompt to identify potential "long-range" dependencies
    that might be missed in the adjacent-grade prompt. The model should be encouraged
    to identify strong dependencies even if they skip intermediate grades, but should
    not be forced to invent edges if the progression is more linear.

    Parameters
    ----------
    lower_grade_label
        The label of the lower grade (e.g., "Grade 3").
    lower_items
        The list of items from the lower grade.
    thread_key
        The normalized thread key for context (e.g., "math_geometry_shapes").
    thread_path
        The human-readable thread path for context (e.g., "Mathematics > Geometry >
        Shapes").
    upper_grade_label
        The label of the upper grade (e.g., "Grade 5").
    upper_items
        The list of items from the upper grade.

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    p = cross_grade_builds_towards(
        lower_items=lower_items,
        lower_grade_label=lower_grade_label,
        thread_key=thread_key,
        thread_path=thread_path,
        upper_grade_label=upper_grade_label,
        upper_items=upper_items,
    )

    # Add one strong sentence so the model does not assume these are single grades.
    p.system_message = (
        p.system_message
        + "\n\nNOTE: The level labels may be *banded stages* (e.g., I–II, III–VI), not single grades. Treat this as adjacent level *ranges*; do not invent per-grade steps."
    )
    return p


def cross_stage_relates_to(
    *,
    forbidden_pairs: list[dict[str, str]],
    lower_grade_label: str,
    lower_items: list[dict[str, Any]],
    max_edges_per_sfi: int,
    subject_label: str,
    upper_grade_label: str,
    upper_items: list[dict[str, Any]],
) -> DotMap:
    """Cross-stage relatesTo between non-adjacent grades within a subject, excluding
    buildsTowards pairs.

    This is a more exploratory prompt to identify potential "long-range" connections
    that might be missed in the adjacent-grade prompt. The model should be encouraged
    to identify strong connections even if they skip intermediate grades, but should
    not be forced to invent edges if the connections are weak or superficial.

    Parameters
    ----------
    forbidden_pairs
        A list of item pairs (dicts with "lower_sfi_uuid" and "upper sfi_uuid") that
        are already connected by buildsTowards and MUST NOT be returned as relatesTo.
    lower_grade_label
        The label of the lower grade (e.g., "Grade 3").
    lower_items
        The list of items from the lower grade.
    max_edges_per_sfi
        A soft cap on the number of relatesTo edges per item to keep the graph sparse.
    subject_label
        The subject label for context (e.g., "Mathematics").
    upper_grade_label
        The label of the upper grade (e.g., "Grade 5").
    upper_items
        The list of items from the upper grade.

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    p = cross_grade_relates_to(
        forbidden_pairs=forbidden_pairs,
        lower_grade_label=lower_grade_label,
        lower_items=lower_items,
        max_edges_per_sfi=max_edges_per_sfi,
        subject_label=subject_label,
        upper_grade_label=upper_grade_label,
        upper_items=upper_items,
    )

    # Add one strong sentence so the model does not assume these are single grades.
    p.system_message = (
        p.system_message
        + "\n\nNOTE: The level labels may be *banded stages* (e.g., I–II, III–VI), not single grades. "
        "Only emit relatesTo when the overlap is genuinely useful for teaching across these adjacent levels."
    )
    return p


def double_check_learning_progressions() -> DotMap:
    """Extra user message to trigger a careful second pass.

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    user_message = dedent(
        """**Hmmmm, are you absolutely sure of your results?**

It's a good idea to carefully review your last output against the stated instructions and double-check your response.

In particular, ensure that:

1. The output matches the schema exactly.
2. All SFI UUIDs exist in the provided input lists.
3. No self-edges (source == target).
4. You followed the provided rules (direction constraints, forbidden pairs, etc.).
5. Confidence is calibrated conservatively; avoid "over-linking".

When you are confident in your answer, return a complete `ProgressionEdgesResponse` that matches the schema and fixes any issues you might've overlooked or incorrect assumptions you might've made.
        """
    )

    return DotMap({"system_message": None, "user_message": user_message.strip()})


def within_grade_builds_towards(
    *, grade_label: str, items: list[dict[str, Any]], thread_path: str
) -> DotMap:
    """Within-grade buildsTowards in a single (grade, thread) bucket.

    Parameters
    ----------
    grade_label
        The grade label for the items (e.g., "Grade 3").
    items
        The list of items in the grade/thread bucket, presented in intended sequence
        order.
    thread_path
        The conceptual thread path for the items (e.g., "Mathematics > Geometry >
        Shapes").

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    system_message = dedent(
        """You are a strict curriculum learning progression analyst.

TASK (Within-Grade buildsTowards):
Given a list of standards (StandardsFrameworkItems) that belong to the SAME grade and SAME thread, decide which prerequisite relationships exist among them.

Definitions:
- buildsTowards(A -> B) means learning A is a meaningful prerequisite for learning B. It is NOT just "related" or "in the same topic"; it should be instructional dependency.

HARD RULES:
1. Use ONLY the provided items. Do NOT invent new items.
2. Only emit edges that are plausible prerequisites a teacher would rely on.
3. Prefer fewer, higher-quality edges over many weak edges.
4. Direction constraint: items are presented in intended sequence order. You MUST NOT point from a later item to an earlier item.

CONFIDENCE CALIBRATION:
- >=0.85 only if the dependency is very clear.
- 0.70–0.84 for plausible prerequisite.
- 0.50–0.69 for weak/uncertain (avoid these unless necessary).
- <0.50 should generally be omitted.

You may return an empty edges list if there are no clear prerequisites.
        """
    )

    user_message = json.dumps(
        {
            "grade_label": grade_label,
            "thread_path": thread_path,
            "items_in_sequence_order": items,
        },
        ensure_ascii=False,
        indent=2,
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )


def within_grade_relates_to(
    *,
    grade_label: str,
    items_a: list[dict[str, Any]],
    items_b: list[dict[str, Any]],
    max_edges_per_sfi: int,
    subject_label: str,
    thread_a_key: str,
    thread_b_key: str,
    thread_a_path: str,
    thread_b_path: str,
) -> DotMap:
    """Within-grade relatesTo between two different threads in the same subject.

    Parameters
    ----------
    grade_label
        The grade label for the items (e.g., "Grade 3").
    items_a
        The list of items from thread A.
    items_b
        The list of items from thread B.
    max_edges_per_sfi
        A soft cap on the number of relatesTo edges per item to keep the graph sparse.
    subject_label
        The subject label for context (e.g., "Mathematics").
    thread_a_key
        The normalized thread key for thread A (e.g., "math_geometry_shapes").
    thread_b_key
        The normalized thread key for thread B (e.g., "math_measurement_length").
    thread_a_path
        The human-readable thread path for thread A (e.g., "Mathematics > Geometry >
        Shapes").
    thread_b_path
        The human-readable thread path for thread B (e.g., "Mathematics > Measurement >
        Length").

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    system_message = dedent(
        f"""You are a strict curriculum concept-connection analyst.

TASK (Within-Grade relatesTo):
Two different threads (topic paths) within the SAME grade and SAME subject are provided.
Identify conceptual associations between items across the two threads.

Definition:
- relatesTo(A -- B) means the concepts meaningfully overlap such that a teacher would reasonably connect them instructionally (reinforcement, application, shared concept), BUT it is NOT a prerequisite chain.

HARD RULES:
1. Use ONLY the provided items. Do NOT invent new items.
2. Only emit edges ACROSS the threads: one endpoint must come from thread A, the other from thread B.
3. Do NOT output edges that are "related" only because they are in the same subject.
4. Keep it sparse: prefer a small number of strong conceptual links.
5. Soft cap: do not exceed about {max_edges_per_sfi} relatesTo edges per item across your output.

CONFIDENCE:
- >=0.90 only for very strong, teacher-usable connections
- 0.80–0.89 for solid connections
- <0.80 should usually be omitted

Note: relatesTo is conceptually UNDIRECTED; you may choose either direction in the output.
        """
    )

    user_message = json.dumps(
        {
            "grade_label": grade_label,
            "subject_label": subject_label,
            "thread_a_key": thread_a_key,
            "thread_b_key": thread_b_key,
            "thread_a_path": thread_a_path,
            "thread_b_path": thread_b_path,
            "thread_a_items": items_a,
            "thread_b_items": items_b,
        },
        ensure_ascii=False,
        indent=2,
    )
    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )
