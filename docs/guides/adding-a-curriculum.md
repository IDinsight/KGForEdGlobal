# Add a New Curriculum

This guide describes how to adapt the pipeline to a new curriculum document without
adding curriculum-specific logic to the backend.

The goal is not to tune every available setting. The goal is to encode the smallest
reviewed **document profile** that lets the generic pipeline reconstruct the source,
extract the intended Academic Standards hierarchy, and derive Learning Components at
the right granularity.

Start with a representative subset of the document, inspect the intermediate artifacts,
and only then run the full curriculum.

!!! tip "Start from the closest existing profile"
    The profiles under `examples/` are the best starting point. Choose the curriculum
    whose source structure is most similar to the new document, not necessarily the one
    from the same country or subject.

    Current examples include Ghana, Nigeria, Rwanda, and India profiles.

## What belongs in the curriculum profile?

A curriculum profile should describe **source-specific evidence and policy**. The
backend should continue to own **general invariants that must hold for every
curriculum**.

| Put this in configuration                                                    | Put this in Python                                      |
|------------------------------------------------------------------------------|---------------------------------------------------------|
| Local statement types such as `Strand`, `Sub-Strand`, `Indicator`, or `Unit` | Schema validation and identifier integrity              |
| Aliases and controlled values visible in a particular source                 | Universal provenance requirements                       |
| Which tables contain standards                                               | Exactly-once DocumentIR consumption                     |
| Curriculum-specific code formats and code scope                              | Graph endpoint, cardinality, and cycle checks           |
| Expected source hierarchy and allowed direct-parent types                    | Deterministic UUID construction                         |
| Grade/stage mappings                                                         | Generic PageIR and DocumentIR transformations           |
| Source-specific extraction or validation instructions                        | Generic producer/checker orchestration                  |
| LC seed types, decomposition guidance, and dedup policy                      | Generic LC reconciliation and export rules              |
| Known document conventions or reviewed anomalies                             | Logic that should apply identically to every curriculum |

A useful test is: **would the rule still be correct for a structurally different
curriculum?** If not, prefer configuration.

## Before editing the config: inspect the source

Do a short source review before defining statement types or writing prompt instructions.
For a large PDF, inspect representative pages rather than reading every page first.

Try to find examples of:

- the highest-level curriculum organizers;
- the lowest-level statements that should become standards;
- repeated hierarchy levels such as grade, strand, topic, sub-topic, unit, or competency;
- official item codes and whether they repeat in different scopes;
- tables that contain standards and tables that are administrative or explanatory;
- merged cells or rowspans that may need DocumentIR fill-down;
- statements that cross page boundaries;
- multilingual or bilingual layouts;
- the grade/stage vocabulary used by the source; and
- representative standards that should produce one LC versus several LCs.

Write down the intended hierarchy before configuring it. For example:

```text
Grade
└── Strand
    └── Sub-Strand
        └── Content Standard
            └── Indicator
```

or:

```text
Grade
└── Topic Area
    └── Sub-Topic Area
        └── Unit
            └── Key Unit Competence
                ├── Knowledge Objective
                ├── Skills Objective
                └── Attitudes and Values Objective
```

The source does not need to be a strict tree: the KG layer supports multiple valid
parents where the configured parent policy allows them. The sketch is still useful for
identifying the source-facing roles that the profile needs to represent.

## 1. Copy a nearby example and set the run basics

Create a new config beside the existing examples, for example:

```text
examples/<country-or-provider>/config_<subject>.json
```

The runtime config has four top-level sections:

```json
{
  "page_ir_extraction": {},
  "page_ir_verification": {},
  "document_ir": {},
  "kgs": {}
}
```

Set the document-specific extraction values first:

- `pdf_fp` — path to the source PDF;
- `output_dir` — root directory for this curriculum's run artifacts;
- `country` and `year`;
- `languages` — BCP-47 language codes such as `en`, `fr`, or `rw`;
- `start_page` / `end_page` — use a representative slice while calibrating; and
- `use_extracted_hints` — useful when the PDF has a reliable text layer or when
  extraction benefits from text/table hints.

The verification and DocumentIR defaults are generally a better starting point than
curriculum-specific tuning. Change them only in response to an observed reconstruction
problem.

### Use an isolated calibration output

During profile development, use a separate `output_dir` for calibration runs. This
prevents a partial-page experiment from being mixed with the artifacts from a later
full-document run.

A good calibration slice contains at least:

- one normal standards page;
- one page boundary where content continues;
- one representative standards table, if the curriculum is table-heavy; and
- one structurally unusual page that the final run must handle correctly.

For curricula with materially different sections or grade layouts, use several small
slices rather than assuming one section represents the entire document.

## 2. Define the Academic Standards vocabulary

The `kgs.as.statement_type_policy` is the semantic backbone of the profile. Define this
before tuning extraction instructions.

Each policy item names a **source-facing statement type** and maps it to either a
Learning Commons `Standard Grouping` or `Standard`.

Conceptually:

```json
{
  "statement_type": "Sub-Strand",
  "normalized_statement_type": "Standard Grouping",
  "description": "Visible sub-strand organizer under the active Grade and Strand.",
  "aliases": ["Sub-strand", "Substrand"],
  "controlled_values": [
    {
      "canonical_value": "Fractions",
      "aliases": ["Number: Fractions"]
    }
  ]
}
```

The example above is illustrative; use source-visible labels and values from the new
curriculum.

### Prefer source-facing names

Use the source's real conceptual roles rather than forcing every curriculum into a
generic taxonomy. `Indicator`, `Competency`, `Learning Outcome`, and `Knowledge
Objective` can all be valid source-facing statement types when that is how the document
is organized.

### Use aliases as recognition evidence

Aliases are useful for capitalization, punctuation, abbreviations, and source-visible
variants. They should not create new semantic categories.

For grouping types with a bounded known vocabulary, use `controlled_values` to define a
canonical value and its source variants. Do not enumerate free-form standards as
controlled values.

### Keep descriptions operational

A statement-type description should help distinguish the role from nearby content. For
example, specify whether a label applies only inside a unit table, whether it represents
an official coded standard, or whether similarly named front-matter text should be
excluded.

## 3. Decide which document structures can produce standards

Academic Standards extraction is windowed over DocumentIR. Table selection should be
explicit when a curriculum contains many non-standards tables.

Useful settings include:

- `included_table_columns_signatures`;
- `included_table_section_patterns`;
- `excluded_table_columns_signatures`; and
- `excluded_table_section_patterns`.

Use these to express source structure, not to compensate for a bad PageIR extraction.
If the table itself is reconstructed incorrectly, fix or tune the earlier stage first.

When section context is the reliable signal, prefer a bounded section-pattern rule. When
a stable columns signature uniquely identifies standards tables, prefer the signature.
Use explicit exclusions for recurring administrative tables that otherwise look
eligible.

## 4. Configure codes only when the source has real item codes

If the document assigns official codes to standards, define them under
`kgs.as.code_patterns` and connect the relevant statement types through their
`code_type`.

Before adding a code pattern, answer two questions:

1. **What does the code identify?** A standard, an organizer, a table, or merely a
   printed section label?
2. **Where is the code unique?** Across the document, or only within a grade, strand,
   unit, or other semantic scope?

If a code is only unique within a reviewed scope, configure
`code_scope_statement_types`. Do not make the regex itself encode hierarchy that is
better represented as semantic scope.

Avoid treating nearby organizer codes or table identifiers as standard codes simply
because they match a convenient pattern.

## 5. Define deterministic identity scope

Two standards can have identical wording without being the same curricular item. The
profile must state which grouping dimensions are part of identity when wording or codes
alone are insufficient.

Use `identity_scope_statement_types` to define the ordered source-facing grouping types
required for each statement type.

For example, a unit label such as `Unit 1` may repeat in every grade. Its identity may
therefore require `Grade`, while a lower-level objective may require both `Grade` and
`Unit`.

Good identity scope should answer:

> If this exact text appeared elsewhere in the document, what visible source context
> would prove that it is a different curricular item?

Do not add dimensions merely because they are available. Excessive scope prevents true
duplicates from reconciling.

## 6. Map the source hierarchy

After the statement vocabulary is stable, configure the expected Academic Standards
hierarchy.

Two settings are especially important:

- `sfi_has_child_statement_type_hierarchy` — preferred hierarchy order used during
  parent retrieval/ranking; and
- `sfi_has_child_parent_policy` — allowed direct-parent types and their cardinality for
  **every** configured statement type.

A root statement type has an empty parent-policy list. A normal one-parent level might
look conceptually like:

```json
{
  "Sub-Strand": [
    {
      "parent_statement_type": "Strand",
      "min_count": 1,
      "max_count": 1
    }
  ]
}
```

Use the parent policy to describe the curriculum's real topology. Do not configure a
single parent simply because most examples happen to have one if the source genuinely
allows multiple direct parents.

The config schema cross-validates these settings against `statement_type_policy`, so it
is usually easier to finalize the vocabulary first and hierarchy second.

## 7. Configure grade or stage mapping deliberately

If the source contains a grade-like dimension, configure:

- `grade_level_statement_types` — the source-facing grouping types that carry grade or
  stage context; and
- `grade_level_mapping` — mapping from canonical source values to Learning Commons grade
  values.

Mapping keys should use the canonical values from the profile rather than aliases.

If a reviewed source value intentionally has no Learning Commons equivalent, an empty
mapping target is meaningful. If the framework has no applicable grade/stage dimension,
use an explicit empty `grade_level_statement_types` list rather than inventing one.

## 8. Add curriculum-specific Academic Standards instructions last

Only after the deterministic policy is defined should you add or refine:

- `sfi_extraction_instructions`;
- `sfi_extraction_validation_instructions`;
- `sfi_dedup_instructions`;
- `sfi_has_child_instructions`; and
- `sfi_has_child_validation_instructions`.

These instructions are appropriate for source conventions that cannot be expressed
cleanly as deterministic configuration, such as distinguishing visually similar columns
or explaining a curriculum-specific relationship convention.

Avoid encoding things in prose that Python can validate directly. The producer and
checker should interpret the source; they should not be responsible for enforcing a
rule that can be made deterministic.

## 9. Configure Learning Components from the validated AS model

Learning Components are downstream of the validated Academic Standards graph. Configure
them only after the intended SFI types and `hasChild` structure are working.

### Choose LC source statement types

Set `kgs.lc.lc_source_statement_types` to the source-facing SFI types that represent
teachable standards suitable for atomic decomposition.

Examples might include:

- `Indicator`;
- `Learning Outcome`;
- `Knowledge Objective`;
- `Skills Objective`; or
- another curriculum-specific leaf/standard role.

When the field is omitted, selection falls back to leaf SFIs whose normalized type is
`Standard`. An explicit allowlist is preferable when the curriculum contains several
kinds of Standards but only some should generate LCs.

### Write generation policy around the source's decomposition conventions

Use `generation_instructions` for curriculum-specific guidance. Focus on rules such as:

- when coordinated actions are separable;
- when a phrase names one indivisible concept;
- which qualifiers must stay attached to an action;
- which source structures are context only; and
- what must never be inferred beyond the seed standard.

Use `lc_generation_validation_instructions` when the independent checker needs the same
curriculum-specific distinctions to audit producer decompositions.

Do not restate the generic LC contract unless the source genuinely requires a special
interpretation. See [Learning Components](../pipeline/learning-components.md) for the
built-in decomposition and validation behavior.

### Pick the narrowest defensible dedup scope

`lc_dedup_scope` controls where semantically equivalent LC texts may merge:

| Scope          | Use when                                                                                 |
|----------------|------------------------------------------------------------------------------------------|
| `framework`    | The same atomic skill should have one identity anywhere in the curriculum                |
| `top_ancestor` | Equivalent wording should merge only within the same root-level curricular context       |
| `parent`       | Equivalent wording should merge only among standards with the same direct parent set     |
| `none`         | LC identity must remain seed-specific except for exact duplicates within that seed scope |

Do not choose `framework` only because it creates a smaller graph. Choose it only when a
skill's identity is truly independent of curricular location.

### Tune semantic blocking only with evidence

The default LC blocking thresholds are intended to be useful across curricula. Override
`lc_dedup_blocking` only after inspecting missed or noisy candidate pairs.

For languages or morphology where the language-independent rules are insufficient,
configure `lc_dedup_language_pack` in the profile. This is deliberately data-driven so a
new language does not require a backend code change.

Use `lc_dedup_instructions` for semantic conventions that affect whether two skills mean
the same thing in this curriculum.

## 10. Run a calibration slice end to end

From the `backend` directory, run all four entry points against the calibration config:

```bash
python src/skg/entries/extract_page_ir.py <config.json>
python src/skg/entries/verify_page_ir_continuity.py <config.json>
python src/skg/entries/stitch_document_ir.py <config.json>
python src/skg/entries/create_kgs.py <config.json>
```

For an end-to-end calibration run, keep the selected verified page range contiguous and
starting at page index `0`. A non-zero slice is still useful for PageIR
extraction/verification inspection, but the current DocumentIR loader cannot stitch
that slice by itself. To test a middle section end to end, use a separate cropped test
PDF whose first page is index `0`. See
[Run, Resume, and Debug](running-and-debugging.md#page-ranges-and-calibration-runs).

Do not tune the final graph first. Find the **earliest stage where the representation
becomes wrong** and fix the profile or source interpretation there.

A useful review order is:

| Question                                                       | Start with                                                            |
|----------------------------------------------------------------|-----------------------------------------------------------------------|
| Does each page reflect what is visibly present?                | `extraction/page_irs/*.json`                                          |
| Are cross-page continuations correct?                          | verification pair reports and verified PageIRs                        |
| Is the document reconstructed correctly?                       | `stitching/document_ir.json` and `stitch_report.json`                 |
| Are the right standards extracted with the right types/scopes? | SFI extraction results and `sfi_candidate_registry.json`              |
| Are true duplicates reconciled conservatively?                 | SFI merge report/groups/conflicts                                     |
| Is the AS hierarchy correct?                                   | `has_child_edges_final.json` and parent-resolution artifacts          |
| Did the AS graph validate?                                     | `as_validation_report.json`                                           |
| Are the intended standards eligible for LCs?                   | `lc_eligibility_report.json`                                          |
| Is decomposition at the right granularity?                     | LC requests, producer drafts, validator verdicts, and final responses |
| Are LC duplicates nominated and adjudicated correctly?         | `lc_dedup_candidate_pairs.jsonl`, verdicts, and groups                |
| Does the final combined graph reconcile?                       | `lc_generation_summary.json` and `as_lc_kg_bundle.json`               |

For the complete artifact map and trust boundaries, see the
[Pipeline Overview](../pipeline/index.md).

## 11. Expand coverage before the full run

A successful five-page slice proves only that those five pages work. Before launching the
entire PDF, test each materially different source layout.

At minimum, sample:

- every major table schema;
- each grade/stage section if formatting changes;
- front matter versus standards-bearing sections;
- coded and uncoded standards, when both exist;
- multilingual layouts, when present; and
- several LC decomposition patterns.

Use failures to refine the smallest relevant piece of configuration. Avoid adding a
broad instruction because one unusual page failed if a bounded table/section rule can
represent the source more precisely.

## 12. Run the full document with a clean output root

Once calibration is stable:

1. copy the calibrated profile to its final config if you used a temporary one;
2. restore `start_page` / `end_page` to the intended full-document range;
3. point `output_dir` to the final run location rather than the calibration output;
4. verify the framework metadata under `kgs.metadata`; and
5. run the four pipeline entry points in order.

Treat the full run as another review step rather than assuming calibration guarantees
success. Corpus-wide deduplication, repeated codes, long-range hierarchy patterns, and
rare source layouts can surface issues that do not appear in a small slice.

## Framework metadata checklist

Before publishing or handing off a final graph, verify that `kgs.metadata` accurately
describes the represented framework:

- `framework_title`;
- `author` and `provider`;
- `country` and `jurisdiction`;
- `subject`;
- `grades_or_stages`;
- `languages` and `primary_language`;
- `license` and `attribution_statement`;
- `adoption_status`, when known; and
- `is_current`.

These are framework-level assertions. Do not infer legal/licensing or adoption status
from the pipeline output.

## Common profile-design mistakes

### Tuning prompts before defining the source model

If the statement taxonomy, identity scope, or parent policy is wrong, increasingly
specific prompt instructions usually make the profile more brittle rather than fixing
the underlying model.

### Treating every heading as a Standard Grouping

Only model headings that participate in the curriculum's standards identity or
hierarchy. Administrative section titles and explanatory headings can remain DocumentIR
context.

### Using table rules to repair extraction

KG table inclusion/exclusion decides **which reconstructed tables are semantically
eligible**. It should not compensate for incorrect PageIR geometry or broken DocumentIR
stitching.

### Making codes globally unique when they are not

If a code repeats by grade, strand, or unit, represent that with code scope instead of
silently merging distinct standards.

### Over-scoping identity

Adding every ancestor to identity prevents legitimate reconciliation. Include only the
source dimensions required to distinguish curricular items.

### Generating LCs from grouping nodes

Choose source types that express teachable standards. Grade, strand, unit, and similar
organizers usually provide context rather than LC seeds.

### Overfitting semantic dedup thresholds

Tune blocking thresholds against observed candidate-pair behavior across several parts
of the curriculum. One surprising pair is not enough evidence for a corpus-wide change.

## Where to go next

- [Local Setup](../development/local-setup.md) — install and run the project locally.
- [Architecture](../architecture.md) — understand stage boundaries, provenance, and
  deterministic invariants.
- [Pipeline Overview](../pipeline/index.md) — inspect every stage and its artifacts.
- [Run, Resume, and Debug](running-and-debugging.md) — recover interrupted runs,
  choose overwrite behavior, and trace failures to the earliest wrong artifact.
- [Academic Standards](../pipeline/academic-standards.md) — detailed AS construction
  behavior.
- [Learning Components](../pipeline/learning-components.md) — detailed LC generation,
  validation, deduplication, and export behavior.
