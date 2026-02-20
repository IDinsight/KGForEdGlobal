"""This module contains utilities related to PDF documents."""

# Standard Library
import hashlib
import re

from collections import Counter
from pathlib import Path
from typing import Optional

# Third Party Library
import pymupdf

from loguru import logger
from PIL import Image, ImageStat

# Package Library
from skg.utils.general import make_dir


def add_section_for_text_hint(
    *,
    items: list[tuple[float, float, float, str]],
    limit: int,
    output: list[str],
    sx: float,
    sy: float,
    title: str,
) -> None:
    """Add a section to the output.

    Parameters
    ----------
    items
        The list of items (y, x, size, text).
    limit
        Maximum number of items to include.
    output
        The output list to append to.
    sx
        Scale factor for x-coordinates.
    sy
        Scale factor for y-coordinates.
    title
        The section title.
    """

    if not items:
        return

    output.append(f"## {title}")
    for y, x, s, t in items[:limit]:
        t = t[:220].rstrip() + "..." if len(t) > 220 else t
        output.append(f"[x={x * sx:.1f} y={y * sy:.1f} sz={s:.1f}] {t}")


def compute_doc_key(*, n_hex: int = 64, pdf_fp: Path) -> str:
    """Compute deterministic doc_key from PDF bytes (sha256 hex).

    Parameters
    ----------
    n_hex
        Number of hex characters to return. Defaults to 64 (full sha256).
    pdf_fp
        Path to the PDF file.

    Returns
    -------
    str
        The computed document key.
    """

    data = pdf_fp.read_bytes()
    h = hashlib.sha256(data).hexdigest()

    return h[:n_hex]


def crop_image_to_bottom(
    *, input_png_fp: Path, output_png_fp: Path, return_cropped_image: bool = False
) -> Optional[Image.Image]:
    """Crop the bottom 50% of the page image.

    Parameters
    ----------
    input_png_fp
        Full-page PNG path (the extraction-time rendered page image).
    output_png_fp
        Where to write the cropped PNG.
    return_cropped_image
        If True, return the cropped PIL Image as well.

    Returns
    -------
    Optional[Image.Image]
        Cropped image if requested; otherwise None.

    Raises
    ------
    ValueError
        If the image size is invalid.
    """

    with Image.open(input_png_fp) as img:
        w, h = img.size

        if h <= 0 or w <= 0:
            raise ValueError(f"Invalid image size: {img.size} for {input_png_fp}")

        # Calculate the 50% mark (start of the bottom half).
        y0 = int(h * 0.5)

        # Crop from (0, 50% height) to (width, height).
        crop = img.crop((0, y0, w, h))

        make_dir(output_png_fp.parent)
        crop.save(output_png_fp)

        return crop if return_cropped_image else None


def crop_image_to_top(
    *, input_png_fp: Path, output_png_fp: Path, return_cropped_image: bool = False
) -> Optional[Image.Image]:
    """Crop the top 50% of the page image.

    Parameters
    ----------
    input_png_fp
        Full-page PNG path (the extraction-time rendered page image).
    output_png_fp
        Where to write the cropped PNG.
    return_cropped_image
        If True, return the cropped PIL Image as well.

    Returns
    -------
    Optional[Image.Image]
        Cropped image if requested; otherwise None.

    Raises
    ------
    ValueError
        If the image size is invalid.
    """

    with Image.open(input_png_fp) as img:
        w, h = img.size

        if w <= 0 or h <= 0:
            raise ValueError(f"Invalid image size: {img.size} for {input_png_fp}")

        # Calculate the 50% mark.
        y1 = int(h * 0.5)

        # Crop from (0, 0) to (width, 50% height).
        crop = img.crop((0, 0, w, y1))

        make_dir(output_png_fp.parent)
        crop.save(output_png_fp)

        return crop if return_cropped_image else None


def crop_image_to_ymax(
    *, input_png_fp: Path, output_png_fp: Path, y_max: float
) -> None:
    """Crop a rendered page PNG to [0, y_max] in pixel coordinates.

    Parameters
    ----------
    input_png_fp
        Full-page PNG path (the extraction-time rendered page image).
    output_png_fp
        Where to write the cropped PNG.
    y_max
        The maximum Y coordinate (in pixels) to crop to. Values outside the image
        height will be clamped to the image bounds.
    """

    with Image.open(input_png_fp) as img:
        w, h = img.size
        y = max(1, min(int(round(y_max)), h))

        make_dir(output_png_fp.parent)
        img.crop((0, 0, w, y)).save(output_png_fp)


def extract_text_layer_hints(
    *,
    doc: pymupdf.Document,
    image_height: int,
    image_width: int,
    max_chars: int = 2500,
    max_lines_per_section: int = 25,
    page_index: int,
) -> str | None:
    """Extract layout-aware text layer hints from a PDF page.

    NB:
      - Uses get_text("dict") for per-line positions and font sizes.
      - Converts PDF coordinates to rendered PNG pixel coords using image dimensions.
      - Produces a compact digest: headings/header/footer/top/bottom body.
      - Adds optional column x-peaks from word positions.

    The process is as follows:

    1. Extract raw text blocks and setup scaling from PDF points to image pixels.
    2. Flatten structure into simple lines: (y, x, size, text).
    3. If no lines found, fallback to raw text extraction.
    4. Categorize lines (Headings, Header, Footer, Body) using heuristics.
    5. Detect column X-peaks.
    6. Build output.

    Parameters
    ----------
    doc
        The PyMuPDF document.
    image_height
        The rendered image height in pixels.
    image_width
        The rendered image width in pixels.
    max_chars
        Maximum number of characters to extract.
    max_lines_per_section
        Maximum number of lines to keep per section (heading, header, footer, body).
    page_index
        The 0-based page index.

    Returns
    -------
    str | None
        The extracted text layer hints, or None if no text found.
    """

    # 1.
    page = doc.load_page(page_index)
    blocks = page.get_text("dict").get("blocks", [])
    sx = image_width / page.rect.width
    sy = image_height / page.rect.height

    # 2.
    lines = []
    for b in (b for b in blocks if b["type"] == 0):
        for line in b["lines"]:
            # Merge spans to get full line text and max font size.
            text = " ".join(" ".join(s["text"] for s in line["spans"]).split())
            if not text:
                continue
            size = max((s["size"] for s in line["spans"]), default=0)
            lines.append((line["bbox"][1], line["bbox"][0], size, text))

    # 3.
    if not lines:
        # Fallback to raw text if structured extraction fails.
        fallback = (page.get_text("text") or "").strip()
        return (
            fallback[:max_chars] + ("..." if len(fallback) > max_chars else "")
            if fallback
            else None
        )

    lines.sort()  # Sort by Y then X

    # 4.
    h_y, f_y = page.rect.height * 0.08, page.rect.height * 0.92

    header = [line for line in lines if line[0] <= h_y]
    footer = [line for line in lines if line[0] >= f_y]
    body = [line for line in lines if h_y < line[0] < f_y]

    # Heuristic: Headings are top 10% sizes (min 11.5).
    sizes = sorted([line[2] for line in lines if line[2] > 0])
    p90 = sizes[int(0.9 * (len(sizes) - 1))] if sizes else 0
    headings = sorted(
        [line for line in lines if line[2] >= max(p90, 11.5)],
        key=lambda x: (-x[2], x[0]),
    )

    # 5.
    output = [
        f"PAGE_TEXT_LAYER_DIGEST dims={image_width}x{image_height}px lines={len(lines)}"
    ]
    words = page.get_text("words")
    if len(words) >= 80 or len(lines) >= 20:
        # Bin x-coords by 20pt to find density peaks
        bins = Counter(int(w[0] // 20) for w in words)
        peaks = sorted([b * 20 for b, _ in bins.most_common(3)])
        if peaks:
            output.append(
                f"COLUMN_X0_PEAKS_PX={', '.join(f'{p * sx:.1f}' for p in peaks)}"
            )
            output.extend(["LIKELY_MULTI_COLUMN_OR_TABLE=true"] * (len(peaks) >= 2))

    # 6.
    add_section_for_text_hint(
        items=headings,
        limit=12,
        output=output,
        sx=sx,
        sy=sy,
        title="HEADINGS_CANDIDATES",
    )
    add_section_for_text_hint(
        items=header, limit=10, output=output, sx=sx, sy=sy, title="HEADER_CANDIDATES"
    )
    add_section_for_text_hint(
        items=footer, limit=10, output=output, sx=sx, sy=sy, title="FOOTER_CANDIDATES"
    )

    if len(body) <= max_lines_per_section:
        add_section_for_text_hint(
            items=body,
            limit=max_lines_per_section,
            output=output,
            sx=sx,
            sy=sy,
            title="BODY_LINES",
        )
    else:
        add_section_for_text_hint(
            items=body,
            limit=max_lines_per_section,
            output=output,
            sx=sx,
            sy=sy,
            title="TOP_BODY_LINES",
        )
        add_section_for_text_hint(
            items=body[-max_lines_per_section:],
            limit=max_lines_per_section,
            output=output,
            sx=sx,
            sy=sy,
            title="BOTTOM_BODY_LINES",
        )

    # Raw text fallback for flow.
    if raw := (page.get_text("text") or "").strip():
        # Collapse spaces/tabs but preserve newlines to keep paragraph structure.
        raw = re.sub(r"[ \t]+", " ", raw)
        output.append(f"## RAW_TEXT_EXCERPT\n{raw[:600].strip()}...")

    final_text = "\n".join(output)
    if len(final_text) > max_chars:
        return final_text[:max_chars].rstrip() + "\n...[truncated]"
    return final_text


def is_mostly_blank(
    *,
    ink_luminance_threshold: int = 150,
    max_ink_fraction: float = 0.005,
    max_std_dev: float = 15.0,
    min_mean_brightness: float = 200.0,
    png_fp: Path,
) -> bool:
    """Check if the rendered page image is nearly blank using grayscale statistics.

    NB: If we cannot read the image properly, then the default behavior is to NOT treat
    the image as blank.

    The process is as follows:

    1. Convert to grayscale.
    2. Crop borders (3% padding) to avoid scanner edge artifacts.
    3. Crop bottom 8% to ignore footer/page numbers.
    4. Downsample to max 600x600 for performance.
    5. Calculate statistics: mean, stddev, ink coverage.
    6. Calculate "ink" coverage from histogram by checking histogram for pixels darker
         than the ink threshold.
    7. Evaluate against thresholds.

    Parameters
    ----------
    ink_luminance_threshold
        Threshold for what counts as "ink" (0=black, 255=white). 150 avoids counting
        light shadows/paper texture as text.
    max_ink_fraction
        Max fraction of pixels that can be "ink" for a page to be considered blank.
        0.5% allows for a speck or two but not a word.
    max_std_dev
        Maximum standard deviation of pixel brightness to avoid high-contrast noise.
    min_mean_brightness
        Minimum mean brightness of the page background to avoid dark scans.
    png_fp
        The PNG file path of the page image.

    Returns
    -------
    bool
        True if the page is visually blank, False otherwise.
    """

    try:
        with Image.open(png_fp) as im:
            # 1.
            im = im.convert("L")
            w, h = im.size

            # 2.
            pad_x = int(w * 0.03)
            pad_y = int(h * 0.03)

            # Clamp coordinates to safe bounds
            crop_box = (pad_x, pad_y, w - pad_x, h - pad_y)
            im = im.crop(crop_box)

            # 3.
            bw, bh = im.size
            if bh > 50:  # Only crop if we have enough height
                im = im.crop((0, 0, bw, int(bh * 0.92)))

            # 4.
            im.thumbnail((600, 600))

            # 5.
            stat = ImageStat.Stat(im)
            mean = stat.mean[0]
            std = stat.stddev[0]

            # 6.
            hist = im.histogram()
            total_pixels = max(1, sum(hist))

            # Sum pixels from black (0) up to threshold.
            dark_pixels = sum(hist[:ink_luminance_threshold])
            dark_frac = dark_pixels / total_pixels

            # 7. Evaluate.
            return (
                dark_frac < max_ink_fraction
                and std < max_std_dev
                and mean > min_mean_brightness
            )
    except Exception:  # pylint: disable=broad-except
        # If we can't read the image, don't treat it as blank.
        return False


def read_png_dimensions(*, png_fp: Path) -> tuple[int, int]:
    """Read PNG width/height without external dependencies.

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
    end_page = end_page or page_count

    if start_page >= end_page:
        raise ValueError(
            f"start_page ({start_page}) must be less than end_page ({end_page})"
        )
    if not 0 <= start_page < page_count:
        raise ValueError(f"start_page must be in [0, {page_count}]")
    if not (0 <= end_page <= page_count) or end_page < start_page:
        raise ValueError(f"end_page must be in [start_page, {page_count}]")

    return page_count, end_page
