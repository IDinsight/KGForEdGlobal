# Run, Resume, and Debug the Pipeline

This guide is for operating an existing curriculum profile: running the pipeline,
resuming interrupted work, deciding what must be regenerated after a change, and
finding the earliest artifact that explains an incorrect result.

For stage internals and artifact contracts, use the
[Pipeline Overview](../pipeline/index.md) and stage-specific pipeline pages. This guide
focuses on **operational decisions**.

!!! info "Roadmap: automated pipeline operations"
    We are working on an automated process for running and resuming the pipeline and for
    automatically diagnosing pipeline issues. This capability is on our roadmap, with
    timing **TBD**. Until it is available, this guide describes the supported manual
    operating and debugging workflow.

    If this automation would be useful to you, please
    [open a new feature issue on GitHub](https://github.com/IDinsight/SenegalKG/issues/new)
    so we can understand the demand and use cases.

## The operating rule that prevents most mistakes

When something looks wrong, find the **earliest persisted representation that is
already wrong** and fix or rerun that stage first.

Do not compensate downstream for an upstream error. For example:

- if the accepted PageIR is wrong, do not tune DocumentIR stitching;
- if the DocumentIR table is wrong, do not tune SFI extraction instructions;
- if the SFI is correct but its parent is wrong, inspect `hasChild` resolution rather
  than LC generation; and
- if an LC generation response is correct but two skills merge incorrectly, inspect LC
  deduplication rather than rewriting the decomposition policy.

The pipeline persists intermediate state specifically so this diagnosis can be made
without treating the final graph as a black box.

## Run identity and output isolation

All stages write beneath a stable document-specific directory:

```text
<output_dir>/<doc_key>/
├── extraction/
├── verification/
├── stitching/
└── kgs/
```

`doc_key` is derived from the **source PDF bytes**. This has two important operational
consequences:

1. Replacing the PDF with different bytes produces a different document directory.
2. Changing configuration, prompts, model settings, or backend code does **not** change
   the document key.

That means two materially different configurations can otherwise write into the same
`<output_dir>/<doc_key>/` tree.

!!! tip "Use a new output root for experiments"
    When comparing profiles, models, major prompt changes, or implementation changes,
    the safest option is usually a separate `page_ir_extraction.output_dir`.

    A fresh output root gives the new run an isolated artifact tree without requiring
    you to reason about which cached files are still compatible.

This is also why calibration runs should not share an output root with the final
production run.

## The four commands

Run all entry points from `backend/` with the same runtime config:

```bash
python src/skg/entries/extract_page_ir.py <config.json>
python src/skg/entries/verify_page_ir_continuity.py <config.json>
python src/skg/entries/stitch_document_ir.py <config.json>
python src/skg/entries/create_kgs.py <config.json>
```

The five conceptual pipeline stages are implemented through these four commands because
`create_kgs.py` builds the Academic Standards layer first and then derives Learning
Components from it.

## Understand `overwrite` before resuming

There are four independent overwrite settings:

```json
{
  "page_ir_extraction": {
    "overwrite": false
  },
  "page_ir_verification": {
    "overwrite": false
  },
  "document_ir": {
    "overwrite": false
  },
  "kgs": {
    "overwrite": false
  }
}
```

!!! note "`overwrite` is a config field"
    The current CLI entry points accept the config path, not a separate
    `--overwrite` option. If a log message says to "pass --overwrite", set the
    appropriate `overwrite` field in the runtime JSON instead.

`overwrite=false` does **not** mean the same thing at every stage:

| Stage                | What `overwrite=false` reuses                                                                                                                                | When to force regeneration                                                               |
|----------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------|
| Page IR extraction   | Existing accepted PageIR JSONs are skipped page by page; missing PNGs are rendered and missing PageIRs are extracted                                         | Extraction settings/model/code changed, or you intentionally want fresh PageIRs          |
| Page IR verification | Existing valid pair reports can be reused; complete compile/postprocess/verified outputs can also be skipped                                                 | PageIR inputs, verifier model/policy, candidate logic, or continuity behavior changed    |
| DocumentIR           | An existing `document_ir.json` causes the stitching stage to skip                                                                                            | Verified PageIRs or any stitching behavior/config changed                                |
| KG construction      | Several LLM-heavy sub-stages reuse complete current artifacts or aligned progress; deterministic derived artifacts are recomputed or cross-checked as needed | KG semantic config/model/code changed and you need the new behavior applied deliberately |

The safest mental model is:

> **Resume with `overwrite=false` only when the inputs and policy for that stage are
> intentionally unchanged.**

If you changed what a stage is supposed to mean, use its overwrite setting or a fresh
output root rather than assuming cached artifacts will be invalidated automatically.

## How each stage resumes

### Page IR extraction: page-level resume

Extraction checks each requested page independently.

With `page_ir_extraction.overwrite=false`:

- an existing page image is reused;
- a missing page image is rendered;
- an existing accepted `page_irs/<page>.json` is skipped; and
- a missing accepted PageIR is extracted and written.

This makes a simple interruption inexpensive to recover from. If extraction stopped on
page 83, rerunning the same command with the same config normally skips completed pages
and resumes at the first missing PageIR.

However, existing PageIRs are not regenerated merely because extraction configuration
or model settings changed. If you changed DPI, languages, extracted hints, extraction
behavior, the extraction model, or another setting that should affect accepted PageIRs,
set `page_ir_extraction.overwrite=true` or use a new output root.

### Page IR verification: pair-level LLM resume plus whole-stage derived state

Verification has two kinds of persisted state:

1. **pair-level semantic work** under `page_irs_pair_reports/`; and
2. **derived whole-stage state** such as `continuity_compile_report.json`,
   `postprocess_report.json`, and `page_irs_verified/`.

With `page_ir_verification.overwrite=false`, a valid existing pair report is loaded and
its LLM work is skipped. A malformed report is re-verified.

Once pair verdicts exist, the stage compiles them into continuity edits, postprocesses
the PageIR set, and writes the verified PageIRs. Those steps intentionally guard against
mixing an old compile report with fresh in-memory PageIRs.

As a result, an **incomplete verification directory can require cleanup or a full
verification overwrite**. For example, if `continuity_compile_report.json` exists but
`postprocess_report.json` does not, the next run cannot safely recreate the missing
postprocess output from unpatched in-memory PageIRs while also reusing the old compile
report.

For a normal interrupted pair-verification run, rerun with `overwrite=false`. If the
failure occurred after compile/postprocess state began to be written, see
[Recovering partial verification state](#recovering-partial-verification-state).

### DocumentIR: whole-stage reuse

DocumentIR construction is deterministic and comparatively inexpensive.

With `document_ir.overwrite=false`, if `stitching/document_ir.json` already exists the
stitcher skips reconstruction.

Therefore, if verified PageIRs changed—or if you changed settings such as link
thresholds, artifact retention, sort behavior, hyphenation repair, or table fill-down—set
`document_ir.overwrite=true`.

Do not expect an existing `document_ir.json` to be automatically invalidated by a
configuration change.

### KG construction: consistency-checked resume

`create_kgs.py` contains several resumable sub-stages rather than one blanket
"KG exists, skip" check.

Examples include:

- SFI extraction resumes from a schema-valid ordered prefix that still aligns with the
  current extraction windows;
- SFI dedup can reuse a complete current merge result or resume completed review
  responses;
- `hasChild` resolution can reuse complete current relationship artifacts or resume an
  aligned producer/checker prefix;
- LC generation reuses only complete draft/verdict/final-response triples that still
  pass quality checks against the current requests;
- failed LC generation requests are not treated as completed and can be retried;
- LC semantic dedup resumes saved pair adjudications; and
- final AS and AS+LC exports are reused only when their persisted payloads or input
  fingerprints still match the freshly computed state.

This makes `kgs.overwrite=false` useful after transient failures.

It is **not**, however, a universal semantic config-change detector. Some progress
checks are designed to prove structural alignment with current requests/windows, not to
prove that every previously generated semantic response was created with exactly the
same prompt text, model, or instruction wording.

If you changed AS/LC instructions, model selection, taxonomy/hierarchy policy,
deduplication semantics, or implementation behavior and need to guarantee fresh LLM
judgments, use `kgs.overwrite=true` or a separate output root.

!!! warning "KG overwrite currently applies to the whole KG command"
    There is one `kgs.overwrite` flag for both Academic Standards and Learning
    Components. There is not a separate LC-only overwrite flag.

    If you set `kgs.overwrite=true` to guarantee fresh LC generation, the expensive AS
    LLM sub-stages are also restarted. There is currently no supported switch that
    forces only the LC LLM work to restart while guaranteeing that all AS LLM work is
    reused.

## Resume after an interruption

For an unchanged config and unchanged source, start with the least destructive option:
rerun the command that failed with its stage `overwrite=false`.

| What happened                                                 | Recommended recovery                                                                                                                                            |
|---------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Extraction stopped during a page                              | Rerun extraction with `page_ir_extraction.overwrite=false`; completed PageIRs are skipped                                                                       |
| Verification stopped while judging page pairs                 | Rerun verification with `page_ir_verification.overwrite=false`; valid pair reports are reused                                                                   |
| Verification has partial compile/postprocess/verified outputs | Prefer a full verification rerun with `overwrite=true`, or use the targeted recovery below only when pair reports are known-current                             |
| Stitching failed before writing `document_ir.json`            | Fix the cause and rerun stitching; `overwrite=false` is sufficient when no DocumentIR exists                                                                    |
| Stitching completed but you changed its inputs/config         | Set `document_ir.overwrite=true` and rerun stitching                                                                                                            |
| KG construction stopped partway through                       | Rerun `create_kgs.py` with `kgs.overwrite=false`; compatible progress is reused                                                                                 |
| LC generation exceeded `lc_max_failure_rate`                  | Inspect `lc_generation_failures.json`, correct any transient/config issue, then rerun with `kgs.overwrite=false` to retry requests that did not complete        |
| A resume check says existing artifacts are stale/misaligned   | First verify that the source/config did not change unintentionally; if the change was intentional, restart that stage with overwrite or use a fresh output root |

### Recovering partial verification state

The safest recovery is:

1. set `page_ir_verification.overwrite=true`;
2. rerun verification; then
3. rebuild DocumentIR and the KG if their inputs may have changed.

This re-runs pair judgments as well as the derived verification steps.

If pair-level LLM work is expensive and you are certain that all existing pair reports
are complete and still valid for the **same extracted PageIRs and verification policy**,
you can preserve them and regenerate only downstream verification state:

1. keep `verification/page_irs_pair_reports/`;
2. remove `continuity_compile_report.json`;
3. remove `postprocess_report.json`;
4. remove the existing files under `page_irs_verified/`; and
5. rerun verification with `page_ir_verification.overwrite=false`.

The pair reports will be reloaded, while compile/postprocess/save can run again.

Do **not** use this shortcut after changing extracted PageIRs, verifier model/instructions,
candidate-selection behavior, or verification policy. In those cases, re-verify the
pairs.

## What must be rerun after a change?

Use this matrix conservatively. "Rerun downstream" means that outputs derived from the
changed stage should not be trusted merely because files already exist.

| Change                                                                                                                        | Earliest affected stage           | Recommended rerun                                                                                                        |
|-------------------------------------------------------------------------------------------------------------------------------|-----------------------------------|--------------------------------------------------------------------------------------------------------------------------|
| Source PDF bytes                                                                                                              | Page IR extraction                | Run all four commands; the changed PDF receives a new `doc_key`                                                          |
| Extraction model, DPI, languages, extracted hints, extraction logic, or accepted PageIR contract                              | Page IR extraction                | Extraction with overwrite, then verification, DocumentIR, and KG from the regenerated PageIRs                            |
| Verification model, candidate logic, semantic instructions, search/selection behavior, or continuity policy                   | Page IR verification              | Verification with overwrite, then DocumentIR and KG                                                                      |
| Only verified PageIR inputs changed                                                                                           | Page IR verification / DocumentIR | Rebuild verification if necessary, then DocumentIR and KG                                                                |
| Stitching/link/fill-down/normalization behavior                                                                               | DocumentIR                        | Stitching with overwrite, then KG                                                                                        |
| Academic Standards taxonomy, extraction policy, identity/code scope, dedup policy, hierarchy policy/instructions, or KG model | Academic Standards KG             | Run `create_kgs.py`; use `kgs.overwrite=true` when fresh semantic judgments are required                                 |
| LC eligibility, generation instructions, validation instructions, dedup scope/blocking/judge policy, or KG model              | Learning Components               | Run `create_kgs.py`; use `kgs.overwrite=true` to guarantee fresh LC judgments, noting that AS LLM work is also restarted |
| Delivery/export schema setting only                                                                                           | Final KG compilation              | Rerun `create_kgs.py`; upstream document stages do not need to be rerun                                                  |
| Documentation only                                                                                                            | None                              | No pipeline rerun                                                                                                        |

For a high-stakes comparison between old and new semantic policy, prefer a new
`output_dir` over mutating a production artifact tree in place.

## Page ranges and calibration runs

Extraction and verification each have `start_page` / `end_page` settings. Verification
can only operate on a contiguous range that actually exists in the extraction output.
These ranges use zero-based page indexes and an exclusive `end_page`.

For an **end-to-end** run through DocumentIR, there is an additional current constraint:
the verified PageIR set loaded by the stitcher must be contiguous and start at page
index `0`.

Therefore:

- a small calibration run such as `[0, 10)` can be run through all four commands;
- a non-zero slice such as `[40, 50)` is useful for PageIR extraction/verification
  inspection, but cannot be stitched by itself in the current pipeline; and
- if you need an end-to-end calibration of pages from the middle of a large source,
  create a separate cropped test PDF so that its first page is index `0`, and use a
  separate output root.

Also keep the extraction and verification ranges compatible. Requesting verification
outside the available extracted pages fails explicitly rather than silently skipping the
missing range.

## Debug from the earliest wrong artifact

Use this sequence rather than jumping directly to the final bundle.

| If this is wrong...                                            | Inspect first                                                                       | Likely stage to fix                                           |
|----------------------------------------------------------------|-------------------------------------------------------------------------------------|---------------------------------------------------------------|
| The rendered source page itself                                | `extraction/page_images/<page>.png`                                                 | Extraction rendering/DPI/source file                          |
| Visible text, table geometry, item type, or boundary in PageIR | accepted `page_irs/<page>.json`, then `page_irs_raw/`                               | Page IR extraction                                            |
| Whether page N continues to N+1                                | `page_irs_pair_reports/<N>_<N+1>.json` and its crop                                 | Page IR verification                                          |
| Pair verdict is correct but verified PageIR state is wrong     | `continuity_compile_report.json`, `postprocess_report.json`, verified PageIR        | Verification compile/postprocess thresholds or logic          |
| Cross-page text/table reconstruction                           | `stitch_report.json` and `document_ir.json`                                         | DocumentIR                                                    |
| Which source content was sent for SFI extraction               | `sfi_extraction_window_plan.json` and `sfi_extraction_windows.jsonl`                | AS window/table-selection config                              |
| A standard was omitted/mis-typed/mis-scoped                    | `sfi_extraction_results.jsonl` and `sfi_candidate_registry.json`                    | AS extraction taxonomy/instructions                           |
| Two source candidates were merged or kept separate incorrectly | `sfi_merge_report.json`, groups/conflicts, dedup review artifacts                   | AS identity/dedup policy                                      |
| Final SFI is correct but its direct parent is wrong            | `has_child_candidate_parent_sets.jsonl`, requests, drafts/verdicts/final responses  | AS hierarchy retrieval/policy/instructions                    |
| A standard did not enter LC generation                         | `lc_eligibility_report.json` and `lc_eligible_sfis.json`                            | LC eligibility policy or unresolved ancestry                  |
| Atomic decomposition is wrong                                  | LC request, producer draft, validation verdict, and final response                  | LC generation/validation policy                               |
| Two LC texts were or were not merged correctly                 | `lc_dedup_candidate_pairs.jsonl`, `lc_dedup_verdicts.jsonl`, `lc_dedup_groups.json` | LC blocking/scope/semantic dedup                              |
| Final counts/endpoints/provenance do not reconcile             | AS validation report, LC summary, AS bundle, combined bundle                        | Finalization/validation; then trace to earliest failing input |

The [Pipeline Overview](../pipeline/index.md#where-to-inspect-a-run) contains the compact
artifact map; the stage pages explain what each artifact means.

## Common failure signatures

The exact traceback is the authority, but these patterns usually point to a specific
class of operational problem.

| Symptom or message                                                                  | Usually means                                                                    | What to do                                                                                                                          |
|-------------------------------------------------------------------------------------|----------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------|
| `PDF doc_key mismatch`                                                              | The configured PDF bytes do not match the upstream run                           | Use the original PDF for that artifact tree or start a new run for the new PDF                                                      |
| Page image and PageIR directories do not match                                      | Extraction is incomplete or files were manually removed                          | Rerun extraction with the intended range and unchanged config                                                                       |
| Requested verification range is outside available extracted pages                   | Extraction/verification page ranges disagree                                     | Align the ranges or extract the missing pages                                                                                       |
| Edge verdict references candidate items that do not exist                           | Pair reports were produced for different/stale PageIR structure                  | Rerun verification with overwrite after confirming extraction is current                                                            |
| Verification cannot run postprocess/save because an upstream output is being reused | The verification directory contains partial derived state                        | Full verification overwrite, or targeted cleanup if pair reports are unquestionably current                                         |
| `document_ir.json` already exists and stitching is skipped                          | Whole-stage DocumentIR reuse is active                                           | Set `document_ir.overwrite=true` if you intended to rebuild it                                                                      |
| Existing SFI extraction result does not match the current window                    | KG extraction progress no longer aligns with the current window plan             | Confirm the DocumentIR/KG config change, then restart KG with overwrite or use a fresh output root                                  |
| Academic Standards export validation fails                                          | Finalized AS entities/edges/mappings/provenance violate a deterministic contract | Start with `as_validation_report.json`, then trace the named inputs upstream                                                        |
| LC generation failure rate exceeds `lc_max_failure_rate`                            | Too many eligible SFIs failed generation after retries                           | Inspect `lc_generation_failures.json`; rerun without KG overwrite after a transient fix, or adjust the actual LC policy/model issue |
| Existing final bundle is reported as stale because fingerprints differ              | Current deterministic inputs differ from the saved delivery bundle               | Let the compiler rebuild it; investigate only if the input change was unexpected                                                    |
| A resumed run reports little or no LLM usage                                        | Compatible semantic artifacts were reused                                        | Expected; usage in the current run record is not cumulative lifetime usage                                                          |

## Run records, status, and LLM usage

Each command writes a stage run record:

```text
extraction/extraction_run.json
verification/verification_run.json
stitching/stitching_run.json
kgs/kg_run.json
```

On completion, the run record contains the current invocation's timestamps and status.
When an exception escapes the stage, the record also captures an error type, message,
and traceback under `extra.error`.

LLM stages include usage information under `extra.usage`, with per-agent buckets and
aggregate totals. Use these records to answer questions such as:

- Did this invocation actually make model calls or mostly resume cached work?
- Which semantic phase accounted for most input/output tokens?
- Did an interrupted run fail before or after significant LLM work?

!!! note "Usage is per invocation, not cumulative artifact cost"
    A usage tracker starts fresh each time the command runs. If a resumed invocation
    reuses existing responses, the tokens spent creating those old responses are not
    added again to the new run's usage totals.

    Preserve earlier run records externally if you need historical/cumulative cost
    accounting; the stage `*_run.json` path is rewritten on later invocations.

For KG runs, `kg_run_manifest.json` is also a useful **preparation diagnostic**. It
summarizes source/document characteristics such as segment counts, observed languages,
code-pattern match counts, table-selection counts/policy, and warnings. Inspect it when
a KG run behaves as though the wrong source structures are being selected before
looking at model responses.

### Increase logging while diagnosing

Logging verbosity is controlled by the backend setting `LOGGING_LOG_LEVEL`. For a local
debugging run, set for example:

```text
LOGGING_LOG_LEVEL=DEBUG
```

Then rerun only the earliest affected stage. Avoid using a more verbose log level as a
substitute for inspecting the persisted artifacts—the artifacts are the durable audit
record.

## Clean rerun strategies

Choose the narrowest strategy that still guarantees the behavior you intend to test.

### 1. Fresh output root — safest for comparisons

Use a new `page_ir_extraction.output_dir` and run from extraction onward.

Best for:

- major profile revisions;
- model comparisons;
- prompt/instruction experiments;
- pipeline implementation changes; and
- final production runs after calibration.

This costs more but provides the clearest audit boundary.

### 2. Stage overwrite — normal regeneration

Keep the same output root, set the earliest affected stage's `overwrite=true`, and
regenerate all downstream stages whose inputs depend on it.

This is appropriate when the source and run identity are unchanged and you intentionally
want to replace derived artifacts in place.

### 3. Targeted artifact recovery — only for known partial state

Use selective deletion only when you understand the stage's resume contract. The
verification recovery described above is the main case where preserving expensive
pair reports while rebuilding deterministic derived outputs can be worthwhile.

Avoid manually deleting arbitrary KG JSONL records. Several KG sub-stages rely on
ordered prefixes, request IDs, aligned producer/checker artifacts, and cross-artifact
consistency. When KG progress is genuinely stale, `kgs.overwrite=true` or a fresh output
root is usually safer than artifact surgery.

## Before rerunning downstream

Use this short checklist:

- confirm the configured PDF is the intended source;
- confirm the output root is the intended run tree;
- decide whether this is a **resume** or a **semantic regeneration**;
- set overwrite only on the earliest stage that must be regenerated;
- if an upstream accepted artifact changes, rebuild every downstream representation
  that depends on it;
- inspect the stage run record after completion;
- inspect validation/unresolved/failure artifacts before treating a successful process
  exit as a publishable graph; and
- for important comparisons, keep old and new runs in separate output roots.

## Related documentation

- [Add a New Curriculum](adding-a-curriculum.md) — design and calibrate a new source
  profile.
- [Pipeline Overview](../pipeline/index.md) — stage contracts and complete artifact map.
- [Architecture](../architecture.md) — trust boundaries, provenance, and deterministic
  responsibilities.
- [Page IR Extraction](../pipeline/page-ir-extraction.md) — extraction details.
- [Page IR Verification](../pipeline/page-ir-verification.md) — continuity verification
  and compile behavior.
- [Document IR](../pipeline/document-ir.md) — deterministic stitching behavior.
- [Academic Standards](../pipeline/academic-standards.md) — AS extraction,
  reconciliation, hierarchy, and export.
- [Learning Components](../pipeline/learning-components.md) — LC eligibility,
  decomposition, validation, deduplication, provenance, and finalization.
