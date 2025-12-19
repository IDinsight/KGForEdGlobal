"""This module contains utility functions for Intermediate Representations (IRs). It
acts as a coordinator, delegating specific logic to the `processing` subpackage.
"""

# Standard Library
import re

from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Package Library
from skg.ir.processing.continuity import (
    ContinuityApplicator,
    ContinuityState,
    build_next_continuity_state,
)
from skg.ir.processing.heuristics import (
    convert_abbreviation_tables_to_glossary,
    infer_page_kind,
    normalize_front_matter_roles,
    normalize_node_types,
    promote_high_level_objectives,
    promote_learning_area_rows_to_nodes,
)
from skg.ir.schemas import PageIR, ProvenancePointer
from skg.utils.constants import PageKind, TableKind
from skg.utils.general import make_dir, open_json_type, write_to_json

META_WARNING_RE = re.compile(r"\b(doc_key|pdf_name)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ExtractionDirs:
    """Dataclass for extraction directories."""

    root: Path
    artifacts: Path
    page_images: Path
    page_ir: Path


def _snippet(s: str, max_len: int = 140) -> str:
    """Creates a single-line snippet of text for logging.

    Parameters
    ----------
    s
        The input string.
    max_len
        The maximum length of the snippet.

    Returns
    -------
    str
        The snippet.
    """

    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= max_len else (s[: max_len - 3] + "...")


def apply_cross_page_continuity(page_ir: PageIR, prev: ContinuityState) -> PageIR:
    """Stitches the current page's semantic graph to the context of the previous page.

    This function handles the following:

    1. **Explicit Links (raw-id remapping):** An element on Page N refers to a parent
       (or other ref) on Page N-1 by its raw ID (e.g. "n10"). This function remaps
       "n10" to the globally namespaced ID from the previous page (e.g. "p0004:n10").
    2. **Implicit Continuations (explicitly marked):** If the extractor marked an
       element with `is_continuation=True` but did not provide a parent, this function
       attaches it to the `active_parent_ref` recorded at the end of Page N-1.
    3. **Implicit structural continuations (conservative heuristics):**
       If the model forgot to mark `is_continuation`, but (a) the current page has
       *no new hierarchy nodes* (content-only spillover), or (b) the page begins with
       a numbered subsection (e.g., "2.3") that should sit under an ancestor heading
       that appeared on the prior page, attach orphaned elements to the most plausible
       ancestor in the previous active path.

    This is common in curricula where subsections start at the top of the next page
    while the parent heading remains on the previous page.

    Parameters
    ----------
    page_ir
        The current page's IR. Its local IDs must already be namespaced (e.g.,
        "p0005:n1") by `ensure_namespace_page_refs`.
    prev
        The state object returned by processing the *previous* page. Contains mappings
        for the previous page's raw IDs and the ID/path context of the last open node.

    Returns
    -------
    PageIR
        The modified PageIR with `parent_ref`, `path`, `cross_references`, and
        relationship `source_ref`/`target_ref` updated to connect across page
        boundaries.
    """

    applicator = ContinuityApplicator(page_ir, prev)
    return applicator.apply()


def build_continuity_state_from_page(
    page_ir: PageIR, prev: ContinuityState
) -> ContinuityState:
    """Builds the continuity state to carry forward to the next page.

    Parameters
    ----------
    page_ir
        The current page's IR.
    prev
        The continuity state from the previous page.

    Returns
    -------
    ContinuityState
        The continuity state to carry forward to the next page.
    """

    return build_next_continuity_state(page_ir, prev)


def continuity_fp(extraction_dirs: ExtractionDirs) -> Path:
    """Get the file path for the continuity state JSON.

    Parameters
    ----------
    extraction_dirs
        The extraction directories.

    Returns
    -------
    Path
        The file path for the continuity state JSON.
    """

    return extraction_dirs.root / "continuity_state.json"


def continuity_state_to_json(state: ContinuityState) -> dict[str, Any]:
    """Serialize ContinuityState to JSON-compatible dict.

    Parameters
    ----------
    state
        The ContinuityState to serialize.

    Returns
    -------
    dict[str, Any]
        The JSON-compatible dictionary representation of the ContinuityState.
    """

    return {
        "active_parent_ref": state.active_parent_ref,
        "active_path": list(state.active_path or []),
        "active_path_ctx": list(state.active_path_ctx or []),
        "recent_raw_ref_map": {
            k: list(v) for k, v in (state.recent_raw_ref_map or {}).items()
        },
    }


def continuity_state_from_json(data: dict[str, Any]) -> ContinuityState:
    """Deserialize ContinuityState from JSON-compatible dict.

    Parameters
    ----------
    data
        The JSON-compatible dictionary representation of the ContinuityState.

    Returns
    -------
    ContinuityState
        The deserialized ContinuityState.
    """

    m: dict[str, deque[tuple[int, str]]] = defaultdict(deque)
    for k, hist in (data.get("recent_raw_ref_map") or {}).items():
        # hist is expected to be a list of [page_index, namespaced_ref].
        m[k] = deque((int(pi), str(ref)) for (pi, ref) in hist)

    return ContinuityState(
        active_parent_ref=data.get("active_parent_ref"),
        active_path=list(data.get("active_path") or []),
        active_path_ctx=list(data.get("active_path_ctx") or []),
        recent_raw_ref_map=m,
    )


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


def load_continuity_state(extraction_dirs: ExtractionDirs) -> ContinuityState:
    """Load the continuity state from file.

    Parameters
    ----------
    extraction_dirs
        The extraction directories.

    Returns
    -------
    ContinuityState
        The loaded continuity state.
    """

    fp = continuity_fp(extraction_dirs)
    if not fp.exists():
        return ContinuityState()
    return continuity_state_from_json(open_json_type(fp))


def postprocess_page_ir(
    *,
    doc_languages: list[str] | None = None,
    fallback_base_ptr: ProvenancePointer | None = None,
    heuristics_config: dict[str, Any] | None = None,
    page_ir: PageIR,
) -> PageIR:
    """Deterministic per-page repairs with safe/reasonable defaults.

    Runs after provenance normalization + ref namespacing. These steps are intended to
    be conservative and idempotent.

    Parameters
    ----------
    doc_languages
        The document's language codes.
    fallback_base_ptr
        Fallback provenance pointer to use when promoting rows to nodes.
    heuristics_config
        Configuration for heuristics.
    page_ir
        The PageIR to post-process.

    Returns
    -------
    PageIR
        The post-processed PageIR.
    """

    hc = heuristics_config or {}
    sanitize_page_warnings(page_ir)

    if page_ir.page_kind in (None, PageKind.UNKNOWN):
        page_ir.page_kind = infer_page_kind(
            languages=doc_languages or [],
            keyword_packs_by_lang=hc.get("page_kind_keywords_by_lang"),
            page_ir=page_ir,
        )

    # Normalize layout front-matter tables so they never get “data-table parsed”.
    if page_ir.page_kind in (PageKind.TOC, PageKind.LIST_OF_TABLES):
        for t in page_ir.tables or []:
            t.table_kind = TableKind.LAYOUT

    normalize_node_types(overrides=hc.get("node_type_overrides"), page_ir=page_ir)
    normalize_front_matter_roles(page_ir)
    promote_high_level_objectives(page_ir)
    convert_abbreviation_tables_to_glossary(
        fallback_base_ptr=fallback_base_ptr, page_ir=page_ir
    )
    promote_learning_area_rows_to_nodes(
        config=hc.get("row_promotion"),
        fallback_base_ptr=fallback_base_ptr,
        page_ir=page_ir,
    )

    return page_ir


def sanitize_page_warnings(page_ir: PageIR) -> None:
    """Removes non-actionable metadata-related warnings from PageIR.

    Parameters
    ----------
    page_ir
        The PageIR to sanitize warnings for.
    """

    if not page_ir.warnings:
        return
    page_ir.warnings = [w for w in page_ir.warnings if not META_WARNING_RE.search(w)]


def save_continuity_state(
    extraction_dirs: ExtractionDirs, state: ContinuityState
) -> None:
    """Persists the current cross-page continuity state to disk (JSON).

    This acts as a "checkpoint" or "save game" for the extraction process. By saving
    the state after every page, we ensure that:

    1. **Crash Recovery:** If the script fails on Page N, we can resume from Page N+1
        using the state derived from Page N.
    2. **Debugging:** We can inspect `continuity_state.json` to see exactly what
        context (active parent, ref map) was passed to the most recent page.

    Parameters
    ----------
    extraction_dirs
        The directory structure object containing the path to the root folder.
    state
        The state object to serialize. This contains the `active_parent_ref` and the
        `recent_raw_ref_map` generated by `build_continuity_state_from_page`.
    """

    write_to_json(continuity_fp(extraction_dirs), continuity_state_to_json(state))


def validate_page_ir(page_ir: PageIR) -> PageIR:  # pylint:disable=R1260
    """Perform conservative validation and minor repairs. This function is intended to
    run after postprocess_page_ir() and after continuity application. It mutates
    page_ir in place and appends human-readable warnings to page_ir.warnings.

    Parameters
    ----------
    page_ir
        The PageIR to validate and repair.

    Returns
    -------
    PageIR
        The validated and repaired PageIR.
    """

    page_ir.warnings = page_ir.warnings or []

    # 1. Self-parent cycles -> null parent_ref.
    for el in page_ir.nodes or []:
        if el.parent_ref == el.ref:
            el.parent_ref = None
            page_ir.warnings.append(
                f"[validate p{page_ir.page_index:04d}] node self-parent cycle fixed: {el.ref}"
            )
    for el in (page_ir.statements or []) + (page_ir.curriculum_elements or []):
        if getattr(el, "parent_ref", None) == getattr(el, "ref", None):
            el.parent_ref = None
            page_ir.warnings.append(
                f"[validate p{page_ir.page_index:04d}] element self-parent cycle fixed: {el.ref}"
            )

    # 2. Orphan statements on content pages.
    if page_ir.page_kind == PageKind.CONTENT:
        for st in page_ir.statements or []:
            if st.parent_ref is None:
                page_ir.warnings.append(
                    f"[validate p{page_ir.page_index:04d}] orphan statement: {st.ref} :: '{_snippet(st.text)}'"
                )

    # 3. Path invariant: if path exists, it should end with self ref (at least for
    # nodes).
    for n in page_ir.nodes or []:
        if n.path and n.path[-1] != n.ref:
            page_ir.warnings.append(
                f"[validate p{page_ir.page_index:04d}] node path invariant violated for {n.ref} "
                f"(endswith='{n.path[-1]}'); repairing"
            )

            # Conservative repair:
            # - If self ref appears somewhere in the path, truncate to its last
            #   occurrence.
            # - Otherwise append self ref.
            if n.ref in n.path:
                last_idx = len(n.path) - 1 - list(reversed(n.path)).index(n.ref)
                n.path = list(n.path[: last_idx + 1])
            else:
                n.path = list(n.path) + [n.ref]

    return page_ir
