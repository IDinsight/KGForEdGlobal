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


def _has_boundary_evidence(*, img: Image.Image, mode: str) -> bool:
    """Check if a cropped image has visual evidence near the boundary seam.

    Valid modes are:
      - "bottom": We care about the TOP of the crop (tail of prev-page candidate)
      - "top": We care about the BOTTOM of the crop (head of next-page candidate)

    Parameters
    ----------
    img
        The cropped PIL Image to analyze.
    mode
        The mode ("bottom" or "top").

    Returns
    -------
    bool
        True if the image has boundary evidence, False if it looks blankish.
    """

    assert mode in {
        "bottom",
        "top",
    }, f"Invalid mode: {mode}. Valid modes are 'bottom' and 'top'."

    img = img.convert("L")
    w, h = img.size

    if w <= 0 or h <= 0:
        return True  # Don't treat as blank/evidence-free

    # Focus on the seam-evidence band, not the whole crop.
    band_frac = 0.65
    img = (
        img.crop((0, 0, w, max(1, int(h * band_frac))))
        if mode == "bottom"
        else img.crop((0, max(0, int(h * (1.0 - band_frac))), w, h))
    )

    # Ignore outer border where crop marks/page frame lines usually are.
    bw, bh = img.size
    pad_x = int(bw * 0.03)
    pad_y = int(bh * 0.03)
    x0 = min(max(pad_x, 0), bw - 1)
    y0 = min(max(pad_y, 0), bh - 1)
    x1 = max(min(bw - pad_x, bw), x0 + 1)
    y1 = max(min(bh - pad_y, bh), y0 + 1)
    im = img.crop((x0, y0, x1, y1))

    # Downsample for speed.
    im.thumbnail((600, 600))

    hist = im.histogram()
    total = float(sum(hist)) or 1.0

    dark_frac = sum(hist[:200]) / total
    mean = sum(i * c for i, c in enumerate(hist)) / total
    var = sum(((i - mean) ** 2) * c for i, c in enumerate(hist)) / total
    std = var**0.5

    # If it looks like a blank region, we have no evidence.
    return not (dark_frac < 0.006 and std < 14.0 and mean > 210.0)


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
    *,
    bbox: list[float],
    desired_padding_inches: float = 0.25,
    input_png_fp: Path,
    min_height_px: int = 400,
    output_png_fp: Path,
    render_dpi: int,
    return_cropped_image: bool = False,
) -> Optional[Image.Image]:
    """Crop a rendered page image from just above the candidate bbox down to the bottom.

    Parameters
    ----------
    bbox
        [x0, y0, x1, y1] in *pixel units* of the rendered PNG (PageIR coordinate
        space). Only y1 (bbox[3]) is used for the crop start (we start above the
        BOTTOM edge so the crop includes the tail of the candidate near the boundary).
    desired_padding_inches
        Extra padding (in inches) to include above the crop start.
    input_png_fp
        Full-page PNG path (the extraction-time rendered page image).
    min_height_px
        Minimum crop height (pixels).
    output_png_fp
        Where to write the cropped PNG.
    render_dpi
        DPI used to interpret `desired_padding_inches` as pixels. This should be the
        same DPI used to render the extraction PNG.
    return_cropped_image
        If True, return the cropped PIL Image as well.

    Returns
    -------
    Optional[Image.Image]
        Cropped image if requested; otherwise None.

    Raises
    ------
    ValueError
        If the computed crop start is invalid.
    """

    with Image.open(input_png_fp) as img:
        w, h = img.size

        if h <= 0 or w <= 0:
            raise ValueError(f"Invalid image size: {img.size} for {input_png_fp}")

        _, _, _, y1 = [float(v) for v in bbox]
        y1 = max(0.0, min(y1, float(h)))
        padding_px = int(desired_padding_inches * render_dpi)

        # Start just above the BOTTOM edge of the candidate item. This focuses the crop
        # on boundary evidence (tail rows/lines) instead of including the whole
        # (potentially large) item.
        y0_px = float(y1) - float(padding_px)

        # Enforce minimum height: y0 must not be below (h - min_height).
        max_allowed_y0 = float(h - min_height_px)
        y0_px = min(y0_px, max_allowed_y0)

        # Clamp to top.
        y0_px = max(0.0, y0_px)

        # Convert to integer pixel index for PIL crop (floor is safer since it will
        # include more context).
        y0 = int(y0_px)

        if y0 >= h:
            raise ValueError(f"y0 ({y0}px) is beyond image height ({h}px).")

        # If bbox is loose (common for tables), the bottom crop can become mostly
        # whitespace. Expand upward a bit so the model sees more of the candidate's
        # tail (grid/rows/lines).
        crop = img.crop((0, y0, w, h))
        step_px = max(200, int(0.12 * h))
        for _ in range(2):
            if y0 <= 0 or _has_boundary_evidence(img=crop, mode="bottom"):
                break
            y0 = max(0, y0 - step_px)
            crop = img.crop((0, y0, w, h))

        output_png_fp.parent.mkdir(parents=True, exist_ok=True)
        crop.save(output_png_fp)

        return crop if return_cropped_image else None


def crop_image_to_top(
    *,
    bbox: list[float],
    desired_padding_inches: float = 0.25,
    input_png_fp: Path,
    min_height_px: int = 400,
    output_png_fp: Path,
    render_dpi: int,
    return_cropped_image: bool = False,
) -> Optional[Image.Image]:
    """Crop a rendered page image from the top down to just below the candidate bbox.

    Parameters
    ----------
    bbox
        [x0, y0, x1, y1] in *pixel units* of the rendered PNG (PageIR coordinate
        space). Only y0 (bbox[1]) is used for the crop end (we end below the TOP edge
        so the crop includes the start of the candidate near the boundary).
    desired_padding_inches
        Extra padding (in inches) to include below the crop end.
    input_png_fp
        Full-page PNG path (the extraction-time rendered page image).
    min_height_px
        Minimum crop height (pixels).
    output_png_fp
        Where to write the cropped PNG.
    render_dpi
        DPI used to interpret `desired_padding_inches` as pixels. This should be the
        same DPI used to render the extraction PNG.
    return_cropped_image
        If True, return the cropped PIL Image as well.

    Returns
    -------
    Optional[Image.Image]
        Cropped image if requested; otherwise None.

    Raises
    ------
    ValueError
        If the computed crop end is invalid.
    """

    with Image.open(input_png_fp) as img:
        w, h = img.size

        if h <= 0 or w <= 0:
            raise ValueError(f"Invalid image size: {img.size} for {input_png_fp}")

        _, y0, _, _ = [float(v) for v in bbox]
        y0 = max(0.0, min(y0, float(h)))
        padding_px = int(desired_padding_inches * render_dpi)

        # End just below the TOP edge of the candidate item. This focuses the crop on
        # boundary evidence (first rows/lines) instead of including the whole
        # (potentially large) item.
        y1_px = float(y0) + float(padding_px)

        # Enforce minimum height: y1 must be at least min_height_px.
        y1_px = max(y1_px, float(min_height_px))

        # Clamp to bottom.
        y1_px = min(y1_px, float(h))

        # Convert to integer pixel index (ceil is safer since it will include more
        # context).
        y1 = int(y1_px) if y1_px.is_integer() else int(y1_px) + 1
        y1 = min(y1, h)

        if y1 <= 0:
            raise ValueError(f"y1 ({y1}px) must be > 0.")

        # If bbox is loose, the top crop can miss the early table rows or show too much
        # blank. Expand downward a bit so the model sees more of the candidate's head
        # (header/first rows).
        crop = img.crop((0, 0, w, y1))
        step_px = max(200, int(0.12 * h))
        for _ in range(2):
            if y1 >= h or _has_boundary_evidence(img=crop, mode="top"):
                break
            y1 = min(h, y1 + step_px)
            crop = img.crop((0, 0, w, y1))

        output_png_fp.parent.mkdir(parents=True, exist_ok=True)
        crop.save(output_png_fp)

        return crop if return_cropped_image else None


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
            text = " ".join("".join(s["text"] for s in line["spans"]).split())
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
    fix_rotation: bool = True,
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
        If True, applies a heuristic: If a page is rotated AND appears landscape
        (i.e., width > height), reset rotation to 0 to try and force portrait
        orientation.
    output_png_fp
        The output PNG file path.
    page_index
        The 0-based page index to render.
    """

    page = doc.load_page(page_index)

    if fix_rotation:
        # Get current visible dimensions (respects current rotation). page.rect returns
        # the bounding box [x0, y0, x1, y1].
        width = page.rect.width
        height = page.rect.height

        # Check if the page is currently landscape.
        is_landscape = width > height

        # Check if the page has a rotation flag set (90, 180, 270).
        is_rotated = page.rotation != 0

        # Apply fix only if the page is rotated AND currently landscape. This assumes
        # the rotation is what made it landscape and we want portrait.
        if is_landscape and is_rotated:
            logger.warning(
                f"Page {page_index}: Detected Landscape ({width:.0f}x{height:.0f}) "
                f"with Rotation={page.rotation}. Resetting to 0."
            )
            page.set_rotation(0)

    scale = dpi / 72.0
    mat = pymupdf.Matrix(scale, scale)

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

    if not 0 <= start_page <= page_count:
        raise ValueError(f"start_page must be in [0, {page_count}]")
    if not (0 <= end_page <= page_count) or end_page < start_page:
        raise ValueError(f"end_page must be in [start_page, {page_count}]")

    return page_count, end_page
