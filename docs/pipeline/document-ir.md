# Document IR

Document IR construction is the third stage of the pipeline. It combines verified
page-level structures into a single document-level representation while preserving
source order and provenance.

For the broader workflow, see the [pipeline overview](index.md) and
[architecture](../architecture.md).

---

## Purpose

Stages 1 and 2 operate on individual pages and page boundaries. This stage turns those
verified page-local structures into ordered document-level segments that downstream KG
construction can interpret.

A `DocumentIR` contains:

- ordered block and table segments across the document;
- multi-page continuations merged into single segments;
- source page and item provenance for every stitched segment;
- lightweight heading context through `section_path`;
- per-page rendering metadata for interpreting bounding boxes; and
- non-fatal stitching warnings.

Document IR remains **layout- and source-oriented**. It does not infer Academic
Standards, curriculum hierarchy, Learning Components, or KG relationships.

---

## Entry point and configuration

The stitching command is implemented in:

```text
backend/src/skg/entries/stitch_document_ir.py
```

From the `backend/` directory, run:

```bash
python src/skg/entries/stitch_document_ir.py <config.json>
```

The command reads the `document_ir` section of the shared `RunConfig`. Important
settings include:

| Setting | Purpose |
| --- | --- |
| `keep_artifacts` | Whether headers, footers, page numbers, and similar artifacts remain in the stitched document |
| `verification_auto_stitch_confidence` | Minimum verification confidence required to automatically use a verified continuation link |
| `min_link_score` | Minimum score for deterministic fallback linking when applicable |
| `repair_hyphenation` | Whether to repair text split by page-break hyphenation |
| `sort_items_by_bbox` | Whether retained PageIR items are reordered by bounding-box position before stitching |
| `table_filldown_enabled` | Whether visually merged grouping cells may be filled down in stitched table output |
| `table_filldown_group_cols_max` | Maximum number of leading grouping columns eligible for fill-down |
| `overwrite` | Whether to replace an existing `document_ir.json` |

---

## How Document IR construction works

The stage is deterministic and does not make a new LLM call.

1. **Load verified PageIRs and verification results.**
   The command cross-checks the document key and loads the verified PageIR files from
   Stage 2 together with their page-pair verdicts.

2. **Normalize page items.**
   Python optionally filters extraction artifacts, preserves original PageIR item
   indices for provenance, and applies the configured item-ordering behavior.

3. **Resolve page-break links.**
   High-confidence verification verdicts are used to connect items that continue across
   adjacent pages. Deterministic guardrails prevent incompatible or content-reordering
   links. Bounded fallback heuristics can be used where the stitching configuration
   permits them.

4. **Build continuation chains.**
   Linked page items are assembled into ordered chains. Each retained normalized PageIR
   item must ultimately belong to exactly one document segment.

5. **Materialize document segments.**
   Block chains become `BlockSegment` objects and table chains become `TableSegment`
   objects. Single-page items also become segments, so `DocumentIR.segments` provides
   one ordered document-level sequence.

6. **Validate and persist the result.**
   Python verifies that normalized page items were consumed exactly once, constructs
   document and page metadata, records warnings, and writes the final artifacts.

---

## Blocks, tables, and heading context

### Block segments

Text, lists, headings, captions, figures, and other compatible block types remain typed
when they become document segments. Multi-page text can be joined into a combined value,
and optional deterministic hyphenation repair can reconstruct words split at a page
break.

### Table segments

Multi-page tables receive additional reconstruction logic. The stitcher can:

- remove repeated header rows from continuation slices;
- preserve canonical table headers and column structure;
- retain row- and slice-level provenance;
- reconstruct a rectangular grid from extracted spans; and
- optionally fill down visually merged values in a bounded number of leading grouping
  columns.

Fill-down is intentionally limited to configured grouping columns so blank cells in
content columns are not treated as repeated values.

### Section paths

As segments are produced in document order, nearby heading blocks are tracked as a
lightweight `section_path`. This provides downstream context without claiming that the
headings represent a curriculum hierarchy.

---

## Provenance and identity

Every stitched segment retains references to the PageIR items that produced it,
including page index, item index, bounding box, boundary state, and local code where
available. Tables additionally retain row-level provenance.

Each segment receives a deterministic ID derived from the document key and the first
source slice in the segment. This makes segment identity stable for the same source and
stitching result without asking an LLM to generate identifiers.

---

## Output artifacts

Results are stored under the document's stable key:

```text
<output_dir>/<doc_key>/stitching/
├── document_ir.json
├── stitch_report.json
└── stitching_run.json
```

The main artifacts are:

- **`document_ir.json`** — the ordered, stitched `DocumentIR` consumed by KG
  construction;
- **`stitch_report.json`** — link diagnostics, page-pair diagnostics, segment counts,
  table summaries, and warnings; and
- **`stitching_run.json`** — run configuration, status, and timestamps.

If `document_ir.json` already exists and `overwrite` is false, the stitching step is
skipped.

---

## Stage boundary

`DocumentIR` is the final source-reconstruction representation in the pipeline. It
combines page-level structures into document-level segments, but it deliberately stops
short of assigning curriculum semantics.

The next stage, **Academic Standards KG construction**, consumes `document_ir.json` and
is the first stage that identifies source-facing curriculum statements, resolves their
identity and hierarchy, and constructs Academic Standards entities and `hasChild`
relationships.

---

## Next

[Academic Standards KG →](academic-standards.md)
