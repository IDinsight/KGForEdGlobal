"""This module contains functionalities related to LLM calls for page IR **extraction**."""

# Standard Library
from pathlib import Path

# Third Party Library
from loguru import logger
from pydantic_ai import Agent, BinaryContent, ModelRetry, ModelSettings

# Package Library
from skg.config import Settings
from skg.page_ir_extraction.prompts import extract_page_ir_from_pdf_page
from skg.page_ir_extraction.schemas import PageIR
from skg.page_ir_extraction.utils import persist_page_ir_attempt_artifacts
from skg.page_ir_extraction.validators import (
    PageIRExtractionQualityCtx,
    QualityError,
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

DEFAULT_MODEL_SETTINGS = ModelSettings(**Settings.TEXT_GENERATION_DEFAULT)
DEFAULT_OUTPUT_RETRIES = 3


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
    """Extract PageIR from a page image using pydantic-ai Agent + Vision + Structured
    Outputs.

    The Agent is constructed per-call so that the output validator closure can capture
    page-specific context (image dimensions, attempt counter, artifact directory). The
    Agent itself is lightweight — the expensive part is the LLM call, not Agent
    construction.

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

    Raises
    ------
    UnexpectedModelBehavior
        If extraction fails after all retries.
    """

    # Build prompts.
    prompts = extract_page_ir_from_pdf_page(
        image_height=image_height,
        image_width=image_width,
        languages=languages,
        page_index=page_index,
    )

    # Read the PNG bytes for BinaryContent input.
    png_bytes = png_fp.read_bytes()

    # Mutable attempt counter shared with the output validator closure.
    attempt_counter = {"value": 0}

    # ------------------------------------------------------------------
    # Construct the Agent with an output validator
    # ------------------------------------------------------------------
    agent = Agent(
        model,
        instructions=prompts.system_message,
        model_settings=DEFAULT_MODEL_SETTINGS,
        output_retries=max_retries,
        output_type=PageIR,
    )

    @agent.output_validator
    def validate_page_ir_quality(output: PageIR) -> PageIR:
        """Validate quality of the parsed PageIR.

        Runs the same validators as before. On failure, persists artifacts and
        raises ModelRetry so pydantic-ai appends the error to the conversation
        and retries.
        """

        attempt = attempt_counter["value"]

        # Populate fields that Python fills post-extraction.
        output.image_width = image_width
        output.image_height = image_height
        output.page_index = page_index

        try:
            verify_page_ir_extraction_quality(
                attempt=attempt,
                image_height=image_height,
                image_width=image_width,
                page_ir=output,
            )
        except QualityError as e:
            # Persist the failed attempt artifacts.
            persist_page_ir_attempt_artifacts(
                attempt=attempt,
                error=e,
                model=model,
                output_text=None,  # pydantic-ai doesn't expose raw text here
                page_index=page_index,
                parsed=output,
                raw_page_irs_dir=raw_page_irs_dir,
            )

            truncated_msg = str(e)[:500]
            logger.error(
                f"Quality check failed on page {page_index}, "
                f"attempt {attempt}: {truncated_msg}"
            )

            attempt_counter["value"] += 1

            # ModelRetry tells pydantic-ai to append this error to the
            # conversation and ask the model to correct its output.
            raise ModelRetry(
                f"Your output had quality issues and must be corrected.\n"
                f"ERROR: {str(e)}\n\n"
                f"Return a complete PageIR that matches the schema and fixes "
                f"the issue."
            ) from e

        # Success — persist and increment.
        persist_page_ir_attempt_artifacts(
            attempt=attempt,
            error=None,
            model=model,
            output_text=None,
            page_index=page_index,
            parsed=output,
            raw_page_irs_dir=raw_page_irs_dir,
        )
        attempt_counter["value"] += 1

        return output

    user_prompt: list[str | BinaryContent] = [
        prompts.user_message,
        BinaryContent(data=png_bytes, media_type="image/png"),
    ]

    result = agent.run_sync(user_prompt, model_settings=DEFAULT_MODEL_SETTINGS)
    page_ir = result.output

    print(f"{page_ir = }")
    exit()
    # Populate remaining Python-side fields.
    page_ir.coord_space = "px"
    page_ir.doc_key = None  # Filled by caller
    page_ir.dpi = None  # Filled by caller
    page_ir.pdf_name = None  # Filled by caller

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

    # Create context object for validators to share information and avoid redundant
    # computations.
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
        tol=2.0,  # Small tolerance for rounding
        top_level_bboxes=[],
    )

    # Call validators. NB: Order can matter here so don't change unless you really know
    # what you are doing!
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
