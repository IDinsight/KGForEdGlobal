"""This module contains utility functions for creating knowledge graphs."""

# Standard Library
import hashlib
import re
import uuid

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Third Party Library
from loguru import logger

# Package Library
from skg.canonical_ir.schemas import CanonicalIR, SegmentDecision
from skg.schemas import CreateKGConfig, RunCtx
from skg.utils.constants import StatementRole
from skg.utils.general import make_dir, write_to_json


@dataclass
class ExportContext:
    """Internal helper model for KG export: indexes + deterministic helpers."""

    doc_key: str
    kg_config: CreateKGConfig
    pdf_name: str
    root_id: str

    # Indexes
    children_by_parent: dict[str, list[str]]
    decisions_by_id: dict[str, dict[str, Any]]
    decisions_by_segment_id: dict[str, dict[str, Any]]
    nodes_by_id: dict[str, dict[str, Any]]
    parent_by_child: dict[str, str]

    _needs_order_disambiguator: set[tuple[str, str]] = field(
        default_factory=set, init=False
    )
    edge_order_index: dict[tuple[str, str], int] = field(default_factory=dict)

    def _infer_language_from_nodes(self) -> str | None:
        """Infer the primary language from the nodes in the KG.

        None
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
        code = _normalize_ws(str(node.get("local_code") or ""))

        # Build the base piece first (no early returns), then apply order
        # disambiguation if needed.
        if role in {item.value for item in StatementRole}:
            text_for_hash = str(node.get("normalized_text") or _node_display_text(node))
            piece = f"{role}:{code}:{stable_text_hash(s=text_for_hash)}"
        else:
            label = _slugify(
                s=str(node.get("normalized_text") or _node_display_text(node))
            )
            piece = f"{role}:{code}:{label}" if code else f"{role}:{label}"

        if (parent_id, child_id) in self._needs_order_disambiguator:
            oi = self.edge_order_index.get((parent_id, child_id), 0)
            piece = f"{piece}~{oi}"

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

        while cur and cur != self.root_id:
            chain.append(cur)
            cur = self.parent_by_child.get(cur)

            if cur is None:
                break

        chain.reverse()

        parts: list[str] = []
        parent = self.root_id

        for nid in chain:
            node = self.nodes_by_id[nid]
            parts.append(self._path_piece(child_id=nid, node=node, parent_id=parent))
            parent = nid

        return "/".join(parts)

    def resolve_framework_metadata(self) -> dict[str, Any]:
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

            if sig and sig in (self.kg_config.non_standard_columns_signature or set()):
                return True

        return False


@dataclass(frozen=True)
class KGDirs:
    """Dataclass for KG directories."""

    root: Path
    cache: Path


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
            role = str(node.get("role") or "")
            code = _normalize_ws(str(node.get("local_code") or ""))

            # NB: include statement roles too, using the same base as _path_piece.
            if role in {item.value for item in StatementRole}:
                text_for_hash = str(
                    node.get("normalized_text") or _node_display_text(node)
                )
                base = f"{role}:{code}:{stable_text_hash(s=text_for_hash)}"
            else:
                label = _slugify(
                    s=str(node.get("normalized_text") or _node_display_text(node))
                )
                base = f"{role}:{code}:{label}" if code else f"{role}:{label}"

            if base in seen:
                needs.add((pid, seen[base]))
                needs.add((pid, cid))
            else:
                seen[base] = cid

    return needs


def _node_display_text(node: dict[str, Any]) -> str:
    """Extract display text from a node dictionary.

    Parameters
    ----------
    node
        The node dictionary.

    Returns
    -------
    str
        The extracted display text.
    """

    title = node.get("title")

    if isinstance(title, dict):
        if title.get("text_en"):
            return str(title["text_en"])

        if title.get("text"):
            return str(title["text"])

    body = node.get("body")

    if isinstance(body, dict):
        if body.get("text_en"):
            return str(body["text_en"])

        if body.get("text"):
            return str(body["text"])

    if node.get("normalized_text"):
        return str(node["normalized_text"])

    return ""


def _normalize_ws(s: str) -> str:
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


def _slugify(*, max_len: int = 80, s: str) -> str:
    """Generate a slug from a string.

    Parameters
    ----------
    max_len
        The maximum length of the slug.
    s
        The input string to slugify.

    Returns
    -------
    str
        The slugified string.
    """

    original = _normalize_ws(s)
    lower = original.lower()

    slug = re.sub(r"[^a-z0-9]+", "-", lower).strip("-")

    # Fallback: if everything got stripped (e.g., non-Latin text), use a short stable
    # hash. The prefix avoids empty/pure-digit oddities.
    slug = slug or f"h{stable_text_hash(s=original)}"

    return slug[:max_len] if max_len else slug


def _verify_columns_signature(
    *, ctx: ExportContext, segment_decisions: list[SegmentDecision]
) -> None:
    """Verify that table segment decisions have columns_signature when required.

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
        if d.segment_kind != "table" or d.decision_type in {"ignore", "unresolved"}:
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

    root_id = ctx.root_id

    assert (
        root_id not in ctx.parent_by_child
    ), f"Root ID unexpectedly has a parent: {root_id}"

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

    dfs(root_id)

    all_nodes = set(ctx.nodes_by_id.keys())

    if visited != all_nodes:
        missing = sorted(all_nodes - visited)[:20]
        raise ValueError(
            f"Tree integrity: {len(all_nodes - visited)} nodes unreachable from root. "
            f"Examples: {missing}"
        )

    # Validate decision references.
    missing_decisions = []

    for n in ctx.nodes_by_id.values():
        for did in n.get("source_decision_ids", []):
            if did not in ctx.decisions_by_id:
                missing_decisions.append(did)
                if len(missing_decisions) >= 10:
                    break

    if missing_decisions:
        raise ValueError(
            f"Nodes reference missing decision_ids (examples): {missing_decisions[:10]}"
        )


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
    cache = root / "cache"

    for p in [root, cache]:
        make_dir(p)

    return KGDirs(root=root, cache=cache)


def build_kg_export_context(
    *, canonical_ir: CanonicalIR, config: CreateKGConfig
) -> ExportContext:
    """Build the KG export context from a CanonicalIR and KG config.

    The process is as follows:

    1. Serialize nodes
    2. Build tree indexes
    3. Serialize decisions by ID
    4. Serialize decisions by segment ID (choose a representative decision per
        segment_id to handle chunking).
    5. Initialize context
    6. Post-init calculations

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

    Raises
    ------
    ValueError
        If the CanonicalIR structure is invalid.
    """

    # 1.
    nodes_by_id: dict[str, dict[str, Any]] = {
        node.node_id: node.model_dump() for node in canonical_ir.nodes
    }
    root_id = canonical_ir.root_id

    if root_id not in nodes_by_id:
        raise ValueError(f"root_id not found in nodes: {root_id}")

    # 2.
    children_by_parent: dict[str, list[str]] = defaultdict(list)
    edge_order_index: dict[tuple[str, str], int] = {}
    parent_by_child: dict[str, str] = {}

    for edge in canonical_ir.edges:
        assert edge.rel == "hasChild", f"Unexpected edge relationship: {edge.rel}"

        cid = edge.child_id
        oi = edge.order_index
        pid = edge.parent_id

        if pid not in nodes_by_id:
            raise ValueError(f"Edge parent_id not found in nodes: {pid}")
        if cid not in nodes_by_id:
            raise ValueError(f"Edge child_id not found in nodes: {cid}")

        children_by_parent[pid].append(cid)
        edge_order_index[(pid, cid)] = oi

        if cid in parent_by_child:
            raise ValueError(f"Node has multiple parents: {cid}")

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
        decisions_by_id[d.decision_id] = d.model_dump()

    # 4.
    by_seg: dict[str, list[SegmentDecision]] = defaultdict(list)

    for d in canonical_ir.segment_decisions:
        sid = d.segment_id
        assert sid, f"Missing segment_id for decision_id: {d.decision_id}"
        by_seg[str(sid)].append(d)

    decisions_by_segment_id: dict[str, dict[str, Any]] = {}

    for sid, decisions in by_seg.items():
        ds_sorted = sorted(
            decisions, key=lambda d: (d.confidence, d.decision_id), reverse=True
        )

        # Dump the best decision to a dict.
        decisions_by_segment_id[sid] = ds_sorted[0].model_dump()

    # 5.
    ctx = ExportContext(
        children_by_parent=dict(children_by_parent),
        decisions_by_id=decisions_by_id,
        decisions_by_segment_id=decisions_by_segment_id,
        doc_key=canonical_ir.doc_key,
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
    exclude_keys = {"model", "overwrite"}
    kg_run = RunCtx(
        extra={k: v for k, v in config.model_dump().items() if k not in exclude_keys},
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(fp=output_dir / "kg_run.json", json_info=kg_run)
    logger.info(f"Saving KG creation results to: {kg_dirs}")

    return kg_dirs, kg_run


def stable_text_hash(*, n: int = 12, s: str) -> str:
    """Generate a stable hash from a string.

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

    s = _normalize_ws(s).lower()

    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]
