"""This module contains the entry point for converting raw PDF pages into a structural
Intermediate Representation (IR).

High-level capabilities:

1. Vision-first extraction: Renders PDF pages to images for LLM analysis.
2. Structured outputs: Enforces schemas via Pydantic and LLM JSON mode.
3. Context preservation: Passes hierarchy context between pages to handle tables/lists
    spanning page breaks.
4. Config-driven: Adapts extraction hints per document type.

Invoke from the backend directory via:

python src/skg/entries/extract_curriculum.py ../data/zambia/zambia.pdf -c Zambia -l en-US -o ../results --overwrite
"""

# Standard Library
import os
import sys

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Third Party Library
import pymupdf
import typer

from loguru import logger
from openai import OpenAI

# Append the framework path. NB: This is required if this entry point is invoked from
# the command line. However, it is not necessary if it is imported from a pip install.
if __name__ == "__main__":
    PACKAGE_PATH = Path(__file__).resolve().parents[2]
    if PACKAGE_PATH not in sys.path:
        print(f"Appending '{PACKAGE_PATH}' to system path...")
        sys.path.append(str(PACKAGE_PATH))

# Package Library
from skg.ir.schemas import (
    DocumentIR,
    DocumentMetadataIR,
    ExtractionRunIR,
    HierarchyNodeIR,
    PageIR,
    ProvenancePointer,
    StatementIR,
)
from skg.prompts.ir import extract_page_ir_info
from skg.utils.constants import HierarchyNodeType, StatementRole
from skg.utils.general import (
    compute_doc_key,
    encode_png_to_data_url,
    make_dir,
    write_text,
)

assert (
    sys.version_info.major >= 3 and sys.version_info.minor >= 13
), "SenegalKG requires at least Python 3.13!"

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


@dataclass(frozen=True)
class ExtractionDirs:
    """Dataclass for extraction directories."""

    root: Path
    artifacts: Path
    page_images: Path
    page_ir: Path


def attach_min_provenance(
    *,
    doc_key: str,
    extraction_method: str,
    page_index: int,
    page_ir: PageIR,
    pdf_name: str,
) -> PageIR:
    """Attach minimal provenance to all nodes/statements in a PageIR.

    Parameters
    ----------
    doc_key
        The document key.
    extraction_method
        The extraction method used.
    page_index
        The 0-based page index.
    page_ir
        The PageIR to attach provenance to.
    pdf_name
        The PDF file name.

    Returns
    -------
    PageIR
        The PageIR with attached provenance.
    """

    # Fill provenance if the model left it empty.
    default_provenance_pointer = ProvenancePointer(
        bbox=None,
        bbox_kind="unknown",
        doc_key=doc_key,
        extraction_method=extraction_method,
        page_index=page_index,
        pdf_name=pdf_name,
        section=None,
    )

    for n in page_ir.nodes:
        if not n.provenance:
            n.provenance = [default_provenance_pointer]
    for s in page_ir.statements:
        if not s.provenance:
            s.provenance = [default_provenance_pointer]

    if hasattr(page_ir, "relationships"):
        for r in page_ir.relationships:
            if not r.provenance:
                r.provenance = [default_provenance_pointer]

    return page_ir


def create_extraction_dirs(*, doc_key: str, output_dir: Path) -> ExtractionDirs:
    """Create extraction directories for a given document key.

    Parameters
    ----------
    doc_key
        The document key.
    output_dir
        The output directory root.

    Returns
    -------
    ExtractionDirs
        The created extraction directories.
    """

    root = output_dir / doc_key
    artifacts = root / "artifacts"
    page_images = root / "page_images"
    page_ir = root / "page_ir"

    for p in [root, page_images, page_ir, artifacts]:
        make_dir(p)

    return ExtractionDirs(
        root=root, artifacts=artifacts, page_images=page_images, page_ir=page_ir
    )


def extract_page_ir_with_llm(
    *,
    doc_key: str,
    model: str = "gpt-4o-2024-08-06",
    page_index: int,
    pdf_name: str,
    png_fp: Path,
) -> PageIR:
    """Extract PageIR from a page image using LLM + Vision + Structured Outputs. Uses
    OpenAI Responses API structured parsing into a Pydantic model. Image is passed as
    an input_image with a base64 data URL.

    Parameters
    ----------
    doc_key
        The document key.
    model
        The OpenAI model to use.
    page_index
        The 0-based page index.
    pdf_name
        The PDF file name.
    png_fp
        The PNG file path of the page image.

    Returns
    -------
    PageIR
        The extracted PageIR.
    """

    system_message = extract_page_ir_info().system_message
    user_message = extract_page_ir_info().user_message

    client = (
        OpenAI()
    )  # reads OPENAI_API_KEY from env :contentReference[oaicite:4]{index=4}

    image_url = encode_png_to_data_url(png_fp)

    resp = client.responses.parse(
        model=model,
        input=[
            {"role": "system", "content": system_message},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": user_message},
                    {"type": "input_image", "image_url": image_url},
                ],
            },
        ],
        text_format=PageIR,
    )

    page_ir: PageIR = resp.output_parsed
    page_ir.page_index = page_index  # enforce truth (don’t trust the model here)
    page_ir = attach_min_provenance(
        page_ir=page_ir,
        doc_key=doc_key,
        pdf_name=pdf_name,
        page_index=page_index,
        extraction_method="vision+structured",
    )
    return page_ir


def get_pdf_metadata(doc: pymupdf.Document) -> dict[str, Any]:
    """Get PDF metadata from a PyMuPDF document.

    NB: Expected PyMuPDF keys are: title, author, subject, keywords, creator, producer,
    creationDate, and modDate.

    Parameters
    ----------
    doc
        The PyMuPDF document.

    Returns
    -------
    dict[str, Any]
        The PDF metadata.
    """

    md = doc.metadata or {}
    return {k: v for k, v in md.items() if v}


def merge_pages_to_document_ir(
    *, doc_key: str, metadata: DocumentMetadataIR, pages: list[PageIR], pdf_name: str
) -> DocumentIR:
    """Merge PageIRs into DocumentIR.

    Parameters
    ----------
    doc_key
        The document key.
    pdf_name
        The PDF file name.
    metadata
        The document metadata.
    pages
        The list of PageIRs.

    Returns
    -------
    DocumentIR
        The merged DocumentIR.
    """

    # Ensure stable ordering regardless of how pages were accumulated.
    pages_sorted = sorted(pages, key=lambda p: p.page_index)

    nodes: list[Any] = []
    statements: list[Any] = []
    relationships: list[Any] = []

    for p in pages_sorted:
        nodes.extend(p.nodes)
        statements.extend(p.statements)

        # Merge page-level relationships if present.
        if hasattr(p, "relationships"):
            rels = getattr(p, "relationships") or []
            relationships.extend(rels)

    doc_kwargs: dict[str, Any] = dict(
        doc_key=doc_key,
        metadata=metadata,
        nodes=nodes,
        pages=pages_sorted,
        pdf_name=pdf_name,
        statements=statements,
    )

    # Only set fields that exist on the current DocumentIR model.
    model_fields = getattr(DocumentIR, "model_fields", {})

    if "relationships" in model_fields:
        doc_kwargs["relationships"] = relationships

    if "schema_version" in model_fields:
        doc_kwargs.setdefault("schema_version", "0.1")

    return DocumentIR(**doc_kwargs)


def render_page_to_png(
    *,
    doc: pymupdf.Document,
    dpi: int = 200,
    page_index: int,
    output_png_fp: Path,
) -> None:
    """Render a PDF page to PNG. Rendering guarantees that we capture everything
    visible on the page, consistently sized by DPI.

    NB: PDFs are typically 72 points/inch. So scale = dpi / 72 is the standard
    conversion. So scaling via Matrix(scale, scale) gives us a predictable pixel size
    and quality for downstream vision/OCR/LLM steps.

    Parameters
    ----------
    doc
        The PyMuPDF document.
    dpi
        Render DPI for page images.
    page_index
        The 0-based page index to render.
    output_png_fp
        The output PNG file path.
    """

    page = doc.load_page(page_index)

    scale = dpi / 72.0
    mat = pymupdf.Matrix(scale, scale)

    pix = page.get_pixmap(matrix=mat, alpha=False)
    pix.set_dpi(dpi, dpi)
    output_png_fp.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(output_png_fp))


@cli.command()
def extract(
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
        help="One or more languages associated with the PDF document (e.g. -l en_US -l fr_FR).",
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
) -> None:
    """Extract canonical curriculum intermediate representation (Layer A) from a PDF.

    The process is as follows:

    1. Load environment variables and configurations and set up paths.
    2.

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
    output_dir
        Output directory root.
    start_page
        0-based start page (inclusive).
    end_page
        0-based end page (exclusive). Default: to end.
    overwrite
        Overwrite existing per-page artifacts.
    """

    logger.info(f"Starting curriculum extraction process for: {pdf_fp}...")

    # 1.
    project_dir = Path(os.getenv("PATHS_PROJECT_DIR", ""))
    assert project_dir.is_dir(), f"'{project_dir}' is not a directory."

    pdf_fp = pdf_fp.resolve()
    output_dir = output_dir.resolve()

    # 2.
    doc_key = compute_doc_key(n_hex=64, pdf_fp=pdf_fp)
    extraction_dirs = create_extraction_dirs(doc_key=doc_key, output_dir=output_dir)

    logger.info(f"PDF: {pdf_fp.name}")
    logger.info(f"doc_key: {doc_key}")
    logger.info(f"Extraction directory: {extraction_dirs.root}")

    with pymupdf.open(str(pdf_fp)) as doc:
        # 3.
        page_count = doc.page_count

        if end_page is None:
            end_page = page_count
        if not (0 <= start_page <= page_count):
            raise typer.BadParameter(f"start_page must be in [0, {page_count}]")
        if not (0 <= end_page <= page_count) or end_page < start_page:
            raise typer.BadParameter(f"end_page must be in [start_page, {page_count}]")

        # 4.
        pdf_md = get_pdf_metadata(doc)
        logger.info(f"PDF metadata: {pdf_md}")
        metadata = DocumentMetadataIR(
            country=country,
            extra={"pymupdf_metadata": pdf_md},
            languages=languages,
            publisher=pdf_md.get("producer", pdf_md.get("creator", None)),
            title=pdf_md.get("title", None),
            year=2024,  # Hardcoded for now
        )
        write_text(
            extraction_dirs.root / "metadata.json", metadata.model_dump_json(indent=2)
        )

        # 5.
        pages: list[PageIR] = []
        for page_index in range(start_page, end_page):
            page_ir_fp = extraction_dirs.page_ir / f"{page_index:04d}.json"
            png_fp = extraction_dirs.page_images / f"{page_index:04d}.png"

            # Only render if it's missing OR if we are overwriting.
            if not png_fp.exists() or overwrite:
                render_page_to_png(
                    doc=doc, dpi=dpi, output_png_fp=png_fp, page_index=page_index
                )

            # If IR exists and we aren't overwriting, then load it.
            if page_ir_fp.exists() and not overwrite:
                page_ir = PageIR.model_validate_json(page_ir_fp.read_text("utf-8"))
            else:
                # Otherwise, extract and save.
                page_ir = extract_page_ir_with_llm(
                    doc_key=doc_key,
                    model="gpt-4o-2024-08-06",
                    page_index=page_index,
                    pdf_name=pdf_fp.name,
                    png_fp=png_fp,
                )
                write_text(page_ir_fp, page_ir.model_dump_json(indent=2))

            pages.append(page_ir)

        # 6. Merge to DocumentIR and save.
        doc_ir = merge_pages_to_document_ir(
            doc_key=doc_key, metadata=metadata, pages=pages, pdf_name=pdf_fp.name
        )
        write_text(
            extraction_dirs.root / "canonical_ir.json", doc_ir.model_dump_json(indent=2)
        )

    logger.success("Extraction completed successfully!")


if __name__ == "__main__":
    cli()
