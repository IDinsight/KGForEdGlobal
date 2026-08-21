# Page IR Extraction

Page IR extraction is the first stage of the pipeline. It converts each selected PDF
page into a structured, layout-faithful `PageIR` that records what is visibly present
on that page without assigning document-wide curriculum semantics.

For the broader workflow, see the [pipeline overview](index.md) and
[architecture](../architecture.md).

---

## Purpose

The goal of this stage is to create a reliable page-level representation that later
stages can verify, stitch, and interpret.

A `PageIR` preserves page-local information such as:

- headings, paragraphs, lists, captions, figures, and artifacts;
- tables, rows, cells, and visible spanning structure;
- text and language information;
- bounding boxes in rendered-page pixel coordinates;
- visible local codes when present; and
- item-level boundary hints indicating whether content appears complete, truncated,
  resumed, or both.

This stage does **not** decide the document-wide curriculum hierarchy or create
Academic Standards or Learning Components.

---

## Entry point and configuration

The extraction command is implemented in:

```text
backend/src/skg/entries/extract_page_ir.py
```

From the `backend/` directory, run:

```bash
python src/skg/entries/extract_page_ir.py <config.json>
```

The command reads the `page_ir_extraction` section of the shared `RunConfig`.
Important settings include:

| Setting | Purpose |
| --- | --- |
| `pdf_fp` | Source curriculum PDF |
| `output_dir` | Root directory for pipeline results |
| `dpi` | Resolution used to render PDF pages to PNG |
| `start_page` / `end_page` | Optional 0-based page range |
| `languages` | Expected source-language context |
| `use_extracted_hints` | Whether to provide usable PyMuPDF text/table hints to the extraction agent |
| `overwrite` | Whether to regenerate existing PageIR JSON files |

---

## How extraction works

For each selected page, the pipeline performs the following steps:

1. **Render the page to PNG.**
   The PDF page is rendered at the configured DPI. The rendered image provides the
   visual evidence used by the extraction and validation agents.

2. **Optionally collect PDF-layer hints.**
   When `use_extracted_hints` is enabled, PyMuPDF can provide text-layer and table-layer
   hints. Unusable text layers are rejected by quality gates before they reach the
   model.

   The hints are supplementary rather than a replacement for the image. The rendered
   page is authoritative for content presence, layout, reading order, block type,
   bounding boxes, and structure. For text that is visibly present, a usable PDF text
   layer can be used to preserve character-level spelling, including diacritics and
   non-Latin characters.

3. **Extract a structured `PageIR`.**
   A page-level extraction agent receives the image, expected languages, and any usable
   hints and returns structured output conforming to the `PageIR` schema.

4. **Run deterministic quality checks.**
   Python validates the extracted structure. If a quality check fails, the agent is
   given the error and can retry with a corrected output.

5. **Audit the accepted extraction against the page image.**
   A separate validation agent compares the structured `PageIR` with the original
   rendered page. If it finds material errors, it returns a complete corrected
   `PageIR`. Any correction must itself pass the deterministic quality checks.

6. **Finalize Python-owned metadata and persist the page.**
   The pipeline fills fields such as the document key, PDF name, DPI, page index,
   coordinate space, and image dimensions. The page-level `boundary_state` is derived
   deterministically from item-level boundary hints and is not authored directly by the
   LLM.

The accepted result is then written as the canonical PageIR for that page.

---

## Validation

The extraction stage combines schema validation, deterministic quality checks, and an
independent image-based audit.

Representative Python checks include:

- image dimensions and bounding-box bounds;
- duplicate, placeholder, or implausible full-page bounding boxes;
- required block content and extraction-time text constraints;
- figure, footnote, and artifact plausibility;
- table integrity and likely collapsed table grids;
- item-level continuation-state consistency; and
- gross visual reading order.

A model response is therefore not accepted simply because it matches the JSON schema.
It must also satisfy extraction-specific quality rules, and the resulting page is
independently checked against the source image.

---

## Output artifacts

Results are stored under the stable document key:

```text
<output_dir>/<doc_key>/extraction/
├── extraction_run.json
├── page_images/
│   ├── 0000.png
│   ├── 0001.png
│   └── ...
├── page_irs/
│   ├── 0000.json
│   ├── 0001.json
│   └── ...
└── page_irs_raw/
    └── ... extraction attempt and retry artifacts ...
```

The main artifacts are:

- **`page_images/`** — rendered page images used as visual evidence;
- **`page_irs/`** — accepted `PageIR` JSON files used by the next pipeline stage;
- **`page_irs_raw/`** — extraction-attempt metadata, parsed outputs, and quality-error
  artifacts useful for debugging retries; and
- **`extraction_run.json`** — run metadata, status, model information, timestamps, and
  aggregate extraction/validation token usage.

If a PageIR already exists and `overwrite` is false, the page extraction is skipped.
This allows an interrupted or partial run to resume without regenerating completed
pages.

---

## Stage boundary

Page IR extraction is intentionally page-local. Item-level continuation labels are
initial observations only; this stage does not establish a trusted relationship between
content on adjacent pages.

The next stage, **Page IR continuity verification**, evaluates those page breaks using
bounded evidence from both pages before continuity metadata is allowed to influence
document stitching.

---

## Next

[Page IR Verification →](page-ir-verification.md)
