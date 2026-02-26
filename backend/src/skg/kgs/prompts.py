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

    high = min(max(min_confidence + 0.15, 0.85), 1.0)
    return (
        f"CONFIDENCE CALIBRATION:\n"
        f"- >={high:.2f} only if the dependency is very clear.\n"
        f"- {min_confidence:.2f}–{high - 0.01:.2f} for plausible prerequisite.\n"
        f"- <{min_confidence:.2f} should generally be omitted."
    )


def _builds_towards_cross_level(
    *,
    lower_grade_label: str,
    lower_items: list[dict[str, Any]],
    min_confidence: float,
    note_suffix: str = "",
    task_description: str,
    task_label: str,
    thread_key: str,
    thread_path: str,
    upper_grade_label: str,
    upper_items: list[dict[str, Any]],
) -> PromptPair:
    """Shared implementation for cross-grade and cross-stage buildsTowards prompts.

    Parameters
    ----------
    lower_grade_label
        The label of the lower grade (e.g., "Grade 3").
    lower_items
        The list of items from the lower grade.
    min_confidence
        The minimum confidence threshold from the config; edges below this should be
        omitted.
    note_suffix
        Optional text appended to the system message (e.g., banded-stage notes).
    task_description
        The description sentence for the TASK header (varies by grade vs stage).
    task_label
        The task label for the TASK header (e.g., "Cross-Grade" or "Cross-Stage").
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

TASK ({task_label} buildsTowards):
{task_description}
Decide which lower-grade items are meaningful prerequisites for upper-grade items.

HARD RULES:
1. Use ONLY the provided items. Do NOT invent new items.
2. Direction constraint: source MUST be from the LOWER grade list, target MUST be from the UPPER grade list.
3. Do NOT emit "obvious but weak" links. Only emit when the lower item truly builds foundation.
4. Prefer fewer, higher-quality edges.

{confidence_block}
        """
    ).strip()

    if note_suffix:
        system_message += note_suffix

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

    high = min(max(min_confidence + 0.05, 0.90), 1.0)
    return (
        f"CONFIDENCE:\n"
        f"- >={high:.2f} only for very strong, teacher-usable connections\n"
        f"- {min_confidence:.2f}–{high - 0.01:.2f} for solid connections\n"
        f"- <{min_confidence:.2f} should usually be omitted"
    )


def _relates_to_cross_level(
    *,
    forbidden_pairs: list[dict[str, str]],
    list_a_grade_label: str,
    list_a_items: list[dict[str, Any]],
    list_b_grade_label: str,
    list_b_items: list[dict[str, Any]],
    max_edges_per_sfi: int,
    min_confidence: float,
    note_suffix: str = "",
    subject_label: str,
    task_description: str,
    task_label: str,
) -> PromptPair:
    """Shared implementation for cross-grade and cross-stage relatesTo prompts.

    Uses neutral "List A"/"List B" positional names so that bidirectional confirmation
    can swap items *and* labels without creating a semantic contradiction in the prompt.

    Parameters
    ----------
    forbidden_pairs
        A list of item pairs (dicts with "a_sfi_uuid" and "b_sfi_uuid") that are
        already connected by buildsTowards and MUST NOT be returned as relatesTo.
    list_a_grade_label
        The grade/level label for the items in List A (e.g., "Grade 3").
    list_a_items
        The list of items for List A.
    list_b_grade_label
        The grade/level label for the items in List B (e.g., "Grade 4").
    list_b_items
        The list of items for List B.
    max_edges_per_sfi
        A soft cap on the number of relatesTo edges per item to keep the graph sparse.
    min_confidence
        The minimum confidence threshold from the config; edges below this should be
        omitted.
    note_suffix
        Optional text appended to the system message (e.g., banded-stage notes).
    subject_label
        The subject label for context (e.g., "Mathematics").
    task_description
        The description sentence for the TASK header (varies by grade vs stage).
    task_label
        The task label for the TASK header (e.g., "Cross-Grade" or "Cross-Stage").

    Returns
    -------
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    confidence_block = _relates_to_confidence_guidance(min_confidence)

    system_message = dedent(
        f"""You are a strict curriculum concept-connection analyst.

TASK ({task_label} relatesTo):
{task_description}
List A contains standards from {list_a_grade_label}. List B contains standards from {list_b_grade_label}.
Some pairs are already connected by buildsTowards and MUST NOT be returned.
For the remaining possibilities, decide which cross-list item pairs are conceptually related (shared concept), but NOT a prerequisite chain.

HARD RULES:
1. Use ONLY the provided items. Do NOT invent new items.
2. Cross-list constraint: one endpoint MUST come from List A (``list_a_items``) and the other MUST come from List B (``list_b_items``).
3. Forbidden pairs: DO NOT output any pair listed in forbidden_pairs (in either direction).
4. Do NOT output weak links. Keep it sparse and teacher-usable.
5. Soft cap: do not exceed about {max_edges_per_sfi} relatesTo edges per item across your output.

{confidence_block}

Note: relatesTo is conceptually UNDIRECTED; you may choose either direction in the output.
        """
    ).strip()

    if note_suffix:
        system_message += note_suffix

    user_message = json.dumps(
        {
            "list_a_grade_label": list_a_grade_label,
            "list_b_grade_label": list_b_grade_label,
            "subject_label": subject_label,
            "forbidden_pairs": forbidden_pairs,
            "list_a_items": list_a_items,
            "list_b_items": list_b_items,
        },
        ensure_ascii=False,
        separators=(",", ":"),  # Remove spaces after commas/colons
    )

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
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

    return _builds_towards_cross_level(
        lower_grade_label=lower_grade_label,
        lower_items=lower_items,
        min_confidence=min_confidence,
        task_description=(
            "You will receive standards from two ADJACENT grades that belong to "
            "the SAME conceptual thread."
        ),
        task_label="Cross-Grade",
        thread_key=thread_key,
        thread_path=thread_path,
        upper_grade_label=upper_grade_label,
        upper_items=upper_items,
    )


def cross_grade_relates_to(
    *,
    forbidden_pairs: list[dict[str, str]],
    list_a_grade_label: str,
    list_a_items: list[dict[str, Any]],
    list_b_grade_label: str,
    list_b_items: list[dict[str, Any]],
    max_edges_per_sfi: int,
    min_confidence: float,
    subject_label: str,
) -> PromptPair:
    """Cross-grade relatesTo between adjacent grades (same subject) excluding
    buildsTowards pairs.

    Parameters
    ----------
    forbidden_pairs
        A list of item pairs (dicts with "a_sfi_uuid" and "b_sfi_uuid") that are
        already connected by buildsTowards and MUST NOT be returned as relatesTo.
    list_a_grade_label
        The grade label for the items in List A (e.g., "Grade 3").
    list_a_items
        The list of items for List A.
    list_b_grade_label
        The grade label for the items in List B (e.g., "Grade 4").
    list_b_items
        The list of items for List B.
    max_edges_per_sfi
        A soft cap on the number of relatesTo edges per item to keep the graph sparse.
    min_confidence
        The minimum confidence threshold from the config; edges below this should be
        omitted.
    subject_label
        The subject label for context (e.g., "Mathematics").

    Returns
    -------
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    return _relates_to_cross_level(
        forbidden_pairs=forbidden_pairs,
        list_a_grade_label=list_a_grade_label,
        list_a_items=list_a_items,
        list_b_grade_label=list_b_grade_label,
        list_b_items=list_b_items,
        max_edges_per_sfi=max_edges_per_sfi,
        min_confidence=min_confidence,
        subject_label=subject_label,
        task_description=(
            "You will receive two lists of standards from ADJACENT grades in the SAME subject."
        ),
        task_label="Cross-Grade",
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
    """Cross-stage buildsTowards between adjacent *level ranges* within a normalized
    thread.

    Used when at least one side is a banded/stage bucket (e.g., I–II, III–VI). Despite
    the name "stage", this function is called only for adjacent level ranges in the
    pipeline; it must NOT encourage skipping intermediate levels.

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

    return _builds_towards_cross_level(
        lower_grade_label=lower_grade_label,
        lower_items=lower_items,
        min_confidence=min_confidence,
        note_suffix=(
            "\n\nNOTE: The level labels may be *banded stages* (e.g., I–II, III–VI), "
            "not single grades. Treat this as adjacent level *ranges*; do not invent "
            "per-grade steps and do not assume missing intermediate grades beyond what "
            "is provided."
        ),
        task_description=(
            "You will receive standards from two ADJACENT level ranges (each may be a "
            "single grade or a banded stage) that belong to the SAME conceptual thread."
        ),
        task_label="Cross-Stage",
        thread_key=thread_key,
        thread_path=thread_path,
        upper_grade_label=upper_grade_label,
        upper_items=upper_items,
    )


def cross_stage_relates_to(
    *,
    forbidden_pairs: list[dict[str, str]],
    list_a_grade_label: str,
    list_a_items: list[dict[str, Any]],
    list_b_grade_label: str,
    list_b_items: list[dict[str, Any]],
    max_edges_per_sfi: int,
    min_confidence: float,
    subject_label: str,
) -> PromptPair:
    """Cross-stage relatesTo between adjacent *level ranges* within a subject,
    excluding buildsTowards pairs.

    Used when at least one side is a banded/stage bucket. Called only for adjacent
    level ranges in the pipeline; must not encourage skipping levels.

    Parameters
    ----------
    forbidden_pairs
        A list of item pairs (dicts with "a_sfi_uuid" and "b_sfi_uuid") that are
        already connected by buildsTowards and MUST NOT be returned as relatesTo.
    list_a_grade_label
        The grade/level label for the items in List A (e.g., "Grade 3").
    list_a_items
        The list of items for List A.
    list_b_grade_label
        The grade/level label for the items in List B (e.g., "Grade 5").
    list_b_items
        The list of items for List B.
    max_edges_per_sfi
        A soft cap on the number of relatesTo edges per item to keep the graph sparse.
    min_confidence
        The minimum confidence threshold from the config; passed through to the
        underlying cross-grade prompt.
    subject_label
        The subject label for context (e.g., "Mathematics").

    Returns
    -------
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    return _relates_to_cross_level(
        forbidden_pairs=forbidden_pairs,
        list_a_grade_label=list_a_grade_label,
        list_a_items=list_a_items,
        list_b_grade_label=list_b_grade_label,
        list_b_items=list_b_items,
        max_edges_per_sfi=max_edges_per_sfi,
        min_confidence=min_confidence,
        note_suffix=(
            "\n\nNOTE: The level labels may be *banded stages* (e.g., I–II, III–VI), "
            "not single grades. Only emit relatesTo when the overlap is genuinely "
            "useful for teaching across these adjacent levels."
        ),
        subject_label=subject_label,
        task_description=(
            "You will receive two lists of standards from ADJACENT level ranges "
            "(each may be a single grade or a banded stage) in the SAME subject."
        ),
        task_label="Cross-Stage",
    )


def decompose_atomic_skills(
    *,
    display_language: str,
    items: list[dict[str, Any]],
    max_per_sfi: int,
    min_per_sfi: int,
    require_rationale: bool,
) -> PromptPair:
    """Decompose expectation statements into atomic skills (Learning Components).

    Parameters
    ----------
    display_language
        The language name in which the skill descriptions should be written (e.g.,
        "English" or "French").
    items
        The list of StandardsFrameworkItems to decompose, each with 'sfi_uuid' and
        'statement' fields.
    max_per_sfi
        The maximum number of skills to return per SFI to keep the graph manageable.
    min_per_sfi
        The minimum number of skills to return per SFI to ensure sufficient granularity.
    require_rationale
        Whether to require a non-empty rationale for each skill, which can improve
        quality but may reduce recall.

    Returns
    -------
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    rationale_req = (
        "Each AtomicSkill MUST include a non-empty `rationale`."
        if require_rationale
        else "`rationale` may be null or omitted."
    )

    system_message = dedent(
        f"""You are decomposing curriculum expectation statements into ATOMIC SKILLS.

OUTPUT FORMAT:
- Return ONLY valid JSON matching the AtomicSkillsResponse schema:
  {{ "items": [ {{ "sfi_uuid": <uuid>, "skills": [ {{ "skill_label": "...", "description": "...", "rationale": "..." }} ] }} ] }}

INPUT FIELDS (per SFI):
- `display_text`: the human-readable expectation statement — base your decomposition on THIS field.
- `id_source_text`: the stable canonical text used for ID generation (often identical to display_text; ignore unless display_text is missing).
- `topic_context` / `aux_statements`: optional contextual hints — use them to inform decomposition but do NOT decompose them directly.

HARD RULES:
1. Use ONLY the provided `sfi_uuid` values. Do not invent UUIDs.
2. For each input SFI, return between {min_per_sfi} and {max_per_sfi} skills.
3. Skills must be *atomic*, actionable, and measurable. Avoid teacher activities/resources.
4. Do NOT paraphrase the entire standard as a single skill unless it is already atomic.
5. Do NOT invent prerequisites or unrelated skills.
6. `skill_label` MUST be English-only snake_case (short and stable).
7. `description` MUST be written in language: {display_language}.
8. No duplicate skills within an SFI (dedupe by description meaning).
9. Keep rationales brief (1–2 sentences max). {rationale_req}
"""
    )

    user_message = (
        dedent(
            """Decompose the following expectation SFIs into atomic skills.

INPUT SFIs (JSON):
"""
        )
        + json.dumps(
            {"items": items},
            ensure_ascii=False,
            separators=(",", ":"),  # Remove spaces after commas/colons
        )
    )

    return PromptPair(system_message=system_message, user_message=user_message)


def double_check_atomic_skills() -> PromptPair:
    """Extra user message to trigger a careful second pass for atomic skills.

    Returns
    -------
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    user_message = dedent(
        """**Hmmmm, are you absolutely sure of your results?**

Carefully review your last output against the instructions and double-check:

1. Output is valid JSON and matches AtomicSkillsResponse exactly.
2. Every input `sfi_uuid` appears exactly once in `items`.
3. Each SFI has 1..N skills within the specified bounds.
4. `skill_label` is snake_case English and short/stable.
5. `description` is an atomic skill (not an activity/resource) in the required language.
6. No duplicates within an SFI.

Return a complete corrected AtomicSkillsResponse object."""
    )

    return PromptPair(system_message="", user_message=user_message)


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
4. Direction constraint: items are presented in the order they appear in the curriculum document (by position within their parent section, then by statement code). You MUST NOT point from a later item to an earlier item — i.e., source must have a lower list index than target.

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
