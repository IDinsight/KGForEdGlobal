"""This module contains the entry point for converting raw PDF pages into a structural
Intermediate Representation (IR).

Invoke from the backend directory via:

python src/skg/entries/extract_curriculum.py ../data/zambia/zambia.pdf -c Zambia -l en-US -o ../results --overwrite
"""

# Standard Library
import sys
import traceback
import uuid

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Third Party Library
import pymupdf
import typer

from loguru import logger

# Append the framework path. NB: This is required if this entry point is invoked from
# the command line. However, it is not necessary if it is imported from a pip install.
if __name__ == "__main__":
    PACKAGE_PATH = Path(__file__).resolve().parents[2]
    if PACKAGE_PATH not in sys.path:
        print(f"Appending '{PACKAGE_PATH}' to system path...")
        sys.path.append(str(PACKAGE_PATH))

# Package Library
from skg.ir.schemas import (
    DocumentIR,
    DocumentMetadataIR,
    ExtractionRunIR,
    PageIR,
    ProvenancePointer,
)
from skg.ir.utils import (
    ContinuityState,
    create_extraction_dirs,
    load_continuity_state,
    save_continuity_state,
)
from skg.utils.constants import BBoxKind
from skg.utils.general import write_text
from skg.utils.pdf import (
    compute_doc_key,
    get_page_dimensions,
    get_pdf_metadata,
    render_page_to_png,
)

assert (
    sys.version_info.major >= 3 and sys.version_info.minor >= 13
), "SenegalKG requires at least Python 3.13!"

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)


def build_continuity_state_from_page(page_ir: PageIR) -> ContinuityState:
    """Build continuity state from a PageIR.

    Parameters
    ----------
    page_ir
        The PageIR to build continuity state from.

    Returns
    -------
    ContinuityState
        The built continuity state.
    """

    # Prefer a node with the deepest path; fallback to "last node with a ref"
    active_parent_ref = None
    active_path: list[str] = []

    nodes = page_ir.nodes or []
    if nodes:
        # deepest path wins
        best = max(nodes, key=lambda n: len(getattr(n, "path", None) or []))
        active_parent_ref = best.ref
        active_path = list(getattr(best, "path", None) or [])
        if not active_path:
            # if model didn’t provide path, approximate with just the node ref
            active_path = [best.ref]

    # For cross-page remap, store raw->qualified for this page only
    # raw ref is the suffix after the last ":" (matches your "p0007:" convention)
    recent_raw_ref_map: dict[str, str] = {}
    for el in (
        nodes
        + (page_ir.statements or [])
        + (page_ir.tables or [])
        + (page_ir.diagrams or [])
        + (page_ir.curriculum_elements or [])
        + (page_ir.relationships or [])
    ):
        r = getattr(el, "ref", None)
        if r and ":" in r:
            recent_raw_ref_map[r.split(":")[-1]] = r

    return ContinuityState(
        active_parent_ref=active_parent_ref,
        active_path=active_path,
        recent_raw_ref_map=recent_raw_ref_map,
    )


def apply_cross_page_continuity(  # pylint: disable=too-complex
    page_ir: PageIR, prev: ContinuityState
) -> PageIR:
    """Apply cross-page continuity to a PageIR.

    Parameters
    ----------
    page_ir
        The PageIR to apply continuity to.
    prev
        The previous continuity state.

    Returns
    -------
    PageIR
        The PageIR with continuity applied.
    """

    def remap_raw(r: Optional[str]) -> Optional[str]:
        """Remap a raw ref using the previous continuity state's recent_raw_ref_map.

        Parameters
        ----------
        r
            The raw ref to remap.

        Returns
        -------
        Optional[str]
            The remapped ref if found, else the original ref.
        """

        if not r:
            return r

        # Already qualified
        if ":" in r:
            return r
        return prev.recent_raw_ref_map.get(r, r)

    def patch_parent(el: Any) -> None:
        """Patch the parent_ref and path of an element.

        Parameters
        ----------
        el
            The element to patch.
        """

        parent_ref = getattr(el, "parent_ref", None)
        parent_ref2 = remap_raw(parent_ref)

        # If it was unqualified and we could map it, patch it.
        if parent_ref2 != parent_ref:
            el.parent_ref = parent_ref2

        # If it's a continuation and parent is missing/unhelpful, fall back to active
        # parent.
        if getattr(el, "is_continuation", False):
            if not getattr(el, "parent_ref", None) and prev.active_parent_ref:
                el.parent_ref = prev.active_parent_ref

        # Patch path too (GraphElementIR.path is a list of refs).
        if hasattr(el, "path") and getattr(el, "path", None):
            el.path = [remap_raw(x) for x in el.path]

    # Patch parent/path on all structural elements.
    for col_name in (
        "nodes",
        "statements",
        "tables",
        "diagrams",
        "curriculum_elements",
    ):
        for el in getattr(page_ir, col_name, None) or []:
            patch_parent(el)

    # Patch relationships.
    for rel in page_ir.relationships or []:
        rel.source_ref = remap_raw(rel.source_ref)
        rel.target_ref = remap_raw(rel.target_ref)
        patch_parent(rel)  # Patches rel.path if it has one

    return page_ir


def extract_page_ir_with_llm(
    *,
    model: str = "gpt-4o-2024-08-06",
    page_index: int,
    png_fp: Path,
) -> PageIR:
    """Extract PageIR from a page image using LLM + Vision + Structured Outputs. Uses
    OpenAI Responses API structured parsing into a Pydantic model. Image is passed as
    an input_image with a base64 data URL.

    Parameters
    ----------
    model
        The OpenAI model to use.
    page_index
        The 0-based page index.
    png_fp
        The PNG file path of the page image.

    Returns
    -------
    PageIR
        The extracted PageIR.
    """

    pass


def merge_pages_to_document_ir(
    *,
    doc_key: str,
    extraction_run: Optional[ExtractionRunIR] = None,
    metadata: DocumentMetadataIR,
    pages: list[PageIR],
    pdf_name: str,
) -> DocumentIR:
    """Merge PageIRs into DocumentIR.

    Parameters
    ----------
    doc_key
        The document key.
    extraction_run
        The extraction run metadata.
    pdf_name
        The PDF file name.
    metadata
        The document metadata.
    pages
        The list of PageIRs.

    Returns
    -------
    DocumentIR
        The merged DocumentIR.
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
        curriculum_elements.extend(p.curriculum_elements)
        diagrams.extend(p.diagrams)
        nodes.extend(p.nodes)
        relationships.extend(p.relationships)
        statements.extend(p.statements)
        tables.extend(p.tables)

    # Stable, de-duped roots.
    seen: set[str] = set()
    root_node_refs: list[str] = []
    for n in nodes:
        if n.parent_ref is None and n.ref not in seen:
            root_node_refs.append(n.ref)
            seen.add(n.ref)

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


def namespace_page_refs(  # pylint: disable=too-complex, too-many-branches
    *, page_ir: PageIR, prefix: str
) -> PageIR:
    """Ensure refs are unique across the whole document by namespacing each page's
    refs. This avoids DocumentIR.validate_unique_refs failures when the model restarts
    numbering on each page.

    1. De-dupe refs within a single page across ALL element types
        (nodes/statements/curriculum_elements/tables/diagrams/relationships).
    2. Then namespace everything with prefix (e.g., "p0007:") and remap intra-page
        links.

    Parameters
    ----------
    page_ir
        The PageIR to namespace.
    prefix
        The prefix to add to each ref.

    Returns
    -------
    PageIR
        The namespaced PageIR.
    """

    # Collect all elements on the page (across all types).
    buckets: list[tuple[str, list[Any], str]] = [
        ("nodes", page_ir.nodes, "n"),
        ("statements", page_ir.statements, "s"),
        ("curriculum_elements", page_ir.curriculum_elements, "c"),
        ("tables", page_ir.tables, "t"),
        ("diagrams", page_ir.diagrams, "d"),
        ("relationships", page_ir.relationships, "r"),
    ]
    element_lists: list[Any] = []
    for _, lst, _ in buckets:
        element_lists.extend(lst)

    # De-dupe refs within the page across all element types.
    refs = [e.ref for e in element_lists if getattr(e, "ref", None)]
    counts = Counter(refs)
    dupes = [r for r, c in counts.items() if c > 1]

    if dupes:
        # map ref -> list of (element, tag)
        occ: dict[str, list[tuple[Any, str]]] = defaultdict(list)
        for _, lst, tag in buckets:
            for el in lst:
                if getattr(el, "ref", None):
                    occ[el.ref].append((el, tag))

        existing = set(refs)
        for r in dupes:
            # Keep first occurrence as-is; rename the rest.
            for i, (el, tag) in enumerate(occ[r][1:], start=1):
                new_ref = f"{r}__{tag}{i}"
                while new_ref in existing:
                    new_ref += "_"
                el.ref = new_ref
                existing.add(new_ref)

        page_ir.warnings.append(
            f"Duplicate refs within page resolved by renaming (kept first occurrence): {sorted(dupes)[:20]}"
        )

    # Rebuild after potential renames.
    element_lists = (
        page_ir.nodes
        + page_ir.statements
        + page_ir.relationships
        + page_ir.tables
        + page_ir.diagrams
        + page_ir.curriculum_elements
    )
    local_refs = {e.ref for e in element_lists if getattr(e, "ref", None)}

    # If it already looks namespaced, do nothing (prevents double-prefixing).
    if local_refs and all(r.startswith(prefix) for r in local_refs):
        return page_ir

    # Namespace refs.
    ref_map = {r: f"{prefix}{r}" for r in local_refs}

    def remap_if_local(r: Optional[str]) -> Optional[str]:
        """Remap a ref if it is local to this page.

        Parameters
        ----------
        r
            The ref to remap.

        Returns
        -------
        Optional[str]
            The remapped ref if local, else the original ref.
        """

        return ref_map.get(r, r)

    # Remap paths
    for e in element_lists:
        if hasattr(e, "path") and getattr(e, "path", None):
            e.path = [remap_if_local(r) for r in e.path]

    # Remap refs + parent refs
    for e in element_lists:
        e.ref = ref_map.get(e.ref, e.ref)
        if hasattr(e, "parent_ref"):
            e.parent_ref = remap_if_local(getattr(e, "parent_ref"))

    # Remap relationship endpoints
    for rel in page_ir.relationships:
        rel.source_ref = remap_if_local(rel.source_ref)
        rel.target_ref = remap_if_local(rel.target_ref)

    # Remap provenance.table_ref
    for e in element_lists:
        for p in getattr(e, "provenance", []):
            if getattr(p, "table_ref", None) in ref_map:
                p.table_ref = ref_map[p.table_ref]

    # Remap provenance.table_ref in relationship evidence
    for rel in page_ir.relationships:
        for ev in getattr(rel, "evidence", []) or []:
            for p in getattr(ev, "provenance", []) or []:
                if getattr(p, "table_ref", None) in ref_map:
                    p.table_ref = ref_map[p.table_ref]

    return page_ir


def normalize_provenance(
    *,
    doc_key: str,
    extraction_method: str,
    page_dimensions: Optional[tuple[float, float]] = None,
    page_index: int,
    page_ir: PageIR,
    pdf_name: str,
) -> PageIR:
    """Ensure every element has provenance and force doc-identity truth fields."""

    base_ptr = ProvenancePointer(
        bbox=None,
        bbox_kind=BBoxKind.UNKNOWN,
        doc_key=doc_key,
        extraction_method=extraction_method,
        page_dimensions=page_dimensions,
        page_index=page_index,
        pdf_name=pdf_name,
        section=None,
    )

    def _ensure_and_patch(
        ptrs: Optional[list[ProvenancePointer]],
    ) -> list[ProvenancePointer]:
        """Ensure provenance pointers exist and patch truth fields.

        Parameters
        ----------
        ptrs
            The existing provenance pointers.

        Returns
        -------
        list[ProvenancePointer]
            The ensured and patched provenance pointers.
        """

        if not ptrs:
            ptrs = [base_ptr.model_copy(deep=True)]  # Avoid shared instance

        for ptr in ptrs:
            # Force doc identity fields (don’t trust the LLM).
            ptr.doc_key = doc_key
            ptr.pdf_name = pdf_name
            ptr.page_index = page_index
            ptr.extraction_method = extraction_method

            # Fill if missing.
            if page_dimensions is not None:
                ptr.page_dimensions = page_dimensions
            if getattr(ptr, "bbox_kind", None) is None:
                ptr.bbox_kind = BBoxKind.UNKNOWN

        return ptrs

    # Patch provenance on all element lists in the page container.
    for col_name in (
        "curriculum_elements",
        "diagrams",
        "nodes",
        "statements",
        "tables",
    ):
        col = getattr(page_ir, col_name, None) or []
        for el in col:
            el.provenance = _ensure_and_patch(getattr(el, "provenance", None))

    # Patch relationships and their evidence provenance too.
    for rel in page_ir.relationships or []:
        rel.provenance = _ensure_and_patch(getattr(rel, "provenance", None))
        for ev in getattr(rel, "evidence", None) or []:
            ev.provenance = _ensure_and_patch(getattr(ev, "provenance", None))

    return page_ir


@cli.command()
def extract(  # pylint: disable=too-many-positional-arguments, too-many-statements
    pdf_fp: Path = typer.Argument(
        ...,
        dir_okay=False,
        exists=True,
        file_okay=True,
        help="The file path to the PDF document to extract curriculum data from.",
        readable=True,
        resolve_path=True,
    ),
    country: str = typer.Option(
        ..., "--country", "-c", help="The country associated with the PDF document."
    ),
    dpi: int = typer.Option(200, "--dpi", help="Render DPI for page images."),
    languages: list[str] = typer.Option(
        ...,
        "--language",
        "-l",
        help="One or more languages associated with the PDF document (e.g. -l en-US -l fr-FR).",
    ),
    model: str = typer.Option(
        "gpt-4o-2024-08-06",
        "--model",
        "-m",
        help="OpenAI model for page IR extraction.",
    ),
    output_dir: Path = typer.Option(
        Path("./results"), "--output_dir", "-o", help="Output directory root."
    ),
    start_page: int = typer.Option(
        0, "--start_page", "-s", help="0-based start page (inclusive)."
    ),
    end_page: Optional[int] = typer.Option(
        None, "--end_page", "-e", help="0-based end page (exclusive). Default: to end."
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Overwrite existing per-page artifacts."
    ),
    year: Optional[int] = typer.Option(
        None,
        "--year",
        "-y",
        help="Document year (optional; overrides any inferred year).",
    ),
) -> None:
    """Extract canonical curriculum intermediate representation (Layer A) from a PDF.

    The process is as follows:

    1. XXX

    Parameters
    ----------
    pdf_fp
        The file path to the PDF document to extract curriculum data from.
    country
        The country associated with the PDF document.
    dpi
        Render DPI for page images.
    languages
        One or more languages associated with the PDF document.
    model
        OpenAI model for page IR extraction.
    output_dir
        Output directory root.
    start_page
        0-based start page (inclusive).
    end_page
        0-based end page (exclusive). Default: to end.
    overwrite
        Overwrite existing per-page artifacts.
    year
        Document year (optional; overrides any inferred year).
    """

    logger.info(f"Starting curriculum extraction process for: {pdf_fp}...")

    # 1. Resolve paths.
    pdf_fp = pdf_fp.resolve()
    output_dir = output_dir.resolve()

    # 2. Persist extraction run metadata so we always have an extraction run record.
    doc_key = compute_doc_key(n_hex=64, pdf_fp=pdf_fp)
    extraction_dirs = create_extraction_dirs(doc_key=doc_key, output_dir=output_dir)
    extraction_run = ExtractionRunIR(
        extra={
            "country": country,
            "doc_key": doc_key,
            "dpi": dpi,
            "end_page_cli": end_page,  # Keep original CLI value (may be None)
            "languages": languages,
            "pdf_name": pdf_fp.name,
            "start_page": start_page,
        },
        models=[model],
        pipeline_version="0.1",
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_text(
        extraction_dirs.root / "extraction_run.json",
        extraction_run.model_dump_json(indent=2),
    )

    logger.info(f"PDF: {pdf_fp.name}")
    logger.info(f"doc_key: {doc_key}")
    logger.info(f"Extraction directory: {extraction_dirs.root}")

    try:
        with pymupdf.open(str(pdf_fp)) as doc:
            # 3. Validate page range.
            page_count = doc.page_count
            if end_page is None:
                end_page = page_count
            if not 0 <= start_page <= page_count:
                raise typer.BadParameter(f"start_page must be in [0, {page_count}]")
            if not (0 <= end_page <= page_count) or end_page < start_page:
                raise typer.BadParameter(
                    f"end_page must be in [start_page, {page_count}]"
                )

            # 4. Persist the updated run record.
            pdf_md = get_pdf_metadata(doc)
            logger.info(f"PDF metadata: {pdf_md}")
            extraction_run.extra.update(
                {
                    "page_count": page_count,
                    "end_page_resolved": end_page,  # The actual value used
                }
            )
            extraction_run.extra["pymupdf_metadata"] = pdf_md
            write_text(
                extraction_dirs.root / "extraction_run.json",
                extraction_run.model_dump_json(indent=2),
            )

            # 5. Persist document metadata.
            metadata = DocumentMetadataIR(
                country=country,
                extra={"pymupdf_metadata": pdf_md},
                languages=languages,
                publisher=pdf_md.get("producer", pdf_md.get("creator", None)),
                title=pdf_md.get("title", None),
                year=year,
            )
            write_text(
                extraction_dirs.root / "metadata.json",
                metadata.model_dump_json(indent=2),
            )

            # 6.
            continuity_state = load_continuity_state(extraction_dirs)

            # 7.
            pages: list[PageIR] = []
            for page_index in range(start_page, end_page):
                page_ir_fp = extraction_dirs.page_ir / f"{page_index:04d}.json"
                png_fp = extraction_dirs.page_images / f"{page_index:04d}.png"

                # Only render if it's missing OR if we are overwriting.
                if not png_fp.exists() or overwrite:
                    render_page_to_png(
                        doc=doc, dpi=dpi, output_png_fp=png_fp, page_index=page_index
                    )

                # If IR exists and we aren't overwriting, then load it.
                if page_ir_fp.exists() and not overwrite:
                    page_ir = PageIR.model_validate_json(page_ir_fp.read_text("utf-8"))
                    page_ir.page_index = page_index
                else:
                    # Otherwise, extract with a suitable vision model.
                    page_ir = extract_page_ir_with_llm(
                        model=model, page_index=page_index, png_fp=png_fp
                    )

                # Ensure page refs won’t collide across pages.
                page_ir = namespace_page_refs(
                    page_ir=page_ir, prefix=f"p{page_index:04d}:"
                )

                # Ensure all elements have provenance + page dimensions.
                dims = get_page_dimensions(doc, page_index)
                page_ir = normalize_provenance(
                    doc_key=doc_key,
                    extraction_method="vision+structured",
                    page_dimensions=dims,
                    page_index=page_index,
                    page_ir=page_ir,
                    pdf_name=pdf_fp.name,
                )

                # Update continuity state for next page (must happen after namespacing).
                page_ir = apply_cross_page_continuity(page_ir, continuity_state)
                continuity_state = build_continuity_state_from_page(page_ir)
                save_continuity_state(extraction_dirs, continuity_state)

                # Persist the normalized version (namespaced refs + provenance).
                write_text(page_ir_fp, page_ir.model_dump_json(indent=2))

                pages.append(page_ir)

            # 8. Merge to DocumentIR and save.
            extraction_run.completed_at = datetime.now(timezone.utc)
            doc_ir = merge_pages_to_document_ir(
                doc_key=doc_key,
                extraction_run=extraction_run,
                metadata=metadata,
                pages=pages,
                pdf_name=pdf_fp.name,
            )
            write_text(
                extraction_dirs.root / "canonical_ir.json",
                doc_ir.model_dump_json(indent=2),
            )

        extraction_run.extra["status"] = "success"
        logger.success("Extraction completed successfully!")
    except Exception as e:  # pylint: disable=broad-except
        extraction_run.extra["status"] = "error"
        extraction_run.extra["error"] = {
            "type": e.__class__.__name__,
            "message": str(e),
            "traceback": traceback.format_exc(limit=20),
        }
        raise
    finally:
        extraction_run.completed_at = datetime.now(timezone.utc)
        write_text(
            extraction_dirs.root / "extraction_run.json",
            extraction_run.model_dump_json(indent=2),
        )


if __name__ == "__main__":
    cli()
