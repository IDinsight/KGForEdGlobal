"""This module contains utility functions for canonical Intermediate Representations."""

# Standard Library
import hashlib
import re
import unicodedata
import uuid

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

# Third Party Library
from loguru import logger

# Package Library
from skg.canonical_ir.llm import generate_segment_decision
from skg.canonical_ir.schemas import (
    CanonicalEdge,
    CanonicalIR,
    CanonicalNode,
    GroupingDecision,
    LeafDecision,
    SegmentDecision,
    SegmentDecisionSet,
    UnresolvedItem,
    compute_decision_set_id,
)
from skg.config import Settings
from skg.document_ir.schemas import BlockSegment, DocumentIR, Segment, TableSegment
from skg.page_ir_extraction.schemas import TextUnit
from skg.schemas import CreateCanonicalConfig, RunCtx
from skg.utils.constants import (
    BlockType,
    CaptionFigurePrefixes,
    CaptionKind,
    CaptionTablePrefixes,
    NodeRole,
    SegmentDecisionType,
    UnresolvedReason,
)
from skg.utils.general import make_dir, open_json_type, write_to_json

# Compiled regexes.
_DASH_RE = re.compile(r"[‐-‒–—−]+")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class CanonicalIRDirs:
    """Dataclass for canonical IR directories."""

    root: Path
    canonical_ir: Path
    segment_decisions: Path


@dataclass(frozen=True)
class CaptionBinding:
    """Dataclass for caption-to-table bindings."""

    caption_kind: CaptionKind
    caption_page_index: int | None
    caption_segment_id: str
    caption_text: str
    gap_segments: int
    table_page_index: int | None
    table_segment_id: str


@dataclass(frozen=True)
class ContextFrame:
    """One frame in the active context stack (excluding the framework root)."""

    grouping_key: str
    node_id: str


def _classify_caption_kind(text: str) -> CaptionKind:
    """Classify caption kind based on text prefixes.

    Parameters
    ----------
    text
        The caption text to classify.

    Returns
    -------
    CaptionKind
        The classified caption kind.
    """

    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)

    for p in CaptionTablePrefixes:
        if t.startswith(p):
            return "table"

    for p in CaptionFigurePrefixes:
        if t.startswith(p):
            return "figure"

    # Regex fallback for common patterns.
    if re.match(r"^(table|tab\.?|tbl\.?|jedwali|tableau)\s*\d+", t):
        return "table"

    if re.match(r"^(figure|fig\.?)\s*\d+", t):
        return "figure"

    return "unknown"


def _count_decision_leaves(d: SegmentDecision) -> int:
    """Count statement leaves emitted by this decision (block leaves + table row
    leaves).

    Parameters
    ----------
    d
        The SegmentDecision to count leaves for.

    Returns
    -------
    int
        The total number of leaves in the decision.
    """

    n = 0
    n += len(d.leaves or [])

    for r in d.rows or []:
        n += len(r.leaves or [])

    return n


def _decision_sort_key(d: SegmentDecision) -> tuple[int, int]:
    """Sort key for SegmentDecision ordering.

    Parameters
    ----------
    d
        The SegmentDecision to compute the sort key for.

    Returns
    -------
    tuple[int, int]
        The sort key as (row_range_start, row_range_end).
    """

    # Sorting rule: (row_range_start,row_range_end) with None treated consistently.
    start = d.row_range_start if d.row_range_start is not None else -1
    end = d.row_range_end if d.row_range_end is not None else 2**31 - 1

    return start, end


def _emit_edge(
    *,
    child_id: str,
    child_to_parent: dict[str, str],
    decision_id: str,
    edge_set: set[tuple[str, str]],
    edges: list[CanonicalEdge],
    next_order_index: dict[str, int],
    parent_id: str,
    segment_id: str,
    warnings: list[str],
) -> None:
    """Emit edge and assign order_index by encounter order.

    NB:

    1. Keep-first-parent tree enforcement.
    2. Drop and warn on parent conflicts.
    3. Assign order_index by first encounter order per parent.

    Parameters
    ----------
    child_id
        The child node ID.
    child_to_parent
        The mapping of child_id to parent_id.
    decision_id
        The SegmentDecision ID.
    edge_set
        The set of emitted edges (parent_id, child_id).
    edges
        The list of CanonicalEdges to append to.
    next_order_index
        The mapping of parent_id to next order_index.
    parent_id
        The parent node ID.
    segment_id
        The segment ID.
    warnings
        The list of warnings to append to.
    """

    # Tree enforcement (keep-first-parent).
    existing_parent = child_to_parent.get(child_id)
    if existing_parent is not None and existing_parent != parent_id:
        msg = (
            f"tree_parent_conflict_dropped:"
            f"child={child_id} "
            f"kept_parent={existing_parent} "
            f"dropped_parent={parent_id} "
            f"segment_id={segment_id} "
            f"decision_id={decision_id}"
        )
        logger.warning(msg)
        warnings.append(msg)
        # input(1)
        return

    # Edge dedupe.
    key = (parent_id, child_id)

    if key in edge_set:
        return

    # This is the first valid parent assignment for this child.
    child_to_parent[child_id] = parent_id

    # Deterministic sibling ordering.
    order = next_order_index[parent_id]
    next_order_index[parent_id] += 1

    edges.append(
        CanonicalEdge(
            child_id=child_id,
            order_index=order,
            parent_id=parent_id,
            source_decision_ids=[decision_id],
            source_segment_ids=[segment_id],
        )
    )
    edge_set.add(key)


def _extract_block_segment_text(segment: BlockSegment) -> str | None:
    """Extract text from a BlockSegment.

    Parameters
    ----------
    segment
        The BlockSegment to extract text from.

    Returns
    -------
    str | None
        The extracted text, or None if not found.
    """

    if segment.combined_text and segment.combined_text.strip():
        return segment.combined_text.strip()

    if isinstance(segment.text, TextUnit) and segment.text.text.strip():
        return segment.text.text.strip()

    if segment.list_items:
        parts: list[str] = []

        for list_item in segment.list_items:
            text_unit = list_item.text

            if text_unit.text.strip():
                parts.append(text_unit.text.strip())

        if parts:
            return "\n".join(parts)

    return None


def _extract_table_headers(segment: Segment) -> list[str]:
    """Best-effort extraction of header cell strings for unresolved table items.

    Parameters
    ----------
    segment
        The Segment to extract headers from.

    Returns
    -------
    list[str]
        The extracted header cell strings.
    """

    if segment.kind != "table":
        return []

    header_rows = getattr(segment, "header_rows", [])

    if not header_rows:
        return []

    # Use the last header row as "most specific".
    last = header_rows[-1]
    output: list[str] = []

    for cell in getattr(last, "cells", []) or []:
        tu = getattr(cell, "text", None)
        if tu and getattr(tu, "text", "").strip():
            output.append(tu.text.strip())

    return output


def _format_page_indices(page_indices: list[int]) -> str:
    """Format page indices as a comma-separated string.

    Parameters
    ----------
    page_indices
        The list of page indices to format.

    Returns
    -------
    str
        The formatted page indices string.
    """

    return "-" if not page_indices else ",".join(str(p) for p in page_indices)


def _format_section_path(*, max_items: int = 6, section_path_text: list[str]) -> str:
    """Compact section path for warnings. Keep the last N headings to avoid huge
    strings.

    Parameters
    ----------
    max_items
        The maximum number of section path items to keep.
    section_path_text
        The full section path text list.

    Returns
    -------
    str
        The compacted section path string.
    """

    if not section_path_text:
        return "-"

    tail = section_path_text[-max_items:]

    return " > ".join(tail)


def _grouping_key(g: GroupingDecision) -> str:
    """Create a stable string for path_fingerprint().

    NB: This key must NOT depend on canonical node_id (circular dependency).

    Parameters
    ----------
    g
        The GroupingDecision to compute the key for.

    Returns
    -------
    str
        The grouping key.
    """

    code = g.list_id or g.local_code or "-"

    return (
        f"{g.role.value}:{_normalize_text(text=g.title)}:{_normalize_text(text=code)}"
    )


def _make_unresolved_sample(
    *, decision: SegmentDecision, max_len: int = 280, segment: Segment
) -> str:
    """Create a short, human-debuggable preview string for UnresolvedItem.sample.

    Parameters
    ----------
    decision
        The SegmentDecision to create a sample for.
    max_len
        The maximum length of the sample string.
    segment
        The Segment to create a sample for.

    Returns
    -------
    str
        The generated sample string.
    """

    parts: list[str] = []
    parts.append(f"type={decision.decision_type.value} conf={decision.confidence:.2f}")

    rationale = getattr(decision, "rationale", None)

    if rationale:
        parts.append(f"rationale={rationale}")

    # Use segment text for blocks when available.
    text_or_none = segment.text
    segment_text = text_or_none.text if isinstance(text_or_none, TextUnit) else None

    if segment_text:
        parts.append(segment_text)

    s = " | ".join(parts).strip()

    return s[:max_len]


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


def _stable_extend_unique(*, base: list[str], extra: list[str]) -> list[str]:
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
    list[str]
        The extended list of strings.
    """

    seen = set(base)
    out = list(base)

    for x in extra:
        if x not in seen:
            out.append(x)
            seen.add(x)

    return out


def apply_caption_binding_to_table_payload(
    *, caption_bindings: CaptionBinding | None, table_payload: dict[str, Any]
) -> dict[str, Any]:
    """Apply caption binding information to a table segment payload.

    Parameters
    ----------
    caption_bindings
        The CaptionBinding to apply, or None to skip.
    table_payload
        The table segment payload to update.

    Returns
    -------
    dict[str, Any]
        The updated table segment payload.
    """

    if not caption_bindings:
        return table_payload

    table_payload["caption_gap_segments"] = caption_bindings.gap_segments
    table_payload["caption_kind"] = caption_bindings.caption_kind
    table_payload["caption_page_index"] = caption_bindings.caption_page_index
    table_payload["caption_segment_id"] = caption_bindings.caption_segment_id
    table_payload["caption_text"] = caption_bindings.caption_text

    return table_payload


def attach_caption_binding_to_segment_decision(
    *, caption_binding: CaptionBinding | None, segment_decision: SegmentDecision
) -> SegmentDecision:
    """Persist caption binding provenance on the SegmentDecision.

    Parameters
    ----------
    caption_binding
        The CaptionBinding to apply, or None to skip.
    segment_decision
        The SegmentDecision to update.

    Returns
    -------
    SegmentDecision
        The updated SegmentDecision.
    """

    if not caption_binding:
        return segment_decision

    segment_decision.caption_gap_segments = caption_binding.gap_segments
    segment_decision.caption_kind = caption_binding.caption_kind
    segment_decision.caption_page_index = caption_binding.caption_page_index
    segment_decision.caption_segment_id = caption_binding.caption_segment_id
    segment_decision.caption_text = caption_binding.caption_text

    return segment_decision


def build_caption_bindings(
    *,
    bind_unknown_caption: bool = True,
    creation_dirs: CanonicalIRDirs,
    document_ir: DocumentIR,
    max_gap_segments: int = 2,
    max_page_distance: int = 1,
) -> dict[str, CaptionBinding]:
    """Bind Caption block to next Table segment (within limits).

    Parameters
    ----------
    bind_unknown_caption
        Whether to bind captions of unknown kind.
    creation_dirs
        The canonical IR creation directories.
    document_ir
        The DocumentIR to process.
    max_gap_segments
        The maximum number of non-table segments allowed between caption and table.
    max_page_distance
        The maximum page distance allowed between caption and table.

    Returns
    -------
    dict[str, CaptionBinding]
        The computed caption bindings, keyed by table segment ID.
    """

    caption_bindings: dict[str, CaptionBinding] = {}
    warnings: list[str] = []

    # (caption_segment, caption_text, caption_kind, caption_page, caption_index)
    pending_caption: tuple[BlockSegment, str, CaptionKind, int, int] | None = None

    for index, segment in enumerate(document_ir.segments):
        page_index = (
            segment.slices[0].page_index
            if segment.slices
            else segment.segment_provenance[0].page_index
        )
        assert isinstance(page_index, int) and page_index >= 0

        # Explicit caption candidate.
        if segment.kind == "block":
            caption_text = _extract_block_segment_text(segment)

            if segment.block_type == BlockType.CAPTION and caption_text:
                kind = _classify_caption_kind(caption_text)

                # Don't bind figure captions to tables.
                if kind == "figure" or (kind == "unknown" and not bind_unknown_caption):
                    continue

                pending_caption = (
                    segment,
                    caption_text,
                    kind,
                    page_index,
                    index,
                )

                continue

        # Bind to next table if eligible.
        if segment.kind == "table" and pending_caption is not None:
            cap_seg, cap_text, cap_kind, cap_page, cap_index = pending_caption
            gap = max(0, index - cap_index - 1)
            page_dist = abs(page_index - cap_page)

            if gap <= max_gap_segments and page_dist <= max_page_distance:
                caption_bindings[segment.segment_id] = CaptionBinding(
                    caption_kind=cap_kind,
                    caption_page_index=cap_page,
                    caption_segment_id=cap_seg.segment_id,
                    caption_text=cap_text,
                    gap_segments=gap,
                    table_page_index=page_index,
                    table_segment_id=segment.segment_id,
                )
            else:
                msg = (
                    f"Dangling caption dropped: "
                    f"caption={cap_seg.segment_id} gap={gap} page_dist={page_dist}"
                )
                logger.warning(msg)
                warnings.append(msg)
                # input(1)

            pending_caption = None

            continue

        # Expire pending caption if too far.
        if pending_caption is not None:
            cap_seg, _cap_text, _cap_kind, _cap_page, cap_index = pending_caption
            gap = max(0, index - cap_index - 1)
            if gap > max_gap_segments:
                msg = (
                    f"Dangling caption dropped: "
                    f"caption={cap_seg.segment_id} gap_exceeded={gap}"
                )
                logger.warning(msg)
                warnings.append(msg)
                pending_caption = None
                # input(1)

    if pending_caption is not None:
        cap_seg, *_ = pending_caption
        msg = f"Dangling caption dropped: caption={cap_seg.segment_id} end_of_document"
        logger.warning(msg)
        warnings.append(msg)
        # input(1)

    warnings_fp = creation_dirs.root / "caption_binding_warnings.json"
    write_to_json(fp=warnings_fp, json_info={"warnings": warnings})

    return caption_bindings


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
    code = grouping.local_code or grouping.list_id or "-"
    text_hash = _normalized_text_hash(text=grouping.title)

    key = canonical_key(
        doc_key=doc_key,
        local_code_or_dash=code,
        normalized_text_hash_hex=text_hash,
        path_fp=path_fp,
        role=grouping.role.value,
    )

    return uuidv5_from_key(key)


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
    code = leaf.list_id or "-"
    text_hash = _normalized_text_hash(text=leaf.body)

    key = canonical_key(
        doc_key=doc_key,
        local_code_or_dash=code,
        normalized_text_hash_hex=text_hash,
        path_fp=path_fp,
        role=leaf.role.value,
    )

    return uuidv5_from_key(key)


def compile_canonical_ir(
    *,
    doc_key: str,
    document_ir: DocumentIR,
    low_conf_threshold: float = 0.80,
    segment_decisions: SegmentDecisionSet,
    structural_leaf_warn_threshold: float = 0.80,
) -> CanonicalIR:
    """Compile a CanonicalIR from DocumentIR and SegmentDecisionSet.

    The process is as follows:

    1. Iterate DocumentIR.segments in order.
    2. Load all decisions for the segment.
    3. Sort decisions by (row_range_start, row_range_end).
    4. Materialize nodes + edges
    5. Assign order_index by encounter order.

    Parameters
    ----------
    doc_key
        The document key.
    document_ir
        The DocumentIR to process.
    low_conf_threshold
        The low confidence threshold for warnings (this comes from the system prompt
        for the LLM).
    segment_decisions
        The SegmentDecisionSet to apply.
    structural_leaf_warn_threshold
        The confidence threshold below which structural leaves will emit warnings.

    Returns
    -------
    CanonicalIR
        The compiled CanonicalIR.
    """

    # Index decisions by segment_id for faster lookups.
    decisions_by_segment: dict[str, list[SegmentDecision]] = defaultdict(list)
    for d in segment_decisions.decisions:
        assert isinstance(d.segment_id, str) and d.segment_id
        decisions_by_segment[d.segment_id].append(d)

    active_context_stack: list[ContextFrame] = []
    child_to_parent: dict[str, str] = {}
    edge_set: set[tuple[str, str]] = set()
    edges: list[CanonicalEdge] = []
    next_order_index: dict[str, int] = defaultdict(int)
    nodes_by_id: dict[str, CanonicalNode] = {}
    unresolved: list[UnresolvedItem] = []
    warnings: list[str] = []

    # Framework root.
    framework_title = segment_decisions.pdf_name
    root_id = uuidv5_from_key(f"lc:canonical:{doc_key}:framework")
    framework_node = CanonicalNode(
        bbox=None,
        body=None,
        list_id=None,
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
        title=TextUnit(language="und", text=framework_title),
    )
    ensure_node(node=framework_node, nodes_by_id=nodes_by_id)

    # Main traversal loop.
    for segment in document_ir.segments:
        seg_id = segment.segment_id
        seg_kind = segment.kind  # "block" | "table"
        seg_decisions = decisions_by_segment.get(seg_id, [])

        if not seg_decisions:
            msg = f"no_decision_for_segment:{seg_id}"
            logger.warning(msg)
            warnings.append(f"no_decision_for_segment:{seg_id}")
            # input(1)

            continue

        page_indices = sorted({p.page_index for p in segment.segment_provenance})
        section_path_text = [h.text for h in (segment.section_path or [])]
        seg_decisions_sorted = sorted(seg_decisions, key=_decision_sort_key)

        # Process each decision for the segment.
        for d in seg_decisions_sorted:
            if d.decision_type == SegmentDecisionType.IGNORE:
                continue

            if d.decision_type == SegmentDecisionType.UNRESOLVED:
                unresolved.append(
                    UnresolvedItem(
                        caption_text=d.caption_text,
                        headers=[],
                        local_code=segment.local_code,
                        kind=seg_kind,
                        page_indices=page_indices,
                        reason=(
                            UnresolvedReason.UNMATCHED_TABLE
                            if seg_kind == "table"
                            else UnresolvedReason.UNMATCHED_BLOCK
                        ),
                        sample=None,
                        section_path_text=section_path_text,
                        segment_id=seg_id,
                    )
                )
                continue

            # Confidence gating.
            if d.confidence < low_conf_threshold:
                msg = (
                    f"low_confidence_decision_not_materialized:"
                    f"segment_id={seg_id} decision_id={d.decision_id} "
                    f"kind={seg_kind} conf={d.confidence:.3f} threshold={low_conf_threshold:.3f}"
                )
                logger.warning(msg)
                warnings.append(msg)
                unresolved.append(
                    UnresolvedItem(
                        caption_text=d.caption_text,
                        headers=(
                            _extract_table_headers(segment)
                            if seg_kind == "table"
                            else []
                        ),
                        kind=seg_kind,
                        local_code=getattr(segment, "local_code", None),
                        page_indices=page_indices,
                        reason="LOW_CONFIDENCE_DECISION_NOT_MATERIALIZED",
                        sample=_make_unresolved_sample(decision=d, segment=segment),
                        section_path_text=section_path_text,
                        segment_id=seg_id,
                    )
                )
                # input(1)

                continue

            # Structural leaf review warning layer. Only applies to decisions that are
            # being materialized (i.e. passed low_conf_threshold).
            if d.confidence < structural_leaf_warn_threshold:
                leaf_count = _count_decision_leaves(d)
                if leaf_count > 0:
                    pages_str = _format_page_indices(page_indices)
                    path_str = _format_section_path(section_path_text=section_path_text)
                    if d.row_range_start is not None or d.row_range_end is not None:
                        start = (
                            d.row_range_start if d.row_range_start is not None else "-"
                        )
                        end = d.row_range_end if d.row_range_end is not None else "-"
                        row_range_str = f"[{start},{end})"
                    else:
                        row_range_str = "-"
                    msg = (
                        f"structural_leaf_review:"
                        f"segment_id={seg_id} decision_id={d.decision_id} "
                        f"kind={seg_kind} conf={d.confidence:.3f} "
                        f"leaf_count={leaf_count} threshold={structural_leaf_warn_threshold:.3f} "
                        f"row_range={row_range_str} "
                        f"pages={pages_str} "
                        f"section_path={path_str}"
                    )
                    logger.warning(msg)
                    warnings.append(msg)
                    # input(1)

            # Context stack reconciliation.
            parent_id, ancestor_keys, active_context_stack = reconcile_context_stack(
                active_stack=active_context_stack,
                child_to_parent=child_to_parent,
                decision=d,
                desired_context=d.context_groupings,
                doc_key=doc_key,
                edge_set=edge_set,
                edges=edges,
                next_order_index=next_order_index,
                nodes_by_id=nodes_by_id,
                root_id=root_id,
                segment=segment,
                warnings=warnings,
            )

            # Apply decision.groupings[] under the context stack tip.
            for g in d.groupings:
                node_id = canonical_grouping_node_id(
                    ancestor_grouping_keys=ancestor_keys, doc_key=doc_key, grouping=g
                )

                node = CanonicalNode(
                    bbox=None,
                    body=None,
                    list_id=g.list_id,
                    local_code=g.local_code,
                    node_id=node_id,
                    normalized_text=_normalize_text(text=g.title),
                    page_indices=page_indices,
                    role=g.role,
                    section_path_text=section_path_text,
                    source_decision_ids=[d.decision_id],
                    source_label=g.source_label,
                    source_segment_ids=[seg_id],
                    source_type=seg_kind,
                    title=TextUnit(language="und", text=g.title),
                )

                ensure_node(node=node, nodes_by_id=nodes_by_id)
                _emit_edge(
                    child_id=node_id,
                    child_to_parent=child_to_parent,
                    decision_id=d.decision_id,
                    edge_set=edge_set,
                    edges=edges,
                    next_order_index=next_order_index,
                    parent_id=parent_id,
                    segment_id=seg_id,
                    warnings=warnings,
                )

                parent_id = node_id
                ancestor_keys.append(_grouping_key(g))

            # Materialize leaves.
            # Block segment leaves live directly under (context + segment groupings).
            if seg_kind == "block":
                for leaf in d.leaves:
                    leaf_id = canonical_leaf_node_id(
                        ancestor_grouping_keys=ancestor_keys, doc_key=doc_key, leaf=leaf
                    )

                    node = CanonicalNode(
                        bbox=None,
                        body=TextUnit(language="und", text=leaf.body),
                        list_id=leaf.list_id,
                        local_code=None,
                        node_id=leaf_id,
                        normalized_text=_normalize_text(text=leaf.body),
                        page_indices=page_indices,
                        role=leaf.role,
                        section_path_text=section_path_text,
                        source_decision_ids=[d.decision_id],
                        source_label=leaf.source_label,
                        source_segment_ids=[seg_id],
                        source_type=seg_kind,
                        title=None,
                    )

                    ensure_node(node=node, nodes_by_id=nodes_by_id)
                    _emit_edge(
                        child_id=leaf_id,
                        child_to_parent=child_to_parent,
                        decision_id=d.decision_id,
                        edge_set=edge_set,
                        edges=edges,
                        next_order_index=next_order_index,
                        parent_id=parent_id,
                        segment_id=seg_id,
                        warnings=warnings,
                    )
            # Table segment leaves live under per-row groupings.
            elif seg_kind == "table":
                for row in sorted(d.rows, key=lambda r: r.row_index):
                    row_parent_id = parent_id
                    row_ancestor_keys = list(ancestor_keys)

                    for g in row.groupings:
                        node_id = canonical_grouping_node_id(
                            ancestor_grouping_keys=row_ancestor_keys,
                            doc_key=doc_key,
                            grouping=g,
                        )

                        node = CanonicalNode(
                            bbox=None,
                            body=None,
                            list_id=g.list_id,
                            local_code=g.local_code,
                            node_id=node_id,
                            normalized_text=_normalize_text(text=g.title),
                            page_indices=page_indices,
                            role=g.role,
                            section_path_text=section_path_text,
                            source_decision_ids=[d.decision_id],
                            source_label=g.source_label,
                            source_segment_ids=[seg_id],
                            source_type=seg_kind,
                            title=TextUnit(language="und", text=g.title),
                        )

                        ensure_node(node=node, nodes_by_id=nodes_by_id)
                        _emit_edge(
                            child_id=node_id,
                            child_to_parent=child_to_parent,
                            decision_id=d.decision_id,
                            edge_set=edge_set,
                            edges=edges,
                            next_order_index=next_order_index,
                            parent_id=row_parent_id,
                            segment_id=seg_id,
                            warnings=warnings,
                        )

                        row_parent_id = node_id
                        row_ancestor_keys.append(_grouping_key(g))

                    for leaf in row.leaves:
                        leaf_id = canonical_leaf_node_id(
                            ancestor_grouping_keys=row_ancestor_keys,
                            doc_key=doc_key,
                            leaf=leaf,
                        )

                        node = CanonicalNode(
                            bbox=None,
                            body=TextUnit(language="und", text=leaf.body),
                            list_id=leaf.list_id,
                            local_code=None,
                            node_id=leaf_id,
                            normalized_text=_normalize_text(text=leaf.body),
                            page_indices=page_indices,
                            role=leaf.role,
                            section_path_text=section_path_text,
                            source_decision_ids=[d.decision_id],
                            source_label=leaf.source_label,
                            source_segment_ids=[seg_id],
                            source_type=seg_kind,
                            title=None,
                        )

                        ensure_node(node=node, nodes_by_id=nodes_by_id)
                        _emit_edge(
                            child_id=leaf_id,
                            child_to_parent=child_to_parent,
                            decision_id=d.decision_id,
                            edge_set=edge_set,
                            edges=edges,
                            next_order_index=next_order_index,
                            parent_id=row_parent_id,
                            segment_id=seg_id,
                            warnings=warnings,
                        )
            else:
                msg = f"unknown_segment_kind:{seg_kind}:{seg_id}"
                logger.warning(msg)
                warnings.append(msg)
                # input(1)

    canonical_ir = CanonicalIR(
        decision_set_id=segment_decisions.decision_set_id,
        doc_key=doc_key,
        edges=edges,
        nodes=list(nodes_by_id.values()),
        pdf_name=segment_decisions.pdf_name,
        root_id=root_id,
        segment_decisions=segment_decisions.decisions,
        unresolved=unresolved,
        warnings=warnings,
    )
    canonical_ir = perform_postpass_hygiene(canonical_ir)

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


def decision_key(
    segment_decision: SegmentDecision,
) -> tuple[str, int | None, int | None]:
    """Compute a unique key for a SegmentDecision based on segment_id and row range.

    Parameters
    ----------
    segment_decision
        The SegmentDecision to compute the key for.

    Returns
    -------
    tuple[str, int | None, int | None]
        The unique key as (segment_id, row_range_start, row_range_end).
    """

    return (
        segment_decision.segment_id or "",
        segment_decision.row_range_start,
        segment_decision.row_range_end,
    )


def dedupe_edges_postpass(
    *, edges: list[CanonicalEdge], node_ids: set[str], warnings: list[str]
) -> list[CanonicalEdge]:
    """Dedupe edges in a post-pass after all edges have been emitted:

    1. Drop dangling edges (missing nodes)
    2. Dedupe edges by (parent_id, child_id, rel)
    3. Merge provenance lists deterministically
    4. keep first order_index encountered

    Parameters
    ----------
    edges
        The list of CanonicalEdges to dedupe.
    node_ids
        The set of valid node IDs.
    warnings
        The list of warnings to append to.

    Returns
    -------
    list[CanonicalEdge]
        The deduped list of CanonicalEdges.
    """

    merged: dict[tuple[str, str, str], CanonicalEdge] = {}

    for e in edges:
        # Drop edges referencing missing nodes.
        if e.parent_id not in node_ids or e.child_id not in node_ids:
            msg = f"dangling_edge_dropped:parent={e.parent_id} child={e.child_id}"
            logger.warning(msg)
            warnings.append(msg)
            # input(1)

            continue

        key = (e.parent_id, e.child_id, e.rel)

        if key not in merged:
            merged[key] = e
            continue

        m = merged[key]

        # order_index should be stable; keep first and warn if mismatch
        if m.order_index != e.order_index:
            msg = (
                f"edge_order_index_conflict_kept_first:"
                f"parent={e.parent_id} child={e.child_id} kept={m.order_index} dropped={e.order_index}"
            )
            logger.warning(msg)
            warnings.append(msg)
            # input(1)

        m.source_segment_ids = _stable_extend_unique(
            base=m.source_segment_ids, extra=e.source_segment_ids
        )
        m.source_decision_ids = _stable_extend_unique(
            base=m.source_decision_ids, extra=e.source_decision_ids
        )

    # Preserve deterministic order: first-seen edge key order.
    return list(merged.values())


def ensure_node(*, node: CanonicalNode, nodes_by_id: dict[str, CanonicalNode]) -> None:
    """Ensure a CanonicalNode is present in nodes_by_id, merging provenance if needed.

    Parameters
    ----------
    node
        The CanonicalNode to ensure.
    nodes_by_id
        The mapping of node_id to CanonicalNode.
    """

    if node.node_id not in nodes_by_id:
        nodes_by_id[node.node_id] = node
        return

    # Merge minimal provenance (should be safe and deterministic).
    existing = nodes_by_id[node.node_id]
    existing.page_indices = sorted(set(existing.page_indices + node.page_indices))
    existing.source_segment_ids = sorted(
        set(existing.source_segment_ids + node.source_segment_ids)
    )
    existing.source_decision_ids = sorted(
        set(existing.source_decision_ids + node.source_decision_ids)
    )


def load_segment_decision_set(
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

    decision_set = SegmentDecisionSet.model_validate(
        open_json_type(segment_decisions_fp)
    )

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


def make_table_chunk_payload(
    *,
    context_rows_after: int = 2,
    context_rows_before: int = 2,
    end: int,
    segment: TableSegment,
    start: int,
) -> dict[str, Any]:
    """Build a table chunk payload for the LLM as follows:

    1. Keep table metadata + headers
    2. Replace `rows` with ONLY the rows in [start,end)
    3. Adds abs_row_index to each provided row
    4. Adds a `chunking` object so prompts can instruct absolute indexing
    5. Adds `context_rows_before` containing up to N rows immediately preceding `start`
       (context-only; the LLM MUST NOT emit RowDecision for these rows).
    6. Adds `context_rows_after` containing up to M rows immediately following `end`
       (context-only; the LLM MUST NOT emit RowDecision for these rows).
    7. If `rows_filldown` exists in the segment, uses it to produce a fill-down view of
       ONLY the decision rows. The filled rows become the main `rows` payload, and the
       raw visual decision rows are preserved under `rows_original`.

    Parameters
    ----------
    context_rows_after
        The number of context rows to include after the chunk end.
    context_rows_before
        The number of context rows to include before the chunk start.
    end
        The exclusive end row index for the chunk.
    segment
        The TableSegment to chunk.
    start
        The inclusive start row index for the chunk.

    Returns
    -------
    dict[str, Any]
        The table chunk payload.
    """

    seg = segment.model_dump(mode="json")

    # NB: Chunk payload should not include full-table derived views that can leak
    # information outside the chunk. NB: We intentionally KEEP rows_filldown here (if
    # present) but slice it down to the decision-row window so it does not expose the
    # entire table.
    for k in ("rows_grid", "grid_sources", "row_provenance"):
        seg.pop(k, None)

    full_rows_raw = seg.get("rows") or []
    full_rows_filldown: list[dict[str, Any]] | None = seg.get("rows_filldown")

    # Context windows (before/after).
    ctx_before_start = max(0, start - max(0, int(context_rows_before or 0)))
    ctx_after_end = min(len(full_rows_raw), end + max(0, int(context_rows_after or 0)))

    context_rows_before_payload: list[dict[str, Any]] = []
    context_rows_after_payload: list[dict[str, Any]] = []

    for abs_i in range(ctx_before_start, start):
        row = dict(full_rows_raw[abs_i])
        row["abs_row_index"] = abs_i
        row["is_context_only"] = True
        context_rows_before_payload.append(row)

    for abs_i in range(end, ctx_after_end):
        row = dict(full_rows_raw[abs_i])
        row["abs_row_index"] = abs_i
        row["is_context_only"] = True
        context_rows_after_payload.append(row)

    # Decision rows: raw visual + optional fill-down view.
    decision_rows_raw: list[dict[str, Any]] = []
    decision_rows_payload: list[dict[str, Any]] = []

    # Prefer fill-down view (if available) for primary `rows`, because validators
    # ground row-local groupings against visible row text.
    use_filldown = (full_rows_filldown is not None) and len(full_rows_filldown) == len(
        full_rows_raw
    )

    for abs_i in range(start, end):
        raw_row = dict(full_rows_raw[abs_i])
        raw_row["abs_row_index"] = abs_i
        raw_row["is_context_only"] = False
        decision_rows_raw.append(raw_row)

        if use_filldown:
            assert full_rows_filldown is not None
            fd_row = dict(full_rows_filldown[abs_i])
            fd_row["abs_row_index"] = abs_i
            fd_row["is_context_only"] = False
            decision_rows_payload.append(fd_row)
        else:
            decision_rows_payload.append(raw_row)

    # Primary decision rows (potentially fill-down adjusted)/
    seg["rows"] = decision_rows_payload

    # Preserve raw visual decision rows for audit/debug.
    seg["rows_original"] = decision_rows_raw

    # Preserve context rows separately (raw visual).
    seg["context_rows_before"] = context_rows_before_payload
    seg["context_rows_after"] = context_rows_after_payload

    # Keep ONLY the decision-row slice of rows_filldown for explicitness/debugging.
    if use_filldown:
        seg["rows_filldown"] = [dict(r) for r in decision_rows_payload]
    else:
        seg.pop("rows_filldown", None)

    seg["chunking"] = {
        "row_range_start": start,
        "row_range_end": end,
        "row_range_end_is_exclusive": True,
        "row_index_is_absolute": True,
        "context_rows_before_start": ctx_before_start,
        "context_rows_before_end": start,
        "context_rows_before_count": len(context_rows_before_payload),
        "context_rows_after_start": end,
        "context_rows_after_end": ctx_after_end,
        "context_rows_after_count": len(context_rows_after_payload),
        "rows_are_filldown_view": use_filldown,
        "rows_original_preserved": True,
    }

    return seg


def make_table_full_payload(*, segment: TableSegment) -> dict[str, Any]:
    """Build a FULL (unchunked) table payload for the LLM.

    This mirrors `make_table_chunk_payload` but includes ALL rows. Critically, it:

    1. Prefers `rows_filldown` (if available) so row-level groupings are grounded
        in-row (raw visual rows are preserved under `rows_original`).
    2. Adds `abs_row_index` to every row so validators can enforce grounding
    3. Adds a lightweight `chunking` object indicating absolute indices

    Parameters
    ----------
    segment
        The TableSegment to process.

    Returns
    -------
    dict[str, Any]
        The full table payload.
    """

    seg = segment.model_dump(mode="json")

    # Prefer fill-down view if it exists.
    rows_raw = seg.get("rows") or []
    rows_filldown = seg.get("rows_filldown")

    use_filldown = (
        isinstance(rows_filldown, list)
        and len(rows_filldown) == len(rows_raw)
        and len(rows_raw) > 0
    )

    if use_filldown:
        seg["rows_original"] = rows_raw
        seg["rows"] = rows_filldown  # Store rows_filldown here before removing
        seg["rows_original_preserved"] = True
    else:
        seg["rows_original_preserved"] = False

    # NB: Remove derived structures that bloat the prompt. We intentionally keep the
    # filldown effect by swapping seg["rows"] above.
    for k in ("rows_grid", "rows_filldown", "grid_sources", "row_provenance"):
        seg.pop(k, None)

    rows = seg.get("rows") or []

    # Add abs_row_index to every row (headers included).
    for abs_i, row in enumerate(rows):
        if isinstance(row, dict):
            row["abs_row_index"] = abs_i

    seg["rows"] = rows
    seg["chunking"] = {
        "row_range_start": 0,
        "row_range_end": len(rows),
        "row_range_end_is_exclusive": True,
        "row_index_is_absolute": True,
    }

    return seg


def merge_nodes_postpass(
    *, nodes: list[CanonicalNode], warnings: list[str]
) -> list[CanonicalNode]:
    """Merge CanonicalNodes with the same node_id, combining provenance.

    Parameters
    ----------
    nodes
        The list of CanonicalNodes to merge.
    warnings
        A list to append warning messages to.

    Returns
    -------
    list[CanonicalNode]
        The merged list of CanonicalNodes.
    """

    merged: dict[str, CanonicalNode] = {}

    for n in nodes:
        if n.node_id not in merged:
            merged[n.node_id] = n

            continue

        m = merged[n.node_id]

        # Sanity check: role + title/body should be consistent.
        if m.role != n.role:
            msg = f"node_merge_role_conflict:{n.node_id} kept={m.role} dropped={n.role}"
            logger.warning(msg)
            warnings.append(msg)
            # input(1)

        if (m.title is None) != (n.title is None) or (m.body is None) != (
            n.body is None
        ):
            msg = f"node_merge_title_body_shape_conflict:{n.node_id}"
            logger.warning(msg)
            warnings.append(msg)
            # input(1)

        # Merge provenance deterministically.
        m.page_indices = sorted(set(m.page_indices + n.page_indices))
        m.source_segment_ids = _stable_extend_unique(
            base=m.source_segment_ids, extra=n.source_segment_ids
        )
        m.source_decision_ids = _stable_extend_unique(
            base=m.source_decision_ids, extra=n.source_decision_ids
        )
        m.section_path_text = _stable_extend_unique(
            base=m.section_path_text, extra=n.section_path_text
        )

        # Merge optional fields conservatively: keep first non-null.
        if m.normalized_text is None and n.normalized_text is not None:
            m.normalized_text = n.normalized_text

        if m.source_label is None and n.source_label is not None:
            m.source_label = n.source_label

        if m.source_type is None and n.source_type is not None:
            m.source_type = n.source_type

        if m.list_id is None and n.list_id is not None:
            m.list_id = n.list_id

        if m.local_code is None and n.local_code is not None:
            m.local_code = n.local_code

        # bbox: keep first non-null.
        if m.bbox is None and n.bbox is not None:
            m.bbox = n.bbox

    # Preserve deterministic order: first-seen node_id order.
    return list(merged.values())


def path_fingerprint(*, encoding: str = "utf-8", grouping_keys: Iterable[str]) -> str:
    """Create a short stable fingerprint of the ancestor grouping key sequence.

    Parameters
    ----------
    encoding
        The string encoding to use.
    grouping_keys
        The sequence of ancestor grouping keys.

    Returns
    -------
    str
        The computed path fingerprint.
    """

    joined = ">".join(grouping_keys)

    if not joined:
        return "-"

    return hashlib.sha256(joined.encode(encoding)).hexdigest()[:16]


def perform_postpass_hygiene(canonical_ir: CanonicalIR) -> CanonicalIR:
    """Perform post-pass hygiene on a CanonicalIR:

    1. Node merge
    2. Edge dedupe
    3. Sanity checks

    Parameters
    ----------
    canonical_ir
        The CanonicalIR to process.

    Returns
    -------
    CanonicalIR
        An updated CanonicalIR.
    """

    warnings = list(canonical_ir.warnings)

    nodes_merged = merge_nodes_postpass(nodes=canonical_ir.nodes, warnings=warnings)
    node_ids = {n.node_id for n in nodes_merged}

    edges_merged = dedupe_edges_postpass(
        edges=canonical_ir.edges, node_ids=node_ids, warnings=warnings
    )

    sanity_checks_postpass(
        edges=edges_merged,
        nodes=nodes_merged,
        root_id=canonical_ir.root_id,
        warnings=warnings,
    )

    return canonical_ir.model_copy(
        update={"nodes": nodes_merged, "edges": edges_merged, "warnings": warnings}
    )


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


def process_segment_decisions(
    *,
    caption_bindings: dict[str, CaptionBinding | None],
    config: CreateCanonicalConfig,
    decision_set: SegmentDecisionSet,
    doc_key: str,
    existing_keys: set[tuple[str, Optional[int], Optional[int]]],
    segment: Segment,
    segment_decisions_fp: Path,
    warnings: list[str],
) -> SegmentDecisionSet:
    """Process a single segment to generate and persist decisions.

    Parameters
    ----------
    caption_bindings
        The caption bindings to apply to table segments.
    config
        The canonical IR creation run configuration.
    decision_set
        The current SegmentDecisionSet to update.
    doc_key
        The expected document key for all page IRs.
    existing_keys
        The set of existing decision keys to avoid duplicates.
    segment
        The Segment to process.
    segment_decisions_fp
        The output file path for the SegmentDecisionSet JSON.
    warnings
        A list to append warning messages to.

    Returns
    -------
    SegmentDecisionSet
        The updated SegmentDecisionSet.
    """

    # Block segments are always 1 decision (unchunked).
    if segment.kind == "block":
        key: tuple[str, int | None, int | None] = (segment.segment_id, None, None)

        if key in existing_keys:
            msg = (
                f"Skipping block segment {segment.segment_id}: decision already exists."
            )
            logger.warning(msg)
            warnings.append(msg)
            # input(1)

            return decision_set

        # NB: Never apply caption bindings to block segments.
        segment_payload = segment.model_dump(mode="json")

        segment_decision = generate_segment_decision(
            always_double_check_first_attempt=config.always_double_check_first_attempt,
            doc_key=doc_key,
            model=config.model,
            segment=segment,
            segment_payload=segment_payload,
        )

        decision_set.decisions.append(segment_decision)
        existing_keys.add(key)

        return save_segment_decision_set(
            decision_set=decision_set, segment_decisions_fp=segment_decisions_fp
        )

    assert segment.kind == "table"

    # Caption bindings dict is keyed by TABLE segment_id, and many tables have NO
    # caption -> Use .get().
    binding: CaptionBinding | None = caption_bindings.get(segment.segment_id)

    # Table segments: chunk only if needed. If an unchunked table decision already
    # exists, do NOT mix chunked + unchunked.
    unchunked_key = (segment.segment_id, None, None)

    if unchunked_key in existing_keys:
        msg = f"Skipping table segment {segment.segment_id}: unchunked decision already exists."
        logger.warning(msg)
        warnings.append(msg)
        # input(1)

        return decision_set

    # Determine table chunks.
    chunks = table_chunks_for_segment(
        max_body_rows=config.max_table_rows_per_decision, segment=segment
    )

    # Unchunked table == 1 decision.
    if len(chunks) == 1 and chunks[0] == (None, None):
        key = unchunked_key

        # Do NOT create an unchunked decision if ANY chunked decisions already exist
        # for this segment (else we would mix chunked + unchunked representations).
        existing_chunked_for_segment = any(
            sid == segment.segment_id and row_start is not None
            for (sid, row_start, _row_end) in existing_keys
        )
        if existing_chunked_for_segment:
            msg = (
                f"Skipping unchunked decision for table segment {segment.segment_id} "
                f"because chunked decisions already exist (avoid mixing chunked + unchunked)."
            )
            logger.warning(msg)
            warnings.append(msg)
            # input(1)

            return decision_set

        if key not in existing_keys:
            # Apply caption binding and pass the payload even for UNCHUNKED tables so
            # the LLM sees caption_text/caption_kind etc.
            table_payload = make_table_full_payload(segment=segment)
            table_payload = apply_caption_binding_to_table_payload(
                caption_bindings=binding, table_payload=table_payload
            )
            segment_decision = generate_segment_decision(
                always_double_check_first_attempt=config.always_double_check_first_attempt,
                doc_key=doc_key,
                model=config.model,
                segment=segment,
                segment_payload=table_payload,
            )
            segment_decision = attach_caption_binding_to_segment_decision(
                caption_binding=binding, segment_decision=segment_decision
            )

            decision_set.decisions.append(segment_decision)
            existing_keys.add(key)

            decision_set = save_segment_decision_set(
                decision_set=decision_set, segment_decisions_fp=segment_decisions_fp
            )
    # Chunked table == N decisions.
    else:
        for start, end in chunks:
            key = (segment.segment_id, start, end)

            if start is None or end is None or key in existing_keys:
                if key in existing_keys:
                    msg = (
                        f"Skipping table chunk for {segment.segment_id}: "
                        f"row_range_start={start}, row_range_end={end} already decided."
                    )
                    logger.warning(msg)
                    warnings.append(msg)
                    # input(1)

                continue

            table_payload = make_table_chunk_payload(
                end=end, segment=segment, start=start
            )

            # Use binding (may be None), do not index caption_bindings[].
            table_payload = apply_caption_binding_to_table_payload(
                caption_bindings=binding, table_payload=table_payload
            )

            segment_decision = generate_segment_decision(
                always_double_check_first_attempt=config.always_double_check_first_attempt,
                doc_key=doc_key,
                model=config.model,
                row_range_end=end,
                row_range_start=start,
                segment=segment,
                segment_payload=table_payload,
            )
            segment_decision = attach_caption_binding_to_segment_decision(
                caption_binding=binding, segment_decision=segment_decision
            )

            decision_set.decisions.append(segment_decision)
            existing_keys.add(key)

            decision_set = save_segment_decision_set(
                decision_set=decision_set, segment_decisions_fp=segment_decisions_fp
            )

    return decision_set


def reconcile_context_stack(
    *,
    active_stack: list[ContextFrame],
    child_to_parent: dict[str, str],
    decision: SegmentDecision,
    desired_context: list[GroupingDecision],
    doc_key: str,
    edge_set: set[tuple[str, str]],
    edges: list[CanonicalEdge],
    next_order_index: dict[str, int],
    nodes_by_id: dict[str, CanonicalNode],
    root_id: str,
    segment: Segment,
    warnings: list[str],
) -> tuple[str, list[str], list[ContextFrame]]:
    """Reconcile the current active context stack to match the desired context by
    enforcing context snapshot exactly per decision and reusing nodes via deterministic
    IDs.

    Parameters
    ----------
    active_stack
        The current active context stack.
    child_to_parent
        The mapping of child_id to parent_id for emitted edges.
    decision
        The SegmentDecision being processed.
    desired_context
        The desired context snapshot from the SegmentDecision.
    doc_key
        The document key.
    edge_set
        The set of existing edges (parent_id, child_id) to avoid duplicates.
    edges
        The list of CanonicalEdges to append new edges to.
    next_order_index
        The mapping of parent_id to next order index for child edges.
    nodes_by_id
        The mapping of node_id to CanonicalNode.
    root_id
        The root node ID.
    segment
        The Segment being processed.
    warnings
        The list of warning messages to append to.

    Returns
    -------
    tuple[str, list[str], list[ContextFrame]]
        The (parent_id, ancestor_keys, new_stack) after reconciliation.
    """

    desired_keys = [_grouping_key(g) for g in desired_context]
    page_indices = sorted({p.page_index for p in segment.segment_provenance})
    section_path_text = [h.text for h in (segment.section_path or [])]
    seg_id = segment.segment_id
    seg_kind = segment.kind

    # 1. Longest common prefix between active_stack keys and desired_keys.
    lcp = 0
    while lcp < len(active_stack) and lcp < len(desired_keys):
        if active_stack[lcp].grouping_key != desired_keys[lcp]:
            break

        lcp += 1

    # 2. Pop extra frames (enforce snapshot exactly).
    new_stack = active_stack[:lcp]

    # Determine current parent and ancestor keys after pop.
    if not new_stack:
        ancestor_keys: list[str] = []
        parent_id = root_id
    else:
        ancestor_keys = [f.grouping_key for f in new_stack]
        parent_id = new_stack[-1].node_id

    # 3. Push missing frames.
    for g in desired_context[lcp:]:
        node_id = canonical_grouping_node_id(
            ancestor_grouping_keys=ancestor_keys, doc_key=doc_key, grouping=g
        )

        node = CanonicalNode(
            bbox=None,
            body=None,
            list_id=g.list_id,
            local_code=g.local_code,
            node_id=node_id,
            normalized_text=_normalize_text(text=g.title),
            page_indices=page_indices,
            role=g.role,
            section_path_text=section_path_text,
            source_decision_ids=[decision.decision_id],
            source_label=g.source_label,
            source_segment_ids=[seg_id],
            source_type=seg_kind,
            title=TextUnit(language="und", text=g.title),
        )

        ensure_node(node=node, nodes_by_id=nodes_by_id)
        _emit_edge(
            child_id=node_id,
            child_to_parent=child_to_parent,
            decision_id=decision.decision_id,
            edge_set=edge_set,
            edges=edges,
            next_order_index=next_order_index,
            parent_id=parent_id,
            segment_id=seg_id,
            warnings=warnings,
        )

        # Advance stack.
        gk = _grouping_key(g)
        new_stack.append(ContextFrame(grouping_key=gk, node_id=node_id))
        parent_id = node_id
        ancestor_keys.append(gk)

    # After reconciliation, new_stack should EXACTLY matches desired_context snapshot.
    return parent_id, ancestor_keys, new_stack


def sanity_checks_postpass(
    *,
    edges: list[CanonicalEdge],
    nodes: list[CanonicalNode],
    root_id: str,
    warnings: list[str],
) -> None:
    """Perform sanity checks on the canonical IR structure.

    Parameters
    ----------
    edges
        The list of CanonicalEdges.
    nodes
        The list of CanonicalNodes.
    root_id
        The root node ID.
    warnings
        The list of warning messages to append to.

    Raises
    ------
    ValueError
        If the root node is missing.
    """

    node_ids = {n.node_id for n in nodes}

    # Root existence is catastrophic if missing.
    if root_id not in node_ids:
        raise ValueError(f"canonical_root_missing:{root_id}")

    # Root should not appear as a child (tree invariant).
    for e in edges:
        if e.child_id == root_id:
            msg = f"root_has_parent_edge:{root_id} parent={e.parent_id}"
            logger.warning(msg)
            warnings.append(msg)
            # input(1)

    # Tree invariant: no child has more than 1 parent.
    child_to_parent: dict[str, str] = {}
    for e in edges:
        existing = child_to_parent.get(e.child_id)

        if existing is None:
            child_to_parent[e.child_id] = e.parent_id
        elif existing != e.parent_id:
            msg = (
                f"tree_invariant_violation_multiple_parents:"
                f"child={e.child_id} p1={existing} p2={e.parent_id}"
            )
            logger.warning(msg)
            warnings.append(msg)
            # input(1)

    # Cycle detection by following parent pointers until root or repeat.
    for child in child_to_parent:
        seen: set[str] = set()
        cur = child

        while cur in child_to_parent:
            if cur in seen:
                msg = f"cycle_detected_at:{cur}"
                logger.warning(msg)
                warnings.append(msg)
                # input(1)

                break

            seen.add(cur)
            cur = child_to_parent[cur]

    # order_index sanity check per parent: duplicates/gaps.
    parent_to_orders: dict[str, list[int]] = {}
    for e in edges:
        parent_to_orders.setdefault(e.parent_id, []).append(e.order_index)

    for parent, orders in parent_to_orders.items():
        if len(orders) != len(set(orders)):
            msg = f"order_index_duplicate_under_parent:{parent}"
            logger.warning(msg)
            warnings.append(msg)
            # input(1)

        # Check strict contiguous ordering starting at 0.
        sorted_orders = sorted(set(orders))
        if sorted_orders and sorted_orders[0] != 0:
            msg = f"order_index_not_starting_at_zero:{parent} min={sorted_orders[0]}"
            logger.warning(msg)
            warnings.append(msg)
            # input(1)

        # Check contiguity ignoring dropped edges.
        for i in range(1, len(sorted_orders)):
            if sorted_orders[i] != sorted_orders[i - 1] + 1:
                msg = f"order_index_gap_under_parent:{parent} orders={sorted_orders}"
                logger.warning(msg)
                warnings.append(msg)
                # input(1)

                break


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
    logger.success(f"Saved canonical IR to: {canonical_ir_fp}")

    # Write structural leaf warnings sidecar file.
    structural_leaf_warnings = [
        w for w in canonical_ir.warnings if w.startswith("structural_leaf_review:")
    ]
    structural_leaf_warnings_fp = canonical_ir_fp.with_name(
        canonical_ir_fp.stem + ".structural_leaf_warnings.json"
    )
    write_to_json(
        fp=structural_leaf_warnings_fp,
        json_info={
            "doc_key": canonical_ir.doc_key,
            "decision_set_id": canonical_ir.decision_set_id,
            "threshold": 0.80,  # Fixed for now to align with LLM prompt
            "count": len(structural_leaf_warnings),
            "warnings": structural_leaf_warnings,
        },
    )
    logger.success(f"Saved structural leaf warnings to: {structural_leaf_warnings_fp}")


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


def table_chunks_for_segment(
    *, max_body_rows: int | None, segment: TableSegment
) -> list[tuple[int | None, int | None]]:
    """Compute table row chunks for a TableSegment based on max_body_rows.

    Parameters
    ----------
    max_body_rows
        The maximum number of body rows per chunk. If None or <= 0, no chunk splitting
        is performed.
    segment
        The TableSegment to compute chunks for.

    Returns
    -------
    list[tuple[int | None, int | None]]
        A list of (start, end) row index tuples for each chunk. If no chunk splitting
        is needed, returns [(None, None)].
    """

    if not max_body_rows or max_body_rows <= 0:
        return [(None, None)]

    header_n = segment.header_row_count or 0
    total_rows = len(segment.rows)
    body_rows = max(0, total_rows - header_n)

    if body_rows <= max_body_rows:
        return [(None, None)]

    chunks = []
    start = header_n  # Chunk only body rows (skip headers)

    while start < total_rows:
        end = min(total_rows, start + max_body_rows)
        chunks.append((start, end))
        start = end

    return chunks


def uuidv5_from_key(key: str) -> str:
    """Create a deterministic UUIDv5 from a string key.

    Parameters
    ----------
    key
        The input string key.

    Returns
    -------
    str
        The resulting UUIDv5 as a string.
    """

    return str(uuid.uuid5(Settings.LC_CANONICAL_NAMESPACE_UUID, key))
