"""This module contains utility functions for canonical Intermediate Representations."""

# Standard Library
import uuid

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Third Party Library
from loguru import logger

# Package Library
from skg.canonical_ir.schemas import (
    CanonicalEdge,
    CanonicalIR,
    SegmentDecision,
    SegmentDecisionSet,
    compute_decision_set_id,
)
from skg.document_ir.schemas import DocumentIR
from skg.schemas import CreateCanonicalConfig, RunCtx
from skg.utils.general import make_dir, open_json_type, write_to_json


@dataclass(frozen=True)
class CanonicalIRDirs:
    """Dataclass for canonical IR directories."""

    root: Path
    canonical_ir: Path
    segment_decisions: Path


def _load_segment_decision_set(
    *, expected_doc_key: str, pdf_name: str, segment_decisions_fp: Path
) -> SegmentDecisionSet:
    """Load SegmentDecisionSet JSON and normalize formats.

    Parameters
    ----------
    expected_doc_key
        The expected document key for the SegmentDecisionSet.
    pdf_name
        The expected PDF name for the SegmentDecisionSet.
    segment_decisions_fp
        The file path to the SegmentDecisionSet JSON.

    Returns
    -------
    SegmentDecisionSet
        The loaded SegmentDecisionSet.

    Raises
    ------
    ValueError
        If the SegmentDecisionSet.doc_key does not match expected_doc_key, or if the
        SegmentDecisionSet.pdf_name does not match pdf_name.
    """

    raw = open_json_type(segment_decisions_fp)

    # Allow "raw list" format.
    if isinstance(raw, list):
        decisions = [SegmentDecision.model_validate(d) for d in raw]
        raw = {
            "pdf_name": pdf_name,
            "doc_key": expected_doc_key,
            "decision_set_id": compute_decision_set_id(decisions=decisions),
            "decisions": decisions,
        }

    # Ensure decision_set_id exists for wrapper format.
    if isinstance(raw, dict):
        if raw.get("decisions") is None:
            raise ValueError(
                f"SegmentDecisionSet file missing `decisions` key: {segment_decisions_fp}"
            )

        decisions = [SegmentDecision.model_validate(d) for d in raw["decisions"]]
        raw["decisions"] = decisions

        if raw.get("decision_set_id") in (None, ""):
            raw["decision_set_id"] = compute_decision_set_id(decisions=decisions)

    decision_set = SegmentDecisionSet.model_validate(raw)

    if decision_set.doc_key != expected_doc_key:
        raise ValueError(
            f"SegmentDecisionSet.doc_key mismatch.\n"
            f"  Expected: {expected_doc_key}\n"
            f"  Got:      {decision_set.doc_key}\n"
            f"  File:     {segment_decisions_fp}"
        )

    if decision_set.pdf_name != pdf_name:
        raise ValueError(
            f"SegmentDecisionSet.pdf_name mismatch.\n"
            f"  DocumentIR: {pdf_name}\n"
            f"  Decisions:  {decision_set.pdf_name}"
        )

    return decision_set

        effective_leaf_id = ensure_node(
            node=node, nodes_by_id=nodes_by_id, warnings=warnings
        )
        _emit_edge(
            child_id=effective_leaf_id,
            child_to_parent=child_to_parent,
            decision_id=decision.decision_id,
            edges=edges,
            edges_by_key=edges_by_key,
            next_order_index=next_order_index,
            parent_id=parent_id,
            segment_id=segment_id,
            warnings=warnings,
        )


def _materialize_decision_structure(
    *,
    active_context_stack: list[ContextFrame],
    child_to_parent: dict[str, str],
    decision: SegmentDecision,
    doc_key: str,
    edges: list[CanonicalEdge],
    edges_by_key: dict[tuple[str, str], CanonicalEdge],
    next_order_index: dict[str, int],
    nodes_by_id: dict[str, CanonicalNode],
    page_indices: list[int],
    root_id: str,
    section_path_text: list[str],
    segment: Segment,
    warnings: list[str],
) -> list[ContextFrame]:
    """Reconcile context stack, create grouping nodes, and materialize leaves/rows.

    NB: For tables, a table decision may contain both decision.leaves[] (segment-level
    statements) OR decision.rows[] (row-wise statements). In these cases, emitting
    leaves first makes them appear first in deterministic order_index order under that
    parent. If we wanted to emit rows first, then we can just switch the if-statements.

    Parameters
    ----------
    active_context_stack
        The current active context stack.
    child_to_parent
        The mapping of child_id to parent_id.
    decision
        The SegmentDecision to materialize.
    doc_key
        The document key.
    edges
        The list of CanonicalEdges to append to.
    edges_by_key
        The mapping of (parent_id, child_id) to CanonicalEdge.
    next_order_index
        The mapping of parent_id to next order_index.
    nodes_by_id
        The mapping of node_id to CanonicalNode.
    page_indices
        The list of page indices for the segment.
    root_id
        The root node ID.
    section_path_text
        The section path text for the segment.
    segment
        The Segment to materialize.
    warnings
        The list of warnings to append to.

    Returns
    -------
    list[ContextFrame]
        The updated active_context_stack.
    """

    # Context stack reconciliation.
    parent_id, ancestor_keys, active_context_stack = reconcile_context_stack(
        active_stack=active_context_stack,
        child_to_parent=child_to_parent,
        decision=decision,
        desired_context=decision.context_groupings,
        doc_key=doc_key,
        edges_by_key=edges_by_key,
        edges=edges,
        next_order_index=next_order_index,
        nodes_by_id=nodes_by_id,
        root_id=root_id,
        segment=segment,
        warnings=warnings,
    )

    # Apply decision.groupings[] under the context stack tip.
    for g in decision.groupings:
        g_title = canonical_grouping_title(role=g.role, title=g.title)
        node_id = canonical_grouping_node_id(
            ancestor_grouping_keys=ancestor_keys, doc_key=doc_key, grouping=g
        )

        node = CanonicalNode(
            bbox=_segment_first_bbox(segment),
            body=None,
            list_marker=None,
            local_code=g.local_code,
            node_id=node_id,
            normalized_text=_normalize_text(text=g_title),
            page_indices=page_indices,
            role=g.role,
            section_path_text=section_path_text,
            source_decision_ids=[decision.decision_id],
            source_label=g.source_label,
            source_segment_ids=[segment.segment_id],
            source_type=segment.kind,
            title=TextUnit(language="und", text=canonical_storage_text(g_title)),
        )

        effective_node_id = ensure_node(
            node=node, nodes_by_id=nodes_by_id, warnings=warnings
        )
        _emit_edge(
            child_id=effective_node_id,
            child_to_parent=child_to_parent,
            decision_id=decision.decision_id,
            edges=edges,
            edges_by_key=edges_by_key,
            next_order_index=next_order_index,
            parent_id=parent_id,
            segment_id=segment.segment_id,
            warnings=warnings,
        )

        parent_id = effective_node_id
        ancestor_keys.append(_grouping_key(g))

    # Dispatch based on segment kind.
    if segment.kind == "block":
        _materialize_block_leaves(
            ancestor_keys=ancestor_keys,
            child_to_parent=child_to_parent,
            decision=decision,
            doc_key=doc_key,
            edges=edges,
            edges_by_key=edges_by_key,
            next_order_index=next_order_index,
            nodes_by_id=nodes_by_id,
            page_indices=page_indices,
            parent_id=parent_id,
            section_path_text=section_path_text,
            segment_bbox=_segment_first_bbox(segment),
            segment_id=segment.segment_id,
            warnings=warnings,
        )
    elif segment.kind == "table":
        if not decision.leaves and not decision.rows:
            msg = f"table_decision_emits_nothing:{segment.segment_id}:{decision.decision_id}"
            logger.warning(msg)
            warnings.append(msg)

        # Table decisions may emit leaves directly.
        if decision.leaves:
            _materialize_table_leaves(
                ancestor_keys=ancestor_keys,
                child_to_parent=child_to_parent,
                decision=decision,
                doc_key=doc_key,
                edges=edges,
                edges_by_key=edges_by_key,
                next_order_index=next_order_index,
                nodes_by_id=nodes_by_id,
                page_indices=page_indices,
                parent_id=parent_id,
                section_path_text=section_path_text,
                segment_bbox=_segment_first_bbox(segment),
                segment_id=segment.segment_id,
                warnings=warnings,
            )

        # Preferred: row-level decisions.
        if decision.rows:
            _materialize_table_rows(
                ancestor_keys=ancestor_keys,
                child_to_parent=child_to_parent,
                decision=decision,
                doc_key=doc_key,
                edges=edges,
                edges_by_key=edges_by_key,
                next_order_index=next_order_index,
                nodes_by_id=nodes_by_id,
                page_indices=page_indices,
                parent_id=parent_id,
                section_path_text=section_path_text,
                segment_id=segment.segment_id,
                table_segment=segment,
                warnings=warnings,
            )
    else:
        msg = f"unknown_segment_kind:{segment.kind}:{segment.segment_id}"
        logger.warning(msg)
        warnings.append(msg)

    return active_context_stack


def _materialize_table_leaves(
    *,
    ancestor_keys: list[str],
    child_to_parent: dict[str, str],
    decision: SegmentDecision,
    doc_key: str,
    edges: list[CanonicalEdge],
    edges_by_key: dict[tuple[str, str], CanonicalEdge],
    next_order_index: dict[str, int],
    nodes_by_id: dict[str, CanonicalNode],
    page_indices: list[int],
    parent_id: str,
    section_path_text: list[str],
    segment_bbox: Optional[BBox],
    segment_id: str,
    warnings: list[str],
) -> None:
    """Materialize table-level leaves (SegmentDecision.leaves) under the current
    parent. This is used for TABLE segments where the LLM emits leaves directly
    (fallback mode) instead of emitting RowDecision entries in SegmentDecision.rows[].

    Parameters
    ----------
    ancestor_keys
        The list of ancestor grouping keys.
    child_to_parent
        The mapping of child_id to parent_id.
    decision
        The SegmentDecision to materialize.
    doc_key
        The document key.
    edges
        The list of CanonicalEdges to append to.
    edges_by_key
        The mapping of (parent_id, child_id) to CanonicalEdge.
    next_order_index
        The mapping of parent_id to next order_index.
    nodes_by_id
        The mapping of node_id to CanonicalNode.
    page_indices
        The list of page indices for the segment.
    parent_id
        The parent node ID.
    section_path_text
        The section path text for the segment.
    segment_bbox
        The segment bounding box.
    segment_id
        The segment ID.
    warnings
        The list of warnings to append to.
    """

    for leaf in decision.leaves:
        leaf_id = canonical_leaf_node_id(
            ancestor_grouping_keys=ancestor_keys, doc_key=doc_key, leaf=leaf
        )

        node = CanonicalNode(
            bbox=segment_bbox,
            body=TextUnit(language="und", text=canonical_storage_text(leaf.body)),
            list_marker=leaf.list_marker,
            local_code=leaf.local_code,
            node_id=leaf_id,
            normalized_text=_normalize_text(text=leaf.body),
            page_indices=page_indices,
            role=leaf.role,
            section_path_text=section_path_text,
            source_decision_ids=[decision.decision_id],
            source_label=leaf.source_label,
            source_segment_ids=[segment_id],
            source_type="table",  # IMPORTANT: keep provenance correct
            title=None,
        )

        effective_leaf_id = ensure_node(
            node=node, nodes_by_id=nodes_by_id, warnings=warnings
        )
        _emit_edge(
            child_id=effective_leaf_id,
            child_to_parent=child_to_parent,
            decision_id=decision.decision_id,
            edges=edges,
            edges_by_key=edges_by_key,
            next_order_index=next_order_index,
            parent_id=parent_id,
            segment_id=segment_id,
            warnings=warnings,
        )


def _materialize_table_rows(
    *,
    ancestor_keys: list[str],
    child_to_parent: dict[str, str],
    decision: SegmentDecision,
    doc_key: str,
    edges: list[CanonicalEdge],
    edges_by_key: dict[tuple[str, str], CanonicalEdge],
    next_order_index: dict[str, int],
    nodes_by_id: dict[str, CanonicalNode],
    page_indices: list[int],
    parent_id: str,
    section_path_text: list[str],
    segment_id: str,
    table_segment: TableSegment,
    warnings: list[str],
) -> None:
    """Materialize rows and leaves for a table segment.

    Parameters
    ----------
    ancestor_keys
        The list of ancestor grouping keys.
    child_to_parent
        The mapping of child_id to parent_id.
    decision
        The SegmentDecision to materialize.
    doc_key
        The document key.
    edges
        The list of CanonicalEdges to append to.
    edges_by_key
        The mapping of (parent_id, child_id) to CanonicalEdge.
    next_order_index
        The mapping of parent_id to next order_index.
    nodes_by_id
        The mapping of node_id to CanonicalNode.
    page_indices
        The list of page indices for the segment.
    parent_id
        The parent node ID.
    section_path_text
        The section path text for the segment.
    segment_id
        The segment ID.
    table_segment
        The TableSegment to materialize rows for.
    warnings
        The list of warnings to append to.
    """

    for row in sorted(decision.rows, key=lambda r: r.row_index):
        row_ancestor_keys = list(ancestor_keys)
        row_bbox = _table_row_bbox(row_index=row.row_index, table_segment=table_segment)
        row_parent_id = parent_id

        # Row groupings.
        for g in row.groupings:
            g_title = canonical_grouping_title(role=g.role, title=g.title)
            node_id = canonical_grouping_node_id(
                ancestor_grouping_keys=row_ancestor_keys, doc_key=doc_key, grouping=g
            )

            node = CanonicalNode(
                bbox=row_bbox,
                body=None,
                list_marker=None,
                local_code=g.local_code,
                node_id=node_id,
                normalized_text=_normalize_text(text=g_title),
                page_indices=page_indices,
                role=g.role,
                section_path_text=section_path_text,
                source_decision_ids=[decision.decision_id],
                source_label=g.source_label,
                source_segment_ids=[segment_id],
                source_type="table",
                title=TextUnit(language="und", text=canonical_storage_text(g_title)),
            )

            effective_node_id = ensure_node(
                node=node, nodes_by_id=nodes_by_id, warnings=warnings
            )
            _emit_edge(
                child_id=effective_node_id,
                child_to_parent=child_to_parent,
                decision_id=decision.decision_id,
                edges=edges,
                edges_by_key=edges_by_key,
                next_order_index=next_order_index,
                parent_id=row_parent_id,
                segment_id=segment_id,
                warnings=warnings,
            )

            row_parent_id = effective_node_id
            row_ancestor_keys.append(_grouping_key(g))

        # Row leaves.
        for leaf in row.leaves:
            leaf_id = canonical_leaf_node_id(
                ancestor_grouping_keys=row_ancestor_keys, doc_key=doc_key, leaf=leaf
            )

            node = CanonicalNode(
                bbox=row_bbox,
                body=TextUnit(language="und", text=canonical_storage_text(leaf.body)),
                list_marker=leaf.list_marker,
                local_code=leaf.local_code,
                node_id=leaf_id,
                normalized_text=_normalize_text(text=leaf.body),
                page_indices=page_indices,
                role=leaf.role,
                section_path_text=section_path_text,
                source_decision_ids=[decision.decision_id],
                source_label=leaf.source_label,
                source_segment_ids=[segment_id],
                source_type="table",
                title=None,
            )

            effective_leaf_id = ensure_node(
                node=node, nodes_by_id=nodes_by_id, warnings=warnings
            )
            _emit_edge(
                child_id=effective_leaf_id,
                child_to_parent=child_to_parent,
                decision_id=decision.decision_id,
                edges=edges,
                edges_by_key=edges_by_key,
                next_order_index=next_order_index,
                parent_id=row_parent_id,
                segment_id=segment_id,
                warnings=warnings,
            )


def _normalize_leaf_body(body: str) -> str:
    """Normalize statement text. The goal here is to remove purely formatting-based
    diffs that shouldn't create merge warnings.

    The following are considered safe operations:

    1. Unicode normalize (NFKC)
    2. Normalize quotes/dashes
    3. Collapse whitespace
    4. Normalize spacing after ":" (no capitalization changes)

    Parameters
    ----------
    body
        The leaf body text to normalize.

    Returns
    -------
    str
        The normalized leaf body text.
    """

    s = unicodedata.normalize("NFKC", body or "")
    s = s.translate(QUOTES_TRANSLATION)
    s = _DASH_RE.sub("-", s)
    s = re.sub(r"\s+", " ", s).strip()

    # Normalize colon spacing ONLY when a non-space follows the colon.
    # Examples:
    #   "Skills:use blends"   -> "Skills: use blends"
    #   "Skills:  use blends" -> "Skills: use blends"
    s = re.sub(r":\s*(?=\S)", ": ", s)
    s = re.sub(r"\s+", " ", s).strip()

    return s


def _normalize_text(text: Optional[str]) -> str:
    """Deterministic normalization for hashing/comparisons:

    Parameters
    ----------
    text
        The text to normalize.

    Returns
    -------
    str
        The normalized text.
    """

    if not text:
        return ""

    text = unicodedata.normalize("NFKC", text)
    text = _DASH_RE.sub("-", text)

    return _WS_RE.sub(" ", text).strip().casefold()


def _normalized_text_hash(*, encoding: str = "utf-8", text: str) -> str:
    """Compute a stable SHA-256 hash of normalized text.

    Parameters
    ----------
    encoding
        The text encoding to use.
    text
        The text to hash.

    Returns
    -------
    str
        The SHA-256 hash of the normalized text as a hex string.
    """

    norm = _normalize_text(text)

    return hashlib.sha256(norm.encode(encoding)).hexdigest()


def _resolve_collision(
    *, node: CanonicalNode, nodes_by_id: dict[str, CanonicalNode], warnings: list[str]
) -> str:
    """Disambiguate a node ID using provenance data and inserts it as a new node. Used
    when a semantic collision is detected. Updates node.node_id in place.

    Parameters
    ----------
    node
        The CanonicalNode with a colliding node_id.
    nodes_by_id
        The mapping of node_id to CanonicalNode.
    warnings
        The list of warnings to append to.

    Returns
    -------
    str
        The new unique node_id.
    """

    parts: list[str] = []

    # Build a stable disambiguator string from provenance.
    if node.source_segment_ids:
        parts.append(f"seg={node.source_segment_ids[0]}")
    if node.source_decision_ids:
        parts.append(f"dec={node.source_decision_ids[0]}")
    if node.page_indices:
        parts.append(f"page={node.page_indices[0]}")
    if node.list_marker:
        parts.append(f"list={node.list_marker}")
    if node.local_code:
        parts.append(f"code={node.local_code}")

    disambiguator = "|".join(parts) if parts else "no_provenance"
    base_key = f"{node.node_id}|collision|{disambiguator}"

    # Generate deterministic new ID.
    new_id = uuidv5_from_key(base_key)

    # Extremely defensive: ensure uniqueness deterministically.
    i = 1
    while new_id in nodes_by_id:
        new_id = uuidv5_from_key(f"{base_key}|{i}")
        i += 1

    msg = (
        f"node_id_collision_resolved:"
        f"old_node_id={node.node_id} new_node_id={new_id} disambiguator={disambiguator}"
    )
    logger.warning(msg)
    warnings.append(msg)

    # Apply changes.
    node.node_id = new_id
    nodes_by_id[new_id] = node

    return new_id


def _rewrite_grouping_list(
    *,
    enforce_unique_roles: bool,
    groupings: list[GroupingDecision] | None,
    mapping_index: dict[
        tuple[str, str, str, str],
        GroupingCanonicalizationKey | list[GroupingCanonicalizationKey] | None,
    ],
    sort_by_precedence: bool,
) -> list[GroupingDecision] | None:
    """Rewrite a list of GroupingDecisions using a mapping index.

    Parameters
    ----------
    enforce_unique_roles
        Whether to enforce unique roles in the output list.
    groupings
        The list of GroupingDecisions to rewrite.
    mapping_index
        The mapping index for rewriting groupings.
    sort_by_precedence
        Whether to sort the output list by context precedence.

    Returns
    -------
    list[GroupingDecision] | None
        The rewritten list of GroupingDecisions.
    """

    if not groupings:
        return groupings

    rewritten: list[GroupingDecision] = []

    for g in groupings:
        key = _gkey_tuple(
            local_code=g.local_code,
            role=g.role,
            source_label=g.source_label,
            title=g.title,
        )

        if key not in mapping_index:
            rewritten.append(g)
            continue

        repl = mapping_index[key]

        if repl is None:  # Drop
            continue

        if isinstance(repl, list):  # Split
            rewritten.extend(
                [
                    GroupingDecision(
                        local_code=k.local_code,
                        role=k.role,
                        source_label=k.source_label,
                        title=k.title,
                    )
                    for k in repl
                ]
            )
        else:  # Replace
            rewritten.append(
                GroupingDecision(
                    local_code=repl.local_code,
                    role=repl.role,
                    source_label=repl.source_label,
                    title=repl.title,
                )
            )

    # Deduplicate while preserving order.
    rewritten = _dedupe_preserve_order(rewritten)

    if enforce_unique_roles:
        rewritten = _drop_duplicate_roles_keep_first(rewritten)

    if sort_by_precedence:
        rewritten = _sort_by_context_precedence(rewritten)

    return rewritten


def _segment_first_bbox(segment: Segment) -> Optional[BBox]:
    """Best-effort bbox for a segment.

    NB: Segments can span pages; bboxes are page-local, so we only take the first
    provenance bbox (deterministic + meaningful for debugging).

    Parameters
    ----------
    segment
        The Segment to extract the bbox from.

    Returns
    -------
    Optional[BBox]
        The first BBox if available, else None.
    """

    return segment.segment_provenance[0].bbox if segment.segment_provenance else None


def _sort_by_context_precedence(
    groupings: list[GroupingDecision],
) -> list[GroupingDecision]:
    """Sort groupings by the global precedence order used for context_groupings.
    Unknown roles fall to the end deterministically.

    Parameters
    ----------
    groupings
        The list of GroupingDecisions to sort.

    Returns
    -------
    list[GroupingDecision]
        The sorted list of GroupingDecisions.
    """

    def key_fn(g: GroupingDecision) -> tuple[int, str]:
        """Key function for sorting groupings by precedence and title.

        Parameters
        ----------
        g
            The GroupingDecision to generate a sort key for.

        Returns
        -------
        tuple[int, str]
            The sort key as (precedence, title).
        """

        return CONTEXT_GROUPINGS_ROLE_PRECEDENCE.get(g.role, 10_000), g.title

    return sorted(groupings, key=key_fn)


def _stable_extend_unique(*, base: list[T], extra: list[T]) -> list[T]:
    """Deterministic "stable union" for string lists:

    1. Preserve first-seen order
    2. Avoid duplicates

    Parameters
    ----------
    base
        The base list of strings.
    extra
        The extra list of strings to append uniquely.

    Returns
    -------
    list[T]
        The extended list of strings.
    """

    seen = set(base)
    out = list(base)

    for x in extra:
        if x not in seen:
            out.append(x)
            seen.add(x)

    return out


def _table_row_bbox(*, row_index: int, table_segment: TableSegment) -> Optional[BBox]:
    """Best-effort bbox for a stitched table row.

    Parameters
    ----------
    row_index
        The row index to extract the bbox for.
    table_segment
        The TableSegment to extract the row bbox from.

    Returns
    -------
    Optional[BBox]
        The BBox for the row if available, else None.
    """

    if table_segment.row_provenance and 0 <= row_index < len(
        table_segment.row_provenance
    ):
        rp = table_segment.row_provenance[row_index]

        return rp.row_bbox or rp.bbox

    return None


def _validate_and_handle_unresolved(
    *,
    decision: SegmentDecision,
    page_indices: list[int],
    section_path_text: list[str],
    segment: Segment,
    segment_decision_conf_threshold: float,
    unresolved: list[UnresolvedItem],
    warnings: list[str],
) -> bool:
    """Validate decision, update unresolved/warnings, return True if materializable.

    Parameters
    ----------
    decision
        The SegmentDecision to validate.
    page_indices
        The list of page indices for the segment.
    section_path_text
        The section path text for the segment.
    segment
        The Segment to validate.
    segment_decision_conf_threshold
        The low confidence threshold for segment decisions.
    unresolved
        The list of UnresolvedItems to append to.
    warnings
        The list of warnings to append to.

    Returns
    -------
    bool
        True if the decision is materializable, False otherwise.
    """

    if decision.decision_type == SegmentDecisionType.IGNORE:
        return False

    # SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED is a "review" decision. It must be
    # persisted to the audit trail, but MUST NOT be materialized into CanonicalIR
    # nodes/edges.
    if decision.decision_type == SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED:
        msg = (
            f"flagged_unresolved_decision_not_materialized:"
            f"segment_id={segment.segment_id} decision_id={decision.decision_id} "
            f"kind={segment.kind} conf={decision.confidence:.3f}"
        )
        logger.warning(msg)
        warnings.append(msg)
        unresolved.append(
            UnresolvedItem(
                caption_text=decision.caption_text,
                headers=(
                    _extract_table_headers(segment) if segment.kind == "table" else []
                ),
                kind=segment.kind,
                local_code=segment.local_code,
                page_indices=page_indices,
                reason=UnresolvedReason.FLAGGED_UNRESOLVED,
                sample=_make_unresolved_sample(decision=decision, segment=segment),
                section_path_text=section_path_text,
                segment_id=segment.segment_id,
            )
        )
        return False

    if decision.decision_type == SegmentDecisionType.UNRESOLVED:
        reason = (
            UnresolvedReason.UNMATCHED_TABLE
            if segment.kind == "table"
            else UnresolvedReason.UNMATCHED_BLOCK
        )
        unresolved.append(
            UnresolvedItem(
                caption_text=decision.caption_text,
                headers=(
                    _extract_table_headers(segment) if segment.kind == "table" else []
                ),
                local_code=segment.local_code,
                kind=segment.kind,
                page_indices=page_indices,
                reason=reason,
                sample=_make_unresolved_sample(decision=decision, segment=segment),
                section_path_text=section_path_text,
                segment_id=segment.segment_id,
            )
        )
        return False

    # Confidence gating.
    if decision.confidence < segment_decision_conf_threshold:
        msg = (
            f"low_confidence_decision_not_materialized:"
            f"segment_id={segment.segment_id} decision_id={decision.decision_id} "
            f"kind={segment.kind} conf={decision.confidence:.3f} "
            f"threshold={segment_decision_conf_threshold:.3f}"
        )
        logger.warning(msg)
        warnings.append(msg)
        unresolved.append(
            UnresolvedItem(
                caption_text=decision.caption_text,
                headers=(
                    _extract_table_headers(segment) if segment.kind == "table" else []
                ),
                kind=segment.kind,
                local_code=getattr(segment, "local_code", None),
                page_indices=page_indices,
                reason=UnresolvedReason.LOW_CONFIDENCE_DECISION_NOT_MATERIALIZED,
                sample=_make_unresolved_sample(decision=decision, segment=segment),
                section_path_text=section_path_text,
                segment_id=segment.segment_id,
            )
        )
        return False

    return True


def apply_grouping_canonicalization_map(
    *,
    canonical_grouping_min_confidence: float,
    creation_dirs: CanonicalIRDirs,
    mapping: GroupingCanonicalizationMap,
    overwrite: bool,
    segment_decisions: SegmentDecisionSet,
) -> SegmentDecisionSet:
    """Deterministically apply a GroupingCanonicalizationMap to all grouping lists in a
    decision set.

    Applies to:

    1. SegmentDecision.context_groupings
    2. SegmentDecision.groupings
    3. RowDecision.groupings

    The mapping is as follows:

    1. KEEP: no-op
    2. DROP: remove the grouping
    3. REPLACE: replace with 1 canonical grouping (same role)
    4. SPLIT: replace with 2+ canonical groupings (may change roles)

    The process is as follows:

    1. Dedupe exact duplicates (stable)
    2. Enforce unique roles for context_groupings + row groupings (keep-first)
    3. Sort context_groupings by global outer→inner precedence for stability

    Parameters
    ----------
    canonical_grouping_min_confidence
        The minimum confidence threshold for applying canonical grouping.
    creation_dirs
        The canonical IR creation directories.
    mapping
        The GroupingCanonicalizationMap to apply.
    overwrite
        Whether to overwrite existing normalized decisions.
    segment_decisions
        The SegmentDecisionSet to update.

    Returns
    -------
    SegmentDecisionSet
        The updated SegmentDecisionSet.
    """

    normalized_segment_decisions_fp = (
        creation_dirs.segment_decisions / "segment_decisions_normalized.json"
    )

    if not overwrite and normalized_segment_decisions_fp.exists():
        logger.warning(
            f"Normalized segment decisions JSON already exists at {normalized_segment_decisions_fp}. "
            f"Reusing existing normalized segment decisions. "
            f"If you wish to overwrite, pass the --overwrite flag."
        )
        return SegmentDecisionSet.model_validate(
            open_json_type(normalized_segment_decisions_fp)
        )

    mapping_index = _build_mapping_index(
        canonical_grouping_min_confidence=canonical_grouping_min_confidence,
        mapping=mapping,
    )

    # NB: Never canonicalize table-local roles (e.g., topic/subtopic). These roles
    # carry local codes and are frequently reused across grades/areas, so global
    # canonicalization causes incorrect merges (e.g., "3.9 NOUNS" -> "1.11 NOUNS").
    _BLOCKED_CANONICALIZATION_ROLE_VALUES = {
        NodeRole.TOPIC.value,
        NodeRole.SUBTOPIC.value,
    }
    mapping_index = {
        k: v
        for k, v in mapping_index.items()
        if k[0] not in _BLOCKED_CANONICALIZATION_ROLE_VALUES
    }

    new_decisions: list[SegmentDecision] = []

    for decision in segment_decisions.decisions:
        updates = {}

        # Context groupings: enforce precedence + no duplicate roles.
        if decision.context_groupings:
            updates["context_groupings"] = _rewrite_grouping_list(
                enforce_unique_roles=True,
                groupings=decision.context_groupings,
                mapping_index=mapping_index,
                sort_by_precedence=True,
            )

        # Segment-level groupings: keep order by default.
        if decision.groupings:
            updates["groupings"] = _rewrite_grouping_list(
                enforce_unique_roles=False,
                groupings=decision.groupings,
                mapping_index=mapping_index,
                sort_by_precedence=False,
            )

        # Row-level groupings: enforce unique roles.
        if decision.rows:
            new_rows = []

            for row in decision.rows:
                if row.groupings:
                    new_groupings = _rewrite_grouping_list(
                        enforce_unique_roles=True,
                        groupings=row.groupings,
                        mapping_index={},  # Don't canonicalize row-local groupings
                        sort_by_precedence=False,
                    )
                    new_rows.append(row.model_copy(update={"groupings": new_groupings}))
                else:
                    new_rows.append(row)

            updates["rows"] = new_rows

        new_decisions.append(decision.model_copy(update=updates))

    # NB: Recompute decision set ID since decisions have changed.
    new_id = compute_decision_set_id(decisions=new_decisions)

    # Save updated decision set.
    normalized_segment_decisions = segment_decisions.model_copy(
        update={"decision_set_id": new_id, "decisions": new_decisions}
    )
    write_to_json(
        fp=normalized_segment_decisions_fp, json_info=normalized_segment_decisions
    )

    logger.success(
        f"Saved normalized segment decisions to: {normalized_segment_decisions_fp}"
    )

    return normalized_segment_decisions


def apply_table_signatures(
    *, decisions: list[SegmentDecision], document_ir: Any
) -> list[SegmentDecision]:
    """Iterate through segment decisions and update table segments with column
    signatures found in the source DocumentIR.

    Parameters
    ----------
    decisions
        The list of SegmentDecisions to update.
    document_ir
        The source DocumentIR (dict or object form).

    Returns
    -------
    list[SegmentDecision]
        The updated list of SegmentDecisions.
    """

    segments = document_ir.segments
    signature_map = {}

    # Build the map for segment_id -> columns_signature.
    for segment in segments:
        seg_id = segment.segment_id
        col_sig = getattr(segment, "columns_signature", None)

        if seg_id and col_sig:
            signature_map[seg_id] = col_sig

    # Update decisions.
    updated_decisions = []

    for d in decisions:
        # If it is a table and we have a signature for it, update the field.
        if d.segment_kind == "table" and d.segment_id in signature_map:
            d = d.model_copy(update={"columns_signature": signature_map[d.segment_id]})

        updated_decisions.append(d)

    return updated_decisions


def build_context_hint_from_decision(d: SegmentDecision) -> list[dict[str, Any]]:
    """Build context hint from a SegmentDecision.

    This is used as `prior_context_groupings` when deciding later chunks of the same
    table segment (and can also be used as a general context hint between segments).

    Preference in order:

    1. `context_groupings[]` (explicit outer-context snapshot)
    2. `groupings[]`        (segment-level groupings emitted by the decision)
    3. `rows[].groupings[]` (row-level groupings; useful when the table repeats context
        per row)

    Only coarse "carry" roles are included to avoid noisy/unstable context.

    Parameters
    ----------
    d
        The SegmentDecision to extract context hint from.

    Returns
    -------
    list[dict[str, Any]]
        The context hint as a list of dicts.
    """

    carry_roles = {
        NodeRole.GRADE_LEVEL,
        NodeRole.STAGE,
        NodeRole.LEARNING_AREA,
        NodeRole.STRAND,
        NodeRole.SUBJECT,
        NodeRole.THEME,
        NodeRole.UNIT,
        NodeRole.WEEK,
    }
    hint: list[dict[str, Any]] = []
    seen: set[str] = set()  # One entry per role

    def _maybe_add(g: GroupingDecision) -> None:
        """Helper to add grouping if eligible.

        Parameters
        ----------
        g
            The GroupingDecision to consider.

        """

        if g.role not in carry_roles:
            return

        key = str(g.role)

        if key in seen:
            return

        seen.add(key)
        hint.append(g.model_dump(mode="json"))

    for g in d.context_groupings or []:
        _maybe_add(g)

    for g in d.groupings or []:
        _maybe_add(g)

    # Row-level groupings matter when the table repeats grade/subject/week/etc per-row
    # (e.g., Uganda thematic curriculum tables).
    for rd in getattr(d, "rows", None) or []:
        for g in rd.groupings or []:
            _maybe_add(g)

    return hint


def canonical_grade_level_title(title: str) -> str:
    """Normalize common grade label variants into a consistent display form.

    Examples:
      - "GRADE 1-3"     -> "GRADES 1–3"
      - "GRADES 1 – 3"  -> "GRADES 1–3"
      - "Grade 2"       -> "GRADE 2"

    NB:

    1. This only fires when patterns match confidently.
    2. Otherwise we return the original string unchanged.
    """

    if not title:
        return title

    # Normalize unicode + whitespace + dash variants for matching.
    t = unicodedata.normalize("NFKC", title).strip()
    t = _WS_RE.sub(" ", t)
    t = _DASH_RE.sub("-", t)  # Unify various dash chars
    t = re.sub(r"\s*-\s*", "-", t)  # Remove spaces around hyphen
    t = _WS_RE.sub(" ", t).strip()

    # Numeric grade range: GRADE(S) 1 - 3,
    m = re.match(r"^(grades?|grade)\s+(\d+)-(\d+)$", t, flags=re.IGNORECASE)
    if m:
        start = int(m.group(2))
        end = int(m.group(3))
        return f"GRADES {start}–{end}"  # en dash

    # Single numeric grade: GRADE(S) 2.
    m = re.match(r"^(grades?|grade)\s+(\d+)$", t, flags=re.IGNORECASE)
    if m:
        n = int(m.group(2))
        return f"GRADE {n}"

    # Otherwise: leave unchanged (avoid accidental over-normalization).
    return title.strip()


def canonical_grouping_node_id(
    *, ancestor_grouping_keys: list[str], doc_key: str, grouping: GroupingDecision
) -> str:
    """Compute the canonical node ID for a grouping decision.

    Parameters
    ----------
    ancestor_grouping_keys
        The list of ancestor grouping keys.
    doc_key
        The document key.
    grouping
        The GroupingDecision to compute the node ID for.

    Returns
    -------
    str
        The computed canonical node ID.
    """

    path_fp = path_fingerprint(grouping_keys=ancestor_grouping_keys)
    code = grouping.local_code or "-"

    if code != "-":
        # Normalize local_code deterministically (whitespace + unicode dash).
        code = unicodedata.normalize("NFKC", code)
        code = _DASH_RE.sub("-", code)
        code = _WS_RE.sub(" ", code).strip()

    title = canonical_grouping_title(role=grouping.role, title=grouping.title)
    text_hash = _normalized_text_hash(text=title)

    key = canonical_key(
        doc_key=doc_key,
        local_code_or_dash=code,
        normalized_text_hash_hex=text_hash,
        path_fp=path_fp,
        role=grouping.role.value,
    )

    return uuidv5_from_key(key)


def canonical_grouping_title(*, role: NodeRole, title: str) -> str:
    """Canonicalize grouping titles in a role-aware way. This is intentionally
    conservative: we only normalize when we can do so deterministically and safely.

    Parameters
    ----------
    role
        The node role.
    title
        The original title.

    Returns
    -------
    str
        The canonicalized title.
    """

    if not title:
        return title

    if role == NodeRole.GRADE_LEVEL:
        return canonical_grade_level_title(title)

    return title.strip()


def canonical_key(
    *,
    doc_key: str,
    local_code_or_dash: str,
    normalized_text_hash_hex: str,
    path_fp: str,
    role: str,
) -> str:
    """Create a canonical key:

    lc:canonical:{doc_key}:{role}:{path_fingerprint}:{local_code_or_-}:{normalized_text_hash}

    Parameters
    ----------
    doc_key
        The document key.
    local_code_or_dash
        The local code or dash.
    normalized_text_hash_hex
        The normalized text hash hex.
    path_fp
        The path fingerprint.
    role
        The node role.

    Returns
    -------
    str
        The canonical key.
    """

    return (
        f"lc:canonical:{doc_key}:"
        f"{role}:"
        f"{path_fp}:"
        f"{local_code_or_dash}:"
        f"{normalized_text_hash_hex}"
    )


def canonical_leaf_node_id(
    *, ancestor_grouping_keys: list[str], doc_key: str, leaf: LeafDecision
) -> str:
    """Compute the canonical node ID for a leaf decision.

    Parameters
    ----------
    ancestor_grouping_keys
        The list of ancestor grouping keys.
    doc_key
        The document key.
    leaf
        The LeafDecision to compute the node ID for.

    Returns
    -------
    str
        The computed canonical node ID.
    """

    path_fp = path_fingerprint(grouping_keys=ancestor_grouping_keys)
    code = leaf.local_code or "-"

    if code != "-":
        # Normalize local_code deterministically (whitespace + unicode dash).
        code = unicodedata.normalize("NFKC", code)
        code = _DASH_RE.sub("-", code)
        code = _WS_RE.sub(" ", code).strip()

    text_hash = _normalized_text_hash(text=leaf.body)

    key = canonical_key(
        doc_key=doc_key,
        local_code_or_dash=code,
        normalized_text_hash_hex=text_hash,
        path_fp=path_fp,
        role=leaf.role.value,
    )

    return uuidv5_from_key(key)


def canonical_storage_text(text: Optional[str]) -> str:
    """Canonicalize text for storage in CanonicalNode.{title,body}.text. The goal here
    is to remove meaningless formatting noise while preserving original casing. This
    reduces formatting-diff warnings and makes merges stable.

    Parameters
    ----------
    text
        The text to canonicalize.

    Returns
    -------
    str
        The canonicalized text.
    """

    if not text:
        return ""

    text = unicodedata.normalize("NFKC", text)
    text = _DASH_RE.sub("-", text)
    text = _WS_RE.sub(" ", text).strip()

    return text


def clean_up_segment_decisions(
    *,
    creation_dirs: CanonicalIRDirs,
    overwrite: bool,
    segment_decisions: SegmentDecisionSet,
) -> SegmentDecisionSet:
    """Apply universal (non-heuristic) hygiene to all grouping fields in a decision
    set. This is intended to run BEFORE the LLM-based global grouping canonicalization
    step so that the mapping operates on stable strings (whitespace/quote/dash noise
    removed).

    Parameters
    ----------
    creation_dirs
        The canonical IR creation directories.
    overwrite
        Whether to overwrite existing cleaned decisions.
    segment_decisions
        The SegmentDecisionSet to clean.

    Returns
    -------
    SegmentDecisionSet
        The cleaned SegmentDecisionSet.
    """

    segment_decisions_cleaned_fp = (
        creation_dirs.segment_decisions / "segment_decisions_cleaned.json"
    )

    if not overwrite and segment_decisions_cleaned_fp.exists():
        logger.warning(
            f"Cleaned segment decisions JSON already exists at {segment_decisions_cleaned_fp}. "
            f"Reusing existing cleaned segment decisions. "
            f"If you wish to overwrite, pass the --overwrite flag."
        )
        return SegmentDecisionSet.model_validate(
            open_json_type(segment_decisions_cleaned_fp)
        )

    new_segment_decisions = []

    for d in segment_decisions.decisions:
        updates = {}

        if d.context_groupings:
            updates["context_groupings"] = [
                _clean_grouping(g) for g in d.context_groupings
            ]

        if d.groupings:
            updates["groupings"] = [_clean_grouping(g) for g in d.groupings]

        if d.leaves:
            updates["leaves"] = [_clean_leaf(leaf) for leaf in d.leaves]

        if d.rows:
            updates["rows"] = _clean_decision_rows(d.rows)

        new_segment_decisions.append(d.model_copy(update=updates))

    # NB: Recompute decision_set_id since decision content changed.
    new_id = compute_decision_set_id(decisions=new_segment_decisions)

    # Save cleaned decisions.
    segment_decisions_cleaned = segment_decisions.model_copy(
        update={"decision_set_id": new_id, "decisions": new_segment_decisions}
    )
    write_to_json(fp=segment_decisions_cleaned_fp, json_info=segment_decisions_cleaned)

    logger.success(
        f"Saved cleaned segment decisions to: {segment_decisions_cleaned_fp}"
    )

    return segment_decisions_cleaned


def collect_unique_grouping_keys(
    *,
    creation_dirs: CanonicalIRDirs,
    overwrite: bool,
    segment_decisions: SegmentDecisionSet,
) -> list[GroupingCanonicalizationKey]:
    """Collect the set of unique grouping candidates
    (role/title/local_code/source_label) from a SegmentDecisionSet to feed into the
    LLM-based global grouping canonicalizer.

    The process ensures:

    1. Input traversal is stable
    2. Dedupe uses exact tuple matching

    Parameters
    ----------
    creation_dirs
        The canonical IR creation directories.
    overwrite
        Whether to overwrite existing unique grouping keys.
    segment_decisions
        The SegmentDecisionSet to extract grouping keys from.

    Returns
    -------
    list[GroupingCanonicalizationKey]
        The list of unique grouping keys.
    """

    grouping_keys_unique_fp = (
        creation_dirs.segment_decisions / "grouping_keys_unique.json"
    )

    if not overwrite and grouping_keys_unique_fp.exists():
        logger.warning(
            f"Unique grouping keys JSON already exists at {grouping_keys_unique_fp}. "
            f"Reusing existing unique grouping keys. "
            f"If you wish to overwrite, pass the --overwrite flag."
        )
        return [
            GroupingCanonicalizationKey.model_validate(item)
            for item in open_json_type(grouping_keys_unique_fp)
        ]

    grouping_keys: list[GroupingCanonicalizationKey] = []
    seen: set[tuple[str, str, str, str]] = set()

    for g in _iter_all_grouping_decisions(segment_decisions):
        role = g.role
        title = (g.title or "").strip()
        assert title, f"GroupingDecision with empty title found: {g}"

        local_code = (g.local_code or "").strip()
        source_label = (g.source_label or "").strip()

        dedupe_key = (role.value, title, local_code, source_label)

        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        grouping_keys.append(
            GroupingCanonicalizationKey(
                local_code=(local_code or None),
                role=role,
                source_label=(source_label or None),
                title=title,
            )
        )

    grouping_keys.sort(
        key=lambda x: (x.role.value, x.title, x.local_code or "", x.source_label or "")
    )
    write_to_json(fp=grouping_keys_unique_fp, json_info=grouping_keys)

    logger.success(f"Saved unique grouping keys to: {creation_dirs.root}")

    return grouping_keys


def compile_canonical_ir(
    *,
    doc_key: str,
    document_ir: DocumentIR,
    segment_decision_conf_threshold: float,
    segment_decisions: SegmentDecisionSet,
    structural_leaf_warn_threshold: float,
) -> CanonicalIR:
    """Compile a CanonicalIR from DocumentIR and SegmentDecisionSet.

    The process is as follows:

    1. Initialize state containers.
    2. Index decisions by segment ID.
    3. Create Framework Root node.
    4. Main traversal loop:
        a. For each segment in DocumentIR:
            i.   Prepare segment-level data.
            ii.  If no decisions, log warning + add to unresolved.
            iii. For each decision for the segment:
                1. Validate decision; update unresolved/warnings; skip if not
                    materializable.
                2. Check for structural warnings; update warnings.
                3. Materialize nodes; update state containers.
    5. Final compilation of CanonicalIR.

    Parameters
    ----------
    doc_key
        The document key.
    document_ir
        The DocumentIR to process.
    segment_decision_conf_threshold
        The low confidence threshold for segment decisions (this ties into the segment
        decision system prompt).
    segment_decisions
        The SegmentDecisionSet to apply.
    structural_leaf_warn_threshold
        The confidence threshold below which structural leaves will emit warnings.

    Returns
    -------
    CanonicalIR
        The compiled CanonicalIR.
    """

    # 1.
    active_context_stack: list[ContextFrame] = []
    child_to_parent: dict[str, str] = {}
    edges: list[CanonicalEdge] = []
    edges_by_key: dict[tuple[str, str], CanonicalEdge] = {}
    next_order_index: dict[str, int] = defaultdict(int)
    nodes_by_id: dict[str, CanonicalNode] = {}
    unresolved: list[UnresolvedItem] = []
    warnings: list[str] = []

    # 2.
    decisions_by_segment = _index_decisions_by_segment(
        segment_decisions=segment_decisions
    )

    # 3.
    framework_title = segment_decisions.pdf_name
    root_id = uuidv5_from_key(f"lc:canonical:{doc_key}:framework")
    framework_node = CanonicalNode(
        bbox=None,
        body=None,
        list_marker=None,
        local_code=None,
        node_id=root_id,
        normalized_text=_normalize_text(text=framework_title),
        page_indices=[],
        role=NodeRole.FRAMEWORK,
        section_path_text=[],
        source_decision_ids=[],
        source_label=None,
        source_segment_ids=[],
        source_type=None,
        title=TextUnit(language="und", text=canonical_storage_text(framework_title)),
    )
    effective_root_id = ensure_node(
        node=framework_node, nodes_by_id=nodes_by_id, warnings=warnings
    )

    # 4.
    for segment in document_ir.segments:
        seg_id = segment.segment_id
        seg_decisions = decisions_by_segment.get(seg_id, [])

        # Prepare segment-level data (needed even when unresolved).
        page_indices = sorted({p.page_index for p in segment.segment_provenance})
        section_path_text = [h.text for h in (segment.section_path or [])]

        if not seg_decisions:
            msg = f"no_decision_for_segment:{seg_id}"
            logger.warning(msg)
            warnings.append(msg)
            unresolved.append(
                UnresolvedItem(
                    caption_text=None,
                    headers=(
                        _extract_table_headers(segment)
                        if segment.kind == "table"
                        else []
                    ),
                    kind=segment.kind,
                    local_code=segment.local_code,
                    page_indices=page_indices,
                    reason=(
                        UnresolvedReason.UNMATCHED_TABLE
                        if segment.kind == "table"
                        else UnresolvedReason.UNMATCHED_BLOCK
                    ),
                    sample=(segment.combined_text if segment.kind == "block" else None),
                    section_path_text=section_path_text,
                    segment_id=segment.segment_id,
                )
            )
            continue

        seg_decisions_sorted = sorted(seg_decisions, key=_decision_sort_key)

        # Process each decision for the segment.
        for decision in seg_decisions_sorted:
            # Check ignore/unresolved/low confidence.
            should_continue = _validate_and_handle_unresolved(
                decision=decision,
                page_indices=page_indices,
                section_path_text=section_path_text,
                segment=segment,
                segment_decision_conf_threshold=segment_decision_conf_threshold,
                unresolved=unresolved,
                warnings=warnings,
            )

            if not should_continue:
                continue

            # Check for structural warnings
            _check_structural_warnings(
                decision=decision,
                page_indices=page_indices,
                section_path_text=section_path_text,
                segment_id=seg_id,
                segment_kind=segment.kind,
                structural_leaf_warn_threshold=structural_leaf_warn_threshold,
                warnings=warnings,
            )

            # Materialize nodes.
            active_context_stack = _materialize_decision_structure(
                active_context_stack=active_context_stack,
                child_to_parent=child_to_parent,
                decision=decision,
                doc_key=doc_key,
                edges=edges,
                edges_by_key=edges_by_key,
                next_order_index=next_order_index,
                nodes_by_id=nodes_by_id,
                page_indices=page_indices,
                root_id=effective_root_id,
                section_path_text=section_path_text,
                segment=segment,
                warnings=warnings,
            )

    # 5.
    canonical_ir = CanonicalIR(
        decision_set_id=segment_decisions.decision_set_id,
        doc_key=doc_key,
        edges=edges,
        nodes=list(nodes_by_id.values()),
        pdf_name=segment_decisions.pdf_name,
        root_id=effective_root_id,
        segment_decisions=segment_decisions.decisions,
        unresolved=unresolved,
        warnings=warnings,
    )
    canonical_ir = perform_postpass_hygiene(
        canonical_ir=canonical_ir, document_ir=document_ir
    )

    logger.info(
        f"Compiled CanonicalIR:\n"
        f"nodes={len(canonical_ir.nodes)}\n"
        f"edges={len(canonical_ir.edges)}\n"
        f"unresolved={len(canonical_ir.unresolved)}\n"
        f"warnings={len(canonical_ir.warnings)}"
    )

    return canonical_ir


def create_canonical_ir_dirs(*, output_dir: Path) -> CanonicalIRDirs:
    """Create canonical IR directories for a given creation run.

    Parameters
    ----------
    output_dir
        The output directory root.

    Returns
    -------
    CanonicalDocumentIRDirs
        The created canonical document IR directories.
    """

    root = output_dir
    canonical_ir = root / "canonical_ir"
    segment_decisions = root / "segment_decisions"

    for p in [root, canonical_ir, segment_decisions]:
        make_dir(p)

    return CanonicalIRDirs(
        root=root, canonical_ir=canonical_ir, segment_decisions=segment_decisions
    )


def load_or_initialize_segment_decision_set(
    *,
    creation_dirs: CanonicalIRDirs,
    doc_key: str,
    document_ir: DocumentIR,
    segment_decisions_fp: Path | None,
) -> tuple[SegmentDecisionSet, set[str], Path]:
    """Load decision set if present, else initialize an empty one.

    Parameters
    ----------
    creation_dirs
        The canonical IR creation directories.
    doc_key
        The expected document key for the SegmentDecisionSet.
    document_ir
        The DocumentIR to reference for segment existence.
    segment_decisions_fp
        The file path to the SegmentDecisionSet JSON.

    Returns
    -------
    tuple[SegmentDecisionSet, set[str], Path]
        The loaded or initialized SegmentDecisionSet, the set of existing segment IDs,
        and the file path to the SegmentDecisionSet JSON.

    Raises
    ------
    ValueError
        If the SegmentDecisionSet refers to missing segment IDs.
    """

    segment_decisions_fp = (
        segment_decisions_fp or creation_dirs.root / "segment_decisions.json"
    )
    segment_decisions_fp = Path(segment_decisions_fp)

    decision_set = (
        _load_segment_decision_set(
            expected_doc_key=doc_key,
            pdf_name=document_ir.pdf_name,
            segment_decisions_fp=segment_decisions_fp,
        )
        if segment_decisions_fp.exists()
        else SegmentDecisionSet.model_validate(
            {
                "pdf_name": document_ir.pdf_name,
                "doc_key": doc_key,
                "decision_set_id": compute_decision_set_id(decisions=[]),
                "decisions": [],
            }
        )
    )

    # Ensure any existing decisions still refer to real segments.
    existing_segment_ids = {d.segment_id for d in decision_set.decisions}
    segments_by_id = {s.segment_id: s for s in document_ir.segments}
    missing = [sid for sid in existing_segment_ids if sid not in segments_by_id]

    if missing:
        raise ValueError(f"Decision set refers to missing segment_ids: {missing[:10]}")

    return decision_set, existing_segment_ids, segment_decisions_fp


def persist_canonical_run(
    *, config: CreateCanonicalConfig, output_dir: Path
) -> tuple[CanonicalIRDirs, RunCtx]:
    """Persist canonical IR creation run metadata.

    Parameters
    ----------
    config
        The canonical IR creation run configuration.
    output_dir
        The output directory for the canonical IR creation run results.

    Returns
    -------
    tuple[CanonicalIRDirs, RunCtx]
        The created canonical IR directories and persisted canonical IR creation run
        metadata.
    """

    creation_dirs = create_canonical_ir_dirs(output_dir=output_dir)
    creation_run = RunCtx(
        extra={},
        models=[config.model],
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(fp=output_dir / "creation_run.json", json_info=creation_run)
    logger.info(f"Saving canonical IR creation results to: {creation_dirs}")

    return creation_dirs, creation_run


def save_canonical_ir(*, canonical_ir: CanonicalIR, canonical_ir_fp: Path) -> None:
    """Export the canonical IR to a JSON file.

    Parameters
    ----------
    canonical_ir
        The CanonicalIR to serialize.
    canonical_ir_fp
        The output file path for the CanonicalIR JSON.
    """

    write_to_json(fp=canonical_ir_fp, json_info=canonical_ir)


def save_segment_decision_set(
    *, decision_set: SegmentDecisionSet, segment_decisions_fp: Path
) -> SegmentDecisionSet:
    """Write a SegmentDecisionSet with an updated stable decision_set_id.

    Parameters
    ----------
    decision_set
        The SegmentDecisionSet to serialize.
    segment_decisions_fp
        The output file path for the SegmentDecisionSet JSON.

    Returns
    -------
    SegmentDecisionSet
        The updated SegmentDecisionSet with recomputed decision_set_id.
    """

    # Recompute stable ID every write and keep the in-memory object consistent.
    new_id = compute_decision_set_id(decisions=decision_set.decisions)
    decision_set.decision_set_id = new_id

    write_to_json(fp=segment_decisions_fp, json_info=decision_set)

    return decision_set
