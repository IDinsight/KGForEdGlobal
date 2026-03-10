"""This module contains the orchestration logic for page IR continuity verification
via LLM.

The Agent definitions and output-validation wiring live in `agents.py`. This module is
responsible for prompt construction, image loading, running the verification and
validation agents, and returning the final PageIRContinuityVerdict.

Orchestration flow
------------------

1. Build verification prompts and create a fresh verification agent (no reasoning).
2. Run the verification agent to produce a PageIRContinuityVerdict. The agent's
   internal output validator handles context-dependent quality checks via ModelRetry.
3. Once the verification agent produces a passing verdict, run the validation agent
   (higher reasoning effort) with the same source images and the verification JSON.
4. If the validation agent returns a passing verdict, return the verification agent's
   PageIRContinuityVerdict.
5. If the validation agent returns a failing verdict, it also provides a corrected
   PageIRContinuityVerdict that has passed the same quality checks (enforced by the
   validation agent's own output validator with retries). Return the corrected verdict.
"""

# Standard Library
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Third Party Library
from loguru import logger
from pydantic_ai import BinaryContent

# Package Library
from skg.page_ir_extraction.schemas import Block, Table
from skg.page_ir_verification.agents import (
    create_continuity_validation_agent,
    create_continuity_verification_agent,
)
from skg.page_ir_verification.prompts import (
    validate_page_ir_continuity_verdict,
    verify_page_ir_pairs_from_extraction,
)
from skg.page_ir_verification.schemas import (
    ContinuityValidationVerdict,
    PageIRContinuityVerdict,
)
from skg.page_ir_verification.validators import (
    validate_item_continuation_kind,
    validate_repeats_header_requires_table_item,
    validate_semantic_flow,
)
from skg.utils.general import AgentUsageBucket


@dataclass
class VerificationUsageTracker:
    """Track LLM token usage across the entire verification pipeline run.

    Maintains separate buckets for each agent type and provides a summary suitable for
    persisting in `verification_run.json`.
    """

    verification: AgentUsageBucket
    validation: AgentUsageBucket

    def __init__(self) -> None:
        """Initialize empty usage buckets for verification and validation agents."""

        self.verification = AgentUsageBucket(agent_name="verification")
        self.validation = AgentUsageBucket(agent_name="validation")

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-friendly dictionary with per-agent and total summaries.

        Returns
        -------
        dict[str, object]
            Dictionary containing `agents` breakdown and `totals`.
        """

        verification_d = self.verification.to_dict()
        validation_d = self.validation.to_dict()

        totals = {
            "cache_read_tokens": (
                self.verification.cache_read_tokens + self.validation.cache_read_tokens
            ),
            "cache_write_tokens": (
                self.verification.cache_write_tokens
                + self.validation.cache_write_tokens
            ),
            "input_tokens": (
                self.verification.input_tokens + self.validation.input_tokens
            ),
            "output_tokens": (
                self.verification.output_tokens + self.validation.output_tokens
            ),
            "requests": self.verification.requests + self.validation.requests,
            "runs": self.verification.runs + self.validation.runs,
            "total_tokens": (
                self.verification.cache_read_tokens
                + self.verification.cache_write_tokens
                + self.verification.input_tokens
                + self.verification.output_tokens
                + self.validation.cache_read_tokens
                + self.validation.cache_write_tokens
                + self.validation.input_tokens
                + self.validation.output_tokens
            ),
        }

        return {
            "agents": {"verification": verification_d, "validation": validation_d},
            "totals": totals,
        }


def _run_validation_agent(
    *,
    min_confidence_to_patch: float,
    min_confidence_to_select_positive: float,
    min_confidence_to_stop_negative_search: float,
    model: str,
    next_item: Block | Table,
    next_item_excerpt: dict[str, Any],
    next_page_index: int,
    next_png_bytes: bytes,
    prev_item: Block | Table,
    prev_item_excerpt: dict[str, Any],
    prev_page_index: int,
    prev_png_bytes: bytes,
    usage_tracker: VerificationUsageTracker,
    verdict: PageIRContinuityVerdict,
) -> ContinuityValidationVerdict:
    """Run the validation agent to check a continuity verdict against the source images.

    Parameters
    ----------
    min_confidence_to_patch
        Positive verdicts at or above this threshold may be patched into PageIR state.
    min_confidence_to_select_positive
        Positive verdicts at or above this threshold may outrank negatives during
        candidate-pair selection.
    min_confidence_to_stop_negative_search
        Same-family primary-primary negative verdicts at or above this threshold may
        stop alternate candidate-pair search.
    model
        The model identifier.
    next_item
        The parsed next page candidate item.
    next_item_excerpt
        The excerpt JSON of the next candidate.
    next_page_index
        The 0-based index of the next page.
    next_png_bytes
        Raw PNG bytes of the next page (cropped).
    prev_item
        The parsed previous page candidate item.
    prev_item_excerpt
        The excerpt JSON of the previous candidate.
    prev_page_index
        The 0-based index of the previous page.
    prev_png_bytes
        Raw PNG bytes of the previous page.
    usage_tracker
        Tracker to accumulate validation agent usage.
    verdict
        The verification verdict to validate.

    Returns
    -------
    ContinuityValidationVerdict
        The validation verdict (with corrected_verdict when failing).
    """

    prompts = validate_page_ir_continuity_verdict(
        min_confidence_to_patch=min_confidence_to_patch,
        min_confidence_to_select_positive=min_confidence_to_select_positive,
        min_confidence_to_stop_negative_search=min_confidence_to_stop_negative_search,
        next_item_excerpt=next_item_excerpt,
        next_page_index=next_page_index,
        prev_item_excerpt=prev_item_excerpt,
        prev_page_index=prev_page_index,
        verdict_json=verdict.model_dump_json(),
    )

    agent = create_continuity_validation_agent(
        instructions=prompts.system_message,
        model=model,
        next_item=next_item,
        prev_item=prev_item,
        verify_continuity_fn=verify_page_ir_continuity_verdict,
    )

    user_prompt: list[str | BinaryContent] = [
        prompts.user_message,
        "IMAGE A: ENTIRETY of Page N (previous page).",
        BinaryContent(data=prev_png_bytes, media_type="image/png"),
        "IMAGE B: TOP crop of page N+1 (next page).",
        BinaryContent(data=next_png_bytes, media_type="image/png"),
    ]

    result = agent.run_sync(user_prompt)
    usage_tracker.validation.add_run_usage(result.usage())

    return result.output


def verify_page_ir_pairs(
    *,
    min_confidence_to_patch: float,
    min_confidence_to_select_positive: float,
    min_confidence_to_stop_negative_search: float,
    model: str,
    next_item: dict[str, Any],
    next_item_excerpt: dict[str, Any],
    next_page_index: int,
    next_png: Path,
    prev_item: dict[str, Any],
    prev_item_excerpt: dict[str, Any],
    prev_page_index: int,
    prev_png: Path,
    usage_tracker: VerificationUsageTracker,
) -> PageIRContinuityVerdict:
    """Verify continuity between two PageIR excerpts using LLM agents.

    Orchestration:

    1. Run the verification agent (no reasoning) to produce a PageIRContinuityVerdict.
    2. Run the validation agent (high reasoning) to check the verdict.
    3. If validation passes, return the verification verdict.
    4. If validation fails, return the corrected verdict from the validation agent.

    Parameters
    ----------
    min_confidence_to_patch
        Positive verdicts at or above this threshold may be patched into PageIR state.
    min_confidence_to_select_positive
        Positive verdicts at or above this threshold may outrank negatives during
        candidate-pair selection.
    min_confidence_to_stop_negative_search
        Same-family primary-primary negative verdicts at or above this threshold may
        stop alternate candidate-pair search.
    model
        The model identifier.
    next_item
        The candidate item dict near top of page N+1.
    next_item_excerpt
        The excerpt JSON of the candidate item near top of page N+1.
    next_page_index
        The 0-based index of the next page (N+1).
    next_png
        The PNG file path of page N+1 (cropped).
    prev_item
        The candidate item dict near bottom of page N.
    prev_item_excerpt
        The excerpt JSON of the candidate item near bottom of page N.
    prev_page_index
        The 0-based index of the previous page (N).
    prev_png
        The PNG file path of page N.
    usage_tracker
        Tracker to accumulate token usage from both verification and validation agents.

    Returns
    -------
    PageIRContinuityVerdict
        The final continuity verdict (original or corrected).

    Raises
    ------
    Exception
        For transient API errors or agent failures.
    """

    # Parse candidate items once.
    prev_item_parsed: Block | Table = (
        Block.model_validate(prev_item)
        if prev_item["kind"] == "block"
        else Table.model_validate(prev_item)
    )
    next_item_parsed: Block | Table = (
        Block.model_validate(next_item)
        if next_item["kind"] == "block"
        else Table.model_validate(next_item)
    )

    # Run verification agent.
    prompts = verify_page_ir_pairs_from_extraction(
        min_confidence_to_patch=min_confidence_to_patch,
        min_confidence_to_select_positive=min_confidence_to_select_positive,
        min_confidence_to_stop_negative_search=min_confidence_to_stop_negative_search,
        next_item=next_item_excerpt,
        next_page_index=next_page_index,
        prev_item=prev_item_excerpt,
        prev_page_index=prev_page_index,
    )

    agent = create_continuity_verification_agent(
        instructions=prompts.system_message,
        model=model,
        next_item=next_item_parsed,
        prev_item=prev_item_parsed,
        verify_continuity_fn=verify_page_ir_continuity_verdict,
    )

    prev_png_bytes = prev_png.read_bytes()
    next_png_bytes = next_png.read_bytes()

    user_prompt: list[str | BinaryContent] = [
        prompts.user_message,
        "IMAGE A: ENTIRETY of Page N (previous page).",
        BinaryContent(data=prev_png_bytes, media_type="image/png"),
        "IMAGE B: TOP crop of page N+1 (next page).",
        BinaryContent(data=next_png_bytes, media_type="image/png"),
    ]

    result = agent.run_sync(user_prompt)
    usage_tracker.verification.add_run_usage(result.usage())
    verdict = result.output

    logger.info(
        f"Verification agent verdict for pages {prev_page_index + 1}-{next_page_index + 1}: "
        f"is_continuation={verdict.is_continuation}, "
        f"continuation_kind={verdict.continuation_kind.value}, "
        f"confidence={verdict.confidence}"
    )

    # Run validation agent.
    logger.info(
        f"Running validation agent for pages {prev_page_index + 1}-{next_page_index + 1}..."
    )

    validation_verdict = _run_validation_agent(
        min_confidence_to_patch=min_confidence_to_patch,
        min_confidence_to_select_positive=min_confidence_to_select_positive,
        min_confidence_to_stop_negative_search=min_confidence_to_stop_negative_search,
        model=model,
        next_item=next_item_parsed,
        next_item_excerpt=next_item_excerpt,
        next_page_index=next_page_index,
        next_png_bytes=next_png_bytes,
        prev_item=prev_item_parsed,
        prev_item_excerpt=prev_item_excerpt,
        prev_page_index=prev_page_index,
        prev_png_bytes=prev_png_bytes,
        usage_tracker=usage_tracker,
        verdict=verdict,
    )

    # Return original or corrected verdict.
    if validation_verdict.passed:
        logger.success(
            f"Pages {prev_page_index + 1}-{next_page_index + 1}: validation passed."
        )
        return verdict

    logger.warning(
        f"Pages {prev_page_index + 1}-{next_page_index + 1}: validation failed: "
        f"{validation_verdict.rationale[:300]}"
    )

    # Schema guarantees corrected_verdict is non-null when passed=false.
    logger.info(
        f"Pages {prev_page_index + 1}-{next_page_index + 1}: using corrected verdict "
        f"from validation agent."
    )

    return validation_verdict.corrected_verdict


def verify_page_ir_continuity_verdict(
    *,
    next_item: Block | Table,
    prev_item: Block | Table,
    verdict: PageIRContinuityVerdict,
) -> None:
    """Validate *quality* (not schema) of a parsed PageIR.

    Parameters
    ----------
    next_item
        The parsed next page candidate item.
    prev_item
        The parsed previous page candidate item.
    verdict
        The PageIRContinuityVerdict to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    # NB: Order matters — don't change unless you really know what you are doing!
    validate_item_continuation_kind(
        next_item=next_item, prev_item=prev_item, verdict=verdict
    )
    validate_repeats_header_requires_table_item(next_item=next_item, verdict=verdict)
    validate_semantic_flow(next_item=next_item, prev_item=prev_item, verdict=verdict)
