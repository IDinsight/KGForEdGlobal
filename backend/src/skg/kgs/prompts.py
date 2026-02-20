"""This module contains prompt templates for Learning Progressions inference."""

# Standard Library
import json

from textwrap import dedent
from typing import Any

# Package Library
from skg.utils.general import PromptPair


def _builds_towards_confidence_guidance(min_confidence: float) -> str:
    """Generate the CONFIDENCE section for buildsTowards prompts.

    Parameters
    ----------
    min_confidence
        The minimum confidence threshold below which edges should be omitted.

    Returns
    -------
    str
        A formatted confidence calibration block for inclusion in the system prompt.
    """

    high = max(min_confidence + 0.15, 0.85)
    return (
        f"CONFIDENCE CALIBRATION:\n"
        f"- >={high:.2f} only if the dependency is very clear.\n"
        f"- {min_confidence:.2f}–{high - 0.01:.2f} for plausible prerequisite.\n"
        f"- <{min_confidence:.2f} should generally be omitted."
    )


def _relates_to_confidence_guidance(min_confidence: float) -> str:
    """Generate the CONFIDENCE section for relatesTo prompts.

    Parameters
    ----------
    min_confidence
        The minimum confidence threshold below which edges should be omitted.

    Returns
    -------
    str
        A formatted confidence calibration block for inclusion in the system prompt.
    """

    high = max(min_confidence + 0.05, 0.90)
    return (
        f"CONFIDENCE:\n"
        f"- >={high:.2f} only for very strong, teacher-usable connections\n"
        f"- {min_confidence:.2f}–{high - 0.01:.2f} for solid connections\n"
        f"- <{min_confidence:.2f} should usually be omitted"
    )


def cross_grade_builds_towards(
    *,
    lower_grade_label: str,
    lower_items: list[dict[str, Any]],
    min_confidence: float,
    thread_key: str,
    thread_path: str,
    upper_grade_label: str,
    upper_items: list[dict[str, Any]],
) -> PromptPair:
    """Cross-grade buildsTowards between adjacent grades within a normalized thread.

    Parameters
    ----------
    lower_grade_label
        The label of the lower grade (e.g., "Grade 3").
    lower_items
        The list of items from the lower grade.
    min_confidence
        The minimum confidence threshold from the config; edges below this should be
        omitted.
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
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    confidence_block = _builds_towards_confidence_guidance(min_confidence)

    system_message = dedent(
        f"""You are a strict curriculum learning progression analyst.

TASK (Cross-Grade buildsTowards):
You will receive standards from two ADJACENT grades that belong to the SAME conceptual thread.
Decide which lower-grade items are meaningful prerequisites for upper-grade items.

HARD RULES:
1. Use ONLY the provided items. Do NOT invent new items.
2. Direction constraint: source MUST be from the LOWER grade list, target MUST be from the UPPER grade list.
3. Do NOT emit "obvious but weak" links. Only emit when the lower item truly builds foundation.
4. Prefer fewer, higher-quality edges.

{confidence_block}
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
        separators=(",", ":"),  # Remove spaces after commas/colons
    )

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )


def cross_grade_relates_to(
    *,
    forbidden_pairs: list[dict[str, str]],
    lower_grade_label: str,
    lower_items: list[dict[str, Any]],
    max_edges_per_sfi: int,
    min_confidence: float,
    subject_label: str,
    upper_grade_label: str,
    upper_items: list[dict[str, Any]],
) -> PromptPair:
    """Cross-grade relatesTo between adjacent grades (same subject) excluding
    buildsTowards pairs.

    Parameters
    ----------
    forbidden_pairs
        A list of item pairs (dicts with "a_sfi_uuid" and "b_sfi_uuid") that are
        already connected by buildsTowards and MUST NOT be returned as relatesTo.
    lower_grade_label
        The label of the lower grade (e.g., "Grade 3").
    lower_items
        The list of items from the lower grade.
    max_edges_per_sfi
        A soft cap on the number of relatesTo edges per item to keep the graph sparse.
    min_confidence
        The minimum confidence threshold from the config; edges below this should be
        omitted.
    subject_label
        The subject label for context (e.g., "Mathematics").
    upper_grade_label
        The label of the upper grade (e.g., "Grade 4").
    upper_items
        The list of items from the upper grade.

    Returns
    -------
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    confidence_block = _relates_to_confidence_guidance(min_confidence)

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

{confidence_block}

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
        separators=(",", ":"),  # Remove spaces after commas/colons
    )

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )


def cross_stage_builds_towards(
    *,
    lower_grade_label: str,
    lower_items: list[dict[str, Any]],
    min_confidence: float,
    thread_key: str,
    thread_path: str,
    upper_grade_label: str,
    upper_items: list[dict[str, Any]],
) -> PromptPair:
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
    min_confidence
        The minimum confidence threshold from the config; passed through to the
        underlying cross-grade prompt.
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
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    p = cross_grade_builds_towards(
        lower_items=lower_items,
        lower_grade_label=lower_grade_label,
        min_confidence=min_confidence,
        thread_key=thread_key,
        thread_path=thread_path,
        upper_grade_label=upper_grade_label,
        upper_items=upper_items,
    )

    # Add one strong sentence so the model does not assume these are single grades.
    return PromptPair(
        system_message=(
            p.system_message
            + "\n\nNOTE: The level labels may be *banded stages* (e.g., I–II, III–VI), not single grades. Treat this as adjacent level *ranges*; do not invent per-grade steps."
        ),
        user_message=p.user_message,
    )


def cross_stage_relates_to(
    *,
    forbidden_pairs: list[dict[str, str]],
    lower_grade_label: str,
    lower_items: list[dict[str, Any]],
    max_edges_per_sfi: int,
    min_confidence: float,
    subject_label: str,
    upper_grade_label: str,
    upper_items: list[dict[str, Any]],
) -> PromptPair:
    """Cross-stage relatesTo between non-adjacent grades within a subject, excluding
    buildsTowards pairs.

    This is a more exploratory prompt to identify potential "long-range" connections
    that might be missed in the adjacent-grade prompt. The model should be encouraged
    to identify strong connections even if they skip intermediate grades, but should
    not be forced to invent edges if the connections are weak or superficial.

    Parameters
    ----------
    forbidden_pairs
        A list of item pairs (dicts with "a_sfi_uuid" and "b_sfi_uuid") that are
        already connected by buildsTowards and MUST NOT be returned as relatesTo.
    lower_grade_label
        The label of the lower grade (e.g., "Grade 3").
    lower_items
        The list of items from the lower grade.
    max_edges_per_sfi
        A soft cap on the number of relatesTo edges per item to keep the graph sparse.
    min_confidence
        The minimum confidence threshold from the config; passed through to the
        underlying cross-grade prompt.
    subject_label
        The subject label for context (e.g., "Mathematics").
    upper_grade_label
        The label of the upper grade (e.g., "Grade 5").
    upper_items
        The list of items from the upper grade.

    Returns
    -------
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    p = cross_grade_relates_to(
        forbidden_pairs=forbidden_pairs,
        lower_grade_label=lower_grade_label,
        lower_items=lower_items,
        max_edges_per_sfi=max_edges_per_sfi,
        min_confidence=min_confidence,
        subject_label=subject_label,
        upper_grade_label=upper_grade_label,
        upper_items=upper_items,
    )

    # Add one strong sentence so the model does not assume these are single grades.
    return PromptPair(
        system_message=(
            p.system_message
            + "\n\nNOTE: The level labels may be *banded stages* (e.g., I–II, III–VI), not single grades. "
            "Only emit relatesTo when the overlap is genuinely useful for teaching across these adjacent levels."
        ),
        user_message=p.user_message,
    )


def double_check_learning_progressions() -> PromptPair:
    """Extra user message to trigger a careful second pass.

    Returns
    -------
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
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

    return PromptPair(system_message="", user_message=user_message.strip())


def within_grade_builds_towards(
    *,
    grade_label: str,
    items: list[dict[str, Any]],
    min_confidence: float,
    thread_path: str,
) -> PromptPair:
    """Within-grade buildsTowards in a single (grade, thread) bucket.

    Parameters
    ----------
    grade_label
        The grade label for the items (e.g., "Grade 3").
    items
        The list of items in the grade/thread bucket, presented in intended sequence
        order.
    min_confidence
        The minimum confidence threshold from the config; edges below this should be
        omitted.
    thread_path
        The conceptual thread path for the items (e.g., "Mathematics > Geometry >
        Shapes").

    Returns
    -------
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    confidence_block = _builds_towards_confidence_guidance(min_confidence)

    system_message = dedent(
        f"""You are a strict curriculum learning progression analyst.

TASK (Within-Grade buildsTowards):
Given a list of standards (StandardsFrameworkItems) that belong to the SAME grade and SAME thread, decide which prerequisite relationships exist among them.

Definitions:
- buildsTowards(A -> B) means learning A is a meaningful prerequisite for learning B. It is NOT just "related" or "in the same topic"; it should be instructional dependency.

HARD RULES:
1. Use ONLY the provided items. Do NOT invent new items.
2. Only emit edges that are plausible prerequisites a teacher would rely on.
3. Prefer fewer, higher-quality edges over many weak edges.
4. Direction constraint: items are presented in intended sequence order. You MUST NOT point from a later item to an earlier item.

{confidence_block}

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
        separators=(",", ":"),  # Remove spaces after commas/colons
    )

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )


def within_grade_relates_to(
    *,
    grade_label: str,
    items_a: list[dict[str, Any]],
    items_b: list[dict[str, Any]],
    max_edges_per_sfi: int,
    min_confidence: float,
    subject_label: str,
    thread_a_key: str,
    thread_b_key: str,
    thread_a_path: str,
    thread_b_path: str,
) -> PromptPair:
    """Within-grade relatesTo between two different threads (which may be from different
    subjects) in the same grade.

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
    min_confidence
        The minimum confidence threshold from the config; edges below this should be
        omitted.
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
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    confidence_block = _relates_to_confidence_guidance(min_confidence)

    system_message = dedent(
        f"""You are a strict curriculum concept-connection analyst.

TASK (Within-Grade relatesTo):
Two different threads (which may be from different subjects) within the SAME grade are provided.
Identify conceptual associations between items across the two threads.

Definition:
- relatesTo(A -- B) means the concepts meaningfully overlap such that a teacher would reasonably connect them instructionally (reinforcement, application, shared concept), BUT it is NOT a prerequisite chain.

HARD RULES:
1. Use ONLY the provided items. Do NOT invent new items.
2. Only emit edges ACROSS the two threads: one endpoint must come from ``thread_a_items``, the other from ``thread_b_items``.
3. Do NOT output edges that are "related" only because they are in the same grade.
4. Keep it sparse: prefer a small number of strong conceptual links.
5. Soft cap: do not exceed about {max_edges_per_sfi} relatesTo edges per item across your output.

{confidence_block}

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
        separators=(",", ":"),  # Remove spaces after commas/colons
    )
    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )
