"""This module contains functionalities related to LLM calls for canonical IR creation."""

# Standard Library
from collections import defaultdict
from typing import Any

# Third Party Library
from loguru import logger
from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from pydantic import ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

# Package Library
from skg.canonical_ir.prompts import (
    decide_on_segment,
    double_check_decision_on_segment,
    grouping_canonicalization_instructions,
    heading_level_instructions,
)
from skg.canonical_ir.schemas import (
    GroupingCanonicalizationItem,
    GroupingCanonicalizationKey,
    GroupingCanonicalizationMap,
    HeadingLevelAnalysis,
    SegmentDecision,
)
from skg.canonical_ir.utils import CanonicalIRDirs, normalize_heading_key
from skg.canonical_ir.validators import (
    validate_chunked_table_context_matches_prior_context,
    validate_chunked_table_first_chunk_must_not_ignore_or_unresolved,
    validate_chunked_table_outer_anchors_in_context_groupings,
    validate_context_groupings_no_duplicate_roles,
    validate_context_groupings_required_for_emit,
    validate_context_groupings_role_order,
    validate_context_groupings_supported_by_outer_evidence,
    validate_emit_flagged_unresolved_confidence,
    validate_emitted_statements_have_outer_anchor,
    validate_established_canonicals,
    validate_grouping_canonicalization_coverage,
    validate_groupings_not_outer_than_context,
    validate_heading_segments_emit_groupings,
    validate_ignore_unresolved_emit_nothing,
    validate_leaf_codes_use_local_code,
    validate_leaf_list_marker_not_code,
    validate_row_groupings_no_duplicate_roles,
    validate_row_groupings_supported_by_row_cells,
    validate_row_leaf_hierarchy_not_flattened,
    validate_row_leaves_supported_by_cell,
    validate_section_titles_not_front_matter,
    validate_segment_kind_coherence,
    validate_table_context_groupings_exclude_row_local_roles,
    validate_table_header_rows_not_emitted,
    validate_table_row_index,
    validate_table_split_explosion,
    validate_unique_table_rows,
)
from skg.document_ir.schemas import Segment
from skg.page_ir_extraction.validators import QualityError
from skg.schemas import Limits
from skg.utils.constants import (
    DEFAULT_CONTEXT_GROUPINGS_ROLE_PRECEDENCE,
    GroupingCanonicalizationAction,
    NodeRole,
    SegmentDecisionType,
)
from skg.utils.general import open_json_type, write_to_json

limits = Limits(max_retry_attempts=5)
openai_client = OpenAI()


@retry(
    reraise=True,
    retry=retry_if_exception_type(
        (
            TimeoutError,
            ConnectionError,
            OSError,
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )
    ),
    stop=stop_after_attempt(limits.max_retry_attempts),
    wait=wait_random_exponential(min=1, max=60),
)
def _call_openai_api_to_canonicalize_groupings(
    *,
    doc_key: str,
    input_items: list[Any],
    instructions: str,
    grouping_keys: list[GroupingCanonicalizationKey],
    known_canonicals_list: list[dict[str, str]] | None = None,
    model: str,
) -> GroupingCanonicalizationMap:
    """Wrapper for grouping canonicalization API calls with retries.

    Parameters
    ----------
    doc_key
        The document key.
    input_items
        The list of messages to send to the OpenAI API.
    instructions
        The extraction instructions to include.
    grouping_keys
        The list of grouping canonicalization keys to process.
    known_canonicals_list
        The known canonical keys provided as context (for established canonicals
        validation).
    model
        The OpenAI model to use.

    Returns
    -------
    GroupingCanonicalizationMap
        The generated GroupingCanonicalizationMap.

    Raises
    ------
    QualityError
        If the response could not be parsed or failed quality checks.
    """

    response = openai_client.responses.parse(
        input=input_items,
        instructions=instructions,
        model=model,
        reasoning={"effort": "high"},
        text_format=GroupingCanonicalizationMap,
    )

    parsed = getattr(response, "output_parsed", None)
    output_text = getattr(response, "output_text", None)

    if parsed is None:
        raise QualityError(
            "Grouping canonicalization returned no parsed output.",
            failed_content=output_text,
        )

    parsed.doc_key = doc_key
    parsed.generator = model

    try:
        parsed = GroupingCanonicalizationMap.model_validate(parsed.model_dump())
        verify_grouping_canonicalization_map_quality(
            grouping_keys=grouping_keys,
            known_canonicals_list=known_canonicals_list,
            mapping=parsed,
        )
    except (ValidationError, QualityError) as e:
        # Attach the raw output so the correction attempt can see what it wrote.
        raise QualityError(str(e), failed_content=output_text) from e

    return parsed


@retry(
    reraise=True,
    retry=retry_if_exception_type(
        (
            TimeoutError,
            ConnectionError,
            OSError,
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )
    ),
    stop=stop_after_attempt(limits.max_retry_attempts),
    wait=wait_random_exponential(min=1, max=60),
)
def _call_openai_api_to_decide_on_segment(
    *,
    always_double_check_first_attempt: bool,
    attempt: int,
    context_groupings_role_dict: dict[NodeRole, int],
    doc_key: str,
    input_items: list[Any],
    instructions: str,
    model: str,
    outer_context_roles: list[str],
    row_range_end: int | None,
    row_range_start: int | None,
    segment: Segment,
    segment_decision_conf_threshold: float,
    segment_payload: dict[str, Any] | None,
    table_chunking: dict[str, Any] | None,
) -> SegmentDecision:
    """Wrapper for segment decision API calls with retries.

    Parameters
    ----------
    always_double_check_first_attempt
        Whether to force a retry on the first attempt. Useful for difficult/messy pages.
    attempt
        The segment decision attempt number (0-based).
    context_groupings_role_dict
        The precedence mapping for context grouping roles, used in quality checks to
        enforce consistent ordering of context groupings and determine "outer-ness" for
        chunked table validations.
    doc_key
        The document key.
    input_items
        The list of messages to send to the OpenAI API.
    instructions
        The extraction instructions to include.
    model
        The OpenAI model to use.
    outer_context_roles
        The list of NodeRoles that are considered outer context for chunked tables. For
        chunked tables, groupings with these roles MUST go in context_groupings[] (not
        segment-level groupings[]) so that all chunks share a stable context stack.
    row_range_end
        The optional row range end for table segments.
    row_range_start
        The optional row range start for table segments.
    segment
        The segment to decide on.
    segment_decision_conf_threshold
        The confidence threshold for the segment decision. emit_flagged_unresolved is
        only valid when confidence is below this threshold.
    segment_payload
        Optional additional payload for the segment.
    table_chunking
        Optional chunking metadata used by downstream quality checks. This is typically
        produced alongside the segment payload builders; for chunked tables it may also
        be present in `segment_payload["chunking"]`.

    Returns
    -------
    SegmentDecision
        The generated SegmentDecision.

    Raises
    ------
    QualityError
        If the response could not be parsed or failed quality checks.
    """

    if attempt == 0 or not always_double_check_first_attempt:
        response = openai_client.responses.parse(
            input=input_items,
            instructions=instructions,
            model=model,
            reasoning={"effort": "high"},
            text_format=SegmentDecision,
        )
    else:
        response = openai_client.responses.parse(
            input=input_items,
            instructions=instructions,
            model=model,
            reasoning={"effort": "high"},
            text_format=SegmentDecision,
        )

    parsed = getattr(response, "output_parsed", None)
    output_text = getattr(response, "output_text", None)

    # Capture the raw text if parsing/validation fails.
    if parsed is None:
        raise QualityError(
            "Segment decision returned no parsed output.", failed_content=output_text
        )

    # Overwrite decision_id, segment_id, and segment_kind to ensure consistency. If the
    # caller is chunking a table, include the row range in the decision_id.
    if row_range_start is not None and row_range_end is not None:
        parsed.decision_id = f"segment_decision:{doc_key}:{segment.segment_id}:{row_range_start}:{row_range_end}"
        parsed.row_range_start = row_range_start
        parsed.row_range_end = row_range_end
    else:
        parsed.decision_id = f"segment_decision:{doc_key}:{segment.segment_id}"
        parsed.row_range_start = None
        parsed.row_range_end = None

    parsed.block_type = segment.block_type if segment.kind == "block" else None
    parsed.segment_kind = segment.kind
    parsed.segment_id = segment.segment_id

    try:
        parsed = SegmentDecision.model_validate(parsed.model_dump())
        verify_segment_decision_quality(
            always_double_check_first_attempt=always_double_check_first_attempt,
            attempt=attempt,
            context_groupings_role_dict=context_groupings_role_dict,
            outer_context_roles=outer_context_roles,
            segment=segment,
            segment_decision=parsed,
            segment_decision_conf_threshold=segment_decision_conf_threshold,
            segment_payload=segment_payload,
            table_chunking=table_chunking,
        )
    except (ValidationError, QualityError) as e:
        # Attach the raw output so the correction attempt can see what it wrote.
        raise QualityError(str(e), failed_content=output_text) from e

    return parsed


@retry(
    reraise=True,
    retry=retry_if_exception_type(
        (
            TimeoutError,
            ConnectionError,
            OSError,
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )
    ),
    stop=stop_after_attempt(limits.max_retry_attempts),
    wait=wait_random_exponential(min=1, max=60),
)
def _call_openai_api_to_generate_heading_levels(
    *, input_items: list[Any], instructions: str, model: str
) -> HeadingLevelAnalysis:
    """Wrapper for heading level assignment API calls with retries.

    Parameters
    ----------
    input_items
        The list of messages to send to the OpenAI API.
    instructions
        The system instructions.
    model
        The OpenAI model to use.

    Returns
    -------
    HeadingLevelAnalysis
        The generated HeadingLevelAnalysis.

    Raises
    ------
    QualityError
        If the response could not be parsed.
    """

    response = openai_client.responses.parse(
        input=input_items,
        instructions=instructions,
        model=model,
        reasoning={"effort": "high"},
        text_format=HeadingLevelAnalysis,
    )

    parsed = getattr(response, "output_parsed", None)
    output_text = getattr(response, "output_text", None)

    # Capture the raw text if parsing/validation fails.
    if parsed is None:
        raise QualityError(
            "Heading level generation returned no parsed output.",
            failed_content=output_text,
        )

    return parsed


def _process_canonicalization_batch(
    *,
    batch_index: int,
    batch_keys: list[GroupingCanonicalizationKey],
    context_groupings_role_dict: dict[NodeRole, int],
    doc_key: str,
    known_canonicals_list: list[dict[str, str]],
    max_retries: int,
    model: str,
    num_grouping_keys: int,
) -> GroupingCanonicalizationMap:
    """Process a single batch with retries and error handling.

    Parameters
    ----------
    batch_index
        The index of the current batch.
    batch_keys
        The list of grouping canonicalization keys in the batch.
    context_groupings_role_dict
        The context grouping role precedence mapping, used for quality checks and LLM
        instructions.
    doc_key
        The document key.
    known_canonicals_list
        The list of known canonical keys for context.
    max_retries
        Maximum number of retries for quality errors.
    model
        The OpenAI model to use.
    num_grouping_keys
        The total number of grouping keys being processed.

    Returns
    -------
    GroupingCanonicalizationMap
        The generated GroupingCanonicalizationMap for the batch.
    """

    logger.info(
        f"Processing canonical grouping batch {batch_index} "
        f"({len(batch_keys)} grouping keys). "
        f"Total number of grouping keys: {num_grouping_keys}. "
        f"Number of known canonicals: {len(known_canonicals_list)}"
    )

    prompts = grouping_canonicalization_instructions(
        context_groupings_role_dict=context_groupings_role_dict,
        grouping_keys=batch_keys,
        known_canonical_keys=known_canonicals_list,
    )
    base_input_items = [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": prompts.user_message}],
        },
    ]
    retry_context: list[dict[str, Any]] = []

    for attempt in range(max_retries + 1):
        current_input_items = base_input_items + retry_context

        try:
            return _call_openai_api_to_canonicalize_groupings(
                doc_key=doc_key,
                input_items=current_input_items,
                instructions=prompts.system_message,
                grouping_keys=batch_keys,
                known_canonicals_list=known_canonicals_list,
                model=model,
            )

        except QualityError as e:
            if attempt >= max_retries:
                logger.error(f"Batch {batch_index} failed final retry.")
                raise

            retry_context = []  # Reset context
            if e.failed_content:
                retry_context.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": e.failed_content}],
                    }
                )

            retry_context.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                f"Your previous output had issues and must be corrected.\n"
                                f"ERROR: {str(e)}\n\n"
                                f"Return a complete GroupingCanonicalizationMap that matches the schema and fixes the issue."
                            ),
                        }
                    ],
                }
            )
            continue
        except Exception as e:  # pylint: disable=broad-except
            # Let transient errors propagate (tenacity should cover most of these).
            if isinstance(
                e,
                (
                    TimeoutError,
                    ConnectionError,
                    OSError,
                    APIConnectionError,
                    APITimeoutError,
                    RateLimitError,
                    InternalServerError,
                ),
            ):
                raise

            if attempt >= max_retries:
                raise QualityError(f"Structured parse/validation failed: {e}") from e

            # Handle structural/parsing errors.
            retry_context = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                f"The previous response failed structured parsing/validation.\n"
                                f"ERROR: {e.__class__.__name__}: {e}\n\n"
                                f"Return a complete GroupingCanonicalizationMap that matches the schema exactly."
                            ),
                        }
                    ],
                }
            ]
            continue

    raise QualityError(f"Batch {batch_index} failed unexpectedly.")


def _resolve_effective_grouping_outputs(
    *, item: GroupingCanonicalizationItem, min_confidence: float
) -> list[GroupingCanonicalizationKey]:
    """Determine the effective canonical output nodes for a processed item.

    Logic:

    1. If confidence is below threshold, the INPUT is the canonical anchor (treated as
        KEEP).
    2. If action is DROP, there are no outputs.
    3. If action is KEEP, the INPUT is the canonical anchor.
    4. Otherwise (REPLACE/SPLIT), the output field contains the anchors.

    Parameters
    ----------
    item
        The processed canonicalization item containing input/output/action/confidence.
    min_confidence
        The minimum confidence threshold to accept the model's output.

    Returns
    -------
    list[GroupingCanonicalizationKey]
        A list of nodes effectively established as canonical.
    """

    if item.confidence < min_confidence:
        return [item.input]

    if item.action == GroupingCanonicalizationAction.DROP:
        return []

    if item.action == GroupingCanonicalizationAction.KEEP:
        return [item.input]

    return item.output or []


def generate_heading_levels(
    *,
    country: str,
    creation_dirs: CanonicalIRDirs,
    headings: list[dict[str, Any]],
    model: str,
    overwrite: bool,
) -> dict[str, int]:
    """Generate heading levels via LLM and save to disk.

    Parameters
    ----------
    country
        The country for this document, used for LLM context when generating the heading
        levels.
    creation_dirs
        The canonical IR creation directories.
    headings
        The unique headings from `collect_unique_headings`.
    model
        The OpenAI model to use.
    overwrite
        Whether to overwrite an existing cached result.

    Returns
    -------
    dict[str, int]
        Mapping from normalized heading text to structural depth level.

    Raises
    ------
    ValueError
        If the number of generated heading levels does not match the number of input
        headings, or if any heading index is out of range.
    """

    heading_levels_fp = creation_dirs.root / "heading_levels.json"

    if not overwrite and heading_levels_fp.exists():
        logger.warning(
            f"Heading levels JSON already exists at {heading_levels_fp}. "
            f"Reusing existing heading levels. "
            f"If you wish to overwrite, pass the --overwrite flag."
        )
        return open_json_type(heading_levels_fp)

    assert headings, "Cannot generate heading levels: no headings provided."

    logger.info(f"Assigning heading levels for {len(headings)} unique headings...")

    prompts = heading_level_instructions(country=country, headings=headings)
    input_items = [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": prompts.user_message}],
        },
    ]
    parsed = _call_openai_api_to_generate_heading_levels(
        input_items=input_items, instructions=prompts.system_message, model=model
    )
    entries = parsed.entries

    # Validate: we should get exactly one entry per heading.
    if len(entries) != len(headings):
        raise ValueError(
            f"Expected {len(headings)} heading level entries, got {len(entries)}."
        )

    level_map: dict[str, int] = {}

    # Build a normalized-key index so we can detect collisions created by normalization
    # (e.g., punctuation/dash/whitespace variants). We resolve collisions
    # deterministically and record them for audit.
    by_norm: dict[str, list[dict[str, Any]]] = defaultdict(list)

    # Map results back to heading text.
    for entry in entries:
        idx = entry.index
        level = entry.level

        if not 0 <= idx < len(headings):
            raise ValueError(f"Heading index {idx} out of range [0, {len(headings)}).")

        text = headings[idx]["text"]
        norm = normalize_heading_key(text)
        by_norm[norm].append({"index": idx, "text": text, "level": level})

    collisions_report: list[dict[str, Any]] = []

    # Resolve collisions deterministically: choose the maximum level; if there is a
    # tie, choose the smallest heading index. Emit a warning + write an audit file.
    for norm, items in sorted(by_norm.items(), key=lambda kv: kv[0]):
        if len(items) == 1:
            level_map[norm] = items[0]["level"]
            continue

        max_level = max(it["level"] for it in items)
        tied = [it for it in items if it["level"] == max_level]
        chosen = min(tied, key=lambda it: it["index"])
        level_map[norm] = max_level
        collisions_report.append(
            {
                "normalized_key": norm,
                "resolution": "max_level_then_smallest_index",
                "chosen": {
                    "index": chosen["index"],
                    "text": chosen["text"],
                    "level": max_level,
                },
                "candidates": sorted(items, key=lambda it: it["index"]),
            }
        )

    level_map = dict(sorted(level_map.items()))

    if collisions_report:
        logger.warning(
            f"Heading normalization collisions detected while generating heading levels: "
            f"{len(collisions_report)} normalized keys had >1 source heading."
        )
        collisions_fp = creation_dirs.root / "heading_level_collisions.json"

        logger.info(f"Saving heading collision audit to: {collisions_fp}")

        write_to_json(fp=collisions_fp, json_info=collisions_report)

    logger.info(f"Saving heading levels to: {heading_levels_fp}")

    write_to_json(fp=heading_levels_fp, json_info=level_map)

    non_structural = sum(1 for v in level_map.values() if v == 0)

    logger.success(
        f"Saved heading levels to: {heading_levels_fp}. "
        f"Non-structural (level 0): {non_structural}/{len(level_map)} "
        f"(normalized keys; source headings={len(headings)})"
    )

    return level_map


def generate_grouping_canonicalization_map(
    *,
    batch_size: int = 600,
    canonical_grouping_min_confidence: float,
    context_groupings_role_order: list[str],
    creation_dirs: CanonicalIRDirs,
    doc_key: str,
    grouping_keys: list[GroupingCanonicalizationKey],
    max_retries: int = 3,
    model: str,
    overwrite: bool,
) -> GroupingCanonicalizationMap:
    """Generate a global GroupingCanonicalizationMap, using incremental batching to
    maintain context across limits.

    Parameters
    ----------
    batch_size
        Number of items to process per LLM call.
    canonical_grouping_min_confidence
        Minimum confidence threshold for treating a mapping item as applied. Items
        below this threshold are treated as KEEP at apply time (step 13), so we must
        register the *input* (not the output) as the established canonical when
        building cross-batch context.
    context_groupings_role_order
        The context grouping role precedence order, used for quality checks and LLM
        instructions.
    creation_dirs
        The CanonicalIRDirs for this document.
    doc_key
        The document key.
    grouping_keys
        The list of grouping canonicalization keys to process.
    max_retries
        Maximum number of retries for quality errors.
    model
        The OpenAI model to use.
    overwrite
        Whether to overwrite any existing canonicalization map (currently unused).

    Returns
    -------
    GroupingCanonicalizationMap
        The generated GroupingCanonicalizationMap.
    """

    if not grouping_keys:
        return GroupingCanonicalizationMap(doc_key=doc_key, generator=model, items=[])

    group_canonicalization_mapping_fp = (
        creation_dirs.group_mapping / "group_canonicalization_map.json"
    )

    if not overwrite and group_canonicalization_mapping_fp.exists():
        logger.warning(
            f"Group canonicalization mapping JSON already exists at {group_canonicalization_mapping_fp}. "
            f"Reusing existing group canonicalization mapping. "
            f"If you wish to overwrite, pass the --overwrite flag."
        )
        return GroupingCanonicalizationMap.model_validate(
            open_json_type(group_canonicalization_mapping_fp)
        )

    logger.info("Generating grouping canonicalization map...")

    # Sort keys to ensure high-quality anchors come first.
    grouping_keys = sorted(
        grouping_keys,
        key=lambda k: (-len(k.title or ""), k.role.value, (k.title or "")),
    )
    num_grouping_keys = len(grouping_keys)

    # Maintain a unique set of (role, title) tuples established as output standards to
    # pass as context to subsequent batches.
    known_canonical_set: set[tuple[str, str]] = set()

    all_canonical_items = []
    batch_size = min(batch_size, len(grouping_keys))
    context_groupings_role_dict: dict[NodeRole, int] = {
        NodeRole(role): i for i, role in enumerate(context_groupings_role_order)
    }

    for i in range(0, len(grouping_keys), batch_size):
        batch_keys = grouping_keys[i : i + batch_size]
        batch_index = (i // batch_size) + 1

        # Prepare context for this batch.
        known_canonicals_list = [
            {"role": r, "title": t}
            for r, t in sorted(known_canonical_set, key=lambda x: (x[0], x[1]))
        ]

        # Process the batch.
        batch_result = _process_canonicalization_batch(
            batch_index=batch_index,
            batch_keys=batch_keys,
            context_groupings_role_dict=context_groupings_role_dict,
            doc_key=doc_key,
            known_canonicals_list=known_canonicals_list,
            max_retries=max_retries,
            model=model,
            num_grouping_keys=num_grouping_keys,
        )
        all_canonical_items.extend(batch_result.items)

        # Update the context set for the next batch.
        for item in batch_result.items:
            effective_outputs = _resolve_effective_grouping_outputs(
                item=item, min_confidence=canonical_grouping_min_confidence
            )
            known_canonical_set.update(
                (out.role.value, out.title.strip())
                for out in effective_outputs
                if out.title
            )

    logger.success("Finished generating grouping canonicalization map!")

    mapping = GroupingCanonicalizationMap(
        doc_key=doc_key, generator=model, items=all_canonical_items
    )

    write_to_json(fp=group_canonicalization_mapping_fp, json_info=mapping)

    logger.success(
        f"Saved grouping canonicalization mapping: {group_canonicalization_mapping_fp}"
    )

    return mapping


def generate_segment_decision(
    *,
    always_double_check_first_attempt: bool,
    context_groupings_role_order: list[str],
    country: str,
    doc_key: str,
    heading_role_hints: list[dict[str, str]],
    max_retries: int = 3,
    model: str,
    outer_context_roles: list[str],
    row_range_end: int | None = None,
    row_range_start: int | None = None,
    segment: Segment,
    segment_decision_conf_threshold: float,
    segment_payload: dict[str, Any] | None = None,
    table_chunking: dict[str, Any] | None = None,
) -> SegmentDecision:
    """Generate a SegmentDecision using the LLM with retries.

    Parameters
    ----------
    always_double_check_first_attempt
        Whether to force a retry on the first attempt. Useful for difficult/messy pages.
    context_groupings_role_order
        The context grouping role precedence order, used for quality checks and LLM
        instructions.
    country
        The country for this document, used for LLM context when making the segment
        decision.
    doc_key
        The document key.
    heading_role_hints
        List of heading role hints to pass as context for segment decision.
    max_retries
        Maximum number of retries for quality errors.
    model
        The OpenAI model to use.
    outer_context_roles
        The list of NodeRoles that are considered outer context for chunked tables. For
        chunked tables, groupings with these roles MUST go in context_groupings[] (not
        segment-level groupings[]) so that all chunks share a stable context stack.
    row_range_end
        The optional row range end for table segments.
    row_range_start
        The optional row range start for table segments.
    segment
        The segment to decide on.
    segment_decision_conf_threshold
        The confidence threshold for the segment decision.
    segment_payload
        Optional additional payload for the segment.
    table_chunking
        Optional chunking metadata used by quality validators. For chunked tables, the
        caller may also inject this into the LLM payload as
        `segment_payload["chunking"]`, so the prompt’s chunking rules apply.

    Returns
    -------
    SegmentDecision
        The generated SegmentDecision.

    Raises
    ------
    Exception
        For transient API errors.
    QualityError
        If segment decision fails after retries.
    """

    context_groupings_role_dict: dict[NodeRole, int] = {
        NodeRole(role): i for i, role in enumerate(context_groupings_role_order)
    } or DEFAULT_CONTEXT_GROUPINGS_ROLE_PRECEDENCE
    prompts = decide_on_segment(
        country=country,
        context_groupings_role_dict=context_groupings_role_dict,
        heading_role_hints=heading_role_hints,
        outer_context_roles=outer_context_roles,
        segment=segment_payload,
        segment_decision_conf_threshold=segment_decision_conf_threshold,
    )
    instructions = prompts.system_message

    # Expose heading-role-hint patterns as supplementary outer evidence so that
    # validate_context_groupings_supported_by_outer_evidence() can accept context
    # titles that match a known hint pattern (even if the heading was filtered from
    # section_path by page-distance limits).
    #
    # NB: This injection MUST happen AFTER decide_on_segment() above, which serializes
    # segment_payload to JSON for the LLM prompt. The patterns are validator-only
    # data--the LLM never sees them.
    if heading_role_hints and segment_payload is not None:
        segment_payload["_heading_role_hint_patterns"] = [
            h["pattern"] for h in heading_role_hints if h.get("pattern")
        ]

    input_items = [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": prompts.user_message}],
        },
    ]

    logger.debug(f"{input_items = }\n")

    for attempt in range(max_retries + 1):
        try:
            return _call_openai_api_to_decide_on_segment(
                always_double_check_first_attempt=always_double_check_first_attempt,
                attempt=attempt,
                context_groupings_role_dict=context_groupings_role_dict,
                doc_key=doc_key,
                input_items=input_items,
                instructions=instructions,
                model=model,
                outer_context_roles=outer_context_roles,
                row_range_end=row_range_end,
                row_range_start=row_range_start,
                segment=segment,
                segment_decision_conf_threshold=segment_decision_conf_threshold,
                segment_payload=segment_payload,
                table_chunking=table_chunking,
            )
        except QualityError as e:
            if attempt == max_retries:
                logger.error("Segment decision failed after exhausting retries.")
                raise  # Re-raise the final quality error

            # Append the assistant's failed attempt to history first. Without this, the
            # model doesn't know what it's correcting.
            if e.failed_content:
                logger.error(f"Segment decision failed content: {e.failed_content}")
                input_items.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": e.failed_content}],
                    }
                )

            input_items.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                double_check_decision_on_segment().user_message
                                if always_double_check_first_attempt and attempt == 0
                                else f"Your previous output had issues and must be corrected.\nERROR: {str(e)}\n\nReturn a complete SegmentDecision that matches the schema and fixes the issue."
                            ),
                        }
                    ],
                }
            )
            continue
        except Exception as e:  # pylint: disable=broad-except
            # Let transient errors propagate (tenacity should cover most of these).
            if isinstance(
                e,
                (
                    TimeoutError,
                    ConnectionError,
                    OSError,
                    APIConnectionError,
                    APITimeoutError,
                    RateLimitError,
                    InternalServerError,
                ),
            ):
                raise

            # Handle general exceptions (like Pydantic ValidationErrors) that bubble up
            # from the API call but might not have attached text.
            last_error = QualityError(f"Structured parse/validation failed: {e}")

            if attempt >= max_retries:
                raise last_error from e

            # If possible, we should try to add the assistant's context here too, but
            # standard Python Exceptions won't carry the model output unless we wrap
            # them in _call_openai_api_for_page_ir_extraction. For now, we proceed with
            # the Error feedback.
            input_items.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                f"The previous response failed structured parsing/validation.\n"
                                f"ERROR: {e.__class__.__name__}: {e}\n\n"
                                f"Return a complete SegmentDecision that matches the schema exactly."
                            ),
                        }
                    ],
                }
            )
            continue

    raise QualityError(f"Segment decision failed after {max_retries + 1} attempts.")


def verify_grouping_canonicalization_map_quality(
    *,
    grouping_keys: list[GroupingCanonicalizationKey],
    known_canonicals_list: list[dict[str, str]] | None = None,
    mapping: GroupingCanonicalizationMap,
) -> None:
    """Deterministic quality checks:

    1. Mapping covers ALL input keys exactly once, in the same order.
    2. Mapping contains no unknown extra inputs.
    3. Established canonical titles are matched exactly.

    Parameters
    ----------
    grouping_keys
        The expected grouping canonicalization input keys.
    known_canonicals_list
        The known canonical keys provided as context (for established canonicals
        validation).
    mapping
        The GroupingCanonicalizationMap to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    # Coverage check (set match + size match + order match): this subsumes a simple
    # set-based missing/extra check.
    validate_grouping_canonicalization_coverage(
        grouping_keys=grouping_keys, mapping=mapping
    )

    if known_canonicals_list:
        validate_established_canonicals(
            known_canonicals_list=known_canonicals_list, mapping=mapping
        )


def verify_segment_decision_quality(
    *,
    always_double_check_first_attempt: bool,
    attempt: int,
    context_groupings_role_dict: dict[NodeRole, int],
    outer_context_roles: list[str],
    segment: Segment,
    segment_decision: SegmentDecision,
    segment_decision_conf_threshold: float,
    segment_payload: dict[str, Any] | None = None,
    table_chunking: dict[str, Any] | None = None,
) -> None:
    """Validate the quality of a segment decision.

    Parameters
    ----------
    always_double_check_first_attempt
        Whether to force a retry on the first attempt. Useful for difficult/messy pages.
    attempt
        The current attempt number (0-based).
    context_groupings_role_dict
        The precedence mapping for context grouping roles, used in quality checks to
        enforce consistent ordering of context groupings and determine "outer-ness" for
        chunked table validations.
    outer_context_roles
        The list of NodeRoles that are considered outer context for chunked tables. For
        chunked tables, groupings with these roles MUST go in context_groupings[] (not
        segment-level groupings[]) so that all chunks share a stable context stack.
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.
    segment_decision_conf_threshold
        The confidence threshold. emit_flagged_unresolved is only valid when confidence
        is below this threshold.
    segment_payload
        Optional additional payload for the segment.
    table_chunking
        Optional chunking metadata used by table-related quality validators. For
        chunked tables this may also exist in `segment_payload["chunking"]`.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    # Force retry on first attempt.
    if always_double_check_first_attempt and attempt == 0:
        raise QualityError("Reason does not matter and is overwritten in caller.")

    validate_segment_kind_coherence(segment=segment, segment_decision=segment_decision)
    validate_ignore_unresolved_emit_nothing(
        segment=segment, segment_decision=segment_decision
    )
    validate_chunked_table_first_chunk_must_not_ignore_or_unresolved(
        segment=segment,
        segment_decision=segment_decision,
        segment_payload=segment_payload,
        table_chunking=table_chunking,
    )
    validate_table_row_index(segment=segment, segment_decision=segment_decision)
    validate_unique_table_rows(segment=segment, segment_decision=segment_decision)
    validate_table_header_rows_not_emitted(
        segment=segment, segment_decision=segment_decision
    )
    validate_heading_segments_emit_groupings(
        segment=segment, segment_decision=segment_decision
    )
    validate_section_titles_not_front_matter(
        segment=segment, segment_decision=segment_decision
    )
    validate_table_split_explosion(segment=segment, segment_decision=segment_decision)

    # Internal context sanity checks (safe to enforce even for flagged_unresolved).
    validate_context_groupings_role_order(
        context_groupings_role_dict=context_groupings_role_dict,
        segment=segment,
        segment_decision=segment_decision,
    )
    validate_context_groupings_no_duplicate_roles(
        segment=segment, segment_decision=segment_decision
    )

    validate_emit_flagged_unresolved_confidence(
        segment=segment,
        segment_decision=segment_decision,
        segment_decision_conf_threshold=segment_decision_conf_threshold,
    )

    # Low-confidence flagged_unresolved: accept without strict validators.
    if segment_decision.decision_type == SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED:
        return

    validate_context_groupings_required_for_emit(
        segment=segment,
        segment_decision=segment_decision,
        segment_payload=segment_payload,
    )
    validate_context_groupings_supported_by_outer_evidence(
        segment=segment,
        segment_decision=segment_decision,
        segment_payload=segment_payload,
        table_chunking=table_chunking,
    )
    validate_chunked_table_context_matches_prior_context(
        segment=segment,
        segment_decision=segment_decision,
        segment_payload=segment_payload,
        table_chunking=table_chunking,
    )
    validate_chunked_table_outer_anchors_in_context_groupings(
        outer_context_roles=outer_context_roles,
        segment_decision=segment_decision,
        segment_payload=segment_payload,
        table_chunking=table_chunking,
    )
    validate_table_context_groupings_exclude_row_local_roles(
        segment=segment, segment_decision=segment_decision
    )
    validate_row_groupings_supported_by_row_cells(
        segment=segment,
        segment_decision=segment_decision,
        segment_payload=segment_payload,
        table_chunking=table_chunking,
    )
    validate_row_leaves_supported_by_cell(
        segment=segment,
        segment_decision=segment_decision,
        segment_payload=segment_payload,
        table_chunking=table_chunking,
    )
    validate_row_groupings_no_duplicate_roles(
        segment=segment, segment_decision=segment_decision
    )
    validate_row_leaf_hierarchy_not_flattened(
        segment=segment, segment_decision=segment_decision
    )
    validate_leaf_list_marker_not_code(
        segment=segment, segment_decision=segment_decision
    )
    validate_leaf_codes_use_local_code(
        segment=segment, segment_decision=segment_decision
    )
    validate_emitted_statements_have_outer_anchor(
        segment=segment, segment_decision=segment_decision
    )
    validate_groupings_not_outer_than_context(
        context_groupings_role_dict=context_groupings_role_dict,
        segment=segment,
        segment_decision=segment_decision,
    )
