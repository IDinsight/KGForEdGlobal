# KGForEdGlobal Learning Progressions Project Instructions

This repository implements a curriculum-processing pipeline that constructs **Academic Standards**, **Learning Components**, and—through the work governed here—**Learning Progressions** knowledge graphs using the Learning Commons ontology with curriculum-specific adaptation for non-US frameworks.

These are shared repository-level instructions for every Codex role working on the Learning Progressions KG project. Role-specific instructions and the canonical engineering brief live under `artifacts/instructions/`.

## 1. Instruction hierarchy, role routing, and required files

The root `AGENTS.md` applies to every role and every task in this repository.

A selected role file may add or narrow behavior for that role, but it may not silently weaken or override these shared rules. If the root instructions, a role file, and the engineering brief appear to conflict materially, stop the affected work and report the conflict rather than choosing whichever rule is more convenient.

The required role files are:

- production/configuration/documentation implementation: `artifacts/instructions/coding_agent.md`
- automated testing, regression fixtures, evaluation harnesses, and pipeline validation: `artifacts/instructions/testing_agent.md`
- independent final gate review: `artifacts/instructions/reviewer_agent.md`
- read-only architecture/code/test/artifact explanation: `artifacts/instructions/explainer_agent.md`

The canonical product and engineering specification is:

- `artifacts/instructions/learning_progressions_engineering_brief.md`

Before substantive work, read this root file, the selected role file, and the governing portions of the engineering brief.

### Dependency-closed engineering-brief pass

Do not substitute a superficial keyword scan for understanding the governing specification.

For an ordinary build-order task, read at minimum:

1. the brief status and opening implementation gate;
2. Section 3.1 marker semantics;
3. Section 4 invariants in full;
4. the exact current Section 5 build-order step in full;
5. every earlier build-order step whose output or contract the current step consumes;
6. every `D#`, `SETTLED`, or `LIMIT` item referenced by the current step, implementation surface, or tests; and
7. the relevant core-model and artifact-contract text from Sections 1 and 2.

For cross-step remediation, read the reopened owning step, every affected approved step through the former frontier, all contracts among those steps, Section 4 in full, and every implicated decision or limitation.

For Step 29 final review, read the entire engineering brief.

For a broad explainer task, read enough of the brief, code, tests, configs, and generated artifacts to explain the requested behavior accurately. For a narrow explainer task, begin with the directly relevant material and expand when dependencies require it.

If the selected role file or canonical brief is missing, unreadable, or ambiguously versioned, stop before substantive work and report the problem.

## 2. One primary role per task

Each task/thread has one primary role. Do not silently substitute one role for another.

- A request to implement production code, runtime config, pipeline integration, exporters, validators, or project documentation uses the **coding role**.
- A request to create or modify automated tests, reduced regression fixtures, test helpers, D12 release-policy conformance coverage, or to execute/red-team a completed step uses the **testing role**. A future semantic-evaluation harness requires a separately approved governance change.
- A request for the final quality gate after implementation and testing uses the **reviewer role**.
- A request for a walkthrough, explanation, comparison, ELI5, or interpretation of the brief/code/tests/artifacts uses the **explainer role**.
- A request to change this file, a role instruction file, or the engineering brief is a **governance-only edit task**. Route it through `artifacts/instructions/coding_agent.md` in governance-edit mode unless the user explicitly selects another governance workflow. Only governance files may be edited, and the task must not be combined with production implementation or test authorship in the same thread.

A role may explain its own findings and provide a handoff, but it must not cross another role's write boundary. In particular:

- production implementation and automated-test authorship remain separate;
- testing may not repair production code;
- review may not repair code or tests;
- explanation is read-only;
- a DECIDE-resolution edit to the brief must be completed and approved before dependent code is written.

If a prompt combines incompatible role-owned work, perform only the explicitly selected primary role and provide a handoff for the remaining work. If no safe primary role can be identified, stop and report the role conflict.

## 3. Build-order ownership

The engineering brief defines Steps 0–29. Follow them in order.

| Step(s) | Primary role                         | Notes                                                                                                                                              |
|---------|--------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------|
| 0       | coding agent in governance-edit mode | Record the user's decisions, update the brief consistently, and obtain the user's implementation OK. No production or test implementation begins.  |
| 1       | testing                              | Establish reduced six-curriculum regression fixtures and fixture validation without changing production behavior.                                  |
| 2–23    | coding, then testing                 | Implement one reviewable production/configuration aspect, then independently red-team it.                                                          |
| 24–25   | testing                              | Add D12 release-policy conformance coverage and run the targeted six-curriculum structural/process matrix. Production remains read-only.           |
| 26      | deferred                             | No v1 implementation or tuning work. After Step 25 reviewer approval, proceed directly to Step 27.                                                 |
| 27      | testing                              | Run and validate all six complete pipelines. External LLM execution requires explicit user authorization.                                          |
| 28      | coding, then testing                 | Update project documentation, then verify it against actual artifacts and accepted LIMITs.                                                         |
| 29      | reviewer                             | Perform the final comprehensive release review. No implementation is owned by this step.                                                           |

When a step contains both production and test obligations, the coding role completes only the production/configuration/documentation surface. The testing role independently creates and runs the required tests afterward.

Do not implement a later step early merely because it would make the current step easier.

## 4. Step 0 is a hard specification gate

The updated brief remains a pre-implementation governance candidate until every decision payload is settled consistently and the user explicitly approves it for implementation.

No Step 1–29 build-order work may begin until all of the following are true:

1. every Section 3 `DECIDE` item has an explicit, complete user decision;
2. every option-required matrix, ordered value, threshold, attribution value, reviewer authority, exception record, and other concrete policy payload is recorded—an option letter alone is insufficient where the decision text requires more;
3. the engineering brief has been updated so the chosen behavior is written as `SETTLED`;
4. dependent core-model, configuration-semantics, invariant, build-order, and test language has been updated consistently;
5. no implementation-governing placeholder such as `TBD`, `...`, or an angle-bracket value remains;
6. no unresolved `DECIDE` marker remains for implementation behavior; and
7. the user has explicitly approved the updated brief for implementation.

When work encounters an unresolved `DECIDE`:

1. identify the exact decision and affected step;
2. explain why the work depends on it;
3. do not choose on the user's behalf;
4. do not encode one option in code, tests, fixtures, prompts, schemas, or outputs;
5. update the brief first only after the user chooses and explicitly requests the specification edit; and
6. wait for the user's approval of the updated brief before implementing that choice.

Unrelated atomic work may continue only when it is demonstrably independent of the unresolved decision.

## 5. Normal progression gate

For coding-primary steps, the normal sequence is:

```text
coding
  -> independent testing/red-team
  -> user-created candidate review commit
  -> independent reviewer
  -> next build-order step
```

For testing-primary steps, the normal sequence is:

```text
testing/evaluation or pipeline validation
  -> user-created candidate review commit when repository content changed
     OR exact existing candidate SHA plus immutable evidence when only execution evidence changed
  -> independent reviewer
  -> next build-order step
```

Step 29 is the terminal comprehensive reviewer gate.

Step 26 is an explicit D12 deferral, not a coding/testing deliverable. It creates no candidate commit or reviewer gate. Confirmed Step 25 defects return to the earliest owning step through the ordinary remediation workflow; otherwise the reviewer-approved Step 25 state is the predecessor for Step 27.

Coding or testing completion alone never authorizes the next numbered step.

### Baseline and review-base SHA

Step 0 governance edits normally occur before this baseline exists and therefore do not require a prior reviewer-approved review-base SHA. The governance-edit agent must still report the observed repository root, branch/detached state, current `HEAD` when one exists, `git status --short`, and pre-existing user changes, and must not describe that observed state as reviewer-approved.

Before Step 1 begins, the user must create a baseline commit containing the intended repository state, this root `AGENTS.md`, all role instructions, and the user-approved canonical engineering brief. Step 1 starts from that baseline as the current `HEAD`, with a materially clean working tree.

Every implementation, testing, review, and remediation handoff must carry one explicit **review-base SHA**:

- Step 1: the baseline commit;
- an ordinary later Step 2–28: the previous step's reviewer-approved commit;
- Step 29 final review: the original pre-Step-1 baseline is the review base and the exact Step 28 reviewer-approved SHA is the candidate, so the reviewer examines the complete LP project range;
- current-step remediation: the same review base used by the rejected candidate;
- cross-step remediation: the formerly approved frontier commit, unless a user-controlled history rewrite establishes a sanitized replacement.

The review base remains unchanged through rejected candidates and remediation for that scope.

If the review base is missing, ambiguous, unreachable, not an ancestor of the candidate, or changed without an explicit reopened-scope decision, stop with `BLOCKED`.

### Candidate review commit

After coding and testing are complete and a meaningful review is possible, the user—not an agent—creates a candidate review commit containing the exact in-scope production, config, documentation, test, fixture, manifest/lockfile, and tracked generated changes. The testing agent may help generate a git commit **message** for the user following the conventional commit style.

The candidate must be a descendant of the review-base SHA.

Agents must not stage or commit unless the user explicitly authorizes that exact operation.

If a reviewer rerun changes only evidence, execution results, ownership classification, or oracle analysis—and the repository tree is identical to the already-reviewed candidate—reuse the same candidate SHA. Do not create an empty/no-op commit.

### Generated pipeline artifacts

Full curriculum result directories are not committed. When generated outputs are review evidence rather than tracked source, the handoff must record:

- exact candidate code SHA;
- exact runtime config path and hash;
- source PDF/document key or hash;
- result directory path;
- run manifest and actual material content hashes/identifiers;
- commands used;
- whether external LLM calls occurred; and
- checksums or a reproducible manifest for the artifacts under review.

Do not present untracked generated artifacts as though they are part of the candidate commit.

### Approval binding

Reviewer approval is bound to the exact approved commit SHA and repository tree. Amending, rebasing, replacing, or materially changing that commit invalidates the approval.

A later engineering-brief change invalidates prior approval when it changes, reinterprets, contradicts, or could affect a requirement, invariant, decision, LIMIT, dependency, or assumption governing that approved step. A clearly prospective change owned only by a future step does not retroactively invalidate earlier approval.

## 6. Cross-step defects and reopened prior steps

Prior approval is not immunity from later evidence.

When coding, testing, or review finds a defect, first determine ownership:

- If the current unapproved step introduced a regression against a previously correct contract, the current step owns the repair.
- If the defect already existed in an earlier approved state, or a later brief change retroactively changes that earlier step's requirements, reopen the earliest affected step.

Reopening Step `K` invalidates the progression approval for Step `K` and every later approved step through the current approved frontier `F`.

A cross-step remediation workflow must:

1. name `K`, `F`, and the exact former frontier/review-base SHA;
2. repair the defect in the current repository without rewriting history, except where history-sensitive material requires a user-controlled rewrite;
3. make only the minimum compatibility changes needed through `F`;
4. rerun the reopened step's obligations and every affected regression/integration path through `F`;
5. require a user-created candidate revalidation commit when material repository content changed; and
6. return the complete affected chain to the reviewer before work on `F + 1` resumes.

Do not hide an earlier defect inside a later step or implement unapproved later functionality as a shortcut.

## 7. Inter-agent disagreement and no-loop rule

Coding, testing, and reviewer agents may challenge another agent's finding when they have brief-grounded evidence.

Do not bounce the same disagreement among fresh threads indefinitely:

1. a first production-vs-test-oracle disagreement receives one independent testing reassessment and a reviewer gate;
2. the reviewer must then explicitly **uphold**, **withdraw**, **reclassify**, or **block** the finding;
3. an upheld finding may be challenged again only with materially new evidence or a newly identified exact specification conflict;
4. if a role cannot perform the upheld remediation without violating its good-faith reading of the brief, stop and surface the exact conflict for user clarification; and
5. if the brief is genuinely open, contradictory, or insufficient, return `BLOCKED` rather than recycling the finding.

Every handoff involving a disputed finding must carry the original finding, prior reassessments, latest reviewer disposition, new evidence, and the next required role.

## 8. Specification authority and marker semantics

The Learning Progressions engineering brief is the product and engineering source of truth.

Use this precedence:

1. governing engineering-brief text;
2. Section 4 invariants;
3. the current Section 5 build-order step and earlier contracts it consumes;
4. existing repository architecture and conventions where they do not conflict with the brief; and
5. implementation judgment only for details the brief genuinely delegates.

The three markers are load-bearing:

- **SETTLED** — implement and test the settled design. Do not reopen it merely because another design seems preferable.
- **DECIDE** — a consequential choice remains open. Do not silently choose it. A letter does not settle a decision that also requires matrices, values, thresholds, attribution text, reviewer authority, or another concrete payload. Update the brief completely after the user decides, then obtain implementation approval.
- **LIMIT** — a known weakness is accepted. Do not silently design around it or claim it has been solved.

Treat every Section 4 invariant as mandatory. If an invariant appears contradictory or impossible to satisfy, stop the affected work and surface the conflict.

Do not replace the brief with general industry practice, model intuition, Learning Commons defaults, or what seems convenient.

External Learning Commons documentation may clarify the public ontology, but it does not override the user-approved engineering brief. Do not use external information to settle an open project decision unless the user explicitly asks for research.

## 9. Project-wide semantic and architectural boundaries

The following constraints apply across roles:

- Learning Progressions adds `buildsTowards` and `relatesTo` relationships; it adds no new node class.
- Published progression endpoints are `StandardsFrameworkItem` nodes using `case_identifier_uuid` keys.
- Learning Components may be evidence, but are not LP relationship endpoints.
- `hasChild` expresses curricular hierarchy/decomposition and is not automatically progression.
- No country, organization, subject, grade label, statement type, or hierarchy shape may be hard-coded into generic LP Python.
- Curriculum-specific semantics belong in required `kgs.lp` runtime configuration.
- Runtime configuration carries only genuine curriculum-specific or operational choices. Universal settled behavior with one permitted value is code-owned and must not be repeated as selector or literal-only fields.
- Under the current brief, Python owns D2 canonical-coordinate and fixed rank/missing-value mechanics, the D3 candidate evidence/ranking/tie-breaking procedure, D11 license-inheritance and required-provenance mechanics, and D13 checkpoint/resume/stale-input behavior. `kgs.lp` still explicitly configures the D2 coordinate type/order, D3 per-SFI and total candidate budgets, D11 attribution/ownership values, and D13 retry counts.
- Local curriculum order is authoritative; code must not derive progression order by lexical label sorting or by assuming US grade enums.
- AS hierarchies may be trees or DAGs. Code and tests must not assume one parent or one ancestor path.
- Framework-root fallback relationships for unresolved AS ancestry are not positive curricular evidence.
- LP eligibility is independent of normalized statement type, leafness, and LC eligibility.
- Candidate generation must be deterministic, explainable, and bounded before any LLM call.
- D3 uses one built-in deterministic non-embedding candidate policy. Runtime configuration must not select its algorithm, strategies, technology, enabled signals, ranking/tie-breaking, fingerprint inputs, or implementation version. Replacing that policy is separately governed and requires deletion and regeneration of affected artifacts rather than cross-implementation reuse.
- The complete candidate and request populations must be validated, materialized, and count/content-hash-aligned before any external LP LLM call.
- Integrity and reuse use hashes/identifiers derived from actual material inputs and artifacts, not manually maintained candidate-policy versions or configurable fingerprint-input lists.
- No single signal—hierarchy, LC overlap, text similarity, code proximity, source order, or local rank—automatically publishes an edge.
- LLM producer/checker responses are untrusted proposals. Deterministic code owns validation, reconciliation, endpoint containment, IDs, counts, and release status.
- `no_relation`, `needs_review`, and processing failure remain distinct.
- `needs_review` remains visible, never publishes, and does not block release; any failed pair after permitted retries/recovery halts LP under D13 with no count/rate tolerance.
- Existing AS-only and AS+LC artifact schemas remain intact; AS+LC+LP outputs are additive.
- Never hand-edit a generated final graph to make a run pass. Fix the earliest incorrect config, candidate, prompt, judgment, finalization, or validation stage and rerun.
- V1 has no independent pre-release semantic/gold-set gate. Structural/process validity and producer/checker agreement must never be described as pedagogical correctness, and the accepted D12/D14 limitations must remain visible.

## 10. Scope discipline

Perform only the work requested by the current task and owned by the applicable build-order step.

Do not perform unrelated refactors, dependency upgrades, formatting sweeps, renames, cleanup, prompt rewrites, threshold tuning, or speculative architecture changes.

Do not tune generic code to one curriculum when the behavior belongs in `kgs.lp`.

Do not edit governance files unless the user explicitly requests a governance/specification change or an approved DECIDE workflow requires it.

Do not alter existing AS/LC artifacts or semantics merely to simplify LP implementation unless the engineering brief explicitly requires and approves that change.

## 11. Repository and Git safety

Treat the connected working tree as user-owned state.

Before editing, inspect:

- repository root;
- branch or detached-HEAD state;
- exact `HEAD` SHA;
- `git status --short`;
- pre-existing staged, modified, and untracked files; and
- relevant commits after the review base.

Preserve pre-existing changes.

Read-only Git inspection is allowed as needed.

Unless the user explicitly authorizes the specific operation, do not:

- create, switch, rename, or delete branches;
- stage or unstage files;
- commit;
- fetch, pull, push, publish, merge, or rebase;
- reset, revert, restore-overwrite, clean, or stash user changes;
- create, move, or delete Git worktrees; or
- rewrite Git history.

Never discard user work merely to obtain a clean diff or make validation pass.

Leave task changes reviewable in the working tree.

Do not read, search, inspect, modify, or use anything under the `data/` or `graveyard/` directories unless the user explicitly asks you to do so. Those are user-personal files and have no effect or bearing on the VCN codebase or specification.

If you modified, changed, updated, added, or deleted anything in the `results` directory, then you must explicitly inform the user of the exact changes.

## 12. External effects, LLM calls, and cost control

Prefer local, deterministic, repository-scoped work.

Do not invoke paid or externally hosted LLM APIs, upload curriculum text, or run complete source-PDF pipelines unless the user explicitly authorizes that execution for the current task and the environment is correctly configured.

Automated tests should use deterministic fixtures, fakes, or recorded synthetic responses by default. They must not make live LLM calls.

When live pipeline execution is authorized:

- use the existing configured `LLM_KG_MODEL`;
- do not introduce a new LP model environment variable;
- report the command, config, model identifier available from the run, usage/cost evidence, output directory, and failures;
- do not log or expose API keys; and
- do not silently retry an unexpectedly expensive full run.

Respect sandbox and approval boundaries. Request only the minimum external permission required.

## 13. Data, fixture, and secret hygiene

Do not expose, invent, commit, log, or copy real secrets.

Use synthetic values in tests and examples unless an approved reduced curriculum fixture is required by the engineering brief.

Do not copy full source PDFs or complete generated result directories into test fixtures when a reduced structural fixture is sufficient.

Preserve provenance fields needed for testing, but minimize fixture size and avoid unrelated source content.

Do not place API keys, credentials, private endpoints, or sensitive environment values into prompts, fixtures, logs, artifacts, reports, or completion summaries.

## 14. Failure behavior

This project prefers explicit state and fail-closed behavior.

Stop the affected work and report the blocker when proceeding would require:

- consuming an unresolved `DECIDE`;
- weakening a `SETTLED` rule or Section 4 invariant;
- silently designing around a `LIMIT`;
- guessing curriculum semantics that belong in `kgs.lp`;
- treating an unvalidated or stale upstream bundle as authoritative;
- making an unbounded or all-pairs LLM call;
- fabricating missing evidence, endpoints, relationships, provenance, or counts;
- modifying another role's write domain;
- advancing without the required review-base/candidate state; or
- invoking an external LLM or full pipeline without explicit authorization.

Do not paper over blockers with permissive defaults, fabricated relationships, hand-edited outputs, skipped validation, or misleading success status.
