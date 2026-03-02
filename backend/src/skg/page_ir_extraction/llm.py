"""This module contains the orchestration logic for page IR extraction via LLM.

The Agent definitions and output-validation wiring live in `agents.py`. This module is
responsible for prompt construction, image loading, running the extraction and
validation agents, and populating the remaining Python-side fields on the returned
PageIR.

Orchestration flow
------------------

1. Build extraction prompts (optionally including validation feedback from a prior
   attempt).
2. Create a fresh extraction agent and run it. The agent's internal output validator
   handles Python quality checks (validators.py) via ModelRetry.
3. Once the extraction agent produces a PageIR that passes all Python quality checks,
   run the validation agent with the same source image and the extracted JSON.
4. If the validation agent returns a passing verdict, return the PageIR.
5. If the validation agent returns a failing verdict, format the issues as feedback and
   loop back to step 1 (up to `max_validation_attempts`).
"""

# Standard Library
from pathlib import Path

# Third Party Library
from loguru import logger
from pydantic_ai import BinaryContent

# Package Library
from skg.page_ir_extraction.agents import (
    DEFAULT_MODEL_SETTINGS,
    DEFAULT_OUTPUT_RETRIES,
    create_page_ir_extraction_agent,
    create_page_ir_validation_agent,
)
from skg.page_ir_extraction.prompts import (
    extract_page_ir_from_pdf_page,
    validate_page_ir_extraction,
)
from skg.page_ir_extraction.schemas import PageIR, ValidationVerdict
from skg.page_ir_extraction.validators import (
    PageIRExtractionQualityCtx,
    validate_artifacts_are_true_artifacts,
    validate_basic_block_invariants,
    validate_continuity_for_extraction,
    validate_extraction_text_constraints,
    validate_figure_blocks_are_well_formed,
    validate_footnote_blocks_are_plausible,
    validate_full_page_bboxes,
    validate_full_page_figure_requires_double_check,
    validate_gross_reading_order,
    validate_image_dimensions,
    validate_item_bboxes_required_and_in_bounds,
    validate_no_duplicate_item_bboxes,
    validate_placeholder_bboxes,
    validate_table_integrity,
)
from skg.utils.constants import BlockType

DEFAULT_MAX_VALIDATION_ATTEMPTS = 2


def _format_validation_feedback(verdict: ValidationVerdict) -> str:
    """Format a failing ValidationVerdict into feedback text for the extraction agent.

    The formatted feedback is appended to the extraction agent's user message on the
    next attempt so it can address the specific issues identified by the validation
    agent.

    Parameters
    ----------
    verdict
        The failing ValidationVerdict.

    Returns
    -------
    str
        Formatted feedback string.
    """

    lines = [
        "## VALIDATION FEEDBACK FROM PREVIOUS ATTEMPT",
        "",
        "Your previous extraction was reviewed by a separate validation agent and "
        "FAILED validation.",
        f"Rationale: {verdict.rationale}",
        "",
        "Issues found:",
    ]

    for i, issue in enumerate(verdict.issues, 1):
        idx_part = (
            f" (item index: {issue.item_index})" if issue.item_index is not None else ""
        )
        lines.append(f"{i}. [{issue.severity.upper()}]{idx_part} {issue.description}")

    lines.append("")
    lines.append(
        "Fix ALL error-severity issues in your new extraction. "
        "Return a complete, corrected PageIR JSON."
    )

    return "\n".join(lines)


def _run_validation_agent(
    *,
    image_height: int,
    image_width: int,
    model: str,
    page_index: int,
    page_ir: PageIR,
    png_bytes: bytes,
) -> ValidationVerdict:
    """Run the validation agent to compare an extracted PageIR against the source image.

    Creates a fresh validation agent (no conversation history) and invokes it with the
    serialized PageIR JSON and the source PNG.

    Parameters
    ----------
    image_height
        The image height in pixels.
    image_width
        The image width in pixels.
    model
        The model identifier (e.g., 'openai:gpt-5.2-2025-12-11').
    page_index
        The 0-based page index.
    page_ir
        The extracted PageIR to validate.
    png_bytes
        The raw PNG bytes of the source page image.

    Returns
    -------
    ValidationVerdict
        The structured validation verdict.
    """

    page_ir_json = page_ir.model_dump_json(indent=2)

    prompts = validate_page_ir_extraction(
        image_height=image_height,
        image_width=image_width,
        page_index=page_index,
        page_ir_json=page_ir_json,
    )

    agent = create_page_ir_validation_agent(
        instructions=prompts.system_message, model=model
    )

    user_prompt: list[str | BinaryContent] = [
        prompts.user_message,
        BinaryContent(data=png_bytes, media_type="image/png"),
    ]
    result = agent.run_sync(user_prompt, model_settings=DEFAULT_MODEL_SETTINGS)

    return result.output


def extract_page_ir(
    *,
    image_height: int,
    image_width: int,
    languages: list[str],
    max_retries: int = DEFAULT_OUTPUT_RETRIES,
    max_validation_attempts: int = DEFAULT_MAX_VALIDATION_ATTEMPTS,
    model: str,
    page_index: int,
    png_fp: Path,
    raw_page_irs_dir: Path,
) -> PageIR:
    """Extract PageIR from a page image using an extraction agent with validation.

    The extraction agent uses a vision model and structured outputs to produce a
    PageIR. After the extraction agent's internal quality checks pass, a separate
    validation agent compares the result against the source image. If validation fails,
    the extraction agent is re-invoked with the validation feedback.

    Parameters
    ----------
    image_height
        The image height in pixels.
    image_width
        The image width in pixels.
    languages
        Expected languages context for the prompt.
    max_retries
        Maximum number of quality-error retries within each extraction attempt
        (correction turns handled by pydantic-ai's ModelRetry).
    max_validation_attempts
        Maximum number of extraction -> validation cycles. The extraction agent is
        re-invoked with validation feedback on each failed cycle.
    model
        The model identifier (e.g., 'openai:gpt-5.2-2025-12-11').
    page_index
        The 0-based page index.
    png_fp
        The PNG file path of the page image.
    raw_page_irs_dir
        Directory to save raw page IR extraction artifacts.

    Returns
    -------
    PageIR
        The extracted PageIR. Python-side provenance fields (coord_space, doc_key, dpi,
        image_height, image_width, page_index, pdf_name) are populated where known. The
        caller is responsible for setting the remaining fields (coord_space, doc_key,
        dpi, and pdf_name).
    """

    png_bytes = png_fp.read_bytes()
    validation_feedback: str | None = None
    page_ir: PageIR | None = None

    for validation_attempt in range(max_validation_attempts):
        # Build extraction prompts.
        prompts = extract_page_ir_from_pdf_page(
            image_height=image_height,
            image_width=image_width,
            languages=languages,
            page_index=page_index,
        )

        # Append validation feedback from a prior failed validation cycle.
        user_message_text = prompts.user_message

        if validation_feedback is not None:
            user_message_text = f"{user_message_text}\n\n{validation_feedback}"

        # Create a fresh extraction agent and run it.
        agent = create_page_ir_extraction_agent(
            image_height=image_height,
            image_width=image_width,
            instructions=prompts.system_message,
            max_retries=max_retries,
            model=model,
            page_index=page_index,
            raw_page_irs_dir=raw_page_irs_dir,
            verify_quality_fn=verify_page_ir_extraction_quality,
        )

        user_prompt: list[str | BinaryContent] = [
            user_message_text,
            BinaryContent(data=png_bytes, media_type="image/png"),
        ]
        result = agent.run_sync(user_prompt, model_settings=DEFAULT_MODEL_SETTINGS)
        page_ir = result.output

        # Run the validation agent.
        verdict = _run_validation_agent(
            image_height=image_height,
            image_width=image_width,
            model=model,
            page_index=page_index,
            page_ir=page_ir,
            png_bytes=png_bytes,
        )

        # If validation passed, we're done.
        if verdict.passed:
            logger.success(
                f"Page {page_index}: validation passed "
                f"(validation attempt {validation_attempt})."
            )

            break

        # Validation failed: format feedback for the next extraction attempt.
        logger.warning(
            f"Page {page_index}: validation failed "
            f"(validation attempt {validation_attempt}): "
            f"{verdict.rationale[:300]}"
        )
        validation_feedback = _format_validation_feedback(verdict)
    else:
        logger.warning(
            f"Page {page_index}: validation did not pass after "
            f"{max_validation_attempts} attempt(s). Returning last extraction."
        )

    assert page_ir is not None, (
        f"page_ir is None after {max_validation_attempts} validation attempt(s) for "
        f"page {page_index}. This should never happen — the extraction agent must "
        f"produce at least one PageIR."
    )

    return page_ir


def verify_page_ir_extraction_quality(
    *,
    attempt: int,
    image_height: int,
    image_width: int,
    page_ir: PageIR,
) -> None:
    """Validate *quality* (not schema) of a parsed PageIR.

    Parameters
    ----------
    attempt
        The extraction attempt number (0-based).
    image_height
        Rendered page image height in pixels.
    image_width
        Rendered page image width in pixels.
    page_ir
        Parsed PageIR object.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    ctx = PageIRExtractionQualityCtx(
        boundary_state=page_ir.boundary_state,
        image_height=image_height,
        image_width=image_width,
        items=page_ir.items,
        non_artifact_items=[
            (i, item)
            for i, item in enumerate(page_ir.items)
            if item.kind != "block" or item.block_type != BlockType.ARTIFACT
        ],
        page_bbox=(0.0, 0.0, float(image_width), float(image_height)),
        page_ir=page_ir,
        tol=2.0,
        top_level_bboxes=[],
    )

    # NB: Order matters — don't change unless you really know what you are doing!
    validate_image_dimensions(ctx)
    validate_extraction_text_constraints(ctx)
    validate_item_bboxes_required_and_in_bounds(ctx)
    validate_full_page_bboxes(ctx)
    validate_full_page_figure_requires_double_check(attempt=attempt, ctx=ctx)
    validate_no_duplicate_item_bboxes(ctx)
    validate_basic_block_invariants(ctx)
    validate_footnote_blocks_are_plausible(ctx)
    validate_figure_blocks_are_well_formed(ctx)
    validate_artifacts_are_true_artifacts(ctx)
    validate_table_integrity(ctx)
    validate_placeholder_bboxes(ctx)
    validate_continuity_for_extraction(ctx)
    validate_gross_reading_order(ctx)
