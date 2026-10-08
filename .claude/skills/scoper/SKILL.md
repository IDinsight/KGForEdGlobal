---
name: scoper
description:
  Define or revise the scope for a project or change before architecture or
  implementation. Use for greenfield planning, post-audit brownfield planning,
  documentation-only brownfield work, or when a downstream phase reports a
  scoping problem. Produce a concise, persisted, implementation-agnostic scope
  with goals, boundaries, constraints, acceptance conditions, dependencies, and
  ordered work items. Normal completion hands off to Architect; recovery follows
  the protocol's recovery-stack and invalidation rules.
---

<!-- standards:framework-owned -->

# Scoper

Turn an idea or requested change into a clear, bounded statement of **what must
be achieved and what counts as done**.

Apply to any project: applications, services, libraries, frameworks, CLIs,
tooling, systems software, infrastructure, or similar work.

## Ownership

Own the project scope. The completed scope must be persisted under the
protocol's **Workflow Artifact Provenance** rules. An appropriate pre-existing
unmarked project-owned scope document may remain the canonical scope location.
If Scoper creates a new scope artifact, create it with
`node .standards/bin/artifact.mjs init SCOPE`, which writes
`.standards/docs/scope/<Active Work.Id>.md` with the current-cycle provenance
block. Never overwrite, repurpose, or adopt a STANDARDS scope artifact whose
recorded cycle differs from `Active Work.Id`. Record the selected
repository-relative path in `STATE.md` as `Active Work.Scope`.

Own the wording and stable `AC-NNN` identifiers of scope-level acceptance
conditions for the active cycle. Number each new condition with
`node .standards/bin/id.mjs next AC <scope>`. When reusing a scope document that
holds another cycle's conditions, move them under a final `## Previous Cycles`
section as the protocol's **Acceptance Traceability** describes.

Do not write architecture specs, production code, tests, audit/context files,
reviews, or user documentation.

## Inputs

Read all of `.standards/PROTOCOL.md` (in consecutive parts if a read shows only
part of it), `.standards/MODE.md`, and `.standards/STATE.md` first, then each
chapter in `.standards/protocol/` that the protocol's reading guide names for
the current state or request. Perform Scoper-owned work only in `SCOPING` with
an initialized `STANDARD` or Brownfield `DOCUMENTATION` cycle; when
`Active Work` is `UNSET`, initialize the first cycle as the protocol describes
(**Start a cycle** in `.standards/protocol/user-decisions.md`) before scoping.
Otherwise identify the current owner and apply only an authorized protocol
control-plane transition, if any. `EXPEDITED` omits Scoper; a required scoping
guarantee uses **Expedited Promotion** in `.standards/protocol/expedited.md`.

- Always: `Active Work.Request`, explicit user constraints, the current
  persisted scope when one exists, and `.standards/CONTEXT.md` when it exists
  and is relevant.
- Greenfield Scoping before the first audit, including recovery reruns, may
  proceed without `CONTEXT.md`; its absence is intentional unless the requested
  work requires project facts that cannot otherwise be established.
- Brownfield Scoping requires the Auditor's project context for the active
  cycle.
- When `Active Work.PromotionReason` is not `NONE`, treat it as durable workflow
  context explaining why a former expedited cycle required the standard
  topology. Use it to surface any unresolved scope question, but do not treat
  the promotion reason itself as user-approved scope unless supported by the
  request, project context, or explicit user input.
- When the active recovery frame's `Owner` is `SCOPING`, use that frame as the
  defect Scoper must correct.
- When recovery is active but the active frame's `Owner` is not `SCOPING`,
  Scoper is a downstream rerun after another correction. Re-evaluate the scope
  against the updated inputs and preserve the active recovery frame.

## Mode Selection

Discover modes through [Invocation Metadata](#invocation-metadata). Assess the
current-cycle scope to select exactly one mode before changing it. These modes
are local execution paths for Scoper and are separate from `.standards/MODE.md`
(`GREENFIELD` / `BROWNFIELD`).

- **PLAN** — use when the active cycle does not yet have a persisted scope for
  `Active Work`. Read and follow [`modes/plan.md`](modes/plan.md).
- **REPLAN** — use when `Active Work.Scope` identifies an existing persisted
  scope that must be corrected, revised, or reconciled with changed inputs. Read
  and follow [`modes/replan.md`](modes/replan.md).

Load only the selected mode file from `modes/`. A request phrased as "add X"
does not create a separate mode or bypass lifecycle rules: if it starts a new
cycle with no current-cycle scope, use PLAN; if it changes an existing
active-cycle scope, use REPLAN.

For either mode, read and follow [`template.md`](template.md). It is the
authoritative shape and authoring contract for the persisted scope artifact.

Apply a user style only as the protocol's **User Styles** defines: the user
explicitly selects `.standards/user-styles/scoper/<identifier>.md`. Scoper has
no record for the selection, so it lasts for the current conversation, and the
user names it again when resuming. A style never changes the template's required
shape or the form of acceptance conditions.

## Invariants

For `DOCUMENTATION`, use Auditor's current baseline to scope documentation of
existing behavior under **Documentation Cycle Contract**. Persist the intended
audiences, documents or areas, editing boundary, required accuracy and coverage,
and any explicit target, collaboration, or user-style choices already saved in
the request. Preserve those choices for Documenter without applying its style to
Scoper's own artifact. Missing documentation can be a target; it is not an
eligibility blocker. Define observable documentation outcomes with current
`AC-NNN` identifiers; checked examples, links, rendering, and content inspection
may establish Documenter-owned evidence where relevant. Do not invent behavior,
implementation tasks, formal Tester evidence, or implementation-review
dependencies. Final Reviewer and Synchronizer still independently assess the
included evidence.

If the requested outcome requires an omitted owner's work, follow the blocking
user decision in **Documentation Cycle Contract** before revising the active
request or scope. Do not silently remove a requested outcome, add implementation
work, or convert the cycle. Recovery and AC invalidation stay within the
included roles; Architect remains the normal next owner.

1. Ask only questions that materially change scope. Resolve blocking ambiguity
   before advancing. Persist any blocking user question in
   `Active Work.BlockedOn` before asking and clear it after incorporating the
   answer.
2. If required project context is missing, materially incomplete, incorrect, or
   unexpectedly invalidated, stop scoping and issue a `PROJECT_CONTEXT` failure
   handoff to Auditor instead of performing a repository-wide audit. Treat an
   **Active-Cycle Non-Baseline Work** entry whose recorded cycle does not match
   the current `Active Work.Id` as stale project context that Auditor must
   reconcile before Scoper relies on it. Do not treat intentionally absent
   project context before the first greenfield audit as defective when scoping
   can proceed without it. Once `.standards/CONTEXT.md` exists, do not ignore it
   merely because `ProjectMode` is still `GREENFIELD`. Planned implementation
   changes within the active cycle do not by themselves make project context
   stale.
3. Treat planned architectural constraints recorded in `.standards/CONTEXT.md`
   as Architect-owned design information, not as independently established scope
   constraints. They constrain Scoper only when the same constraint is
   independently established by the user request or by pre-existing project
   requirements, policy, platform, compatibility, or other baseline constraints.

## Completion Gate

Scoping is complete when:

- `node .standards/bin/check.mjs` reports no problem in files Scoper owns (see
  the protocol's **Runtime Tools and Hooks**);
- the persisted scope satisfies the artifact shape and authoring contract in
  `template.md`, including matching current-cycle provenance when Scoper created
  the artifact;
- every verifiable in-scope obligation that must be proven at completion is
  represented by one or more current scope-level acceptance conditions;
- separable obligations whose satisfaction or verification evidence is
  established in different workflow phases are not combined under one acceptance
  identifier;
- every current scope-level acceptance condition has a unique, stable `AC-NNN`
  identifier, and retired identifiers remain recorded and unreused;
- no blocking scope question remains unresolved;
- in `DOCUMENTATION`, the scope describes an achievable documentation outcome
  within the permitted editing boundary, with no required omitted-owner work;
- `Active Work.Scope` points to the completed persisted scope.

On normal success, the scope is a **completed scope**. Hand off to
**Architect**.

When recovery is active, apply `.standards/PROTOCOL.md` **Recovery Mechanics**
after the completion gate succeeds. Scoper determines downstream invalidation
only when it owns the active frame; otherwise it follows the protocol as a
downstream rerun.

## Invocation Metadata

Discover options from the tagged JSON below and the metadata blocks in direct
child `modes/*.md` files. Inspect only those blocks for discovery, then load the
selected mode's full instructions. A `user` option is selectable; `state` and
`assessment` options are determined under `selectionRules`. Preserve applicable
saved choices and use defaults only for genuinely new work without a selection.
Entry rules, protocol gates, and style restrictions still apply.

For user-style discovery, list direct-child Markdown filenames in
`.standards/user-styles/scoper/` using the protocol's **User Styles** rules.
Never load unselected style contents or infer a style from availability.

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "role",
  "role": "scoper",
  "groups": [
    {
      "id": "scope-mode",
      "label": "Scope mode",
      "source": "modes",
      "selectionRules": "SKILL.md#mode-selection"
    }
  ],
  "userStyles": {
    "source": "role-directory",
    "savedValue": {
      "kind": "conversation"
    }
  }
}
```
