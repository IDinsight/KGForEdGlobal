"""This module handles the sanitization and namespacing of Intermediate Representation
(IR) identifiers.

It ensures that:

1. Intra-page collisions (LLM hallucinations) are resolved.
2. Global IDs are deterministically namespaced (e.g. "n1" -> "p001:n1").
"""

# pylint:disable=R0911,R1260,R0912,R0915
# Standard Library
from collections import Counter, defaultdict
from typing import Any, Optional

# Package Library
from skg.ir.schemas import PageIR


def ensure_namespace_page_refs(
    *, on_duplicate: str = "raise", page_ir: PageIR, prefix: str
) -> PageIR:
    """Guarantees global uniqueness of IDs across the document by namespacing all
    references on a specific page.

    This function performs a two-pass sanitization process:

    1. **Intra-page De-duplication:** Detects if the LLM reused an ID *within* the same
        page (e.g., two distinct nodes both claimed to be "n1"). It resolves this by
        appending a suffix (e.g., "n1__n1") to subsequent occurrences.
    2. **Global Namespacing:** Prefixes every local ID with the provided `prefix`
        (e.g., "n1" --> "p0005:n1"). It recursively updates all pointers.

    Parameters
    ----------
    on_duplicate
        Strategy for handling duplicate IDs within the same page.
    page_ir
        The extraction results for a single page containing raw, model-generated IDs.
    prefix
        The string to prepend to all IDs (e.g., "p0005:").

    Returns
    -------
    PageIR
        The mutated PageIR object with globally unique IDs.

    Raises
    ------
    ValueError
        If duplicate IDs are found and `on_duplicate` is set to "raise".
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

    # Flatten list for iteration.
    all_elements: list[Any] = []
    for _, element_list, _ in buckets:
        all_elements.extend(element_list)

    # 1. De-dupe refs within this single page.
    refs = [e.ref for e in all_elements if getattr(e, "ref", None)]
    counts = Counter(refs)
    dupes = [r for r, c in counts.items() if c > 1]

    if dupes:
        msg = f"Duplicate refs within page: {sorted(dupes)[:20]} (total={len(dupes)})."

        if on_duplicate == "raise":
            raise ValueError(msg)

        if on_duplicate != "rename":
            raise ValueError(f"{msg} Invalid on_duplicate={on_duplicate!r}")

        occ: dict[str, list[tuple[Any, str]]] = defaultdict(list)
        for _, lst, tag in buckets:
            for el in lst:
                if getattr(el, "ref", None):
                    occ[el.ref].append((el, tag))
        existing = set(refs)

        for r in dupes:
            for i, (el, tag) in enumerate(occ[r][1:], start=1):
                new_ref = f"{r}__{tag}{i}"
                while new_ref in existing:
                    new_ref += "_"
                el.ref = new_ref
                existing.add(new_ref)

        if page_ir.warnings is None:
            page_ir.warnings = []
        page_ir.warnings.append(f"[RENAMED] {msg}")

    # De-dupe "namespacing collisions": cases like "n1" and "p0005:n1" that are
    # distinct pre-namespacing but would collide after applying the prefix.
    occ_suffix: dict[str, list[tuple[Any, str, str]]] = defaultdict(list)
    for _, lst, tag in buckets:
        for el in lst:
            r = getattr(el, "ref", None)
            if not isinstance(r, str) or not r:
                continue
            suffix = r[len(prefix) :] if r.startswith(prefix) else r
            occ_suffix[suffix].append((el, tag, r))

    suffix_dupes = [s for s, items in occ_suffix.items() if len(items) > 1]
    if suffix_dupes:
        msg = (
            "Namespacing collision within page (raw vs already-prefixed refs share suffix): "
            f"{sorted(suffix_dupes)[:20]} (total={len(suffix_dupes)})."
        )
        if on_duplicate == "raise":
            raise ValueError(msg)
        if on_duplicate != "rename":
            raise ValueError(f"{msg} Invalid on_duplicate={on_duplicate!r}")

        existing_full = {e.ref for e in all_elements if getattr(e, "ref", None)}
        for s in suffix_dupes:
            group = occ_suffix[s]
            # Deterministic: keep already-prefixed first (resume runs), then stable by
            # tag/ref.
            group_sorted = sorted(
                group, key=lambda x: (0 if x[2].startswith(prefix) else 1, x[1], x[2])
            )
            # Keep the first; rename the rest so their *final* namespaced ref is unique.
            for i, (el, tag, _) in enumerate(group_sorted[1:], start=1):
                new_suffix = f"{s}__{tag}{i}"
                while (
                    new_suffix in existing_full
                    or f"{prefix}{new_suffix}" in existing_full
                ):
                    new_suffix += "_"
                el.ref = new_suffix
                existing_full.add(new_suffix)

        page_ir.warnings = page_ir.warnings or []
        page_ir.warnings.append(f"[RENAMED] {msg}")

    # 2. Build the master map: "n1" --> "p0005:n1". Re-collect refs in case they
    # changed during de-duplication.
    local_refs = {e.ref for e in all_elements if getattr(e, "ref", None)}

    # If refs are already namespaced (resume run), we still must build a ref_map that
    # can repair dangling raw pointers (e.g., "n12" -> "p0005:n12").
    all_prefixed = bool(local_refs) and all(r.startswith(prefix) for r in local_refs)
    if all_prefixed:
        # Identity mapping for already-namespaced refs.
        ref_map = {r: r for r in local_refs}
        # ALSO map raw suffixes ("n1") to namespaced ("p0005:n1") so we can patch
        # pointers.
        for r in local_refs:
            suffix = r[len(prefix) :]  # Safe because r.startswith(prefix)
            if suffix and suffix not in ref_map:
                ref_map[suffix] = r
    else:
        # Normal case: local raw refs -> namespaced refs.
        ref_map = {
            r: (r if r.startswith(prefix) else f"{prefix}{r}") for r in local_refs
        }

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

    # 3. Update all pointers in the graph.

    # A. Update Path (ancestor chain).
    for e in all_elements:
        if hasattr(e, "path") and getattr(e, "path", None):
            e.path = [remap_if_local(r) for r in e.path]

    # B. Update cross references (if they are element refs, not curriculum codes).
    for e in all_elements:
        if hasattr(e, "cross_references") and getattr(e, "cross_references", None):
            e.cross_references = [remap_if_local(r) for r in e.cross_references]

    # C. Update Identity and Hierarchy (Self and Parent).
    for e in all_elements:
        if e.ref in ref_map:
            e.ref = ref_map[e.ref]
        if hasattr(e, "parent_ref") and getattr(e, "parent_ref", None):
            e.parent_ref = remap_if_local(getattr(e, "parent_ref"))

    # D. Update Relationships (Graph Edges).
    for rel in page_ir.relationships or []:
        rel.source_ref = remap_if_local(rel.source_ref)
        rel.target_ref = remap_if_local(rel.target_ref)

    # E. Update Provenance Links (Evidence).
    # If a statement came from "Table 1", and "Table 1" became "p005:Table 1", update it.
    for e in all_elements:
        for p in getattr(e, "provenance", []) or []:
            if getattr(p, "table_ref", None) in ref_map:
                p.table_ref = ref_map[p.table_ref]

    # F. Update Relationship Evidence Provenance.
    for rel in page_ir.relationships or []:
        for ev in getattr(rel, "evidence", []) or []:
            for p in getattr(ev, "provenance", []) or []:
                if getattr(p, "table_ref", None) in ref_map:
                    p.table_ref = ref_map[p.table_ref]

    return page_ir
