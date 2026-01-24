"""This module contains functionalities related to LLM calls for canonical IR creation."""

# Standard Library
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
from skg.canonical_ir.schemas import (
    GroupingCanonicalizationKey,
    GroupingCanonicalizationMap,
    SegmentDecision,
)
from skg.canonical_ir.validators import (
    validate_chunked_table_context_matches_prior_context,
    validate_chunked_table_outer_anchors_in_context_groupings,
    validate_context_groupings_no_duplicate_roles,
    validate_context_groupings_required_for_emit,
    validate_context_groupings_role_order,
    validate_context_groupings_supported_by_outer_evidence,
    validate_emitted_statements_have_outer_anchor,
    validate_groupings_not_outer_than_context,
    validate_heading_segments_emit_groupings,
    validate_ignore_unresolved_emit_nothing,
    validate_leaf_codes_use_local_code,
    validate_leaf_list_marker_not_code,
    validate_row_groupings_no_duplicate_roles,
    validate_row_groupings_supported_by_row_cells,
    validate_row_leaf_hierarchy_not_flattened,
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
from skg.prompts.canonical_ir import (
    decide_on_segment,
    double_check_decision_on_segment,
    grouping_canonicalization_instructions,
)
from skg.schemas import Limits
from skg.utils.constants import GroupingCanonicalizationAction

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
        temperature=0,
        text_format=GroupingCanonicalizationMap,
        top_p=1,
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
            grouping_keys=grouping_keys, mapping=parsed
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
    doc_key: str,
    input_items: list[Any],
    instructions: str,
    model: str,
    row_range_end: int | None,
    row_range_start: int | None,
    segment: Segment,
    segment_payload: dict[str, Any] | None,
) -> SegmentDecision:
    """Wrapper for segment decision API calls with retries.

    Parameters
    ----------
    always_double_check_first_attempt
        Whether to force a retry on the first attempt. Useful for difficult/messy pages.
    attempt
        The segment decision attempt number (0-based).
    doc_key
        The document key.
    input_items
        The list of messages to send to the OpenAI API.
    instructions
        The extraction instructions to include.
    model
        The OpenAI model to use.
    row_range_end
        The optional row range end for table segments.
    row_range_start
        The optional row range start for table segments.
    segment
        The segment to decide on.
    segment_payload
        Optional additional payload for the segment.

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
            temperature=0,
            text_format=SegmentDecision,
            top_p=1,
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
            segment=segment,
            segment_decision=parsed,
            segment_payload=segment_payload,
        )
    except (ValidationError, QualityError) as e:
        # Attach the raw output so the correction attempt can see what it wrote.
        raise QualityError(str(e), failed_content=output_text) from e

    return parsed


def _process_canonicalization_batch(
    *,
    batch_index: int,
    batch_keys: list[GroupingCanonicalizationKey],
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
        grouping_keys=batch_keys, known_canonical_keys=known_canonicals_list
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


def generate_grouping_canonicalization_map(
    *,
    batch_size: int = 100,
    doc_key: str,
    grouping_keys: list[GroupingCanonicalizationKey],
    max_retries: int = 2,
    model: str,
) -> GroupingCanonicalizationMap:
    """Generate a global GroupingCanonicalizationMap, using incremental batching to
    maintain context across limits.

    Parameters
    ----------
    batch_size
        Number of items to process per LLM call.
    doc_key
        The document key.
    grouping_keys
        The list of grouping canonicalization keys to process.
    max_retries
        Maximum number of retries for quality errors.
    model
        The OpenAI model to use.

    Returns
    -------
    GroupingCanonicalizationMap
        The generated GroupingCanonicalizationMap.
    """

    if not grouping_keys:
        return GroupingCanonicalizationMap(doc_key=doc_key, generator=model, items=[])

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
            doc_key=doc_key,
            known_canonicals_list=known_canonicals_list,
            max_retries=max_retries,
            model=model,
            num_grouping_keys=num_grouping_keys,
        )
        all_canonical_items.extend(batch_result.items)

        # Update the context set for the next batch.
        for item in batch_result.items:
            # DROP produces no canonical outputs.
            if item.action == GroupingCanonicalizationAction.DROP:
                continue

            # KEEP often has output=[], but the input IS the canonical anchor.
            if item.action == GroupingCanonicalizationAction.KEEP:
                canonical_outputs = [item.input]
            else:
                # REPLACE/MERGE: outputs are the new canonical groupings.
                canonical_outputs = item.output or []

            for out in canonical_outputs:
                if out.title:
                    known_canonical_set.add((out.role.value, out.title.strip()))

    return GroupingCanonicalizationMap(
        doc_key=doc_key, generator=model, items=all_canonical_items
    )


def generate_segment_decision(
    *,
    always_double_check_first_attempt: bool,
    doc_key: str,
    max_retries: int = 2,
    model: str,
    row_range_end: int | None = None,
    row_range_start: int | None = None,
    segment: Segment,
    segment_payload: dict[str, Any] | None = None,
) -> SegmentDecision:
    """Generate a SegmentDecision using the LLM with retries.

    Parameters
    ----------'
    always_double_check_first_attempt
        Whether to force a retry on the first attempt. Useful for difficult/messy pages.
    doc_key
        The document key.
    max_retries
        Maximum number of retries for quality errors.
    model
        The OpenAI model to use.
    row_range_end
        The optional row range end for table segments.
    row_range_start
        The optional row range start for table segments.
    segment
        The segment to decide on.
    segment_payload
        Optional additional payload for the segment.

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

    prompts = decide_on_segment(
        segment=segment_payload or segment.model_dump(mode="json")
    )
    instructions = prompts.system_message
    input_items = [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": prompts.user_message}],
        },
    ]

    for attempt in range(max_retries + 1):
        try:
            return _call_openai_api_to_decide_on_segment(
                always_double_check_first_attempt=always_double_check_first_attempt,
                attempt=attempt,
                doc_key=doc_key,
                input_items=input_items,
                instructions=instructions,
                model=model,
                row_range_end=row_range_end,
                row_range_start=row_range_start,
                segment=segment,
                segment_payload=segment_payload,
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

            if always_double_check_first_attempt and attempt == 0:
                input_items.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": double_check_decision_on_segment().user_message,
                            }
                        ],
                    }
                )
            else:
                input_items.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    f"Your previous output had issues and must be corrected.\n"
                                    f"ERROR: {str(e)}\n\n"
                                    f"Return a complete SegmentDecision that matches the schema and fixes the issue."
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
    mapping: GroupingCanonicalizationMap,
) -> None:
    """Deterministic quality checks:

    1. Mapping covers ALL input keys exactly once.
    2. Mapping contains no unknown extra inputs.

    Parameters
    ----------
    grouping_keys
        The expected grouping canonicalization input keys.
    mapping
        The GroupingCanonicalizationMap to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    def _grouping_key_tuple(
        k: GroupingCanonicalizationKey,
    ) -> tuple[str, str, str, str]:
        """Convert a GroupingCanonicalizationKey to a comparable tuple.

        Parameters
        ----------
        k
            The GroupingCanonicalizationKey to convert.

        Returns
        -------
        tuple[str, str, str, str]
            The comparable tuple representation.
        """

        return k.role.value, k.title, k.local_code or "", k.source_label or ""

    expected = {_grouping_key_tuple(k) for k in grouping_keys}
    got = {_grouping_key_tuple(item.input) for item in mapping.items}
    missing = expected - got
    extra = got - expected

    if missing or extra:
        msg = []

        if missing:
            msg.append(f"Missing {len(missing)} input keys in mapping.")
        if extra:
            msg.append(f"Found {len(extra)} unexpected input keys in mapping.")

        raise QualityError(" ".join(msg))


def verify_segment_decision_quality(
    *,
    always_double_check_first_attempt: bool,
    attempt: int,
    segment: Segment,
    segment_decision: SegmentDecision,
    segment_payload: dict[str, Any] | None = None,
) -> None:
    """Validate the quality of a segment decision.

    Parameters
    ----------
    always_double_check_first_attempt
        Whether to force a retry on the first attempt. Useful for difficult/messy pages.
    attempt
        The current attempt number (0-based).
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.
    segment_payload
        Optional additional payload for the segment.

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
    validate_context_groupings_required_for_emit(
        segment=segment,
        segment_decision=segment_decision,
        segment_payload=segment_payload,
    )
    validate_context_groupings_supported_by_outer_evidence(
        segment=segment,
        segment_decision=segment_decision,
        segment_payload=segment_payload,
    )
    validate_context_groupings_role_order(
        segment=segment, segment_decision=segment_decision
    )
    validate_context_groupings_no_duplicate_roles(
        segment=segment, segment_decision=segment_decision
    )
    validate_chunked_table_context_matches_prior_context(
        segment=segment,
        segment_decision=segment_decision,
        segment_payload=segment_payload,
    )
    validate_chunked_table_outer_anchors_in_context_groupings(
        segment=segment,
        segment_decision=segment_decision,
        segment_payload=segment_payload,
    )
    validate_table_context_groupings_exclude_row_local_roles(
        segment=segment, segment_decision=segment_decision
    )
    validate_row_groupings_supported_by_row_cells(
        segment=segment,
        segment_decision=segment_decision,
        segment_payload=segment_payload,
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
        segment=segment, segment_decision=segment_decision
    )
