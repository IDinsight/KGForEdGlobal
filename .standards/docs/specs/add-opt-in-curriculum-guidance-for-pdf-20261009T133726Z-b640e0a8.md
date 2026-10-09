<!-- STANDARDS
Artifact: ARCHITECTURE
Cycle: add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8
-->

# Technical Design: Opt-in Curriculum Guidance for Page IR Extraction and Verification

Design mode: Feature.

## Context

- Scope: `.standards/docs/scope/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
  (AC-001 to AC-025). Project context: `.standards/CONTEXT.md`.
- Curriculum policy belongs in runtime config. No backend source or test code
  may name a curriculum or carry curriculum text.
- Run config models extend `BaseSchema` (`extra="forbid"`). `ExtractionConfig`
  (`backend/src/kgfeg/schemas.py:3308`) and `VerificationConfig` (`:3449`) are
  the stage configs. Their `model_dump` is saved to `extraction_run.json` and
  `verification_run.json`, so new fields show up there automatically (allowed by
  scope).
- The pattern to follow is `kgs.lc.lc_dedup_instructions`: an `Optional[str]`
  that defaults to `None`, and `build_lc_dedup_prompt`
  (`backend/src/kgfeg/kgs/prompts.py:896-905`) appends a
  "Runtime curriculum instructions" section to the system message only when it
  is not `None`.
- Extraction (`page_ir_extraction/llm.py:extract_page_ir`) runs the extraction
  agent, then the checker (`_run_validation_agent`). A failing verdict's
  `corrected_page_ir` replaces the extraction with no further check (`:313-328`).
  The entry point (`entries/extract_page_ir.py:115`) loads the PyMuPDF page
  only when `use_extracted_hints` is true, and `extract_page_ir` treats
  "`pdf_page` is not None" as "build prompt hints". All seven example configs,
  including CAPS, set `use_extracted_hints` to true.
- Verification (`page_ir_verification/llm.py:verify_page_ir_pairs`) runs the
  verifier, then the checker. A failing checker's `corrected_verdict` replaces
  the verifier's verdict. Only three thresholds reach the LLM layer today, as
  keyword arguments from `_execute_verification_attempts`
  (`verify_page_pairs.py:290`, call at `:351`), which holds the
  `VerificationConfig`.
- CAPS PDF facts (from context and the trial runs): every banner page has
  "REQUIREMENTS PER TERM" in its usable text layer. Banner tables put Grade, Term
  and skill area in their top rows. Skill areas are Listening and Speaking
  (Oral), Emergent Reading and Emergent Writing (Grade R), and Reading and
  Phonics and Writing (Grades 1-3). Running headers are "ENGLISH HOME LANGUAGE
  GRADES R-3" and "CURRICULUM AND ASSESSMENT POLICY STATEMENT (CAPS)". The
  document has no item codes. Stitched `columns_signature` values carry the
  banner text. KG table selection matches `included_table_section_patterns` with
  a regex search over text that includes the table's `columns_signature`
  (`kgs/utils.py:211-265`, `:1480-1500`).
- The KG step is not run in this cycle. The CAPS `kgs` block is checked only by
  review and by config loading.

## Decision

1. **Four optional instruction fields, one per agent**, typed
   `Optional[str] = None` like `lc_dedup_instructions`:
   - `ExtractionConfig.extraction_instructions` goes to the extraction agent.
   - `ExtractionConfig.validation_instructions` goes to the extraction checker.
   - `VerificationConfig.verification_instructions` goes to the continuity
     verifier.
   - `VerificationConfig.validation_instructions` goes to the continuity
     checker.

   The stage nesting names each field's stage, and "validation" matches what the
   code already calls the checker agents. A field validator strips the value and
   turns a blank or whitespace-only string into `None`. This differs on purpose
   from `lc_dedup_instructions`, which emits an empty section for `""`. With
   this rule a blank config value can never add an empty override section.
2. **Values arrive as keyword arguments, not config objects**, matching how the
   thresholds already travel. Each prompt function gets one new keyword
   parameter, `curriculum_instructions: str | None = None`. Each orchestration
   function in between gets explicit keyword parameters that default to `None`,
   so existing callers and tests keep working unchanged.
3. **Injection point and wording.** When a value is not `None`, a section headed
   `## RUNTIME CURRICULUM INSTRUCTIONS` is appended at the very end of that
   agent's **system message**, after every generic section. The user message is
   left alone. The section must say that the text is authoritative
   curriculum-specific guidance for this document, and that where it conflicts
   with the generic rules above, the agent follows it, unless that would break
   the output contract (the output schema, and for extraction the structural
   rules that the Python quality validators enforce). The two verification
   sections must also name, as overridable, the TABLE continuation decision
   procedure (section B, Steps 1-4, including the sentence saying that content
   differences inside the grid do not override the structural conclusion) and
   the TABLE<->TABLE exception in the uncertainty policy, which only the
   verifier's prompt has. The configured text follows that framing verbatim.
   With a value of `None`, the prompt-building code produces exactly today's
   string: no new separator, whitespace or heading. Developer chooses the exact
   framing wording.
4. **Generic correction guard**, deterministic, with no LLM call, no prompt
   change and no config switch of its own. It runs in `extract_page_ir` only
   when hints are enabled and the checker returned `passed=false` with a
   `corrected_page_ir`.
   - **Gating on `use_extracted_hints` (AC-027).** Keep today's contract: the
     entry point passes `pdf_page` only when `config.use_extracted_hints` is
     true (`entries/extract_page_ir.py:115`), so inside `extract_page_ir`
     "`pdf_page is not None`" means "hints on". The guard runs only on that
     path. When `pdf_page` is `None`, a failing verdict's correction is returned
     exactly as today. No new parameter or entry-point change is needed.
   - **Text layer source.** The guard reuses the text hint that
     `extract_page_text_layer_hints` already computes for the prompt
     (`PageTextLayerHints.text_hint`): the raw `page.get_text("text")` that
     passed the existing quality gate (at least 20 characters, printable ratio
     at least 0.90, U+FFFD ratio at most 0.02;
     `page_ir_extraction/utils.py:176-207`). The text layer is not read a second
     time, and a `None` text hint means "no usable layer".
   - **Normalization**, applied the same way to every compared text: Unicode
     NFKC (this also expands ligatures); remove soft hyphens (U+00AD); map curly
     single and double quotes and primes to their straight forms; casefold.
   - **Content word.** Split the normalized text into maximal runs of Unicode
     letters and digits (`[^\W_]+`). A content word is a run of at least 2
     characters that contains at least one letter. Pure numbers and single
     characters are not content words. Splitting on punctuation makes quote and
     apostrophe style irrelevant, and casefolding makes display case irrelevant.
   - **Scanned fields.** Scan only text transcribed from the page: `Block.text`,
     each `ListItem.text`, `FigureUnit.caption`, and every `TableCell.text`.
     Skip `ARTIFACT` blocks entirely, since those are running headers, footers
     and page numbers. Skip `figure.alt_text` (a description, not page text),
     `figure.embedded_text` (often raster text with no text layer), `local_code`,
     list `marker`, and `text_en`.
   - **Occurrence counts (AC-029).** The guard compares how many times each
     content word occurs, not just whether it occurs. Build three counts per
     normalized content word: `C(w)` over the corrected PageIR, `E(w)` over the
     extraction agent's PageIR (both scanning the same fields), and `L(w)` over
     the text layer. `L` is the element-wise maximum (`Counter | Counter`) of
     the counts from the raw text layer and from the text layer with each
     line-break hyphen (`-` followed by a newline) removed. A word split across
     a line therefore counts as both its joined form and its pieces, and no word
     is counted twice.
   - **Added words** = every `w` with `C(w) > L(w)` and `C(w) > E(w)`. A word
     missing from both the text layer and the extraction is the case where
     `L(w) = E(w) = 0`. Comparing against `E` means occurrences the correction
     merely keeps from the extraction, such as raster-only text, never block it.
     Comparing against `L` catches on-page text repeated more often than the
     page has it, which is the PDF 43 case. The text layer also contains running
     headers that the PageIR scans skip, which can only raise `L` and so only
     makes the guard more lenient.
   - **Evidence.** A read-only prototype of this rule over the saved trial
     artifacts for PDF 28-55 flags only PDF 43, the only page there whose final
     PageIR differs from the extraction. It reports exactly 8 over-counted
     words: assessment, suggestions, for, informal, oral, or, practical,
     observation. The other trial ranges have no saved extraction outputs to
     compare against.
   - **Usable text layer.** The layer is usable when it passes the quality gate
     and, if the extraction agent's PageIR has at least 10 distinct content
     words, at least 50% of those words appear in the text layer. The coverage
     check uses distinct words (sets), not counts. It catches garbled layers
     (for example, broken font encodings) that pass
     the printable-character gate. A real text layer covers nearly all of the
     extracted words, and a garbled one covers almost none, so 50% sits safely
     between the two.
   - **Outcome.** If the layer is usable and there are added words, reject the
     correction and return the extraction agent's PageIR. Log `logger.warning`
     naming the 1-based page number, the number of added words, and the sorted
     added words. Showing each word's correction and text-layer counts is
     optional. This goes to loguru's default terminal output; no
     log file or persisted record is added. Otherwise, return the correction as
     today. When no usable layer exists, an info-level log may say the guard was
     skipped. The guard never raises.
5. **Verified page IR loader (fix 5).** Keep the committed logic in
   `load_page_irs_from_verification` (`page_ir_verification/utils.py:541-542`).
   Update the NB note, the Raises entry and the error message so they describe a
   gap-free (contiguous) `page_index` sequence that may start at any index, with
   no claim that it must start at 0.
6. **CAPS config only.** All CAPS behavior lives in
   `examples/funda_wande/config_english_curriculum.json`. That file's
   committed config holds the Grade R run (AC-034): `start_page` 35 and
   `end_page` 59 in both `page_ir_extraction` and `page_ir_verification`.
   Its `page_ir_extraction.output_dir` changes to a new Grade R rerun
   directory under `results/` (for example
   `<repo>/results/funda_wande_grade_r`), keeping the existing absolute-path
   convention. This is needed because extraction skips any page IR that already
   exists (`overwrite` false). The current value,
   `results/funda_wande_trial_p28_55`, already holds trial page IRs for page
   indexes 35-54, so a run into it would reuse them instead of re-extracting.
   The other example configs are not touched.

## Acceptance Coverage

- `AC-001`: Decision 1. Two `Optional[str]` fields on `ExtractionConfig`,
  defaulting to `None`. Configs that omit them load because the defaults apply.
- `AC-002`: Decisions 2 and 3. `extraction_instructions` goes only to
  `extract_page_ir_from_pdf_page`, and `validation_instructions` goes only to
  `validate_page_ir_extraction`. Each is appended with the override framing.
- `AC-003`: Decision 3. The `None` path is byte-identical to today's output.
- `AC-004`: Decision 1. Two `Optional[str]` fields on `VerificationConfig`,
  defaulting to `None`.
- `AC-005`: Decisions 2 and 3. `verification_instructions` goes only to
  `verify_page_ir_pairs_from_extraction`, and `validation_instructions` goes
  only to `validate_page_ir_continuity_verdict`. The framing explicitly names
  the table-to-table decision procedure as overridable.
- `AC-006`: Decision 3, as for AC-003.
- `AC-029`, `AC-030`: Decision 4, the occurrence-count added-words rule and its
  outcome, with the warning on terminal output only.
- `AC-032`: Decision 4 plus the replay contract under **Interfaces and
  Contracts**. The prototype evidence in Decision 4 shows the rule blocks that
  correction.
- `AC-008`: Decision 4. Casefolding handles display case. Splitting on
  punctuation plus quote mapping handles curly versus straight quotes. Skipping
  `ARTIFACT` blocks handles running headers.
- `AC-009`: Decision 4, usable-text-layer rule (quality gate plus coverage
  check, and a missing page or failed read counts as unusable).
- `AC-027`: Decision 4, gating on `use_extracted_hints`. The guard runs only
  on the `pdf_page is not None` path, after both agents, and changes no prompt
  argument.
- `AC-012`: Decision 5.
- `AC-013`: No architectural impact. The tests are Developer or Tester work
  against the Decision 5 contract.
- `AC-028`: CAPS `kgs` block contract: `grade_level_mapping` maps Grade R to
  [K]; the Grade R label itself is unchanged.
- `AC-014`, `AC-015`: Decision 6 and the CAPS `kgs` block contract under
  **Interfaces and Contracts**.
- `AC-016`, `AC-017`: Decision 6 and the CAPS page-stage instruction contract
  under **Interfaces and Contracts**.
- `AC-018`: Constraint on all backend changes. The guard, the fields and the
  framing text are curriculum-neutral. Curriculum text appears only in the CAPS
  config.
- `AC-019`: Decisions 1 to 3. The four other configs leave every field unset,
  so all four prompts are unchanged. The guard changes no prompt.
- `AC-020`: No architectural impact. Satisfied by implementation and checked by
  the existing `make test` and `make lint`.
- `AC-034`: Decision 6, the committed Grade R range and its fresh output
  directory.
- `AC-033`, `AC-022`, `AC-023`, `AC-025`, `AC-031`: Validation reruns under
  **Build Plan** step 5. AC-022
  depends on the verification instructions (Decision 3, CAPS contract). AC-023
  depends on the extraction instructions, since stitching only joins tables to
  tables. AC-031 depends on Decision 4. AC-025 has no code change of its own:
  it depends on AC-023, because continuation content kept as table rows stops
  adding headings to `section_path` (non-goal: changing `section_path`).

## Components

- `kgfeg/schemas.py`: the four new fields and their blank-to-`None` validators.
- `page_ir_extraction/prompts.py`: an optional curriculum-instructions block on
  both prompt builders.
- `page_ir_extraction/llm.py`: carries both values through and, when
  `pdf_page` was supplied, calls the guard after a failing verdict.
- `page_ir_extraction/correction_guard.py` (new; Developer may instead put it in
  `utils.py`): pure functions for normalization, content-word collection,
  text-layer usability, and the accept-or-reject decision. No I/O apart from
  logging.
- `entries/extract_page_ir.py`: passes both instruction values. Its existing
  `pdf_page` gating on `use_extracted_hints` is unchanged.
- `page_ir_verification/prompts.py`: an optional block on the verifier and
  checker prompts.
- `page_ir_verification/llm.py` and `verify_page_pairs.py`: carry the two
  values from `VerificationConfig` to the prompt builders.
- `page_ir_verification/utils.py`: docstring and message updates to the loader.
- `examples/funda_wande/config_english_curriculum.json`: the CAPS values.

## Interfaces and Contracts

- **Config fields.** Each of the four fields is
  `Optional[str] = Field(default=None, description=...)`, with a
  `field_validator` that strips the value and maps `""` to `None`. The field
  descriptions must stay curriculum-neutral. Unknown keys are still rejected.
- **Prompt builders.** Each of the four prompt builders gets a new keyword
  parameter `curriculum_instructions: str | None = None`. Its output for `None`
  equals the current output exactly.
- **`extract_page_ir`** gets new keyword parameters
  `extraction_instructions: str | None = None`,
  `validation_instructions: str | None = None`. The meaning of `pdf_page` is
  unchanged (supplied only when hints are on). `_run_validation_agent` gets
  `validation_instructions: str | None = None`.
- **`verify_page_ir_pairs`** gets new keyword parameters
  `verification_instructions: str | None = None` and
  `validation_instructions: str | None = None`. Its `_run_validation_agent`
  gets `validation_instructions`. `_execute_verification_attempts` passes the
  values from `config`.
- **Guard decision.** The inputs are the extraction PageIR, the corrected
  PageIR, the gated text hint (`str | None`; `None` means no usable layer) and
  the page index. The output tells the caller whether to accept the correction, and
  supplies the sorted added words for the log. The outcome contract
  is Decision 4; the type and function names are Developer's choice. The
  coverage threshold (0.50) and the minimum word count (10) are named module
  constants.
- **PDF 43 replay (AC-032).** This is a local verification procedure, not
  committed code: the inputs are git-ignored, and AC-018 forbids backend source
  or test code from naming the curriculum. It calls the public guard decision
  function on three inputs from the trial `results/funda_wande_trial_p28_55/`
  extraction run:
  - extraction: the highest-numbered
    `page_irs_raw/0042.val00.attempt*.parsed.json` (only the extraction agent
    writes to the raw directory);
  - correction: `page_irs/0042.json` (the final PageIR, which in that trial was
    the checker's correction);
  - text hint: page index 42 of the PDF, taken through the existing text-hint
    quality gate.

  Expected result: rejected, and the added words include the 8 words above.
  The comparison is unaffected by Python-filled fields on the saved PageIRs,
  because the guard scans only transcribed text.
- **CAPS page-stage instructions** (curriculum text, config only):
  - `page_ir_extraction.extraction_instructions` and
    `page_ir_extraction.validation_instructions` each state that a bordered box
    without the "REQUIREMENTS PER TERM" banner continues the previous page's
    table as rows, including a page that opens with an ASSESSMENT band; that the
    banner rows (Grade, Term, skill area) are header rows of one table and
    count toward `header_row_count`; and that a continuation keeps the parent
    table's column count. The checker's text also tells it to correct
    extractions that break these rules. AC-016 is satisfied by the two texts
    together, but carrying all three rules in both is the design choice,
    because each checker correction replaces the extraction.
  - `page_ir_verification.verification_instructions` and
    `page_ir_verification.validation_instructions` each state that a table
    whose top rows contain "REQUIREMENTS PER TERM" starts a new table
    (`is_continuation=false`) even when its columns match the previous table,
    and that this phrase appears on exactly the 48 banner pages, PDF 36-133, and
    nowhere else. They may add that a bordered table without the banner at the
    top of page N+1 continues the previous table.
- **CAPS `kgs` block** (replaces the Ghana copy entirely; must load as part of
  `RunConfig`):
  - `metadata`: country and jurisdiction "South Africa"; author Department of
    Basic Education; framework title naming the Curriculum and Assessment Policy
    Statement (CAPS), Foundation Phase, English Home Language, Grades R-3;
    `grades_or_stages` Grade R, Grade 1, Grade 2, Grade 3; subject "English Home
    Language"; and a South African attribution statement. No Ghana, NaCCA or
    BASIC values anywhere in the block.
  - Statement-type hierarchy `Grade` > `Term` > `Skill Area` > `Skill`. The
    first three are `Standard Grouping` and `Skill` is `Standard`.
    - Grade controlled values: Grade R, Grade 1, Grade 2, Grade 3.
    - Term controlled values: Term 1 to Term 4.
    - Skill Area controlled values: Listening and Speaking (Oral), Emergent
      Reading, Emergent Writing, Reading and Phonics, Writing.
    - Each type carries aliases for the uppercase display forms.
  - Policies built on that hierarchy:
    - `identity_scope_statement_types`: Term [Grade]; Skill Area [Grade, Term];
      Skill [Grade, Term, Skill Area].
    - `sfi_has_child_parent_policy`: one parent of the next-broader type, with
      Grade attaching to the root.
    - `grade_level_statement_types`: [Grade].
    - `grade_level_mapping`: Grade R -> [K], Grade n -> [n].
  - No codes: `code_patterns` and `code_parent_rules` are empty, and Ghana's
    excluded and included signatures are removed. Tables are selected with
    `included_table_section_patterns` matching "requirements per term", which
    hits the banner tables through their `columns_signature`. Exact signatures
    are not used because the reruns will change them.
  - `sfi_extraction_instructions` (AC-015) states that Grade, Term and Skill
    Area come from each table's header rows. Body rows supply `Skill`
    candidates. ASSESSMENT rows and contact-time text are not skills.
  - The other required AS instruction fields, the LC and LP instruction fields,
    `lc_dedup_instructions`, the LC language pack, and the LP developmental
    coordinate (`Grade` ordered Grade R to Grade 3) and statement-type pairs
    (`Skill`-`Skill`) are rewritten for CAPS. Generic numeric settings may keep
    their current values.

## Data and Control Flow

Extraction, per page:

1. If `use_extracted_hints` is on, the entry point loads the PyMuPDF page and
   `extract_page_ir` builds the prompt hints from it, as today.
3. The extraction prompt, with the optional block, goes to the extraction
   agent, which returns its PageIR.
4. The checker prompt, with its optional block, goes to the checker.
5. If the checker passes, return the extraction PageIR.
6. If the checker fails and hints are on, run the guard on the text hint.
   Return the correction if the guard accepts it. Otherwise log a terminal
   warning and return the extraction PageIR. If hints are off, return the
   correction as today.

Downstream steps (boundary_state, re-validation, writing to disk) are unchanged.

Verification: `VerificationConfig` -> `_execute_verification_attempts` ->
`verify_page_ir_pairs` -> the verifier prompt (optional block) -> the checker
prompt (optional block). Selection, patching and compile steps are unchanged.

## Technical Acceptance Criteria

- `AC-003`, `AC-006`, `AC-019`: For representative inputs, each of the four
  prompt builders called with `curriculum_instructions=None` returns a
  `PromptPair` equal, as strings, to the output of the pre-cycle code (commit
  `d916660`). The four non-CAPS example configs load with all four fields set to
  `None`.
- `AC-002`, `AC-005`: With a field set, its text appears only in its own
  agent's system message, under the override heading, after all generic
  sections. No other agent's prompt contains it.
- `AC-029`, `AC-030`, `AC-008`, `AC-009`, `AC-027`: The guard decision follows
  Decision 4 exactly. With `pdf_page=None` (hints off) a failing verdict's
  correction is returned unchanged and the guard logic is not invoked. With a
  page supplied, the prompts are the same whether the guard accepts or rejects.
  The added-words test uses occurrence counts. A correction that duplicates an
  item already on the page, with no new vocabulary, is rejected. A correction
  whose counts stay within `max(L, E)` for every word is accepted.
- `AC-032`: The replay procedure above returns "rejected", with the 8 words.
- `AC-012`: A sorted, gap-free `page_index` list starting at any non-negative
  index passes. Any gap or duplicate raises `ValueError` with a message about
  contiguity, not about starting at 0.
- `AC-028`: The CAPS `grade_level_mapping` has the key for the Grade R
  canonical value mapped to `["K"]`.
- `AC-034`: The committed CAPS config has `start_page` 35 and `end_page` 59 in
  both page stages, and a `page_ir_extraction.output_dir` that differs from
  every trial-run directory.
- `AC-014`, `AC-019`: `RunConfig.model_validate` succeeds for the CAPS config
  and for the Ghana, India, Nigeria and Rwanda configs.

## Build Plan

1. Add the config fields and the prompt-builder parameters for both stages,
   with framing text. Add tests for the `None` path and the set path.
2. Thread the values through extraction (`llm.py` and the entry point) and
   verification (`verify_page_pairs.py` to `llm.py`).
3. Add the correction guard and call it on the hints path in
   `extract_page_ir`. Add unit tests for every Decision 4 branch, including
   hints off. The guard's existing set-based rule (commit `fd83fee`) becomes the
   occurrence-count rule, and its tests are updated to match.
4. Update the loader documentation and messages, and add range tests (AC-012,
   AC-013).
5. Write the CAPS config values (page-stage instructions, the `kgs` block,
   and the Grade R range and output directory), then confirm the config
   loads. Run validation as four grade runs (AC-033), each running extraction,
   verification and stitching:

   | Run     | `page_index` range | PDF pages | Trial baseline              |
   | ------- | ------------------ | --------- | --------------------------- |
   | Grade R | 35-59              | 36-59     | `trial_p28_55` (PDF 36-55)  |
   | Grade 1 | 59-84              | 60-84     | none                        |
   | Grade 2 | 84-109             | 85-109    | none                        |
   | Grade 3 | 109-135            | 110-135   | `trial_p110_135` (all)      |

   - Grade R runs from the committed config as is.
   - For each later grade, the user sets `start_page` and `end_page` in both
     page stages, and also `page_ir_extraction.output_dir` (a fresh directory
     per grade, for example `results/funda_wande_grade_1`), in the local config
     only. None of these local edits are committed. A separate output directory
     per grade is required: verification and stitching operate on the shared
     `<output_dir>/<doc_key>/` tree, and stitching loads every verified page IR
     in it without filtering by range (`document_ir/utils.py:341-440`). Grades
     sharing one directory would be stitched together.
   - Read blocked-correction warnings from the terminal output (AC-031; no log
     file).
   - Run the PDF 43 replay (AC-032). It uses the `trial_p28_55` artifacts, which
     are unaffected by the reruns.
   - Compare against the trial runs for each Goal finding where pages overlap.
     Report the pages with no baseline (PDF 56-109) on their own. The
     `trial_p13_15` run has no counterpart and is not compared.
   - If a run misses AC-022, AC-023, AC-025 or AC-031 because of config wording,
     revise the CAPS config text and rerun only the affected grade, into a fresh
     output directory or with `overwrite` set.

## Risks and Follow-up

- LLM compliance with the instructions is not guaranteed. AC-022, AC-023, AC-025 and AC-031 may
  need several rounds of config wording, and each rerun costs money.
- Guard false positives: kerning-split or ligature-garbled words in an
  otherwise usable layer could block a legitimate correction. The cost is
  bounded, since the page keeps the extraction agent's PageIR. If the reruns
  show this happening, adjust the normalization within this design rather than
  disabling the guard.
- Count rule tolerance: a correction that legitimately restores an occurrence
  the text layer lacks (for example, a word the layer dropped) and that the
  extraction also missed is blocked. The page then keeps the extraction.
- Guard blind spots: hallucinated `alt_text` or `embedded_text`, and pure
  numbers, are not checked. Neither are reorderings that keep every count. This is accepted to avoid blocking raster text.
- Blocked corrections appear only as terminal warnings, by user decision.
  Evidence for AC-031 lasts only as long as that terminal output is kept.
- Configs with `use_extracted_hints` false get no guard protection.
- The CAPS `kgs` block is not exercised by `create_kgs` in this cycle. Its
  hierarchy and table selection are unproven until a later KG run.
- Page breaks between grades (PDF 59->60, 84->85 and 109->110) are never
  verified, because each grade is its own run. Every grade starts with a
  section heading, so no table can legitimately continue across those breaks.
- Forgetting to change `output_dir` for a later grade either reuses page IRs
  already in that directory or merges grades at stitching. The run procedure
  in Build Plan step 5 guards against this.
- AC-025 depends on AC-023. If continuation content still becomes heading
  blocks, `section_path` truncation at 60 can recur. Changing `section_path` is
  a non-goal, so such a miss goes back to config wording.

## Alternatives

- Passing the whole `ExtractionConfig` or `VerificationConfig` object into
  `llm.py` and `prompts.py`. Rejected: no config object reaches these layers
  today, and keyword arguments match the existing threshold plumbing and keep
  the tests simple.
- One shared instruction field per stage. Rejected by scope: each agent gets its
  own field.
- Making the guard a checker output validator that raises `ModelRetry`.
  Rejected: scope requires blocking and keeping the extraction, and retry
  feedback would change the checker's conversation.
- Running the guard for every config, including hints off. Replaced by user
  rework (AC-027): the guard follows `use_extracted_hints`.
- Set-based comparison (word present or absent), the previous design. It
  cannot catch on-page text the correction repeats, which is the PDF 43
  failure.
- Comparing the correction with the text layer without subtracting the
  extraction's words. Rejected: it would block every correction on pages with
  raster-only text that the extraction already had (see Decision 4).
- Selecting CAPS tables by exact `columns_signature`. Rejected: the signatures
  change once the banner rows are extracted as header rows.
