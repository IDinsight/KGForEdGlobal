"""This module manages the cross-page continuity logic.

It handles:

1. Carrying context (active parent, deep path) from Page N to Page N+1.
2. Stitching "orphan" elements on a new page to the most likely parent from the
    previous page.
3. Resolving explicit references to IDs defined on previous pages.
"""

# pylint:disable=R0911,R1260,R0915,R0912
# Standard Library
import re

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Optional

# Package Library
from skg.ir.processing.heuristics import _record_parent_inference
from skg.ir.schemas import PageIR

# Page-level namespace format produced by ensure_namespace_page_refs: "p0005:n12".
PAGE_REF_RE = re.compile(r"^p\d{4}:[^:]+$")

# Keep a short history per raw suffix to avoid "snapping" to a newer unrelated ref.
RAW_REF_WINDOW_PAGES = 10


@dataclass
class ContinuityState:
    """Dataclass for cross-page continuity state."""

    active_parent_ref: Optional[str] = None
    active_path: list[str] = field(default_factory=list)

    # Cached metadata for refs in active_path (label, list_index, node_type, etc.)
    # Used for heuristics like "attach 2.3 under 2.2's parent".
    active_path_ctx: list[dict[str, Any]] = field(default_factory=list)

    # Maps raw suffix refs (e.g. 'n12') -> namespaced refs (e.g. 'p0007:n12').
    # recent_raw_ref_map: dict[str, str] = field(default_factory=dict)
    recent_raw_ref_map: dict[str, deque[tuple[int, str]]] = field(
        default_factory=lambda: defaultdict(deque)
    )


class ContinuityApplicator:
    """Service class that applies continuity logic to a single PageIR."""

    def __init__(self, page_ir: PageIR, prev: ContinuityState) -> None:
        """Initializes the applicator.

        Parameters
        ----------
        page_ir
            The current page's PageIR to patch.
        prev
            The previous page's ContinuityState.
        """

        self.page_ir = page_ir
        self.prev = prev

    def _choose_parent_for_numbered_node(self, code: str) -> Optional[str]:
        """Heuristic to find parent for a code like '2.3' from context.

        Parameters
        ----------
        code
            The list_index code of the current node (e.g., '2.3').

        Returns
        -------
        Optional[str]
            The chosen parent_ref, or None if not found.
        """

        ctx = self.prev.active_path_ctx or []
        if not ctx:
            return self.prev.active_parent_ref

        # Try 1: Exact parent code match (e.g. '2.3' -> look for '2').
        parent_code = self._code_parent(code)
        if parent_code:
            for item in reversed(ctx):
                if (item.get("local_code") or "").strip().rstrip(".") == parent_code:
                    return item.get("ref")

        # Try 2: Sibling match (e.g. '2.3' -> look for '2.2', use its parent).
        deepest = ctx[-1]
        deepest_code = deepest.get("local_code")
        if (
            isinstance(deepest_code, str)
            and self._looks_like_numeric_code(deepest_code)
            and self._code_top(deepest_code) == self._code_top(code)
        ):
            if len(ctx) >= 2:
                return ctx[-2].get("ref")

        # Try 3: Fallback to nearest Section-like ancestor.
        for item in reversed(ctx):
            if self._is_sectionish(item):
                return item.get("ref")

        return self.prev.active_parent_ref

    def _code_parent(self, v: str) -> Optional[str]:
        """Gets the parent code for a numeric code string.

        Parameters
        ----------
        v
            The numeric code string (e.g., '2.3.1').

        Returns
        -------
        Optional[str]
            The parent code (e.g., '2.3'), or None if no parent.
        """

        v2 = v.strip().rstrip(".")
        parts = v2.split(".")
        return None if len(parts) <= 1 else ".".join(parts[:-1])

    def _code_top(self, v: str) -> Optional[str]:
        """Gets the top-level code for a numeric code string.

        Parameters
        ----------
        v
            The numeric code string (e.g., '2.3.1').

        Returns
        -------
        Optional[str]
            The top-level code (e.g., '2'), or None if empty.
        """

        v2 = v.strip().rstrip(".")
        return v2.split(".")[0] if v2 else None

    def _compute_spillover_allowed(self, all_elements: list[Any]) -> bool:
        """Conservative gate for 'content-only spillover' parenting.

        We only attach orphan elements to prev.active_parent_ref when there is evidence
        the page is truly a continuation; otherwise we risk silently mis-parenting
        unrelated content.

        Parameters
        ----------
        all_elements
            All elements on the current page.

        Returns
        -------
        bool
            True if spillover parenting is allowed, False otherwise.
        """

        # 1. Any explicit continuation marker on this page.
        if any(getattr(e, "is_continuation", False) for e in all_elements):
            return True

        # 2. Heading continuity: a provenance.section on this page matches a prior
        # active label.
        prev_labels = {
            (x.get("label") or "").strip().lower()
            for x in (self.prev.active_path_ctx or [])
            if x.get("label")
        }
        prev_labels.discard("")
        if prev_labels:
            for e in all_elements:
                for p in getattr(e, "provenance", None) or []:
                    sec = (getattr(p, "section", None) or "").strip().lower()
                    if not sec:
                        continue
                    # Exact or substring match either direction.
                    if sec in prev_labels or any(
                        sec in lbl or lbl in sec for lbl in prev_labels
                    ):
                        return True

        # 3. "continued" marker on a table caption (cheap & high-signal when present).
        for t in self.page_ir.tables or []:
            cap = (getattr(t, "caption", None) or "").strip().lower()
            if "continued" in cap or "cont." in cap:
                return True

        return False

    def _get_all_elements(self) -> list[Any]:
        """Helper to get all elements in the PageIR.

        Returns
        -------
        list[Any]
            A list of all elements in the PageIR.
        """

        return (
            (self.page_ir.nodes or [])
            + (self.page_ir.statements or [])
            + (self.page_ir.curriculum_elements or [])
            + (self.page_ir.tables or [])
            + (self.page_ir.diagrams or [])
        )

    def _is_sectionish(self, ctx_item: dict[str, Any]) -> bool:
        """Heuristic to check if a context item looks like a Section/Chapter.

        Parameters
        ----------
        ctx_item
            The context item metadata.

        Returns
        -------
        bool
            True if it looks like a Section/Chapter, False otherwise.
        """

        lbl = (ctx_item.get("label") or "").strip().lower()
        nty = (ctx_item.get("node_type_other") or "").strip().lower()
        return (
            lbl.startswith("section")
            or lbl.startswith("chapter")
            or nty in {"section", "chapter", "section_heading", "chapter_heading"}
        )

    def _looks_like_numeric_code(self, v: Optional[str]) -> bool:
        """Heuristic to check if a string looks like a numeric code (e.g., "2.3.1").

        Parameters
        ----------
        v
            The string to check.

        Returns
        -------
        bool
            True if it looks like a numeric code, False otherwise.
        """

        if not isinstance(v, str):
            return False
        v2 = v.strip().rstrip(".")
        return bool(re.match(r"^\d+(?:\.\d+)*$", v2))

    def _patch_element_continuity(self, el: Any) -> None:
        """Updates a single element's parent pointers and path.

        Parameters
        ----------
        el
            The element to patch.
        """

        # A. Remap explicit raw refs (e.g. "n10" -> "p004:n10")
        parent_ref = getattr(el, "parent_ref", None)
        remapped_parent = self._remap_raw(parent_ref)
        if remapped_parent != parent_ref:
            el.parent_ref = remapped_parent

        if hasattr(el, "path") and getattr(el, "path", None):
            el.path = [self._remap_raw(x) for x in el.path]

        if hasattr(el, "cross_references") and getattr(el, "cross_references", None):
            el.cross_references = [self._remap_raw(x) for x in el.cross_references]

        # B. Apply Heuristics for Implicit Parenting

        # 1. Front-matter headings should be siblings under the document root, not
        # chained under the previous front-matter heading.
        if getattr(el, "node_type_other", None) == "front_matter_heading":
            root_ref = None
            if getattr(self.prev, "active_path_ctx", None):
                # active_path_ctx is a list like [{"ref": "p0000:n1", ...}, ...]
                root_ref = self.prev.active_path_ctx[0].get("ref")
            if root_ref and getattr(el, "ref", None) != root_ref:
                if getattr(el, "parent_ref", None) != root_ref:
                    el.parent_ref = root_ref
                    self._repair_path_from_parent(el)
                    _record_parent_inference(
                        el=el,
                        parent_ref=root_ref,
                        reason="continuity:front_matter_heading_under_root",
                    )

        # 2. Explicit Continuation Flag.
        if getattr(el, "is_continuation", False):
            if not getattr(el, "parent_ref", None) and self.prev.active_parent_ref:
                el.parent_ref = self.prev.active_parent_ref
                self._repair_path_from_parent(el)
                _record_parent_inference(
                    el=el,
                    parent_ref=self.prev.active_parent_ref,
                    reason="continuity:is_continuation",
                )
            return

        # 3. Content-only spillover (no nodes on this page).
        page_has_nodes = getattr(self, "_page_has_nodes", bool(self.page_ir.nodes))
        spillover_allowed = getattr(self, "_spillover_allowed", False)
        if (
            not page_has_nodes
            and spillover_allowed
            and not getattr(el, "parent_ref", None)
            and self.prev.active_parent_ref
        ):
            el.parent_ref = self.prev.active_parent_ref
            self._repair_path_from_parent(el)
            _record_parent_inference(
                el=el,
                parent_ref=self.prev.active_parent_ref,
                reason="continuity:content_only_spillover",
            )
            return

        # 4. Numbered-node sibling heuristic (e.g., Node "2.3" appears). If we see
        # "2.3", we try to find the parent of "2.2" in history, rather than making
        # "2.3" a root.
        if hasattr(el, "node_type") and not getattr(el, "parent_ref", None):
            # Prefer true document codes (local_code). Use list_index only as a
            # last-resort fallback.
            code = getattr(el, "local_code", None)

            if not code:
                fallback = getattr(el, "list_index", None)
                # Only treat list_index as a code if it is purely numeric-dot (avoid
                # "Theme 3", "Unit A", etc.)
                if isinstance(fallback, str) and re.fullmatch(
                    r"\d+(?:\.\d+)+\.?", fallback.strip()
                ):
                    code = fallback.strip()

            if isinstance(code, str) and self._looks_like_numeric_code(code):
                chosen = self._choose_parent_for_numbered_node(code)
                if chosen and chosen != getattr(el, "ref", None):
                    el.parent_ref = chosen
                    self._repair_path_from_parent(el)
                    _record_parent_inference(
                        detail=f"code={code}",
                        el=el,
                        parent_ref=chosen,
                        reason="continuity:numbered_node_heuristic",
                    )

    def _patch_provenance_table_refs(self, ptrs: list[Any]) -> None:
        """Patches table_ref in provenance pointers.

        Parameters
        ----------
        ptrs
            The list of provenance pointers to patch.
        """

        if not ptrs:
            return
        for p in ptrs:
            if getattr(p, "table_ref", None):
                p.table_ref = self._remap_raw(p.table_ref)

    def _remap_raw(self, r: Optional[str]) -> Optional[str]:
        """Resolves a raw ref against previous page history.

        Parameters
        ----------
        r
            The reference to remap.

        Returns
        -------
        Optional[str]
            The remapped reference if found, else the original reference.
        """

        if r is None or not isinstance(r, str) or not r.strip():
            return r

        r = r.strip()

        # Already page-namespaced?
        if _is_page_namespaced_ref(r):
            return r

        hist = self.prev.recent_raw_ref_map.get(r)
        if not hist:
            return r

        # choose most recent prior mapping.
        prior = None
        for pi, ref in reversed(hist):
            if pi < self.page_ir.page_index:
                prior = (pi, ref)
                break
        if prior is None:
            return r

        # If this suffix was defined multiple times in-window, only remap when it's
        # "very recent" (<=2); otherwise fail closed to avoid snapping to a newer
        # unrelated n1.
        prior_defs = [(pi, ref) for (pi, ref) in hist if pi < self.page_ir.page_index]
        if len(prior_defs) > 1:
            most_recent_pi, most_recent_ref = prior_defs[-1]
            if self.page_ir.page_index - most_recent_pi <= 2:
                return most_recent_ref

            # Warn here (fail-closed).
            if getattr(self.page_ir, "warnings", None) is None:
                self.page_ir.warnings = []

            # Optional dedupe so we don’t spam the same warning 20x per page.
            if not hasattr(self, "_warned_ambiguous"):
                self._warned_ambiguous: set[Any] = set()
            key = (self.page_ir.page_index, r)
            if key not in self._warned_ambiguous:
                self._warned_ambiguous.add(key)
                # Include candidates to aid debugging.
                cands = ", ".join([f"p{pi:04d}:{ref}" for pi, ref in prior_defs[-3:]])
                self.page_ir.warnings.append(
                    f"[continuity p{self.page_ir.page_index:04d}] ambiguous raw ref "
                    f"'{r}' (multiple prior defs in window; most recent at "
                    f"p{most_recent_pi:04d}). Left unresolved. Recent candidates: "
                    f"{cands}"
                )

            return r

        return prior[1]

    def _repair_path_from_parent(self, el: Any) -> None:
        """Ensure el.path is consistent with el.parent_ref (for nodes).

        Needed because build_next_continuity_state() relies on len(node.path) to pick
        the active context across pages.

        Parameters
        ----------
        el
            The node element to repair.
        """

        if not hasattr(el, "path"):
            return

        ref = getattr(el, "ref", None)
        if not isinstance(ref, str) or not ref:
            return

        pr = getattr(el, "parent_ref", None)
        cur = list(getattr(el, "path", None) or [])

        # If already consistent, do nothing.
        if cur and cur[-1] == ref:
            if pr is None and len(cur) == 1:
                return
            if isinstance(pr, str) and len(cur) >= 2 and cur[-2] == pr:
                return

        if isinstance(pr, str) and pr:
            parent_path: Optional[list[str]] = None

            # If parent is on this page and has a path, use it.
            node_map = {n.ref: n for n in (self.page_ir.nodes or [])}
            pn = node_map.get(pr)
            if pn is not None and getattr(pn, "path", None):
                parent_path = list(pn.path)

            # Otherwise, if parent is in the previous active_path, use that prefix.
            if parent_path is None and pr in (self.prev.active_path or []):
                idx = (self.prev.active_path or []).index(pr)
                parent_path = list((self.prev.active_path or [])[: idx + 1])

            # Fallback: at least anchor under parent.
            if parent_path is None:
                parent_path = [pr]
            el.path = parent_path + [ref]
        else:
            el.path = [ref]

    def apply(self) -> PageIR:
        """Main entry point to patch the page.

        Returns
        -------
        PageIR
            The modified PageIR with continuity applied.
        """

        all_elements = self._get_all_elements()

        # Page-level cache: only allow "content-only spillover" when we have strong
        # evidence that this page is continuing the previous context.
        self._page_has_nodes = bool(self.page_ir.nodes)
        self._spillover_allowed = self._compute_spillover_allowed(all_elements)

        # 1. Patch references on elements (parents, paths, etc.).
        for el in all_elements:
            self._patch_element_continuity(el)
            self._patch_provenance_table_refs(getattr(el, "provenance", []))

        # 2. Patch references on relationships.
        for rel in self.page_ir.relationships or []:
            if getattr(rel, "source_ref", None):
                rel.source_ref = self._remap_raw(rel.source_ref)
            if getattr(rel, "target_ref", None):
                rel.target_ref = self._remap_raw(rel.target_ref)

            self._patch_provenance_table_refs(getattr(rel, "provenance", []))
            for ev in getattr(rel, "evidence", []) or []:
                self._patch_provenance_table_refs(getattr(ev, "provenance", []))

        return self.page_ir


def _is_page_namespaced_ref(r: str) -> bool:
    """Checks if a ref is a namespaced page ref (e.g., 'p0005:n12').

    Parameters
    ----------
    r
        The reference to check.

    Returns
    -------
    bool
        True if it is a namespaced page ref, False otherwise.
    """

    return bool(PAGE_REF_RE.match((r or "").strip()))


def build_next_continuity_state(
    page_ir: PageIR, prev: ContinuityState
) -> ContinuityState:
    """Computes the state to pass to the NEXT page.

    Parameters
    ----------
    page_ir
        The current page's PageIR.
    prev
        The previous page's ContinuityState.

    Returns
    -------
    ContinuityState
        The updated continuity state for the next page.
    """

    nodes = page_ir.nodes or []

    # 1. Determine active parent/path.
    if nodes:
        # Heuristic: Deepest node on the page is likely the active context.
        best = max(nodes, key=lambda n: len(getattr(n, "path", None) or []))
        active_parent_ref = best.ref
        active_path = list(getattr(best, "path", None) or []) or [best.ref]
    else:
        # Carry forward if no structural change.
        active_parent_ref = prev.active_parent_ref
        active_path = list(prev.active_path)

    # 2. Build active_path_ctx (metadata for heuristics).
    prev_ctx_map = {
        (x.get("ref") or ""): x for x in (prev.active_path_ctx or []) if x.get("ref")
    }
    node_map = {n.ref: n for n in nodes}

    active_path_ctx = []
    for ref in active_path:
        if ref in node_map:
            n = node_map[ref]
            active_path_ctx.append(
                {
                    "ref": ref,
                    "label": getattr(n, "label", None),
                    "local_code": getattr(n, "local_code", None),
                    "list_index": getattr(n, "list_index", None),
                    "node_type": getattr(n, "node_type", None),
                    "node_type_other": getattr(n, "node_type_other", None),
                }
            )
        elif ref in prev_ctx_map:
            active_path_ctx.append(prev_ctx_map[ref])
        else:
            active_path_ctx.append({"ref": ref})

    # 3. Update Ref Map (for explicit linking back to this page).

    # Copy prior history (don't mutate prev).
    recent_raw_ref_map: dict[str, deque[tuple[int, str]]] = defaultdict(deque)
    cutoff = page_ir.page_index - RAW_REF_WINDOW_PAGES

    for suffix, dq in (prev.recent_raw_ref_map or {}).items():
        # Copy deque.
        ndq = deque(dq)
        # Prune old.
        while ndq and ndq[0][0] < cutoff:
            ndq.popleft()
        if ndq:
            recent_raw_ref_map[suffix] = ndq

    def _record(raw_suffix: str, namespaced_ref: str) -> None:
        """Helper to record a raw_suffix --> namespaced_ref mapping.

        Parameters
        ----------
        raw_suffix
            The raw suffix (e.g., 'n12').
        namespaced_ref
            The namespaced ref (e.g., 'p0007:n12').
        """

        dq = recent_raw_ref_map[raw_suffix]
        dq.append((page_ir.page_index, namespaced_ref))
        cutoff = page_ir.page_index - RAW_REF_WINDOW_PAGES
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    # Helper to scan all items.
    all_elements = (
        (page_ir.nodes or [])
        + (page_ir.statements or [])
        + (page_ir.curriculum_elements or [])
        + (page_ir.tables or [])
        + (page_ir.diagrams or [])
    )

    for el in all_elements:
        r = getattr(el, "ref", None)
        if isinstance(r, str) and _is_page_namespaced_ref(r):
            raw_suffix = r.split(":", 1)[1]
            _record(raw_suffix, r)

    return ContinuityState(
        active_parent_ref=active_parent_ref,
        active_path=active_path,
        active_path_ctx=active_path_ctx,
        recent_raw_ref_map=recent_raw_ref_map,
    )
