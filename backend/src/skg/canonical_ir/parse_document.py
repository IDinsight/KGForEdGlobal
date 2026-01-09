"""This module contains the configuration and state management for parsing a DocumentIR
into a CanonicalIR.
"""

# Standard Library
from typing import Any

# Third Party Library
from loguru import logger

# Package Library
from skg.canonical_ir.schemas import (
    BlockSpec,
    CanonicalEdge,
    CanonicalIR,
    CanonicalNode,
    CanonicalRowIR,
    LeafStatement,
    ParserConfig,
    TableSpec,
)
from skg.canonical_ir.utils import (
    _as_textunit,
    _block_text_for_matching,
    _cell_at,
    _coerce_text_to_str,
    _extract_table_header_texts,
    _normalize_space,
    _pick_heading_role_and_level,
    _segment_bbox_union,
    _segment_page_indices,
    _split_leaf_statements,
    _stable_list_item_key,
    _stable_table_identity,
    _table_is_contentful,
    _table_preview_rows,
    _union_bbox,
    generate_global_id,
    normalize_table_grid,
)
from skg.document_ir.schemas import DocumentIR
from skg.page_ir.schemas import TextUnit
from skg.utils.constants import BlockType, StatementRole


class CanonicalIRBuilder:
    """Stateful helper for constructing the CanonicalIR graph.

    Manages the collection of nodes and edges, handles ID conflicts by merging
    provenance, and enforces tree policies.
    """

    def __init__(self, *, doc_key: str, pdf_name: str) -> None:
        """

        Parameters
        ----------
        doc_key
            The unique identifier for the document.
        pdf_name
            The filename of the source PDF.
        """

        self.doc_key = doc_key
        self.edge_set: set[tuple[str, str, str]] = set()
        self.edges_in_order: list[CanonicalEdge] = []
        self.nodes_by_id: dict[str, CanonicalNode] = {}
        self.pdf_name = pdf_name
        self.unresolved: list[dict[str, Any]] = []
        self.warnings: list[str] = []

    def _node_summary(self, node_id: str) -> dict[str, Any]:
        """Generate a summary dictionary for a node by ID.

        Parameters
        ----------
        node_id
            The ID of the node to summarize.

        Returns
        -------
        dict[str, Any]
            A summary dictionary containing node details.
        """

        n = self.nodes_by_id.get(node_id)

        if n is None:
            return {"node_id": node_id}

        return {
            "node_id": node_id,
            "role": getattr(n.role, "value", str(n.role)),
            "page_indices": list(getattr(n, "page_indices", []) or []),
            "source_ids": list(getattr(n, "source_ids", []) or []),
            "bbox": getattr(n, "bbox", None),
        }

    def add_edge(self, *, child_id: str, parent_id: str) -> None:
        """Add a hasChild (containment) edge between two canonical nodes.

        Parameters
        ----------
        child_id
            The ID of the child node.
        parent_id
            The ID of the parent node.
        """

        rel = "hasChild"
        key = (parent_id, child_id, rel)
        if key in self.edge_set:
            return

        self.edge_set.add(key)
        self.edges_in_order.append(
            CanonicalEdge(child_id=child_id, parent_id=parent_id, rel=rel)
        )

    def enforce_tree_mode(self, *, keep_first_parent: bool = True) -> None:
        """Enforce a strict tree topology by resolving multi-parent conflicts.

        Parameters
        ----------
        keep_first_parent
            If True, keeps the first edge seen for a child and drops subsequent edges
            from other parents. If False, keeps the last edge.
        """

        dropped = 0
        kept: list[CanonicalEdge] = []
        parent_for_child: dict[str, str] = {}
        kept_index_for_child: dict[str, int] = {}

        for e in self.edges_in_order:
            prev_parent = parent_for_child.get(e.child_id)

            if prev_parent is None:
                parent_for_child[e.child_id] = e.parent_id
                kept_index_for_child[e.child_id] = len(kept)
                kept.append(e)
                continue

            if prev_parent == e.parent_id:
                kept.append(e)
                continue

            # Conflict detected: multiple parents for the same child node.
            dropped += 1
            if keep_first_parent:
                self.warnings.append(
                    f"Tree conflict: child_id={e.child_id} had parent_id={prev_parent}, "
                    f"conflicting parent_id={e.parent_id} dropped."
                )
                self.unresolved.append(
                    {
                        "issue": "tree_conflict",
                        "policy": "keep_first_parent",
                        "child": self._node_summary(e.child_id),
                        "kept_parent": self._node_summary(prev_parent),
                        "dropped_parent": self._node_summary(e.parent_id),
                    }
                )
                continue

            # Keep last parent: replace the previously kept edge for this child node.
            old_idx = kept_index_for_child[e.child_id]
            old_edge = kept[old_idx]
            kept[old_idx] = e
            parent_for_child[e.child_id] = e.parent_id
            self.warnings.append(
                f"Tree conflict: child_id={e.child_id} parent changed "
                f"{old_edge.parent_id} -> {e.parent_id} (kept last)."
            )
            self.unresolved.append(
                {
                    "issue": "tree_conflict",
                    "policy": "keep_last_parent",
                    "child": self._node_summary(e.child_id),
                    "kept_parent": self._node_summary(e.parent_id),
                    "dropped_parent": self._node_summary(old_edge.parent_id),
                }
            )

        self.edges_in_order = kept

        # Keep internal de-dupe state consistent with the enforced edge list.
        self.edge_set = {(e.parent_id, e.child_id, e.rel) for e in self.edges_in_order}

        if dropped:
            logger.warning(
                f"Dropped {dropped} conflicting hasChild edges due to tree-mode enforcement."
            )

    def upsert_node(self, *, node: CanonicalNode) -> None:
        """Insert a node or merge it with an existing one if the ID exists.

        If a node with the same ``node_id`` exists, this method merges:
            - `page_indices` (union)
            - `source_ids` (union)
            - `bbox` (union/bounding box of both)
            - `title`/`body` (updates if existing is None)

        Parameters
        ----------
        node
            The node to insert or merge.
        """

        existing = self.nodes_by_id.get(node.node_id)
        if existing is None:
            self.nodes_by_id[node.node_id] = node
            return

        existing.page_indices = sorted(
            set(existing.page_indices) | set(node.page_indices)
        )
        existing.source_ids = sorted(set(existing.source_ids) | set(node.source_ids))
        existing.bbox = _union_bbox(existing.bbox, node.bbox)

        if existing.title is None and node.title is not None:
            existing.title = node.title
        if existing.body is None and node.body is not None:
            existing.body = node.body

        if existing.role != node.role:
            self.warnings.append(
                f"Node role mismatch for node_id={node.node_id}: "
                f"{existing.role} vs. {node.role}"
            )


class DocumentParser:
    """Encapsulates logic and state for parsing a document into CanonicalIR."""

    def __init__(
        self,
        *,
        config: ParserConfig,
        document_ir: DocumentIR,
        wizard_mode: bool = False,
    ) -> None:
        """

        Parameters
        ----------
        config
            The parsing configuration.
        document_ir
            The input DocumentIR to parse.
        wizard_mode
            If True, enables additional logging and unresolved captures for
            debugging/validation.
        """

        assert (
            config.graph_policy.mode == "tree"
        ), f"Unsupported graph mode: {config.graph_policy.mode}"

        self.config = config
        self.doc_ir = document_ir
        self.wizard_mode = wizard_mode

        self.builder = CanonicalIRBuilder(
            doc_key=document_ir.doc_key, pdf_name=document_ir.pdf_name
        )

        # Hierarchy stack: (level, node_id, label_for_path).
        self.root_id: str = ""
        self.stack: list[tuple[int, str, str]] = []

        # Caption handling state.
        self.pending_caption_gap_remaining: int = 0
        self.pending_caption_key: str | None = None
        self.pending_caption_text: str | None = None

    def _initialize_root(self) -> None:
        """Create the root framework node and initialize the stack."""

        root_title = _as_textunit(
            default_language="en",
            value=self.doc_ir.pdf_name or "Curriculum Framework",
        )
        self.root_id = generate_global_id(
            code=None,
            doc_key=self.doc_ir.doc_key,
            path=["framework"],
            role=StatementRole.FRAMEWORK,
            text=None,
        )
        self.builder.upsert_node(
            node=CanonicalNode(
                bbox=None,
                body=None,
                doc_key=self.doc_ir.doc_key,
                list_id=None,
                node_id=self.root_id,
                page_indices=[],
                role=StatementRole.FRAMEWORK,
                source_ids=[],
                title=root_title,
            )
        )
        self.stack = [
            (
                self.config.role_levels[StatementRole.FRAMEWORK],
                self.root_id,
                "framework",
            )
        ]

    def _process_segment(self, seg: Any) -> None:
        """Dispatch a single document segment to the appropriate handler.

        Parameters
        ----------
        seg
            The document segment to process.
        """

        kind = getattr(seg, "kind", None)

        if kind == "block":
            self._handle_block_segment(seg)
        elif kind == "table":
            self._handle_table_segment(seg)
        else:
            self.builder.warnings.append(f"Unknown segment kind: {kind}")

    def parse(self) -> CanonicalIR:
        """Execute the parsing process.

        Returns
        -------
        CanonicalIR
            The fully constructed canonical representation.
        """

        self._initialize_root()

        for seg in self.doc_ir.segments:
            self._process_segment(seg)

        if self.config.graph_policy.mode == "tree":
            self.builder.enforce_tree_mode(
                keep_first_parent=self.config.graph_policy.keep_first_parent
            )

        return CanonicalIR(
            doc_key=self.doc_ir.doc_key,
            edges=self.builder.edges_in_order,
            nodes=sorted(self.builder.nodes_by_id.values(), key=lambda n: n.node_id),
            pdf_name=self.doc_ir.pdf_name,
            root_id=self.root_id,
            unresolved=self.builder.unresolved,
            warnings=self.builder.warnings,
        )

    # BLOCK HANDLING
    def _capture_unmatched_block(
        self, *, seg: Any, bt_str: str, block_text: str
    ) -> None:
        """Log unmatched blocks to the wizard output if configured.

        Parameters
        ----------
        block_text
            The extracted text content of the block.
        bt_str
            The block type string.
        seg
            The block segment to process.
        """

        if not (self.wizard_mode and self.config.capture_unmatched_blocks_in_wizard):
            return

        if bt_str in (
            BlockType.ARTIFACT.value,
            BlockType.CAPTION.value,
            BlockType.HEADING.value,
        ):
            return

        preview = block_text.strip()
        if len(preview) >= self.config.unmatched_block_min_chars:
            self.builder.unresolved.append(
                {
                    "issue": "unmatched_block",
                    "segment_key": getattr(seg, "segment_key", None),
                    "block_type": bt_str,
                    "local_code": getattr(seg, "local_code", None),
                    "context_path": self._current_path_labels(),
                    "preview": preview[: self.config.unmatched_block_max_chars],
                }
            )

    def _capture_unmatched_heading(self, *, heading_text: str, seg: Any) -> None:
        """Log unmatched headings to the wizard output if configured.

        Parameters
        ----------
        heading_text
            The text content of the heading.
        seg
            The heading segment to process.
        """

        if not (self.wizard_mode and self.config.capture_unmatched_headings_in_wizard):
            return

        preview = (heading_text or "").strip()

        if not preview:
            return

        self.builder.unresolved.append(
            {
                "issue": "unmatched_heading_rule",
                "segment_key": getattr(seg, "segment_key", None),
                "block_type": BlockType.HEADING.value,
                "local_code": getattr(seg, "local_code", None),
                "context_path": self._current_path_labels(),
                "preview": preview[: self.config.unmatched_block_max_chars],
            }
        )

    def _handle_block_segment(self, seg: Any) -> None:
        """Handle 'block' type segments (headings, paragraphs, lists, captions).

        Parameters
        ----------
        seg
            The block segment to process.
        """

        bt = getattr(seg, "block_type", None)
        bt_str = str(bt.value) if bt is not None and hasattr(bt, "value") else str(bt)

        if bt_str == BlockType.ARTIFACT.value:
            return  # Explicitly skip artifacts

        # Handle captions.
        if bt_str == BlockType.CAPTION.value:
            self.pending_caption_text = _normalize_space(
                _coerce_text_to_str(getattr(seg, "text", None))
                or getattr(seg, "combined_text", "")
                or ""
            )
            self.pending_caption_key = getattr(seg, "segment_key", None)
            self.pending_caption_gap_remaining = (
                self.config.caption_to_table_max_gap_blocks
            )
            return

        # Clear (or keep) pending caption if we see intervening non-table blocks. Many
        # PDFs include small interstitial blocks (e.g., "continued", short headings)
        # between a caption and the table it describes. We tolerate a small gap
        # deterministically.
        if self.pending_caption_text is not None and bt_str != BlockType.ARTIFACT.value:
            interstitial = _normalize_space(
                _block_text_for_matching(bt_str=bt_str, seg=seg)
            )
            is_small_gap = (
                bt_str
                in (
                    BlockType.HEADING.value,
                    BlockType.PARAGRAPH.value,
                    BlockType.LIST.value,
                )
                and len(interstitial) <= self.config.caption_to_table_max_gap_chars
            )
            if is_small_gap and self.pending_caption_gap_remaining > 0:
                self.pending_caption_gap_remaining -= 1
            else:
                self.builder.warnings.append(
                    f"Caption not followed by table; clearing pending caption "
                    f"(caption_seg={self.pending_caption_key}, next_block_type={bt_str})."
                )
                self.pending_caption_text = None
                self.pending_caption_key = None
                self.pending_caption_gap_remaining = 0

        # Handle headings.
        if bt_str == BlockType.HEADING.value:
            self._handle_heading(seg)
            return

        # Handle content blocks (paragraphs, lists).
        self._handle_generic_block(bt_str=bt_str, seg=seg)

    def _handle_generic_block(self, *, bt_str: str, seg: Any) -> None:
        """Match and process paragraphs, lists, etc. against `BlockSpecs`.

        Parameters
        ----------
        bt_str
            The block type string.
        seg
            The block segment to process.
        """

        block_text = _block_text_for_matching(bt_str=bt_str, seg=seg)
        matched: BlockSpec | None = None

        for spec in self.config.block_specs or []:
            ctx_titles = self._context_titles(scope=spec.context_scope)
            if spec.matches(
                block_type=bt_str, block_text=block_text, context_titles=ctx_titles
            ):
                matched = spec
                break

        if matched:
            self._process_matched_block(
                bt_str=bt_str, block_text=block_text, matched=matched, seg=seg
            )
        else:
            self._capture_unmatched_block(block_text=block_text, bt_str=bt_str, seg=seg)

    def _handle_heading(self, seg: Any) -> None:
        """Process a heading segment, updating the hierarchy stack.

        Parameters
        ----------
        seg
            The heading segment to process.
        """

        title_tu = _as_textunit(
            default_language="und",
            value=getattr(seg, "text", None) or getattr(seg, "combined_text", None),
        )
        title_str = _normalize_space(_coerce_text_to_str(title_tu))

        role, level, unique, matched = _pick_heading_role_and_level(
            cfg=self.config, text=title_str
        )

        if not matched:
            # Do not silently invent heading semantics; surface it in wizard output. In
            # addition, do not override uniqueness here; fallback policy is decided
            # centrally.
            self._capture_unmatched_heading(heading_text=title_str, seg=seg)

        self._push_heading(
            level=level,
            role=role,
            source_seg=seg,
            title_tu=title_tu or TextUnit(language="und", text=title_str, text_en=None),
            unique_per_occurrence=unique,
        )

    def _process_list_items(
        self,
        *,
        bbox: list[float] | None,
        matched: BlockSpec,
        page_indices: list[int],
        parent_id: str,
        seg: Any,
    ) -> None:
        """Process items within a LIST block.

        Parameters
        ----------
        bbox
            The bounding box covering the entire list segment.
        matched
            The matched BlockSpec.
        page_indices
            The page indices covered by the segment.
        parent_id
            The parent node ID to attach list item nodes to.
        seg
            The block segment to process.
        """

        items = getattr(seg, "list_items", None) or []
        seen: dict[str, int] = {}

        for li in items:
            marker = (
                getattr(li, "marker", None)
                if not isinstance(li, dict)
                else li.get("marker")
            )
            tu = (
                getattr(li, "text", None)
                if not isinstance(li, dict)
                else li.get("text")
            )
            body_str = _normalize_space(_coerce_text_to_str(tu))

            if not body_str:
                continue

            seg_key = getattr(seg, "segment_key", "unknown")
            base_key = _stable_list_item_key(marker=marker, body=body_str)
            n_seen = seen.get(base_key, 0)
            seen[base_key] = n_seen + 1
            stable_key = base_key if n_seen == 0 else f"{base_key}-{n_seen}"

            path = (
                self._current_path_labels()
                + [f"block:list:{seg_key}"]
                + [f"{matched.role.value}:{marker or 'nolist'}:{stable_key}"]
            )

            node_id = generate_global_id(
                code=marker,
                doc_key=self.doc_ir.doc_key,
                path=path,
                role=matched.role,
                text=body_str,
            )

            tu_obj = _as_textunit(default_language="und", value=tu)
            if tu_obj:
                body_str2 = _normalize_space(_coerce_text_to_str(tu_obj))
                if body_str2:
                    self.builder.upsert_node(
                        node=CanonicalNode(
                            bbox=bbox,
                            body=TextUnit(
                                language=tu_obj.language,
                                text=body_str2,
                                text_en=tu_obj.text_en,
                            ),
                            doc_key=self.doc_ir.doc_key,
                            list_id=marker,
                            page_indices=page_indices,
                            node_id=node_id,
                            role=matched.role,
                            source_ids=[seg_key],
                            title=None,
                        )
                    )
                    self.builder.add_edge(child_id=node_id, parent_id=parent_id)

    def _process_matched_block(
        self, *, bt_str: str, block_text: str, matched: BlockSpec, seg: Any
    ) -> None:
        """Materialize nodes for a successfully matched block.

        Parameters
        ----------
        block_text
            The extracted text content of the block.
        bt_str
            The block type string.
        matched
            The matched BlockSpec.
        seg
            The block segment to process.
        """

        parent_id = self._current_parent_id()
        bbox = _segment_bbox_union(seg)
        page_indices = _segment_page_indices(seg)

        if bt_str == BlockType.PARAGRAPH.value:
            tu = _as_textunit(
                default_language="und", value=getattr(seg, "text", None) or block_text
            )
            if tu is None:
                if self.wizard_mode and self.config.capture_unmatched_blocks_in_wizard:
                    self.builder.unresolved.append(
                        {
                            "issue": "paragraph_missing_textunit",
                            "segment_key": getattr(seg, "segment_key", None),
                            "block_type": bt_str,
                            "context_path": self._current_path_labels(),
                        }
                    )
                return

            self._add_leaf_nodes(
                caption_seg_key=None,
                cell_tu=tu,
                leaf_parent_id=parent_id,
                leaf_scope_path=[
                    f"block:{bt_str}",
                    f"seg:{getattr(seg, 'segment_key', 'unknown')}",
                ],
                role=matched.role,
                row_bbox=bbox,
                row_page_index=(page_indices[0] if page_indices else None),
                split=matched.split,
                table_seg=seg,
            )
            return
        if bt_str == BlockType.LIST.value:
            self._process_list_items(
                bbox=bbox,
                matched=matched,
                page_indices=page_indices,
                parent_id=parent_id,
                seg=seg,
            )
            return

        # If we matched a BlockSpec but don't materialize this block type, allow
        # wizard-mode capture instead of silently dropping.
        self._capture_unmatched_block(block_text=block_text, bt_str=bt_str, seg=seg)

    # TABLE HANDLING
    def _check_table_results(
        self,
        *,
        caption_text: str,
        header_texts: list[str],
        local_code: str | None,
        seg: Any,
        spec: TableSpec,
        state: dict,
    ) -> None:
        """Verify table results and log wizard diagnostics if configured.

        Parameters
        ----------
        caption_text
            The text of the table caption.
        header_texts
            The list of header texts extracted from the table.
        local_code
            The local code associated with the table segment.
        seg
            The table segment to process.
        spec
            The matched TableSpec.
        state
            The processing state containing counts and samples.
        """

        problem = state["expectations_added"] == 0

        if (
            self.wizard_mode
            and state["row_facts_sample"]
            and (problem or self.config.capture_table_row_facts_sample_always)
        ):
            self.builder.unresolved.append(
                {
                    "issue": "table_row_facts_sample",
                    "segment_key": getattr(seg, "segment_key", None),
                    "local_code": local_code,
                    "caption_text": caption_text,
                    "header_texts_by_col": header_texts,
                    "context_path": self._current_path_labels(),
                    "table_spec": spec.name,
                    "expectations_added": state["expectations_added"],
                    "descriptors_added": state["descriptors_added"],
                    "sample_row_facts": [
                        r.model_dump() for r in state["row_facts_sample"]
                    ],
                }
            )

        if self.wizard_mode and problem:
            self.builder.unresolved.append(
                {
                    "issue": "table_matched_but_no_expectations",
                    "segment_key": getattr(seg, "segment_key", None),
                    "local_code": local_code,
                    "caption_text": caption_text,
                    "header_texts_by_col": header_texts,
                    "context_path": self._current_path_labels(),
                    "table_spec": spec.name,
                    "preview_rows": _table_preview_rows(
                        max_cols=12, max_rows=6, seg=seg
                    ),
                    "expectations_added": state["expectations_added"],
                    "descriptors_added": state["descriptors_added"],
                }
            )
            self.builder.warnings.append(
                f"Table matched spec '{spec.name}' but produced 0 expectations "
                f"(segment_key={getattr(seg, 'segment_key', None)} local_code={local_code})"
            )

    def _handle_table_segment(self, seg: Any) -> None:
        """Process a table segment: match spec, normalize, and extract rows.

        Parameters
        ----------
        seg
            The table segment to process.
        """

        caption_text = self.pending_caption_text or ""
        caption_key = self.pending_caption_key

        header_texts = _extract_table_header_texts(seg)
        local_code = getattr(seg, "local_code", None)

        spec = self._match_table_spec(
            caption_text=caption_text, header_texts=header_texts, local_code=local_code
        )

        if not spec:
            self._handle_unmatched_table(
                caption_text=caption_text,
                header_texts=header_texts,
                local_code=local_code,
                seg=seg,
            )
            return

        if getattr(spec, "ignore", False):
            if self.wizard_mode:
                self.builder.unresolved.append(
                    {
                        "issue": "table_matched_but_ignored",
                        "segment_key": getattr(seg, "segment_key", None),
                        "table_spec": spec.name,
                        "local_code": local_code,
                        "caption_text": caption_text,
                        "header_texts_by_col": header_texts,
                        "context_path": self._current_path_labels(),
                    }
                )
            return

        if getattr(spec, "expectation_col", None) is None:
            if self.wizard_mode:
                self.builder.unresolved.append(
                    {
                        "issue": "table_spec_missing_expectation_col",
                        "segment_key": getattr(seg, "segment_key", None),
                        "table_spec": spec.name,
                        "header_texts_by_col": header_texts,
                        "context_path": self._current_path_labels(),
                    }
                )
            self.builder.warnings.append(
                f"TableSpec '{spec.name}' has no expectation_col; skipping."
            )
            return

        # Consume pending caption.
        self.pending_caption_gap_remaining = 0
        self.pending_caption_key = None
        self.pending_caption_text = None

        table_identity = _stable_table_identity(
            caption_text=caption_text,
            header_texts=header_texts,
            local_code=local_code,
            segment_key=getattr(seg, "segment_key", None),
        )

        self._process_table_rows(
            caption_key=caption_key,
            caption_text=caption_text,
            local_code=local_code,
            seg=seg,
            spec=spec,
            table_identity=table_identity,
        )

    def _handle_unmatched_table(
        self,
        *,
        caption_text: str,
        header_texts: list[str],
        local_code: str | None,
        seg: Any,
    ) -> None:
        """Log unmatched table info to the builder.

        Parameters
        ----------
        caption_text
            The text of the table caption.
        header_texts
            The list of header texts extracted from the table.
        seg
            The table segment to process.
        """

        self.builder.warnings.append(
            f"No TableSpec matched table segment_key={getattr(seg, 'segment_key', None)} "
            f"local_code={local_code}"
        )

        if (
            self.wizard_mode
            and self.config.capture_unmatched_tables_in_wizard
            and _table_is_contentful(cfg=self.config, seg=seg)
        ):
            self.builder.unresolved.append(
                {
                    "issue": "no_table_spec_matched",
                    "segment_key": getattr(seg, "segment_key", None),
                    "local_code": local_code,
                    "caption_text": caption_text,
                    "header_texts_by_col": header_texts,
                    "context_path": self._current_path_labels(),
                    "preview_rows": _table_preview_rows(
                        max_cols=12, max_rows=6, seg=seg
                    ),
                }
            )

    def _match_table_spec(
        self, *, caption_text: str, header_texts: list[str], local_code: str | None
    ) -> TableSpec | None:
        """Find the first matching TableSpec from the configuration.

        Parameters
        ----------
        caption_text
            The text of the table caption.
        header_texts
            The list of header texts extracted from the table.
        local_code
            The local code associated with the table segment.

        Returns
        -------
        TableSpec | None
            The matched TableSpec, or None if no match was found.
        """

        for spec in self.config.table_specs:
            if spec.match(
                caption_text=caption_text,
                header_texts=header_texts,
                local_code=local_code,
            ):
                return spec

        return None

    def _process_single_row(
        self,
        *,
        base_parent_id: str,
        base_source_ids: list[str],
        caption_key: str | None,
        caption_text: str,
        local_code: str | None,
        nr: Any,
        r_idx: int,
        seg: Any,
        spec: TableSpec,
        state: dict,
    ) -> None:
        """Handle logic for a single row: hierarchy updates and leaf extraction.

        Parameters
        ----------
        base_parent_id
            The base parent ID for the table.
        base_source_ids
            The base source IDs for the table.
        caption_key
            The segment key of the caption, if any.
        caption_text
            The text of the table caption, if any.
        local_code
            The local code of the table, if any.
        nr
            The normalized row object.
        r_idx
            The row index within the normalized table.
        seg
            The table segment containing the row.
        spec
            The TableSpec for the table.
        """

        row_page_index = getattr(nr, "provenance_page_index", None)
        row_bbox = getattr(nr, "provenance_bbox", None)

        # Update Hierarchy (Subject -> Group -> Topic).
        self._update_row_hierarchy(
            base_parent_id=base_parent_id,
            base_source_ids=base_source_ids,
            nr=nr,
            row_bbox=row_bbox,
            row_page_index=row_page_index,
            spec=spec,
            state=state,
        )

        # Extract Leaves (Expectations/Descriptors).
        leaf_parent_id = (
            state["curr_topic_id"]
            or state["curr_group_id"]
            or state["curr_subject_id"]
            or base_parent_id
        )

        leaf_scope_path = [
            f"table:{state.get('table_identity', 'unknown')}",
            f"subject:{(state['last_subject'] or 'none')}",
            f"group:{(state['last_group'] or 'none')}",
            f"topic:{(state['last_topic'] or 'none')}",
        ]

        exp_tu = _cell_at(nr, spec.expectation_col)
        desc_tu = (
            _cell_at(nr, spec.descriptor_col)
            if spec.descriptor_col is not None
            else None
        )

        # Debug sampling.
        if self.wizard_mode and len(state["row_facts_sample"]) < 10:
            state["row_facts_sample"].append(
                CanonicalRowIR(
                    descriptors_raw=(
                        _normalize_space(_coerce_text_to_str(desc_tu))
                        if desc_tu
                        else None
                    ),
                    expectations_raw=(
                        _normalize_space(_coerce_text_to_str(exp_tu))
                        if exp_tu
                        else None
                    ),
                    group=state["last_group"],
                    provenance={
                        "segment_key": getattr(seg, "segment_key", "unknown"),
                        "local_code": local_code,
                        "caption_text": caption_text,
                        "row_index": getattr(nr, "row_index", r_idx),
                        "original_row_index": getattr(nr, "original_row_index", r_idx),
                        "page_index": getattr(nr, "provenance_page_index", None),
                        "bbox": getattr(nr, "provenance_bbox", None),
                        "slice_index": getattr(nr, "provenance_slice_index", None),
                    },
                    subject=state["last_subject"],
                    topic=state["last_topic"],
                )
            )

        row_source_tags = [
            f"prov:table_spec={spec.name}",
            f"prov:slice_index={getattr(nr, 'provenance_slice_index', -1)}",
            f"prov:original_row_index={getattr(nr, 'original_row_index', r_idx)}",
            f"prov:normalized_row_index={getattr(nr, 'row_index', r_idx)}",
            f"prov:page_index={row_page_index if isinstance(row_page_index, int) else 'nopage'}",
            f"prov:local_code={local_code or 'none'}",
        ]

        if exp_tu:
            expectation_ids = self._add_leaf_nodes(
                caption_seg_key=caption_key,
                cell_tu=exp_tu,
                extra_source_ids=row_source_tags,
                leaf_parent_id=leaf_parent_id,
                leaf_scope_path=leaf_scope_path
                + [
                    f"leaf_role:{getattr(spec, 'expectation_role', StatementRole.EXPECTATION).value}"
                ],
                role=getattr(spec, "expectation_role", StatementRole.EXPECTATION),
                row_bbox=row_bbox,
                row_page_index=row_page_index,
                split=bool(getattr(spec, "split_expectations", True)),
                table_seg=seg,
            )
            state["expectations_added"] += len(expectation_ids)
        else:
            expectation_ids = []

        descriptor_parent_id = leaf_parent_id
        if (
            getattr(spec, "descriptor_parenting", "expectation_if_single")
            == "expectation_if_single"
            and len(expectation_ids) == 1
        ):
            descriptor_parent_id = expectation_ids[0]

        if spec.descriptor_col is not None and desc_tu:
            desc_ids = self._add_leaf_nodes(
                caption_seg_key=caption_key,
                cell_tu=desc_tu,
                extra_source_ids=row_source_tags,
                leaf_parent_id=descriptor_parent_id,
                leaf_scope_path=leaf_scope_path
                + [
                    f"leaf_role:{getattr(spec, 'descriptor_role', StatementRole.DESCRIPTOR).value}"
                ],
                role=getattr(spec, "descriptor_role", StatementRole.DESCRIPTOR),
                row_bbox=row_bbox,
                row_page_index=row_page_index,
                split=bool(getattr(spec, "split_descriptors", True)),
                table_seg=seg,
            )
            state["descriptors_added"] += len(desc_ids)

    def _process_table_rows(
        self,
        *,
        caption_key: str | None,
        caption_text: str,
        local_code: str | None,
        seg: Any,
        spec: TableSpec,
        table_identity: str,
    ) -> None:
        """Iterate through table rows, updating hierarchy and extracting leaves.

        Parameters
        ----------
        caption_key
            The segment key of the caption, if any.
        caption_text
            The text of the table caption, if any.
        local_code
            The local code of the table, if any.
        seg
            The table segment to process.
        spec
            The TableSpec for the table.
        table_identity
            The stable identity string for the table.
        """

        forward_fill_cols = list(getattr(spec, "forward_fill_cols", ()) or ())
        norm_rows = normalize_table_grid(
            forward_fill_cols=forward_fill_cols, segment=seg
        )
        header_row_count = int(getattr(seg, "header_row_count", 0) or 0)

        # State for table traversal.
        state: dict[str, Any] = {
            "curr_group_id": None,
            "curr_subject_id": None,
            "curr_topic_id": None,
            "descriptors_added": 0,
            "expectations_added": 0,
            "last_group": None,
            "last_subject": None,
            "last_topic": None,
            "row_facts_sample": [],
            "table_identity": table_identity,
        }

        base_parent_id = self._current_parent_id()
        base_source_ids = [getattr(seg, "segment_key", "unknown")]
        if caption_key:
            base_source_ids.append(caption_key)

        for r_idx, nr in enumerate(norm_rows):
            if r_idx < header_row_count:
                continue

            self._process_single_row(
                base_parent_id=base_parent_id,
                base_source_ids=base_source_ids,
                caption_key=caption_key,
                caption_text=caption_text,
                local_code=local_code,
                nr=nr,
                r_idx=r_idx,
                seg=seg,
                spec=spec,
                state=state,
            )

        self._check_table_results(
            caption_text=caption_text,
            header_texts=_extract_table_header_texts(seg),
            local_code=local_code,
            seg=seg,
            spec=spec,
            state=state,
        )

    def _touch_node_provenance(
        self,
        *,
        bbox: list[float] | None,
        node_id: str | None,
        page_index: int | None,
        role: StatementRole,
        source_ids: list[str],
    ) -> None:
        """Union row-level provenance into an existing node. This is used for tables
        where Subject/Group/Topic cells are merged and forward-filled: the grouping
        nodes should still reflect all rows/pages they span.

        Parameters
        ----------
        bbox
            The bounding box to union into the node.
        node_id
            The node ID to update.
        page_index
            The page index to add to the node.
        role
            The StatementRole of the node.
        source_ids
            The source IDs to add to the node.
        """

        if not node_id:
            return

        page_indices = [page_index] if isinstance(page_index, int) else []
        if bbox is None and not page_indices:
            return

        self.builder.upsert_node(
            node=CanonicalNode(
                bbox=bbox,
                body=None,
                doc_key=self.doc_ir.doc_key,
                list_id=None,
                node_id=node_id,
                page_indices=page_indices,
                role=role,
                source_ids=source_ids,
                title=None,
            )
        )

    def _update_row_hierarchy(
        self,
        *,
        base_parent_id: str,
        base_source_ids: list[str],
        nr: Any,
        row_bbox: list[float] | None,
        row_page_index: int | None,
        spec: TableSpec,
        state: dict,
    ) -> None:
        """Detect changes in Subject/Group/Topic columns and ensure nodes exist.

        Parameters
        ----------
        base_parent_id
            The base parent ID for the table.
        base_source_ids
            The base source IDs for the table.
        nr
            The normalized row object.
        row_bbox
            The bounding box for the current row.
        row_page_index
            The page index for the current row.
        spec
            The TableSpec for the table.
        state
            The current state of hierarchy tracking.
        """

        subj_tu = _cell_at(nr, spec.subject_col)
        group_tu = _cell_at(nr, spec.group_col)
        topic_tu = _cell_at(nr, spec.topic_col)

        subj_str = _normalize_space(_coerce_text_to_str(subj_tu))
        group_str = _normalize_space(_coerce_text_to_str(group_tu))
        topic_str = _normalize_space(_coerce_text_to_str(topic_tu))

        # Subject level.
        if subj_str and subj_str != state["last_subject"]:
            state["curr_subject_id"] = self._ensure_group_node(
                bbox=row_bbox,
                extra_path_parts=[],
                page_index=row_page_index,
                parent_id=base_parent_id,
                role=getattr(spec, "subject_role", StatementRole.SUBJECT),
                source_ids=base_source_ids,
                title_tu=subj_tu
                or TextUnit(language="und", text=subj_str, text_en=None),
            )
            state["curr_group_id"] = None
            state["curr_topic_id"] = None
            state["last_subject"] = subj_str
            state["last_group"] = None
            state["last_topic"] = None

        # Group level.
        group_parent = state["curr_subject_id"] or base_parent_id
        if group_str and group_str != state["last_group"]:
            state["curr_group_id"] = self._ensure_group_node(
                bbox=row_bbox,
                extra_path_parts=[f"subject:{state['last_subject'] or 'none'}"],
                page_index=row_page_index,
                parent_id=group_parent,
                role=getattr(spec, "group_role", StatementRole.STRAND),
                source_ids=base_source_ids,
                title_tu=group_tu
                or TextUnit(language="und", text=group_str, text_en=None),
            )
            state["curr_topic_id"] = None
            state["last_group"] = group_str
            state["last_topic"] = None

        # Topic level.
        topic_parent = state["curr_group_id"] or group_parent
        if topic_str and topic_str != state["last_topic"]:
            state["curr_topic_id"] = self._ensure_group_node(
                bbox=row_bbox,
                extra_path_parts=[
                    f"subject:{state['last_subject'] or 'none'}",
                    f"group:{state['last_group'] or 'none'}",
                ],
                page_index=row_page_index,
                parent_id=topic_parent,
                role=getattr(spec, "topic_role", StatementRole.TOPIC),
                source_ids=base_source_ids,
                title_tu=topic_tu
                or TextUnit(language="und", text=topic_str, text_en=None),
            )
            state["last_topic"] = topic_str

        # Even if the subject/group/topic value did *not* change on this row (common
        # when tables use merged cells + forward-fill), we still want the grouping
        # nodes to accumulate provenance for every row they cover.
        self._touch_node_provenance(
            bbox=row_bbox,
            node_id=state.get("curr_subject_id"),
            page_index=row_page_index,
            role=getattr(spec, "subject_role", StatementRole.SUBJECT),
            source_ids=base_source_ids,
        )
        self._touch_node_provenance(
            bbox=row_bbox,
            node_id=state.get("curr_group_id"),
            page_index=row_page_index,
            role=getattr(spec, "group_role", StatementRole.STRAND),
            source_ids=base_source_ids,
        )
        self._touch_node_provenance(
            bbox=row_bbox,
            node_id=state.get("curr_topic_id"),
            page_index=row_page_index,
            role=getattr(spec, "topic_role", StatementRole.TOPIC),
            source_ids=base_source_ids,
        )

    # STACK AND NODE HELPERS
    def _add_leaf_nodes(
        self,
        *,
        caption_seg_key: str | None,
        cell_tu: TextUnit,
        extra_source_ids: list[str] | None = None,
        leaf_parent_id: str,
        leaf_scope_path: list[str],
        role: StatementRole,
        row_bbox: list[float] | None,
        row_page_index: int | None,
        split: bool,
        table_seg: Any,
    ) -> list[str]:
        """Create leaf nodes (Expectation/Descriptor) from a content cell.

        Parameters
        ----------
        caption_seg_key
            The segment key of the caption, if any.
        cell_tu
            The TextUnit containing the cell content.
        extra_source_ids
            Additional source IDs to attach to the node.
        leaf_parent_id
            The parent node ID to attach the leaf nodes to.
        leaf_scope_path
            The path components representing the scope of the leaf nodes.
        role
            The StatementRole for the leaf nodes.
        row_bbox
            The bounding box for the current row.
        row_page_index
            The page index for the current row.
        split
            Whether to split the cell content into multiple leaf statements.
        table_seg
            The table segment containing the cell.

        Returns
        -------
        list[str]
            The node_ids of the leaf nodes added (in insertion order).
        """

        raw_text = _normalize_space(_coerce_text_to_str(cell_tu))
        if not raw_text:
            return []

        parts = (
            _split_leaf_statements(leaf_cfg=self.config.leaf_parsing, text=raw_text)
            if split
            else [LeafStatement(body=raw_text, list_id=None)]
        )

        node_ids: list[str] = []
        for i, p in enumerate(parts):
            body_str = _normalize_space(p.body)
            if not body_str:
                continue

            leaf_body = TextUnit(
                language=cell_tu.language,
                text=body_str,
                text_en=getattr(cell_tu, "text_en", None),
            )
            path = self._current_path_labels() + leaf_scope_path
            path.append(f"{role.value}:{p.list_id or 'nolist'}:{i}")

            node_id = generate_global_id(
                code=p.list_id,
                doc_key=self.doc_ir.doc_key,
                path=path,
                role=role,
                text=body_str,
            )

            source_ids = [getattr(table_seg, "segment_key", "unknown")]
            if caption_seg_key:
                source_ids.append(caption_seg_key)
            if extra_source_ids:
                source_ids.extend(extra_source_ids)

            self.builder.upsert_node(
                node=CanonicalNode(
                    bbox=row_bbox,
                    body=leaf_body,
                    doc_key=self.doc_ir.doc_key,
                    list_id=p.list_id,
                    node_id=node_id,
                    page_indices=(
                        [row_page_index] if isinstance(row_page_index, int) else []
                    ),
                    role=role,
                    source_ids=source_ids,
                    title=None,
                )
            )
            self.builder.add_edge(child_id=node_id, parent_id=leaf_parent_id)
            node_ids.append(node_id)
        return node_ids

    def _context_titles(self, *, scope: str) -> list[str]:
        """Retrieve titles from the stack for context matching.

        Parameters
        ----------
        scope
            The scope of titles to retrieve ("current" or "any").

        Returns
        -------
        list[str]
            The list of context titles.
        """

        assert scope in ("current", "any"), f"Invalid scope: {scope}"

        if scope == "current" and self.stack:
            # In "current" scope, avoid letting an UNRESOLVED heading block context
            # matching for downstream specs. Walk upward until we find the nearest
            # non-UNRESOLVED label.
            labels: list[str] = []
            for _, _, lbl in reversed(self.stack):
                if ":" in lbl:
                    role_prefix = lbl.split(":", 1)[0].strip()
                    if role_prefix == StatementRole.UNRESOLVED.value:
                        continue
                labels = [lbl]
                break
            if not labels:
                labels = [self.stack[-1][2]]
        else:
            labels = [s[2] for s in self.stack]

        output: list[str] = []

        for lbl in labels:
            if ":" in lbl:
                _, t = lbl.split(":", 1)
                t = t.strip()
            else:
                t = lbl.strip()
            if "#" in t:
                t = t.split("#", 1)[0].strip()
            output.append(t)
        return output

    def _current_parent_id(self) -> str:
        """Get the current parent node ID from the top of the stack.

        Returns
        -------
        str
            The current parent node ID.
        """

        return self.stack[-1][1]

    def _current_path_labels(self) -> list[str]:
        """Get the current path labels from the stack.

        Returns
        -------
        list[str]
            The list of current path labels.
        """

        return [lbl for (_, _, lbl) in self.stack]

    def _ensure_group_node(
        self,
        *,
        bbox: list[float] | None,
        extra_path_parts: list[str],
        page_index: int | None,
        parent_id: str,
        role: StatementRole,
        source_ids: list[str],
        title_tu: TextUnit,
    ) -> str:
        """Ensure an intermediate grouping node (e.g., Strand, Topic) exists.

        Parameters
        ----------
        bbox
            The bounding box for the node.
        extra_path_parts
            Additional path components to include in the node ID.
        page_index
            The page index for the node.
        parent_id
            The parent node ID to attach this node to.
        role
            The StatementRole for the node.
        source_ids
            The source IDs to attach to the node.
        title_tu
            The title TextUnit for the node.

        Returns
        -------
        str
            The node ID of the ensured grouping node.
        """

        title_str = _normalize_space(_coerce_text_to_str(title_tu))
        path = (
            self._current_path_labels()
            + extra_path_parts
            + [f"{role.value}:{title_str}"]
        )

        node_id = generate_global_id(
            code=None,
            doc_key=self.doc_ir.doc_key,
            path=path,
            role=role,
            text=title_str,
        )

        self.builder.upsert_node(
            node=CanonicalNode(
                bbox=bbox,
                body=None,
                doc_key=self.doc_ir.doc_key,
                list_id=None,
                node_id=node_id,
                page_indices=[page_index] if isinstance(page_index, int) else [],
                role=role,
                source_ids=source_ids,
                title=title_tu,
            )
        )
        self.builder.add_edge(child_id=node_id, parent_id=parent_id)

        return node_id

    def _push_heading(
        self,
        *,
        level: int,
        role: StatementRole,
        source_seg: Any,
        title_tu: TextUnit,
        unique_per_occurrence: bool,
    ) -> str:
        """Push a new heading onto the stack, popping deeper levels.

        Parameters
        ----------
        level
            The heading level (e.g., 1 for H1, 2 for H2
        role
            The StatementRole for the heading.
        source_seg
            The heading segment to process.
        title_tu
            The title TextUnit for the heading.
        unique_per_occurrence
            Whether to make the heading unique per occurrence.

        Returns
        -------
        str
            The node ID of the newly created heading node.
        """

        while (
            self.stack
            and self.stack[-1][0] >= level
            and self.stack[-1][1] != self.root_id
        ):
            self.stack.pop()

        parent_id = self._current_parent_id()
        title_str = _normalize_space(_coerce_text_to_str(title_tu))

        base_label = f"{role.value}:{title_str}"
        if unique_per_occurrence:
            seg_key = getattr(source_seg, "segment_key", "unknown")
            seg_key_short = seg_key.split(":")[-1]
            label = f"{base_label}#{seg_key_short}"
        else:
            label = base_label

        path = self._current_path_labels() + [label]
        node_id = generate_global_id(
            code=getattr(source_seg, "local_code", None),
            doc_key=self.doc_ir.doc_key,
            path=path,
            role=role,
            text=title_str,
        )

        node = CanonicalNode(
            bbox=_segment_bbox_union(source_seg),
            body=None,
            doc_key=self.doc_ir.doc_key,
            list_id=None,
            node_id=node_id,
            page_indices=_segment_page_indices(source_seg),
            role=role,
            source_ids=[getattr(source_seg, "segment_key", "unknown")],
            title=title_tu,
        )

        self.builder.upsert_node(node=node)
        self.builder.add_edge(child_id=node_id, parent_id=parent_id)
        self.stack.append((level, node_id, label))

        return node_id


def parse_document(
    *, config: ParserConfig, document_ir: DocumentIR, wizard_mode: bool = False
) -> CanonicalIR:
    """Deterministically parse DocumentIR --> CanonicalIR using config-driven rules.

    Parameters
    ----------
    config
        The parsing configuration.
    document_ir
        The input DocumentIR object.
    wizard_mode
        If True, captures additional diagnostics for unmatched content.

    Returns
    -------
    CanonicalIR
        The constructed CanonicalIR graph.
    """

    parser = DocumentParser(
        config=config, document_ir=document_ir, wizard_mode=wizard_mode
    )
    return parser.parse()
