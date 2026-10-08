# S.T.A.N.D.A.R.D.S. Protocol

<!-- standards:framework-owned -->

This file is the canonical contract for shared workflow vocabulary, state,
transitions, recovery, and the installed runtime in S.T.A.N.D.A.R.D.S.

`README.md` explains the framework at a high level. Individual skills define
role-specific behavior. This protocol defines the rules those skills must share.

Read all of this file before workflow work, then each chapter that the reading
guide below names for the current state or request. This file is longer than a
single read in some tools. If a read shows only part of it, such as its
beginning, or its beginning and end with lines left out between them, read the
missing lines in consecutive parts before acting.

## How to read this document

The sections run from foundations (roles, modes, states, the persisted state
record, and the runtime tools) through handoffs, gates, failure and recovery,
user decisions, record formats, and user styles. The order is for orientation
only: a role applies every applicable rule wherever it appears. **Canonical
Terms**, the last section, defines terms this protocol uses in a specific sense;
consult it whenever a term is load-bearing.

Three chapters in `.standards/protocol/` hold rules that apply only in some
situations. Read a chapter before acting whenever its condition holds. If a
chapter is missing, stop and report an incomplete runtime.

| Chapter             | Sections                                                                                                                                                                                                                                    | Read when                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `user-decisions.md` | Choose the next cycle's mode, Standalone Documenter entry, Start a cycle, Change completion policy, Switch verification cadence, Promote an expedited cycle, Sign off, Cancel an active cycle, Greenfield Bootstrap Cancellation, Cycle IDs | No cycle is active (`Active Work.Id` is `UNSET`, or `WorkflowState` is `SIGNED_OFF` or `CANCELLED`), or the user invokes Documenter for a standalone documentation request, asks to choose the next cycle's mode, select or withdraw a completion policy, switch verification cadence, or start, promote, sign off, or cancel a cycle, or `Handoff.Kind` is `COMPLETION_CHANGE`, or `Active Work.PendingVerificationCadence` is not `NONE`, or `Active Work.BlockedOn` holds a pending approval to reset the workflow for a greenfield bootstrap cancellation |
| `expedited.md`      | Expedited Cycle Contract, Expedited Promotion, Outstanding Obligations                                                                                                                                                                      | `CycleMode` or `PendingCycleMode` is `EXPEDITED`, `EXPEDITED` is requested or being considered, `Active Work.PromotionReason` is not `NONE`, or `Outstanding Obligations` is active                                                                                                                                                                                                                                                                                                                                                                           |
| `installation.md`   | Installed Runtime Contract, Running the CLI, Project Reset, Project Uninstallation                                                                                                                                                          | Before running the `standards` CLI, including for a greenfield bootstrap cancellation, or when the user asks about installing, upgrading, resetting, or uninstalling                                                                                                                                                                                                                                                                                                                                                                                          |

## Roles

```text
Role
- SCOPER
- TESTER
- ARCHITECT
- NAVIGATOR
- DEVELOPER
- AUDITOR
- REVIEWER
- DOCUMENTER
- SYNCHRONIZER
```

A role owns decisions or artifacts; it is not necessarily a workflow state.
Workflow role skills are invoked explicitly by the user. `WorkflowState`
determines whether an invoked role may perform role-owned workflow work; it does
not dispatch a skill automatically. Where supported, client installation must
prevent implicit model invocation of workflow role skills. Read-only discovery
of a role's invocation options does not invoke that role, authorize role-owned
work, or change workflow state.

`NAVIGATOR` is explicitly invoked, strictly non-mutating, and outside the
workflow state machine. Its **Navigator Boundary** below applies instead of
workflow entry, persistence, completion, and handoff requirements.

State ownership governs role-owned work, not protocol coordination. **User
Decisions and Intervention** defines the control-plane transitions that an
explicit user instruction authorizes regardless of which role owns the current
state. An active workflow role may also `PROMOTE` without separate user
authorization when an expedited cycle can no longer safely remain expedited.

## Project Modes

```text
ProjectMode
- GREENFIELD
- BROWNFIELD
```

- `GREENFIELD`: no meaningful pre-existing project implementation must be
  treated as established baseline.
- `BROWNFIELD`: meaningful implementation already exists and workflow work must
  understand, preserve, extend, repair, or otherwise depend on it.

Choose the initial mode from project state at installation. `GREENFIELD` is
temporary: during the initial greenfield cycle, Developer must permanently
change `.standards/MODE.md` to `BROWNFIELD` as soon as Developer observes and
verifies that the active cycle has successfully created or materially modified a
project implementation artifact. The trigger is implementation existence, not
authorship: it applies equally to code written by Developer and code applied by
the user during Developer collaboration. Workflow metadata and role-owned
planning, context, test, review, or documentation artifacts do not count as
project implementation. The mode never reverts, including during recovery; only
**Project Reset** (in `.standards/protocol/installation.md`) chooses it again,
from `--mode` or the project's contents at that time.

For a `STANDARD` cycle:

- `GREENFIELD` starts in `SCOPING`.
- `BROWNFIELD` starts in `AUDITING`.

Before the first greenfield audit, missing Auditor-produced project context is
intentional. Treat it as a `PROJECT_CONTEXT` failure only when the active role
actually requires context that cannot be established from completed upstream
artifacts and known project constraints.

Cancellation while still `GREENFIELD` follows **Greenfield Bootstrap
Cancellation** in `.standards/protocol/user-decisions.md`, which resets the
workflow. Cancellation after the permanent transition to `BROWNFIELD` enters
terminal `CANCELLED`.

## Cycle Modes

```text
CycleMode
- UNSET
- STANDARD
- EXPEDITED
- DOCUMENTATION
```

`ProjectMode` describes the persistent implementation baseline. `CycleMode`
records the assurance topology of the active cycle only. `PendingCycleMode`
records an explicit user preference for the next cycle before that cycle exists.
`PendingCycleRequest` and `PendingCycleBlockedOn` durably hold a next-cycle
request only when a pre-cycle decision prevents that request from becoming an
active cycle. All are persisted in `.standards/STATE.md`.

- `CycleMode: UNSET`: no cycle is active yet. It is a coordination value, not an
  execution topology.
- `CycleMode: STANDARD`: the active cycle uses the standard workflow for the
  current `ProjectMode`, with its completion boundary selected by
  `Active Work.CompletionPolicy` under **Completion Policies**.
- `CycleMode: EXPEDITED`: the active cycle uses the bounded brownfield path that
  intentionally omits Scoping, Architecture, Auditing, Testing, Documentation,
  Final Review, and Synchronization unless promoted.
- `CycleMode: DOCUMENTATION`: the active cycle uses the brownfield documentation
  path under **Documentation Cycle Contract**, retaining Auditing, Scoping,
  Architecture, Documentation, Final Review, and Synchronization while omitting
  Development, Testing, and Implementation Review.
- `PendingCycleMode: UNSET`: no explicit next-cycle preference is persisted.
- `PendingCycleMode: STANDARD | EXPEDITED | DOCUMENTATION`: explicit user
  preference for the next cycle. It is not an active topology and must be
  validated when consumed.
- `PendingCycleRequest: UNSET | <request>`: normally `UNSET`; stores the actual
  next-cycle request only while a pre-cycle decision blocks cycle creation.
- `PendingCycleBlockedOn: NONE | <question>`: unresolved pre-cycle user decision
  for `PendingCycleRequest`, otherwise `NONE`. `PendingCycleRequest` and
  `PendingCycleBlockedOn` are paired: the request is `UNSET` exactly when the
  blocker is `NONE`.

Cycle-mode rules:

1. Installation initializes `CycleMode: UNSET`, `PendingCycleMode: UNSET`,
   `PendingCycleRequest: UNSET`, and `PendingCycleBlockedOn: NONE`. Terminal
   `SIGNED_OFF` and retained `CANCELLED` states reset `CycleMode` to `UNSET`
   after the completed or cancelled cycle's mode-specific transition rules have
   been applied; all pending-cycle fields must be clear at that point.
2. No role-owned workflow work may proceed with `CycleMode: UNSET`. A cycle's
   mode is chosen, validated, and persisted when it starts, under **Start a
   cycle** in `.standards/protocol/user-decisions.md`; the user sets a pending
   preference under **Choose the next cycle's mode** in
   `.standards/protocol/user-decisions.md`.
3. `GREENFIELD` supports only `STANDARD`. Pending `EXPEDITED` and
   `DOCUMENTATION` preferences are invalid in `GREENFIELD` and must not be
   persisted. Documentation existence does not determine project mode.
4. `BROWNFIELD` supports all three execution modes. A standalone Documenter
   request asks for `DOCUMENTATION` before eligibility and preference checks,
   under **Standalone Documenter entry** in
   `.standards/protocol/user-decisions.md`. Ineligible or conflicting entry
   requires a pre-cycle decision, including in `GREENFIELD`; it never defaults
   to `STANDARD`. Otherwise, without an explicit or pending mode choice,
   `STANDARD` is the default, except that an explicit Developer invocation for a
   sufficiently bounded brownfield implementation change may select `EXPEDITED`.
5. The rules for keeping, promoting, and completing an expedited cycle are in
   **Expedited Cycle Contract** and **Expedited Promotion**, both in
   `.standards/protocol/expedited.md`.
6. A documentation cycle has one fixed completion contract and no promotion or
   in-place mode conversion. Work requiring an omitted role follows
   **Documentation Cycle Contract**; selecting a mode for the next cycle never
   changes an active cycle.

## Completion Policies

```text
CompletionPolicy
- NONE
- FULL_DELIVERABLE
- IMPLEMENTATION_REVIEWED
```

`Active Work.CompletionPolicy` is required in `.standards/STATE.md`. A missing,
invalid, or repeated value is an inconsistency. It selects a standard cycle's
completion boundary independently of project mode, role mode, and verification
cadence; it does not change `CycleMode` or the ownership of any work.

| Policy                    | Meaning                                                                                                                                                          |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `NONE`                    | No standard completion policy applies: no cycle has started, or the cycle is expedited or documentation-only. It is invalid for an active standard cycle.        |
| `FULL_DELIVERABLE`        | Complete the standard workflow through documentation, final review, and synchronization before user sign-off.                                                    |
| `IMPLEMENTATION_REVIEWED` | Complete all standard upstream work and the implementation review, then satisfy the early-closure gate under **Standard Cycle Completion** before user sign-off. |

Both standard policies support cycles that start in `GREENFIELD` or
`BROWNFIELD`. The permanent project-mode transition remains unchanged. Absence
of project documentation never selects a policy or proves eligibility for early
closure. Scope, architecture, verification, and review records remain required
workflow evidence even when project-facing documentation is not required.

Policy lifecycle:

- Installation and project reset initialize `CompletionPolicy: NONE`.
- Each new standard cycle initializes `FULL_DELIVERABLE` unless the user
  explicitly selects `IMPLEMENTATION_REVIEWED` for that cycle. A prior cycle's
  policy is never inherited.
- Each expedited cycle uses `NONE` and retains its existing completion contract.
  Promotion to standard initializes `FULL_DELIVERABLE`; promotion itself never
  authorizes the shorter completion boundary.
- Each documentation cycle uses `NONE` and must satisfy **Documentation Cycle
  Contract** through Synchronizer. Standard completion policies cannot shorten
  or extend this route; `NONE` does not waive its required gates.
- Only an explicit user choice authorizes selecting or withdrawing
  `IMPLEMENTATION_REVIEWED`. A policy choice does not revise acceptance
  conditions, resolve findings, clear blockers, or discharge recovery and
  outstanding obligations.
- Sign-off and retained cancellation preserve the cycle's policy in
  `Active Work`, even though `CycleMode` becomes `UNSET`. It is historical
  context until the next cycle replaces it; a terminal cycle cannot be reopened
  by changing its policy.

The shorter policy omits the normal Documenter, final Reviewer, and Synchronizer
phases and their completion guarantees. It does not transfer their work to
Reviewer, permit fabricated completion records, or remove those owners from
standard failure and recovery routing. Required corrective work still returns to
its owner. Recovery routing takes precedence over the normal completion
boundary.

Selection, withdrawal, and permitted policy-change routes follow **Change
completion policy** in `.standards/protocol/user-decisions.md`. A policy choice
alone is not user sign-off or an invocation of a workflow role.

## Documentation Cycle Contract

`DOCUMENTATION` is valid only with `ProjectMode: BROWNFIELD` for a request to
document existing implementation. It may update existing documentation or create
missing documentation. No existing guide or prior cycle record is required for
entry. A greenfield request cannot enter this mode merely because documentation
files exist; do not change project mode to make it eligible.

The fixed forward route is **Documentation Brownfield** under **Forward
Transitions**. Auditor establishes relevant existing behavior and project
constraints; Scoper defines audiences, documentation targets, editing
boundaries, and acceptance conditions; Architect records the existing technical
contracts and constraints needed to document them accurately. Architect accounts
for each acceptance condition with relevant technical coverage or an explicit
no-architectural-impact disposition, without inventing implementation work.
Documenter produces documentation and checked evidence. Final Reviewer assesses
its accuracy and acceptance coverage independently. Synchronizer reconciles the
current deliverable and the applicability of those assessments before sign-off
readiness. Existing state ownership, explicit role invocation, handoff, artifact
provenance, and independent Reviewer session rules still apply.

Permitted project edits are Documenter-owned prose, guides, project agent
guidance outside managed framework blocks, and ordinary comments and docstrings.
Examples must describe evidenced existing behavior. Comments interpreted as
tooling directives, runtime metadata, or executable logic are outside this
boundary. Respect established documentation generation workflows and verify
their outputs; a documentation request does not authorize changing behavior,
tests, fixtures, configuration, or tooling to make documentation or checks pass.
Other roles retain their existing artifact ownership and protocol coordination
permissions.

Development, Testing, and Implementation Review are intentionally omitted, not
pending work. Their plans, reports, and completion claims are not prerequisites
for documentation-cycle gates. Existing source, tests, and prior assessments may
be inspected as supporting evidence, but do not fabricate current-cycle records
or claim formal implementation verification. Documentation checks provide
Documenter-owned evidence; final review and synchronization assess and reconcile
it without manufacturing another role's evidence.

Before entering `AWAITING_USER_SIGNOFF`, all six included roles must have passed
their applicable full gates for the current work. The scope and architecture
references, current Auditor context, documentation record, `FINAL_DELIVERABLE`
review report, and synchronization record must be present and applicable. Every
current `AC-NNN` and relevant technical criterion must have sufficient current
evidence. No unresolved material finding, documentation gap, dependency, or
blocking user question may remain. `CompletionPolicy`, `Development`,
`PromotionReason`, `PendingVerificationCadence`, and `BaselineReconciliation`
must all be `NONE`; recovery must be inactive with an empty stack and
outstanding obligations inactive. An assessed no-change documentation result is
valid; it does not skip final review or synchronization. Readiness still
requires user acceptance, with evidence freshness rechecked at sign-off.

Failure and rework within this boundary use **Recovery Mechanics** among the
included states only; `REVIEW` targets `REVIEWING_FINAL`. Corrections must
re-establish affected downstream evidence before readiness. A defect or changed
request requiring implementation, formal testing, or implementation review does
not authorize entry into an omitted state or automatic promotion. Persist the
evidence and required user decision in `Active Work.BlockedOn` when it prevents
the documentation contract from being met. The user may keep an achievable
documentation-only scope, or explicitly cancel this cycle and start a separate
implementation cycle. Preserve the active cycle, request, and recovery while
that decision is unresolved; unrelated observations do not alone block
documentation completion. Cancellation retains changed project documentation for
later Auditor baseline reconciliation under **Start a cycle** in
`.standards/protocol/user-decisions.md`.

## Workflow States

```text
WorkflowState
- SCOPING
- ARCHITECTING
- AUDITING
- DEVELOPING
- TESTING
- REVIEWING_IMPLEMENTATION
- DOCUMENTING
- REVIEWING_FINAL
- SYNCHRONIZING
- AWAITING_USER_SIGNOFF
- SIGNED_OFF
- CANCELLED
```

`.standards/STATE.md` records exactly one `WorkflowState`, one `CycleMode`, and
one set of pending-cycle coordination fields: `PendingCycleMode`,
`PendingCycleRequest`, and `PendingCycleBlockedOn`. `SIGNED_OFF` and `CANCELLED`
are terminal and mean there is no active cycle. In a terminal state, `CycleMode`
is `UNSET`; persisted `Active Work` data describe the most recent cycle for
traceability until `NEW_CYCLE` replaces them, while the pending-cycle fields may
independently describe an explicit next-cycle preference or a blocked next-cycle
request.

The owning role for each state is:

| Workflow state             | Owning role     |
| -------------------------- | --------------- |
| `SCOPING`                  | `SCOPER`        |
| `ARCHITECTING`             | `ARCHITECT`     |
| `AUDITING`                 | `AUDITOR`       |
| `DEVELOPING`               | `DEVELOPER`     |
| `TESTING`                  | `TESTER`        |
| `REVIEWING_IMPLEMENTATION` | `REVIEWER`      |
| `DOCUMENTING`              | `DOCUMENTER`    |
| `REVIEWING_FINAL`          | `REVIEWER`      |
| `SYNCHRONIZING`            | `SYNCHRONIZER`  |
| `AWAITING_USER_SIGNOFF`    | User            |
| `SIGNED_OFF`               | User (terminal) |
| `CANCELLED`                | User (terminal) |

## Persisted Workflow State

`.standards/STATE.md` is the authoritative, branch-persisted, version-controlled
record needed to resume workflow work across sessions or agents. Before workflow
work, read `.standards/PROTOCOL.md`, `.standards/MODE.md`, and
`.standards/STATE.md`. Resume from persisted state, active work, handoff,
recovery context, and outstanding corrective obligations; do not infer a
different state from chat history or artifact presence.

`STATE.md` uses this shape:

```markdown
# S.T.A.N.D.A.R.D.S. Workflow State

`WorkflowState`: `ARCHITECTING` `CycleMode`: `STANDARD` `PendingCycleMode`:
`UNSET` `PendingCycleRequest`: `UNSET` `PendingCycleBlockedOn`: `NONE`

## Active Work

`Id`: `add-user-search-by-name-and-email-20260923T150000Z-a7f3c2e9` `Request`:
`Add user search by name and email.` `Scope`: `docs/scope/add-user-search.md`
`CompletionPolicy`: `FULL_DELIVERABLE` `Architecture`:
`docs/specs/add-user-search.md` `Development`:
`.standards/docs/development/add-user-search-by-name-and-email-20260923T150000Z-a7f3c2e9.md`
`PromotionReason`: `NONE` `AuditTarget`: `NONE` `BlockedOn`: `NONE`
`PendingVerificationCadence`: `NONE`

`BaselineReconciliation`: `NONE`

## Handoff

`Kind`: `FAILURE` `From`: `TESTING` `FailureType`: `ARCHITECTURE` `Reason`:
`Retry behavior is not defined by the current technical design.`

## Recovery

`Active`: `true`

### Frame 1

`From`: `TESTING` `Owner`: `ARCHITECTING` `FailureType`: `ARCHITECTURE`
`Reason`: `Retry behavior is not defined by the current technical design.`
`ResumeAt`: `TESTING` `RerunThrough`: `NONE`

## Outstanding Obligations

`Active`: `false`
```

### Branches and merges

Commit `.standards/` like any other project files. `STATE.md` belongs to the
branch it is committed on, and each branch carries at most one active cycle,
which anyone who checks out the branch continues where it was left.

- To start over on a branch, including one created from a branch with an active
  cycle, the user cancels the cycle or runs **Project Reset** in
  `.standards/protocol/installation.md`.
- If the main branch has STANDARDS installed, once a branch's cycle is signed
  off or cancelled, the user runs **Project Reset** (in
  `.standards/protocol/installation.md`) before merging. The main branch then
  keeps a fresh installation with an accurate `MODE.md` instead of one branch's
  workflow state, Auditor context, and cycle records, and every branch created
  from it starts with no cycle.
- If the main branch has no STANDARDS installation, the user may instead run
  **Project Uninstallation** (in `.standards/protocol/installation.md`) on the
  feature branch before merging. Uninstallation removes the branch's runtime and
  cycle records while leaving project work outside the installation.

Merging two branches that both changed `.standards/` without a reset usually
conflicts in `STATE.md`. The user resolves it by keeping exactly one cycle; the
other cycle stops being tracked, and its records stay under `.standards/docs/`
for the user to decide about. While `.standards/` has unmerged files or conflict
markers, a workflow role stops, tells the user which cycles are involved, and
waits; it never resolves the conflict by choosing a side.

### Manual edits

STANDARDS changes its runtime files and cycle records only through legal
protocol transitions and the runtime tools. Editing them by hand is unsupported,
except to resolve a merge conflict as described above; a user who edits them
otherwise is responsible for the result. `check` reports many, but not all, of
the problems such edits cause.

## Navigator Boundary

Navigator helps the user understand existing work, investigate questions, and
check comprehension. It may run in any workflow state, including user-owned and
terminal states, with any cycle mode or no active cycle. It owns no workflow
state or persisted artifact and has no workflow completion gate.

- Navigator persists nothing. It creates no quiz scores, preferences, or
  summaries, and conversation context stays in the conversation.
- The control-plane permissions elsewhere in this protocol do not apply while
  Navigator runs. It never initializes cycles, generates IDs, records blockers,
  selects or promotes cycle modes, signs off, cancels, or creates failure,
  recovery, or other state-changing handoffs. It may explain these actions and
  their owners, but performing them requires leaving Navigator.

The Navigator skill defines the full operating boundary, including its
non-mutation rules, diagnostic safety requirements, explanation standards, and
interaction modes.

## Instruction Layering and Conflicts

Role-specific precedence may order **compatible** guidance, such as applying a
repository formatter before generic style preferences. Precedence must never be
used to silently resolve a material conflict among the protocol or installed
integration contract, explicit user constraints, completed role-owned workflow
artifacts, project-specific instructions, or repository-enforced/toolchain
constraints.

When authorities materially conflict:

1. If the conflict is a defect or changed requirement owned by a workflow role,
   route it through the protocol's normal failure, recovery, or `USER_REWORK`
   mechanics to that owner.
2. If no workflow owner can resolve the contradiction without overriding another
   authority, or project instructions conflict with the protocol/integration
   contract, stop workflow work and require user resolution.
3. Do not weaken an authority, invent an exception, or choose a winner merely
   because one source appears earlier in a role-specific precedence list.

Role skills should reference these rules rather than redefine
conflict-resolution semantics locally.

## Runtime Tools and Hooks

Installation places these tools in `.standards/bin/`. They run with Node.js.
Agents call them internally; users do not need an extra post-install command or
setup step. Roles use them instead of doing these steps by hand:

| Command                                                              | Purpose                                                                                                               |
| -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `node .standards/bin/cycle.mjs new --request "<request>"`            | Generate a new cycle ID (see **Cycle IDs** in `.standards/protocol/user-decisions.md`).                               |
| `node .standards/bin/artifact.mjs init <TYPE>`                       | Create one of the active cycle's records with its provenance block and header (see **Workflow Artifact Provenance**). |
| `node .standards/bin/id.mjs next <AC\|DEV\|F\|D\|DOC> <file>`        | Print the next free identifier for a record.                                                                          |
| `node .standards/bin/check.mjs`                                      | Check the runtime files and the active cycle's records. It only reads files.                                          |
| `node .standards/bin/invocation.mjs <role> --client <client> --json` | Discover a role's invocation options without changing files (see **Next-Role Invocation Options**).                   |

A workflow role runs `check` before its first substantive work and again before
every state-changing handoff. For each problem it reports:

1. If the problem is in work the current role owns, fix it before continuing.
2. If another role owns it, route it through **Failure Handoffs**.
3. If no role can fix it, for example a merge conflict or a hand-edited file,
   stop and report it to the user.

A forward handoff requires that `check` reports no problem in files the current
role owns. `check` finds mechanical errors such as malformed cycle IDs, broken
provenance, or missing acceptance coverage; it does not replace a role's
completion gate. If a tool is missing or fails, stop and report it; do not do
its step by hand. Invocation discovery is the presentation-only exception:
follow **Next-Role Invocation Options** for unavailable or incomplete results.

A user instruction not to run commands covers tests, builds, scripts, package
managers, git (including read-only git commands) and any other command, with
these exceptions. A workflow role still runs the runtime tools above as this
protocol requires; if the user explicitly forbids them too, apply **Instruction
Layering and Conflicts**. Viewing, listing and searching files without changing
them is not running a command, whatever tool the client uses, and neither is
editing files with the client's file tools. Say which commands were not run and
what they would have established, in the role's record when it keeps one.

Installation can also add a stop hook for Claude Code and Codex (see **Installed
Runtime Contract** in `.standards/protocol/installation.md`). When an agent
finishes a turn and workflow files have uncommitted changes, the stop hook runs
`check`. If it finds problems, it sends the agent back once with the list, and
the agent handles them as described above. Because the turn may have ended with
a handoff, the hook does not hold the current state's own `COMPLETE` records to
full acceptance coverage; the role that owns the state reconciles them when it
starts, and its own `check` before handing off still includes them. During
recovery, when `check` otherwise holds only the current state's `COMPLETE`
records, the hook instead holds those of the state named in `Handoff.From` after
a `FORWARD`, `CHECKPOINT`, `RESUME`, or `FAILURE` handoff. Apply the gate for
the persisted assignment: a checkpoint or scoped corrective return does not
assert full phase completion. It holds none while in `SCOPING`, because Scoper
may have changed the acceptance conditions since that handoff. The checker must
allow legitimate partial records during an increment assessment or scoped
correction while preserving full completion checks at Reviewer entry.

Navigator may run `check` and `invocation`, because they change nothing.
Discovery needs no active cycle. When a tool or hook reports problems during
Navigator work, Navigator reports the relevant limits and changes nothing.

## Verification Cadence

Verification cadence controls when Developer hands implemented outcomes to
Tester in a `STANDARD` cycle. It is independent of Developer's `AUTONOMOUS`,
`STEPWISE`, or `CODE_WITH_ME` collaboration mode and does not add a workflow
state or change `CycleMode`.

Developer persists exactly one effective `Verification Cadence` in its plan:

- `AFTER_IMPLEMENTATION`: the default. Finish all approved implementation, then
  hand off to Tester for full verification.
- `INCREMENTAL`: alternate implementation and independent verification of
  testable increments, then establish full completion before Reviewer.

`Verification Cadence` and `Current Increment` are required. Initialize new
plans with `AFTER_IMPLEMENTATION` and `NONE`, respectively. Missing, invalid,
repeated, or contradictory values are defects. `EXPEDITED` supports only
`AFTER_IMPLEMENTATION`. Initial selection and later switches follow **Switch
verification cadence** in `.standards/protocol/user-decisions.md`, including
promotion when required.

### Testable Increments

An increment is an observable implementation outcome ready for independent
assessment, usually one AC or a small group of dependent ACs. It may require
several `DEV-NNN` steps. Increment references do not redefine acceptance or
imply that every referenced AC is fully satisfied by that increment.

The existing development plan holds ordered `### Increment N` entries, starting
at 1, with their `Development Steps`, `Acceptance`, and concrete
`Ready Outcome`. Their numbers are plan-local scheduling references, not new
requirement IDs. Preserve existing numbers; append new entries rather than
renumbering assessed increments. An AC or development step may be referenced by
more than one increment when their testable outcomes genuinely require it.
Respect approved dependencies; do not manufacture a checkpoint for an outcome
that cannot yet be assessed independently.

Developer alone selects and advances `Current Increment`, a defined positive
increment number or `NONE`, in its plan. Tester records assessments in the
existing cycle verification report and never edits the plan. Before advancing,
Developer independently reconciles the matching Tester result and current
inputs. A report's existence or an earlier pass alone does not authorize
progression. `NONE` is valid before scheduling, after switching to
`AFTER_IMPLEMENTATION`, or at full completion. Every checkpoint requires a
defined increment. Historical handoffs follow **Handoff**; partial acceptance
coverage follows **Acceptance Traceability**.

### Checkpoint Handoffs

With effective cadence `INCREMENTAL` and no active recovery, `CHECKPOINT`
permits only `DEVELOPING -> TESTING` and `TESTING -> DEVELOPING`. Set
`FailureType: NONE`. It records a completed assignment gate, not full phase
completion, and creates no recovery frame. Persist claims and actual evidence in
the owning records before changing state; independent-session and
explicit-role-invocation rules still apply. A pending cadence request does not
change the active assignment.

Before Developer hands off an increment:

- the plan has explicit approval under the normal approval rules;
- `Current Increment` names a defined, testable outcome; all implementation
  steps and dependencies needed for that outcome are `DONE`;
- relevant self-checks are satisfactory and the assessed implementation content
  and readiness claim are saved;
- no unresolved defect, Developer-owned outstanding obligation, unapproved
  material deviation, or user question blocks the assignment;
- `check` reports no problem in Developer-owned files.

Tester independently reconstructs that assignment, selects `VERIFY` or
`REVERIFY` using the existing input-change rules, records
`Assessment Purpose: INCREMENT` and `Assessment Target: Increment N`, and
assesses the outcome plus affected earlier behavior. Both checkpoint directions
identify the increment concisely in `Handoff.Reason`; the owned artifacts hold
readiness and assessment evidence. Tests may be updated, replaced, or removed
when appropriate to the current contract; preserve required coverage, evidence
history, and cycle-wide scenario allocations. Source, test, fixture, dependency,
configuration, or contract changes can invalidate earlier results.

Before Tester returns a checkpoint to Developer:

- the selected outcome and affected completed behavior have sufficient current
  evidence and the report identifies assessed content, actual results, and any
  justified evidence reuse;
- no required assignment check is unrun, failed, flaky, blocked, or uncovered,
  and no unresolved defect, Tester-owned obligation, or user question blocks it;
- the increment assessment and remaining acceptance work are saved, with the
  report still `IN_PROGRESS` while full verification remains unfinished;
- `check` reports no problem in Tester-owned files.

A failed checkpoint follows normal failure routing. During recovery, use the
saved `FAILURE`, `FORWARD`, and `RESUME` route rather than ordinary checkpoint
exchanges; see **Implementation and Verification Recovery Gates**. Developer
resumes under its collaboration mode's permissions and pauses.

### Full Verification Boundary

For normal completion, when all current approved steps are `DONE` and every
other Developer completion-gate condition passes, mark the plan `COMPLETE`, set
`Current Increment: NONE`, and hand off to full Tester verification. Recovery
follows **Recovery Mechanics**. Tester reconciles every current AC and relevant
technical criterion against the final implementation, reusing only still-valid
evidence and choosing regression breadth from affected dependencies and risk.

Only the full Tester gate permits a `COMPLETE` verification report and normal
handoff to Reviewer, with `Assessment Purpose: FULL` and
`Assessment Target: NONE`. Before either a `FORWARD` or recovery `RESUME` enters
a Reviewer state, the full applicable Developer and Tester gates must hold for
the current implementation, even if another recovery frame remains active.
Existing implementation-stage dependencies on later roles remain permitted under
those gates. Checkpoint passes, targeted corrections, and report labels cannot
replace full completion.

## Forward Transitions

The following normal forward routes use the gates defined in **Handoff Rules**.

### Standard Greenfield

With `CompletionPolicy: FULL_DELIVERABLE`:

```text
SCOPING
-> ARCHITECTING
-> AUDITING
-> DEVELOPING
-> TESTING
-> REVIEWING_IMPLEMENTATION
-> DOCUMENTING
-> REVIEWING_FINAL
-> SYNCHRONIZING
-> AWAITING_USER_SIGNOFF
```

### Standard Brownfield

With `CompletionPolicy: FULL_DELIVERABLE`:

```text
AUDITING
-> SCOPING
-> ARCHITECTING
-> DEVELOPING
-> TESTING
-> REVIEWING_IMPLEMENTATION
-> DOCUMENTING
-> REVIEWING_FINAL
-> SYNCHRONIZING
-> AWAITING_USER_SIGNOFF
```

For brownfield standard work, `ARCHITECTING -> DEVELOPING` requires valid
project context for the active cycle. Missing, materially incomplete, incorrect,
or unexpectedly invalidated context routes to `AUDITING`. Planned active-cycle
implementation changes do not alone make context stale.

### Standard Implementation-Reviewed Completion

With `CompletionPolicy: IMPLEMENTATION_REVIEWED`, either standard route keeps
its entire prefix through `REVIEWING_IMPLEMENTATION` and replaces only the
remaining forward tail with:

```text
REVIEWING_IMPLEMENTATION
-> AWAITING_USER_SIGNOFF
```

This handoff requires the early-closure gate under **Standard Cycle
Completion**, not just a passing implementation review. Unmet closure
requirements block this handoff and follow normal ownership and failure rules.
User acceptance is still required to reach `SIGNED_OFF`.

### Documentation Brownfield

```text
AUDITING
-> SCOPING
-> ARCHITECTING
-> DOCUMENTING
-> REVIEWING_FINAL
-> SYNCHRONIZING
-> AWAITING_USER_SIGNOFF
```

This topology is valid only for `ProjectMode: BROWNFIELD` with
`CycleMode: DOCUMENTATION` and `CompletionPolicy: NONE`, under **Documentation
Cycle Contract**. Architecture requires valid current-cycle project context
before handing off to Documenter. User acceptance is still required to reach
`SIGNED_OFF`.

### Expedited Brownfield

```text
DEVELOPING
-> REVIEWING_IMPLEMENTATION
-> AWAITING_USER_SIGNOFF
```

This topology is valid only for `ProjectMode: BROWNFIELD` with
`CycleMode: EXPEDITED`, under **Expedited Cycle Contract** in
`.standards/protocol/expedited.md`.

## Handoff Rules

1. Forward handoff requires the current completion gate to pass. Scoped recovery
   reruns use the explicitly permitted applicable gate under **Implementation
   and Verification Recovery Gates**. A `CHECKPOINT` uses its assignment gate
   under **Checkpoint Handoffs** and does not assert the full completion gate
   passed.
2. A role does not repair work it does not own; failures route under **Failure
   Handoffs**.
3. A handoff identifies its target role or workflow state. A failure handoff
   also identifies `FailureType`; `REVIEW` targets the Reviewer state matching
   the affected `ReviewKind`.
4. No role silently changes another role's artifact or decision.
5. Corrective routing and reruns follow **Recovery Mechanics**, which skills
   must not redefine; user decisions follow **User Decisions and Intervention**;
   and promotion follows **Expedited Promotion** in
   `.standards/protocol/expedited.md` and is not encoded as `FAILURE`.
6. Every state-changing transition persists all applicable `WorkflowState`,
   `CycleMode`, pending-cycle, `Active Work`, `Handoff`, `Recovery`, and
   `Outstanding Obligations` changes before further role work and before
   presenting any next-role invocation.
7. After a legal transition to a different workflow role, provide a concise
   copy/paste invocation for that role with **Next-Role Invocation Options**,
   unless the same user instruction already explicitly invoked it and it will
   continue immediately. Do not emit one when the workflow remains with the same
   role, a blocking question is unresolved, the result is
   `AWAITING_USER_SIGNOFF`, `SIGNED_OFF`, or `CANCELLED`, or Navigator is used.
8. The invocation is convenience only; `STATE.md` and role-owned artifacts
   remain authoritative. Use the active client's syntax and avoid duplicating
   authoritative workflow content unless needed for disambiguation:

<!-- markdownlint-disable MD013 -->

```text
Next role: <Role>

Codex:       $<skill> Continue the active workflow from `.standards/STATE.md`. Read the persisted Active Work, relevant project context and owned artifacts, and recovery context before proceeding.
Claude Code: /<skill> Continue the active workflow from `.standards/STATE.md`. Read the persisted Active Work, relevant project context and owned artifacts, and recovery context before proceeding.
```

<!-- markdownlint-enable MD013 -->

Emit only the active-client line. When recovery is active, replace “active
workflow” with “active recovery” and explicitly direct the role to read the
active recovery frame.

At `AWAITING_USER_SIGNOFF`, present the applicable user actions rather than a
next-role invocation.

### Next-Role Invocation Options

When a next-role invocation is required under **Handoff Rules**, the current
role runs the read-only discovery helper after persisting the transition and
before presenting the message:

```sh
node .standards/bin/invocation.mjs <role> --client <client> --json
```

Use the destination skill identifier for `<role>` and the active client, `codex`
or `claude`, for `<client>`. This is an internal agent operation, not a user
setup step or a command to include in the copy/paste invocation. Installation
includes the helper and its schema. It discovers the installed role/mode
metadata and current user-style filenames on each call. Do not keep a second
mode/style catalog, infer styles from identity, or load unselected style
contents.

Read the JSON dispositions, including `catalogStatus`, group selections, option
`selectability`, saved values/arguments, style inventory and lock, and
diagnostics. Exit status 0 does not establish that every option is selectable or
that a workflow gate passed. Read a referenced `selectionRules` section only
when needed to explain an option's restrictions; this does not invoke the next
role or authorize performing its assessment or procedure. The detailed output
contract is in `.standards/bin/schemas/invocation-metadata.md`.

Keep the base invocation from **Handoff Rules** unchanged, including its
persisted-input directions, recovery wording, and any independent-session prefix
and review kind. Put a compact summary beside it, separating:

- **You can choose:** options whose `selectability` is `user`, with a short
  description and optional text the user can append to the invocation. Name the
  group when needed to distinguish collaboration from a target, and supply a
  placeholder for any required file, directory, capability, or area argument. An
  `assessment-required` user option is a request subject to the receiving role's
  assessment, not a promise that the requested boundary is eligible.
- **What happens next:** known current selections, applicable saved target
  details, and any assessment the receiving role still needs to make. Never
  present state or assessment modes as user switches, or an assessed candidate
  as a decided outcome. A saved value cannot override a state-selected value or
  a current condition. A saved assessed mode is resume context only.

Use the discovered identifiers and descriptions. For example, a discovered
`STEPWISE` collaboration choice may be expressed as an optional addition
`Use STEPWISE collaboration.`; do not append it to the base invocation on the
user's behalf. A bare continuation preserves applicable choices, including
intent retained in the active request/scope. Mention a `defaultForNew` only as a
fallback for genuinely new work with no applicable choice; missing records do
not establish new work. If a saved choice is unresolved or invalid, describe
that uncertainty instead of replacing it with the default.

Include a few discovered user-style identifiers when the inventory is complete
and the selection is unlocked; label samples as examples if more exist. Use the
helper's non-null `selector` for copy/paste examples such as
`Use user style <selector>.`, or clear an unlocked selection with
`Use user style NONE.`. An entry with a null `selector` is inventory information
only, not a confirmed selectable choice; use `selectorReason` to distinguish
ambiguous names, record-syntax restrictions, and an unverified inventory. Style
files are role-specific; never carry a style from the current role into the next
role automatically. For conversation bindings, use only an explicit choice for
that role still present in the conversation; a different conversation needs the
user to name it again.

If a style is locked, report the retained identifier, including `NONE`, and omit
change/clear suggestions. Developer's style stays locked after first plan
approval even during recovery or a revised plan; collaboration modes remain
separately selectable. If a lock is unknown, report that change eligibility is
unresolved. Discovered filenames may be named as inventory information, but not
as confirmed selectable styles. Only the receiving role can establish that a
missing record belongs to genuinely new work. A missing locked style requires
restoration under **User Styles**, not substitution or clearing.

Omit empty categories and irrelevant alternatives. Keep small groups of
selectable choices complete; for a long inventory, label the displayed choices
as examples and point to the discovered role/style location for the rest. Do not
dump metadata, diagnostics, all mode procedures, or a configuration
questionnaire. Optional choices do not add an approval gate, revise scope, waive
remaining required work, or invoke a role on their own. Do not pause an
already-authorized immediate continuation to solicit choices; **Independent
Assessment Sessions** still takes precedence.

If the helper cannot run or its catalog is unavailable, provide the normal
invocation with a brief note that optional choices could not be verified. Do not
invent an option list or ask the user to run the helper. With a complete catalog
but unresolved context, present only independently verified choices and qualify
the affected values; `complete: false` need not hide usable parts of the result.
Genuine workflow blockers still follow their existing rules. Discovery neither
repairs framework files nor changes a selection.

### Independent Assessment Sessions

Tester and Reviewer reconstruct assessments from persisted artifacts and
repository evidence. Before a handoff to either role, persist the applicable
state, owned artifacts, claims, assessed content identities, actual commands and
results, limitations, unresolved findings and dependencies, and resume context.
Record detail in the owning artifact, not in `Handoff.Reason`. The receiving
role must be able to work without the authoring conversation.

Every handoff entering `TESTING`, `REVIEWING_IMPLEMENTATION`, or
`REVIEWING_FINAL`, including recovery and corrections, keeps the normal
invocation with its persisted-input and recovery-frame directions, adds the
independent-session prefix below, and names the review kind when entering
Reviewer. Even when the user invoked both roles together, the
immediate-continuation exception never runs an assessment in a conversation that
contains the prohibited authoring history.

A skill cannot erase chat history or certify session freshness without client
support. Known prohibited history requires persisting missing context within
existing ownership, then stopping before the formal assessment to request a
fresh session. When history or client metadata is unavailable, state that
limitation and proceed from persisted evidence, without inventing an
attestation, blocking automatically, or asking for routine confirmation. The
assessing role may resume its own interrupted assessment in a conversation
separate from the prohibited authoring work. Both roles reload persisted state
and current inputs on every resumption; two open chats do not authorize
simultaneous role-owned workflow work. Developer and Tester must operate on the
same active cycle and checkout.

### Independent Tester Session

Apply **Independent Assessment Sessions**. Prefix a Tester invocation with “Use
the existing independent Tester chat, or open a fresh chat separate from
Developer's implementation conversation, then run:”. Developer implementation
history is prohibited for formal Tester work, including later increments and
targeted corrections. Reusing the Tester chat preserves independence only while
it contains no prohibited authoring history.

### Independent Reviewer Session

Apply **Independent Assessment Sessions**. Prefix a Reviewer invocation with
“Open a fresh chat separate from the conversations that produced the artifacts
under review, then run:”. The separation covers requirements, design, context,
implementation, tests and evidence, and documentation authoring, not just
Developer. Reviewer's own review history, including resuming its assessment and
correcting its own findings, is not prohibited history.

Also recommend that the user select a different model of equal or higher
capability than the one that produced the work, where known. This is advisory:
do not switch models automatically, guess model identities or capability
rankings, or add a routine author-model confirmation gate. Unknown model
metadata is a stated limitation, not a blocker, and session separation is
required even when no different model is available.

## Commit Message Guidance

After role work, if the role produced a meaningful atomic set of repository
changes, provide a suggested Git commit message. Do not create the commit unless
the user explicitly requests it. Coordination-only changes such as advancing
`STATE.md` do not justify a suggestion by themselves.

Use Conventional Commits, `<type>(<optional-scope>): <description>`, for example
`feat(search): implement user search` or
`docs(scope): define user search requirements`. Choose type and scope from the
actual changes, not the role or workflow state. Common types include `feat`,
`fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`, and `chore`. Keep the
description concise, imperative, and specific. Use breaking-change syntax or
footers only for genuinely breaking changes.

`NAVIGATOR` never suggests a commit. Other roles omit the suggestion when they
produced no meaningful committable changes. When both a commit suggestion and a
next-role invocation are emitted, present the commit suggestion first.

## Acceptance Traceability

These obligations apply to `STANDARD` and `DOCUMENTATION` cycles and do not
create a shared traceability artifact or add acceptance data to `STATE.md`.
`EXPEDITED` cycles do not fabricate Scoper-owned acceptance conditions or
substitute identifiers. If an expedited cycle is promoted, Scoper establishes
them when the standard topology reaches `SCOPING`.

In `DOCUMENTATION`, apply the ownership and identity rules below to the included
roles under **Documentation Cycle Contract**. Tester-specific scheduling and
evidence obligations do not apply; Documenter supplies documentation evidence,
final Reviewer assesses it, and Synchronizer reconciles it under the same ACs.

1. Scoper represents every verifiable in-scope completion obligation as one or
   more acceptance conditions with stable, unique `AC-NNN` identifiers for the
   active cycle. Do not combine separable obligations under one identifier when
   their satisfaction or verification evidence is established in different
   workflow phases. The identifier is a reference, not an ordering guarantee.
   Get each new identifier with `node .standards/bin/id.mjs next AC <scope>`.
   Define each condition as a Markdown list item, bulleted or numbered, that
   starts with its identifier followed by a colon or dash, for example
   ``- `AC-001`: Users can search by name.`` A table row, heading, or prose
   mention does not define a condition, and `check` reports an identifier the
   scope mentions without defining. This applies to a reused project scope
   document too: write its current conditions in this form.
2. During REPLAN, preserve an identifier when meaning is unchanged. New or
   materially replaced conditions receive previously unused identifiers. Removed
   or replaced identifiers remain in the scope's retired-identifier record: list
   items in the same form under a `## Retired Acceptance Identifiers` heading.
   Never renumber surviving identifiers or reuse retired ones within the cycle.
3. When a cycle reuses a canonical scope document that already holds another
   cycle's acceptance conditions, numbering continues from the highest
   identifier ever used in that document. Move the earlier cycle's conditions,
   including its retired identifiers, under a `## Previous Cycles` heading that
   stays the document's last section; the moved content may keep its own
   headings. Everything under that heading is history, not current obligations,
   and its identifiers are never used again in that document.
4. Scoper owns acceptance wording and meaning. Downstream roles reference IDs
   but do not redefine intent; material defects route to Scoper.
5. Architect accounts for every current ID with technical design coverage or an
   explicit no-architectural-impact disposition when satisfaction depends
   entirely on established nontechnical behavior or work owned by another
   workflow phase. Group IDs only when the same disposition applies; do not
   invent architecture or claim ownership of satisfaction that belongs to
   another workflow phase. When a cycle reuses a canonical design document that
   already holds another cycle's acceptance coverage or technical acceptance
   criteria, move them under a `## Previous Cycles` heading that stays the
   document's last section, as in rule 3, before adding the current coverage.
   Everything under that heading is history, not current coverage or criteria.
6. All downstream coverage, work, and evidence references the same current IDs;
   downstream roles do not invent substitute requirement IDs.
7. Tester accounts for every current ID with verification evidence, a blocker,
   or—when satisfaction explicitly depends on a later role—a pending dependency.
   During partial development, `AWAITING_IMPLEMENTATION` identifies approved
   future steps or increments, regardless of effective cadence. It is neither a
   defect nor a later-role dependency, and cannot pass full verification or
   Reviewer entry. For partially satisfied ACs, distinguish evidenced outcomes
   from the remaining work; an increment pass alone does not verify an entire
   AC. Known regressions or defective completed outcomes follow failure routing.
   Pending and awaiting implementation are not verification evidence and must be
   resolved under the same ID.
8. `AWAITING_USER_SIGNOFF` is forbidden while any current ID lacks sufficient
   evidence or has an unresolved blocker. Route defects to their owning roles
   through normal failure and recovery rules; never treat an unevidenced
   condition as satisfied by assumption.
9. Acceptance changes during recovery or rework invalidate any completed
   downstream artifact whose completion contract requires accounting for every
   current ID. Include those states when computing downstream invalidation even
   if underlying behavior or technical decisions remain otherwise valid.

## Review Kinds

```text
ReviewKind
- IMPLEMENTATION
- FINAL_DELIVERABLE
```

`IMPLEMENTATION` corresponds to `REVIEWING_IMPLEMENTATION`. `FINAL_DELIVERABLE`
corresponds to `REVIEWING_FINAL`. A review handoff must identify the requested
kind.

### Review Gates

Reviewer's skill defines the gate for each review kind. `FINAL_DELIVERABLE` is
available in `STANDARD` and `DOCUMENTATION`; in `EXPEDITED`, implementation
review assesses the bounded `Active Work.Request` and Developer evidence under
**Expedited Cycle Contract** in `.standards/protocol/expedited.md`.

In `DOCUMENTATION`, final review assesses **Documentation Cycle Contract**
without requiring current-cycle Development, Testing, or Implementation Review
artifacts. It still requires independent assessment and a full applicable gate.

Passing a review gate does not complete the cycle. Apply **Recovery Mechanics**
when active; otherwise follow **Forward Transitions**. Before entering
`AWAITING_USER_SIGNOFF`, all applicable cycle completion requirements, including
an empty recovery stack and inactive outstanding obligations, must hold.

In `STANDARD`, an implementation review may pass with permitted later-role
dependencies. Its `COMPLETE` status alone therefore does not establish
eligibility for `IMPLEMENTATION_REVIEWED` closure. Reviewer separately assesses
and records that eligibility under **Standard Cycle Completion**; this remains
implementation review, not a third review kind or a substitute final review.

## Synchronization Gate

Synchronizer reconciles completed assessments, the current deliverable, and
workflow records in `SYNCHRONIZING` during `STANDARD` or `DOCUMENTATION`.
Reviewer owns the assessment of soundness; Synchronizer establishes whether that
assessment and its supporting evidence still apply to the work being offered for
sign-off. Synchronizer's skill defines the gate; passing it is not cycle
completion or user acceptance. In `STANDARD`, its normal phase belongs to
`FULL_DELIVERABLE`. Under `IMPLEMENTATION_REVIEWED`, required reconciliation
corrections use **Recovery Mechanics** and, when applicable, **Corrective
Returns**; they do not replace Reviewer's closure assessment or restore the
omitted normal tail.

In `DOCUMENTATION`, normal synchronization is mandatory with
`CompletionPolicy: NONE`; reconcile the included roles' assessments and evidence
under **Documentation Cycle Contract**. Intentionally omitted implementation
phases are not missing prerequisites or incomplete synchronization guarantees
for that contract.

Project-facing documentation and agent guidance are Documenter-owned, including
project instructions outside managed framework blocks. Their completion evidence
must identify the relevant documents and assessed content, checks performed and
results or limits, and any current AC or technical criterion they satisfy.
Documenter persists this evidence and resumable progress in its documentation
record under **Workflow Artifact Provenance**, referencing supporting evidence
where it already exists rather than copying it. The record also preserves
collaboration mode, target and editing boundary, explicitly selected user style
(or `NONE`), remaining work, discrepancies, dependencies, and the completion
conclusion. It is not an authoritative acceptance ledger and does not certify
another role's work. Reviewer and Synchronizer consume it independently; neither
manufactures its evidence or rewrites documentation. Managed framework blocks,
installed protocol, and installation metadata retain installer/protocol
ownership. Preserve user-authored instructions and apply **Instruction Layering
and Conflicts** when necessary.

## Standard Cycle Completion

Before a `STANDARD` cycle enters `AWAITING_USER_SIGNOFF`, it must satisfy the
common requirements below and the completion gate selected by
`Active Work.CompletionPolicy`.

For both policies, Scoper, Architect, Auditor, Developer, Tester, and
implementation Reviewer must have passed their applicable full gates for the
current work. The **Full Verification Boundary** applies regardless of
verification cadence; an increment pass or scoped corrective return does not
establish full completion. Every current AC and relevant technical criterion
must have sufficient current evidence, with no unresolved material findings,
discrepancies, dependencies, or blocking user question. Required project context
must be valid, `Active Work.BaselineReconciliation` must be `NONE`, and
`Active Work.PendingVerificationCadence` must be `NONE`. Recovery must be
complete (empty stack) and outstanding obligations inactive. Apply recovery
routing first; neither a report label nor correction of one owned obligation
bypasses these requirements.

| Completion policy         | Additional gate before sign-off readiness                                                                      |
| ------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `FULL_DELIVERABLE`        | Documenter, final Reviewer, and Synchronizer have passed their full gates for the current work.                |
| `IMPLEMENTATION_REVIEWED` | Implementation Reviewer has assessed and recorded eligibility under **Implementation-Reviewed Closure** below. |

### Implementation-Reviewed Closure

Reviewer assesses early-closure eligibility separately from the ordinary
implementation-review conclusion. In the cycle's implementation review report,
record the selected policy, the explicit user choice and its stated reason (if
any), assessed input identities, evidence references, eligibility conclusion,
and the omitted documentation, final-review, and synchronization guarantees.
This evidence must survive replacement of `Handoff.Reason` and remain in the
cycle-owned report after a later cycle replaces `Active Work`.

Use the implementation report's `Implementation-Reviewed Closure` section and
structured fields in the Reviewer template. Its `Eligibility` is `NOT_ASSESSED`,
`INELIGIBLE`, or `ELIGIBLE`, independently of ordinary review `Status`. Only
current `ELIGIBLE` closure and a `COMPLETE` implementation review permit
shorter-policy sign-off readiness. The checker requires that explicit assessment
and its recorded prerequisites; Reviewer still determines evidence sufficiency
and freshness. Full-deliverable and expedited completion do not require this
assessment.

Eligibility requires all common standard completion requirements and:

- every current acceptance condition and relevant technical criterion is
  satisfied by sufficient current evidence, with no pending later-role work or
  evidence dependency;
- any required project documentation or agent guidance has owner-produced
  evidence sufficient for the current contract. Selecting this policy neither
  waives required documentation nor authorizes Reviewer to create Documenter's
  evidence. A contract change uses normal owner-directed rework;
- assessed inputs still match the current contract, implementation,
  verification, relevant documentation, dependencies, and configuration.
  Reviewer reconciles changes under its existing assessment obligations without
  claiming the omitted final-review or independent synchronization guarantees;
- any artifacts and corrections already produced remain accounted for. The
  policy cannot hide an unresolved finding or discrepancy merely because its
  owner is outside the shorter normal forward route.

If the ordinary implementation gate passes but closure eligibility does not,
keep those conclusions distinct and record the unmet requirement and its owner.
Do not enter `AWAITING_USER_SIGNOFF`, claim skipped phases passed, or silently
change the policy. Required owner work follows standard failure, rework, and
recovery rules; a user may instead choose full-deliverable completion.

### Sign-off Readiness and Freshness

`AWAITING_USER_SIGNOFF` means ready for the user's decision, not accepted or
`SIGNED_OFF`. User acceptance follows **User Decisions and Intervention**;
revalidate the selected policy's requirements at sign-off against current
inputs, including after a recovery return to this state. Changes that invalidate
supporting evidence or a closure conclusion require reassessment by the affected
owners before sign-off; a previously passing report cannot authorize acceptance
of changed work. Legal coordination updates alone do not invalidate assessed
deliverable content. `EXPEDITED` uses **Expedited Cycle Contract** in
`.standards/protocol/expedited.md` instead and never fabricates synchronization.

## Failure Types

```text
FailureType
- SCOPING
- ARCHITECTURE
- PROJECT_CONTEXT
- IMPLEMENTATION
- VERIFICATION
- DOCUMENTATION
- REVIEW
- SYNCHRONIZATION
```

| Failure type      | Owning role    |
| ----------------- | -------------- |
| `SCOPING`         | `SCOPER`       |
| `ARCHITECTURE`    | `ARCHITECT`    |
| `PROJECT_CONTEXT` | `AUDITOR`      |
| `IMPLEMENTATION`  | `DEVELOPER`    |
| `VERIFICATION`    | `TESTER`       |
| `DOCUMENTATION`   | `DOCUMENTER`   |
| `REVIEW`          | `REVIEWER`     |
| `SYNCHRONIZATION` | `SYNCHRONIZER` |

The discoverer of a failure does not automatically own the fix. Route it to the
owner of the defective artifact or decision. A material decision that an owner's
completed artifact should have settled but left open is such a defect. When
another role owns the correction, do not ask the user to settle it in that
role's place, offer to route it only if the user wants, continue on an assumed
answer, or make a forward handoff with the defect noted only in the reply; the
owner asks the user when its correction needs a decision. In `EXPEDITED`, a
defect or guarantee owned by a skipped role requires **Expedited Promotion** in
`.standards/protocol/expedited.md`. Navigator only explains the route; see
**Navigator Boundary**.

## Failure Handoffs

A failure handoff routes a defect to its owning state:

<!-- markdownlint-disable MD013 -->

```text
SCOPING failure         -> SCOPING
ARCHITECTURE failure    -> ARCHITECTING
PROJECT_CONTEXT failure -> AUDITING
IMPLEMENTATION failure  -> DEVELOPING
VERIFICATION failure    -> TESTING
DOCUMENTATION failure   -> DOCUMENTING
REVIEW failure          -> REVIEWING_IMPLEMENTATION or REVIEWING_FINAL, matching the affected review
SYNCHRONIZATION failure -> SYNCHRONIZING
```

<!-- markdownlint-enable MD013 -->

In `STANDARD`, use this routing directly. In `EXPEDITED`, only
`IMPLEMENTATION -> DEVELOPING` and implementation
`REVIEW -> REVIEWING_IMPLEMENTATION` are valid failure routes because only those
states exist in the expedited topology. A defect or guarantee owned by a skipped
role requires **Expedited Promotion** in `.standards/protocol/expedited.md`, not
a failure transition into a skipped state.

In `DOCUMENTATION`, only owners included in **Documentation Cycle Contract** are
valid failure targets, with `REVIEW` targeting `REVIEWING_FINAL`. A required
omitted role follows that contract's blocking user-decision rule instead of a
failure handoff into a skipped state.

If routing changes state, apply **Recovery Mechanics**. A same-state failure
records the handoff but does not create a recovery frame.

## Recovery Mechanics

This is the canonical recovery algorithm. Skills define only how their role
corrects its owned work, evaluates its completion gate, and identifies which
downstream work its correction invalidates.

Recovery follows the active `CycleMode` topology. Expedited recovery reruns only
expedited states; a newly required skipped role or guarantee triggers
**Expedited Promotion** in `.standards/protocol/expedited.md`, which preserves
unresolved corrective obligations as `Outstanding Obligations` and clears only
the now-obsolete expedited recovery routing.

Documentation recovery uses only its included states under **Documentation Cycle
Contract**, including qualifying **Corrective Returns** to an interrupted role
whose unfinished work prevents the correcting role's full gate. Standard
implementation/checkpoint and shorter-policy corrective exceptions do not add
skipped states or guarantees to that topology.

Every documentation frame's `From` and `ResumeAt` must be included states;
`Owner` is an included role, with `REVIEW` owned by `REVIEWING_FINAL`.
`RerunThrough` is `NONE` or a downstream included role, never
`AWAITING_USER_SIGNOFF`. Use `RESUME` for a recovery-directed jump that skips an
unaffected phase; a normal `FORWARD` still follows the fixed route and requires
the full gate, even with frames active. A qualifying Documenter or Synchronizer
return retains its valid current-cycle record and verified correction evidence
while leaving unfinished full-gate work explicit. The saved return does not
establish full completion or waive readiness requirements. Do not create
expedited-promotion obligations or current-cycle artifacts for omitted owners;
preserve the existing cycle and recovery and use the contract's blocking user
decision instead.

In `STANDARD`, both completion policies retain all failure owners and the same
recovery algorithm. `IMPLEMENTATION_REVIEWED` omits only the normal forward
tail; corrective routing may still enter `DOCUMENTING`, `REVIEWING_FINAL`, or
`SYNCHRONIZING`. Those states require active recovery under the shorter policy.
Their absence from the normal route is not itself a missing-artifact defect.
Preserve the selected policy, frames, and obligations; a correction does not
select full completion or authorize running the omitted normal phases.

Compute reruns from affected existing work and evidence required by the active
contract, not every phase in the full-deliverable tail. Include already-produced
artifacts from omitted phases when their evidence or conclusions are affected.
Also treat Reviewer's early-closure assessment as produced work: a correction
that invalidates its assessed inputs or eligibility requires
`REVIEWING_IMPLEMENTATION` reassessment before shorter-policy sign-off
readiness. If that is the interrupted state, Reviewer reassesses on resume;
otherwise include it in the required reruns. Changes that preserve applicability
need a recorded justification, not an automatic full-workflow restart.

1. **Push only when corrective routing changes state.** For `FAILURE` or
   `USER_REWORK` moving to a different state, push a frame with `From` =
   interrupted state, `Owner` = corrective target, applicable `FailureType` and
   `Reason`, `ResumeAt` = interrupted state, and `RerunThrough: NONE`.
   Same-state correction creates no frame.
2. **Preserve nesting.** New failure or rework during recovery pushes another
   frame. Never overwrite older frames. The last frame is active.
3. **Only the active frame owner plans resumption.** After correcting the defect
   and passing its applicable gate, that owner decides which already-produced
   downstream work must be re-established before `ResumeAt`. Include affected
   implementation and checkpoint evidence in unfinished phases; a phase need not
   be `COMPLETE` to require a rerun. Unimplemented future work alone does not
   require a rerun. Route through the states owning the affected work using the
   active topology. **Corrective Returns**, **Synchronizer Corrective Reruns**,
   and **Implementation and Verification Recovery Gates** define the only
   exceptions to full completion; the routing algorithm below remains unchanged.
4. **No rerun:** pop the frame and transition directly to `ResumeAt` with
   `Handoff.Kind: RESUME`.
5. **Rerun required:** set `RerunThrough` to the last required state and
   transition to the earliest required rerun state. Keep the frame on the stack.
6. **Rerun states use the applicable gates and legal recovery handoffs** while
   preserving the stack. Normal full gates apply unless **Implementation and
   Verification Recovery Gates** or **Synchronizer Corrective Reruns**
   explicitly permits a scoped rerun. They do not own the frame unless a nested
   defect creates a new one.
7. **At the rerun boundary**, after `RerunThrough` passes its applicable gate,
   pop the frame and transition to `ResumeAt` with `Handoff.Kind: RESUME`
   instead of taking the normal forward handoff. This explicit return is valid
   even when `ResumeAt` lies outside the project's normal forward topology.
8. Recovery ends only when the stack is empty.

A return to `AWAITING_USER_SIGNOFF` must re-establish the selected completion
contract after the final frame is closed. For `IMPLEMENTATION_REVIEWED`, the
ordinary implementation review must be `COMPLETE` and its closure assessment
currently `ELIGIBLE`; a correction or a passing downstream gate alone is not
enough. A role returning directly without a Reviewer rerun must establish that
the existing closure assessment remains applicable, without rewriting
Reviewer-owned conclusions. Otherwise route reassessment before returning.

Examples:

```text
Architecture defect discovered during testing:
TESTING -> ARCHITECTING                 # push frame, ResumeAt TESTING
ARCHITECTING -> DEVELOPING              # set RerunThrough DEVELOPING
DEVELOPING -> TESTING                   # boundary completes, pop, resume TESTING

Scoping defect discovered during implementation review:
REVIEWING_IMPLEMENTATION -> SCOPING     # push frame
SCOPING -> ARCHITECTING                 # set RerunThrough TESTING
ARCHITECTING -> DEVELOPING -> TESTING
TESTING -> REVIEWING_IMPLEMENTATION     # pop, explicit resume

Invalid project context discovered during architecture:
ARCHITECTING -> AUDITING                # push frame
AUDITING -> ARCHITECTING                # no rerun; pop and resume

Nested recovery:
TESTING -> AUDITING                     # outer frame, ResumeAt TESTING
AUDITING -> SCOPING                     # nested rework frame, ResumeAt AUDITING
SCOPING -> ARCHITECTING                 # set nested RerunThrough DEVELOPING
ARCHITECTING -> DEVELOPING
DEVELOPING -> AUDITING                  # pop nested frame, explicit resume
```

### Implementation and Verification Recovery Gates

Developer and Tester may finish a scoped corrective assignment or affected
downstream rerun in `STANDARD` without completing unrelated future
implementation or verification. This exception is selected by the interrupted
assignment and required return, not by effective cadence, a pending switch, or
the latest handoff kind. It also applies when Developer needs a Tester-owned
test corrected after switching to `AFTER_IMPLEMENTATION`.

Before replacing a Developer or Tester assignment during corrective routing,
persist its purpose, target, assessed input identities, and next action in that
role's existing artifact. Associate the saved assignment with the recovery frame
and its specific reason; a frame number alone may be reused after it is popped.
In Developer's `Plan Notes` or Tester's `Resume or Handoff`, use a
`### Suspended Assignment N` entry with these required fields:

```markdown
`Recovery Frame`: `1` `Recovery Reason`: `<exact frame Reason>` `Purpose`:
`DEVELOPMENT | FULL | INCREMENT | CORRECTION` `Target`:
`NONE | Increment N | specific correction` `Assessed Inputs`:
`<relevant content identities>` `Next Action`: `<concrete action to resume>`
```

`DEVELOPMENT` represents Developer's overall implementation assignment, and
`FULL` represents Tester's overall assessment; both use target `NONE`.
`INCREMENT` names the assigned increment and `CORRECTION` names the specific
correction. Select one concrete value for each field. Append entries with unique
numbers within the artifact; the latest entry for a frame is its saved
assignment. Retain the frame association through nested recovery. The checker
uses these records to distinguish scoped work from full assignments; the owning
roles still determine evidence validity and required reruns.

Preserve older suspended assignments through nested recovery. When the role
receives its corresponding `RESUME`, restore the assignment and reconcile
changed inputs. Corrected upstream intent may require revising the assignment
under normal ownership and approval rules; record that reconciliation before
relying on it.

A scoped gate may replace a Developer or Tester full gate only when:

- the active recovery frame and owned artifacts identify the specific correction
  or affected rerun and the unfinished assignment to resume;
- all owned work necessary to correct that defect or re-establish the affected
  outcome is done and verified with appropriate role-owned evidence;
- affected dependencies and previously completed behavior have been reconciled,
  and any newly discovered independent defect follows normal failure routing;
- remaining full-gate gaps consist only of explicitly recorded future approved
  implementation or assessment that depends on unfinished work in the preserved
  route; no assignment defect, owned outstanding obligation, required check, or
  blocking user question is being deferred;
- `check` reports no problem in the correcting or rerunning role's owned files,
  and the applicable claims, limitations, and resume context are current.

Use the canonical stack algorithm. For a scoped return, Developer keeps its plan
`IN_PROGRESS`; Tester keeps its report `IN_PROGRESS` or `BLOCKED`. Only full
completion permits `COMPLETE`. A same-state correction can resume the partial
assignment without a new frame. Scoper, Architect, and Auditor retain their full
gates; Documenter and Synchronizer retain their separate **Corrective Returns**.

For example, an architecture correction discovered in an increment assessment
can rerun Developer's affected implementation and resume that assessment while
future steps remain unfinished. A nested test correction may return to
unfinished Developer work before the outer frame resumes Tester. A return to
Reviewer or a later phase must satisfy **Full Verification Boundary**.

### Corrective Returns

An interrupted role may need a documentation or reconciliation defect corrected
before it can finish its own work, and requiring the correcting role to finish
its full gate first would stop both. Documenter and Synchronizer may therefore
plan resumption without passing their full gate. This is a narrow recovery
outcome in `STANDARD` or `DOCUMENTATION`, not a passing completion conclusion.
Every corrective, rerun, and resume state must be legal for the active cycle's
topology. It applies only when:

- the correcting role owns the active recovery frame, whose `RerunThrough` is
  `NONE`, and `ResumeAt` is another workflow role's state, or is
  `AWAITING_USER_SIGNOFF` under `IMPLEMENTATION_REVIEWED` with the mandatory
  Reviewer rerun described below;
- its record has valid current-cycle provenance, and the frame's specific
  correction has been verified as that role's skill requires under **Documenter
  Corrective Return** or **Synchronizer Corrective Return**;
- every remaining full-gate gap exists only because work or assessment already
  assigned to the interrupted role or the preserved recovery route is
  unfinished, or, in `STANDARD`, because a normal phase is intentionally omitted
  by `IMPLEMENTATION_REVIEWED` and its guarantee is not required by the active
  contract. Record each unfinished item, its owner, and the evidence still
  required; distinguish intentionally omitted guarantees from pending work.
  Omission cannot excuse evidence needed for the correction, an acceptance
  condition, or an unresolved material defect;
- any newly discovered independent defect or gap is handled through normal
  failure and blocking rules instead of being deferred by this exception.

Persist the correction evidence and remaining work, leaving the record
`IN_PROGRESS` or `BLOCKED` while its full gate is unmet. Then use **Recovery
Mechanics** to determine reruns and the return, preserving older frames and
outstanding obligations. Do not mark the record `COMPLETE`, claim the gate
passed, close another role's findings, or take a normal forward handoff on the
strength of the correction. A same-state correction, a downstream rerun that
does not own the active frame, or a direct return to `AWAITING_USER_SIGNOFF`
cannot use this exception. **Synchronizer Corrective Reruns** separately defines
the narrow non-owner rerun case. The full gate still applies before a normal
forward handoff. **Standard Cycle Completion** or **Documentation Cycle
Contract**, as applicable, still requires every full gate applicable to the mode
and completion policy before sign-off readiness. A corrective return does not
itself establish early-closure eligibility or waive remaining owner work.

In `DOCUMENTATION`, this exception may return a verified Documenter or
Synchronizer correction to another included role while the correction record
stays incomplete solely because that interrupted role or preserved route has
unfinished work. For example, final Reviewer may find an error in an existing
current-cycle synchronization record during re-review. Synchronizer verifies the
correction with available evidence, persists the unfinished final-review
dependency, and uses the active frame to return to Reviewer without claiming
full synchronization. After the review gate passes, the preserved route must
re-establish any affected synchronization before readiness. This exception does
not waive evidence needed to verify the correction, permit entry into an omitted
phase, allow an incomplete normal forward handoff, or return directly to
`AWAITING_USER_SIGNOFF` on an incomplete gate. Reaching sign-off readiness still
requires all full documentation-cycle gates, an empty recovery stack, and no
outstanding obligations.

If `ResumeAt` is `AWAITING_USER_SIGNOFF` under `IMPLEMENTATION_REVIEWED`, a
verified corrective return must keep the frame and rerun implementation Reviewer
before resuming sign-off readiness. Set `RerunThrough` to
`REVIEWING_IMPLEMENTATION`, including any earlier required reruns. Reviewer
reassesses ordinary review and closure eligibility after the correction; only
their passing conclusions and closure of the final frame permit resumption. This
prevents an omitted full-phase guarantee from deadlocking required correction
while retaining the complete early-closure gate. It does not permit unfinished
contract work at sign-off or relax `FULL_DELIVERABLE` completion.

For example, an error in an existing synchronization record discovered while
awaiting shorter-policy sign-off routes to Synchronizer with a recovery frame
whose `ResumeAt` is `AWAITING_USER_SIGNOFF`. If the correction is verified but
the full synchronization gate lacks intentionally omitted final review,
Synchronizer keeps its record incomplete, sets `RerunThrough` to
`REVIEWING_IMPLEMENTATION`, and hands off to Reviewer with the frame retained.
Reviewer rechecks the correction's effect on ordinary review and early closure,
then pops the frame and resumes sign-off readiness only when both gates pass.

### Synchronizer Corrective Reruns

Under `STANDARD` with `IMPLEMENTATION_REVIEWED`, an upstream correction may
invalidate a synchronization record left incomplete by an earlier verified
**Corrective Returns** outcome. Synchronizer may re-establish that existing
corrective evidence as a downstream rerun without claiming its full gate passed,
only when:

- the active frame belongs to another role, has a non-`NONE` `RerunThrough`, and
  its saved rerun plan includes this affected synchronization work;
- the current-cycle record preserves the earlier verified correction, assessed
  inputs, and intentionally omitted full-phase guarantees. This exception does
  not initiate normal synchronization or downgrade a previously completed full
  gate merely to avoid reassessment;
- all affected corrective evidence is rechecked against current inputs, and
  every required reconciliation check and owned correction is complete. No
  unresolved material defect, required evidence gap, owned outstanding
  obligation, or blocking user question is deferred;
- remaining full-gate gaps are limited to intentionally omitted normal-phase
  guarantees not required by the active contract, or implementation Reviewer
  reassessment awaiting this rerun on the preserved route. Unfinished required
  implementation, verification, or documentation cannot use this exception.

Persist the frame association and reason, earlier corrective evidence, changed
inputs, revalidation results, remaining full-gate limitations, and next action
in the synchronization record. Keep it `IN_PROGRESS` or `BLOCKED`; this is a
passing scoped assignment, not `COMPLETE` synchronization or closure
eligibility. Do not invent missing final-review evidence or change the selected
policy.

The active frame owner plans the route; Synchronizer follows its existing
boundary without taking ownership, rewriting `RerunThrough`, or discarding older
frames. At its saved boundary it may pop that frame and resume a workflow role;
otherwise it keeps the frame and continues the required reruns. In either case,
implementation Reviewer must reassess affected review and closure conclusions
before shorter-policy sign-off readiness. When `ResumeAt` is
`AWAITING_USER_SIGNOFF`, the owner must set `RerunThrough` to
`REVIEWING_IMPLEMENTATION`, with Synchronizer's revalidation and other affected
work before that final assessment. Synchronizer cannot pop directly to sign-off
using this scoped gate. An incompatible saved route does not authorize a
shortcut or a self-directed boundary change.

This exception does not apply to `FULL_DELIVERABLE`, final Reviewer, Documenter,
same-state corrections, or an unmet ordinary Synchronizer assignment. Their
existing gates and ownership rules remain in force.

## User Decisions and Intervention

These are user-authorized control-plane transitions, subject to **Navigator
Boundary**. An explicit, unambiguous user instruction may authorize the
receiving agent to persist the coordination change regardless of current state
ownership. That authorizes only the coordination changes the applicable rule
requires, not the target role's work: resulting role-owned work may proceed only
when that role was explicitly invoked and owns the resulting state. Otherwise
stop after persisting the transition and provide the next-role invocation when
the handoff rules require one.

At `AWAITING_USER_SIGNOFF`, available actions are sign off, rework, or cancel;
for expedited work, explicit promotion is also available. A standard cycle with
`IMPLEMENTATION_REVIEWED` may instead withdraw that choice under **Change
completion policy** in `.standards/protocol/user-decisions.md` to resume the
full workflow. Bounded expedited rework follows normal recovery back to
sign-off. Rework requiring a skipped standard guarantee promotes and restarts
the standard brownfield topology at `AUDITING`.

The rules for choosing the next cycle's mode, changing completion policy,
switching verification cadence, and starting, promoting, signing off, and
cancelling a cycle are in `.standards/protocol/user-decisions.md`. Reworking an
active cycle follows below.

### Rework an active cycle

From any nonterminal state, update `Active Work.Request` when the request
changed, identify the earliest owned artifact or decision invalidated, record
`Handoff.Kind: USER_REWORK` with its `FailureType`, and route to that owner. If
state changes, push the recovery frame defined by **Recovery Mechanics** with
`ResumeAt` equal to the interrupted state; preserve older frames. User-requested
changes are not agent-discovered failures.

In `EXPEDITED`, bounded implementation rework routes to `DEVELOPING`. If the new
contract requires a skipped standard role or guarantee, the rework request
itself authorizes promotion: update `Active Work.Request` to the changed
contract, then apply **Expedited Promotion** in
`.standards/protocol/expedited.md` instead of routing to `DEVELOPING`. Record
`Handoff.Kind: PROMOTE`, not `USER_REWORK`, and push no rework frame.

In `DOCUMENTATION`, apply rework only within **Documentation Cycle Contract**.
If the proposed request needs an omitted role, resolve its blocking user
decision before replacing the active request or routing; it does not authorize
mode conversion or implementation work.

## Workflow State Reference

This section defines the fields of `.standards/STATE.md` and the rules for
updating them. The subsections follow the order of the shape example in
**Persisted Workflow State**: `Active Work`, `Handoff`, and `Recovery`.
`Outstanding Obligations` is normally `Active: false`; only **Expedited
Promotion** adds entries. When it is active, read **Outstanding Obligations** in
`.standards/protocol/expedited.md` before role work.

### Active Work

- `Id`: stable, user-readable identifier generated by `cycle.mjs new` (see
  **Cycle IDs** in `.standards/protocol/user-decisions.md`). It is never reused
  by another cycle. Never overwrite or repurpose an artifact belonging to
  another cycle.
- `Request`: persisted user request at enough fidelity for the entry role to
  understand the work.
- `CompletionPolicy`: required `NONE`, `FULL_DELIVERABLE`, or
  `IMPLEMENTATION_REVIEWED` under **Completion Policies**. Preserve the selected
  value at sign-off or retained cancellation and initialize it afresh for each
  new cycle. It is a completion boundary, not acceptance evidence or user
  sign-off.
- `Scope`, `Architecture`, and `Development`: repository-relative paths to the
  owning artifacts, or `NONE` until created. Scoper, Architect, and Developer
  must persist their artifact path before their normal completion gate passes.
  In expedited work `Scope` and `Architecture` normally remain `NONE` unless the
  cycle is promoted; `Development` is still Developer-owned and is created
  before implementation begins. In documentation work, Scoper and Architect
  still persist their paths; `Development` remains `NONE` throughout the cycle.
- `PromotionReason`: durable reason an expedited cycle was promoted, otherwise
  `NONE`. Preserve it for the remainder of the cycle; it is workflow context,
  not a user requirement, scope decision, architecture decision, or baseline
  fact.
- `BaselineReconciliation`: durable provenance for unresolved project changes
  retained from cancelled cycles, otherwise `NONE`. It must be resolved before
  the current cycle may rely on the affected repository state as established
  baseline. Use the **Baseline Reconciliation Format** below, with one entry per
  unique `SourceCycle` and its `Request` summary. Carry the list across
  handoffs, failures, rework, recovery, and cancellation until Auditor resolves
  every source and clears it. `Handoff.Reason` is not a substitute.
- `AuditTarget`: transient repository-relative path or area label for an
  in-progress targeted audit when that focus cannot otherwise be recovered from
  persisted active work, owned artifacts, or recovery context; otherwise `NONE`.
  Auditor must persist an ad-hoc user-directed target before relying on it and
  clear it when that targeted audit completes, is abandoned, or no longer needs
  separate persistence.
- `BlockedOn`: unresolved user question preventing completion, otherwise `NONE`.
  Do not use it for a defect another role owns; route that as **Failure Types**
  describes, except for the omitted-role user decision required by
  **Documentation Cycle Contract**.
- `PendingVerificationCadence`: `NONE`, `INCREMENTAL`, or
  `AFTER_IMPLEMENTATION`; a user-requested cadence change awaiting application
  by Developer under **Switch verification cadence** in
  `.standards/protocol/user-decisions.md`. It is coordination, not the effective
  cadence or an acceptance ledger. This field is required; a missing, invalid,
  or repeated value is an inconsistency. Initialize and clear it under the cycle
  lifecycle rules; never carry it into a new cycle. Documentation cycles keep it
  `NONE` because they schedule no implementation or Tester assessments.

Installation initializes `Id` and `Request` as `UNSET`; **Start a cycle** (in
`.standards/protocol/user-decisions.md`) sets them for every cycle.

### Baseline Reconciliation Format

Within `Active Work`, store `BaselineReconciliation` on its own line. When no
obligation exists, use exactly:

```markdown
`BaselineReconciliation`: `NONE`
```

Otherwise use a nonempty Markdown list with both required fields in each entry:

```markdown
`BaselineReconciliation`:

- `SourceCycle`: `change-invoice-cache-invalidation-20260923T141500Z-5d2e8b17`
  `Request`: `Change invoice-cache invalidation behavior.`
- `SourceCycle`: `add-internal-notes-to-admin-records-20260924T093000Z-c81f4a06`
  `Request`: `Add internal notes to admin records.`
```

`SourceCycle` is the cancelled source's exact cycle `Id`, not the new cycle's
ID. It is the unique key; `Request` is that source's brief persisted request
summary. Preserve entry order and existing entries when carrying the list into
another cycle. Append a newly obligated source only if its ID is absent; never
replace older sources with the latest cancellation or duplicate an existing ID.
Confirmation that the latest cycle left no changes does not clear older entries.
Keep the complete list while any source remains unresolved; only Auditor clears
it to `NONE` after reconciling every listed source. An empty list is represented
as `NONE`, not an empty string or `[]`.

A value that does not match this format is invalid workflow state. Report the
inconsistency and block work that depends on it until corrected; do not infer
entries or discard obligations.

### Handoff

`Handoff.Kind` is one of `INITIAL`, `FORWARD`, `CHECKPOINT`, `FAILURE`,
`RESUME`, `PROMOTE`, `USER_REWORK`, `NEW_CYCLE`, `SIGNOFF`, `CANCEL`, or
`COMPLETION_CHANGE`. Use `NONE` for inapplicable fields. `Reason` describes only
the latest transition and must remain concise; it is not durable storage for
outstanding recovery or baseline obligations.

`COMPLETION_CHANGE` is reserved for the two state-changing routes under **Change
completion policy** in `.standards/protocol/user-decisions.md`. It requires
`FailureType: NONE`, records the source state in `From`, and creates no recovery
frame. A same-state policy choice preserves the existing handoff; it does not
use this kind or replace an incremental checkpoint.

Use `RESUME` whenever a recovery frame returns to `ResumeAt`, and for any other
recovery-directed transition that is not the normal forward handoff.
`CHECKPOINT` is reserved for the two normal incremental exchange routes under
**Checkpoint Handoffs**, never for a defect, cadence change, or recovery return.
Validate a saved checkpoint against its assigned increment and evidence. After
the return, Developer may change cadence or select the next increment without
rewriting the handoff or Tester's historical assessment target. These records do
not assert that the new selection is verified.

### Recovery

`Recovery` is a stack ordered oldest to newest, and **Recovery Mechanics**
defines how frames are pushed, rerun, and popped. A frame preserves one
corrective defect together with the routing needed to return to interrupted
work. A role owns the active frame only when the current `WorkflowState` equals
its `Owner`; merely running during recovery does not make a role responsible for
the frame.

### State-update rules

1. Installation initializes from the selected mode template: `SCOPING` for
   `GREENFIELD`, `AUDITING` for `BROWNFIELD`, `CycleMode: UNSET`,
   `PendingCycleMode: UNSET`, `PendingCycleRequest: UNSET`,
   `PendingCycleBlockedOn: NONE`, `Handoff.Kind: INITIAL`, unset active work,
   `Active Work.CompletionPolicy: NONE`,
   `Active Work.PendingVerificationCadence: NONE`, and inactive recovery.
2. Every legal state-changing transition updates all applicable fields as
   **Handoff Rules** requires; failure and recovery, promotion, and user-control
   transitions follow their canonical sections.
3. Blocking questions during an active cycle do not change workflow state. Set
   `Active Work.BlockedOn` before asking and clear it after incorporating the
   answer. Pre-cycle control-plane questions must not modify
   `Active Work.BlockedOn`; persist the blocked request and question in
   `PendingCycleRequest` and `PendingCycleBlockedOn` instead.
4. A user-requested cadence switch is a protocol coordination update, not a
   failure or `USER_REWORK`. Preserve the current handoff, recovery stack, and
   unrelated blockers while recording or applying it. Follow **Switch
   verification cadence** in `.standards/protocol/user-decisions.md` for safe
   application, replacement, and cleanup of pending requests.
5. A completion-policy choice changes only authorized coordination fields.
   Follow **Change completion policy** in
   `.standards/protocol/user-decisions.md` for permitted boundaries,
   preservation of assignments and obligations, and state-changing handoffs.
   Role-owned closure assessment and user sign-off remain separate actions.

`STATE.md` coordinates the workflow; it does not replace role-owned artifacts.
Role-owned artifacts remain authoritative for their own content.

## Workflow Artifact Provenance

Cycle ownership must be recoverable from the artifact itself whenever STANDARDS
creates one of the records below. Create each with
`node .standards/bin/artifact.mjs init <TYPE>`, adding `--kind <ReviewKind>` for
a review; it writes the provenance block and record header and never overwrites
an existing file. Every newly created record begins with this block, using
exactly one concrete artifact type and the exact current cycle ID. A review
report adds `ReviewKind: IMPLEMENTATION | FINAL_DELIVERABLE` before `-->`, with
exactly one concrete kind matching its path and the current review state.

<!-- markdownlint-disable MD013 -->

```markdown
<!-- STANDARDS
Artifact: SCOPE | ARCHITECTURE | DEVELOPMENT | VERIFICATION | REVIEW | DOCUMENTATION | SYNCHRONIZATION
Cycle: <Active Work.Id>
-->
```

<!-- markdownlint-enable MD013 -->

| Record                   | Owner        | Artifact type                      | Location                                                                              | Entries                                           |
| ------------------------ | ------------ | ---------------------------------- | ------------------------------------------------------------------------------------- | ------------------------------------------------- |
| Scope                    | Scoper       | `SCOPE`                            | `.standards/docs/scope/<Active Work.Id>.md`, or an existing unmarked project document | `AC-NNN` list items (**Acceptance Traceability**) |
| Technical design         | Architect    | `ARCHITECTURE`                     | `.standards/docs/specs/<Active Work.Id>.md`, or an existing unmarked project document | —                                                 |
| Development plan         | Developer    | `DEVELOPMENT`                      | `.standards/docs/development/<Active Work.Id>.md`                                     | `### DEV-NNN`                                     |
| Verification report      | Tester       | `VERIFICATION`                     | `.standards/docs/verification/<Active Work.Id>.md`                                    | —                                                 |
| Implementation review    | Reviewer     | `REVIEW`, kind `IMPLEMENTATION`    | `.standards/docs/reviews/<Active Work.Id>/implementation.md`                          | `### F-NNN`                                       |
| Final-deliverable review | Reviewer     | `REVIEW`, kind `FINAL_DELIVERABLE` | `.standards/docs/reviews/<Active Work.Id>/final-deliverable.md`                       | `### F-NNN`                                       |
| Documentation record     | Documenter   | `DOCUMENTATION`                    | `.standards/docs/documentation/<Active Work.Id>.md`                                   | `### DOC-NNN`                                     |
| Synchronization record   | Synchronizer | `SYNCHRONIZATION`                  | `.standards/docs/synchronization/<Active Work.Id>.md`                                 | `### D-NNN`                                       |

An existing unmarked project document remains project-owned when Scoper or
Architect selects it as the active scope or design location. The role may update
an appropriate unmarked canonical project document and must not add STANDARDS
provenance solely because `Active Work.Scope` or `Active Work.Architecture`
references it; a new scope or design record is created with `artifact init` at
its path in the table.

Every other record is always cycle-owned at its fixed path. Its provenance block
must match `Active Work.Id` and the path, and its visible `Cycle` field, plus
the visible `ReviewKind` field in a review report, must match the block.
`Active Work.Development` names the plan; the other fixed paths derive from the
cycle ID, so `STATE.md` gets no path field for them. Because every path that
`artifact init` creates includes the cycle ID, a new record's path never belongs
to another cycle.

A valid provenance block makes the file a STANDARDS cycle-owned artifact even if
it is later renamed or moved. A different cycle may read it as prior evidence
when a role contract permits, but must never overwrite, repurpose, or adopt it.
Preserve other cycles' records and the other review kind's report, including
moved artifacts whose provenance still names their original cycle.

Before creating or editing a record, or an artifact referenced by
`Active Work.Scope`, `Active Work.Architecture`, or `Active Work.Development`,
inspect its path and any provenance block:

- A file that starts with a malformed STANDARDS block (for example an unknown
  artifact type, an invalid cycle ID, a missing or extra `ReviewKind`, or an
  unclosed block) is a collision for every artifact type. Do not edit, adopt, or
  repair it.
- An unrelated, unmarked, incorrectly marked, or different-cycle or
  different-kind file at a fixed path is a collision, and so is a non-directory
  or unsafe path that prevents the required location.
- An `Active Work` reference to another cycle's artifact is inconsistent with
  the active cycle. Do not overwrite or silently repair that artifact; correct
  the reference without mutating it.

Report a collision and block dependent work until the user resolves it,
preserving the existing content, without overwriting, relabeling, or adopting it
or silently choosing another path.

Test suites, fixtures, ordinary project documentation, comments, and docstrings
remain reusable project assets and do not acquire cycle provenance because a
role creates or updates them.

The development plan, review reports, documentation record, and synchronization
record number their entries as headings with the prefixes in the table. Get each
new number, and each new `AC-NNN`, with
`node .standards/bin/id.mjs next <prefix> <file>`. When a record refers to an
entry in another record, it names that record's path with the identifier, for
example `.standards/docs/reviews/<Active Work.Id>/implementation.md#F-003`; a
path relative to the referring file, as in a Markdown link, also works.
Acceptance identifiers (`AC-NNN`) and development steps (`DEV-NNN`) of the
active cycle are referred to without a path.

## Project context lifecycle

`.standards/CONTEXT.md` is the canonical Auditor-owned project-context artifact.
Installation does not fabricate it; Auditor creates or refreshes it when
`AUDITING` runs. In a `STANDARD` or `DOCUMENTATION` cycle, refreshed context is
the project baseline for downstream roles, subject to ownership and freshness
rules. In an `EXPEDITED` cycle, existing context is prior evidence only and is
not presumed refreshed.

Any **Active-Cycle Non-Baseline Work** entry is scoped to the `Active Work.Id`
that produced it and applies only while that same cycle is nonterminal; it is
stale in `SIGNED_OFF`, `CANCELLED`, and later cycles. A later audit reconciles
each prior-cycle exclusion against current repository and version-control
evidence, removing or reclassifying it rather than copying it forward, and
blocks for user clarification when baseline status cannot be established safely.

Greenfield status does not require context to be absent. Before the first
scheduled greenfield audit, Scoping and Architecture may run or rerun without
`CONTEXT.md` when the facts their owned work needs are otherwise established;
absence of context alone is not a defect, and a role that needs project facts
that cannot safely be established without Auditor-owned context routes a
`PROJECT_CONTEXT` failure. Once `CONTEXT.md` exists, later Scoping or
Architecture work uses it when relevant, even while `ProjectMode` remains
`GREENFIELD`.

`.standards/MODE.md` and `.standards/STATE.md` are protocol-owned coordination
artifacts. A role or user may change them only through **Project Reset** (in
`.standards/protocol/installation.md`), a legal protocol transition, or a
protocol-required coordination update, including initializing `Active Work`,
recording artifact paths, selecting or promoting `CycleMode`, and setting or
clearing `PromotionReason`, `BaselineReconciliation`, `AuditTarget`, or
`BlockedOn`. `.standards/PROTOCOL.md`, its chapters in `.standards/protocol/`,
`.standards/bin/`, `.standards/VERSION.json`, and `.standards/INSTALLATION.json`
are framework-owned and may be changed only by framework installation or
upgrade.

## User Styles

A user style is a Markdown file of personal preferences for one role, kept at
`.standards/user-styles/<role>/<identifier>.md`, where `<role>` is the role's
skill name, such as `developer` or `tester`. Users add and maintain these files;
STANDARDS ships none. Installation and **Project Reset** keep them, and
**Project Uninstallation** deletes them with `.standards/` (both in
`.standards/protocol/installation.md`).

A role uses a user style only when the user explicitly selects one:

- The identifier is the filename stem: `tony` and `tony.md` both select
  `.standards/user-styles/<role>/tony.md`. Only a direct child Markdown file of
  that role's folder can be selected; reject paths, separators, traversal, and
  symlinks that leave the folder. `NONE` is reserved and means no user style.
  When a filename overlaps another style's identifier, use an unambiguous
  accepted name in invocations and saved `User Style` fields; do not shorten it
  into an ambiguous name. If neither the stem nor the full filename resolves
  uniquely, the user must disambiguate the filenames before selecting that file.
- For roles that persist styles in records, the selector must also be a concrete
  header value: it must be nonblank and must neither contain `|` nor be entirely
  enclosed in `<...>`. Prefer the stem only when it is unique and usable;
  otherwise use a unique, usable full filename. For example, `<formal>.md`
  persists as `<formal>.md`, never `<formal>`. Neither name for
  `team | compact.md` can be saved in a record. Report this syntax limitation
  separately from filename ambiguity. Retain a working saved selector on resume;
  do not rename user files or rewrite locked selections to work around a
  limitation. Conversation-only styles do not have record-field syntax
  restrictions.
- Never infer a style from the user's identity, repository ownership, prior
  usage, another role's selection, or the mere presence of a file.
- If a selection does not resolve to exactly one available file, stop and ask
  the user to choose an available style or clear the selection; never substitute
  another.

A user style governs discretionary choices only. Within a role's style guidance,
apply the role's `styles/universal.md` first when it has one, then the selected
user style, then the role's other applicable style files. A user style never
overrides the protocol, role ownership, the active contract, the required shape
of a role's artifacts, repository-enforced constraints, project instructions, or
correctness. A precedence list never settles a material conflict; use
**Instruction Layering and Conflicts**.

Roles that keep a cycle record persist the selection as `User Style` in that
record and reload it on resume: Developer's development plan, Tester's
verification report, Reviewer's review reports, Documenter's documentation
record, and Synchronizer's synchronization record. Scoper, Architect, Auditor,
and Navigator have no record for it, so their selection lasts only for the
current conversation and the user names it again when resuming. The user may
change or clear a selection at any time, except that Developer locks its
selection when the user first approves the development plan. If a persisted
selection's file is missing on resume, stop and ask the user to restore it or,
when the selection is not locked, to choose another or clear it.

## Canonical Terms

Use these terms consistently across all skills, and likewise the names defined
in other sections, such as the handoff kinds (failure, forward, resume, and
promotion handoffs), expedited cycle, recovery frame, outstanding obligation,
active work, cycle mode, user style, runtime tools, and project reset:

- **completed scope**: scope artifact that passed Scoper's completion gate; it
  does not imply separate user approval unless the project adds such a gate.
- **scope-level acceptance conditions**: observable outcomes owned by Scoper,
  each identified by a stable `AC-NNN` **acceptance identifier** for the active
  cycle that downstream artifacts reuse.
- **retired acceptance identifier**: an ID whose condition was removed or
  materially replaced; retained so it cannot be reused and no longer a current
  coverage or verification obligation.
- **technical acceptance criteria**: Architect-derived technical conditions
  linked to scope-level acceptance identifiers.
- **development plan**: Developer's persisted plan for the active cycle. It
  decomposes the active contract into stable `DEV-NNN` steps, records
  collaboration mode and resumable progress, and never replaces scope,
  architecture, Tester verification, review, or documentation.
- **verification cadence**: the plan's effective `AFTER_IMPLEMENTATION` or
  `INCREMENTAL` scheduling choice, independent of collaboration mode.
- **increment**: a plan-local testable implementation outcome linked to existing
  development steps and acceptance conditions; it may revisit earlier ACs.
- **checkpoint handoff**: a normal incremental exchange after an assignment gate
  passes, without asserting full Developer or Tester completion.
- **verification report**: records acceptance coverage, scenario allocations,
  actual execution evidence, gaps, and later-phase dependencies; it does not
  replace scope, design, or workflow coordination state.
- **review report**: records inspected inputs, checks, findings, limitations,
  dependencies, and resumable progress under Reviewer's **Review Gates**.
- **documentation record**: holds the evidence and progress that
  **Synchronization Gate** describes; it is distinct from reusable project
  documentation and does not replace another role's evidence or an acceptance
  authority.
- **synchronization record**: records assessed identities, references to
  completion and evidence artifacts, discrepancies and their owners,
  limitations, and a resumable conclusion under Synchronizer's **Synchronization
  Gate**; it is not an acceptance ledger or user sign-off.
- **project context**: the Auditor-owned baseline in `.standards/CONTEXT.md`. It
  may persist as evidence across cycles under **Project context lifecycle**. A
  `PROJECT_CONTEXT` failure means it is materially incomplete, incorrect, or
  unexpectedly invalidated; planned implementation does not by itself make it
  stale.
- **completion gate**: conditions required before a role may make a forward
  handoff.
- **STANDARDS hook**: a Claude Code or Codex hook handler whose command runs
  `.standards/bin/hook.mjs`.

The paths of the records are in **Workflow Artifact Provenance**. Do not
introduce alternate names for these concepts inside individual skills unless
this protocol is updated first.
