"""This module contains the orchestration logic for page IR extraction via LLM.

The Agent definitions and output-validation wiring live in `agents.py`. This module is
responsible for prompt construction, image loading, running the extraction and
validation agents, and populating the remaining Python-side fields on the returned
PageIR.

Orchestration flow
------------------

1. Build extraction prompts (optionally including PDF-derived text/table hints).
2. Create a fresh extraction agent (lower reasoning effort) and run it. The agent's
   internal output validator handles Python quality checks (validators.py) via
   ModelRetry.
3. Once the extraction agent produces a PageIR that passes all Python quality checks,
   run the validation agent (higher reasoning effort) with the same source image and
   the extracted JSON.
4. If the validation agent returns a passing verdict, return the extraction agent's
   PageIR.
5. If the validation agent returns a failing verdict, it also provides a corrected
   PageIR that has passed the same Python quality checks (enforced by the validation
   agent's own output validator with retries). Return the corrected PageIR.
"""

# Standard Library
from pathlib import Path

# Third Party Library
import pymupdf

from loguru import logger
from pydantic_ai import BinaryContent

# Package Library
from skg.page_ir_extraction.agents import (
    create_page_ir_extraction_agent,
    create_page_ir_validation_agent,
)
from skg.page_ir_extraction.prompts import (
    extract_page_ir_from_pdf_page,
    validate_page_ir_extraction,
)
from skg.page_ir_extraction.schemas import PageIR, ValidationVerdict
from skg.page_ir_extraction.utils import (
    ExtractionUsageTracker,
    extract_page_text_layer_hints,
)
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


def _run_validation_agent(
    *,
    image_height: int,
    image_width: int,
    model: str,
    page_index: int,
    page_ir: PageIR,
    png_bytes: bytes,
    usage_tracker: ExtractionUsageTracker,
) -> ValidationVerdict:
    """Run the validation agent to compare an extracted PageIR against the source image.

    Creates a fresh validation agent (no conversation history) and invokes it with the
    serialized PageIR JSON and the source PNG. The validation agent returns a
    structured verdict; when the verdict is failing, it also includes a corrected
    PageIR that has passed the same Python quality checks as the extraction agent's
    output (enforced by the validation agent's output validator).

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
    usage_tracker
        Tracker to accumulate validation agent usage.

    Returns
    -------
    ValidationVerdict
        The structured validation verdict (with corrected_page_ir when failing).
    """

    logger.info(f"Running validation agent for page: {page_index + 1}...")

    prompts = validate_page_ir_extraction(
        image_height=image_height,
        image_width=image_width,
        page_index=page_index,
        page_ir_json=page_ir.model_dump_json(),
    )

    agent = create_page_ir_validation_agent(
        image_height=image_height,
        image_width=image_width,
        instructions=prompts.system_message,
        model=model,
        page_index=page_index,
        verify_quality_fn=verify_page_ir_extraction_quality,
    )

    user_prompt: list[str | BinaryContent] = [
        prompts.user_message,
        BinaryContent(data=png_bytes, media_type="image/png"),
    ]
    result = agent.run_sync(user_prompt)
    usage_tracker.validation.add_run_usage(result.usage())

    logger.success(f"Finished running validation agent for page: {page_index + 1}.")

    return result.output


def extract_page_ir(
    *,
    image_height: int,
    image_width: int,
    languages: list[str],
    model: str,
    page_index: int,
    pdf_page: pymupdf.Page | None = None,
    png_fp: Path,
    raw_page_irs_dir: Path,
    usage_tracker: ExtractionUsageTracker,
) -> PageIR:
    """Extract PageIR from a page image using an extraction agent with validation.

    Orchestration flow:

    1. The extraction agent (lower reasoning effort) produces an initial PageIR. Its
        internal output validator runs Python quality checks via ModelRetry.
    2. Once the extraction agent produces a quality-passing PageIR, the validation
        agent (higher reasoning effort) compares it against the source image.
    3. If the validation agent finds errors, it returns a corrected PageIR alongside
        its verdict. The corrected PageIR is also quality-checked by the validation
        agent's own output validator (same Python checks, with retries).
    4. If validation passes, the extraction agent's PageIR is returned directly.

    This single-pass design avoids re-invoking the weaker extraction agent with
    feedback it cannot see its own prior output for, and leverages the stronger
    validation model's ability to edit an existing extraction.

    Parameters
    ----------
    image_height
        The image height in pixels.
    image_width
        The image width in pixels.
    languages
        Expected languages context for the prompt.
    model
        The model identifier (e.g., 'openai:gpt-5.2-2025-12-11').
    page_index
        The 0-based page index.
    pdf_page
        The PyMuPDF page object for extracting text-layer and table-layer hints. When
        provided, hints are extracted and passed to the extraction agent as
        supplementary context for character-level spelling accuracy. May be None if the
        PDF is not available or hints are not desired.
    png_fp
        The PNG file path of the page image.
    raw_page_irs_dir
        Directory to save raw page IR extraction artifacts.
    usage_tracker
        Tracker to accumulate token usage from both extraction and validation agents.

    Returns
    -------
    PageIR
        The extracted (or corrected) PageIR. Python-side provenance fields
        (coord_space, doc_key, dpi, image_height, image_width, page_index, pdf_name)
        are populated where known. The caller is responsible for setting the remaining
        fields (coord_space, doc_key, dpi, and pdf_name).
    """

    png_bytes = png_fp.read_bytes()

    # Extract text-layer and table-layer hints from the PDF page (if available).
    table_layer_hint: str | None = None
    text_layer_hint: str | None = None

    if pdf_page is not None:
        hints = extract_page_text_layer_hints(page=pdf_page, page_index=page_index)
        table_layer_hint = hints.table_hint
        text_layer_hint = hints.text_hint

        if hints.has_hints:
            table_flag = "yes" if table_layer_hint else "no"
            text_flag = "yes" if text_layer_hint else "no"
            logger.info(
                f"Page {page_index + 1}: PDF hints available — "
                f"text_layer={text_flag}, table_layer={table_flag}."
            )

    # Run the extraction agent (lower reasoning effort).
    prompts = extract_page_ir_from_pdf_page(
        image_height=image_height,
        image_width=image_width,
        languages=languages,
        page_index=page_index,
        table_layer_hint=table_layer_hint,
        text_layer_hint=text_layer_hint,
    )

    agent = create_page_ir_extraction_agent(
        image_height=image_height,
        image_width=image_width,
        instructions=prompts.system_message,
        model=model,
        page_index=page_index,
        raw_page_irs_dir=raw_page_irs_dir,
        verify_quality_fn=verify_page_ir_extraction_quality,
    )

    user_prompt: list[str | BinaryContent] = [
        prompts.user_message,
        BinaryContent(data=png_bytes, media_type="image/png"),
    ]
    result = agent.run_sync(user_prompt)
    page_ir = result.output
    usage_tracker.extraction.add_run_usage(result.usage())

    # Run the validation agent (higher reasoning effort). If the verdict is failing,
    # the validation agent returns a corrected PageIR that has already passed the same
    # Python quality checks (enforced by its own output validator).
    verdict = _run_validation_agent(
        image_height=image_height,
        image_width=image_width,
        model=model,
        page_index=page_index,
        page_ir=page_ir,
        png_bytes=png_bytes,
        usage_tracker=usage_tracker,
    )

    if verdict.passed:
        logger.success(f"Page {page_index + 1}: validation passed.")

        return page_ir

    # Validation failed: use the corrected PageIR from the validation agent.
    logger.warning(
        f"Page {page_index + 1}: validation failed: {verdict.rationale[:300]}"
    )

    if verdict.corrected_page_ir is not None:
        logger.info(
            f"Page {page_index + 1}: using corrected PageIR from validation agent."
        )

        return verdict.corrected_page_ir

    # Fallback: if the validation agent somehow failed to provide a corrected PageIR
    # (should not happen given schema validators, but defensive). Return the original.
    logger.warning(
        f"Page {page_index + 1}: validation agent did not provide corrected_page_ir. "
        f"Returning original extraction."
    )

    return page_ir


def verify_page_ir_extraction_quality(
    *, attempt: int, image_height: int, image_width: int, page_ir: PageIR
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
