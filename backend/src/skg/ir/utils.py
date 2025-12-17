"""This module contains utility functions for Intermediate Representation (IR) package."""

# Standard Library
import json

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

# Package Library
from skg.ir.schemas import (
    DocumentIR,
    DocumentMetadataIR,
    ExtractionRunIR,
    PageIR,
    ProvenancePointer,
)
from skg.utils.constants import BBoxKind
from skg.utils.general import make_dir, write_text


@dataclass
class ContinuityState:
    """Dataclass for cross-page continuity state."""

    active_parent_ref: Optional[str] = None
    active_path: list[str] = field(default_factory=list)

    # Maps raw refs like "n12" -> "p0007:n12".
    recent_raw_ref_map: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractionDirs:
    """Dataclass for extraction directories."""

    root: Path
    artifacts: Path
    page_images: Path
    page_ir: Path


def apply_cross_page_continuity(  # pylint: disable=too-complex
    page_ir: PageIR, prev: ContinuityState
) -> PageIR:
    """Apply cross-page continuity to a PageIR.

    Parameters
    ----------
    page_ir
        The PageIR to apply continuity to.
    prev
        The previous continuity state.

    Returns
    -------
    PageIR
        The PageIR with continuity applied.
    """

    def remap_raw(r: Optional[str]) -> Optional[str]:
        """Remap a raw ref using the previous continuity state's recent_raw_ref_map.

        Parameters
        ----------
        r
            The raw ref to remap.

        Returns
        -------
        Optional[str]
            The remapped ref if found, else the original ref.
        """

        if not r:
            return r

        # Already qualified
        if ":" in r:
            return r
        return prev.recent_raw_ref_map.get(r, r)

    def patch_parent(el: Any) -> None:
        """Patch the parent_ref and path of an element.

        Parameters
        ----------
        el
            The element to patch.
        """

        parent_ref = getattr(el, "parent_ref", None)
        parent_ref2 = remap_raw(parent_ref)

        # If it was unqualified and we could map it, patch it.
        if parent_ref2 != parent_ref:
            el.parent_ref = parent_ref2

        # If it's a continuation and parent is missing/unhelpful, fall back to active
        # parent.
        if getattr(el, "is_continuation", False):
            if not getattr(el, "parent_ref", None) and prev.active_parent_ref:
                el.parent_ref = prev.active_parent_ref

        # Patch path too (GraphElementIR.path is a list of refs).
        if hasattr(el, "path") and getattr(el, "path", None):
            el.path = [remap_raw(x) for x in el.path]

    # Patch parent/path on all structural elements.
    for col_name in (
        "nodes",
        "statements",
        "tables",
        "diagrams",
        "curriculum_elements",
    ):
        for el in getattr(page_ir, col_name, None) or []:
            patch_parent(el)

    # Patch relationships.
    for rel in page_ir.relationships or []:
        rel.source_ref = remap_raw(rel.source_ref)
        rel.target_ref = remap_raw(rel.target_ref)
        patch_parent(rel)  # Patches rel.path if it has one

    return page_ir


def build_continuity_state_from_page(page_ir: PageIR) -> ContinuityState:
    """Build continuity state from a PageIR.

    Parameters
    ----------
    page_ir
        The PageIR to build continuity state from.

    Returns
    -------
    ContinuityState
        The built continuity state.
    """

    # Prefer a node with the deepest path; fallback to "last node with a ref"
    active_parent_ref = None
    active_path: list[str] = []

    nodes = page_ir.nodes or []
    if nodes:
        # deepest path wins
        best = max(nodes, key=lambda n: len(getattr(n, "path", None) or []))
        active_parent_ref = best.ref
        active_path = list(getattr(best, "path", None) or [])
        if not active_path:
            # if model didn’t provide path, approximate with just the node ref
            active_path = [best.ref]

    # For cross-page remap, store raw->qualified for this page only
    # raw ref is the suffix after the last ":" (matches your "p0007:" convention)
    recent_raw_ref_map: dict[str, str] = {}
    for el in (
        nodes
        + (page_ir.statements or [])
        + (page_ir.tables or [])
        + (page_ir.diagrams or [])
        + (page_ir.curriculum_elements or [])
        + (page_ir.relationships or [])
    ):
        r = getattr(el, "ref", None)
        if r and ":" in r:
            recent_raw_ref_map[r.split(":")[-1]] = r

    return ContinuityState(
        active_parent_ref=active_parent_ref,
        active_path=active_path,
        recent_raw_ref_map=recent_raw_ref_map,
    )


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


def document_ir_json_schema(strict: bool = True) -> dict[str, Any]:
    """Get the JSON schema for the DocumentIR model.

    Parameters
    ----------
    strict
        Whether to enforce strictness (i.e., no additional properties).

    Returns
    -------
    dict[str, Any]
        The JSON schema for the DocumentIR model.
    """

    schema = DocumentIR.model_json_schema()
    return make_schema_strict(schema) if strict else schema


def ensure_namespace_page_refs(  # pylint: disable=too-complex, too-many-branches
    *, page_ir: PageIR, prefix: str
) -> PageIR:
    """Ensure refs are unique across the whole document by namespacing each page's
    refs. This avoids DocumentIR.validate_unique_refs failures when the model restarts
    numbering on each page.

    1. De-dupe refs within a single page across ALL element types
        (nodes/statements/curriculum_elements/tables/diagrams/relationships).
    2. Then namespace everything with prefix (e.g., "p0007:") and remap intra-page
        links.

    Parameters
    ----------
    page_ir
        The PageIR to namespace.
    prefix
        The prefix to add to each ref.

    Returns
    -------
    PageIR
        The namespaced PageIR.
    """

    # Collect all elements on the page (across all types).
    buckets: list[tuple[str, list[Any], str]] = [
        ("curriculum_elements", page_ir.curriculum_elements, "c"),
        ("diagrams", page_ir.diagrams, "d"),
        ("nodes", page_ir.nodes, "n"),
        ("relationships", page_ir.relationships, "r"),
        ("statements", page_ir.statements, "s"),
        ("tables", page_ir.tables, "t"),
    ]
    element_lists: list[Any] = []
    for _, element_list, _ in buckets:
        element_lists.extend(element_list)

    # De-dupe refs within the page across all element types.
    refs = [e.ref for e in element_lists if getattr(e, "ref", None)]
    counts = Counter(refs)
    dupes = [r for r, c in counts.items() if c > 1]

    if dupes:
        # map ref -> list of (element, tag)
        occ: dict[str, list[tuple[Any, str]]] = defaultdict(list)
        for _, lst, tag in buckets:
            for el in lst:
                if getattr(el, "ref", None):
                    occ[el.ref].append((el, tag))

        existing = set(refs)
        for r in dupes:
            # Keep first occurrence as-is; rename the rest.
            for i, (el, tag) in enumerate(occ[r][1:], start=1):
                new_ref = f"{r}__{tag}{i}"
                while new_ref in existing:
                    new_ref += "_"
                el.ref = new_ref
                existing.add(new_ref)

        page_ir.warnings.append(
            f"Duplicate refs within page resolved by renaming (kept first occurrence): {sorted(dupes)[:20]}"
        )

    # Rebuild after potential renames.
    element_lists = (
        page_ir.nodes
        + page_ir.statements
        + page_ir.relationships
        + page_ir.tables
        + page_ir.diagrams
        + page_ir.curriculum_elements
    )
    local_refs = {e.ref for e in element_lists if getattr(e, "ref", None)}

    # If it already looks namespaced, do nothing (prevents double-prefixing).
    if local_refs and all(r.startswith(prefix) for r in local_refs):
        return page_ir

    # Namespace refs.
    ref_map = {r: f"{prefix}{r}" for r in local_refs}

    def remap_if_local(r: Optional[str]) -> Optional[str]:
        """Remap a ref if it is local to this page.

        Parameters
        ----------
        r
            The ref to remap.

        Returns
        -------
        Optional[str]
            The remapped ref if local, else the original ref.
        """

        return ref_map.get(r, r)

    # Remap paths
    for e in element_lists:
        if hasattr(e, "path") and getattr(e, "path", None):
            e.path = [remap_if_local(r) for r in e.path]

    # Remap refs + parent refs
    for e in element_lists:
        e.ref = ref_map.get(e.ref, e.ref)
        if hasattr(e, "parent_ref"):
            e.parent_ref = remap_if_local(getattr(e, "parent_ref"))

    # Remap relationship endpoints
    for rel in page_ir.relationships:
        rel.source_ref = remap_if_local(rel.source_ref)
        rel.target_ref = remap_if_local(rel.target_ref)

    # Remap provenance.table_ref
    for e in element_lists:
        for p in getattr(e, "provenance", []):
            if getattr(p, "table_ref", None) in ref_map:
                p.table_ref = ref_map[p.table_ref]

    # Remap provenance.table_ref in relationship evidence
    for rel in page_ir.relationships:
        for ev in getattr(rel, "evidence", []) or []:
            for p in getattr(ev, "provenance", []) or []:
                if getattr(p, "table_ref", None) in ref_map:
                    p.table_ref = ref_map[p.table_ref]

    return page_ir


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
    return ContinuityState(**json.loads(fp.read_text("utf-8")))


def make_schema_strict(  # pylint:disable=too-complex
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Recursively enforce `additionalProperties: false` for object schemas, without
    clobbering schemas that already specify additionalProperties (e.g., dict/map fields
    that intentionally allow arbitrary keys).

    This function prevents the extractor from inventing arbitrary fields not defined in
    the schema and forces the extraction to stay within the expected IR shape so that
    downstream components can rely on a stable contract.

    Parameters
    ----------
    schema
        The JSON schema to make strict.

    Returns
    -------
    dict[str, Any]
        The strict JSON schema.
    """

    def _walk(node: Any) -> Any:
        """Recursively walk the schema node and enforce strictness.

        Parameters
        ----------
        node
            The current schema node.

        Returns
        -------
        Any
            The processed schema node.
        """

        if isinstance(node, list):
            return [_walk(x) for x in node]

        if not isinstance(node, dict):
            return node

        # Recurse into common schema containers.
        for key in ("properties", "$defs", "definitions"):
            if key in node and isinstance(node[key], dict):
                node[key] = {k: _walk(v) for k, v in node[key].items()}

        for key in ("items", "additionalProperties"):
            if key in node:
                node[key] = _walk(node[key])

        for key in ("anyOf", "oneOf", "allOf"):
            if key in node and isinstance(node[key], list):
                node[key] = [_walk(x) for x in node[key]]

        # Enforce strictness for objects when not explicitly set.
        is_object = node.get("type") == "object" or "properties" in node
        if is_object and "additionalProperties" not in node:
            node["additionalProperties"] = False

        return node

    # Work on a shallow copy; nested dicts are rewritten by _walk anyway.
    return _walk(dict(schema))


def merge_pages_to_document_ir(
    *,
    doc_key: str,
    extraction_run: Optional[ExtractionRunIR] = None,
    metadata: DocumentMetadataIR,
    pages: list[PageIR],
    pdf_name: str,
) -> DocumentIR:
    """Merge PageIRs into DocumentIR.

    Parameters
    ----------
    doc_key
        The document key.
    extraction_run
        The extraction run metadata.
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

    # Merge all ElementContainerIR lists.
    curriculum_elements: list[Any] = []
    diagrams: list[Any] = []
    nodes: list[Any] = []
    relationships: list[Any] = []
    statements: list[Any] = []
    tables: list[Any] = []

    for p in pages_sorted:
        curriculum_elements.extend(p.curriculum_elements)
        diagrams.extend(p.diagrams)
        nodes.extend(p.nodes)
        relationships.extend(p.relationships)
        statements.extend(p.statements)
        tables.extend(p.tables)

    # Stable, de-duped roots.
    seen: set[str] = set()
    root_node_refs: list[str] = []
    for n in nodes:
        if n.parent_ref is None and n.ref not in seen:
            root_node_refs.append(n.ref)
            seen.add(n.ref)

    return DocumentIR(
        curriculum_elements=curriculum_elements,
        diagrams=diagrams,
        doc_key=doc_key,
        extraction_run=extraction_run,
        metadata=metadata,
        nodes=nodes,
        pages=pages_sorted,
        pdf_name=pdf_name,
        relationships=relationships,
        root_node_refs=root_node_refs,
        schema_version="0.1",
        statements=statements,
        tables=tables,
    )


def normalize_provenance(
    *,
    doc_key: str,
    extraction_method: str,
    page_dimensions: Optional[tuple[float, float]] = None,
    page_index: int,
    page_ir: PageIR,
    pdf_name: str,
) -> PageIR:
    """Ensure every element has provenance and force doc-identity truth fields."""

    base_ptr = ProvenancePointer(
        bbox=None,
        bbox_kind=BBoxKind.UNKNOWN,
        doc_key=doc_key,
        extraction_method=extraction_method,
        page_dimensions=page_dimensions,
        page_index=page_index,
        pdf_name=pdf_name,
        section=None,
    )

    def _ensure_and_patch(
        ptrs: Optional[list[ProvenancePointer]],
    ) -> list[ProvenancePointer]:
        """Ensure provenance pointers exist and patch truth fields.

        Parameters
        ----------
        ptrs
            The existing provenance pointers.

        Returns
        -------
        list[ProvenancePointer]
            The ensured and patched provenance pointers.
        """

        if not ptrs:
            ptrs = [base_ptr.model_copy(deep=True)]  # Avoid shared instance

        for ptr in ptrs:
            # Force doc identity fields (don’t trust the LLM).
            ptr.doc_key = doc_key
            ptr.pdf_name = pdf_name
            ptr.page_index = page_index
            ptr.extraction_method = extraction_method

            # Fill if missing.
            if page_dimensions is not None:
                ptr.page_dimensions = page_dimensions
            if getattr(ptr, "bbox_kind", None) is None:
                ptr.bbox_kind = BBoxKind.UNKNOWN

        return ptrs

    # Patch provenance on all element lists in the page container.
    for col_name in (
        "curriculum_elements",
        "diagrams",
        "nodes",
        "statements",
        "tables",
    ):
        col = getattr(page_ir, col_name, None) or []
        for el in col:
            el.provenance = _ensure_and_patch(getattr(el, "provenance", None))

    # Patch relationships and their evidence provenance too.
    for rel in page_ir.relationships or []:
        rel.provenance = _ensure_and_patch(getattr(rel, "provenance", None))
        for ev in getattr(rel, "evidence", None) or []:
            ev.provenance = _ensure_and_patch(getattr(ev, "provenance", None))

    return page_ir


def page_ir_json_schema(strict: bool = True) -> dict[str, Any]:
    """Get the JSON schema for the PageIR model.

    Parameters
    ----------
    strict
        Whether to enforce strictness (i.e., no additional properties).

    Returns
    -------
    dict[str, Any]
        The JSON schema for the PageIR model.
    """

    schema = PageIR.model_json_schema()
    return make_schema_strict(schema) if strict else schema


def save_continuity_state(
    extraction_dirs: ExtractionDirs, state: ContinuityState
) -> None:
    """Save the continuity state to disk.

    Parameters
    ----------
    extraction_dirs
        The extraction directories.
    state
        The continuity state to save.
    """

    write_text(continuity_fp(extraction_dirs), json.dumps(asdict(state), indent=2))
