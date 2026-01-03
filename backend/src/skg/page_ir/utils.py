"""This module contains utility functions for page Intermediate Representations (IRs)."""

# Standard Library
import re

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Third Party Library
from loguru import logger

# Package Library
from skg.page_ir.llm import verify_page_ir_continuity_verdict
from skg.page_ir.schemas import PageIRContinuityVerdict
from skg.utils.constants import (
    BlockType,
    ItemBoundary,
    PageBoundaryState,
    PageContinuationKind,
)
from skg.utils.general import clamp, make_dir, near


@dataclass(frozen=True)
class PageIRExtractionDirs:
    """Dataclass for page IR extraction directories."""

    root: Path
    page_images: Path
    page_irs: Path


@dataclass(frozen=True)
class PageIRVerificationDirs:
    """Dataclass for page IR verification directories."""

    root: Path
    page_irs_pair_crops: Path
    page_irs_pair_reports: Path
    page_irs_verified: Path


def _block_type_val(v: Any) -> str:
    """Normalize block type enum/string to string.

    Parameters
    ----------
    v
        The block type value (enum or string).

    Returns
    -------
    str
        The normalized block type string.
    """

    return getattr(v, "value", v) if v is not None else ""


def _boundary_str(item: Any) -> str:
    """Get the boundary string of an item.

    Parameters
    ----------
    item
        The item to get the boundary string from.

    Returns
    -------
    str
        The boundary string, or empty string if not set.
    """

    b = getattr(item, "boundary", None)
    return "" if b is None else b.value if hasattr(b, "value") else str(b)


def _boundary_val(v: Any) -> str:
    """Normalize boundary enum/string to string.

    Parameters
    ----------
    v
        The boundary value (enum or string).

    Returns
    -------
    str
        The normalized boundary string.
    """

    return getattr(v, "value", v) if v is not None else ItemBoundary.COMPLETE.value


def _derive_boundary_state_from_items(
    non_artifact_items: list[tuple[int, Any]],
) -> PageBoundaryState:
    """Derive the page boundary state from item boundaries.

    Parameters
    ----------
    non_artifact_items
        The list of non-artifact items with their indices.

    Returns
    -------
    PageBoundaryState
        The derived page boundary state.
    """

    # Only consider non-artifact items for continuity.
    non_artifact = [it for _, it in non_artifact_items]

    if not non_artifact:
        return PageBoundaryState.STANDALONE

    any_from_prev = any(is_resumed(_boundary_str(it)) for it in non_artifact)
    any_to_next = any(is_truncated(_boundary_str(it)) for it in non_artifact)

    if any_from_prev and any_to_next:
        return PageBoundaryState.BOTH
    if any_from_prev:
        return PageBoundaryState.CONTINUES_FROM_PREV
    if any_to_next:
        return PageBoundaryState.CONTINUES_TO_NEXT
    return PageBoundaryState.STANDALONE


def _has_boundary(it: dict[str, Any], b: ItemBoundary) -> bool:
    """Check if the item has the specified boundary flag.

    Parameters
    it
        The item to check.
    b
        The boundary flag to check for.

    Returns
    -------
    bool
        True if the item has the specified boundary flag, False otherwise.
    """

    v = it.get("boundary")

    if v is None:
        v = it.get("_orig_boundary")

    if v is None:
        v = ItemBoundary.COMPLETE.value

    v = getattr(v, "value", v)

    if v == getattr(b, "value", b):
        return True
    if v == ItemBoundary.BOTH.value:
        return b in (ItemBoundary.RESUMED, ItemBoundary.TRUNCATED)
    return False


def _truncate_text_word_boundary(
    *, max_len: int, mode: str, text: str
) -> tuple[str, bool]:
    """Truncate text without cutting mid-word.

    Valid modes are:
      - "head": Keep the beginning.
      - "tail": Keep the end.

    Parameters
    ----------
    max_len
        Maximum length of the returned text.
    mode
        Truncation mode ("head" or "tail").
    text
        The text to truncate.

    Returns
    -------
    tuple[str, bool]
        The truncated text and a boolean indicating if truncation occurred.
    """

    assert mode in (
        "head",
        "tail",
    ), f"Invalid mode: {mode}. Valid modes are: 'head' or 'tail'"

    if not isinstance(text, str):
        return "", False

    # Normalize whitespace so length comparisons are stable.
    t = re.sub(r"\s+", " ", text).strip()

    if len(t) <= max_len:
        return t, False

    if max_len <= 12:
        # Degenerate case; just slice.
        return (t[:max_len] if mode == "head" else t[-max_len:]), True

    if mode == "head":
        chunk = t[:max_len]
        if " " in chunk:
            chunk = chunk.rsplit(" ", 1)[0]
        return chunk + "...", True

    chunk = t[-max_len:]

    # If we started mid-word, drop the partial first word.
    if " " in chunk:
        chunk = chunk.split(" ", 1)[1]
    return "..." + chunk, True


def bottommost_continuity_candidate(
    *,
    image_height: float,
    items: list[dict[str, Any]],
) -> tuple[int, dict[str, Any]]:
    """Pick the best "bottom of page" candidate for continuity checks.

    Heuristic:
        - Choose the non-artifact item with largest bbox y1 (closest to bottom).
        - If multiple items are near the bottom-most y1 (within a small delta),
            prefer a table among that near-bottom set.

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    items
        List of PageIR items on the page.

    Returns
    -------
    tuple[int, dict[str, Any]]
        The index and item of the chosen candidate.

    Raises
    ------
    ValueError
        If no non-artifact items are found.
    """

    candidates = [
        (i, it)
        for i, it in enumerate(items)
        if (not is_artifact(it))
        and (not is_probable_header_footer_noise(image_height=image_height, item=it))
    ]
    if not candidates:
        raise ValueError("No non-artifact items found.")

    # Look at the last 5 candidates by vertical position (closest to bottom). If any
    # are explicitly marked as truncated, prefer those (tables first).
    last_n = 5
    bottom_sorted = sorted(
        candidates, key=lambda p: (float(p[1]["bbox"][3]), p[0]), reverse=True
    )
    bottom_slice = bottom_sorted[: min(last_n, len(bottom_sorted))]
    truncated = [
        (i, it) for (i, it) in bottom_slice if _has_boundary(it, ItemBoundary.TRUNCATED)
    ]
    if truncated:
        truncated_non_fig = [
            (i, it) for (i, it) in truncated if not is_figure_block(it)
        ]
        base = truncated_non_fig if truncated_non_fig else truncated
        truncated_tables = [(i, it) for (i, it) in base if it.get("kind") == "table"]
        chosen_trunc = truncated_tables if truncated_tables else base
        return max(chosen_trunc, key=lambda p: (float(p[1]["bbox"][3]), p[0]))

    # 2. Table-in-bottom-band override (bottom 20%), when edge-most is not a table.
    edge_item = max(candidates, key=lambda p: (float(p[1]["bbox"][3]), p[0]))

    # If the edge-most item is a small "minor" block (e.g., footnote-ish), but there's
    # a table close to the bottom, prefer the table as the continuity anchor.
    if edge_item[1].get("kind") != "table" and is_minor_edge_block(
        image_height=image_height, item=edge_item[1]
    ):
        tables = [(i, it) for (i, it) in candidates if it.get("kind") == "table"]
        if tables:
            # Bottom-most table by y1.
            best_table = max(tables, key=lambda p: (float(p[1]["bbox"][3]), p[0]))

            # Use a slightly wider band than 20% (tables often end above the margin).
            if float(best_table[1]["bbox"][3]) >= 0.70 * image_height:
                return best_table

    if edge_item[1].get("kind") != "table":
        bottom_band_y = 0.80 * image_height
        tables_in_band = [
            (i, it)
            for (i, it) in candidates
            if it.get("kind") == "table" and float(it["bbox"][3]) >= bottom_band_y
        ]
        if tables_in_band:
            return max(tables_in_band, key=lambda p: (float(p[1]["bbox"][3]), p[0]))

    # Edge-first: find the max y1, then consider items close to that edge.
    max_y1 = max(float(it["bbox"][3]) for _, it in candidates)
    edge_slop = min(200.0, max(80.0, 0.04 * image_height))
    near_edge = [
        (i, it)
        for i, it in candidates
        if near(float(it["bbox"][3]), max_y1, tol=edge_slop)
    ]
    table_near_edge = [(i, it) for i, it in near_edge if it.get("kind") == "table"]
    if table_near_edge:
        chosen = table_near_edge
    else:
        non_fig = [(i, it) for (i, it) in near_edge if not is_figure_block(it)]
        chosen = non_fig if non_fig else near_edge

    return max(chosen, key=lambda p: (float(p[1]["bbox"][3]), p[0]))


def create_page_ir_extraction_dirs(
    *, doc_key: str, output_dir: Path
) -> PageIRExtractionDirs:
    """Create page IR extraction directories for a given document key.

    Parameters
    ----------
    doc_key
        The document key.
    output_dir
        The output directory root.

    Returns
    -------
    PageIRExtractionDirs
        The created page IR extraction directories.
    """

    root = output_dir / doc_key / "extraction"
    page_images = root / "page_images"
    page_irs = root / "page_irs"

    for p in [root, page_images, page_irs]:
        make_dir(p)

    return PageIRExtractionDirs(root=root, page_images=page_images, page_irs=page_irs)


def create_page_ir_verification_dirs(*, output_dir: Path) -> PageIRVerificationDirs:
    """Create page IR verification directories for a given extraction run.

    Parameters
    ----------
    output_dir
        The output directory root.

    Returns
    -------
    PageIRVerificationDirs
        The created page IR verification directories.
    """

    root = output_dir
    page_irs_pair_crops = root / "page_irs_pair_crops"
    page_irs_pair_reports = root / "page_irs_pair_reports"
    page_irs_verified = root / "page_irs_verified"

    for p in [root, page_irs_pair_crops, page_irs_pair_reports, page_irs_verified]:
        make_dir(p)

    return PageIRVerificationDirs(
        root=root,
        page_irs_pair_crops=page_irs_pair_crops,
        page_irs_pair_reports=page_irs_pair_reports,
        page_irs_verified=page_irs_verified,
    )


def derive_page_boundary_state(*, page_ir: dict[str, Any]) -> PageBoundaryState:
    """Derive page-level boundary_state from verified item boundaries.

    Scan all non-artifact, non-header/footer-noise items:
      - from_prev if ANY item is resumed/both
      - to_next if ANY item is truncated/both

    Parameters
    ----------
    page_ir
        The page IR dictionary.

    Returns
    -------
    PageBoundaryState
        The derived page-level boundary state.
    """

    items = page_ir.get("items") or []
    image_height = float(page_ir.get("image_height") or 0.0)

    candidates = [
        it
        for it in items
        if not is_artifact(it)
        and not is_probable_header_footer_noise(image_height=image_height, item=it)
    ]

    if not candidates:
        return PageBoundaryState.STANDALONE

    from_prev = any(
        _boundary_val(it.get("boundary"))
        in (ItemBoundary.RESUMED.value, ItemBoundary.BOTH.value)
        for it in candidates
    )
    to_next = any(
        _boundary_val(it.get("boundary"))
        in (ItemBoundary.TRUNCATED.value, ItemBoundary.BOTH.value)
        for it in candidates
    )

    return encode_boundary_state(from_prev, to_next)


def encode_boundary_state(from_prev: bool, to_next: bool) -> PageBoundaryState:
    """Encode (from_prev, to_next) boolean flags into a PageBoundaryState enum.

    Parameters
    ----------
    from_prev
        Whether the item continues from the previous page.
    to_next
        Whether the item continues to the next page.

    Returns
    -------
    PageBoundaryState
            The encoded PageBoundaryState enum member.
    """

    if from_prev and to_next:
        return PageBoundaryState.BOTH
    if from_prev:
        return PageBoundaryState.CONTINUES_FROM_PREV
    if to_next:
        return PageBoundaryState.CONTINUES_TO_NEXT
    return PageBoundaryState.STANDALONE


def ensure_boundary(
    *,
    allow_downgrade_both: bool = False,
    desired: str,
    items: list[dict[str, Any]],
    index: int,
) -> None:
    """Ensure that the item at the given index has the desired boundary flag.

    Parameters
    ----------
    allow_downgrade_both
        If True, allow downgrading BOTH to a single-sided boundary.
    desired
        Desired boundary flag ("complete", "truncated", "resumed", or "both").
    items
        List of PageIR items.
    index
        Index of the item to update.

    Raises
    ------
    ValueError
        If an invalid boundary flag is provided.
    """

    target_boundary: ItemBoundary = ItemBoundary(desired)

    current_val = items[index].get("boundary")
    current_boundary = ItemBoundary(_boundary_val(current_val))

    if (
        target_boundary == ItemBoundary.TRUNCATED
        and current_boundary == ItemBoundary.RESUMED
    ):
        target_boundary = ItemBoundary.BOTH
    elif (
        target_boundary == ItemBoundary.RESUMED
        and current_boundary == ItemBoundary.TRUNCATED
    ):
        target_boundary = ItemBoundary.BOTH
    elif (
        (not allow_downgrade_both)
        and current_boundary == ItemBoundary.BOTH
        and target_boundary in (ItemBoundary.RESUMED, ItemBoundary.TRUNCATED)
    ):
        target_boundary = ItemBoundary.BOTH
    elif (
        (not allow_downgrade_both)
        and current_boundary == ItemBoundary.BOTH
        and target_boundary == ItemBoundary.COMPLETE
    ):
        target_boundary = ItemBoundary.BOTH

    if current_boundary != target_boundary:
        items[index]["boundary"] = target_boundary.value


def extract_text(v: Any) -> Optional[str]:
    """Extract text from a TextUnit-like structure.

    Parameters
    ----------
    v
        The TextUnit-like structure.

    Returns
    -------
    Optional[str]
        The extracted text, or None if not found.
    """

    if isinstance(v, str):
        return v

    if isinstance(v, dict):
        # Common shapes: {"text": "..."} or {"text": {"text": "..."}}.
        inner = v.get("text")
        if isinstance(inner, str):
            return inner
        if isinstance(inner, dict):
            inner2 = inner.get("text")
            return inner2 if isinstance(inner2, str) else None
    return None


def is_artifact(item: dict[str, Any]) -> bool:
    """Check if an item is an artifact.

    Parameters
    ----------
    item
        The item to check.

    Returns
    -------
    bool
        True if the item is an artifact, False otherwise.
    """

    if item.get("kind") != "block":
        return False
    return _block_type_val(item.get("block_type")) == BlockType.ARTIFACT.value


def is_figure_block(item: dict[str, Any]) -> bool:
    """Check if an item is a figure/diagram block.

    Parameters
    ----------
    item
        The item to check.

    Returns
    -------
    bool
        True if the item is a figure block, False otherwise.
    """

    if item.get("kind") != "block":
        return False
    return _block_type_val(item.get("block_type")) == BlockType.FIGURE.value


def is_full_page_bbox(
    *, bb: tuple[float, ...], page_bbox: tuple[float, ...], tol: float
) -> bool:
    """Check if a bbox is effectively full-page within tolerance.

    Parameters
    ----------
    bb
        The bbox to check.
    page_bbox
        The page bbox.
    tol
        The tolerance for comparison.

    Returns
    -------
    bool
        True if the bbox is full-page within tolerance, False otherwise.
    """

    x0, y0, x1, y1 = bb

    return (
        abs(x0 - page_bbox[0]) <= tol
        and abs(y0 - page_bbox[1]) <= tol
        and abs(x1 - page_bbox[2]) <= tol
        and abs(y1 - page_bbox[3]) <= tol
    )


def is_minor_edge_block(*, image_height: float, item: dict[str, Any]) -> bool:
    """Return True for small, low-information blocks near an edge that often sit
    below/above the real continuation content (e.g., short notes, tiny captions).

    NB: Keep this conservative to avoid suppressing real content.

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    item
        The item to check.

    Returns
    -------
    bool
        True if the item is a minor edge block, False otherwise.
    """

    bt = _block_type_val(item.get("block_type"))

    # If not a block then it's never "minor". In addition, headings are usually
    # meaningful context; do not treat as "minor".
    if item.get("kind") != "block" or bt == BlockType.HEADING.value:
        return False

    # Figures/diagrams often appear as small isolated blocks near the edge (icons,
    # stamps, small illustrations). For continuity anchoring, treat *small* figures as
    # "minor" so they don't steal the anchor from a nearby table/text.
    if bt == BlockType.FIGURE.value:
        _, y0, _, y1 = map(float, item["bbox"])
        box_h = y1 - y0
        return box_h <= max(180.0, 0.10 * image_height)

    text = (extract_text(item.get("text")) or "").strip()
    if not text:
        return False

    _, y0, _, y1 = map(float, item["bbox"])
    box_h = y1 - y0

    # Captions are often the first/last thing near the edge ("e.g., Table 2: ..."), and
    # we usually want the *table* (or main text) as the continuity anchor. Treat
    # caption blocks as minor based on visual size, not character length.
    if bt == BlockType.CAPTION.value and box_h <= max(180.0, 0.08 * image_height):
        return True

    # "minor" if it's short AND visually small.
    if len(text) <= 80 and box_h <= max(120.0, 0.06 * image_height):
        return True

    return False


def is_probable_header_footer_noise(
    *,
    image_height: float,
    item: dict[str, Any],
) -> bool:
    """Heuristic to exclude common header/footer noise (page numbers, running headers).

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    item
        The item to check.

    Returns
    -------
    bool
        True if the item is likely header/footer noise, False otherwise.
    """

    text = (extract_text(item.get("text")) or "").strip()

    if item.get("kind") != "block" or not text:
        return False

    # Very small box height is usually a strong cue (page numbers, running headers).
    bbox = item["bbox"]
    _, y0, _, y1 = map(float, bbox)
    near_top = y0 <= 0.06 * image_height
    near_bottom = y1 >= 0.94 * image_height
    if not (near_top or near_bottom):
        return False

    # Require small box height to avoid sparse pages from being mis-classified as
    # footer/header noise.
    box_h = y1 - y0
    if box_h > max(90.0, 0.05 * image_height):
        return False

    # Common page number/footer patterns (keep conservative).
    t = re.sub(r"\s+", " ", text).strip()
    if len(t) <= 12 and re.fullmatch(r"(\d+|[ivxlcdm]+)", t.lower()):
        return True
    if len(t) <= 20 and re.fullmatch(r"(page\s*)?\d+(\s*/\s*\d+)?", t.lower()):
        return True

    return False


def is_resumed(boundary: str) -> bool:
    """Check if a boundary string indicates a resumed or both item.

    Parameters
    ----------
    boundary
        The boundary string to check.

    Returns
    -------
    bool
        True if the boundary indicates a resumed or both item, False otherwise.
    """

    return boundary in (ItemBoundary.RESUMED.value, ItemBoundary.BOTH.value)


def is_truncated(boundary: str) -> bool:
    """Check if a boundary string indicates a truncated or both item.

    Parameters
    ----------
    boundary
        The boundary string to check.

    Returns
    -------
    bool
        True if the boundary indicates a truncated or both item, False otherwise.
    """

    return boundary in (ItemBoundary.TRUNCATED.value, ItemBoundary.BOTH.value)


def item_snippet(
    *, item: dict[str, Any], max_len: int = 260, text_mode: str = "head"
) -> dict[str, Any]:
    """Create a small, stable representation of an item to show the verifier model.

    Parameters
    ----------
    item
        The item to create a snippet for.
    max_len
        Maximum length of text fields.
    text_mode
        Text truncation mode ("head" or "tail").

    Returns
    -------
    dict[str, Any]
        The item snippet.
    """

    kind = item["kind"]
    assert kind in {"block", "table"}, f"Unexpected item kind: {kind}"
    out: dict[str, Any] = {
        "kind": kind,
        "bbox": item["bbox"],
        "boundary": item.get("boundary"),
        "local_code": item.get("local_code"),
    }

    if kind == "block":
        bt = _block_type_val(item.get("block_type"))
        out["block_type"] = bt
        out["language"] = (item.get("text") or {}).get("language")
        text = (item.get("text") or {}).get("text")
        if isinstance(text, str):
            snippet, was_truncated = _truncate_text_word_boundary(
                max_len=max_len, mode=text_mode, text=text
            )
            out["text"] = snippet
            out["text_was_truncated"] = was_truncated
            out["text_snippet_mode"] = text_mode
        if bt == BlockType.LIST.value:
            lis = item.get("list_items") or []
            out["list_items"] = [
                {
                    "marker": (li.get("marker") or ""),
                    "text": (extract_text(li.get("text")) or "")[:120],
                }
                for li in lis[:6]
            ]

        # Include lightweight metadata so the verifier isn't looking at an "empty"
        # excerpt for figures.
        if is_figure_block(item):
            fig = item.get("figure") or {}
            cap = fig.get("caption")
            out["figure"] = {
                "figure_kind": fig.get("figure_kind"),
                "contains_text": fig.get("contains_text"),
                "alt_text": (fig.get("alt_text") or "")[:200],
                "caption": (extract_text(cap) or "")[:max_len],
                "caption_language": (
                    (cap or {}).get("language") if isinstance(cap, dict) else None
                ),
            }
    else:
        out["header_row_count"] = item.get("header_row_count")
        out["repeats_header"] = item.get("repeats_header")
        out["n_cols"] = item.get("n_cols")
        out["n_rows"] = len(item.get("rows") or [])
        rows = item.get("rows") or []

        # Show up to 2 header rows + 1 first body row + last 2 rows.
        hrc = item.get("header_row_count") or 0
        head = rows[: min(len(rows), min(2, hrc) if hrc else 2)]

        first_body = rows[hrc : hrc + 1] if (hrc and len(rows) > hrc) else []
        tail = rows[-2:] if len(rows) > 2 else []

        def row_to_text(r: dict[str, Any]) -> list[Optional[str]]:
            """Convert a table row to a list of cell texts.

            Parameters
            r
                The table row.

            Returns
            -------
            list[Optional[str]]
                The list of cell texts.
            """

            out_cells: list[Optional[str]] = []
            for c in r.get("cells", []):
                t = extract_text(c.get("text"))
                if isinstance(t, str):
                    t = t[:120]
                out_cells.append(t)
            return out_cells

        out["rows_head"] = [row_to_text(r) for r in head]
        out["rows_first_body"] = [row_to_text(r) for r in first_body]
        out["rows_tail"] = [row_to_text(r) for r in tail]

    return out


def min_crop_height_px(*, kind: str, page_h_px: int) -> int:
    """Get the minimum crop height in pixels for continuity candidate extraction.

    kind:
      - "table": show more (tables need extra rows)
      - "figure": show more (figures need more visual context)
      - default: text-ish blocks

    Parameters
    ----------
    kind
        The kind of item ("table" or other).
    page_h_px
        The height of the page in pixels.

    Returns
    -------
    int
        The minimum crop height in pixels.
    """

    if kind == "table":
        return clamp(0.33 * page_h_px, low=900, high=1600)

    if kind == "figure":
        # Bigger than text blocks so the verifier sees enough context/continuation cues.
        return clamp(0.28 * page_h_px, low=750, high=1500)

    return clamp(0.16 * page_h_px, low=450, high=1000)


def pad_inches(kind: str) -> float:
    """Get the padding in inches for continuity candidate extraction.

    Parameters
    ----------
    kind
        The kind of item ("table" or other).

    Returns
    -------
    float
        The padding in inches.
    """

    if kind == "table":
        return 0.35
    if kind == "figure":
        return 0.50
    return 0.25


def textunit_text(tu: Any) -> str:
    """Safely extract tu.text from a TextUnit-like object.

    Parameters
    ----------
    tu
        The TextUnit-like object.

    Returns
    -------
    str
        The text content, or empty string if not available.
    """

    if tu is None:
        return ""

    value = getattr(tu, "text", None)
    return value if isinstance(value, str) else ""


def topmost_continuity_candidate(
    *,
    image_height: float,
    items: list[dict[str, Any]],
) -> tuple[int, dict[str, Any]]:
    """Pick the best "top of page" candidate for continuity checks.

    Heuristic:
        - Choose the non-artifact item with smallest bbox y0 (closest to top).
        - If multiple items are near the top-most y0 (within a small delta),
            prefer a table among that near-top set.

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    items
        List of items to search.

    Returns
    -------
    tuple[int, dict]
        The index and the chosen item.

    Raises
    ------
    ValueError
        If no non-artifact items are found.
    """

    candidates = [
        (i, it)
        for i, it in enumerate(items)
        if (not is_artifact(it))
        and (not is_probable_header_footer_noise(image_height=image_height, item=it))
    ]
    if not candidates:
        raise ValueError("No non-artifact items found.")

    # Look at the first ~5 candidates by vertical position (closest to top). If any are
    # explicitly marked as resumed, prefer those (tables first).
    first_n = 5
    top_sorted = sorted(candidates, key=lambda p: (float(p[1]["bbox"][1]), p[0]))
    top_slice = top_sorted[: min(first_n, len(top_sorted))]
    resumed = [
        (i, it) for (i, it) in top_slice if _has_boundary(it, ItemBoundary.RESUMED)
    ]
    if resumed:
        resumed_non_fig = [(i, it) for (i, it) in resumed if not is_figure_block(it)]
        base = resumed_non_fig if resumed_non_fig else resumed
        resumed_tables = [(i, it) for (i, it) in base if it.get("kind") == "table"]
        chosen_res = resumed_tables if resumed_tables else base
        return min(chosen_res, key=lambda p: (float(p[1]["bbox"][1]), p[0]))

    # 2. Table-in-top-band override (top 20%), when edge-most is not a table.
    edge_item = min(candidates, key=lambda p: (float(p[1]["bbox"][1]), p[0]))

    # If the edge-most item is a small "minor" block (short note), but a continuation
    # table starts near the top, prefer the table.
    if edge_item[1].get("kind") != "table" and is_minor_edge_block(
        image_height=image_height, item=edge_item[1]
    ):
        tables = [(i, it) for (i, it) in candidates if it.get("kind") == "table"]
        if tables:
            best_table = min(tables, key=lambda p: (float(p[1]["bbox"][1]), p[0]))

            # Wider band than 20% (tables can start below a small header line).
            if float(best_table[1]["bbox"][1]) <= 0.30 * image_height:
                return best_table

    if edge_item[1].get("kind") != "table":
        top_band_y = 0.20 * image_height
        tables_in_band = [
            (i, it)
            for (i, it) in candidates
            if it.get("kind") == "table" and float(it["bbox"][1]) <= top_band_y
        ]
        if tables_in_band:
            return min(tables_in_band, key=lambda p: (float(p[1]["bbox"][1]), p[0]))

    # Edge-first: find the min y0, then consider items close to that edge.
    min_y0 = min(float(it["bbox"][1]) for _, it in candidates)
    edge_slop = min(200.0, max(80.0, 0.04 * image_height))
    near_edge = [
        (i, it)
        for i, it in candidates
        if near(float(it["bbox"][1]), min_y0, tol=edge_slop)
    ]
    table_near_edge = [(i, it) for i, it in near_edge if it.get("kind") == "table"]
    if table_near_edge:
        chosen = table_near_edge
    else:
        non_fig = [(i, it) for (i, it) in near_edge if not is_figure_block(it)]
        chosen = non_fig if non_fig else near_edge

    return min(chosen, key=lambda p: (float(p[1]["bbox"][1]), p[0]))


def topmost_continuity_candidate_paired(
    *, image_height: float, items: list[dict[str, Any]], prev_item: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """Pick the best next-page candidate *conditioned on* the chosen previous page
    candidate. Falls back to `topmost_continuity_candidate` if no good match is found.

    Motivation:
    - If the prev candidate is a table, we should strongly prefer the first real table
      near the top of the next page (even if there are headings above it).
    - If the prev candidate is a block, we should strongly prefer a block near the top,
      avoiding selecting a table just because it starts near the edge.

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    items
        List of items to search.
    prev_item
        The chosen previous page candidate item.

    Returns
    -------
    tuple[int, dict[str, Any]]
        The index and the chosen item.

    Raises
    ------
    ValueError
        If no non-artifact items are found.
    """

    preferred_kind = prev_item.get("kind")

    # Use the same base candidate filtering as the unpaired selector.
    candidates = [
        (i, it)
        for i, it in enumerate(items)
        if (not is_artifact(it))
        and (not is_probable_header_footer_noise(image_height=image_height, item=it))
    ]

    if not candidates:
        raise ValueError("No non-artifact items found.")

    # Prefer table when the previous item is a table.
    if preferred_kind == "table":
        tables = [(i, it) for (i, it) in candidates if it.get("kind") == "table"]
        if tables:
            # If any table is explicitly RESUMED, pick the top-most resumed table.
            resumed_tables = [
                (i, it) for (i, it) in tables if _has_boundary(it, ItemBoundary.RESUMED)
            ]
            if resumed_tables:
                return min(resumed_tables, key=lambda p: (float(p[1]["bbox"][1]), p[0]))

            # Otherwise pick the top-most table, but only if it's not absurdly far down.
            best_table = min(tables, key=lambda p: (float(p[1]["bbox"][1]), p[0]))
            if float(best_table[1]["bbox"][1]) <= 0.65 * image_height:
                return best_table

    # Prefer block when the previous item is a block.
    if preferred_kind == "block":
        blocks = [(i, it) for (i, it) in candidates if it.get("kind") != "table"]
        if blocks:
            # if we have any non-minor blocks near the top, drop minor edge blocks
            # (e.g., short captions like "Table 2: ...") so they don't become the
            # anchor.
            non_minor = [
                (i, it)
                for (i, it) in blocks
                if not is_minor_edge_block(image_height=image_height, item=it)
            ]
            blocks = non_minor or blocks

            # Prefer non-figure blocks if possible.
            non_fig = [(i, it) for (i, it) in blocks if not is_figure_block(it)]
            chosen = non_fig if non_fig else blocks
            return min(chosen, key=lambda p: (float(p[1]["bbox"][1]), p[0]))

    # Fallback to unpaired logic.
    return topmost_continuity_candidate(image_height=image_height, items=items)


def veto_continuation(
    *, reason: str, verdict: PageIRContinuityVerdict
) -> PageIRContinuityVerdict:
    """Veto a continuation claim by forcing is_continuation=False with low confidence.

    Parameters
    ----------
    reason
        The reason for vetoing the verdict.
    verdict
        The continuity verdict from the model.

    Returns
    -------
    PageIRContinuityVerdict
        The modified verdict with the veto applied.
    """

    logger.warning(f"Vetoing continuation due to: {reason}")

    verdict.is_continuation = False

    # Candidate mismatch means we can't trust the continuation claim between THESE two
    # items, not "there is definitely no continuation anywhere". Keep this
    # continuation_kind="none" and low-confidence so downstream edit-application
    # thresholds will not apply.
    verdict.clamped_confidence = min(float(verdict.confidence), 0.49)
    verdict.continuation_kind = PageContinuationKind.NONE
    verdict.rationale = (verdict.rationale or "") + f" | Postprocess veto: {reason}"

    # NB: Never apply edits if we veto the continuation claim.
    verdict.set_prev_item_boundary = None
    verdict.set_next_item_boundary = None
    verdict.set_next_table_repeats_header = None

    verify_page_ir_continuity_verdict(verdict)
    return verdict
