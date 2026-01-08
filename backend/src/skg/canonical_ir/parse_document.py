# Future Library
from __future__ import annotations

# Standard Library
import re

from dataclasses import dataclass, field
from typing import Any, Optional

# Third Party Library
from loguru import logger

# Package Library
from skg.canonical_ir.schemas import CanonicalEdge, CanonicalIR, CanonicalNode
from skg.canonical_ir.table_specs import TableSpec
from skg.canonical_ir.utils import generate_global_id, normalize_table_grid
from skg.document_ir.schemas import DocumentIR
from skg.page_ir.schemas import TextUnit
from skg.utils.constants import BlockType, StatementRole


@dataclass(frozen=True)
class CanonicalRowIR:
    """Intermediate representation for a single curriculum row."""

    descriptors_raw: Optional[str]
    expectations_raw: Optional[str]
    group: Optional[str]
    provenance: dict[str, Any]
    subject: Optional[str]
    topic: Optional[str]


@dataclass(frozen=True)
class HeadingRule:
    """Match a heading segment's text to a role (with optional stack level)."""

    role: StatementRole
    required_terms: tuple[str, ...] = ()
    level: int | None = None  # Lower = higher in hierarchy (closer to root)
    pattern: str | None = None  # regex

    def matches(self, text: str) -> bool:
        """Check if the heading text matches this rule.

        Parameters
        ----------
        text
            The heading text to check.

        Returns
        -------
        bool
            True if the text matches the rule, False otherwise.
        """

        t = (text or "").casefold()
        if self.required_terms:
            for term in self.required_terms:
                if term.casefold() not in t:
                    return False

        if self.pattern:
            return re.search(self.pattern, text or "", flags=re.IGNORECASE) is not None

        # If no pattern/terms, it matches nothing (avoid accidental always-true rules).
        return False


@dataclass(frozen=True)
class LeafParsingConfig:
    """Deterministic splitting rules for expectation/descriptors."""

    # If provided, should extract list_id/body at *start* of a line.
    # Must use named groups: (?P<list_id>...) and (?P<body>...)
    code_line_regex: str | None = None

    # Bullet stripping (line start).
    bullet_regex: str = r"^\s*(?:[-•*]|\d+[.)]|\([a-zA-Z0-9]+\)|[a-zA-Z][.)])\s+"

    # Split on blank lines into chunks (in addition to bullets/codes).
    split_on_blank_lines: bool = True

    # If a chunk becomes empty after cleaning, drop it.
    drop_empty: bool = True


@dataclass(frozen=True)
class GraphPolicy:
    mode: str = "tree"  # currently only "tree"
    keep_first_parent: bool = True


@dataclass(frozen=True)
class ParseConfig:
    heading_rules: list[HeadingRule] = field(default_factory=list)
    table_specs: list[TableSpec] = field(default_factory=list)
    leaf_parsing: LeafParsingConfig = field(default_factory=LeafParsingConfig)
    graph_policy: GraphPolicy = field(default_factory=GraphPolicy)

    # Default role → stack level (used if rule.level is None, or if no rule matched).
    # lower = higher/outer. This is intentionally coarse and stable.
    role_levels: dict[StatementRole, int] = field(
        default_factory=lambda: {
            StatementRole.FRAMEWORK: 0,
            StatementRole.GRADE_LEVEL: 10,
            StatementRole.SUBJECT: 20,
            StatementRole.STRAND: 30,
            StatementRole.TOPIC: 40,
            StatementRole.SECTION: 50,
            StatementRole.UNRESOLVED: 90,
            StatementRole.EXPECTATION: 100,
            StatementRole.DESCRIPTOR: 110,
        }
    )


# ----------------------------
# Small helpers
# ----------------------------


def _textunit_to_str(tu: TextUnit | dict | str | None) -> str:
    if tu is None:
        return ""
    if isinstance(tu, str):
        return tu
    if isinstance(tu, dict):
        # Prefer original (stable); fallback to English.
        return tu.get("text") or tu.get("text_en") or ""
    # Pydantic model
    return getattr(tu, "text", None) or getattr(tu, "text_en", None) or ""


def _as_textunit(
    value: TextUnit | dict | str | None, *, default_language: str = "und"
) -> TextUnit | None:
    if value is None:
        return None
    if isinstance(value, TextUnit):
        return value
    if isinstance(value, dict):
        # Trust upstream shape; validate into TextUnit
        return TextUnit.model_validate(value)
    if isinstance(value, str):
        return TextUnit(language=default_language, text=value, text_en=None)
    # Fallback: stringify
    return TextUnit(language=default_language, text=str(value), text_en=None)


def _normalize_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _union_bbox(a: list[float] | None, b: list[float] | None) -> list[float] | None:
    if a is None:
        return b
    if b is None:
        return a
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _segment_bbox_union(seg: Any) -> list[float] | None:
    # Block/table segments both expose slices with bbox in your DocumentIR.
    bbox: list[float] | None = None
    for sl in getattr(seg, "slices", []) or []:
        bbox = _union_bbox(bbox, getattr(sl, "bbox", None))
    return bbox


def _segment_page_indices(seg: Any) -> list[int]:
    pages: set[int] = set()
    for sl in getattr(seg, "slices", []) or []:
        pi = getattr(sl, "page_index", None)
        if isinstance(pi, int):
            pages.add(pi)
    return sorted(pages)


def _extract_table_header_texts(segment: Any) -> list[str]:
    """
    Return per-column header strings, derived from segment.header_rows (preferred),
    otherwise from the first header_row_count rows of segment.rows.
    """
    header_rows = getattr(segment, "header_rows", None)
    if not header_rows:
        hrc = int(getattr(segment, "header_row_count", 0) or 0)
        rows = getattr(segment, "rows", []) or []
        header_rows = rows[:hrc] if hrc > 0 else []

    n_cols = int(getattr(segment, "n_cols", 0) or 0)
    cols: list[list[str]] = [[] for _ in range(max(0, n_cols))]

    for r in header_rows or []:
        cells = (
            getattr(r, "cells", None)
            or (r.get("cells") if isinstance(r, dict) else None)
            or []
        )
        # TableRow.cells is a list of TableCell-like
        c_idx = 0
        for cell in cells:
            # cell.text is TextUnit/dict/None
            t = _normalize_space(
                _textunit_to_str(
                    getattr(cell, "text", None)
                    if not isinstance(cell, dict)
                    else cell.get("text")
                )
            )
            if c_idx < len(cols) and t:
                cols[c_idx].append(t)
            # Advance by col_span if present
            cs = (
                getattr(cell, "col_span", 1)
                if not isinstance(cell, dict)
                else cell.get("col_span", 1)
            )
            cs = int(cs or 1)
            c_idx += cs

    return [" | ".join(parts).strip() for parts in cols]


def _pick_heading_role_and_level(
    text: str, cfg: ParseConfig
) -> tuple[StatementRole, int]:
    for rule in cfg.heading_rules:
        if rule.matches(text):
            role = rule.role
            level = (
                rule.level if rule.level is not None else cfg.role_levels.get(role, 50)
            )
            return role, level
    # Default: treat as SECTION
    role = StatementRole.SECTION
    return role, cfg.role_levels.get(role, 50)


@dataclass
class _LeafStmt:
    body: str
    list_id: str | None = None


def _split_leaf_statements(text: str, leaf_cfg: LeafParsingConfig) -> list[_LeafStmt]:
    raw = (text or "").strip()
    if not raw:
        return []

    code_re = re.compile(leaf_cfg.code_line_regex) if leaf_cfg.code_line_regex else None
    bullet_re = re.compile(leaf_cfg.bullet_regex) if leaf_cfg.bullet_regex else None

    lines = raw.splitlines()

    out: list[_LeafStmt] = []
    cur_lines: list[str] = []
    cur_id: str | None = None

    def _flush():
        nonlocal cur_lines, cur_id
        if not cur_lines:
            cur_id = None
            return
        body = _normalize_space("\n".join(cur_lines))
        if bullet_re:
            body = bullet_re.sub("", body, count=1).strip()
        if leaf_cfg.drop_empty and not body:
            cur_lines = []
            cur_id = None
            return
        out.append(_LeafStmt(body=body, list_id=cur_id))
        cur_lines = []
        cur_id = None

    for ln in lines:
        if leaf_cfg.split_on_blank_lines and not ln.strip():
            _flush()
            continue

        if code_re:
            m = code_re.match(ln.strip())
            if m:
                _flush()
                cur_id = (m.groupdict().get("list_id") or "").strip() or None
                body = (m.groupdict().get("body") or "").strip()
                cur_lines = [body] if body else []
                continue

        cur_lines.append(ln.rstrip())

    _flush()
    return out


# ----------------------------
# Builder (dedupe + tree enforcement)
# ----------------------------


class _Builder:
    def __init__(self, *, doc_key: str, pdf_name: str | None):
        self.doc_key = doc_key
        self.pdf_name = pdf_name
        self.nodes_by_id: dict[str, CanonicalNode] = {}
        self.edges_in_order: list[CanonicalEdge] = []
        self.edge_set: set[tuple[str, str, str]] = set()
        self.warnings: list[str] = []
        self.unresolved: list[dict[str, Any]] = []

    def upsert_node(self, node: CanonicalNode) -> None:
        existing = self.nodes_by_id.get(node.node_id)
        if existing is None:
            self.nodes_by_id[node.node_id] = node
            return

        # Merge provenance fields (keep deterministic, additive)
        existing.page_indices = sorted(
            set(existing.page_indices) | set(node.page_indices)
        )
        existing.source_ids = sorted(set(existing.source_ids) | set(node.source_ids))
        existing.bbox = _union_bbox(existing.bbox, node.bbox)

        # Prefer already-set title/body; otherwise fill.
        if existing.title is None and node.title is not None:
            existing.title = node.title
        if existing.body is None and node.body is not None:
            existing.body = node.body

        # role/doc_key/list_id should match by construction; if not, warn.
        if existing.role != node.role:
            self.warnings.append(
                f"Node role mismatch for node_id={node.node_id}: {existing.role} vs {node.role}"
            )

    def add_edge(self, *, parent_id: str, child_id: str, rel: str = "hasChild") -> None:
        key = (parent_id, child_id, rel)
        if key in self.edge_set:
            return
        self.edge_set.add(key)
        self.edges_in_order.append(
            CanonicalEdge(parent_id=parent_id, child_id=child_id, rel=rel)
        )

    def enforce_tree_mode(self, *, keep_first_parent: bool = True) -> None:
        parent_for_child: dict[str, str] = {}
        kept: list[CanonicalEdge] = []
        dropped = 0

        for e in self.edges_in_order:
            prev = parent_for_child.get(e.child_id)
            if prev is None:
                parent_for_child[e.child_id] = e.parent_id
                kept.append(e)
                continue

            if prev == e.parent_id:
                kept.append(e)
                continue

            # Conflict
            dropped += 1
            msg = (
                f"Tree conflict: child_id={e.child_id} had parent_id={prev}, "
                f"conflicting parent_id={e.parent_id} dropped."
            )
            self.warnings.append(msg)
            if not keep_first_parent:
                # Replace parent (rare; not default)
                parent_for_child[e.child_id] = e.parent_id

        self.edges_in_order = kept

        if dropped:
            logger.warning(
                f"Dropped {dropped} conflicting hasChild edges due to tree-mode enforcement."
            )


# ----------------------------
# Main: parse_document()
# ----------------------------


def parse_document(
    *, document_ir: DocumentIR, config: ParseConfig, wizard_mode: bool = False
) -> CanonicalIR:
    """
    Deterministically parse DocumentIR -> CanonicalIR using config-driven heading/table rules.

    This function does not call any LLMs and is intended to be fully repeatable.
    """

    b = _Builder(doc_key=document_ir.doc_key, pdf_name=document_ir.pdf_name)

    # Root framework node
    root_title = _as_textunit(
        document_ir.pdf_name or "Curriculum Framework", default_language="en"
    )
    root_id = generate_global_id(
        code=None,
        doc_key=document_ir.doc_key,
        path=["framework"],
        role=StatementRole.FRAMEWORK,
        text=None,  #  Don't use root_title here to generate stable root ID
    )
    b.upsert_node(
        CanonicalNode(
            node_id=root_id,
            doc_key=document_ir.doc_key,
            role=StatementRole.FRAMEWORK,
            title=root_title,
            body=None,
            list_id=None,
            bbox=None,
            page_indices=[],
            source_ids=[],
        )
    )

    # Context stack of (level, node_id, label_for_path)
    stack: list[tuple[int, str, str]] = [
        (config.role_levels[StatementRole.FRAMEWORK], root_id, "framework")
    ]

    pending_caption_text: str | None = None
    pending_caption_key: str | None = None

    def current_parent_id() -> str:
        return stack[-1][1]

    def current_path_labels() -> list[str]:
        return [lbl for (_, _, lbl) in stack]

    def push_heading(
        *, role: StatementRole, level: int, title_tu: TextUnit, source_seg: Any
    ) -> str:
        # Pop to correct level (same-or-higher pops)
        while stack and stack[-1][0] >= level and stack[-1][1] != root_id:
            stack.pop()

        parent_id = current_parent_id()
        title_str = _normalize_space(_textunit_to_str(title_tu))

        path = current_path_labels() + [f"{role.value}:{title_str}"]
        node_id = generate_global_id(
            code=getattr(source_seg, "local_code", None),
            doc_key=document_ir.doc_key,
            path=path,
            role=role,
            text=title_str,
        )

        node = CanonicalNode(
            node_id=node_id,
            doc_key=document_ir.doc_key,
            role=role,
            title=title_tu,
            body=None,
            list_id=None,
            bbox=_segment_bbox_union(source_seg),
            page_indices=_segment_page_indices(source_seg),
            source_ids=[getattr(source_seg, "segment_key", "unknown")],
        )

        b.upsert_node(node)
        b.add_edge(parent_id=parent_id, child_id=node_id, rel="hasChild")

        stack.append((level, node_id, f"{role.value}:{title_str}"))
        return node_id

    def ensure_group_node(
        *,
        role: StatementRole,
        title_tu: TextUnit,
        parent_id: str,
        extra_path_parts: list[str],
        source_ids: list[str],
        page_index: int | None,
        bbox: list[float] | None,
    ) -> str:
        title_str = _normalize_space(_textunit_to_str(title_tu))
        path = current_path_labels() + extra_path_parts + [f"{role.value}:{title_str}"]
        node_id = generate_global_id(
            code=None,
            doc_key=document_ir.doc_key,
            path=path,
            role=role,
            text=title_str,
        )
        node = CanonicalNode(
            node_id=node_id,
            doc_key=document_ir.doc_key,
            role=role,
            title=title_tu,
            body=None,
            list_id=None,
            bbox=bbox,
            page_indices=[page_index] if isinstance(page_index, int) else [],
            source_ids=source_ids,
        )
        b.upsert_node(node)
        b.add_edge(parent_id=parent_id, child_id=node_id, rel="hasChild")
        return node_id

    def add_leaf_nodes(
        *,
        leaf_parent_id: str,
        role: StatementRole,
        cell_tu: TextUnit,
        table_seg: Any,
        caption_seg_key: str | None,
        row_page_index: int | None,
        row_bbox: list[float] | None,
        split: bool,
    ) -> None:
        raw_text = _normalize_space(_textunit_to_str(cell_tu))
        if not raw_text:
            return

        parts = (
            _split_leaf_statements(raw_text, config.leaf_parsing)
            if split
            else [_LeafStmt(body=raw_text, list_id=None)]
        )
        for i, p in enumerate(parts):
            body_str = _normalize_space(p.body)
            if not body_str:
                continue

            # Construct a TextUnit for the leaf body, preserving language
            leaf_body = TextUnit(language=cell_tu.language, text=body_str, text_en=None)

            path = current_path_labels() + [
                f"{role.value}:{leaf_parent_id}",
                f"leaf:{p.list_id or 'nolist'}:{i}",
            ]
            node_id = generate_global_id(
                code=p.list_id,
                doc_key=document_ir.doc_key,
                path=path,
                role=role,
                text=body_str,
            )

            source_ids = [getattr(table_seg, "segment_key", "unknown")]
            if caption_seg_key:
                source_ids.append(caption_seg_key)

            node = CanonicalNode(
                node_id=node_id,
                doc_key=document_ir.doc_key,
                role=role,
                title=None,
                body=leaf_body,
                list_id=p.list_id,
                bbox=row_bbox,
                page_indices=(
                    [row_page_index] if isinstance(row_page_index, int) else []
                ),
                source_ids=source_ids,
            )
            b.upsert_node(node)
            b.add_edge(parent_id=leaf_parent_id, child_id=node_id, rel="hasChild")

    # Walk segments in order
    for seg in document_ir.segments:
        kind = getattr(seg, "kind", None)

        # ----------------
        # Block segments
        # ----------------
        if kind == "block":
            bt = getattr(seg, "block_type", None)
            bt_str = bt.value if hasattr(bt, "value") else str(bt)

            if bt_str == BlockType.CAPTION.value:
                pending_caption_text = _normalize_space(
                    _textunit_to_str(getattr(seg, "text", None))
                    or getattr(seg, "combined_text", "")
                    or ""
                )
                pending_caption_key = getattr(seg, "segment_key", None)
                continue

            # One-shot caption binding: caption must be immediately followed by a table
            # segment.
            if pending_caption_text is not None and bt_str != BlockType.ARTIFACT.value:
                b.warnings.append(
                    f"Caption not followed by table; clearing pending caption "
                    f"(caption_seg={pending_caption_key}, next_block_type={bt_str})."
                )
                pending_caption_text = None
                pending_caption_key = None

            if bt_str == BlockType.HEADING.value:
                title_tu = _as_textunit(
                    getattr(seg, "text", None) or getattr(seg, "combined_text", None),
                    default_language="und",
                )
                title_str = _normalize_space(_textunit_to_str(title_tu))
                role, level = _pick_heading_role_and_level(title_str, config)
                # Push heading node
                push_heading(
                    role=role,
                    level=level,
                    title_tu=title_tu
                    or TextUnit(language="und", text=title_str, text_en=None),
                    source_seg=seg,
                )
                continue

            # Other block types: ignore in Step 4 (guidance later)
            continue

        # ----------------
        # Table segments
        # ----------------
        if kind == "table":
            caption_text = pending_caption_text or ""
            caption_key = pending_caption_key
            pending_caption_text = None
            pending_caption_key = None

            row_facts_sample: list[dict[str, Any]] = []
            MAX_ROW_FACTS = 10

            header_texts = _extract_table_header_texts(seg)
            local_code = getattr(seg, "local_code", None)

            chosen: TableSpec | None = None
            for spec in config.table_specs:
                if spec.match(
                    caption_text=caption_text,
                    header_texts=header_texts,
                    local_code=local_code,
                ):
                    chosen = spec
                    break

            if chosen is None:
                payload = {
                    "issue": "no_table_spec_matched",
                    "segment_key": getattr(seg, "segment_key", None),
                    "local_code": local_code,
                    "caption_text": caption_text,
                    "header_texts_by_col": header_texts,
                    "context_path": current_path_labels(),
                }
                if wizard_mode:
                    # Include a few sample rows for config generation
                    try:
                        rows = getattr(seg, "rows", []) or []
                        payload["sample_rows"] = rows[:5]
                    except Exception:
                        payload["sample_rows"] = None
                b.unresolved.append(payload)
                b.warnings.append(
                    f"No TableSpec matched table segment_key={getattr(seg, 'segment_key', None)} local_code={local_code}"
                )
                continue

            # Normalize the grid (Option B: returns NormalizedRow with TextUnit cells + row provenance)
            forward_fill_cols = list(getattr(chosen, "forward_fill_cols", ()) or ())
            norm_rows = normalize_table_grid(
                forward_fill_cols=forward_fill_cols, segment=seg
            )

            header_row_count = int(getattr(seg, "header_row_count", 0) or 0)
            subject_col = getattr(chosen, "subject_col", None)
            group_col = getattr(chosen, "group_col", None)
            expectation_col = getattr(chosen, "expectation_col", None)

            # Optional extensions: topic_col, descriptor_col
            topic_col = getattr(chosen, "topic_col", None)
            descriptor_col = getattr(chosen, "descriptor_col", None)

            if expectation_col is None:
                b.unresolved.append(
                    {
                        "issue": "table_spec_missing_expectation_col",
                        "segment_key": getattr(seg, "segment_key", None),
                        "table_spec": chosen.name,
                        "header_texts_by_col": header_texts,
                        "context_path": current_path_labels(),
                    }
                )
                b.warnings.append(
                    f"TableSpec '{chosen.name}' has no expectation_col; skipping."
                )
                continue

            # Per-table running grouping state
            last_subject: str | None = None
            last_group: str | None = None
            last_topic: str | None = None

            # Node ids for current grouping chain under current heading context
            current_subject_id: str | None = None
            current_group_id: str | None = None
            current_topic_id: str | None = None

            base_parent_id = current_parent_id()
            base_source_ids = [getattr(seg, "segment_key", "unknown")]
            if caption_key:
                base_source_ids.append(caption_key)

            for r_idx, nr in enumerate(norm_rows):
                if r_idx < header_row_count:
                    continue

                # Option B row provenance fields (assumed available per your instruction)
                row_page_index = getattr(nr, "provenance_page_index", None)
                row_bbox = getattr(nr, "provenance_bbox", None)

                def cell_at(col: int | None) -> TextUnit | None:
                    if col is None:
                        return None
                    cells = getattr(nr, "cells", [])
                    if col < 0 or col >= len(cells):
                        return None
                    return _as_textunit(cells[col], default_language="und")

                subj_tu = cell_at(subject_col)
                group_tu = cell_at(group_col)
                topic_tu = cell_at(topic_col)

                subj_str = _normalize_space(_textunit_to_str(subj_tu))
                group_str = _normalize_space(_textunit_to_str(group_tu))
                topic_str = _normalize_space(_textunit_to_str(topic_tu))

                # Rebuild grouping nodes when values change (or first time)
                if subj_str and subj_str != last_subject:
                    current_subject_id = ensure_group_node(
                        role=StatementRole.SUBJECT,
                        title_tu=subj_tu
                        or TextUnit(language="und", text=subj_str, text_en=None),
                        parent_id=base_parent_id,
                        extra_path_parts=[f"table:{chosen.name}"],
                        source_ids=base_source_ids,
                        page_index=row_page_index,
                        bbox=row_bbox,
                    )
                    current_group_id = None
                    current_topic_id = None
                    last_subject = subj_str
                    last_group = None
                    last_topic = None

                group_parent = current_subject_id or base_parent_id
                if group_str and group_str != last_group:
                    current_group_id = ensure_group_node(
                        role=StatementRole.STRAND,
                        title_tu=group_tu
                        or TextUnit(language="und", text=group_str, text_en=None),
                        parent_id=group_parent,
                        extra_path_parts=[
                            f"table:{chosen.name}",
                            f"subject:{last_subject or 'none'}",
                        ],
                        source_ids=base_source_ids,
                        page_index=row_page_index,
                        bbox=row_bbox,
                    )
                    current_topic_id = None
                    last_group = group_str
                    last_topic = None

                topic_parent = current_group_id or group_parent
                if topic_str and topic_str != last_topic:
                    current_topic_id = ensure_group_node(
                        role=StatementRole.TOPIC,
                        title_tu=topic_tu
                        or TextUnit(language="und", text=topic_str, text_en=None),
                        parent_id=topic_parent,
                        extra_path_parts=[
                            f"table:{chosen.name}",
                            f"subject:{last_subject or 'none'}",
                            f"group:{last_group or 'none'}",
                        ],
                        source_ids=base_source_ids,
                        page_index=row_page_index,
                        bbox=row_bbox,
                    )
                    last_topic = topic_str

                leaf_parent_id = (
                    current_topic_id
                    or current_group_id
                    or current_subject_id
                    or base_parent_id
                )

                exp_tu = cell_at(expectation_col)
                desc_tu = (
                    cell_at(descriptor_col) if descriptor_col is not None else None
                )

                if wizard_mode and len(row_facts_sample) < MAX_ROW_FACTS:
                    exp_raw = (
                        _normalize_space(_textunit_to_str(exp_tu)) if exp_tu else None
                    )
                    desc_raw = (
                        _normalize_space(_textunit_to_str(desc_tu)) if desc_tu else None
                    )
                    row_facts_sample.append(
                        CanonicalRowIR(
                            subject=subj_str,
                            group=group_str,
                            topic=topic_str,
                            expectations_raw=exp_raw,
                            descriptors_raw=desc_raw,
                            provenance={
                                "segment_key": getattr(seg, "segment_key", None),
                                "local_code": local_code,
                                "caption_text": caption_text,
                                "row_index": nr.row_index,
                                "original_row_index": nr.original_row_index,
                                "page_index": nr.provenance_page_index,
                                "bbox": nr.provenance_bbox,
                                "slice_index": nr.provenance_slice_index,
                            },
                        ).__dict__
                    )

                if exp_tu is not None:
                    add_leaf_nodes(
                        leaf_parent_id=leaf_parent_id,
                        role=StatementRole.EXPECTATION,
                        cell_tu=exp_tu,
                        table_seg=seg,
                        caption_seg_key=caption_key,
                        row_page_index=row_page_index,
                        row_bbox=row_bbox,
                        split=bool(getattr(chosen, "split_expectations", True)),
                    )

                if descriptor_col is not None:
                    desc_tu = cell_at(descriptor_col)
                    if desc_tu is not None:
                        add_leaf_nodes(
                            leaf_parent_id=leaf_parent_id,
                            role=StatementRole.DESCRIPTOR,
                            cell_tu=desc_tu,
                            table_seg=seg,
                            caption_seg_key=caption_key,
                            row_page_index=row_page_index,
                            row_bbox=row_bbox,
                            split=True,
                        )

            if wizard_mode and row_facts_sample:
                b.unresolved.append(
                    {
                        "issue": "table_row_facts_sample",
                        "segment_key": getattr(seg, "segment_key", None),
                        "local_code": local_code,
                        "caption_text": caption_text,
                        "header_texts_by_col": header_texts,
                        "context_path": current_path_labels(),
                        "table_spec": getattr(chosen, "name", None),
                        "sample_row_facts": row_facts_sample,
                    }
                )

            continue

        # Unknown segment kind
        b.warnings.append(f"Unknown segment kind: {kind}")

    # Graph hygiene
    if config.graph_policy.mode == "tree":
        b.enforce_tree_mode(keep_first_parent=config.graph_policy.keep_first_parent)

    canonical = CanonicalIR(
        doc_key=document_ir.doc_key,
        pdf_name=document_ir.pdf_name,
        root_id=root_id,
        nodes=list(b.nodes_by_id.values()),
        edges=b.edges_in_order,
        warnings=b.warnings,
        unresolved=b.unresolved,
    )
    return canonical
