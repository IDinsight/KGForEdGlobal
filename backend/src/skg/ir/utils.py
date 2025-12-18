"""This module contains utility functions for Intermediate Representation (IR) package."""

# Standard Library
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

# Package Library
from skg.ir.schemas import (
    DocumentIR,
    DocumentMetadataIR,
    PageIR,
    ProvenancePointer,
    StatementIR,
)
from skg.schemas import ExtractionRunIR
from skg.utils.constants import BBoxKind, HierarchyNodeType
from skg.utils.general import make_dir, open_json_type, write_to_json


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
    """Stitches the current page's semantic graph to the context of the previous page.

    This handles two main scenarios caused by page breaks:

    1. **Explicit Links:** An element on Page N refers to a parent/source on Page N-1
       by its raw ID (e.g. "n10"). This function remaps "n10" to the globally
       namespaced ID from the previous page (e.g. "p0004:n10").
    2. **Implicit Continuations (Orphans):** An element starts at the top of Page N
       (e.g., a bullet point) belonging to a section header that appeared on Page N-1.
       This function attaches it to the `active_parent_ref` recorded at the end of
       Page N-1.

    Parameters
    ----------
    page_ir
        The current page's IR. Its local IDs must already be namespaced (e.g.,
        "p0005:n1") by `ensure_namespace_page_refs`.
    prev
        The state object returned by processing the *previous* page. Contains mappings
        for the previous page's IDs and the ID of the last open node.

    Returns
    -------
    PageIR
        The modified PageIR with `parent_ref`, `path`, `source_ref`, and `target_ref`
        updated to connect across the page boundary.
    """

    def remap_raw(r: Optional[str]) -> Optional[str]:
        """Look up a raw reference (e.g., "n10") in the previous page's ID map. If
        found, return the global ID ("p0004:n10"). If 'r' is already namespaced or not
        found, return as is.

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

        # If it contains ':', it is likely already namespaced (e.g. "p0005:n1"). We
        # assume raw local refs from the LLM do not contain colons.
        if ":" in r:
            return r

        # Try to resolve against the previous page's map.
        return prev.recent_raw_ref_map.get(r, r)

    def patch_parent(el: Any) -> None:
        """Update the hierarchy pointers for a single element.

        Parameters
        ----------
        el
            The element to patch.
        """

        # Fix explicit links. If the model explicitly cited a parent ID ("n10") that
        # belongs to the previous page, update it to the global ID ("p0004:n10").
        parent_ref = getattr(el, "parent_ref", None)
        parent_ref2 = remap_raw(parent_ref)

        # If it was unqualified and we could map it, patch it.
        if parent_ref2 != parent_ref:
            el.parent_ref = parent_ref2

        # Fix implicit continuations (orphans). If the model marked this as
        # 'is_continuation' AND it has no parent (because the header is on the previous
        # page), attach it to the active parent from the previous context.
        if getattr(el, "is_continuation", False):
            if not getattr(el, "parent_ref", None) and prev.active_parent_ref:
                el.parent_ref = prev.active_parent_ref

        # Fix ancestor path. If the element has a path list (e.g. ["n1", "n10"]),
        # iterate through it and remap any raw IDs that refer to the previous page.
        if hasattr(el, "path") and getattr(el, "path", None):
            el.path = [remap_raw(x) for x in el.path]

    def patch_table_refs(ptrs: Any) -> None:
        """Patch table references in a list of provenance pointers.

        Parameters
        ----------
        ptrs
            The provenance pointers to patch.
        """

        for p in ptrs or []:
            if getattr(p, "table_ref", None):
                p.table_ref = remap_raw(p.table_ref)

    # Patch structural elements (Nodes, Statements, etc.).
    for col_name in (
        "curriculum_elements",
        "diagrams",
        "nodes",
        "statements",
        "tables",
    ):
        for el in getattr(page_ir, col_name, None) or []:
            patch_parent(el)
            patch_table_refs(getattr(el, "provenance", None))

    # Patch relationships (edges). Relationships connect two nodes (source --> target).
    # Both endpoints might refer to the previous page.
    for rel in page_ir.relationships or []:
        rel.source_ref = remap_raw(rel.source_ref)
        rel.target_ref = remap_raw(rel.target_ref)

        # Relationships can also have parents (e.g., scoped inside a Unit), so we patch
        # the relationship itself too.
        patch_parent(rel)

        patch_table_refs(getattr(rel, "provenance", None))
        for ev in rel.evidence or []:
            patch_table_refs(getattr(ev, "provenance", None))

    return page_ir


def build_continuity_state_from_page(
    page_ir: PageIR, prev: ContinuityState
) -> ContinuityState:
    """Analyzes the current page to determine the context state required for the next
    page's processing.

    It employs a "Deepest Node Wins" heuristic with a fallback:

    1. If this page defines new HierarchyNodes, the deepest one becomes the new active
        parent.
    2. If this page has NO new nodes (e.g., it is just a wall of bullet points), we
        propagate the `active_parent_ref` from the previous page. This prevents the
        continuity chain from breaking on long lists that span multiple pages.

    Parameters
    ----------
    page_ir
        The processed IR for the current page.
    prev
        The state from the previous page (used for fallback propagation).

    Returns
    -------
    ContinuityState
        The state object containing the active parent reference and the ID lookup map
        for the next page.
    """

    nodes = page_ir.nodes or []
    if nodes:
        # Scenario A: This page has structure --> deepest node wins.
        best = max(nodes, key=lambda n: len(getattr(n, "path", None) or []))
        active_parent_ref = best.ref
        active_path = list(getattr(best, "path", None) or []) or [best.ref]
    else:
        # Scenario B: This page is only content (Statements/Tables). The fallback is to
        # propagate the context from the previous page.
        active_parent_ref = prev.active_parent_ref
        active_path = prev.active_path

    # Build a reference map. We map THIS page's raw IDs. We do NOT propagate the
    # previous page's map because local raw IDs (like "n1") might be reused on the next
    # page.
    recent_raw_ref_map: dict[str, str] = {}

    all_elements = (
        nodes
        + (page_ir.statements or [])
        + (page_ir.tables or [])
        + (page_ir.diagrams or [])
        + (page_ir.curriculum_elements or [])
        + (page_ir.relationships or [])
    )

    for el in all_elements:
        r = getattr(el, "ref", None)
        if r and ":" in r:
            raw_suffix = r.split(":")[-1]
            recent_raw_ref_map[raw_suffix] = r

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


def ensure_namespace_page_refs(  # pylint: disable=too-complex, too-many-branches
    *, page_ir: PageIR, prefix: str
) -> PageIR:
    """Guarantees global uniqueness of IDs across the document by namespacing all
    references on a specific page.

    This function performs a two-pass sanitization process:

    1. **Intra-page De-duplication:** Detects if the LLM reused an ID *within* the same
        page (e.g., two distinct nodes both claimed to be "n1"). It resolves this by
        appending a suffix (e.g., "n1__n1") to subsequent occurrences, ensuring valid
        internal graph topology before namespacing.
    2. **Global Namespacing:** Prefixes every local ID with the provided `prefix`
        (e.g., "n1" --> "p0005:n1"). It then recursively updates all pointers (parents,
        paths, relationships, provenance) to match these new IDs.

    This prevents data loss during the final merge of PageIRs into DocumentIR, where
    duplicate IDs would otherwise cause validation failures or silent overwrites.

    Parameters
    ----------
    page_ir
        The extraction results for a single page containing raw, model-generated IDs.
    prefix
        The string to prepend to all IDs (e.g., "p0005:"). This usually corresponds to
        the page index to ensure document-wide uniqueness.

    Returns
    -------
    PageIR
        The mutated PageIR object where all `ref`, `parent_ref`, `source_ref`,
        `target_ref`, and `path` fields have been updated to use the namespaced IDs.
    """

    # Gather all addressable elements into generic buckets. The 'tag' is a short suffix
    # used for renaming collisions (n=node, s=statement, etc.)
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
        # We process 'element_lists' to iterate over everything, but we modify the
        # objects inside 'page_ir' lists in-place (they are the same objects).
        element_lists.extend(element_list)

    # De-dupe refs within this single page (e.g. The model hallucinated and output "n1"
    # twice for two different concepts).
    refs = [e.ref for e in element_lists if getattr(e, "ref", None)]
    counts = Counter(refs)
    dupes = [r for r, c in counts.items() if c > 1]
    if dupes:
        # Map ref --> list of (element_object, type_tag).
        occ: dict[str, list[tuple[Any, str]]] = defaultdict(list)
        for _, lst, tag in buckets:
            for el in lst:
                if getattr(el, "ref", None):
                    occ[el.ref].append((el, tag))
        existing = set(refs)

        # Resolve collisions by renaming 2nd, 3rd, etc. occurrences.
        for r in dupes:
            # Skip the first occurrence (index 0); it keeps the original ID "n1".
            # Rename index 1+ to "n1__n1", "n1__n2", etc.
            for i, (el, tag) in enumerate(occ[r][1:], start=1):
                new_ref = f"{r}__{tag}{i}"

                # Safety check: ensure new name doesn't accidentally hit an existing ID.
                while new_ref in existing:
                    new_ref += "_"

                el.ref = new_ref
                existing.add(new_ref)

        page_ir.warnings.append(
            f"Duplicate refs within page resolved by renaming (kept first occurrence): "
            f"{sorted(dupes)[:20]}"
        )

    # Re-collect elements (IDs might have changed above). We now build a map of
    # {old_local_id: new_global_id}.
    element_lists = (
        page_ir.nodes
        + page_ir.statements
        + page_ir.relationships
        + page_ir.tables
        + page_ir.diagrams
        + page_ir.curriculum_elements
    )
    local_refs = {e.ref for e in element_lists if getattr(e, "ref", None)}

    # If refs are already namespaced (resume run), skip to avoid double prefixing.
    if local_refs and all(r.startswith(prefix) for r in local_refs):
        return page_ir

    # Build the master map: "n1" --> "p0005:n1"
    ref_map = {r: (r if r.startswith(prefix) else f"{prefix}{r}") for r in local_refs}

    def remap_if_local(r: Optional[str]) -> Optional[str]:
        """Returns the namespaced ID if 'r' is in our map, otherwise returns 'r' as-is.

        Parameters
        ----------
        r
            The reference to remap.

        Returns
        -------
        Optional[str]
            The remapped reference if found, else the original reference.
        """

        return ref_map.get(r, r)

    # Update all pointers in the graph.

    # A. Update Path (ancestor chain). For example,
    # path=["n1", "n2"] --> ["p0005:n1", "p0005:n2"].
    for e in element_lists:
        if hasattr(e, "path") and getattr(e, "path", None):
            e.path = [remap_if_local(r) for r in e.path]

    # B. Update Identity and Hierarchy. For example,
    # ref="n3", parent_ref="n2" --> ref="p0005:n3", parent_ref="p0005:n2"
    for e in element_lists:
        e.ref = ref_map.get(e.ref, e.ref)
        if hasattr(e, "parent_ref"):
            e.parent_ref = remap_if_local(getattr(e, "parent_ref"))

    # C. Update Relationships (Graph Edges). For example,
    # source="n1", target="s5" --> source="p0005:n1", target="p0005:s5"
    for rel in page_ir.relationships:
        rel.source_ref = remap_if_local(rel.source_ref)
        rel.target_ref = remap_if_local(rel.target_ref)

    # D. Update Provenance Links (Evidence). For example,
    # If a statement says "I came from Table 1", and we renamed "Table 1" to
    # "p0005:Table 1", we must update the statement's pointer to match.
    for e in element_lists:
        for p in getattr(e, "provenance", []):
            if getattr(p, "table_ref", None) in ref_map:
                p.table_ref = ref_map[p.table_ref]

    # E. Update Relationship Evidence Provenance (deeply nested evidence pointers
    # within relationships).
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
    return (
        ContinuityState() if not fp.exists() else ContinuityState(**open_json_type(fp))
    )


def merge_pages_to_document_ir(  # pylint: disable=R1260,R0912,R0915
    *,
    doc_key: str,
    extraction_run: Optional[ExtractionRunIR] = None,
    metadata: DocumentMetadataIR,
    pages: list[PageIR],
    pdf_name: str,
) -> DocumentIR:
    """Merge per-page IR outputs into a single, validated DocumentIR.

    This function is the document-level “normalization boundary” between page-by-page
    extraction and downstream mapping/export. It performs four core tasks:

    1. Sort pages into a stable order.
    2. Concatenate all extracted element lists (nodes, statements, etc.).
    3. Normalize/repair `parent_ref` so roots are well-defined and validation passes.
    4. Compute `root_node_refs` using a conservative heuristic to avoid obvious orphan
        roots.

    Notes
    -----
    Parent reference normalization
        `StructuralElementIR.parent_ref` is Optional[str]. Empty/whitespace strings
        are *not* valid “no parent” markers and can break root validation, so we
        normalize them to None.

    Parent reference repair (lightweight)
        If a node has `parent_ref is None` but a non-empty `path`, we recover the
        immediate parent as `path[-2]` when that ref exists among merged nodes.
        This is safe because `GraphElementIR.path` is explicitly an ancestor ref list
        (including self).

    Statement nesting
        `StatementIR.parent_ref` may point to a HierarchyNodeIR *or another
        StatementIR* (nested bullets). Root payload detection climbs statement parents
        until it reaches a node (or stops).

    Root detection
        We traverse from `root_node_refs` to avoid guessing roots by
        `parent_ref is None` (which can be noisy due to orphans). Also, every root node
        ref must exist and must have `parent_ref is None`.

    Parameters
    ----------
    doc_key
        Deterministic identifier for the source PDF (e.g., sha256 hex digest). This is
        embedded in DocumentIR and used by provenance validators elsewhere.
    extraction_run
        Optional metadata about the extraction run (model, timestamp, config hash,
        etc.).
    metadata
        Document-level metadata (country, publisher, year, languages, grade range,
        etc.).
    pages
        Per-page extraction outputs to merge. Each PageIR contains partial lists of:
            - nodes (HierarchyNodeIR)
            - statements (StatementIR)
            - curriculum_elements (CurriculumElementIR)
            - tables (TableIR)
            - diagrams (DiagramIR)
            - relationships (RelationshipIR)
    pdf_name
        Source PDF filename (no path).

    Returns
    -------
    DocumentIR
        A single aggregated DocumentIR containing:
            - `pages` sorted by page_index
            - merged element lists
            - normalized/repaired parent pointers where safe
            - `root_node_refs` suitable as deterministic traversal entry points
    """

    # Ensure stable ordering regardless of how pages were accumulated.
    pages_sorted: list[PageIR] = sorted(pages, key=lambda p: p.page_index)

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

    def _normalize_parent_ref(el: Any) -> None:
        """Normalize empty/whitespace `parent_ref` to None. This prevents downstream
        root validation failures caused by `parent_ref=""` being treated as
        “has a parent” instead of “no parent”.

        Parameters
        ----------
        el
            The element to normalize.
        """

        p = getattr(el, "parent_ref", None)
        if isinstance(p, str) and not p.strip():
            el.parent_ref = None

    # Normalize parent_ref everywhere it can exist. Relationships don't have parent_ref
    # in the schema for now.
    for el in nodes + statements + curriculum_elements + tables + diagrams:
        _normalize_parent_ref(el)

    node_refs: set[str] = {n.ref for n in nodes}

    # If the model forgot node.parent_ref but did emit a path, recover the immediate
    # parent. Only apply when the implied parent exists as a node in this merged
    # document.
    for n in nodes:
        if n.parent_ref is None:
            path = n.path or []
            if len(path) >= 2:
                implied_parent = path[-2]
                if implied_parent in node_refs and implied_parent != n.ref:
                    n.parent_ref = implied_parent

    # Candidate roots are nodes with parent_ref is None. We keep a candidate root if:
    #   - it has child nodes (structural container), OR
    #   - it has payload attached somewhere in its local vicinity (statements/elements),
    #     OR
    #   - it's a recognized high-level container node_type (keep even if empty).
    #
    # We also include a safe fallback to avoid producing zero roots.
    # Count structural children: parent_node_ref --> number of child nodes.
    node_child_counts: Counter[str] = Counter()
    for n in nodes:
        if n.parent_ref:
            node_child_counts[n.parent_ref] += 1

    # Build statement lookup for nested-parent traversal.
    stmt_by_ref: dict[str, StatementIR] = {s.ref: s for s in statements}

    def _nearest_node_ancestor(start_ref: Optional[str]) -> Optional[str]:
        """Resolve a starting parent_ref to the nearest HierarchyNodeIR ancestor.

        - If start_ref is already a node ref, return it.
        - If start_ref is a statement ref, climb statement.parent_ref until:
            * we reach a node ref, or
            * we hit a missing ref / cycle, or
            * we run out of parents.

        Parameters
        ----------
        start_ref
            The starting parent_ref to resolve.

        Returns
        -------
        Optional[str]
            The nearest node ancestor ref if found, else None.
        """

        seen: set[str] = set()
        r = start_ref
        while r and r not in node_refs and r not in seen:
            seen.add(r)
            s = stmt_by_ref.get(r)
            if s is None:
                break
            r = s.parent_ref
        return r if r in node_refs else None

    # Count payload attached to nodes (directly or via statement nesting).
    payload_counts: Counter[str] = Counter()
    for el in statements + curriculum_elements + tables + diagrams:
        nref = _nearest_node_ancestor(getattr(el, "parent_ref", None))
        if nref:
            payload_counts[nref] += 1

    valid_empty_root_types: set[HierarchyNodeType] = {
        HierarchyNodeType.SUBJECT,
        HierarchyNodeType.LEARNING_AREA,
        HierarchyNodeType.GRADE,
        HierarchyNodeType.STAGE,
        HierarchyNodeType.DOMAIN,
        HierarchyNodeType.SUBDOMAIN,
        HierarchyNodeType.STRAND,
        HierarchyNodeType.THEME,
        HierarchyNodeType.SUBTHEME,
        HierarchyNodeType.MODULE,
        HierarchyNodeType.TERM,
        HierarchyNodeType.SEMESTER,
        HierarchyNodeType.QUARTER,
        HierarchyNodeType.UNIT,
        HierarchyNodeType.COMPETENCY_AREA,
        HierarchyNodeType.WEEK,
    }
    root_node_refs: list[str] = []
    seen_roots: set[str] = set()

    for n in nodes:
        if n.parent_ref is None and n.ref not in seen_roots:
            has_structure_children = node_child_counts.get(n.ref, 0) > 0
            has_payload = payload_counts.get(n.ref, 0) > 0
            is_high_level = n.node_type in valid_empty_root_types

            if has_structure_children or has_payload or is_high_level:
                root_node_refs.append(n.ref)
                seen_roots.add(n.ref)

    # Fallback: never emit an empty root list if there are any parentless nodes.
    if not root_node_refs:
        for n in nodes:
            if n.parent_ref is None and n.ref not in seen_roots:
                root_node_refs.append(n.ref)
                seen_roots.add(n.ref)

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
    page_dimensions: Optional[list[float]] = None,
    page_index: int,
    page_ir: PageIR,
    pdf_name: str,
) -> PageIR:
    """Enforces 'Ground Truth' metadata on all extracted elements by normalizing their
    provenance pointers.

    This function serves two critical purposes:

    1. **Completeness:** If the LLM omitted provenance for an element (common for
        simple nodes), this creates a default pointer indicating the element exists on
        the current page.
    2. **Trust and Sanitation:** It forcibly overwrites the identity fields (doc_key,
       pdf_name, page_index) of *existing* pointers. We do not trust the LLM to
       self-report which file or page it is reading. We use the runtime values to
       guarantee that downstream systems can reliably locate the source.

    It traverses all semantic elements (Nodes, Statements, Tables, Diagrams, Curriculum
    Elements) and relationship evidence.

    Parameters
    ----------
    doc_key
        The deterministic hash of the source document.
    extraction_method
        The identifier for the method used (e.g., 'vision+structured', 'text-only').
    page_dimensions
        The (width, height) of the PDF page in points. Used to give context to
        bounding boxes.
    page_index
        The 0-based index of the current page being processed.
    page_ir
        The raw extraction output from the LLM.
    pdf_name
        The filename of the source PDF.

    Returns
    -------
    PageIR
        The mutated PageIR object where every element has at least one valid,
        truth-verified provenance pointer.
    """

    # Define the 'Ground Truth' for this specific page context. This template is used
    # when an element has absolutely no provenance info.
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
        """Ensures a list of pointers exists, and forcibly patches truth fields on
        every pointer within it.

        Parameters
        ----------
        ptrs
            The existing provenance pointers.

        Returns
        -------
        list[ProvenancePointer]
            The ensured and patched provenance pointers.
        """

        # Handle missing provenance. If the list is None or empty, create a new list
        # containing our base template. NB: We must use .model_copy(deep=True) to
        # ensure we don't share the exact same object instance across 100 different
        # statements.
        if not ptrs:
            ptrs = [base_ptr.model_copy(deep=True)]  # Avoid shared instance

        # Patch existing provenance. If the LLM provided a pointer (e.g., it gave us a
        # specific bbox or quote), we keep that specific data but overwrite the
        # metadata fields to ensure correctness.
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

    # Patch provenance on all primary element lists in the page container.
    for col_name in (
        "curriculum_elements",
        "diagrams",
        "nodes",
        "statements",
        "tables",
    ):
        col = getattr(page_ir, col_name, None) or []
        for el in col:
            # We assign back to el.provenance to handle the case where it was None.
            el.provenance = _ensure_and_patch(getattr(el, "provenance", None))

    # Patch relationships and evidence provenance in the page container.
    for rel in page_ir.relationships or []:
        rel.provenance = _ensure_and_patch(getattr(rel, "provenance", None))

        # Patch the evidence supporting the relationship.
        for ev in getattr(rel, "evidence", None) or []:
            ev.provenance = _ensure_and_patch(getattr(ev, "provenance", None))

    return page_ir


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

    write_to_json(continuity_fp(extraction_dirs), asdict(state))
