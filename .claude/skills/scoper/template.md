# Scoper Artifact Template

This file is the authoritative artifact shape and authoring contract for the
persisted scope owned by Scoper.

The scope defines **what must be achieved, why it matters, what is excluded, and
what counts as done**. It must remain implementation-agnostic except where a
technology or implementation constraint is already established by the user
request or by pre-existing project context independent of the active technical
design. A planned architectural constraint recorded in `.standards/CONTEXT.md`
does not become a scope constraint merely because Auditor recorded it.

Both `PLAN` and `REPLAN` produce this same artifact shape. `REPLAN` should
preserve valid existing content and revise only what the new information
invalidates.

Omit empty sections. Add detail only when it materially reduces ambiguity.

In `DOCUMENTATION`, use this same shape for documentation outcomes. Identify
audiences and documentation targets in Goal and Work, permitted editing in
Constraints, and excluded behavior changes in Non-goals. Preserve the request's
explicit Documenter choices without inventing defaults. Acceptance conditions
describe verifiable documentation accuracy, coverage, or usability of existing
behavior; they do not require new implementation or formal Tester reports. An
assessed no-change result may satisfy the requested documentation outcome, but
does not omit final review or synchronization. Apply the documentation
contract's blocking decision when an outcome needs an omitted role.

When Scoper creates a new STANDARDS-owned scope artifact, create it with
`node .standards/bin/artifact.mjs init SCOPE`, which writes this block with the
active cycle ID:

```markdown
<!-- STANDARDS
Artifact: SCOPE
Cycle: <Active Work.Id>
-->
```

Do not add this block solely because Scoper updates a pre-existing unmarked
project-owned canonical scope document; that document remains project-owned.

---

# Project or Change Name

## Goal

Describe the intended outcome and why this work is needed. State the result the
project or change must achieve without prescribing implementation.

## Constraints

- Required boundary, fixed condition, compatibility requirement, policy,
  platform limitation, performance bound, or other established constraint.
- Add only constraints that materially affect the scope.

## Non-goals

- Explicitly excluded outcome, capability, migration, integration, behavior, or
  adjacent work.
- Use this section to make important boundaries unambiguous.

## Work

### 1. Outcome-oriented work item

**Intent:** Explain why this work item exists and what user, system, or project
outcome it serves.

**Done when:**

- `AC-001`: Observable scope-level acceptance condition.
- `AC-002`: Another externally verifiable behavior or required property, when
  needed.

**Depends on:** Other numbered work items, an established external dependency,
or `None`.

### 2. Outcome-oriented work item

**Intent:** Why this work item exists.

**Done when:**

- `AC-003`: Observable scope-level acceptance condition.

**Depends on:** Dependency or `None`.

## Retired Acceptance Identifiers

- `AC-004`: Retired because the condition was removed or materially replaced.
  When replaced, reference the new acceptance identifier when useful.

## Assumptions

- Non-blocking assumption that is useful to preserve but is not yet established
  as fact.
- Do not record unresolved blocking decisions here; blocking questions belong in
  `Active Work.BlockedOn`.

## Previous Cycles

Only in a scope document reused across cycles, and always the last section: the
earlier cycles' acceptance conditions and retired identifiers, moved here
unchanged, including their headings. Everything below this heading is history,
not current obligations.

---

## Authoring Rules

- Define **what** and **why**, not **how**.
- Keep work items coarse and outcome-oriented; do not turn the scope into a
  coding task list.
- Express required outcomes through the Goal and Work items instead of
  duplicating them in a separate requirements section.
- Write observable completion conditions. Describe behavior, compatibility,
  conformance, performance bounds, resource limits, build/compile guarantees, or
  other externally verifiable outcomes when relevant.
- Represent every verifiable in-scope obligation that must be proven at
  completion with at least one acceptance condition. Do not leave such an
  obligation only in Goal or Constraints.
- Do not combine separable obligations under one acceptance identifier when
  their satisfaction or verification evidence is established in different
  workflow phases. Give those obligations separate acceptance conditions so
  downstream roles can establish evidence without masking unfinished work.
- Assign every scope-level acceptance condition a unique `AC-NNN` identifier
  within the active cycle. Treat the identifier as a stable reference, not as an
  ordering guarantee.
- Define each condition as a list item, bulleted or numbered, that starts with
  its identifier followed by a colon or dash, as in **Done when** above:
  ``- `AC-001`: <condition>``. Do not define conditions in a table, heading, or
  prose; `check` does not recognize those forms and reports the identifier as
  undefined. Retired identifiers use the same form under
  `## Retired Acceptance Identifiers`. When reusing a project scope document,
  rewrite its current conditions in this form.
- During REPLAN, preserve an acceptance identifier when the condition keeps the
  same meaning. Use a new, previously unused identifier for a new or materially
  replaced condition. Record removed or replaced identifiers under **Retired
  Acceptance Identifiers**. Do not renumber surviving identifiers or reuse
  retired identifiers within the active cycle.
- Get each new identifier with `node .standards/bin/id.mjs next AC <scope>`.
  When a cycle reuses a scope document that holds another cycle's conditions,
  move them and their retired identifiers under a final **Previous Cycles**
  section and continue numbering from the highest identifier ever used in the
  document.
- Do not choose new libraries, frameworks, APIs, storage systems, protocols,
  algorithms, deployment targets, or implementation patterns. Those decisions
  belong to Architect unless independently established by the user request or
  pre-existing project constraints. Planned decisions from the active technical
  design remain Architect-owned even when Auditor records them in
  `.standards/CONTEXT.md`.
- Distinguish constraints, non-goals, and assumptions clearly.
- Record only non-blocking assumptions. Resolve blocking scope questions before
  Scoper completes.
- Record meaningful dependencies and ordering between work items.
- Preserve user intent and established boundaries during REPLAN. Do not silently
  expand, remove, or materially change requested outcomes.
- Keep the artifact concise. Add sections or detail only when they materially
  improve scope clarity.
