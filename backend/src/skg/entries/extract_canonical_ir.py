"""This module contains the entry point for converting raw PDF pages into a structural,
canonical Intermediate Representation (IR).

Invoke from the backend directory via:

python src/skg/entries/extract_canonical_ir.py ../data/tanzania/tanzania.pdf -c Tanzania -y 2023 -l en-TZ -l sw-TZ -l fr -l zh-Hans -l ar -o ../results
python src/skg/entries/extract_canonical_ir.py ../data/uganda/uganda.pdf -c Uganda -y 2016 -l en-US -o ../results
python src/skg/entries/extract_canonical_ir.py ../data/zambia/zambia.pdf -c Zambia -y 2024 -l en-US -o ../results
python src/skg/entries/extract_canonical_ir.py ../data/ghana/ghana.pdf -c Ghana -y 2019 -l en-US -o ../results
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
from skg.ir.schemas import PageIR
from skg.ir.utils import ExtractionDirs, create_extraction_dirs
from skg.schemas import ExtractionRunIR
from skg.utils.constants import PageBoundaryState
from skg.utils.general import write_to_json
from skg.utils.openai_ import extract_page_ir
from skg.utils.pdf import (
    compute_doc_key,
    is_mostly_blank,
    read_png_dimensions,
    render_page_to_png,
)

assert (
    sys.version_info.major >= 3 and sys.version_info.minor >= 13
), "SenegalKG requires at least Python 3.13!"

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


def extract_stage_1(
    *,
    country: str,
    doc: pymupdf.Document,
    doc_key: str,
    dpi: int,
    end_page: int,
    extraction_dirs: ExtractionDirs,
    languages: list[str],
    model: str,
    overwrite: bool,
    pdf_name: str,
    start_page: int,
    year: Optional[int],
) -> None:
    """Perform stage 1 extraction of PageIR components from the PDF document.

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
    year
        Document year (optional; overrides any inferred year).
    """

    for page_index in range(start_page, end_page):
        page_ir_fp = extraction_dirs.page_ir / f"{page_index:04d}.json"
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

        # Perform stage 1 extraction.
        logger.info(f"Extracting and saving page: {page_index}...")
        image_width, image_height = read_png_dimensions(png_fp=png_fp)

        if is_mostly_blank(png_fp=png_fp):
            logger.warning(f"Page {page_index} looks blank; skipping model call.")
            page_ir = PageIR(
                boundary_state=PageBoundaryState.STANDALONE, coord_space="px", items=[]
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
                year=year,
            )
        page_ir.coord_space = "px"
        page_ir.doc_key = doc_key
        page_ir.dpi = dpi
        page_ir.image_height = image_height
        page_ir.page_index = page_index
        page_ir.pdf_name = pdf_name
        page_ir.image_width = image_width

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
    start_page: int,
) -> tuple[str, ExtractionDirs, ExtractionRunIR]:
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
    start_page
        0-based start page (inclusive).

    Returns
    -------
    tuple[str, ExtractionDirs, ExtractionRunIR]
        The document key, extraction directories, and extraction run record.
    """

    doc_key = compute_doc_key(n_hex=64, pdf_fp=pdf_fp)
    extraction_dirs = create_extraction_dirs(doc_key=doc_key, output_dir=output_dir)
    extraction_run = ExtractionRunIR(
        extra={
            "country": country,
            "doc_key": doc_key,
            "dpi": dpi,
            "end_page_cli": end_page,  # Keep original CLI value (may be None)
            "languages": languages,
            "pdf_name": pdf_fp.name,
            "start_page": start_page,
        },
        models=[model],
        pipeline_version="0.1",
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(
        extraction_dirs.root / "extraction_run.json",
        json.loads(extraction_run.model_dump_json(indent=2)),
    )
    logger.info(f"Extraction directory: {extraction_dirs.root}")

    return doc_key, extraction_dirs, extraction_run


def validate_page_count(
    *, doc: pymupdf.Document, end_page: Optional[int], start_page: int
) -> tuple[int, int]:
    """Validate start and end page against document page count.

    Parameters
    ----------
    doc
        The PyMuPDF document.
    end_page
        0-based end page (exclusive).
    start_page
        0-based start page (inclusive).

    Returns
    -------
    tuple[int, int]
        The document page count and resolved end page.

    Raises
    ------
    ValueError
        If start_page or end_page are out of bounds.
    """

    page_count = doc.page_count
    if end_page is None:
        end_page = page_count
    if not 0 <= start_page <= page_count:
        raise ValueError(f"start_page must be in [0, {page_count}]")
    if not (0 <= end_page <= page_count) or end_page < start_page:
        raise ValueError(f"end_page must be in [start_page, {page_count}]")
    return page_count, end_page


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
    dpi: int = typer.Option(200, "--dpi", help="Render DPI for page images."),
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
        Path("./results"), "--output_dir", "-o", help="Output directory root."
    ),
    start_page: int = typer.Option(
        0, "--start_page", "-s", help="0-based start page (inclusive)."
    ),
    end_page: Optional[int] = typer.Option(
        None, "--end_page", "-e", help="0-based end page (exclusive). Default: to end."
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Overwrite existing per-page artifacts."
    ),
    year: Optional[int] = typer.Option(
        None,
        "--year",
        "-y",
        help="Document year (optional; overrides any inferred year).",
    ),
) -> None:
    """Extract canonical curriculum intermediate representation (Layer A) from a PDF.

    The process is as follows:

    1. Persist extraction run metadata so we always have an extraction run record.
    2. Validate page range.
    3. ???

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
    year
        Document year (optional; overrides any inferred year).
    """

    pdf_fp = pdf_fp.resolve()
    output_dir = output_dir.resolve()
    logger.info(f"Starting IR extraction process for: {pdf_fp}...")
    logger.info(f"Outputting results to: {output_dir}")

    # 1.
    doc_key, extraction_dirs, extraction_run = persist_extraction_run(
        country=country,
        dpi=dpi,
        end_page=end_page,
        languages=languages,
        model=model,
        output_dir=output_dir,
        pdf_fp=pdf_fp,
        start_page=start_page,
    )

    try:
        with pymupdf.open(str(pdf_fp)) as doc:
            # 2.
            page_count, end_page = validate_page_count(
                doc=doc, end_page=end_page, start_page=start_page
            )

            # 3.
            extract_stage_1(
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
                year=year,
            )

        extraction_run.extra["status"] = "success"
        logger.success("IR extraction completed successfully!")
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
            extraction_dirs.root / "extraction_run.json",
            extraction_run.model_dump(mode="json"),
        )


if __name__ == "__main__":
    cli()
