"""This module contains the orchestration logic for page IR extraction via LLM.

The Agent definition and output-validation wiring live in `agents.py`. This module is
responsible for prompt construction, image loading, running the agent, and populating
the remaining Python-side fields on the returned PageIR.
"""

# Standard Library
from pathlib import Path

# Third Party Library
from pydantic_ai import BinaryContent

# Package Library
from skg.page_ir_extraction.agents import (
    DEFAULT_MODEL_SETTINGS,
    DEFAULT_OUTPUT_RETRIES,
    create_page_ir_extraction_agent,
)
from skg.page_ir_extraction.prompts import extract_page_ir_from_pdf_page
from skg.page_ir_extraction.schemas import PageIR
from skg.page_ir_extraction.validators import (
    PageIRExtractionQualityCtx,
    validate_artifacts_are_true_artifacts,
    validate_basic_block_invariants,
    validate_continuity_for_extraction,
    validate_figure_blocks_are_well_formed,
    validate_footnote_blocks_are_plausible,
    validate_full_page_bboxes,
    validate_full_page_figure_requires_double_check,
    validate_gross_reading_order,
    validate_image_dimensions,
    validate_item_bboxes_required_and_in_bounds,
    validate_no_duplicate_item_bboxes,
    validate_no_whitespace_or_empty_blocks,
    validate_placeholder_bboxes,
    validate_table_integrity,
)
from skg.utils.constants import BlockType


def extract_page_ir(
    *,
    image_height: int,
    image_width: int,
    languages: list[str],
    max_retries: int = DEFAULT_OUTPUT_RETRIES,
    model: str,
    page_index: int,
    png_fp: Path,
    raw_page_irs_dir: Path,
) -> PageIR:
    """Extract PageIR from a page image using pydantic-ai Agent, vision model, and
    structured outputs.

    Parameters
    ----------
    image_height
        The image height in pixels.
    image_width
        The image width in pixels.
    languages
        Expected languages context for the prompt.
    max_retries
        Maximum number of quality-error retries (correction turns).
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
        The extracted PageIR.
    """

    # Build prompts.
    prompts = extract_page_ir_from_pdf_page(
        image_height=image_height,
        image_width=image_width,
        languages=languages,
        page_index=page_index,
    )

    # Create the agent with quality validation function wired in.
    agent, _ = create_page_ir_extraction_agent(
        image_height=image_height,
        image_width=image_width,
        instructions=prompts.system_message,
        max_retries=max_retries,
        model=model,
        page_index=page_index,
        raw_page_irs_dir=raw_page_irs_dir,
        verify_quality_fn=verify_page_ir_extraction_quality,
    )

    # Run the agent.
    png_bytes = png_fp.read_bytes()
    user_prompt: list[str | BinaryContent] = [
        prompts.user_message,
        BinaryContent(data=png_bytes, media_type="image/png"),
    ]
    result = agent.run_sync(user_prompt, model_settings=DEFAULT_MODEL_SETTINGS)
    page_ir = result.output

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
    validate_no_whitespace_or_empty_blocks(ctx)
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
