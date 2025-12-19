"""This module contains heuristic logic for curriculum structure inference.

It handles:

1. Inferring page kinds (Content vs Front Matter).
2. Promoting table rows to hierarchy nodes (e.g. "Topic" columns).
3. Converting abbreviation tables to glossary elements.
"""

# pylint:disable=R0911,R1260,R0912,R0915,R1702
# Standard Library
import re

from typing import Any, Optional

# Package Library
from skg.ir.schemas import (
    CurriculumElementIR,
    HierarchyNodeIR,
    KeyValuePair,
    PageIR,
    ProvenancePointer,
)
from skg.utils.constants import (
    BBoxKind,
    CurriculumElementType,
    HierarchyNodeType,
    PageKind,
    StatementRole,
    TextFormat,
)
from skg.utils.general import stable_text_hash

DEFAULT_NODE_TYPE_OVERRIDES: list[dict[str, Any]] = []
DEFAULT_PAGE_KIND_KEYWORDS_BY_LANG: dict[str, dict[PageKind, list[str]]] = {
    # English
    "en": {
        PageKind.ABBREVIATIONS: ["abbreviations", "acronyms"],
        PageKind.ACKNOWLEDGEMENTS: ["acknowledgements", "acknowledgments"],
        PageKind.APPENDIX: ["appendix"],
        PageKind.LIST_OF_TABLES: ["list of tables"],
        PageKind.PREFACE: ["preface", "foreword"],
        PageKind.TOC: ["table of contents", "contents"],
    },
    # Swahili (Tanzania)
    "sw": {
        PageKind.TOC: ["yaliyomo", "jedwali la yaliyomo", "maudhui"],
        PageKind.PREFACE: ["utangulizi", "dibaji"],
        PageKind.APPENDIX: ["kiambatisho"],
        PageKind.ACKNOWLEDGEMENTS: ["shukrani", "utangulizi wa shukrani"],
    },
    # French (Senegal / Francophone docs)
    "fr": {
        PageKind.TOC: ["table des matières", "sommaire"],
        PageKind.PREFACE: ["préface", "avant-propos"],
        PageKind.APPENDIX: ["annexe", "annexes"],
        PageKind.ACKNOWLEDGEMENTS: ["remerciements"],
        PageKind.LIST_OF_TABLES: ["liste des tableaux"],
    },
}
DEFAULT_ROW_PROMOTION_CONFIG: dict[str, Any] = {
    "grouping_keywords": [
        "component",
        "content",
        "domain",
        "learning area",
        "learning field",
        "strand",
        "sub-theme",
        "sub-topic",
        "subject",
        "theme",
        "topic",
    ],
    "payload_keywords": [
        "competences",
        "competency",
        "core competence",
        "general competence",
        "key competence",
        "main competence",
        "objectives",
        "outcomes",
        "specific outcomes",
        "standard",
    ],
    "min_header_confidence": 0.70,  # If header matches are too weak, don’t promote rows
}


def _create_cell_provenance(
    *,
    base_ptr: ProvenancePointer,
    cell: Any,
    method: str,
    page_idx: int,
    t_ref: str,
    table_col: Optional[int] = None,
    table_row: Optional[int] = None,
    text: str,
) -> ProvenancePointer:
    """Helper to clone a base pointer and attach cell-specific info.

    Notes
    -----
    `table_row` and `table_col` are canonical 0-based indices into TableIR.rows and
    TableIR.col_headers. Heuristics should pass these explicitly (do not rely on
    cell.row_idx/col_idx which may be missing).

    Parameters
    ----------
    base_ptr
        The base provenance pointer to clone.
    cell
        The table cell object.
    method
        The extraction method description.
    page_idx
        The 0-based page index.
    t_ref
        The table reference.
    table_col
        The table column index.
    table_row
        The table row index.
    text
        The cell text quote.

    Returns
    -------
    ProvenancePointer
        The new provenance pointer for the cell.
    """

    return ProvenancePointer(
        bbox=getattr(cell, "bbox", None),
        bbox_kind=getattr(cell, "bbox_kind", BBoxKind.UNKNOWN),
        doc_key=base_ptr.doc_key,
        extraction_method=method,
        image_dimensions=getattr(base_ptr, "image_dimensions", None),
        page_dimensions=base_ptr.page_dimensions,
        page_index=page_idx,
        pdf_name=base_ptr.pdf_name,
        render_dpi=getattr(base_ptr, "render_dpi", None),
        section=base_ptr.section,
        table_col=(
            table_col if table_col is not None else getattr(cell, "col_idx", None)
        ),
        table_ref=t_ref,
        table_row=(
            table_row if table_row is not None else getattr(cell, "row_idx", None)
        ),
        text_quote=text,
    )


def _find_any_provenance(page_ir: PageIR) -> Optional[ProvenancePointer]:
    """Helper to find any existing provenance pointer on the page.

    Parameters
    ----------
    page_ir
        The PageIR to search.

    Returns
    -------
    Optional[ProvenancePointer]
        The first found provenance pointer, or None if none exist.
    """

    for el in (
        (page_ir.nodes or []) + (page_ir.statements or []) + (page_ir.tables or [])
    ):
        if getattr(el, "provenance", None):
            return el.provenance[0]
    return None


def _find_glossary_columns(headers: list[str]) -> tuple[Optional[int], Optional[int]]:
    """Identify abbreviation and meaning columns in a table header.

    Parameters
    ----------
    headers
        The list of column headers.

    Returns
    -------
    tuple[Optional[int], Optional[int]]
        The (abbreviation_column_index, meaning_column_index), or (None, None) if
        not found.
    """

    abbr_col = None
    meaning_col = None
    for i, h in enumerate(headers):
        if "abbr" in h or "acron" in h:
            abbr_col = i
        if "mean" in h or "defin" in h or "full" in h:
            meaning_col = i
    return abbr_col, meaning_col


def _is_abbreviation_table(headers: list[str]) -> bool:
    """Heuristic to determine if a table is an abbreviation/acronym table.

    Parameters
    ----------
    headers
        The list of column headers.

    Returns
    -------
    bool
        True if the table appears to be an abbreviation/acronym table.
    """

    txt = " ".join(headers)
    return ("abbreviation" in txt or "acronym" in txt) and (
        "meaning" in txt or "definition" in txt or "full" in txt
    )


def _merge_page_kind_keywords(
    *,
    keyword_packs_by_lang: dict[str, dict[PageKind, list[str]]] | None,
    languages: list[str] | None,
) -> dict[PageKind, list[str]]:
    """Merge page-kind keyword packs for the specified languages.

    Parameters
    ----------
    keyword_packs_by_lang
        Optional custom keyword packs by language.
    languages
        Optional list of language tags to consider.

    Returns
    -------
    dict[PageKind, list[str]]
        The merged page-kind keywords.
    """

    packs = keyword_packs_by_lang or DEFAULT_PAGE_KIND_KEYWORDS_BY_LANG

    # Always include English as baseline.
    langs = ["en"] + [lang.split("-")[0].lower() for lang in (languages or [])]
    langs = [lang for i, lang in enumerate(langs) if lang and lang not in langs[:i]]

    merged: dict[PageKind, list[str]] = {}
    for lang in langs:
        pack = packs.get(lang)
        if not pack:
            continue
        for kind, kws in pack.items():
            merged.setdefault(kind, [])
            merged[kind].extend(kws)

    # De-dup.
    for kind in list(merged.keys()):
        merged[kind] = sorted(set(merged[kind]))

    return merged


def _record_parent_inference(
    *,
    detail: Optional[str] = None,
    el: Any,
    parent_ref: str,
    reason: str,
) -> None:
    """Attach a lightweight breadcrumb explaining why parent_ref was inferred.

    Parameters
    ----------
    detail
        Optional detailed explanation.
    el
        The element to annotate.
    parent_ref
        The inferred parent reference.
    reason
        The high-level reason for the inference.
    """

    # Tag for quick debugging/filtering.
    tags = getattr(el, "tags", None)
    if isinstance(tags, list):
        tag = f"parent_inferred:{reason}"
        if tag not in tags:
            tags.append(tag)

    # Key/value pairs for machine-usable downstream logic.
    extra = getattr(el, "extra", None)
    if isinstance(extra, list):
        existing = {
            (getattr(kv, "key", None), getattr(kv, "value", None)) for kv in extra
        }
        items = [
            ("parent_inference_reason", reason),
            ("parent_inference_parent_ref", parent_ref),
        ]
        if detail:
            items.append(("parent_inference_detail", detail))

        for k, v in items:
            if (k, v) not in existing:
                extra.append(KeyValuePair(key=k, value=v))


def build_heuristics_config(*, country: str) -> dict[str, object]:
    """Build a per-country/profile heuristics config to be passed to
    postprocess_page_ir(). This is intentionally minimal at the moment.

    Parameters
    ----------
    country
        The country/profile name.

    Returns
    -------
    dict[str, object]
        The heuristics configuration dictionary.
    """

    c = (country or "").strip().lower()

    cfg: dict[str, object] = {
        # Keep row promotion enabled by default, with defaults defined in heuristics.py
        # (postprocess_page_ir will pass this through).
        "row_promotion": None,
        # Optional: allow overriding page-kind keyword packs. Usually leave None to use
        # defaults.
        "page_kind_keywords_by_lang": None,
        # Default: no node-type overrides unless we explicitly turn them on per profile.
        "node_type_overrides": [],
    }

    # Example for Ghana: Enable the Ghana-only node-type override pack.
    if c == "ghana":
        cfg["node_type_overrides"] = [
            {
                "name": "vision_objective_is_section_heading",
                "contains_all": ["vision", "objective"],
                "if_node_type": HierarchyNodeType.THEME,
                "set_node_type": HierarchyNodeType.OTHER,
                "node_type_other": "section_heading",
            }
        ]

    return cfg


def convert_abbreviation_tables_to_glossary(
    *, fallback_base_ptr: ProvenancePointer | None = None, page_ir: PageIR
) -> None:
    """Convert abbreviation tables into GLOSSARY_ENTRY elements.

    Parameters
    ----------
    fallback_base_ptr
        Optional fallback provenance pointer if none exist on the page.
    page_ir
        The PageIR to process.
    """

    if page_ir.page_kind not in {PageKind.ABBREVIATIONS, PageKind.FRONT_MATTER}:
        return

    if page_ir.curriculum_elements is None:
        page_ir.curriculum_elements = []

    prefix = f"p{page_ir.page_index:04d}"

    # Locate any existing provenance pointer to inherit metadata.
    any_ptr = _find_any_provenance(page_ir)
    base_ptr = any_ptr or fallback_base_ptr
    if base_ptr is None:
        if page_ir.warnings is None:
            page_ir.warnings = []
        page_ir.warnings.append(
            "[glossary_promotion] no provenance available; skipping"
        )
        return

    # Find a parent heading (fallback).
    fm_parent = None
    for n in reversed(page_ir.nodes or []):
        if getattr(n, "node_type_other", None) == "front_matter_heading":
            fm_parent = n.ref
            break

    existing_refs = {
        getattr(ce, "ref", None) for ce in (page_ir.curriculum_elements or [])
    }

    for t in page_ir.tables or []:
        headers = [h.lower().strip() for h in (getattr(t, "col_headers", None) or [])]
        if not headers:
            continue

        if not _is_abbreviation_table(headers):
            continue

        abbr_col, meaning_col = _find_glossary_columns(headers)
        if abbr_col is None or meaning_col is None:
            continue

        t_suffix = (getattr(t, "ref", "") or "t").split(":")[-1]
        parent_ref = getattr(t, "parent_ref", None) or fm_parent

        for r_i, row in enumerate(getattr(t, "rows", []) or []):

            if not row or any(getattr(c, "is_header", False) for c in row):
                continue
            if abbr_col >= len(row) or meaning_col >= len(row):
                continue

            abbr = (getattr(row[abbr_col], "text", "") or "").strip()
            meaning = (getattr(row[meaning_col], "text", "") or "").strip()
            if not abbr or not meaning:
                continue

            stable_sig = stable_text_hash(f"{abbr}\n{meaning}")
            ce_ref = f"{prefix}:ce_glossary_{t_suffix}_r{r_i}_{stable_sig[:10]}"

            if ce_ref in existing_refs:
                continue

            # Create Provenance.
            prov = [
                _create_cell_provenance(
                    base_ptr=base_ptr,
                    cell=row[abbr_col],
                    method="table-glossary-promotion",
                    page_idx=page_ir.page_index,
                    t_ref=t.ref,
                    table_col=abbr_col,
                    table_row=r_i,
                    text=f"{abbr}",
                ),
                _create_cell_provenance(
                    base_ptr=base_ptr,
                    cell=row[meaning_col],
                    method="table-glossary-promotion",
                    page_idx=page_ir.page_index,
                    t_ref=t.ref,
                    table_col=meaning_col,
                    table_row=r_i,
                    text=f"{meaning}",
                ),
            ]

            page_ir.curriculum_elements.append(
                CurriculumElementIR(
                    confidence=None,
                    element_type=CurriculumElementType.GLOSSARY_ENTRY,
                    element_type_other=None,
                    grade_labels_raw=[],
                    grade_levels=[],
                    is_continuation=False,
                    language="und",
                    original_label="Abbreviations/Acronyms",
                    parent_ref=parent_ref,
                    provenance=prov,
                    ref=ce_ref,
                    sequence=None,
                    source_field="abbreviations_table",
                    tags=["glossary_entry", "front_matter"],
                    text=f"{abbr}: {meaning}",
                    text_en=None,
                    text_format=TextFormat.PLAIN,
                    time_allocation=None,
                    translation_meta=None,
                    url=None,
                )
            )
            existing_refs.add(ce_ref)


def infer_page_kind(
    *,
    languages: list[str] | None = None,
    keyword_packs_by_lang: dict[str, dict[PageKind, list[str]]] | None = None,
    page_ir: PageIR,
) -> PageKind:
    """Heuristic page classifier to prevent non-standards pages from generating LCs.

    Parameters
    ----------
    languages
        Optional list of language tags to consider.
    keyword_packs_by_lang
        Optional custom keyword packs by language.
    page_ir
        The PageIR to classify.

    Returns
    -------
    PageKind
        The inferred page kind.
    """

    blob_parts: list[str] = []
    for n in page_ir.nodes or []:
        blob_parts.append(str(getattr(n, "label", "") or ""))
    for s in page_ir.statements or []:
        blob_parts.append(str(getattr(s, "text", "") or ""))
    for t in page_ir.tables or []:
        for h in getattr(t, "col_headers", None) or []:
            blob_parts.append(str(h))
        for row in (getattr(t, "rows", None) or [])[:3]:
            for cell in row:
                blob_parts.append(str(getattr(cell, "text", "") or ""))
    text = "\n".join(blob_parts).lower()

    keywords = _merge_page_kind_keywords(
        keyword_packs_by_lang=keyword_packs_by_lang, languages=languages
    )
    toc_leader_pattern = bool(re.search(r"\.{3,}\s*\d+", text))
    for kind, kws in keywords.items():
        if any(k in text for k in kws):
            # TOC false-positive guard: If we matched TOC keywords weakly (e.g.,
            # generic "contents") but there are no dot-leaders/page numbers, be
            # conservative.
            if kind == PageKind.TOC and not toc_leader_pattern:
                # Allow strong TOC phrases across languages; otherwise require dot
                # leaders.
                strong_toc_phrases = [
                    "table of contents",
                    "table des matières",
                    "jedwali la yaliyomo",
                    "sommaire",
                ]
                if not any(p in text for p in strong_toc_phrases):
                    continue
            return kind

    # Early-page fallback (SAFE): absence of normative roles is not strong enough
    # evidence to call something FRONT_MATTER, because mis-classification triggers
    # role downgrades (and can erase real standards). Be conservative instead.
    if page_ir.page_index <= 10:
        roles = [getattr(s, "role", None) for s in (page_ir.statements or [])]
        has_normative = any(
            r in {StatementRole.EXPECTATION, StatementRole.PERFORMANCE_DESCRIPTOR}
            for r in roles
        )
        if not has_normative:
            return PageKind.UNKNOWN

    return PageKind.CONTENT


def normalize_front_matter_roles(page_ir: PageIR) -> None:
    """Downgrade non-content page statements to DOCUMENT_CONTEXT.

    Parameters
    ----------
    page_ir
        The PageIR to process.
    """

    if page_ir.page_kind in {PageKind.CONTENT, PageKind.UNKNOWN}:
        return

    for s in page_ir.statements or []:
        if getattr(s, "role", None) != StatementRole.DOCUMENT_CONTEXT:
            s.role = StatementRole.DOCUMENT_CONTEXT

        tags = (getattr(s, "tags", None) or []) + [
            "front_matter",
            f"page_kind:{page_ir.page_kind.value}",
        ]
        s.tags = sorted(set(tags))


def normalize_node_types(
    *, overrides: list[dict[str, Any]] | None = None, page_ir: PageIR
) -> None:
    """Patch common node-type drift from vision/table extraction.

    Goal: keep semantic node types (e.g., THEME) meaningful while forcing
    document-structural headers (Section/Chapter/front matter headings) to OTHER.
    Conservative: only overrides when the label is a strong signal.

    Parameters
    ----------
    overrides
        Optional list of override dictionaries.
    page_ir
        The PageIR to normalize.
    """

    for n in page_ir.nodes or []:
        lbl = (getattr(n, "label", "") or "").strip()
        lbl_l = lbl.lower()

        for ov in overrides or DEFAULT_NODE_TYPE_OVERRIDES:
            contains_all = [x.lower() for x in (ov.get("contains_all") or [])]
            if contains_all and all(tok in lbl_l for tok in contains_all):
                if_node_type = ov.get("if_node_type")
                if (
                    if_node_type is None
                    or getattr(n, "node_type", None) == if_node_type
                ):
                    n.node_type = ov.get("set_node_type", getattr(n, "node_type", None))
                    n.node_type_other = ov.get(
                        "node_type_other", getattr(n, "node_type_other", None)
                    )

                    # Tag it.
                    tags = getattr(n, "tags", None)
                    if isinstance(tags, list):
                        nm = ov.get("name", "override")
                        tags.append(f"node_type_overridden:{nm}")
                        n.tags = sorted(set(tags))
                    break

        if lbl_l.startswith("section "):
            n.node_type = HierarchyNodeType.OTHER
            n.node_type_other = "section"
            continue

        if lbl_l.startswith("chapter "):
            n.node_type = HierarchyNodeType.OTHER
            n.node_type_other = "chapter"
            continue

        # Front-matter headings.
        if any(
            k in lbl_l
            for k in [
                "table of contents",
                "list of tables",
                "abbreviations",
                "acknowledgement",  # Catches acknowledgements
                "acknowledgment",  # Catches acknowledgments
                "preface",
                "foreword",
            ]
        ):
            n.node_type = HierarchyNodeType.OTHER
            n.node_type_other = "front_matter_heading"
            continue


def promote_learning_area_rows_to_nodes(
    *,
    config: dict[str, Any] | None = None,
    fallback_base_ptr: ProvenancePointer | None = None,
    page_ir: PageIR,
) -> None:
    """Promote 'Learning areas' (or similar grouping) table rows to SUBJECT-ish nodes.

    This heuristic looks for tables that contain a high-level grouping column (e.g.,
    "Learning Area", "Subject", "Component") alongside a high-level competence column
    (e.g., "Main Competence"). It promotes the text in the grouping column to a
    HierarchyNodeIR so it acts as a parent for the statements in that row.

    Parameters
    ----------
    config
        Optional configuration dictionary.
    fallback_base_ptr
        Optional fallback provenance pointer if none exist on the page.
    page_ir
        The PageIR to process.
    """

    if page_ir.nodes is None:
        page_ir.nodes = []

    prefix = f"p{page_ir.page_index:04d}"
    node_map = {n.ref: n for n in (page_ir.nodes or [])}
    any_ptr = _find_any_provenance(page_ir)

    cfg = config or DEFAULT_ROW_PROMOTION_CONFIG
    grouping_keywords = cfg.get("grouping_keywords", [])
    payload_keywords = cfg.get("payload_keywords", [])
    min_header_conf = float(cfg.get("min_header_confidence", 0.70))

    for t in page_ir.tables or []:
        headers = [h.lower().strip() for h in (getattr(t, "col_headers", None) or [])]
        if not headers:
            continue

        # Find the index of the grouping column (e.g. "Learning Area").
        la_col = next(
            (
                i
                for i, h in enumerate(headers)
                if any(k in h for k in grouping_keywords)
            ),
            None,
        )

        # Find the index of the main payload column (e.g. "Main Competence"). We
        # enforce this check to avoid promoting rows in random data tables.
        mc_col = next(
            (i for i, h in enumerate(headers) if any(k in h for k in payload_keywords)),
            None,
        )

        if la_col is None or mc_col is None:
            continue

        la_header = headers[la_col]
        mc_header = headers[mc_col]

        la_hits = [k for k in grouping_keywords if k in la_header]
        mc_hits = [k for k in payload_keywords if k in mc_header]

        def _hdr_strength(header: str, hits: list[str]) -> float:
            """Helper to score header match strength. This is a crude but (hopefully)
            effective header confidence: exact/starts-with matches are stronger than
            substring matches.

            Parameters
            ----------
            header
                The header text.
            hits
                The list of matched keywords.

            Returns
            -------
            float
                The header strength score (0.0-1.0).
            """

            if not hits:
                return 0.0
            if any(header == h or header.startswith(h) for h in hits):
                return 1.0
            return 0.7

        conf = 0.5 * _hdr_strength(la_header, la_hits) + 0.5 * _hdr_strength(
            mc_header, mc_hits
        )

        if conf < min_header_conf:
            # Too risky; likely a random data table.
            if page_ir.warnings is not None:
                page_ir.warnings.append(
                    f"[row_promotion] skipped table {getattr(t, 'ref', None)} low header confidence={conf:.2f}"
                )
            continue

        t_suffix = (getattr(t, "ref", "") or "t").split(":")[-1]

        # Parent for the new node is the table's parent.
        parent_ref = getattr(t, "parent_ref", None)
        parent_path = None
        if parent_ref and parent_ref in node_map:
            parent_path = list(getattr(node_map[parent_ref], "path", None) or []) or [
                parent_ref
            ]

        # Optional but very useful: handle merged/blank grouping cells by carrying
        # forward the last seen grouping node within this table.
        last_group_node_ref: str | None = None

        for r_i, row in enumerate(getattr(t, "rows", []) or []):
            if not row or any(getattr(c, "is_header", False) for c in row):
                continue

            # Ensure the row actually has these columns.
            if la_col >= len(row) or mc_col >= len(row):
                continue

            la_text = (getattr(row[la_col], "text", "") or "").strip()

            if not la_text:
                # Common when the PDF uses merged cells for the grouping column. If we
                # previously created a node for this table, reuse it.
                if last_group_node_ref is None:
                    continue
                n_ref = last_group_node_ref
            else:
                sig = stable_text_hash(la_text)[:10]
                # IMPORTANT: make the promoted node stable per (table,label), not per
                # row; this makes reuse predictable.
                n_ref = f"{prefix}:n_promoted_{t_suffix}_{sig}"

            # Create the node if missing, otherwise reuse it.
            if n_ref not in node_map:
                # Create Node.
                path = (parent_path or []) + [n_ref] if parent_path else [n_ref]

                prov = []
                base_ptr = any_ptr or fallback_base_ptr
                if base_ptr:
                    prov.append(
                        _create_cell_provenance(
                            base_ptr=base_ptr,
                            cell=row[la_col],
                            method="table-row-promotion",
                            table_row=r_i,
                            table_col=la_col,
                            page_idx=page_ir.page_index,
                            t_ref=t.ref,
                            text=la_text,
                        )
                    )
                else:
                    page_ir.warnings = page_ir.warnings or []
                    page_ir.warnings.append(
                        f"[row_promotion] no provenance available for promoted node "
                        f"(table={getattr(t, 'ref', None)} row={r_i})"
                    )

                header_text = headers[la_col]
                inferred_type: HierarchyNodeType = HierarchyNodeType.OTHER
                type_other = "promoted_group"
                if "learning area" in header_text:
                    inferred_type = HierarchyNodeType.LEARNING_AREA
                elif "subject" in header_text:
                    inferred_type = HierarchyNodeType.SUBJECT
                elif "theme" in header_text:
                    inferred_type = HierarchyNodeType.THEME
                elif "strand" in header_text:
                    inferred_type = HierarchyNodeType.STRAND
                else:
                    type_other = header_text

                new_node = HierarchyNodeIR(
                    confidence=None,
                    description=None,
                    description_en=None,
                    description_translation_meta=None,
                    grade_labels_raw=[],
                    grade_levels=[],
                    is_continuation=False,
                    label=la_text,
                    label_en=None,
                    label_translation_meta=None,
                    language="und",
                    list_index=None,
                    node_type=inferred_type,
                    node_type_other=(
                        type_other if inferred_type == HierarchyNodeType.OTHER else None
                    ),
                    original_label=headers[la_col],
                    parent_ref=parent_ref,
                    provenance=prov,
                    ref=n_ref,
                    sequence=None,
                    source_field=headers[la_col],
                    subject_tag=None,
                    tags=["table_row_promoted"],
                    time_allocation=None,
                )
                new_node.path = path
                page_ir.nodes.append(new_node)
                node_map[n_ref] = new_node

            # Track last non-empty grouping node to support merged/blank cells.
            if la_text:
                last_group_node_ref = n_ref

            # Re-parent competence statements found in the payload column using
            # canonical 0-based (table_row=r_i, table_col=mc_col) provenance indices.
            for s in page_ir.statements or []:
                for p in getattr(s, "provenance", None) or []:
                    if (
                        getattr(p, "table_ref", None) == getattr(t, "ref", None)
                        and getattr(p, "table_row", None) == r_i
                        and getattr(p, "table_col", None) == mc_col
                    ):
                        # Only re-parent if it doesn't already have a specific parent.
                        if getattr(s, "parent_ref", None) in {parent_ref, None}:
                            s.parent_ref = n_ref
                            _record_parent_inference(
                                detail=f"table_ref={getattr(t, 'ref', None)} row={r_i} col={mc_col}",
                                el=s,
                                parent_ref=n_ref,
                                reason="heuristics:table_row_promotion",
                            )
                        break
