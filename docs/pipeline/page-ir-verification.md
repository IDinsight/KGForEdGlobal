# Page IR Verification

Page IR verification is the second stage of the pipeline. It reviews adjacent page
boundaries to determine whether content at the end of page *N* continues at the start
of page *N+1*.

For the broader workflow, see the [pipeline overview](index.md) and
[architecture](../architecture.md).

---

## Purpose

Page IR extraction is intentionally page-local, so its continuation labels are only
initial observations. This stage verifies those cross-page relationships using evidence
from both pages before they are allowed to influence document stitching.

The verifier can confirm or reject continuations involving:

- text or list-like blocks;
- tables; and
- figures.

It can also correct table-specific continuation metadata, such as whether a continued
table visually repeats its header on the next page.

This stage does **not** re-extract page content or infer curriculum semantics.

---

## Entry point and configuration

The verification command is implemented in:

```text
backend/src/skg/entries/verify_page_ir_continuity.py
```

From the `backend/` directory, run:

```bash
python src/skg/entries/verify_page_ir_continuity.py <config.json>
```

The command reads the `page_ir_verification` section of the shared `RunConfig`.
Important settings include:

| Setting | Purpose |
| --- | --- |
| `start_page` / `end_page` | Optional 0-based page range |
| `min_confidence_to_select_positive` | Minimum confidence for a positive verdict to be preferred during candidate selection |
| `min_confidence_to_patch` | Minimum confidence required before a selected verdict can change PageIR metadata |
| `min_confidence_to_stop_negative_search` | Confidence required to stop alternate-candidate search after a strong primary negative |
| `next_page_crop_padding_px` | Extra visual context included below a selected next-page candidate |
| `overwrite` | Whether to re-run page-pair verification instead of reusing existing pair reports |

By default, selection is more permissive than mutation. A candidate continuation can
therefore be the best available explanation without automatically changing the PageIR.

---

## How verification works

For each adjacent page pair, the pipeline performs the following steps:

1. **Identify plausible boundary candidates.**
   Python selects a small set of non-artifact items near the bottom of page *N* and
   compatible candidates near the top of page *N+1*. Candidate matching preserves
   structural families: tables are compared with tables, figures with figures, and
   other blocks with compatible block candidates.

2. **Build bounded visual evidence.**
   The verifier receives the previous-page image and a pair-specific crop of the next
   page around the candidate being evaluated. Existing extraction-time continuation
   hints are removed from the evidence excerpts so the model does not simply repeat
   the Stage 1 decision.

3. **Verify the candidate pair.**
   The verification agent returns a structured verdict containing whether the items
   continue, the continuation kind, confidence, rationale, and any permitted repeated-
   header patch for a table continuation.

4. **Validate the verdict.**
   Deterministic checks enforce type and state consistency. A separate validation agent
   then audits the verdict against the source images and can return a corrected verdict
   when necessary. Corrections must pass the same deterministic checks.

5. **Select one verdict for the page boundary.**
   If alternate candidates were evaluated, Python selects the best successful attempt
   according to the configured selection policy. Strong primary positives and strong
   same-family negatives can stop the search early.

6. **Compile trusted changes.**
   Only selected verdicts whose confidence meets `min_confidence_to_patch` are allowed
   to modify PageIR boundary metadata. Lower-confidence decisions are recorded but the
   extraction-time boundary state is preserved.

7. **Postprocess and save verified PageIRs.**
   Python reconciles item and page boundary states, applies safe table metadata changes,
   performs table normalization, and propagates table local codes only across verified
   table-continuation edges.

---

## What can change

A sufficiently confident verdict can update the cross-page state of the selected items,
for example:

```text
page N item      COMPLETE   -> TRUNCATED
page N+1 item    COMPLETE   -> RESUMED
```

An item that both resumes from the previous page and continues onto the next page can
become `BOTH`.

For verified table continuations, the compiler can also update `repeats_header` and
safely carry a missing table `local_code` onto a resumed table. Conflicting existing
codes are preserved and reported rather than silently overwritten.

If a selected verdict is below the patch threshold, none of these continuity changes
are applied.

---

## Output artifacts

Results are stored under the same stable document key as extraction:

```text
<output_dir>/<doc_key>/verification/
├── verification_run.json
├── continuity_compile_report.json
├── postprocess_report.json
├── page_irs_pair_crops/
├── page_irs_pair_reports/
└── page_irs_verified/
    ├── 0000.json
    ├── 0001.json
    └── ...
```

The main artifacts are:

- **`page_irs_pair_reports/`** — candidate attempts and the selected verdict for each
  adjacent page boundary;
- **`page_irs_pair_crops/`** — pair-specific visual crops used during verification;
- **`continuity_compile_report.json`** — which selected decisions were applied or
  skipped and what continuity metadata changed;
- **`postprocess_report.json`** — deterministic postprocessing changes and review
  information;
- **`page_irs_verified/`** — the verified PageIR JSON files consumed by Document IR
  construction; and
- **`verification_run.json`** — run status, configuration, model information,
  timestamps, and aggregate usage.

When `overwrite` is false, existing page-pair reports can be reloaded so an interrupted
run does not need to repeat completed verification calls.

---

## Stage boundary

The output of this stage is still Page IR: the page-local content and layout extracted
in Stage 1 remain intact, but cross-page continuation metadata has now been independently
reviewed and confidence-gated.

The next stage, **Document IR construction**, uses these verified boundaries to stitch
page-local structures into document-level segments while preserving source provenance.

---

## Next

[Document IR →](document-ir.md)
