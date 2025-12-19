"""This module handles the aggregation of pages into a final DocumentIR and the
normalization of provenance data.
"""

# pylint: disable=R1260,R0915,R0912
# Standard Library
import json

from collections import Counter, defaultdict
from typing import Any, Optional

# Package Library
from skg.ir.schemas import (
    DocumentIR,
    DocumentMetadataIR,
    ExtractionRunIR,
    KeyValuePair,
    PageIR,
    ProvenancePointer,
)
from skg.utils.constants import BBoxKind, HierarchyNodeType


def _add_tag(el: Any, tag: str) -> None:
    """Add a tag to an element's tags list if not already present.

    Parameters
    ----------
    el
        The element to which the tag should be added.
    tag
        The tag to add.
    """

    tags = getattr(el, "tags", None)
    if isinstance(tags, list) and tag not in tags:
        tags.append(tag)


def _add_kv(el: Any, key: str, value: str) -> None:
    """Add a key-value pair to an element's extra list if not already present.

    Parameters
    ----------
    el
        The element to which the key-value pair should be added.
    key
        The key of the key-value pair.
    value
        The value of the key-value pair.
    """

    extra = getattr(el, "extra", None)
    if not isinstance(extra, list):
        return

    # Avoid duplicating identical breadcrumbs.
    for kv in extra:
        if getattr(kv, "key", None) == key and getattr(kv, "value", None) == value:
            return
    extra.append(KeyValuePair(key=key, value=value))


def _dedupe_by_json(items: list[Any]) -> list[Any]:
    """Deduplicate a list of (possibly Pydantic) objects by stable JSON dump.

    Parameters
    ----------
    items
        The list of items to deduplicate.

    Returns
    -------
    list[Any]
        The deduplicated list of items.
    """

    seen: set[str] = set()
    out: list[Any] = []
    for it in items or []:
        k = _stable_json(it)
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def _dedupe_relationships(relationships: list[Any]) -> list[Any]:
    """Deduplicate semantically identical relationships created across pages/retries.
    Keyed by: (rel_type, source_ref, target_ref, inference_type, is_inferred)

    NOTE: Because `is_inferred` is part of the key, explicit and inferred relationships
    are kept as separate records if they otherwise match; deduplication only merges
    relationships within the same `is_inferred` bucket.

    Parameters
    ----------
    relationships
        The list of RelationshipIR objects to deduplicate.

    Returns
    -------
    list[Any]
        The deduplicated list of RelationshipIR objects.
    """

    groups: dict[tuple[str, str, str, str, bool], list[Any]] = defaultdict(list)

    for r in relationships or []:
        key = (
            str(getattr(r, "rel_type", "")),
            str(getattr(r, "source_ref", "")),
            str(getattr(r, "target_ref", "")),
            str(getattr(r, "inference_type", "") or ""),
            bool(getattr(r, "is_inferred", False)),
        )
        groups[key].append(r)

    merged: list[Any] = []

    for key, rels in groups.items():
        if len(rels) == 1:
            merged.append(rels[0])
            continue

        def rank(rel: Any) -> tuple[float, int, str]:
            """Ranking function to select the primary relationship among duplicates.

            Parameters
            ----------
            rel
                The relationship to rank.

            Returns
            -------
            tuple[float, int, str]
                The ranking key: (-confidence, -evidence_count, ref).
            """

            # NOTE: explicit and inferred relationships are intentionally kept separate
            # (dedupe key includes is_inferred). We rank only within the same bucket.
            conf = getattr(rel, "confidence", None)
            conf_val = float(conf) if conf is not None else -1.0
            ev_count = len(getattr(rel, "evidence", None) or [])

            return -conf_val, -ev_count, str(getattr(rel, "ref", ""))

        primary = sorted(rels, key=rank)[0]

        for other in rels:
            if other is primary:
                continue

            # Confidence: keep max.
            oc = getattr(other, "confidence", None)
            if oc is not None:
                pc = getattr(primary, "confidence", None)
                primary.confidence = oc if pc is None else max(pc, oc)

            # Merge provenance/evidence/extra (deduped).
            primary.provenance = _dedupe_by_json(
                (getattr(primary, "provenance", None) or [])
                + (getattr(other, "provenance", None) or [])
            )
            primary.evidence = _dedupe_by_json(
                (getattr(primary, "evidence", None) or [])
                + (getattr(other, "evidence", None) or [])
            )
            primary.extra = _dedupe_by_json(
                (getattr(primary, "extra", None) or [])
                + (getattr(other, "extra", None) or [])
            )

        merged.append(primary)

    # Ensure deterministic output ordering.
    merged.sort(
        key=lambda r: (
            str(getattr(r, "rel_type", "")),
            str(getattr(r, "source_ref", "")),
            str(getattr(r, "target_ref", "")),
            str(getattr(r, "inference_type", "") or ""),
            bool(getattr(r, "is_inferred", False)),
            str(getattr(r, "ref", "")),
        )
    )
    return merged


def _sanitize_bbox(
    *,
    image_dimensions: Optional[list[int]],
    page_dimensions: Optional[list[float]],
    ptr: ProvenancePointer,
) -> None:
    """Sanitize and infer the kind of a ProvenancePointer's bounding box.

    Parameters
    ----------
    image_dimensions
        The (width, height) of the page image in pixels, if available.
    page_dimensions
        The (width, height) of the PDF page in points, if available.
    ptr
        The ProvenancePointer to sanitize.
    """

    bbox = getattr(ptr, "bbox", None)
    if not bbox or not (isinstance(bbox, list) and len(bbox) == 4):
        return

    x0, y0, x1, y1 = bbox
    bbox_to_set = bbox
    kind_to_set: Optional[BBoxKind] = None

    # Drop obvious placeholders.
    if all(v == 0 for v in bbox):
        bbox_to_set = None
        kind_to_set = BBoxKind.UNKNOWN

    # Drop invalid geometry.
    elif x1 <= x0 or y1 <= y0:
        bbox_to_set = None
        kind_to_set = BBoxKind.UNKNOWN

    # Infer kind if unknown/unset.
    elif getattr(ptr, "bbox_kind", None) in (None, BBoxKind.UNKNOWN):

        # 1. Try PDF points (highest priority).
        if page_dimensions is not None and (
            0 <= x0 <= page_dimensions[0]
            and 0 <= x1 <= page_dimensions[0]
            and 0 <= y0 <= page_dimensions[1]
            and 0 <= y1 <= page_dimensions[1]
        ):
            kind_to_set = BBoxKind.PDF_POINTS

        # 2. Try image pixels (only if PDF check failed).
        elif image_dimensions is not None and (
            0 <= x0 <= image_dimensions[0]
            and 0 <= x1 <= image_dimensions[0]
            and 0 <= y0 <= image_dimensions[1]
            and 0 <= y1 <= image_dimensions[1]
        ):
            kind_to_set = BBoxKind.IMAGE_PIXELS

        # 3. Fallback: Neither matched --> invalid.
        else:
            bbox_to_set = None
            kind_to_set = BBoxKind.UNKNOWN

    # Apply updates.
    if bbox_to_set is None:
        ptr.bbox = None
    if kind_to_set is not None:
        ptr.bbox_kind = kind_to_set


def _stable_dump(obj: Any) -> Any:
    """Best-effort stable serialization for Pydantic models and plain objects.

    Parameters
    ----------
    obj
        The object to serialize.

    Returns
    -------
    Any
        The stable-serialized object.
    """

    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def _stable_json(obj: Any) -> str:
    """Get a stable JSON representation of an object for deduplication.

    Parameters
    ----------
    obj
        The object to serialize.

    Returns
    -------
    str
        The stable JSON string.
    """

    return json.dumps(
        _stable_dump(obj), sort_keys=True, ensure_ascii=False, default=str
    )


def enforce_node_parent_path_invariants(nodes: list[Any]) -> None:
    """Enforce invariants between `HierarchyNodeIR.parent_ref` and
    `HierarchyNodeIR.path`.

    Enforce:
      - path[-1] == ref
      - if len(path) > 1, then parent_ref == path[-2]
      - if len(path) == 1, then parent_ref is None

    Plus: sanitize missing parents + break parent-pointer cycles.

    Parameters
    ----------
    nodes
        The list of HierarchyNodeIR objects to process.
    """

    node_by_ref: dict[str, Any] = {
        getattr(n, "ref"): n for n in nodes if getattr(n, "ref", None)
    }

    # 1. Sanitize parent_ref (missing parent, self-parent, blank).
    for n in nodes:
        ref = getattr(n, "ref", None)
        if not ref:
            continue

        pr = getattr(n, "parent_ref", None)
        if isinstance(pr, str) and not pr.strip():
            n.parent_ref = None
            _add_tag(n, "parent_sanitized:blank")
            _add_kv(n, "parent_sanitized_reason", "blank_parent_ref")

        pr = getattr(n, "parent_ref", None)
        if pr == ref:
            n.parent_ref = None
            _add_tag(n, "parent_sanitized:self_parent")
            _add_kv(n, "parent_sanitized_reason", "self_parent_ref")

        pr = getattr(n, "parent_ref", None)
        if pr and pr not in node_by_ref:
            n.parent_ref = None
            _add_tag(n, "parent_sanitized:missing_parent")
            _add_kv(n, "parent_sanitized_reason", "parent_ref_missing_in_nodes")
            _add_kv(n, "parent_sanitized_missing_parent_ref", str(pr))

    # 2. Break cycles in parent pointers (forest required for deterministic paths).
    state: dict[str, int] = {}  # 0=unvisited, 1=visiting, 2=done

    def visit(ref: str) -> None:
        """Depth-first visit to detect and break cycles in parent pointers.

        Parameters
        ----------
        ref
            The reference of the node to visit.
        """

        st = state.get(ref, 0)
        if st == 2:
            return
        if st == 1:
            # Cycle detected: break at this node.
            n = node_by_ref[ref]
            old_parent = getattr(n, "parent_ref", None)
            n.parent_ref = None
            _add_tag(n, "parent_sanitized:cycle_break")
            _add_kv(n, "parent_sanitized_reason", "cycle_break")
            if old_parent:
                _add_kv(n, "parent_sanitized_old_parent_ref", str(old_parent))
            state[ref] = 2
            return

        state[ref] = 1
        n = node_by_ref[ref]
        pr = getattr(n, "parent_ref", None)
        if pr and pr in node_by_ref:
            visit(pr)
        state[ref] = 2

    for ref in list(node_by_ref.keys()):
        visit(ref)

    # 3. Rebuild all node paths from parent pointers (single source of truth).
    memo: dict[str, list[str]] = {}

    def build_path(ref: str) -> list[str]:
        """Recursively build the path for a node by following parent_ref pointers.

        Parameters
        ----------
        ref
            The reference of the node to build the path for.

        Returns
        -------
        list[str]
            The built path for the node.
        """

        if ref in memo:
            return memo[ref]

        n = node_by_ref[ref]
        old_path = list(getattr(n, "path", None) or [])

        pr = getattr(n, "parent_ref", None)
        if pr and pr in node_by_ref:
            parent_path = build_path(pr)
            new_path = parent_path + [ref]
        else:
            new_path = [ref]

        # Set and enforce invariants.
        n.path = new_path
        if len(new_path) > 1:
            if getattr(n, "parent_ref", None) != new_path[-2]:
                old_parent = getattr(n, "parent_ref", None)
                n.parent_ref = new_path[-2]
                _add_tag(n, "parent_sanitized:path_mismatch_fix")
                _add_kv(n, "parent_sanitized_reason", "parent_ref_path_mismatch_fix")
                if old_parent:
                    _add_kv(n, "parent_sanitized_old_parent_ref", str(old_parent))
        elif getattr(n, "parent_ref", None) is not None:
            old_parent = getattr(n, "parent_ref", None)
            n.parent_ref = None
            _add_tag(n, "parent_sanitized:single_path_forced_root")
            _add_kv(
                n,
                "parent_sanitized_reason",
                "forced_parent_none_for_singleton_path",
            )
            if old_parent:
                _add_kv(n, "parent_sanitized_old_parent_ref", str(old_parent))

        if old_path and old_path != new_path:
            _add_tag(n, "path_rebuilt")
            _add_kv(n, "path_rebuild_old", " > ".join(old_path))
            _add_kv(n, "path_rebuild_new", " > ".join(new_path))

        memo[ref] = new_path
        return new_path

    for ref in list(node_by_ref.keys()):
        build_path(ref)


def merge_pages_to_document_ir(
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
    pages_sorted = sorted(pages, key=lambda p: p.page_index)

    # Merge all ElementContainerIR lists.
    curriculum_elements: list[Any] = []
    diagrams: list[Any] = []
    nodes: list[Any] = []
    relationships: list[Any] = []
    statements: list[Any] = []
    tables: list[Any] = []

    for p in pages_sorted:
        curriculum_elements.extend(p.curriculum_elements or [])
        diagrams.extend(p.diagrams or [])
        nodes.extend(p.nodes or [])
        relationships.extend(p.relationships or [])
        statements.extend(p.statements or [])
        tables.extend(p.tables or [])

    # 1. Normalize Parent Refs (empty string -> None).
    for el in nodes + statements + curriculum_elements + tables + diagrams:
        p = getattr(el, "parent_ref", None)
        if isinstance(p, str) and not p.strip():
            el.parent_ref = None

    # 2. Repair Node Parents (using path).
    node_refs = {n.ref for n in nodes}
    for n in nodes:
        if n.parent_ref is None:
            path = n.path or []
            if len(path) >= 2:
                implied_parent = path[-2]
                if implied_parent in node_refs and implied_parent != n.ref:
                    n.parent_ref = implied_parent

    # Enforce strong invariants between parent_ref and path.
    enforce_node_parent_path_invariants(nodes)

    # 3. Detect Roots (nodes with no parents).
    node_child_counts: dict[str, int] = Counter()
    for n in nodes:
        if n.parent_ref:
            node_child_counts[n.parent_ref] += 1

    payload_counts: dict[str, int] = Counter()
    stmt_by_ref = {s.ref: s for s in statements}

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

        seen = set()
        r = start_ref
        while r and r not in node_refs and r not in seen:
            seen.add(r)
            s = stmt_by_ref.get(r)
            if s is None:
                break
            r = s.parent_ref
        return r if r in node_refs else None

    for el in statements + curriculum_elements + tables + diagrams:
        nref = _nearest_node_ancestor(getattr(el, "parent_ref", None))
        if nref:
            payload_counts[nref] += 1

    valid_empty_root_types = {
        HierarchyNodeType.COMPETENCY_AREA,
        HierarchyNodeType.DOMAIN,
        HierarchyNodeType.GRADE,
        HierarchyNodeType.LEARNING_AREA,
        HierarchyNodeType.MODULE,
        HierarchyNodeType.QUARTER,
        HierarchyNodeType.SEMESTER,
        HierarchyNodeType.STAGE,
        HierarchyNodeType.STRAND,
        HierarchyNodeType.SUBDOMAIN,
        HierarchyNodeType.SUBJECT,
        HierarchyNodeType.SUBTHEME,
        HierarchyNodeType.TERM,
        HierarchyNodeType.THEME,
        HierarchyNodeType.UNIT,
        HierarchyNodeType.WEEK,
    }

    root_node_refs = []
    seen_roots = set()

    for n in nodes:
        if (
            getattr(n, "node_type") == HierarchyNodeType.OTHER
            and getattr(n, "node_type_other", None) == "front_matter_heading"
        ):
            continue
        if n.parent_ref is None and n.ref not in seen_roots:
            has_children = node_child_counts.get(n.ref, 0) > 0
            has_payload = payload_counts.get(n.ref, 0) > 0
            is_high_level = n.node_type in valid_empty_root_types

            if has_children or has_payload or is_high_level:
                root_node_refs.append(n.ref)
                seen_roots.add(n.ref)

    excluded_parentless: list[str] = []
    for n in nodes:
        if (
            getattr(n, "node_type") == HierarchyNodeType.OTHER
            and getattr(n, "node_type_other", None) == "front_matter_heading"
        ):
            continue
        if n.parent_ref is None and n.ref not in seen_roots:
            excluded_parentless.append(n.ref)

    # Fallback: keep all parentless nodes if logic filtered everything out.
    if not root_node_refs:
        for n in nodes:
            if n.parent_ref is None and n.ref not in seen_roots:
                root_node_refs.append(n.ref)
                seen_roots.add(n.ref)

        # If fallback ran, we *didn't* exclude parentless nodes after all.
        excluded_parentless = []

    if excluded_parentless:
        examples = ", ".join(excluded_parentless[:10])
        metadata.extra.append(
            KeyValuePair(
                key="aggregation_warning:root_node_refs_filtered",
                value=(
                    f"[aggregation] excluded {len(excluded_parentless)} parentless nodes from root_node_refs "
                    f"(no children/payload and not a known high-level root type). examples=[{examples}]"
                ),
            )
        )

    # Deduplicate repeated edges created across pages/retries.
    relationships = _dedupe_relationships(relationships)

    # WARNING: Element refs are only guaranteed unique within this DocumentIR.
    # Downstream exporters must include doc_key in deterministic global IDs.
    if metadata is not None and not any(
        getattr(kv, "key", None) == "aggregation_warning:ref_scope"
        for kv in (metadata.extra or [])
    ):
        metadata.extra.append(
            KeyValuePair(
                key="aggregation_warning:ref_scope",
                value=(
                    "[aggregation] element `ref` values are document-scoped (not globally unique across PDFs). "
                    "Use doc_key + ref (or UUIDv5(doc_key, ...)) for global identifiers."
                ),
            )
        )

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
    image_dimensions: Optional[list[int]] = None,
    page_dimensions: Optional[list[float]] = None,
    page_index: int,
    page_ir: PageIR,
    pdf_name: str,
    render_dpi: Optional[int] = None,
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
    image_dimensions
        The (width, height) of the rendered page image in pixels. Used to give context
        to bounding boxes.
    page_dimensions
        The (width, height) of the PDF page in points. Used to give context to
        bounding boxes.
    page_index
        The 0-based index of the current page being processed.
    page_ir
        The raw extraction output from the LLM.
    pdf_name
        The filename of the source PDF.
    render_dpi
        The DPI used when rendering the page image, if applicable.

    Returns
    -------
    PageIR
        The mutated PageIR object where every element has at least one valid,
        truth-verified provenance pointer.
    """

    base_ptr = ProvenancePointer(
        bbox=None,
        bbox_kind=BBoxKind.UNKNOWN,
        doc_key=doc_key,
        extraction_method=extraction_method,
        page_dimensions=page_dimensions,
        image_dimensions=image_dimensions,
        render_dpi=render_dpi,
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
            ptrs = [base_ptr.model_copy(deep=True)]

        # Patch existing provenance. If the LLM provided a pointer (e.g., it gave us a
        # specific bbox or quote), we keep that specific data but overwrite the
        # metadata fields to ensure correctness.
        for ptr in ptrs:
            # Force doc identity fields (don’t trust the LLM).
            ptr.doc_key = doc_key
            ptr.pdf_name = pdf_name
            ptr.page_index = page_index

            # Preserve pointer-level extraction_method when it exists (e.g. table-row
            # promotion), but ensure it's populated for pointers missing it.
            if getattr(ptr, "extraction_method", None) in (None, "", "unknown"):
                ptr.extraction_method = extraction_method

            # Fill if missing.
            if page_dimensions is not None:
                ptr.page_dimensions = page_dimensions
            if (
                image_dimensions is not None
                and getattr(ptr, "image_dimensions", None) is None
            ):
                ptr.image_dimensions = image_dimensions
            if render_dpi is not None and getattr(ptr, "render_dpi", None) is None:
                ptr.render_dpi = render_dpi
            if getattr(ptr, "bbox_kind", None) is None:
                ptr.bbox_kind = BBoxKind.UNKNOWN
            _sanitize_bbox(
                image_dimensions=image_dimensions,
                page_dimensions=page_dimensions,
                ptr=ptr,
            )

        return ptrs

    # Patch provenance on all primary element lists in the page container.
    for col_name in [
        "curriculum_elements",
        "diagrams",
        "nodes",
        "statements",
        "tables",
    ]:
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
