"""This module contains the entry point for extracting structural page Intermediate
Representations (IRs) from raw PDF pages. This is step 1.

Invoke from the backend directory via:

python src/skg/entries/extract_page_ir.py ../data/tanzania/tanzania.pdf -c Tanzania -y 2023 -l en-TZ -l sw-TZ -l fr -l zh-Hans -l ar -o ../results --use-text-layer-hints
python src/skg/entries/extract_page_ir.py ../data/uganda/uganda.pdf -c Uganda -y 2016 -l en-US -o ../results --use-text-layer-hints
python src/skg/entries/extract_page_ir.py ../data/zambia/zambia.pdf -c Zambia -y 2024 -l en-US -o ../results --use-text-layer-hints
python src/skg/entries/extract_page_ir.py ../data/ghana/ghana.pdf -c Ghana -y 2019 -l en-US -o ../results --use-text-layer-hints
"""

# Standard Library
import json
import sys
import traceback
import uuid

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
from skg.page_ir.llm import extract_page_ir
from skg.page_ir.schemas import PageIR
from skg.page_ir.utils import PageIRExtractionDirs, create_page_ir_extraction_dirs
from skg.schemas import RunCtx
from skg.utils.constants import PageBoundaryState
from skg.utils.general import write_to_json
from skg.utils.pdf import (
    compute_doc_key,
    extract_text_layer_hints,
    is_mostly_blank,
    read_png_dimensions,
    render_page_to_png,
    validate_page_count,
)

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


def extract_page_by_page(
    *,
    country: str,
    doc: pymupdf.Document,
    doc_key: str,
    dpi: int,
    end_page: int,
    extraction_dirs: PageIRExtractionDirs,
    languages: list[str],
    model: str,
    overwrite: bool,
    pdf_name: str,
    start_page: int,
    use_text_layer_hints: bool,
    year: Optional[int],
) -> None:
    """Perform page-by-page extraction of PageIR components from the PDF document.

    Parameters
    ----------
    country
        The country associated with the PDF document.
    doc
        The PyMuPDF document.
    doc_key
        The document key.
    dpi
        Render DPI for page images.
    end_page
        0-based end page (exclusive).
    extraction_dirs
        The extraction directories.
    languages
        One or more languages associated with the PDF document.
    model
        OpenAI model for page IR extraction.
    overwrite
        Overwrite existing per-page artifacts.
    pdf_name
        The PDF filename.
    start_page
        0-based start page (inclusive).
    use_text_layer_hints
        Whether to extract and use text layer hints from the PDF during extraction.
    year
        Document year (optional; overrides any inferred year).
    """

    for page_index in range(start_page, end_page):
        page_ir_fp = extraction_dirs.page_irs / f"{page_index:04d}.json"
        png_fp = extraction_dirs.page_images / f"{page_index:04d}.png"

        # Always ensure the PNG exists first. We render if the file is missing OR if we
        # are overwriting (e.g. changed DPI).
        if overwrite or not png_fp.exists():
            render_page_to_png(
                doc=doc, dpi=dpi, output_png_fp=png_fp, page_index=page_index
            )

        # Check cache. If not overwriting and JSON exists, skip entirely.
        if page_ir_fp.exists() and not overwrite:
            logger.info(f"Skipping page {page_index} (cached).")
            continue

        # Extract information from the page image.
        logger.info(f"Extracting and saving page: {page_index}...")
        image_width, image_height = read_png_dimensions(png_fp=png_fp)
        text_layer_hints = (
            extract_text_layer_hints(
                doc=doc,
                image_height=image_height,
                image_width=image_width,
                page_index=page_index,
            )
            if use_text_layer_hints
            else None
        )

        if is_mostly_blank(png_fp=png_fp) and text_layer_hints is None:
            logger.warning(f"Page {page_index} looks blank; skipping model call.")
            page_ir = PageIR(
                boundary_state=PageBoundaryState.STANDALONE,
                coord_space="px",
                doc_key=doc_key,
                dpi=dpi,
                image_height=image_height,
                image_width=image_width,
                items=[],
                page_index=page_index,
                pdf_name=pdf_name,
            )
        else:
            page_ir = extract_page_ir(
                country=country,
                image_height=image_height,
                image_width=image_width,
                languages=languages,
                model=model,
                page_index=page_index,
                png_fp=png_fp,
                text_layer_hints=text_layer_hints,
                year=year,
            )
            page_ir.coord_space = "px"
            page_ir.doc_key = doc_key
            page_ir.dpi = dpi
            page_ir.image_height = image_height
            page_ir.image_width = image_width
            page_ir.page_index = page_index
            page_ir.pdf_name = pdf_name

        # Re-validate after schema validators.
        page_ir = PageIR.model_validate(page_ir.model_dump(mode="python"))

        # Save PageIR JSON.
        write_to_json(page_ir_fp, page_ir.model_dump(mode="json"))
        logger.success(f"Finished extracting and saving page: {page_index}!")


def persist_extraction_run(
    *,
    country: str,
    dpi: int,
    end_page: Optional[int],
    pdf_fp: Path,
    languages: list[str],
    model: str,
    output_dir: Path,
    overwrite: bool,
    start_page: int,
    use_text_layer_hints: bool,
) -> tuple[str, PageIRExtractionDirs, RunCtx]:
    """Persist extraction run metadata.

    Parameters
    ----------
    country
        The country associated with the PDF document.
    dpi
        Render DPI for page images.
    end_page
        0-based end page (exclusive).
    pdf_fp
        The file path to the PDF document to extract curriculum data from.
    languages
        One or more languages associated with the PDF document.
    model
        OpenAI model for page IR extraction.
    output_dir
        Output directory root.
    overwrite
        Specifies whether to overwrite existing per-page artifacts.
    start_page
        0-based start page (inclusive).
    use_text_layer_hints
        Whether to extract and use text layer hints from the PDF during extraction.

    Returns
    -------
    tuple[str, ExtractionDirs, RunCtx]
        The document key, extraction directories, and extraction run record.
    """

    doc_key = compute_doc_key(n_hex=64, pdf_fp=pdf_fp)
    extraction_dirs = create_page_ir_extraction_dirs(
        doc_key=doc_key, output_dir=output_dir
    )
    extraction_run = RunCtx(
        extra={
            "country": country,
            "doc_key": doc_key,
            "dpi": dpi,
            "end_page_cli": end_page,  # Keep original CLI value (may be None)
            "languages": languages,
            "pdf_name": pdf_fp.name,
            "overwrite": overwrite,
            "start_page": start_page,
            "use_text_layer_hints": use_text_layer_hints,
        },
        models=[model],
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(
        extraction_dirs.root / "extraction_run.json",
        json.loads(extraction_run.model_dump_json(indent=2)),
    )
    logger.info(f"Extraction directory: {extraction_dirs.root}")

    return doc_key, extraction_dirs, extraction_run


@cli.command()
def extract(  # pylint: disable=too-many-positional-arguments
    pdf_fp: Path = typer.Argument(
        ...,
        dir_okay=False,
        exists=True,
        file_okay=True,
        help="The file path to the PDF document to extract curriculum data from.",
        readable=True,
        resolve_path=True,
    ),
    country: str = typer.Option(
        ..., "--country", "-c", help="The country associated with the PDF document."
    ),
    dpi: int = typer.Option(250, "--dpi", help="Render DPI for page images."),
    languages: list[str] = typer.Option(
        ...,
        "--language",
        "-l",
        help="One or more languages associated with the PDF document (e.g. -l en-US -l fr-FR).",
    ),
    model: str = typer.Option(
        "gpt-5.2-2025-12-11",
        "--model",
        "-m",
        help="OpenAI model for page IR extraction.",
    ),
    output_dir: Path = typer.Option(
        Path("./results"), "--output-dir", "-o", help="Output directory root."
    ),
    start_page: int = typer.Option(
        0, "--start-page", "-s", help="0-based start page (inclusive)."
    ),
    end_page: Optional[int] = typer.Option(
        None, "--end-page", "-e", help="0-based end page (exclusive). Default: to end."
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Overwrite existing per-page artifacts."
    ),
    use_text_layer_hints: bool = typer.Option(
        False,
        "--use-text-layer-hints",
        help="Whether to extract and use text layer hints from the PDF during extraction.",
    ),
    year: Optional[int] = typer.Option(
        None,
        "--year",
        "-y",
        help="Document year (optional; overrides any inferred year).",
    ),
) -> None:
    """Extract structured page-by-page IRs from raw PDF pages.

    The process is as follows:

    1. Persist extraction run metadata so we always have an extraction run record.
    2. Validate page range.
    3. Extract page-by-page IR components.
    4. Finalize extraction run record.

    Parameters
    ----------
    pdf_fp
        The file path to the PDF document to extract curriculum data from.
    country
        The country associated with the PDF document.
    dpi
        Render DPI for page images.
    languages
        One or more languages associated with the PDF document.
    model
        OpenAI model for page IR extraction.
    output_dir
        Output directory root.
    start_page
        0-based start page (inclusive).
    end_page
        0-based end page (exclusive). Default: to end.
    overwrite
        Overwrite existing per-page artifacts.
    use_text_layer_hints
        Whether to extract and use text layer hints from the PDF during extraction.
    year
        Document year (optional; overrides any inferred year).
    """

    pdf_fp = pdf_fp.resolve()

    # 1.
    doc_key, extraction_dirs, extraction_run = persist_extraction_run(
        country=country,
        dpi=dpi,
        end_page=end_page,
        languages=languages,
        model=model,
        output_dir=output_dir,
        overwrite=overwrite,
        pdf_fp=pdf_fp,
        start_page=start_page,
        use_text_layer_hints=use_text_layer_hints,
    )

    logger.info(f"Starting page IR extraction process for: {pdf_fp}")
    logger.info(f"Saving extraction results to: {extraction_dirs.root}")

    try:
        with pymupdf.open(str(pdf_fp)) as doc:
            # 2.
            _, end_page = validate_page_count(
                doc=doc, end_page=end_page, start_page=start_page
            )

            # 3.
            extract_page_by_page(
                country=country,
                doc=doc,
                doc_key=doc_key,
                dpi=dpi,
                end_page=end_page,
                extraction_dirs=extraction_dirs,
                languages=languages,
                model=model,
                overwrite=overwrite,
                pdf_name=pdf_fp.name,
                start_page=start_page,
                use_text_layer_hints=use_text_layer_hints,
                year=year,
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
        # 4.
        extraction_run.completed_at = datetime.now(timezone.utc)
        write_to_json(
            extraction_dirs.root / "extraction_run.json",
            extraction_run.model_dump(mode="json"),
        )


if __name__ == "__main__":
    cli()
