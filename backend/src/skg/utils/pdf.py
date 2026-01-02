"""This module contains utilities related to PDF documents."""

# Standard Library
import hashlib
import re

from collections import Counter
from pathlib import Path
from typing import Optional

# Third Party Library
import pymupdf

from PIL import Image


def _add_section_for_text_layer_hint(
    *,
    items: list[tuple[float, float, float, str]],
    limit: int,
    out: list[str],
    sx: float,
    sy: float,
    title: str,
) -> None:
    """Add a section to the text layer hint output.

    Parameters
    ----------
    items
        The list of items (y0, x0, size, text).
    limit
        Maximum number of items to include.
    out
        The output list to append to.
    sx
        Scale factor for x coordinates.
    sy
        Scale factor for y coordinates.
    title
        The section title.
    """

    if not items:
        return

    out.append(f"## {title}")

    for y0, x0, size, txt in items[:limit]:
        # Truncate individual lines to keep digest compact.
        if len(txt) > 220:
            txt = txt[:220].rstrip() + "..."
        out.append(f"[x={x0 * sx:.1f} y={y0 * sy:.1f} sz={size:.1f}] {txt}")


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

        padding_px = int(desired_padding_inches * render_dpi)

        # Start just above the BOTTOM edge of the candidate item. This focuses the crop
        # on boundary evidence (tail rows/lines) instead of including the whole
        # (potentially large) item.
        y0_px = float(bbox[3]) - float(padding_px)

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

        padding_px = int(desired_padding_inches * render_dpi)

        # End just below the TOP edge of the candidate item. This focuses the crop on
        # boundary evidence (first rows/lines) instead of including the whole
        # (potentially large) item.
        y1_px = float(bbox[1]) + float(padding_px)

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
      - Uses get_text("dict") for per-line positions and font sizes
      - Converts PDF coordinates to rendered PNG pixel coords using image dims
      - Produces a compact digest: headings/header/footer/top/bottom body
      - Adds optional column x-peaks from word positions

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

    page = doc.load_page(page_index)

    # Pull structured text.
    d = page.get_text("dict")
    blocks = d.get("blocks") if isinstance(d, dict) else None

    # Try plain text as fallback.
    if not blocks:
        fallback = (page.get_text("text") or "").strip()
        return (
            fallback[:max_chars]
            + ("\n...[truncated]" if len(fallback) > max_chars else "")
            if fallback
            else None
        )

    # Coordinate conversion from PDF units to pixels.
    page_w, page_h = float(page.rect.width), float(page.rect.height)
    sx, sy = float(image_width) / page_w, float(image_height) / page_h

    # Collect line items: (y0, x0, max_font_size, text).
    lines: list[tuple[float, float, float, str]] = []
    for b in (b for b in blocks if b.get("type") == 0):
        for ln in b.get("lines", []):
            spans = ln.get("spans", [])

            # Join span texts in order.
            text = " ".join("".join(s.get("text", "") for s in spans).split())

            if not text:
                continue

            # Font size heuristic: take max span size in the line.
            max_size = max((float(s.get("size", 0.0)) for s in spans), default=0.0)
            bbox = ln.get("bbox") or b.get("bbox")
            x0, y0 = (float(bbox[0]), float(bbox[1])) if bbox else (0.0, 0.0)
            lines.append((y0, x0, max_size, text))

    if not lines:
        return None

    # Stable visual order.
    lines.sort(key=lambda t: (t[0], t[1]))

    # Identify regions by y band (in PDF coordinates).
    header_y, footer_y = 0.08 * page_h, 0.92 * page_h
    header = [t for t in lines if t[0] <= header_y]
    footer = [t for t in lines if t[0] >= footer_y]
    body = [t for t in lines if header_y < t[0] < footer_y]

    # Heading candidates: pick top font sizes (percentile-ish) without heavy heuristics.
    sizes = sorted([t[2] for t in lines if t[2] > 0.0])
    p90 = sizes[int(0.90 * (len(sizes) - 1))] if sizes else 0.0

    # Column x-peaks (optional): helps multi-column reading order.
    col_peaks: list[float] = []
    words = page.get_text("words") or []

    # Only compute peaks if we have enough signal.
    if len(words) >= 80 or len(lines) >= 20:
        # words: (x0, y0, x1, y1, "word", block_no, line_no, word_no).
        bin_w = 20.0
        xs = [float(w[0]) for w in words if len(w) >= 1]
        bins = [int(x // bin_w) for x in xs]
        top_bins = [b for b, _ in Counter(bins).most_common(3)]
        col_peaks = [b * bin_w for b in top_bins]

    out = [
        f"PAGE_TEXT_LAYER_DIGEST dims={image_width}x{image_height}px lines={len(lines)}"
    ]
    if col_peaks:
        out.append(
            f"COLUMN_X0_PEAKS_PX={', '.join(f'{p:.1f}' for p in [p * sx for p in col_peaks])}"
        )

    _add_section_for_text_layer_hint(
        items=sorted(
            [t for t in lines if t[2] >= max(p90, 11.5)],
            key=lambda t: (-t[2], t[0], t[1]),
        ),
        limit=min(12, max_lines_per_section),
        out=out,
        sx=sx,
        sy=sy,
        title="HEADINGS_CANDIDATES",
    )
    _add_section_for_text_layer_hint(
        items=header,
        limit=min(10, max_lines_per_section),
        out=out,
        sx=sx,
        sy=sy,
        title="HEADER_CANDIDATES",
    )
    _add_section_for_text_layer_hint(
        items=footer,
        limit=min(10, max_lines_per_section),
        out=out,
        sx=sx,
        sy=sy,
        title="FOOTER_CANDIDATES",
    )

    # Body lines: avoid duplicate TOP/BOTTOM on short pages.
    if len(body) <= max_lines_per_section:
        _add_section_for_text_layer_hint(
            items=body,
            limit=max_lines_per_section,
            out=out,
            sx=sx,
            sy=sy,
            title="BODY_LINES",
        )
    else:
        _add_section_for_text_layer_hint(
            items=body[:max_lines_per_section],
            limit=max_lines_per_section,
            out=out,
            sx=sx,
            sy=sy,
            title="TOP_BODY_LINES",
        )
        _add_section_for_text_layer_hint(
            items=body[-max_lines_per_section:],
            limit=max_lines_per_section,
            out=out,
            sx=sx,
            sy=sy,
            title="BOTTOM_BODY_LINES",
        )

    # Small raw excerpt fallback (kept short).
    if raw := (page.get_text("text") or "").strip():
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        out.append("## RAW_TEXT_EXCERPT")
        out.append(raw[:600].rstrip() + ("\n...[truncated]" if len(raw) > 600 else ""))

    hint = "\n".join(out).strip()

    return (
        hint[:max_chars].rstrip() + "\n...[truncated]"
        if len(hint) > max_chars
        else hint
    )


def is_mostly_blank(*, png_fp: Path) -> bool:
    """Check if the rendered page image is nearly blank (separator/intentional blank
    page). Uses grayscale histogram statistics (no OCR).

    NB: If we cannot read the image properly, then the default behavior is to NOT treat
    the image as blank.

    Parameters
    ----------
    png_fp
        The PNG file path of the page image.

    Returns
    -------
    bool
        True if the page is visually blank, False otherwise.
    """

    try:
        with Image.open(png_fp) as im:
            im = im.convert("L")
            w, h = im.size

            # Ignore outer border where crop marks/page frame lines live. 3% is usually
            # enough at 200 DPI; use small values to avoid skipping real content.
            pad_x = int(w * 0.03)
            pad_y = int(h * 0.03)
            x0 = min(max(pad_x, 0), w - 1)
            y0 = min(max(pad_y, 0), h - 1)
            x1 = max(min(w - pad_x, w), x0 + 1)
            y1 = max(min(h - pad_y, h), y0 + 1)

            im = im.crop((x0, y0, x1, y1))

            # Ignore bottom strip where page number boxes often sit. Keep 92% of the
            # (already border-cropped) height.
            bw, bh = im.size
            bottom_keep = int(bh * 0.92)
            if bottom_keep > 20:  # Avoid degenerate crops on tiny images
                im = im.crop((0, 0, bw, bottom_keep))

            # Downsample aggressively for speed.
            im.thumbnail((600, 600))

            # Histogram over 0..255.
            hist = im.histogram()
            total = float(sum(hist)) or 1.0

            # Fraction of "dark-ish" pixels (tuned to catch faint text but not crop
            # marks). 0..199 are darker than mid-gray.
            dark_frac = sum(hist[:200]) / total

            # Contrast and brightness stats.
            mean = sum(i * c for i, c in enumerate(hist)) / total
            var = sum(((i - mean) ** 2) * c for i, c in enumerate(hist)) / total
            std = var**0.5

            # Conservative thresholds:
            # - very low dark pixels
            # - low contrast (std)
            # - fairly light background
            return dark_frac < 0.006 and std < 14.0 and mean > 210.0
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

    Raises
    ------
    ValueError
        If the file is not a valid PNG.
    """

    data = png_fp.read_bytes()

    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Not a PNG file: {png_fp}")

    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")

    return width, height


def render_page_to_png(
    *,
    doc: pymupdf.Document,
    dpi: int,
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
