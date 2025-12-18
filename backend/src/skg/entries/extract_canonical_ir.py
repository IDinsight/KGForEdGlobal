"""This module contains the entry point for converting raw PDF pages into a structural,
canonical Intermediate Representation (IR).

Invoke from the backend directory via:

python src/skg/entries/extract_canonical_ir.py ../data/zambia/zambia.pdf -c Zambia -l en-US -o ../results
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
from skg.ir.schemas import DocumentMetadataIR, KeyValuePair, PageIR
from skg.ir.utils import (
    ContinuityState,
    ExtractionDirs,
    apply_cross_page_continuity,
    build_continuity_state_from_page,
    create_extraction_dirs,
    ensure_namespace_page_refs,
    load_continuity_state,
    merge_pages_to_document_ir,
    normalize_provenance,
    save_continuity_state,
)
from skg.schemas import ExtractionRunIR
from skg.utils.general import write_to_json
from skg.utils.openai_ import extract_page_ir
from skg.utils.pdf import (
    compute_doc_key,
    get_page_dimensions,
    get_pdf_metadata,
    render_page_to_png,
)

assert (
    sys.version_info.major >= 3 and sys.version_info.minor >= 13
), "SenegalKG requires at least Python 3.13!"

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


def extract_page_irs(
    *,
    continuity_state: ContinuityState,
    doc: pymupdf.Document,
    doc_key: str,
    dpi: int,
    end_page: int,
    extraction_dirs: ExtractionDirs,
    model: str,
    overwrite: bool,
    pdf_fp: Path,
    start_page: int,
) -> list[PageIR]:
    """Extract per-page PageIRs from the PDF document.

    Parameters
    ----------
    continuity_state
        The cross-page continuity state.
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
    model
        OpenAI model for page IR extraction.
    overwrite
        Overwrite existing per-page artifacts.
    pdf_fp
        The file path to the PDF document to extract curriculum data from.
    start_page
        0-based start page (inclusive).

    Returns
    -------
    list[PageIR]
        The list of extracted PageIRs.
    """

    pages: list[PageIR] = []
    for page_index in range(start_page, end_page):
        page_ir_fp = extraction_dirs.page_ir / f"{page_index:04d}.json"
        png_fp = extraction_dirs.page_images / f"{page_index:04d}.png"
        rendered = False

        # Only render if it's missing OR if we are overwriting.
        if not png_fp.exists() or overwrite:
            render_page_to_png(
                doc=doc, dpi=dpi, output_png_fp=png_fp, page_index=page_index
            )
            rendered = True

        # If IR exists and we aren't overwriting, then load it.
        if page_ir_fp.exists() and not overwrite and not rendered:
            page_ir = PageIR.model_validate_json(page_ir_fp.read_text("utf-8"))
            page_ir.page_index = page_index
        else:
            # Otherwise, extract with a suitable vision model.
            page_ir = extract_page_ir(model=model, page_index=page_index, png_fp=png_fp)

        # Ensure page refs won’t collide across pages.
        page_ir = ensure_namespace_page_refs(
            page_ir=page_ir, prefix=f"p{page_index:04d}:"
        )

        # Ensure all elements have provenance + page dimensions.
        dims = get_page_dimensions(doc, page_index)
        page_ir = normalize_provenance(
            doc_key=doc_key,
            extraction_method="vision+structured",
            page_dimensions=dims,
            page_index=page_index,
            page_ir=page_ir,
            pdf_name=pdf_fp.name,
        )

        # Update continuity state for next page (must happen after namespacing).
        page_ir = apply_cross_page_continuity(page_ir, continuity_state)
        continuity_state = build_continuity_state_from_page(page_ir, continuity_state)
        save_continuity_state(extraction_dirs, continuity_state)

        # Persist the normalized version (namespaced refs + provenance).
        write_to_json(page_ir_fp, json.loads(page_ir.model_dump_json(indent=2)))

        pages.append(page_ir)
    return pages


def persist_updated_extraction_run_and_metadata(
    *,
    country: str,
    doc: pymupdf.Document,
    end_page: int,
    extraction_dirs: ExtractionDirs,
    extraction_run: ExtractionRunIR,
    languages: list[str],
    page_count: int,
    year: Optional[int],
) -> DocumentMetadataIR:
    """Persist the updated extraction run and document metadata.

    Parameters
    ----------
    country
        The country associated with the PDF document.
    doc
        The PyMuPDF document.
    end_page
        The resolved 0-based end page (exclusive).
    extraction_dirs
        The extraction directories.
    extraction_run
        The extraction run record.
    languages
        One or more languages associated with the PDF document.
    page_count
        The document page count.
    year
        Document year (optional; overrides any inferred year).

    Returns
    -------
    DocumentMetadataIR
        The document metadata.
    """

    pdf_md = get_pdf_metadata(doc)
    extraction_run.extra.update(  # The actual end page value used
        {"page_count": page_count, "end_page_resolved": end_page}
    )
    extraction_run.extra["pymupdf_metadata"] = pdf_md
    write_to_json(
        extraction_dirs.root / "extraction_run.json",
        json.loads(extraction_run.model_dump_json(indent=2)),
    )
    logger.info(f"PDF metadata: {pdf_md}")

    metadata = DocumentMetadataIR(
        country=country,
        extra=[
            KeyValuePair(key="pymupdf_metadata", value=json.dumps(pdf_md, default=str))
        ],
        languages=languages,
        publisher=pdf_md.get("producer", pdf_md.get("creator", None)),
        title=pdf_md.get("title", None),
        year=year,
    )
    write_to_json(
        extraction_dirs.root / "metadata.json",
        json.loads(metadata.model_dump_json(indent=2)),
    )
    return metadata


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

    1. XXX

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

    # 1. Persist extraction run metadata so we always have an extraction run record.
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
            # 2. Validate page range.
            page_count, end_page = validate_page_count(
                doc=doc, end_page=end_page, start_page=start_page
            )

            # 3. Persist the updated run record and the document metadata.
            doc_metadata = persist_updated_extraction_run_and_metadata(
                country=country,
                doc=doc,
                end_page=end_page,
                extraction_dirs=extraction_dirs,
                extraction_run=extraction_run,
                languages=languages,
                page_count=page_count,
                year=year,
            )

            # 4.
            continuity_state = load_continuity_state(extraction_dirs)

            # 5.
            pages = extract_page_irs(
                continuity_state=continuity_state,
                doc=doc,
                doc_key=doc_key,
                dpi=dpi,
                end_page=end_page,
                extraction_dirs=extraction_dirs,
                model=model,
                overwrite=overwrite,
                pdf_fp=pdf_fp,
                start_page=start_page,
            )

            # 6. Merge to DocumentIR and save.
            extraction_run.completed_at = datetime.now(timezone.utc)
            doc_ir = merge_pages_to_document_ir(
                doc_key=doc_key,
                extraction_run=extraction_run,
                metadata=doc_metadata,
                pages=pages,
                pdf_name=pdf_fp.name,
            )
            write_to_json(
                extraction_dirs.root / "canonical_ir.json",
                json.loads(doc_ir.model_dump_json(indent=2)),
            )

        extraction_run.extra["status"] = "success"
        logger.success("IR extraction completed successfully!")
    except Exception as e:  # pylint: disable=broad-except
        extraction_run.extra["status"] = "error"
        extraction_run.extra["error"] = {
            "type": e.__class__.__name__,
            "message": str(e),
            "traceback": traceback.format_exc(limit=20),
        }
        raise
    finally:
        extraction_run.completed_at = datetime.now(timezone.utc)
        write_to_json(
            extraction_dirs.root / "extraction_run.json",
            json.loads(extraction_run.model_dump_json(indent=2)),
        )


if __name__ == "__main__":
    cli()
