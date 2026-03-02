"""This module contains the entry point for extracting structural page Intermediate
Representations (IRs) from raw PDF pages. This is step 1.

Invoke from the backend directory via:

python src/skg/entries/extract_page_ir.py ../examples/tanzania/config.json
"""

# Standard Library
import sys
import traceback

from datetime import datetime, timezone
from pathlib import Path

# Third Party Library
import pymupdf
import typer

from loguru import logger

# Append the framework path. NB: This is required if this entry point is invoked from
# the command line. However, it is not necessary if it is imported from a pip install.
if __name__ == "__main__":
    PACKAGE_PATH = Path(__file__).resolve().parents[2]

    if PACKAGE_PATH not in sys.path:
        print(f"Appending '{PACKAGE_PATH}' to system path...")
        sys.path.append(str(PACKAGE_PATH))

# Package Library
from skg.page_ir_extraction.llm import extract_page_ir
from skg.page_ir_extraction.schemas import PageIR
from skg.page_ir_extraction.utils import (
    persist_extraction_run,
    read_png_dimensions,
    render_and_save_page_to_png,
)
from skg.schemas import ExtractionConfig, RunConfig
from skg.utils.general import PipelineDirs, open_json_type, write_to_json
from skg.utils.pdf import validate_page_count

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


def extract_page_by_page(
    *,
    config: ExtractionConfig,
    doc: pymupdf.Document,
    doc_key: str,
    end_page: int,
    extraction_dirs: PipelineDirs,
    start_page: int,
) -> None:
    """Perform page-by-page extraction of PageIR components from the PDF document.

    Parameters
    ----------
    config
        The extraction run configuration.
    doc
        The PyMuPDF document to extract from.
    doc_key
        The document key.
    end_page
        0-based end page (exclusive).
    extraction_dirs
        The extraction directories.
    start_page
        0-based start page (inclusive).
    """

    total_pages = end_page - start_page

    for page_index in range(start_page, end_page):
        page_ir_fp = extraction_dirs.page_irs / f"{page_index:04d}.json"
        png_fp = extraction_dirs.page_images / f"{page_index:04d}.png"

        if not config.overwrite and page_ir_fp.exists() and png_fp.exists():
            logger.info(
                f"Page IR JSON and PNG already exist for page {page_index}. "
                f"Skipping page IR extraction AND PNG rendering. "
                f"If you wish to overwrite, pass the --overwrite flag."
            )

            continue

        # Always ensure the PNG exists first. We render if the file is missing OR if we
        # are overwriting (e.g. changed DPI).
        if config.overwrite or not png_fp.exists():
            render_and_save_page_to_png(
                doc=doc, dpi=config.dpi, output_png_fp=png_fp, page_index=page_index
            )

        # Check cache: if not overwriting and extracted Page IR JSON exists, skip
        # entirely.
        if page_ir_fp.exists() and not config.overwrite:
            logger.warning(
                f"Extracted page IR JSON already exists for page {page_index}. "
                f"Skipping page IR extraction. "
                f"If you wish to overwrite, pass the --overwrite flag."
            )

            continue

        # Extract information from the page image.
        logger.info(
            f"Extracting and saving page IR: {page_index - start_page + 1}/{total_pages}..."
        )

        image_width, image_height = read_png_dimensions(png_fp)
        page_ir = extract_page_ir(
            image_height=image_height,
            image_width=image_width,
            languages=config.languages,
            model=config.model,
            page_index=page_index,
            png_fp=png_fp,
            raw_page_irs_dir=extraction_dirs.page_irs_raw,
        )
        page_ir.coord_space = "px"
        page_ir.doc_key = doc_key
        page_ir.dpi = config.dpi
        page_ir.image_height = image_height
        page_ir.image_width = image_width
        page_ir.page_index = page_index
        page_ir.pdf_name = config.pdf_fp.name

        # Re-validate after schema validators.
        page_ir = PageIR.model_validate(page_ir.model_dump(mode="python"))

        # Save PageIR JSON.
        write_to_json(fp=page_ir_fp, json_info=page_ir)

        logger.success(f"Finished extracting and saving page IR: {page_index}!")


@cli.command()
def extract(
    config_fp: Path = typer.Argument(
        ...,
        dir_okay=False,
        exists=True,
        file_okay=True,
        help="The file path to the global config file for the pipeline.",
        readable=True,
        resolve_path=True,
    )
) -> None:
    """Extract structured page-by-page IRs from raw PDF pages.

    The process is as follows:

    1. Validate page range against PDF document.
    2. Persist extraction run metadata so we always have an extraction run record.
    3. Extract page-by-page IR components and save to file.

    Parameters
    ----------
    config_fp
        The file path to the global config file for the pipeline.

    Raises
    ------
    Exception
        If any error occurs during extraction.
    """

    config = RunConfig.model_validate(open_json_type(config_fp)).page_ir_extraction

    with pymupdf.open(str(config.pdf_fp)) as doc:
        # 1.
        _, start_page, end_page = validate_page_count(
            doc=doc, end_page=config.end_page, start_page=config.start_page
        )

        # 2.
        doc_key, extraction_dirs, extraction_run = persist_extraction_run(config=config)

        try:
            # 3.
            logger.info(f"Starting page IR extraction process for: {config.pdf_fp}")

            extract_page_by_page(
                config=config,
                doc=doc,
                doc_key=doc_key,
                end_page=end_page,
                extraction_dirs=extraction_dirs,
                start_page=start_page,
            )
            extraction_run.extra["status"] = "success"

            logger.success("Page IR extraction completed successfully!")
        except Exception as e:  # pylint: disable=broad-except
            extraction_run.extra["status"] = "error"
            extraction_run.extra["error"] = {
                "message": str(e),
                "traceback": traceback.format_exc(limit=20),
                "type": e.__class__.__name__,
            }
            raise
        finally:
            extraction_run.completed_at = datetime.now(timezone.utc)
            write_to_json(
                fp=extraction_dirs.root / "extraction_run.json",
                json_info=extraction_run,
            )


if __name__ == "__main__":
    cli()
