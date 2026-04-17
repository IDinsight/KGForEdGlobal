"""This module contains utility functions for creating knowledge graphs."""

# Standard Library
import hashlib
import re
import unicodedata
import uuid

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Third Party Library
from loguru import logger
from PIL import Image

# Package Library
from skg.canonical_ir.schemas import CanonicalIR, SegmentDecision
from skg.config import Settings
from skg.schemas import CreateKGConfig, ExtractionConfig, RunCtx
from skg.utils.constants import StatementRole
from skg.utils.general import make_dir, open_json_type, write_to_json


@dataclass
class ExportContext:
    """Internal helper model for KG export: indexes + deterministic helpers."""

    doc_key: str
    kg_config: CreateKGConfig
    pdf_name: str
    root_id: str

    # Indexes.
    children_by_parent: dict[str, list[str]]
    decisions_by_id: dict[str, dict[str, Any]]
    decisions_by_segment_id: dict[str, dict[str, Any]]
    nodes_by_id: dict[str, dict[str, Any]]
    parent_by_child: dict[str, str]

    _needs_order_disambiguator: set[tuple[str, str]] = field(
        default_factory=set, init=False
    )
    edge_metadata_by_pair: dict[tuple[str, str], dict[str, Any]] = field(
        default_factory=dict
    )
    edge_order_index: dict[tuple[str, str], int] = field(default_factory=dict)

    def _infer_language_from_nodes(self) -> str | None:
        """Infer the primary language from the nodes in the KG.

        Returns
        -------
        str | None
            The inferred language code, or None if not found.
        """

        for n in self.nodes_by_id.values():
            for key in ("title", "body"):
                text_unit_or_none = n.get(key)

                if isinstance(text_unit_or_none, dict):
                    lang = text_unit_or_none.get("language")

                    if lang and lang != "und":
                        return str(lang)

        return None

    def _path_piece(
        self, *, child_id: str, node: dict[str, Any], parent_id: str
    ) -> str:
        """Compute a path piece for a node based on its role, local code, and text.

        Parameters
        ----------
        child_id
            The ID of the child node.
        node
            The node dictionary containing its attributes.
        parent_id
            The ID of the parent node.

        Returns
        -------
        str
            The computed path piece for the node.
        """

        role = node["role"]
        assert role, f"Node {child_id} is missing role in provenance: {node}"
        piece = _compute_base_piece(node)

        if (parent_id, child_id) in self._needs_order_disambiguator:
            oi = self.edge_order_index.get((parent_id, child_id), 0)

            # Add a stable disambiguator so sibling collisions cannot produce identical
            # path keys even when `order_index` values are duplicated or missing.
            suffix = stable_text_hash(s=child_id, n=8)
            piece = f"{piece}~{oi}~{suffix}"

        return piece

    def compute_path_key(self, node_id: str) -> str:
        """Compute a deterministic path key for a node based on its position in the
        hierarchy.

        Parameters
        ----------
        node_id
            The ID of the node to compute the path key for.

        Returns
        -------
        str
            The computed path key for the node.
        """

        if node_id == self.root_id:
            return "framework"

        chain: list[str] = []
        cur: str | None = node_id
        seen: set[str] = set()

        while cur and cur != self.root_id and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            nxt = self.parent_by_child.get(cur)

            if nxt == cur:  # Self-loop guard
                break

            cur = nxt

        chain.reverse()

        parts: list[str] = []
        parent = self.root_id

        for nid in chain:
            node = self.nodes_by_id[nid]
            parts.append(self._path_piece(child_id=nid, node=node, parent_id=parent))
            parent = nid

        return "/".join(parts)

    def get_framework_metadata(self) -> dict[str, Any]:
        """Resolve the framework-level metadata for the KG export.

        Returns
        -------
        dict[str, Any]
            The framework metadata dictionary.
        """

        return {
            "academic_subject_default": self.kg_config.academic_subject_default,
            "adoption_status": self.kg_config.adoption_status,
            "attribution_statement": self.kg_config.attribution_statement,
            "author": self.kg_config.author,
            "case_uri_base": self.kg_config.case_uri_base,
            "doc_key": self.doc_key,
            "export_dialect": self.kg_config.export_dialect,
            "in_language": (
                self.kg_config.language_default
                if self.kg_config.export_in_language_policy == "default"
                else self._infer_language_from_nodes()
                or self.kg_config.language_default
            ),
            "jurisdiction": self.kg_config.jurisdiction_default,
            "license": self.kg_config.license,
            "namespace_uuid": str(self.kg_config.namespace_uuid),
            "pdf_name": self.pdf_name,
            "provider": self.kg_config.provider,
        }

    def should_drop_segment(self, decision: dict[str, Any]) -> bool:
        """Determine if a segment should be dropped based on non-standard policies.

        Parameters
        ----------
        decision
            The decision dictionary for the segment.

        Returns
        -------
        bool
            True if the segment should be dropped, False otherwise.
        """

        policies = set(self.kg_config.non_standard_segment_drop_policy or [])

        if "by_decision_type" in policies:
            dt = decision.get("decision_type")

            if dt in {t.value for t in self.kg_config.non_standard_decision_types}:
                return True

        if "by_columns_signature" in policies:
            sig = decision.get("columns_signature")

            if sig and sig in self.kg_config.non_standard_columns_signature:
                return True

        return False


@dataclass(frozen=True)
class KGDirs:
    """Dataclass for KG directories."""

    root: Path
    academic_standards: Path
    learning_components: Path
    learning_progressions: Path
    combined: Path


def _compute_base_piece(node: dict[str, Any]) -> str:
    """Compute the base path piece for a canonical node (before order disambiguation).

    This is the single source of truth for the string used to identify a node within
    its sibling set. It is called by both `ExportContext._path_piece` (for path-key
    generation) and `_detect_sibling_collisions` (for order disambiguation detection).

    For grouping-like nodes, the label normalization policy is intentionally kept
    consistent with `keyify()` so accented and other Unicode text collapses the same
    way across path keys and LP thread/topic keys.

    Parameters
    ----------
    node
        The canonical node dictionary.

    Returns
    -------
    str
        The base piece string for the node.
    """

    role = str(node.get("role") or "")
    code = normalize_ws(str(node.get("local_code") or ""))
    text_source = node["normalized_text"]

    if role in {item.value for item in StatementRole}:
        return f"{role}:{code}:{stable_text_hash(s=text_source)}"

    label = normalize_key_token(label=text_source, separator="-")
    return f"{role}:{code}:{label}" if code else f"{role}:{label}"


def _detect_sibling_collisions(ctx: ExportContext) -> set[tuple[str, str]]:
    """Detect sibling nodes that may require order disambiguation.

    Parameters
    ----------
    ctx
        The KG export context.

    Returns
    -------
    set[tuple[str, str]]
        A set of (parent_id, child_id) tuples that require order disambiguation.
    """

    needs: set[tuple[str, str]] = set()

    for pid, kids in ctx.children_by_parent.items():
        seen: dict[str, str] = {}

        for cid in kids:
            node = ctx.nodes_by_id[cid]
            base = _compute_base_piece(node)

            if base in seen:
                needs.add((pid, seen[base]))
                needs.add((pid, cid))
            else:
                seen[base] = cid

    return needs


def _validate_decision_references(ctx: ExportContext) -> None:
    """Ensure all decision IDs referenced by nodes exist in the context.

    Parameters
    ----------
    ctx
        The KG export context.

    Raises
    ------
    ValueError
        If there are nodes that reference missing decision IDs.
    """

    missing_decisions = []

    for n in ctx.nodes_by_id.values():
        for sid in n.get("source_decision_ids", []):
            if sid not in ctx.decisions_by_id:
                missing_decisions.append(sid)

                if len(missing_decisions) >= 10:
                    break

        if len(missing_decisions) >= 10:
            break

    if missing_decisions:
        raise ValueError(
            f"Nodes reference missing decision_ids (examples): {missing_decisions[:10]}"
        )


def _validate_no_cycles(ctx: ExportContext) -> None:
    """Detect cycles by walking up parent links from every node.

    Parameters
    ----------
    ctx
        The KG export context.

    Raises
    ------
    ValueError
        If there are cycles detected in the parent-child relationships.
    """

    cycle_examples: list[str] = []

    for nid in ctx.parent_by_child:
        walk_seen: set[str] = set()
        cur: str | None = nid

        # Walk up towards root to check for circular parents.
        while cur and cur != ctx.root_id:
            if cur in walk_seen:
                cycle_examples.append(nid)
                break

            walk_seen.add(cur)
            cur = ctx.parent_by_child.get(cur)

        if len(cycle_examples) >= 5:
            break

    if cycle_examples:
        raise ValueError(
            f"Tree integrity: cycle(s) detected in parent_by_child. "
            f"{len(cycle_examples)} node(s) do not reach root. "
            f"Examples: {cycle_examples[:5]}. "
            f"This typically indicates a bug in the canonicalization step that "
            f"produced circular `hasChild` edges."
        )


def _validate_reachability(ctx: ExportContext) -> None:
    """Ensure all nodes in the context are reachable from the root.

    Parameters
    ----------
    ctx
        The KG export context.

    Raises
    ------
    ValueError
        If there are nodes that are not reachable from the root.
    """

    visited: set[str] = set()

    def dfs(nid: str) -> None:
        """Depth-first search to mark visited nodes.

        Parameters
        ----------
        nid
            The current node ID.
        """

        if nid in visited:
            return

        visited.add(nid)

        for cid in ctx.children_by_parent.get(nid, []):
            dfs(cid)

    dfs(ctx.root_id)
    all_nodes = set(ctx.nodes_by_id.keys())

    if visited != all_nodes:
        missing = sorted(all_nodes - visited)[:20]
        raise ValueError(
            f"Tree integrity: {len(all_nodes - visited)} nodes unreachable from root. "
            f"Examples: {missing}"
        )


def _validate_root_structure(ctx: ExportContext) -> None:
    """Ensure the root has no parent.

    Parameters
    ----------
    ctx
        The KG export context.
    """

    assert (
        ctx.root_id not in ctx.parent_by_child
    ), f"Root ID unexpectedly has a parent: {ctx.root_id}"


def _verify_columns_signature(
    *, ctx: ExportContext, segment_decisions: list[SegmentDecision]
) -> None:
    """Verify that table segment decisions have `columns_signature` when required.

    Parameters
    ----------
    ctx
        The KG export context.
    segment_decisions
        The list of segment decisions.

    Raises
    ------
    ValueError
        If a table segment decision is missing columns_signature when required.
    """

    if "by_columns_signature" not in set(
        ctx.kg_config.non_standard_segment_drop_policy or []
    ):
        return

    for d in segment_decisions:
        if d.segment_kind != "table" or d.decision_type.value in {
            "ignore",
            "unresolved",
        }:
            continue

        if not d.columns_signature:
            raise ValueError(
                f"Missing columns_signature on table decision {d.decision_id} "
                f"(segment_id={d.segment_id})"
            )


def _verify_tree_integrity(ctx: ExportContext) -> None:
    """Verify the integrity of the KG tree structure.

    Raises
    ------
    ValueError
        If the tree structure is invalid.
    """

    _validate_root_structure(ctx)
    _validate_reachability(ctx)
    _validate_decision_references(ctx)
    _validate_no_cycles(ctx)


def build_kg_export_context(
    *, canonical_ir: CanonicalIR, config: CreateKGConfig
) -> ExportContext:
    """Build the KG export context from a CanonicalIR and KG config.

    The process is as follows:

    1. Serialize nodes.
    2. Build tree indexes.
    3. Serialize decisions by ID.
    4. Serialize decisions by segment ID (choose a representative decision per
        `segment_id` to handle chunking). For segments with multiple decisions (e.g.,
        chunked tables), choose a single representative decision per `segment_id`.
        Tiebreaker: (confidence DESC, decision_id DESC).
            - `confidence` reflects the LLM's reported certainty, so higher is
                preferred.
            - `decision_id` is a deterministic hash-based string, so its lexicographic
                ordering is stable across reruns but carries no semantic meaning. It
                serves solely to break ties when confidence values are equal, ensuring
                a single deterministic winner per segment.
    5. Initialize the export context.
    6. Post-init calculations.

    Parameters
    ----------
    canonical_ir
        The CanonicalIR instance.
    config
        The KG creation configuration.

    Returns
    -------
    ExportContext
        The KG export context.
    """

    # 1.
    nodes_by_id: dict[str, dict[str, Any]] = {
        node.node_id: node.model_dump(mode="json") for node in canonical_ir.nodes
    }
    root_id = canonical_ir.root_id
    assert root_id in nodes_by_id, f"Root ID missing from nodes: {root_id}"

    # 2.
    children_by_parent: dict[str, list[str]] = defaultdict(list)
    edge_metadata_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    edge_order_index: dict[tuple[str, str], int] = {}
    parent_by_child: dict[str, str] = {}

    for edge in canonical_ir.edges:
        cid = edge.child_id
        oi = edge.order_index
        pid = edge.parent_id
        assert edge.rel == "hasChild", f"Unexpected edge relationship: {edge.rel}"
        assert cid in nodes_by_id, f"Edge child_id not found in nodes: {cid}"
        assert pid in nodes_by_id, f"Edge parent_id not found in nodes: {pid}"
        children_by_parent[pid].append(cid)
        edge_order_index[(pid, cid)] = oi

        # Preserve edge-level provenance if present (best-effort).
        edge_metadata_by_pair[(pid, cid)] = edge.model_dump(mode="json")
        assert cid not in parent_by_child, f"Node has multiple parents: {cid}"
        parent_by_child[cid] = pid

    # Sort children by (order_index, child_id) for stability.
    for pid, kids in list(children_by_parent.items()):
        children_by_parent[pid] = sorted(
            kids,
            key=lambda c: (
                edge_order_index.get((pid, c), 0),  # pylint:disable=W0640
                c,
            ),
        )

    # 3.
    decisions_by_id: dict[str, dict[str, Any]] = {}

    for d in canonical_ir.segment_decisions:
        assert d.decision_id, f"Missing decision_id for segment decision: {d}"
        decisions_by_id[d.decision_id] = d.model_dump(mode="json")

    # 4.
    by_seg: dict[str, list[SegmentDecision]] = defaultdict(list)

    for d in canonical_ir.segment_decisions:
        sid = d.segment_id
        assert sid, f"Missing segment_id for decision_id: {d.decision_id}"
        by_seg[str(sid)].append(d)

    decisions_by_segment_id: dict[str, dict[str, Any]] = {}

    # Dump the best decision to a dict.
    for sid, decisions in by_seg.items():
        ds_sorted = sorted(
            decisions, key=lambda d: (d.confidence, d.decision_id), reverse=True
        )
        decisions_by_segment_id[sid] = ds_sorted[0].model_dump(mode="json")

    # 5.
    ctx = ExportContext(
        children_by_parent=dict(children_by_parent),
        decisions_by_id=decisions_by_id,
        decisions_by_segment_id=decisions_by_segment_id,
        doc_key=canonical_ir.doc_key,
        edge_metadata_by_pair=edge_metadata_by_pair,
        edge_order_index=edge_order_index,
        kg_config=config,
        nodes_by_id=nodes_by_id,
        parent_by_child=parent_by_child,
        pdf_name=canonical_ir.pdf_name,
        root_id=canonical_ir.root_id,
    )

    # 6.
    ctx._needs_order_disambiguator = _detect_sibling_collisions(ctx)
    _verify_tree_integrity(ctx)
    _verify_columns_signature(ctx=ctx, segment_decisions=canonical_ir.segment_decisions)

    return ctx


def canon_str_pair(a: str, b: str) -> tuple[str, str]:
    """Canonicalize an undirected pair of UUID strings by lexicographic sort.

    This is the single source of truth for how undirected (i.e., `relatesTo`) edge
    pairs are canonicalized when compared as *strings*. All code that builds or checks
    forbidden-pair sets, validator duplicate detection, and disposition-map keys for
    undirected relationships should use this function to ensure consistent ordering.

    Parameters
    ----------
    a
        The first UUID string.
    b
        The second UUID string.

    Returns
    -------
    tuple[str, str]
        A tuple `(lo, hi)` where `lo <= hi` lexicographically.
    """

    return (a, b) if a <= b else (b, a)


def create_kg_dirs(*, output_dir: Path) -> KGDirs:
    """Create KG directories for a given KG run.

    Parameters
    ----------
    output_dir
        The output directory root.

    Returns
    -------
    KGDirs
        The created KG directories.
    """

    root = output_dir
    academic_standards = root / "academic_standards"
    learning_components = root / "learning_components"
    learning_progressions = root / "learning_progressions"
    combined = root / "combined"

    for p in [
        root,
        academic_standards,
        learning_components,
        learning_progressions,
        combined,
    ]:
        make_dir(p)

    return KGDirs(
        root=root,
        academic_standards=academic_standards,
        learning_components=learning_components,
        learning_progressions=learning_progressions,
        combined=combined,
    )


def cross_check_canonical_ir_run(
    *,
    canonical_ir_fp: Path,
    computed_doc_key: str,
    expected_doc_key: str,
    extraction_config: ExtractionConfig,
    kg_config: CreateKGConfig | None,
) -> CanonicalIR:
    """Cross-check that the canonical IR run matches expected parameters and load the
    canonical IR for the KG run.

    Parameters
    ----------
    canonical_ir_fp
        The file path to the canonical IR JSON.
    computed_doc_key
        The document key computed from the source PDF bytes by the caller.
    expected_doc_key
        The expected document key (hex string) from the extraction run metadata.
    extraction_config
        The extraction configuration used for the run.
    kg_config
        The KG creation configuration for the run.

    Returns
    -------
    CanonicalIR
        The loaded CanonicalIR instance from the provided file path.

    Raises
    ------
    ValueError
        If kg_config is not provided.
        If the computed `doc_key` from the PDF does not match the `doc_key` in the
            canonical IR run metadata.
    """

    if not kg_config:
        raise ValueError("KG config is required")

    if computed_doc_key != expected_doc_key:
        raise ValueError(
            f"PDF doc_key mismatch.\n"
            f"  PDF provided to create_kgs():   {extraction_config.pdf_fp}\n"
            f"  computed doc_key:               {computed_doc_key}\n"
            f"  extraction_run.json key:        {expected_doc_key}\n"
            f"You are likely creating KGs using a different PDF than the one used to "
            f"create the canonical IR. Pass the same PDF used in the canonical IR run "
            f"or re-run the canonical IR."
        )

    return CanonicalIR.model_validate(open_json_type(canonical_ir_fp))


def get_page_image_dims(extraction_dir: Path) -> list[dict[str, Any]]:
    """Get page image dimensions from extraction results. Page images from extraction
    should always be in extraction_dir/page_images as PNGs named 0000.png, 0001.png, ...

    Parameters
    ----------
    extraction_dir
        The extraction results directory.

    Returns
    -------
    list[dict[str, Any]]
        Per-page pixel dimensions keyed by 0-based page_index.
    """

    page_dir = extraction_dir / "page_images"
    assert page_dir.exists(), f"Page images directory not found: {page_dir}"
    pngs = list(page_dir.glob("*.png"))
    assert pngs, f"No PNG page images found in: {page_dir}"
    dims: list[dict[str, Any]] = []

    # Sort by numeric stem to preserve true page order (0000, 0001, ...).
    def _page_index(p: Path) -> int:
        """Extract the page index from the filename stem.

        Parameters
        ----------
        p
            The Path object for the PNG file.

        Returns
        -------
        int
            The extracted page index.
        """

        return int(p.stem)

    for p in sorted(pngs, key=_page_index):
        page_index = int(p.stem)  # "0000" -> 0

        with Image.open(p) as im:
            w, h = im.size

        dims.append(
            {
                "filename": p.name,
                "height_px": h,
                "page_index": page_index,
                "relative_path": f"page_images/{p.name}",
                "width_px": w,
            }
        )

    return dims


def merge_graph_bundles(
    *, bundles: list[dict[str, Any]], doc_key: str, export_dialect: str
) -> dict[str, Any]:
    """Merge multiple KG graph bundles into a single bundle.

    Parameters
    ----------
    bundles
        The list of KG graph bundles to merge.
    doc_key
        The document key for the merged bundle.
    export_dialect
        The export dialect for the merged bundle.

    Returns
    -------
    dict[str, Any]
        The merged KG graph bundle.

    Raises
    ------
    ValueError
        If there are ID collisions with differing properties.
    """

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    included_graph_types: list[str] = []
    nodes_by_id: dict[str, dict[str, Any]] = {}
    rels_by_id: dict[str, dict[str, Any]] = {}

    for b in bundles:
        gt = str(b.get("graph_type", "")).strip()

        if gt:
            included_graph_types.append(gt)

        for n in b.get("nodes", []) or []:
            nid = str(n["id"])

            if nid in nodes_by_id:
                existing = nodes_by_id[nid]

                if (existing.get("properties") or {}) != (n.get("properties") or {}):
                    logger.error(
                        f"Node ID collision with differing properties: "
                        f"id={nid} "
                        f"existing_labels={existing.get('labels')} "
                        f"new_labels={n.get('labels')}"
                    )
                    raise ValueError(
                        f"Node ID collision with differing properties: {nid}"
                    )

                merged_labels = sorted(
                    set(existing.get("labels") or []) | set(n.get("labels") or [])
                )
                nodes_by_id[nid] = {
                    "id": nid,
                    "labels": merged_labels,
                    "properties": (
                        existing.get("properties")
                        if existing.get("properties") is not None
                        else n.get("properties") or {}
                    ),
                }
            else:
                nodes_by_id[nid] = n

        for r in b.get("relationships", []) or []:
            rid = str(r["id"])

            if rid in rels_by_id:
                existing = rels_by_id[rid]

                if (existing.get("properties") or {}) != (r.get("properties") or {}):
                    logger.error(
                        f"Relationship ID collision with differing properties: "
                        f"id={rid} "
                        f"existing_type={existing.get('type')} "
                        f"new_type={r.get('type')}"
                    )
                    raise ValueError(
                        f"Relationship ID collision with differing properties: {rid}"
                    )

                if (
                    existing.get("type") != r.get("type")
                    or existing.get("start") != r.get("start")
                    or existing.get("end") != r.get("end")
                ):
                    logger.error(
                        f"Relationship ID collision with differing endpoints/type: "
                        f"id={rid}"
                    )
                    raise ValueError(
                        f"Relationship ID collision with differing endpoints/type: {rid}"
                    )
            else:
                rels_by_id[rid] = r

    # Compute a correct merged `graph_type` from what was actually merged.
    included_unique = sorted(set(included_graph_types))
    preferred_order = [
        "academic_standards",
        "learning_components",
        "learning_progressions",
    ]
    ordered = [t for t in preferred_order if t in included_unique] + sorted(
        set(included_unique) - set(preferred_order)
    )
    merged_graph_type = "_plus_".join(ordered) if ordered else ""

    # Sort merged records by stable identifier so serialized bundle order stays
    # deterministic even if upstream bundle or dict insertion order changes.
    return {
        "doc_key": doc_key,
        "export_dialect": export_dialect,
        "generated_at": generated_at,
        "graph_type": merged_graph_type,
        "included_graph_types": included_unique,
        "nodes": [nodes_by_id[nid] for nid in sorted(nodes_by_id)],
        "relationships": [rels_by_id[rid] for rid in sorted(rels_by_id)],
    }


def normalize_key_token(*, label: str, separator: str) -> str:
    """Normalize a label into a deterministic ASCII key token.

    This centralizes the normalization policy shared by path-key pieces and other
    compact thread/topic keys. The normalization steps are:

    1. Collapse internal whitespace.
    2. Normalize Unicode with NFKD.
    3. Strip diacritics by ASCII-folding where possible.
    4. Lowercase and replace non-alphanumeric runs with `separator`.
    5. Fall back to a short stable hash when normalization yields an empty token.

    Parameters
    ----------
    label
        The input label string to normalize.
    separator
        The separator to use between token runs (for example "-" or "_").

    Returns
    -------
    str
        A normalized key token capped at 80 characters. Returns an empty string only
        when the input label is empty/blank.
    """

    raw = normalize_ws(str(label or ""))

    if not raw:
        return ""

    norm = unicodedata.normalize("NFKD", raw)
    ascii_s = norm.encode("ascii", "ignore").decode("ascii")
    lowered = normalize_ws(ascii_s).lower()
    token = re.sub(r"[^a-z0-9]+", separator, lowered).strip(separator)

    if not token:
        return f"h{stable_text_hash(s=raw)}"

    return token[:80]


def normalize_ws(s: str) -> str:
    """Normalize whitespace in a string by collapsing multiple spaces and trim.

    Parameters
    ----------
    s
        The input string to normalize.

    Returns
    -------
    str
        The normalized string.
    """

    return re.sub(r"\s+", " ", (s or "")).strip()


def persist_kg_run(
    *, config: CreateKGConfig, output_dir: Path
) -> tuple[KGDirs, RunCtx]:
    """Persist KG run metadata.

    Parameters
    ----------
    config
        The KG creation run configuration.
    output_dir
        The output directory for the KG run results.

    Returns
    -------
    tuple[KGDirs, RunCtx]
        The created KG directories and persisted KG run metadata.
    """

    kg_dirs = create_kg_dirs(output_dir=output_dir)
    exclude_keys = {"overwrite"}
    extra = {
        k: v for k, v in config.model_dump(mode="json").items() if k not in exclude_keys
    }
    kg_run = RunCtx(
        extra=extra,
        models={
            "learning_components": Settings.LLM_KG_MODEL,
            "learning_progressions": Settings.LLM_KG_MODEL,
        },
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(fp=output_dir / "kg_run.json", json_info=kg_run)

    logger.info(f"Saving KG creation results to: {kg_dirs.root}")

    return kg_dirs, kg_run


def stable_text_hash(*, n: int = 32, s: str) -> str:
    """Generate a stable hash from a string.

    The hashing normalization policy intentionally mirrors the Unicode handling used
    by `normalize_key_token()` so canonically equivalent text does not drift across
    reruns due only to normalization-form differences.

    Parameters
    ----------
    n
        The length of the hash to return.
    s
        The input string to hash.

    Returns
    -------
    str
        The stable hash of the string.
    """

    s = unicodedata.normalize("NFKC", normalize_ws(str(s or ""))).casefold()
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]
