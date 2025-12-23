"""This module contains utility functions for page Intermediate Representations (IRs)."""

# Standard Library
import re

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Package Library
from skg.utils.constants import ItemBoundary, PageBoundaryState
from skg.utils.general import clamp, make_dir, near


@dataclass(frozen=True)
class PageIRExtractionDirs:
    """Dataclass for page IR extraction directories."""

    root: Path
    artifacts: Path
    page_images: Path
    page_irs: Path


@dataclass(frozen=True)
class PageIRVerificationDirs:
    """Dataclass for page IR verification directories."""

    root: Path
    page_irs_pair_crops: Path
    page_irs_pair_reports: Path
    page_irs_verified: Path


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

    # 1. Prefer boundary-marked items when they exist (strict-continuity helper).
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
        return v == b or v == b.value

    # Look at the last ~5 candidates by vertical position (closest to bottom). If any
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
        truncated_tables = [
            (i, it) for (i, it) in truncated if it.get("kind") == "table"
        ]
        chosen_trunc = truncated_tables if truncated_tables else truncated
        return max(chosen_trunc, key=lambda p: (float(p[1]["bbox"][3]), p[0]))

    # 2. Table-in-bottom-band override (bottom 20%), when edge-most is not a table.
    edge_item = max(candidates, key=lambda p: (float(p[1]["bbox"][3]), p[0]))
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
    chosen = table_near_edge if table_near_edge else near_edge

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
    artifacts = root / "artifacts"
    page_images = root / "page_images"
    page_irs = root / "page_irs"

    for p in [root, page_images, page_irs, artifacts]:
        make_dir(p)

    return PageIRExtractionDirs(
        root=root, artifacts=artifacts, page_images=page_images, page_irs=page_irs
    )


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


def decode_boundary_state(state: str | PageBoundaryState) -> tuple[bool, bool]:
    """Decode a boundary_state string/enum into (from_prev, to_next) boolean flags.

    Parameters
    ----------
    state
        The boundary_state string or Enum member.

    Returns
    -------
    tuple[bool, bool]
        A tuple of (from_prev, to_next) boolean flags.
    """

    if isinstance(state, str):
        state = PageBoundaryState(state)

    return (
        state in (PageBoundaryState.CONTINUES_FROM_PREV, PageBoundaryState.BOTH),
        state in (PageBoundaryState.CONTINUES_TO_NEXT, PageBoundaryState.BOTH),
    )


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


def ensure_boundary(*, desired: str, items: list[dict[str, Any]], index: int) -> None:
    """Ensure that the item at the given index has the desired boundary flag.

    Parameters
    ----------
    desired
        Desired boundary flag ("complete", "truncated", or "resumed").
    items
        List of PageIR items.
    index
        Index of the item to update.

    Raises
    ------
    ValueError
        If an invalid boundary flag is provided.
    """

    target_boundary = ItemBoundary(desired)

    current_val = items[index].get("boundary")
    current_boundary = (
        ItemBoundary(current_val) if current_val else ItemBoundary.COMPLETE
    )

    # 3. Update: Only change if different
    if current_boundary != target_boundary:
        # Since ItemBoundary inherits from str, we can assign the Enum directly
        items[index]["boundary"] = target_boundary


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

    return item.get("kind") == "block" and item.get("block_type") == "artifact"


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


def item_snippet(*, item: dict[str, Any], max_len: int = 260) -> dict[str, Any]:
    """Create a small, stable representation of an item to show the verifier model.

    Parameters
    ----------
    item
        The item to create a snippet for.
    max_len
        Maximum length of text fields.

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
        out["block_type"] = item.get("block_type")
        out["language"] = (item.get("text") or {}).get("language")
        text = (item.get("text") or {}).get("text")
        if isinstance(text, str):
            out["text"] = text[:max_len]
        if item.get("block_type") == "list":
            lis = item.get("list_items") or []
            out["list_items"] = [
                {
                    "marker": (li.get("marker") or ""),
                    "text": (extract_text(li) or "")[:120],
                }
                for li in lis[:6]
            ]
    else:
        out["header_row_count"] = item.get("header_row_count")
        out["repeats_header"] = item.get("repeats_header")
        out["n_cols"] = item.get("n_cols")
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
    return clamp(0.16 * page_h_px, low=450, high=1000)


def set_boundary_flag(*, flag: str, page_ir: dict[str, Any], value: bool) -> None:
    """Set exactly one direction flag on page_ir["boundary_state"] without clobbering
    the other. `flag` must be one of: "from_prev" or "to_next" (i.e.,
    PageBoundaryState.CONTINUES_FROM_PREV.value / CONTINUES_TO_NEXT.value).

    Parameters
    ----------
    flag
        The flag to set ("from_prev" or "to_next").
    page_ir
        The page IR dictionary.
    value
        The boolean value to set for the flag.

    Raises
    ------
    ValueError
        If an unknown flag is provided.
    """

    cur = page_ir.get("boundary_state", "standalone")
    from_prev, to_next = decode_boundary_state(cur)

    if flag == "from_prev":
        from_prev = value
    elif flag == "to_next":
        to_next = value
    else:
        raise ValueError(f"Unknown flag: {flag}")

    page_ir["boundary_state"] = encode_boundary_state(from_prev, to_next).value


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

    # 1. Prefer boundary-marked items when they exist (strict-continuity helper).
    def _has_boundary(it: dict[str, Any], b: ItemBoundary) -> bool:
        """Check if the item has the specified boundary flag.

        Parameters
        ----------
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
        return v == b or v == b.value

    # Look at the first ~5 candidates by vertical position (closest to top). If any are
    # explicitly marked as resumed, prefer those (tables first).
    first_n = 5
    top_sorted = sorted(candidates, key=lambda p: (float(p[1]["bbox"][1]), p[0]))
    top_slice = top_sorted[: min(first_n, len(top_sorted))]
    resumed = [
        (i, it) for (i, it) in top_slice if _has_boundary(it, ItemBoundary.RESUMED)
    ]
    if resumed:
        resumed_tables = [(i, it) for (i, it) in resumed if it.get("kind") == "table"]
        chosen_res = resumed_tables if resumed_tables else resumed
        return min(chosen_res, key=lambda p: (float(p[1]["bbox"][1]), p[0]))

    # 2. Table-in-top-band override (top 20%), when edge-most is not a table.
    edge_item = min(candidates, key=lambda p: (float(p[1]["bbox"][1]), p[0]))
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
    chosen = table_near_edge if table_near_edge else near_edge

    return min(chosen, key=lambda p: (float(p[1]["bbox"][1]), p[0]))
