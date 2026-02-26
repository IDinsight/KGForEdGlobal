# Implementation Plan: `llm_atomic_skills` Learning Component Policy

This plan aligns with the current pipeline conventions:

- Reuse existing CreateKGConfig knobs (`model`, `always_double_check_first_attempt`, `lc_max_splits_per_standard`) instead of introducing duplicates.
- Treat **aux statements** as the main “notes” source (because exported expectation SFIs currently set `notes=None` by design).
- Maintain determinism by sorting LLM skills per SFI before assigning `split_index`-based IDs (LearningComponent IDs should already include `split_index` in the current code).

---

## 1. Problem Statement

Current LearningComponent generation policies (`1_to_1`, `split_bullets`) create LearningComponents that are often too coarse for reuse and alignment, because they mirror curriculum text verbatim.

**Goal:** Add a third LearningComponent policy, `llm_atomic_skills`, that decomposes each **expectation** (normative standard/outcome/competence) into 1–N **atomic skill statements**, while preserving:

- LearningComponent KG “shape”: `LearningComponent -[:supports]-> StandardsFrameworkItem`
- Deterministic IDs across reruns
- Strong provenance and traceability to the source SFI
- Graceful fallback to `1_to_1` if LLM decomposition fails

---

## 2. Key Constraints in Current Codebase

### 2.1 Where LearningComponent text comes from today
In `export_learning_components.py`, the LearningComponent “source text” is built from the SFI’s `description`, and the deterministic ID seed prefers `sfi.metadata["normalized_text"]` when present.

### 2.2 Expectation SFIs do not carry `notes`
Academic Standards exporter sets `notes=None` for SFIs. So “descriptor/guidance” context usually lives in `sfi.metadata["aux_statements"]` (and related metadata), not in `sfi.notes`.

**Implication:** “include notes” must mean include **aux statements/context metadata** (optionally), not `notes`.

### 2.3 Deterministic IDs currently include `split_index`
LearningComponent IDs currently include a numeric split index `i`, plus a `stable_text_hash(id_part)`. For `llm_atomic_skills`, the list order of atomic skills must be deterministic.

---

## 3. Intended Output Shape

For each expectation SFI:

- Create one or more LearningComponents with:
  - `LearningComponent.description`: atomic skill description (display-language policy)
  - `LearningComponent.metadata["skill_label"]`: short English stable label
  - `LearningComponent.metadata["split_policy"] = "llm_atomic_skills"`
  - `LearningComponent.metadata["supporting_sfi_case_uuid"]`: SFI UUID for traceability

- Create exactly one supports edge per LearningComponent:
  - `(:LearningComponent)-[:supports]->(:StandardsFrameworkItem)`

---

## 4. Data Model Additions (`schemas.py`)

Add these Pydantic models to `skg/kgs/schemas.py`:

- `AtomicSkill`
  - `skill_label: str` (English-only stable key, tight format constraints)
  - `description: str` (atomic skill description; language matches LearningComponent display policy)
  - `rationale: Optional[str]` (short explanation; optional but recommended)
- `SFIAtomicSkills`
  - `sfi_uuid: UUID`
  - `skills: list[AtomicSkill]`
- `AtomicSkillsResponse`
  - `items: list[SFIAtomicSkills]`

**Why per-SFI lists?** We’ll batch multiple SFIs per call and need stable mapping from response → SFI.

---

## 5. Config Updates (`run_config_schemas.py`)

### 5.1 Extend policy enum
Update `CreateKGConfig.learning_component_policy` to include `"llm_atomic_skills"`.

### 5.2 Reuse existing knobs instead of adding duplicates
Do **not** add new `model` or `always_double_check_first_attempt` fields—those already exist and are used by current LLM wrappers.

Also reuse `lc_max_splits_per_standard` as the hard cap on atomic skills per SFI.

### 5.3 Minimal new LearningComponent-LLM config fields
Add only what we need:

- `lc_atomic_skills_batch_size: int = 5`
  - Number of expectation SFIs per LLM call
- `lc_atomic_skills_min_per_sfi: int = 1`
  - Enforced by validator; if LLM returns 0 skills, trigger correction/fallback
- `lc_atomic_skills_include_aux_statements: bool = True`
  - Include `sfi.metadata["aux_statements"]` content as extra context
- `lc_atomic_skills_include_topic_context: bool = True`
  - Include `progression_context.topic_path_parts` / grade info when present
- `lc_atomic_skills_require_rationale: bool = True`
  - Validator requires `rationale` for each skill

---

## 6. Prompting (`prompts.py`)

### 6.1 Add prompt builders
Add:
- `decompose_atomic_skills(...) -> PromptPair`
- `double_check_atomic_skills(...) -> PromptPair`

Use the same PromptPair style as current learning progressions prompts.

### 6.2 Input payload per SFI
Each SFI payload should include both:

1. Stable source text for traceability:
   - `id_source_text`: `sfi.metadata["normalized_text"]` (preferred) else `sfi.description`
2. Display text for LearningComponent descriptions:
   - `display_text`: `sfi.description` (already follows `description_text_policy` upstream)

Plus optional context:
- `statement_code` (if present)
- `grade_level` and/or `progression_context` (topic path)
- `aux_statements` (if enabled), reduced to short bullets with roles

### 6.3 Output rules (tight)
Require:
- JSON that matches `AtomicSkillsResponse`
- For each SFI: `skills` length 1..`lc_max_splits_per_standard`
- `skill_label`: English-only, short, stable, and format-constrained (pick one: snake_case OR Title Case)
- `description`: atomic skill statement (not activity/resource)
- No duplicates within an SFI

Explicitly forbid:
- “single paraphrase of the whole standard”
- teacher activities as skills
- injecting prerequisites not supported by the input

---

## 7. Validation (`validators.py`)

Add `validate_atomic_skills(parsed: AtomicSkillsResponse, *, allowed_sfi_uuids: set[UUID], min_per_sfi: int, max_per_sfi: int, require_rationale: bool) -> None`

Checks:
1. Every returned `sfi_uuid` is allowed for this batch
2. No missing SFIs (or: allow missing and fallback those; correction tends to be higher quality)
3. `skills` length in [min_per_sfi, max_per_sfi]
4. `skill_label`: non-empty, English-only heuristic, unique per SFI
5. `description`: non-empty, actionable (soft heuristic)
6. If `require_rationale`: each skill has rationale

Raise `QualityError` to trigger the correction loop.

---

## 8. LLM Wrapper (`llm.py`)

### 8.1 Add a new inference entry point
Add `infer_atomic_skills(...) -> AtomicSkillsResponse`:

- Use `OpenAI().responses.parse(..., text_format=AtomicSkillsResponse)`
- Reuse the retry/correction pattern you already use for learning progressions:
  - attempt 0 parse + validate
  - on `QualityError`, call `double_check_atomic_skills(...)` with failed content
  - honor `always_double_check_first_attempt`

**Keep it parallel to the learning progressions wrapper**, even if it duplicates some logic.

---

## 9. Export Integration (`export_learning_components.py`)

### 9.1 Add an LLM-based export path
Right now LearningComponent creation is per-SFI in `_create_lcs_for_expectation(...)`. For LLM batching, add:

- `_export_lcs_via_llm(...) -> tuple[list[LearningComponent], list[Relationship], dict[str, Any], dict[str, Any]]`

Return: LCs, supports relationships, lc_stats, optional debug payload.

### 9.2 LLM path steps
1. Gather expectation SFIs.
2. Sort deterministically (e.g., by `case_identifier_uuid`) before batching.
3. Build SFI payload objects (including optional aux/context).
4. Batch into `config.lc_atomic_skills_batch_size`.
5. For each batch:
   - call `infer_atomic_skills(...)` with validator configured for that batch’s UUIDs
   - if fails after correction loop: fallback those SFIs to `1_to_1`

### 9.3 Deterministic skill ordering
For each SFI’s returned skills:
- Compute `split_hash = stable_text_hash(description)` for each skill
- Sort by `(split_hash, normalized_skill_label)` where `normalized_skill_label = skill_label.strip().lower()`
- Truncate to `config.lc_max_splits_per_standard`

**Why `split_hash` as primary sort key?** `skill_label` is LLM-generated and can vary cosmetically across reruns (casing, synonyms, reordering) even with identical inputs. `description` carries the actual skill content and is more stable, so hashing it produces a more consistent ordering. Using `normalized_skill_label` as a tiebreaker keeps the order human-readable when hashes collide. This keeps the `split_index` in IDs as stable as possible across reruns.

### 9.4 LearningComponent ID seed and metadata
Match existing ID style:

- `policy = "llm_atomic_skills"`
- `id_part = skill_label` (preferred) OR `stable_text_hash(skill_label + "|" + description)` to reduce collisions
- `split_hash = stable_text_hash(id_part)`
- `lc_id_text = f"lc:curriculum:{doc_key}:lc:{policy}:{sfi_uuid}:{i}:{split_hash}"`

Metadata should include:
- `split_policy`
- `split_id_text` (skill_label)
- `split_display_text` (description)
- `skill_label`
- `supporting_sfi_case_uuid`
- `canonical_node_id` (copy from SFI when available)
- `provenance` (copy/merge from SFI provenance the same way you already do)
- optional: `llm_rationale`, `llm_model`

### 9.5 Debug artifact
Write:
- `learning_components_llm_atomic_skills_debug.json`

Include:
- per-batch input SFIs (uuid + code + display text)
- parsed response (as dict)
- fallback list (uuids)
- validation errors captured (messages)

---

## 10. Reporting Impact (`reporting.py`)

No required reporting changes.

As long as LearningComponent export sets:
- `lc_stats["split_policy"] = "llm_atomic_skills"`
- `lc_stats["max_splits_observed"]`
- `lc_stats["splits_distribution"]`

…reporting will continue to function unchanged.

---

## 11. File-by-File Change Summary (Revised)

| File | Required Changes                                                      | Notes                                                                                      |
|------|-----------------------------------------------------------------------|--------------------------------------------------------------------------------------------|
| `schemas.py` | Add `AtomicSkill`, `SFIAtomicSkills`, `AtomicSkillsResponse`          | Put alongside existing LLM response models.                                                |
| `run_config_schemas.py` | Add `"llm_atomic_skills"` + small LearningComponent-LLM config fields | Reuse existing `model`, `always_double_check_first_attempt`, `lc_max_splits_per_standard`. |
| `prompts.py` | Add `decompose_atomic_skills()`, `double_check_atomic_skills()`       | Mirror existing PromptPair style.                                                          |
| `validators.py` | Add `validate_atomic_skills()`                                        | Raise `QualityError` for correction loop.                                                  |
| `llm.py` | Add `infer_atomic_skills()` + `_call_openai_api_for_atomic_skills()`  | Keep parallel to learning progressions wrapper.                                            |
| `export_learning_components.py` | Add LLM branch + batching + deterministic ordering + debug artifact   | Keep existing policies unchanged.                                                          |
| `reporting.py` | No required changes                                                   |
| `create_kgs.py` | No changes                                                            | Already calls LearningComponent export generically.                                        |

---

## 12. Suggested Implementation Order

1. `schemas.py`: add response models
2. `run_config_schemas.py`: add new policy + config fields
3. `prompts.py`: add prompt builders
4. `validators.py`: add atomic skills validator
5. `llm.py`: add atomic skills inference wrapper
6. `export_learning_components.py`: integrate policy with batching + fallback + debug write
7. Run on Senegal math (small page range first), inspect debug artifact, then scale up

---

## 13. Non-Goals (for first pass)

- Cross-SFI skill deduplication / skill graph merging
- Multi-lingual LearningComponent entities (LearningComponent schema currently holds a single description string)
- Automatically converting atomic skills into a cross-document reusable skill ontology
