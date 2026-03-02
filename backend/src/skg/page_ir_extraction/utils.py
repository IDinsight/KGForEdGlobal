"""This module contains utility functions related to page IR **extraction**."""

# Standard Library
import uuid

from datetime import datetime, timezone
from pathlib import Path

# Third Party Library
import pymupdf

from loguru import logger
from PIL import Image

# Package Library
from skg.page_ir_extraction.schemas import PageIR
from skg.schemas import ExtractionConfig, RunCtx
from skg.utils.general import PipelineDirs, write_to_json
from skg.utils.pdf import compute_doc_key


def persist_extraction_run(
    *, config: ExtractionConfig
) -> tuple[str, PipelineDirs, RunCtx]:
    """Persist extraction run metadata.

    Parameters
    ----------
    config
        The extraction run configuration.

    Returns
    -------
    tuple[str, PipelineDirs, RunCtx]
        The document key, extraction directories, and extraction run record.
    """

    doc_key = compute_doc_key(n_hex=64, pdf_fp=config.pdf_fp)
    extraction_dirs = PipelineDirs.create_from_root(
        root_path=config.output_dir / doc_key / "extraction"
    )
    exclude_keys = {"model", "overwrite"}
    extra = {
        k: v for k, v in config.model_dump(mode="json").items() if k not in exclude_keys
    }
    extra["doc_key"] = doc_key
    extraction_run = RunCtx(
        extra=extra,
        models=[config.model],
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(
        fp=extraction_dirs.root / "extraction_run.json", json_info=extraction_run
    )

    logger.success(f"Saved extraction results to: {extraction_dirs.root}")

    return doc_key, extraction_dirs, extraction_run


def persist_page_ir_attempt_artifacts(
    *,
    attempt: int,
    error: Exception | None,
    model: str,
    output_text: str | None,
    page_index: int,
    parsed: PageIR | None,
    raw_page_irs_dir: Path,
    validation_cycle: int = 0,
) -> None:
    """Persist raw artifacts from a page IR extraction attempt.

    Parameters
    ----------
    attempt
        The attempt number (0-based) within the current validation cycle.
    error
        The error encountered (if any).
    model
        The model identifier used.
    output_text
        The raw output text from the model (if any).
    page_index
        The 0-based page index.
    parsed
        The parsed PageIR object (if any).
    raw_page_irs_dir
        Directory to save raw page IR extraction artifacts.
    validation_cycle
        The 0-based validation cycle index. Included in the filename to prevent
        overwriting artifacts when the outer extraction -> validation loop retries.
    """

    stem = f"{page_index:04d}.val{validation_cycle:02d}.attempt{attempt:02d}"

    if output_text is not None:
        (raw_page_irs_dir / f"{stem}.output.txt").write_text(
            output_text, encoding="utf-8"
        )

    if parsed is not None:
        write_to_json(fp=raw_page_irs_dir / f"{stem}.parsed.json", json_info=parsed)

    if error is not None:
        (raw_page_irs_dir / f"{stem}.error.txt").write_text(
            f"{error.__class__.__name__}: {str(error)}", encoding="utf-8"
        )

    meta = {
        "attempt": attempt,
        "has_error": error is not None,
        "has_output_text": output_text is not None,
        "has_parsed": parsed is not None,
        "model": model,
        "page_index": page_index,
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "validation_cycle": validation_cycle,
    }
    write_to_json(fp=raw_page_irs_dir / f"{stem}.meta.json", json_info=meta)


def read_png_dimensions(png_fp: Path) -> tuple[int, int]:
    """Read PNG width/height.

    Parameters
    ----------
    png_fp
        Path to a rendered PNG.

    Returns
    -------
    tuple[int, int]
        (width_px, height_px)
    """

    with Image.open(png_fp) as img:
        width, height = img.size

    return width, height


def render_and_save_page_to_png(
    *,
    doc: pymupdf.Document,
    dpi: int,
    fix_rotation: bool = False,
    output_png_fp: Path,
    page_index: int,
) -> None:
    """Render a PDF page to PNG. Rendering guarantees that we capture everything
    visible on the page, consistently sized by DPI.

    NB: PDFs are typically 72 points/inch. So scale = dpi / 72 is the standard
    conversion. Scaling via Matrix(scale, scale) gives us a predictable pixel size and
    quality for downstream vision/OCR/LLM steps.

    Parameters
    ----------
    doc
        The PyMuPDF document.
    dpi
        Render DPI for page images.
    fix_rotation
        If True, counter-rotate the render matrix to neutralize page rotation. This is
        needed for accurate cropping and text hint extraction. If False, the rendered
        image will reflect the page's inherent rotation, which may be desirable for
        some use cases.
    output_png_fp
        The output PNG file path.
    page_index
        The 0-based page index to render.
    """

    page = doc.load_page(page_index)

    scale = dpi / 72.0
    mat = pymupdf.Matrix(scale, scale)

    if fix_rotation:
        rotation = page.rotation % 360

        # Neutralize page rotation for the rasterized image.
        if rotation in (90, 180, 270):
            logger.warning(
                f"Page {page_index}: rotation={rotation}. Counter-rotating render matrix."
            )

            mat.prerotate(-rotation)

    pix = page.get_pixmap(matrix=mat, alpha=False)
    pix.set_dpi(dpi, dpi)

    output_png_fp.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(output_png_fp))

    logger.success(f"Saved rendered page {page_index} at {dpi} DPI: {output_png_fp} ")
