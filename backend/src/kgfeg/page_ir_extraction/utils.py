"""This module contains utility functions related to page IR **extraction**."""

# Standard Library
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Third Party Library
import pymupdf

from loguru import logger
from PIL import Image

# Package Library
from kgfeg.config import Settings
from kgfeg.page_ir_extraction.schemas import PageIR
from kgfeg.schemas import ExtractionConfig, RunCtx
from kgfeg.utils.general import make_dir, write_to_json
from kgfeg.utils.pdf import compute_doc_key

# Quality-gate thresholds for text-layer usability.
_MAX_REPLACEMENT_CHAR_RATIO = 0.02
_MIN_PRINTABLE_RATIO = 0.90
_MIN_TEXT_LENGTH = 20


@dataclass(frozen=True)
class PageIRExtractionDirs:
    """Dataclass for page IR extraction directories."""

    root: Path
    page_images: Path
    page_irs: Path
    page_irs_raw: Path


@dataclass(frozen=True)
class PageTextLayerHints:
    """Container for optional PDF-derived hints passed to the extraction agent.

    Attributes
    ----------
    table_hint
        Serialized table structure extracted by PyMuPDF, or None if no usable tables
        were found.
    text_hint
        Plain-text content extracted from the PDF text layer, or None if the text layer
        failed the quality gate.
    """

    table_hint: str | None
    text_hint: str | None

    @property
    def has_hints(self) -> bool:
        """Return True if at least one hint is available.

        Returns
        -------
        bool
            True if either text_hint or table_hint is not None, else False.
        """

        return self.text_hint is not None or self.table_hint is not None


def _create_page_ir_extraction_dirs(output_dir: Path) -> PageIRExtractionDirs:
    """Create page IR extraction directories for a given extraction run.

    Parameters
    ----------
    output_dir
        The output directory root.

    Returns
    -------
    PageIRExtractionDirs
        The created page IR extraction directories.
    """

    root = output_dir
    page_images = root / "page_images"
    page_irs = root / "page_irs"
    page_irs_raw = root / "page_irs_raw"

    for p in [root, page_images, page_irs, page_irs_raw]:
        make_dir(p)

    return PageIRExtractionDirs(
        root=root, page_images=page_images, page_irs=page_irs, page_irs_raw=page_irs_raw
    )


def _extract_table_hint(*, page: pymupdf.Page, page_index: int) -> str | None:
    """Extract table structures from a PyMuPDF page and serialize them.

    Parameters
    ----------
    page
        The PyMuPDF page object.
    page_index
        0-based page index (used for logging).

    Returns
    -------
    str | None
        Serialized table hint string, or None if no usable tables were found.
    """

    try:
        finder_result = page.find_tables()
        tables = finder_result.tables
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f"Page {page_index + 1}: failed to extract tables: {e}")

        return None

    if not tables:
        return None

    serialized_tables: list[str] = []

    for i, table in enumerate(tables):
        try:
            data = table.extract()
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f"Page {page_index + 1}: failed to extract table {i}: {e}")

            continue

        if not data:
            continue

        # Skip trivially small tables (likely decorative or header bars).
        non_empty_cells = sum(
            1 for row in data for cell in row if cell is not None and cell.strip()
        )

        if non_empty_cells < 2:
            continue

        serialized = _serialize_table(table_data=data, table_index=i)
        serialized_tables.append(serialized)

    if not serialized_tables:
        return None

    return "\n\n".join(serialized_tables)


def _extract_text_hint(*, page: pymupdf.Page, page_index: int) -> str | None:
    """Extract and quality-gate the page text layer.

    Parameters
    ----------
    page
        The PyMuPDF page object.
    page_index
        0-based page index (used for logging).

    Returns
    -------
    str | None
        The raw text-layer content if it passes quality checks, else None.
    """

    try:
        raw_text = page.get_text("text")
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f"Page {page_index + 1}: failed to extract text layer: {e}")

        return None

    if not raw_text or len(raw_text.strip()) < _MIN_TEXT_LENGTH:
        logger.debug(
            f"Page {page_index + 1}: text layer too short "
            f"({len(raw_text.strip()) if raw_text else 0} chars). Skipping hint."
        )

        return None

    # Check printable ratio and replacement characters.
    total = len(raw_text)
    printable_count = sum(1 for c in raw_text if c.isprintable() or c in "\n\r\t")
    replacement_count = raw_text.count("\ufffd")
    printable_ratio = printable_count / total
    replacement_ratio = replacement_count / total

    if printable_ratio < _MIN_PRINTABLE_RATIO:
        logger.warning(
            f"Page {page_index + 1}: text layer has low printable ratio "
            f"({printable_ratio:.3f}). Skipping hint."
        )

        return None

    if replacement_ratio > _MAX_REPLACEMENT_CHAR_RATIO:
        logger.warning(
            f"Page {page_index + 1}: text layer has excessive replacement chars "
            f"({replacement_count}/{total}). Skipping hint."
        )

        return None

    return raw_text


def _serialize_table(*, table_data: list[list[str | None]], table_index: int) -> str:
    """Serialize a single extracted table into a compact, readable text format.

    Each row is rendered as a pipe-delimited line. None cells are shown as empty
    strings. The format is designed to be easy for an LLM to parse while remaining
    compact.

    Parameters
    ----------
    table_data
        The extracted table data (list of rows, each row a list of cell strings or
        None).
    table_index
        0-based index of the table on the page (used for labeling).

    Returns
    -------
    str
        The serialized table string.
    """

    lines = [f"### Table {table_index}"]

    for row_idx, row in enumerate(table_data):
        cells = []

        for cell in row:
            if cell is None:
                cells.append("")
            else:
                # Collapse internal newlines to spaces for compact display.
                cells.append(cell.replace("\n", " ").strip())

        lines.append(f"  row {row_idx}: | {' | '.join(cells)} |")

    return "\n".join(lines)


def extract_page_text_layer_hints(
    *, page: pymupdf.Page, page_index: int
) -> PageTextLayerHints:
    """Extract text-layer and table-layer hints from a PyMuPDF page.

    Both extractions include quality gates:

    1. If the text layer is empty, mostly non-printable, or contains excessive
        replacement characters (U+FFFD), the text hint is set to None.
    2. If no tables are found, the table hint is None.

    Parameters
    ----------
    page
        The PyMuPDF page object to extract hints from.
    page_index
        0-based page index (used for logging).

    Returns
    -------
    PageTextLayerHints
        Container with text_hint and table_hint (either may be None).
    """

    table_hint = _extract_table_hint(page=page, page_index=page_index)
    text_hint = _extract_text_hint(page=page, page_index=page_index)
    return PageTextLayerHints(table_hint=table_hint, text_hint=text_hint)


def persist_extraction_run(
    *, config: ExtractionConfig
) -> tuple[str, PageIRExtractionDirs, RunCtx]:
    """Persist extraction run metadata.

    Parameters
    ----------
    config
        The extraction run configuration.

    Returns
    -------
    tuple[str, PageIRExtractionDirs, RunCtx]
        The document key, extraction directories, and extraction run record.
    """

    doc_key = compute_doc_key(n_hex=64, pdf_fp=config.pdf_fp)
    extraction_dirs = _create_page_ir_extraction_dirs(
        output_dir=config.output_dir / doc_key / "extraction"
    )
    exclude_keys = {"overwrite"}
    extra = {
        k: v for k, v in config.model_dump(mode="json").items() if k not in exclude_keys
    }
    extra["doc_key"] = doc_key
    extraction_run = RunCtx(
        extra=extra,
        models={
            "extraction": Settings.LLM_PAGE_IR_EXTRACTION_MODEL,
            "validation": Settings.LLM_PAGE_IR_EXTRACTION_MODEL,
        },
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(
        fp=extraction_dirs.root / "extraction_run.json", json_info=extraction_run
    )

    logger.info(f"Saving extraction results to: {extraction_dirs.root}")

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
                f"Page {page_index + 1}: rotation={rotation}. Counter-rotating render matrix."
            )

            mat.prerotate(-rotation)

    pix = page.get_pixmap(matrix=mat, alpha=False)
    pix.set_dpi(dpi, dpi)

    output_png_fp.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(output_png_fp))

    logger.success(
        f"Saved rendered page {page_index + 1} at {dpi} DPI: {output_png_fp} "
    )
