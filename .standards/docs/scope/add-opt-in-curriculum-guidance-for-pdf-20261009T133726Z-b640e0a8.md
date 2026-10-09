<!-- STANDARDS
Artifact: SCOPE
Cycle: add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8
-->

# Opt-in Curriculum Guidance for Page IR Extraction and Verification

## Goal

The page IR stages handle the South Africa CAPS English Home Language R-3 PDF
(Funda Wande) badly. Trial runs showed that 12 of 13 Grade 3 continuation
pages, and all 7 pages that open with an ASSESSMENT band, came out as loose
blocks, so stitching never joined them to the table they continue. Two of eight
breaks into a new "REQUIREMENTS PER TERM" table were merged with the previous
table (PDF 110->111 and 123->124). The extraction checker's correction on PDF 43
added 8 words that are not on the page. No content segment carries a Term in
`section_path`, and in Grade 3 the "3.4 GRADE 3" heading drops out of
`section_path` at PDF 122.

The pipeline's rule is that curriculum-specific policy lives in runtime config,
not in backend branches. This cycle adds optional, generic instruction fields
that let a config steer the extraction and continuity agents, plus a generic
guard that stops the extraction checker from inventing text. It then uses only
the CAPS runtime config to fix the CAPS problems, and confirms the fix by
rerunning the trial ranges.

## Constraints

- No backend source or test code added or changed in this cycle may name CAPS,
  Funda Wande, South Africa, any other curriculum, or curriculum-specific text
  such as "REQUIREMENTS PER TERM". Curriculum details go only in runtime config.
- With the new instruction fields unset, every agent prompt stays exactly as it
  is today.
- The Ghana, India, Nigeria and Rwanda example configs are not edited and keep
  today's behavior. The one accepted exception is the always-on guard in work
  item 3, which may block a checker correction that adds words missing from the
  page (user decision, 2026-10-09).
- New unset fields may appear in run metadata files such as
  `extraction_run.json` and `verification_run.json`, because those files save
  the whole stage config.
- Each of the four agents (extraction, extraction checker, continuity verifier,
  continuity checker) gets its own instruction field.
- The new fields follow the existing `kgs.lc.lc_dedup_instructions` pattern
  (`backend/src/kgfeg/schemas.py:2495`, `backend/src/kgfeg/kgs/prompts.py:901`),
  as the request specifies.
- Run configs reject unknown keys (`BaseSchema`, `extra="forbid"`); that must
  stay true.
- Trial data, PDFs and rerun outputs are local and git-ignored. They are not
  committed.
- Live pipeline runs call paid LLM APIs. Only the validation runs in work item 7
  are in scope.

## Non-goals

- Changing Document IR stitching, including letting a Table stitch to a Block,
  or changing how `section_path` is built or truncated.
- Changing KG construction code. Fix 4 is a config change only.
- Running the KG step (`create_kgs`) as part of validation. Fix 4 and the CAPS
  `kgs` block are checked by config review and config loading (user decision,
  2026-10-09).
- Editing the Ghana, India, Nigeria or Rwanda configs.
- Rewording the generic extraction or verification prompts beyond adding the
  optional instruction blocks.
- Adding OCR or otherwise repairing missing or garbled PDF text layers.
- Committing trial or rerun outputs.

## Work

### 1. Curriculum instructions for page IR extraction

**Intent:** Let a runtime config give the extraction agent and its checker
curriculum-specific rules. The checker needs them too: when it fails a page,
its corrected PageIR replaces the extraction
(`backend/src/kgfeg/page_ir_extraction/llm.py:328`).

**Done when:**

- `AC-001`: The page IR extraction config accepts two optional instruction
  fields, one for the extraction agent and one for its checker. Both default to
  unset, and existing configs that omit them still load.
- `AC-002`: When the extraction agent's field is set, its text appears in that
  agent's prompt together with an explicit rule that it overrides the generic
  extraction rules where they conflict. When the checker's field is set, the
  same holds for the checker's prompt. Each field's text reaches only its own
  agent.
- `AC-003`: With both fields unset, the extraction and checker prompts are
  identical to the prompts produced before this cycle.

**Depends on:** None.

### 2. Curriculum instructions for page IR continuity verification

**Intent:** Let a runtime config give the continuity verifier and its checker
curriculum-specific rules, strong enough to override the generic table-to-table
rule (`backend/src/kgfeg/page_ir_verification/prompts.py:273-286`) that treats
skill-area changes inside a grid as continuations. The checker needs them too:
its corrected verdict replaces the verifier's
(`backend/src/kgfeg/page_ir_verification/llm.py:389`).

**Done when:**

- `AC-004`: The page IR verification config accepts two optional instruction
  fields, one for the continuity verifier and one for its checker. Both default
  to unset, and existing configs that omit them still load.
- `AC-005`: When the verifier's field is set, its text appears in the
  verifier's prompt with an explicit rule that it overrides the generic rules
  where they conflict, and that override explicitly includes the generic
  table-to-table continuation rule. When the checker's field is set, the same
  holds for the checker's prompt. Each field's text reaches only its own agent.
- `AC-006`: With both fields unset, the verifier and checker prompts are
  identical to the prompts produced before this cycle.

**Depends on:** None.

### 3. Guard against hallucinated extraction corrections

**Intent:** Stop the extraction checker from replacing a page with text that is
not on the page, as happened on PDF 43. The guard is generic and always on, for
every config, with no config switch (user decision, 2026-10-09).

**Done when:**

- `AC-007`: When a page has a usable PDF text layer and the checker's corrected
  PageIR contains content words that are not in that text layer, the correction
  is not accepted. The page keeps the extraction agent's PageIR (user decision,
  2026-10-09), and a warning is logged that identifies the page and the missing
  words.
- `AC-008`: The guard does not block a correction because of differences in
  display case, curly versus straight quotes, or running header text.
- `AC-009`: When a page's text layer is missing or unusable (for example,
  garbled), the guard does not block the correction; the correction is handled
  as it is today.
- `AC-010`: A correction that adds no content words missing from the text
  layer is accepted as it is today.
- `AC-011`: The guard applies to every config, including configs with
  `use_extracted_hints` turned off, and running it does not change any agent's
  prompt.

**Depends on:** None.

### 4. Verified page IR loader accepts ranges that do not start at 0

**Intent:** Verification writes only the configured page range, so verified
page indexes can start above 0. Keep the baseline change in
`load_page_irs_from_verification`
(`backend/src/kgfeg/page_ir_verification/utils.py:541-542`) and make its
documentation and tests match.

**Done when:**

- `AC-012`: `load_page_irs_from_verification` accepts a gap-free page index
  sequence that starts above 0 and rejects a sequence with a gap. Its docstring
  and its error message describe a gap-free sequence and no longer say the
  sequence must start at 0.
- `AC-013`: Automated tests show that a gap-free range starting above 0 loads
  and that a range with a gap raises an error.

**Depends on:** None.

### 5. CAPS runtime config

**Intent:** Apply the new fields to the CAPS English Home Language R-3 PDF
through `examples/funda_wande/config_english_curriculum.json` alone, and
replace that config's `kgs` block, which is still a copy of Ghana English's.

**Done when:**

- `AC-014`: The CAPS config's `kgs` block describes South Africa CAPS English
  Home Language, Grades R-3, in its metadata, framework, grade and instruction
  fields, and contains no Ghana-specific content. The full config loads as a
  valid run config.
- `AC-015`: The CAPS config's `kgs.as.sfi_extraction_instructions` states that
  Term and skill area come from each table's header rows.
- `AC-016`: The CAPS config sets the extraction instruction fields for both the
  extraction agent and its checker. Together they say that a bordered box
  without the REQUIREMENTS PER TERM banner continues the previous page's table
  as rows, including pages that open with an ASSESSMENT band; that the banner
  rows (Grade, Term, skill area) are header rows; and that a continuation keeps
  the parent table's column count.
- `AC-017`: The CAPS config sets the verification instruction fields for both
  the continuity verifier and its checker. Together they say that a table whose
  top rows contain REQUIREMENTS PER TERM starts a new table even when its
  columns match the previous table, and that the phrase appears on exactly the
  48 banner pages, PDF 36-133, and nowhere else.

**Depends on:** Work items 1 and 2 (the fields must exist).

### 6. Compatibility with existing configs and code standards

**Intent:** Prove the change is generic and leaves existing pipelines as they
were.

**Done when:**

- `AC-018`: No backend source or test code added or changed in this cycle
  names CAPS, Funda Wande, South Africa, any other curriculum, or
  curriculum-specific text such as "REQUIREMENTS PER TERM".
- `AC-019`: The Ghana, India, Nigeria and Rwanda example configs are unchanged,
  still load, and produce the same prompts for all four page IR agents as they
  did before this cycle.
- `AC-020`: The existing automated test suite and the repository's lint checks
  pass with the changes in place.

**Depends on:** Work items 1-4.

### 7. Validation reruns on the CAPS PDF

**Intent:** Show on real pages that the CAPS config and the guard fix the
defects found in the trial runs. Validation must show the named defects fixed,
not only record a comparison (user decision, 2026-10-09).

**Done when:**

- `AC-021`: Extraction, verification and stitching are rerun with the updated
  CAPS config over the ranges of the three trial runs
  (`results/funda_wande_trial_p13_15/`, `results/funda_wande_trial_p28_55/`,
  `results/funda_wande_trial_p110_135/`) and over PDF 60-66 (`start_page` 59,
  `end_page` 66). A before/after comparison against the trial results is
  recorded for each finding in the Goal; PDF 60-66 has no trial baseline and is
  reported on its own.
- `AC-022`: In the reruns, every page break into a new REQUIREMENTS PER TERM
  table starts a new table, including PDF 110->111 and 123->124.
- `AC-023`: In the reruns, bordered continuation pages without the banner, and
  pages that open with an ASSESSMENT band, are extracted as table rows that
  continue the previous page's table, and stitching joins them to that table.
- `AC-024`: In the reruns, no accepted checker correction adds content words
  that are missing from the page's usable text layer, and any correction the
  guard blocks is logged.
- `AC-025`: In the stitched Grade 3 reruns, the Grade 3 heading stays in
  `section_path` on every Grade 3 content segment, including at and after
  PDF 122.

**Depends on:** Work items 1, 2, 3 and 5.

## Assumptions

- The audit described the fix-5 loader change as uncommitted and the CAPS
  config as untracked. Both were committed in `a34c4e6` after the audit; the
  content is the same, so this does not change the scope.
- Exactly what counts as a "content word", and how running header text is
  recognized, are design decisions for Architect within AC-007 and AC-008.
- The trial runs under `results/` stay available locally as the "before" side
  of the comparison in AC-021.
- AC-021 to AC-025 judge LLM output. If a rerun misses one of them, the cause
  is routed to its owner (for example, config wording or the guard) rather
  than the condition being waived.
