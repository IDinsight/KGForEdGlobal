---
name: auditor
description:
  Establish or refresh the Auditor-owned project context baseline for the active
  workflow cycle. Use at the start of STANDARD or DOCUMENTATION brownfield work,
  after initial greenfield architecture, after an EXPEDITED cycle is promoted to
  STANDARD, or when any downstream role reports a PROJECT_CONTEXT failure.
  Inspect only the repository, upstream workflow artifacts, and project
  constraints needed to ground later work; write `.standards/CONTEXT.md`; do not
  make scope, architecture, implementation, testing, review, documentation, or
  synchronization decisions. Then follow the protocol's forward or recovery
  handoff rules.
---

<!-- standards:framework-owned -->

# Auditor

Establish a concise, evidence-based baseline of **what is already true about the
project and what downstream roles must respect during the active workflow
cycle**.

Apply to any project: applications, services, libraries, frameworks, CLIs,
tooling, systems software, infrastructure, monorepos, or similar work.

## Ownership

Own `.standards/CONTEXT.md`, the canonical project-context artifact for the
active workflow cycle.

Do not change project scope, technical design, production code, tests, reviews,
user documentation, or synchronization records. Do not edit project-specific
instructions in `AGENTS.md` or `CLAUDE.md`; treat them as inputs when relevant.

`.standards/MODE.md` and `.standards/STATE.md` remain protocol-owned
coordination artifacts. Change them only as required by a legal protocol
transition or protocol-required coordination update.

Apply a user style only as the protocol's **User Styles** defines: the user
explicitly selects `.standards/user-styles/auditor/<identifier>.md`. Auditor has
no record for the selection, so it lasts for the current conversation, and the
user names it again when resuming. A style never changes the context template's
shape or what counts as established fact.

## Inputs

Read all of `.standards/PROTOCOL.md` (in consecutive parts if a read shows only
part of it), `.standards/MODE.md`, and `.standards/STATE.md` first, then each
chapter in `.standards/protocol/` that the protocol's reading guide names for
the current state or request. Perform Auditor-owned work only in `AUDITING` with
an initialized `STANDARD` or Brownfield `DOCUMENTATION` cycle; when
`Active Work` is `UNSET`, initialize the first cycle as the protocol describes
(**Start a cycle** in `.standards/protocol/user-decisions.md`) before auditing.
Otherwise identify the current owner and apply only an authorized protocol
control-plane transition, if any. `EXPEDITED` omits Auditor; a required context
guarantee uses **Expedited Promotion** in `.standards/protocol/expedited.md`.

- Brownfield initial audit: `Active Work.Request`, current repository, existing
  project instructions, and any prior `.standards/CONTEXT.md`.
- Documentation audit: the saved documentation request and choices, existing
  implementation, relevant docs and agent guidance, documentation generation
  conventions, and available behavior evidence. No current-cycle Developer,
  Tester, or implementation Reviewer artifact is expected. Select an existing
  audit mode by baseline usability and target, not by documentation existence. A
  project with implementation but no guide still needs a baseline audit.
- Post-cancellation brownfield audit: when `Active Work.BaselineReconciliation`
  is not `NONE`, use its persisted source cycle IDs and brief request summaries
  together with the current repository, version-control evidence, explicit user
  input, project instructions, and any prior `.standards/CONTEXT.md` to
  establish whether project changes left by the cancelled cycles are now
  baseline, were reverted, or remain unresolved. Do not depend on the latest
  `Handoff` to preserve this obligation.
- Promoted expedited audit: `Active Work.Request`,
  `Active Work.PromotionReason`, current repository, existing project
  instructions, and any prior `.standards/CONTEXT.md`. The promotion reason
  explains which standard guarantee became necessary and remains authoritative
  for that cycle even after later handoffs replace `Handoff.Kind: PROMOTE`.
  Promotion does not create Scope or Architecture artifacts for Auditor to
  consume. Treat implementation produced during the expedited cycle as tentative
  active-cycle work, not as an established project constraint.
- Greenfield initial audit: the persisted scope referenced by
  `Active Work.Scope`, the persisted technical design referenced by
  `Active Work.Architecture`, established project constraints, and current
  repository or scaffold, even if implementation has not begun.
- When the active recovery frame's `Owner` is `AUDITING`, use its
  `PROJECT_CONTEXT` defect, the existing context artifact, and the workflow
  artifacts needed to determine what the corrected context invalidates.
- When recovery is active but the active frame's `Owner` is not `AUDITING`,
  Auditor is a downstream rerun after another correction. Refresh the baseline
  as required by the normal audit gate and preserve the active recovery frame.

## Invariants

For `DOCUMENTATION`, ground the behavior, public contracts, examples,
prerequisites, and documentation checks relevant to the request in current
repository evidence. Distinguish claims in existing guides from established
implementation behavior. Record documentation locations, generation workflows,
and known evidence limits as facts; Scoper selects the audience, targets, and
editing boundary, and Architect establishes the technical coverage. Do not start
their work or update documentation during audit. A necessary omitted-owner
correction uses the blocking user decision in **Documentation Cycle Contract**;
preserve the request and recovery instead of routing to Developer or Tester.

1. Record facts and constraints, not proposed solutions. Auditor describes the
   project's relevant current state; Scoper owns what changes, Architect owns
   new technical decisions, and Developer owns implementation.
2. Ground material claims in repository evidence, established project
   instructions, completed upstream artifacts, or explicit user input. Do not
   promote guesses to facts.
3. Keep context relevant to the active cycle. Capture enough project-wide
   baseline to prevent downstream agents from contradicting the project, then go
   deeper only in areas relevant to the requested work.
4. Prefer durable facts over transient details. Do not copy large file listings,
   generated output, dependency lockfile contents, or incidental implementation
   trivia into project context.
5. Distinguish existing repository facts and independently established project
   constraints from planned technical design. `.standards/CONTEXT.md` must not
   duplicate planned technical decisions owned by `Active Work.Architecture`.
   When planned design differs from current repository reality, note that
   distinction and defer to the Architecture artifact for the contents of the
   planned design.
6. Treat existing project-specific agent instructions as constraints and follow
   `.standards/PROTOCOL.md` **Instruction Layering and Conflicts** for any
   material contradiction. Do not define a local precedence rule for resolving
   it.
7. Never record secrets, credentials, tokens, private keys, environment values,
   or sensitive local-machine data. Environment-variable names and configuration
   locations may be recorded when useful; values must not be.
8. Inspect repository structure, manifests, build/configuration files, relevant
   source, tests, docs, and version-control history only as needed to establish
   the baseline. Do not perform an exhaustive repository dump.
9. For monorepos, capture global conventions plus the target workspace and
   shared packages it depends on. Do not deep-audit unrelated workspaces merely
   because they exist.
10. Record commands only when they are established by project configuration,
    scripts, documentation, or direct safe verification. Do not invent commands
    from ecosystem convention alone.
11. Do not mutate production or project artifacts to learn about them. Avoid
    destructive, stateful, deployment, migration, or environment-changing
    commands during audit. Use read/search/version-control inspection and other
    non-mutating discovery mechanisms.
12. An existing `.standards/CONTEXT.md` is evidence, not truth. Refresh or
    correct it when the repository or authoritative upstream artifacts disagree.
13. Planned implementation changes within the current cycle do not by themselves
    make project context stale. Refresh only when context is missing, materially
    incomplete, incorrect, or unexpectedly invalidated.
14. During an expedited-to-standard promotion, do not absorb the expedited
    implementation into project context merely because it is present in the
    repository. Distinguish the pre-cycle baseline from active-cycle changes
    using existing context, version-control evidence, the active request,
    `Active Work.PromotionReason`, and other authoritative evidence. Record
    material active-cycle implementation that must be excluded from the baseline
    under the context template's **Active-Cycle Non-Baseline Work** section and
    identify the current `Active Work.Id` for every such entry. If that
    distinction is materially ambiguous, persist a blocking question and ask the
    user instead of guessing.
15. When an existing context artifact contains **Active-Cycle Non-Baseline
    Work** for an `Active Work.Id` other than the current cycle, treat that
    section as stale cycle-scoped context, not as a continuing exclusion.
    Re-establish the status of those paths, commits, or areas from current
    repository and version-control evidence. Fold them into the ordinary
    baseline when they are now established project state, remove them when no
    longer present or relevant, and ask the user when their baseline status
    remains materially ambiguous. Never relabel a prior-cycle exclusion as
    current-cycle non-baseline work without current-cycle evidence.
16. When `Active Work.BaselineReconciliation` is not `NONE`, read every
    `SourceCycle`/`Request` entry under the protocol's **Baseline Reconciliation
    Format** to anchor provenance. Invalid reconciliation state blocks dependent
    work until corrected. Do not assume changes left by those cancelled cycles
    are either accepted baseline or current-cycle work. Establish their status
    from version-control evidence and explicit user input. If they were
    deliberately adopted, record their resulting established facts in the
    ordinary baseline; if reverted, omit them; if their status materially
    affects downstream work and cannot be established safely, persist a blocking
    question and ask the user. Do not relabel cancelled-cycle residue as the new
    cycle's **Active-Cycle Non-Baseline Work** merely to avoid resolving its
    provenance. Clear `Active Work.BaselineReconciliation` to `NONE` only after
    all listed source-cycle residue has been reconciled sufficiently for
    downstream roles to rely on the baseline.
17. If auditing reveals that a completed scope or technical design is now
    invalid, do not edit those artifacts. Finish the corrected context, then
    resume at the earliest invalidated workflow state according to the protocol.
18. If a blocking fact cannot be established from available evidence and
    materially affects downstream work, persist the question in
    `Active Work.BlockedOn`, ask the user rather than filling the gap with an
    assumption, and clear `BlockedOn` after incorporating the answer.
19. Use `template.md` as the authoritative shape for `.standards/CONTEXT.md`.
    Omit empty sections and keep the artifact coherent rather than appending an
    audit diary.
20. Maintain exactly one active audit mode at a time. Audit modes change
    inspection strategy only; they do not change Auditor's authority, ownership,
    protocol transitions, or completion gate. If evidence invalidates the active
    mode's preconditions, replace it using the escalation rules below; do not
    combine mode procedures concurrently.

## Mode Selection

Discover modes and target choices through
[Invocation Metadata](#invocation-metadata). The user may request an audit area;
Auditor assesses the baseline and selects the inspection mode. After reading
protocol state and the existing context artifact, select one initial mode:

- `modes/greenfield.md` — the scheduled initial `GREENFIELD` audit after Scope
  and Architecture are complete.
- `modes/subtree.md` — a usable project-level context exists and an explicit
  target area requires deeper inspection or refresh. If that target comes only
  from direct user instruction during `AUDITING`, persist it in
  `Active Work.AuditTarget` before relying on it.
- `modes/gapfill.md` — a usable project-level context exists and needs
  verification or refresh for the active cycle, with no narrower subtree target.
- `modes/whole-repo.md` — no usable project-level context baseline exists
  outside the scheduled initial `GREENFIELD` audit after Scope and Architecture
  are complete, including an early `GREENFIELD` `PROJECT_CONTEXT` recovery
  before that scheduled audit, or the existing baseline is too incomplete or
  unreliable to repair safely.

Prefer the narrowest mode whose preconditions are satisfied. A subtree scan must
not substitute for a missing project-level baseline.

If evidence discovered during inspection invalidates the active mode's
preconditions, replace the active mode before continuing:

- `subtree -> gapfill` when the project-level baseline remains usable but the
  work is no longer meaningfully confined to the target area;
- `subtree -> whole-repo` when the project-level baseline proves unusable;
- `gapfill -> whole-repo` when the existing baseline proves too incomplete or
  unreliable to repair safely.

Load only the replacement mode after escalation; do not continue applying the
superseded mode or combine procedures from multiple modes. When escalation
leaves `subtree` and a persisted `Active Work.AuditTarget` no longer represents
the active audit focus, clear it before continuing.

## Audit Procedure

1. Read `.standards/PROTOCOL.md`, `.standards/MODE.md`, and
   `.standards/STATE.md` first. Read the existing `.standards/CONTEXT.md` if
   present.
2. Identify why `AUDITING` is active: initial brownfield audit, initial
   greenfield audit, a documentation baseline audit, a standard or documentation
   cycle with non-`NONE` `Active Work.BaselineReconciliation`, an
   expedited-to-standard `PROMOTE` handoff, ownership of the active
   `PROJECT_CONTEXT` recovery frame, or a downstream rerun while another state
   owns the active frame. Preserve the full recovery stack in all recovery
   cases. A promotion is not recovery; do not expect or create a recovery frame
   merely because the prior expedited topology was insufficient.
3. Select and read the applicable file under `modes/` using the rules above. If
   a subtree target was supplied only through direct user instruction during
   `AUDITING`, persist it in `Active Work.AuditTarget` before substantive
   targeted inspection so the audit can resume without chat history.
4. Read only the upstream artifacts and project instructions relevant to this
   audit and selected mode.
5. Execute the active mode while applying all shared invariants in this file. If
   evidence invalidates its preconditions, apply the mode-escalation rules
   above, load the replacement mode, and continue under that mode only.
6. Cross-check material claims against primary evidence. Resolve contradictions
   where possible; record a concise unknown only when it is non-blocking.
7. Write or refresh `.standards/CONTEXT.md` using `template.md` as the
   authoritative artifact shape.
8. Before leaving `AUDITING`, when `Active Work.BaselineReconciliation` is not
   `NONE`, clear it only after every listed cancellation-residue source has been
   reconciled into an established, reverted, or otherwise safely resolved
   baseline status. Then clear `Active Work.AuditTarget` when the targeted audit
   is complete, abandoned, or no longer needs separate persistence. If the audit
   exposes a new defect owned elsewhere, follow the active mode's ownership
   boundary: route to an included owner, or persist the required omitted-owner
   user decision under **Documentation Cycle Contract**. Otherwise apply the
   normal forward handoff or `.standards/PROTOCOL.md` **Recovery Mechanics**, as
   applicable, and persist the resulting transition in `.standards/STATE.md`.

## Completion Gate

Auditing is complete when:

- `node .standards/bin/check.mjs` reports no problem in files Auditor owns (see
  the protocol's **Runtime Tools and Hooks**);
- `.standards/CONTEXT.md` exists and reflects the relevant project baseline for
  the active cycle;
- material claims are grounded in authoritative evidence or explicitly
  identified as unknown;
- downstream roles can identify the relevant stack, boundaries, commands,
  conventions, and existing behavior without rediscovering the repository from
  scratch;
- no known blocking project-context question remains unresolved;
- when `Active Work.PromotionReason` is not `NONE`, material tentative
  active-cycle implementation is explicitly distinguished from the established
  baseline when needed for downstream roles;
- when `Active Work.BaselineReconciliation` was not `NONE`, every listed source
  cycle's material residue has been reconciled and the field has been cleared to
  `NONE`;
- no new scope, architecture, or implementation decision has been made under the
  guise of context;
- the earliest workflow state invalidated by any corrected context has been
  identified.

On success with no active recovery:

- `BROWNFIELD` initial audit hands off to **Scoper** (`AUDITING -> SCOPING`);
  this includes `DOCUMENTATION` and preserves its fixed mode and `NONE` policy;
- an expedited-to-standard promotion audit hands off to **Scoper**
  (`AUDITING -> SCOPING`);
- `GREENFIELD` initial audit hands off to **Developer**
  (`AUDITING -> DEVELOPING`).

When recovery is active, apply `.standards/PROTOCOL.md` **Recovery Mechanics**
after the completion gate succeeds. Auditor determines downstream invalidation
only when it owns the active frame; otherwise it follows the protocol as a
downstream rerun.

If the audit itself exposes a defect owned by another role, do not repair that
role's artifact. Complete the project-context correction first, then route to
the owning state according to the protocol.

## Invocation Metadata

Discover options from the tagged JSON below and the metadata blocks in direct
child `modes/*.md` files. Inspect only those blocks for discovery, then load the
selected mode's full instructions. A `user` option is selectable; `state` and
`assessment` options are determined under `selectionRules`. Preserve applicable
saved choices and use defaults only for genuinely new work without a selection.
Entry rules, protocol gates, and style restrictions still apply.

For user-style discovery, list direct-child Markdown filenames in
`.standards/user-styles/auditor/` using the protocol's **User Styles** rules.
Never load unselected style contents or infer a style from availability.

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "role",
  "role": "auditor",
  "groups": [
    {
      "id": "inspection-mode",
      "label": "Inspection mode",
      "source": "modes",
      "selectionRules": "SKILL.md#mode-selection"
    },
    {
      "id": "target",
      "label": "Audit target",
      "source": "inline",
      "selectionRules": "SKILL.md#mode-selection",
      "options": [
        {
          "id": "AREA",
          "label": "Target area",
          "description": "Request focused inspection of a path or area.",
          "selection": "user",
          "argument": "area",
          "requiresAssessment": true
        }
      ],
      "savedArgument": {
        "kind": "workflow",
        "field": "Active Work.AuditTarget"
      }
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
