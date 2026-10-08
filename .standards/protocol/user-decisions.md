# S.T.A.N.D.A.R.D.S. Protocol: User Decisions

This chapter is part of `.standards/PROTOCOL.md`. Read that file first; its
reading guide says when this chapter applies.

## Choose the next cycle's mode

While no cycle is active, or before the initialized first cycle has received its
request, an explicit user instruction may set `PendingCycleMode` to a mode
supported by the current `ProjectMode`, replace an earlier pending preference,
or clear it to `UNSET`. Leave `CycleMode: UNSET` and `Active Work` unchanged;
the selection does not activate a cycle. The latest selection survives across
sessions until a cycle consumes it or the user replaces or clears it. While a
blocked `PendingCycleRequest` exists, a mode change or clear revalidates that
request under **Start a cycle** without asking the user to repeat it.

`DOCUMENTATION` is a supported preference only in `BROWNFIELD`. Validate the
request against **Documentation Cycle Contract** when consuming it; a pending
preference does not authorize implementation work or overwrite an active cycle.
Reject unsupported Greenfield preferences without persisting them. A request
already blocked before cycle creation remains governed by **Start a cycle**.

## Standalone Documenter entry

An explicit Documenter invocation with a new documentation-only assignment
requests `DOCUMENTATION` while no cycle is active. Classify that intent before
applying project-mode eligibility, pending preferences, completion-policy
choices, or default mode selection. Use **Start a cycle**, including
cancelled-cycle baseline reconciliation and ID generation. In `GREENFIELD`, or
with an incompatible mode preference or standard completion-policy choice, use
its pre-cycle blocking decision; never substitute `STANDARD` automatically.

Persist the standalone Documenter entry instruction alongside the concrete
documentation request and any explicit target, editing boundary, collaboration
choice, or user style in `PendingCycleRequest` when blocked, or
`Active Work.Request` when starting. Recover that intent from the saved request
on resumption even when the user invokes another role or only says to continue.
Only an explicit user decision to revise or withdraw standalone entry permits a
different mode; preserve the documentation goal and other unchanged choices. A
conflicting completion-policy choice alone does not withdraw that intent. Do not
create a premature documentation record or invent missing choices. A bare
invocation or request to continue without an available assignment does not
invent a new cycle request.

The resulting state is `AUDITING`, not `DOCUMENTING`. Initializing coordination
does not authorize Documenter to audit, scope, design, or edit documentation
early. Persist the transition and provide the Auditor invocation under the
protocol's **Handoff Rules**. Auditor may continue immediately only when the
same user instruction explicitly invoked Auditor too. Subsequent roles likewise
require explicit invocation and ownership; never auto-dispatch the full route.

When a cycle is active, its saved mode and state remain authoritative. An
invocation to continue the active documentation assignment follows the current
owner and existing recovery. Documenter may perform its owned work only in
`DOCUMENTING`; otherwise identify the owner and apply only an authorized
coordination action. A materially changed active request uses the protocol's
**Rework an active cycle**, including documentation-boundary limits. A separate
standalone request must wait until the active cycle is finished or explicitly
cancelled. Do not overwrite its ID, request, artifacts, blockers, recovery, or
pending fields to start another cycle, and do not infer cancellation from a
Documenter invocation.

## Start a cycle

A cycle starts from the installed state, where `Active Work.Id`,
`Active Work.Request`, and `CycleMode` are `UNSET`, or from `SIGNED_OFF` or
retained `CANCELLED`; a finished cycle is never reopened. The request is the
user's new request or, when one exists, the persisted `PendingCycleRequest`: use
it without asking the user to restate it, and let a revised request replace it.

1. **Determine the reconciliation obligation** the new cycle would carry,
   without writing it yet. For the first cycle and from `SIGNED_OFF`, it is
   `NONE`. From retained `CANCELLED`, it preserves any existing
   `BaselineReconciliation` and, unless the user explicitly confirms that the
   just-cancelled cycle left no project changes because none were produced or
   they were reverted, adds a `SourceCycle`/`Request` entry for that cycle under
   **Baseline Reconciliation Format** if its exact cycle ID is not already
   listed.
2. **Choose the mode before changing any state.** First identify standalone
   Documenter intent from the current invocation or the saved request under
   **Standalone Documenter entry**, incorporating any explicit user resolution.
   While that intent remains, the requested mode is `DOCUMENTATION`, including
   in `GREENFIELD` or with a standard completion-policy choice. Conflicting
   explicit or pending mode choices and incompatible policies use step 3 before
   cycle creation; none changes this request into inferred `STANDARD` work. A
   pending preference must also be valid for the request, the current
   `ProjectMode`, and the reconciliation obligation. Honor explicit and pending
   choices; never silently replace a conflicting preference. Without standalone
   intent or an explicit or pending mode choice, `STANDARD` is the default,
   except that in `BROWNFIELD` an explicit Developer invocation with a
   sufficiently bounded implementation request may select `EXPEDITED`. A pending
   `STANDARD` preference prevents inferred expedited entry. `GREENFIELD`
   supports only `STANDARD`. Unresolved reconciliation requires an Auditor-first
   `STANDARD` or `DOCUMENTATION` cycle, never `EXPEDITED`. Documentation entry
   must satisfy **Documentation Cycle Contract**, including preserving
   reconciliation for Auditor; a request requiring implementation or another
   omitted role is ineligible. A pending `EXPEDITED` preference does not bypass
   the **Expedited Cycle Contract** in `.standards/protocol/expedited.md`:
   validate eligibility before consuming it. An explicit `FULL_DELIVERABLE` or
   `IMPLEMENTATION_REVIEWED` selection requires `STANDARD` and prevents inferred
   expedited entry. It conflicts with standalone documentation intent and does
   not override an explicit or pending `EXPEDITED` or `DOCUMENTATION` mode
   choice; resolve conflicts in step 3. Documentation cycles use
   `CompletionPolicy: NONE`.
3. **Block instead of starting when the mode is not legal**, whether because of
   an invalid pending preference or an explicitly requested mode the current
   `ProjectMode` does not support. Never silently reinterpret the mode as
   `STANDARD` or persist an unsupported mode. Leave `WorkflowState`,
   `Active Work`, `CycleMode`, and `PendingCycleMode` unchanged; persist the
   request in `PendingCycleRequest` and the specific decision required in
   `PendingCycleBlockedOn`, not in `Active Work.BlockedOn`; and ask the user to
   choose a supported mode, replace or clear the preference, revise the request,
   or abandon it. Abandoning clears `PendingCycleRequest` and
   `PendingCycleBlockedOn` without modifying `Active Work` or starting a cycle.
   Apply the same rule to an incompatible completion-policy choice. Keep the
   standalone entry instruction, explicit mode and policy choices, and stated
   reasons in the saved request text so resumption does not lose them; there is
   no separate pending completion-policy field. When the user explicitly revises
   or withdraws standalone entry to use another mode, update that instruction in
   the saved request before revalidating it. Merely resuming or clearing a
   conflicting preference does not withdraw standalone intent.
4. **Generate the ID** as **Cycle IDs** describes. If the tool refuses, leave
   the state unchanged and do not start the cycle.
5. **Persist the cycle** in one state update:
   - `Active Work`: the new ID and request; `Scope`, `Architecture`,
     `Development`, `PromotionReason`, `AuditTarget`, and `BlockedOn` set to
     `NONE`; `PendingVerificationCadence: NONE`; and `BaselineReconciliation`
     from step 1.
   - `Active Work.CompletionPolicy`: `NONE` for `EXPEDITED` or `DOCUMENTATION`;
     `FULL_DELIVERABLE` for `STANDARD` unless the user explicitly selected
     `IMPLEMENTATION_REVIEWED` for this request. Preserve any explicit choice
     and stated reason in `Active Work.Request` as a workflow instruction,
     distinct from implementation requirements. Do not inherit the prior cycle's
     policy or invent a reason.
   - `CycleMode`: the chosen mode. `PendingCycleMode` and `PendingCycleRequest`
     become `UNSET`, and `PendingCycleBlockedOn` becomes `NONE`.
   - `Handoff`: for the first cycle, keep `Kind: INITIAL` with `From: NONE` and
     `FailureType: NONE`; from a terminal state, record `Kind: NEW_CYCLE`,
     `From` set to that state, `FailureType: NONE`, and a concise reason. An
     expedited or documentation first cycle records a concise mode-entry reason.
     Mention an unresolved reconciliation obligation concisely, without copying
     its source-cycle provenance.
   - Recovery and outstanding obligations inactive.
   - `WorkflowState`: `AUDITING` when reconciliation is unresolved or
     cancelled-cycle changes are being retained or adopted, or the chosen mode
     is `DOCUMENTATION`, so Auditor establishes baseline status first. Otherwise
     `DEVELOPING` for an allowed `EXPEDITED` cycle, or the standard entry state
     for the current `ProjectMode`: `SCOPING` for `GREENFIELD` or `AUDITING` for
     `BROWNFIELD`.

`CycleMode` never remains `UNSET` once a cycle has started.

## Change completion policy

An explicit user instruction may select `FULL_DELIVERABLE` or
`IMPLEMENTATION_REVIEWED` for the active `STANDARD` cycle under **Completion
Policies**. A request to finish after implementation review selects the shorter
policy; it does not itself assert that review passed or accept the deliverable.
A request to withdraw that choice selects `FULL_DELIVERABLE`. The **Navigator
Boundary** and **User Decisions and Intervention** permissions still apply.

With no active cycle, select the policy only as part of **Start a cycle**; there
is no standalone next-cycle policy preference. With an active `EXPEDITED` cycle,
keep `CompletionPolicy: NONE` and explain its existing path through
implementation review to sign-off. A standard policy requires authorization for
**Promote an expedited cycle**; selecting a policy alone does not promote it.
Promotion first initializes `FULL_DELIVERABLE`, after which a separately
authorized policy selection must satisfy the rules below. Both instructions may
be given together, but selection must not bypass promotion obligations.

With an active `DOCUMENTATION` cycle, preserve `CompletionPolicy: NONE` and its
fixed route through final review and synchronization. Explain that standard
policy selection does not convert this cycle or omit its required phases.

1. **Check whether this changes the policy.** An instruction matching the saved
   policy is a no-op: preserve state, handoff, and records. It does not reassert
   evidence validity, sign off, or restart work. For an unset or terminal cycle,
   do not edit the retained cycle or reopen it.
2. **Check the boundary before applying a change.** Recovery must be inactive
   with an empty stack, and outstanding obligations inactive with no entries. No
   normal documentation, final-review, or synchronization work may have begun in
   this cycle. Determine this from the saved role progress and repository
   evidence, not merely the current state or absence of a record. Earlier
   corrective work by those owners is not normal downstream phase entry and
   remains subject to its existing evidence and obligations.
3. **Select the permitted route** from the table below. These are the only
   policy-change routes; other states or failed preconditions do not authorize a
   change. Leave the policy and routing unchanged, explain the unmet condition,
   and do not queue a deferred policy change. Established defects still use
   normal owner-directed failure routing. A materially changed request uses
   normal rework, not this policy-only action.

   | Current state                                                                                 | Policy change                                   | Resulting state            | Additional precondition                                                                |
   | --------------------------------------------------------------------------------------------- | ----------------------------------------------- | -------------------------- | -------------------------------------------------------------------------------------- |
   | `SCOPING`, `ARCHITECTING`, `AUDITING`, `DEVELOPING`, `TESTING`, or `REVIEWING_IMPLEMENTATION` | Either standard policy to the other             | Same state                 | Preserve the current role assignment and handoff, including an incremental checkpoint. |
   | `DOCUMENTING`                                                                                 | `FULL_DELIVERABLE` to `IMPLEMENTATION_REVIEWED` | `REVIEWING_IMPLEMENTATION` | Untouched normal Documenter handoff under the conditions below.                        |
   | `AWAITING_USER_SIGNOFF`                                                                       | `IMPLEMENTATION_REVIEWED` to `FULL_DELIVERABLE` | `DOCUMENTING`              | Current implementation-review evidence under the conditions below.                     |

   For the `DOCUMENTING` route, the saved handoff must be either `FORWARD` from
   `REVIEWING_IMPLEMENTATION`, or `COMPLETION_CHANGE` from
   `AWAITING_USER_SIGNOFF` after a withdrawal. Documenter must not have begun
   its normal phase. The implementation review must have passed and remain
   applicable to current inputs; permitted later-role dependencies may still be
   unresolved. Entering Reviewer also requires **Full Verification Boundary**.
   Reviewer then assesses early-closure eligibility; selecting the policy never
   supplies that assessment or permits jumping directly to sign-off.

   For the `AWAITING_USER_SIGNOFF` withdrawal route, the completed
   implementation review and its supporting full verification must remain
   applicable to current inputs. If changes have invalidated them, route the
   affected work to its owners before applying the withdrawal. Withdrawal
   removes readiness for sign-off and resumes the full forward tail at
   Documenter; it does not require Auditor or other upstream owners to repeat
   still-valid work.

   Both state-changing routes require `Active Work.BlockedOn: NONE`. An
   unrelated blocker does not prevent a same-state policy choice, but that
   choice never clears it or authorizes blocked role work.

4. **Persist the authorized choice.** Set `Active Work.CompletionPolicy` and
   retain the explicit choice and any stated reason in `Active Work.Request` as
   a workflow instruction, preserving the implementation request. Replace an
   earlier policy instruction so the saved request and policy agree; do not
   treat this as a scope change or an acceptance waiver. Preserve cycle ID,
   mode, artifact references, evidence, blockers, pending verification cadence,
   baseline reconciliation, recovery, and outstanding obligations.
5. **Record routing only when the state changes.** For the two state-changing
   routes, set `WorkflowState` to the target and `Handoff.Kind` to
   `COMPLETION_CHANGE`, `From` to the source state, `FailureType: NONE`, and a
   concise reason identifying the old and new policies. Do not push a recovery
   frame or overwrite report conclusions. For a same-state change, preserve the
   entire handoff and any checkpoint assignment. Cycle initialization retains
   `INITIAL` or `NEW_CYCLE` instead.
6. **Hand off within existing role permissions.** Persist the coordination
   update before presenting the next action. Stop after the policy change unless
   the user also explicitly invoked the role that owns the resulting state.
   State-changing routes follow **Handoff Rules**, including the implementation
   review kind and independent-session requirements when returning to Reviewer.
   A policy change authorizes no other role's work.

Existing review reports retain their historical assessments. Their closure
conclusions apply only to the policy and inputs they assessed; a later return to
the shorter policy requires Reviewer to reconcile eligibility again. Sign-off
and retained cancellation preserve the last effective policy; starting a new
cycle replaces it under **Start a cycle**.

## Switch verification cadence

An explicit user instruction may select `INCREMENTAL` or `AFTER_IMPLEMENTATION`
for the active cycle under **Verification Cadence**. Record and apply it as
follows, preserving collaboration-mode permissions and pauses. The coordination
rules in **State-update rules** apply throughout.

`DOCUMENTATION` has no implementation or Tester scheduling. Keep
`PendingVerificationCadence: NONE`; a cadence request does not add those phases
or convert the cycle. Explain that it requires a separate implementation cycle.

1. **Promote when needed.** `EXPEDITED` supports only `AFTER_IMPLEMENTATION`. An
   explicit `INCREMENTAL` request authorizes **Expedited Promotion** in
   `.standards/protocol/expedited.md`. Preserve the request as pending during
   promotion and follow the Auditor handoff. Evaluate remaining scheduling after
   the standard contract is established.
2. **Check whether there is work to schedule.** Accept the request during an
   active cycle while implementation or its increment scheduling remains
   unfinished, including before a development plan exists or during recovery.
   Once the current contract and recovery route establish that no implementation
   or increment scheduling remains, clear pending intent and explain why the
   switch has no remaining effect. Do not rely on completion claims invalidated
   by rework or promotion, or reopen work solely to change cadence. No request
   is retained for an unset, signed-off, or cancelled cycle.
3. **Persist the latest request.** Any current role may record the explicit
   request in `Active Work.PendingVerificationCadence`. Replace an earlier
   pending choice with the latest one. A request matching the effective cadence
   cancels an opposite pending choice; if no plan exists, retain the explicit
   selection for Developer to apply when creating it.
4. **Apply only at a safe Developer boundary.** Developer applies the pending
   choice during normal `DEVELOPING`, before its next implementation action or
   checkpoint assignment. Finish any already assigned Tester assessment and its
   required return first. During recovery, defer application until the preserved
   route returns to normal development. Until then, the saved effective setting
   governs the assignment.
5. **Reconcile scheduling under existing approval rules.** When enabling
   `INCREMENTAL`, group planned work into testable outcomes and select the next
   increment, including implemented work still needing assessment. When enabling
   `AFTER_IMPLEMENTATION` at the safe boundary, clear `Current Increment`,
   including an increment selected but not yet handed to Tester, and schedule
   remaining implementation before full verification. Retain previous increment
   definitions and evidence; reconcile changed inputs under normal ownership.
   Pure cadence selection and grouping of unchanged approved steps do not
   require duplicate approval. Material changes to steps, dependencies,
   behavior, or technical approach still require plan approval.
6. **Save effective selection before clearing pending intent.** Persist the
   plan's cadence and reconciled scheduling first, then clear
   `Active Work.PendingVerificationCadence` to `NONE`. If interrupted between
   those writes, Developer reconciles the matching request and clears it
   idempotently. Report whether the switch was applied or remains pending.

Cycle lifecycle rules clear pending intent on cancellation, sign-off, reset, or
new-cycle initialization; it never becomes a preference for a later cycle.

## Promote an expedited cycle

An explicit user instruction may authorize **Expedited Promotion** in
`.standards/protocol/expedited.md` from any nonterminal expedited brownfield
state, including `AWAITING_USER_SIGNOFF`. After persisting promotion, stop and
hand off to Auditor unless Auditor was also explicitly invoked.

## Sign off

From `AWAITING_USER_SIGNOFF`, sign-off is legal only when the
`Outstanding Obligations` section is inactive. Revalidate the current mode's
completion contract: **Standard Cycle Completion**, including every standard
gate applicable to `Active Work.CompletionPolicy` and the
acceptance-traceability obligations, or, for `EXPEDITED`, only the narrower
**Expedited Cycle Contract** in `.standards/protocol/expedited.md`; skipped
standard phases must not be represented as completed. For `DOCUMENTATION`,
revalidate **Documentation Cycle Contract**, including current final review and
synchronization evidence, without demanding omitted implementation artifacts.
For `IMPLEMENTATION_REVIEWED`, inspect the implementation report's separate
closure assessment, its recorded user choice, evidence references, and assessed
input identities. Require a `COMPLETE` ordinary review and current `ELIGIBLE`
closure, with the omitted guarantees made clear. Check current inputs instead of
trusting the saved label alone. If evidence or eligibility is invalidated, use
owner-directed recovery and Reviewer reassessment under **Recovery Mechanics**;
the agent recording sign-off does not author a replacement review conclusion. An
unchanged, applicable assessment does not require another review merely because
the user is now accepting it. Then transition to `SIGNED_OFF`, set
`CycleMode: UNSET`, leave all pending-cycle fields clear, record
`Handoff.Kind: SIGNOFF`, `From: AWAITING_USER_SIGNOFF`, and `FailureType: NONE`,
clear `Active Work.PendingVerificationCadence` to `NONE`, preserve
`Active Work.CompletionPolicy`, and clear recovery. The cycle is complete. A
policy-selection instruction alone is not user sign-off.

## Cancel an active cycle

- If `ProjectMode: GREENFIELD`, follow **Greenfield Bootstrap Cancellation**,
  including explicit approval of the reset before it runs.
- If `ProjectMode: BROWNFIELD`, transition to `CANCELLED`, set
  `CycleMode: UNSET`, leave all pending-cycle fields clear, record
  `Handoff.Kind: CANCEL`, set `From` to the interrupted state,
  `FailureType: NONE`, preserve `Active Work` except for clearing
  `PendingVerificationCadence` to `NONE`, and clear recovery plus outstanding
  obligations. Preserve `Active Work.CompletionPolicy` as historical context.
  Residual project-change provenance is handled through `BaselineReconciliation`
  when a later cycle starts.

Cancellation never reverts project artifacts and does not by itself establish
cancelled-cycle project changes as baseline.

## Greenfield Bootstrap Cancellation

If cancellation occurs while `ProjectMode` is still `GREENFIELD`, first verify
that the active cycle has not successfully created or materially modified a
project implementation artifact, regardless of authorship. If it has, persist
the permanent `BROWNFIELD` transition and use retained brownfield `CANCELLED`
semantics instead, even if recovery has moved to an earlier workflow state. Only
when no such implementation exists is cancellation a reset of the workflow
rather than a reusable terminal cycle.

The agent performs the reset with **Project Reset**, following **Running the
CLI**, both in `.standards/protocol/installation.md`:

1. Use `standards reset --mode greenfield` if a globally installed STANDARDS CLI
   has the version recorded in `.standards/VERSION.json`; otherwise use
   `npx @idinsight/standards@<that version> reset --mode greenfield`. The cycle
   produced no implementation, so the project stays `GREENFIELD`; do not let the
   reset choose the mode from the project's files.
2. Preview it with `--dry-run`, which may run before approval. Also warn that
   the reset deletes the saved workflow state, the Auditor's project context,
   and every cycle record under `.standards/docs/`, and that the skills, hooks,
   client settings, and user styles stay installed.
3. Ask for explicit approval to run the exact command with `--yes`. A generic
   cancellation request does not grant it. Record the target, command, and
   pending approval in `Active Work.BlockedOn`, preserving any other unresolved
   questions, and keep the workflow state, cycle mode, recovery, and outstanding
   obligations intact while waiting. Do not record `CANCELLED` or continue role
   work while approval is pending. A resumed chat must resolve the saved
   question; silence or a request to continue is not approval.
4. After approval, recheck bootstrap eligibility and the preview. If
   implementation now exists, persist `BROWNFIELD` and use retained cancellation
   instead. If the target, command, planned changes, or warnings changed, obtain
   fresh approval for the updated preview. Otherwise run the approved command
   with `--yes`; the flag avoids a second CLI prompt and never substitutes for
   user approval.
5. Confirm that the reset succeeded before reporting the cancellation complete.
   Do not edit `STATE.md` afterwards to record it; the fresh state is the
   result. Reset approval authorizes only the reset, not starting a new cycle or
   resuming role work. After reporting success, stop and wait for a new request.

If approval is declined, clear only the approval question, keep the active
cycle, and report that cancellation was not completed; further role work
requires a user instruction to continue. If a matching CLI is unavailable, the
preview fails, or the reset refuses or fails, stop and report that cancellation
did not complete, including any backups the CLI reports, and do not work around
a refusal by switching commands or by deleting or rewriting the files yourself.
A retry requires a valid preview and approval covering it.

The reset leaves no `CANCELLED` state or cycle record behind, so the next
request starts a first greenfield cycle from the fresh state. S.T.A.N.D.A.R.D.S.
does not revert the project working tree; reverting project changes is the
user's responsibility.

## Cycle IDs

Every cycle has a unique ID. Get it only from
`node .standards/bin/cycle.mjs new --request "<request>"`. The tool:

1. builds an ID from the request, the UTC time, and eight random hex digits, for
   example `add-user-search-20260927T190146Z-7bef0f04`;
2. checks that no cycle-owned artifact path or STANDARDS provenance block uses
   it; and
3. prints it without changing any file.

It refuses while a cycle is active (`Active Work.Id` is set and the state is not
terminal), when `STATE.md` has a merge conflict, and when `STATE.md` is invalid.
Only after the tool succeeds may the printed ID be written to `Active Work.Id`
and cycle initialization continue. If initialization fails afterwards, run the
tool again for the next attempt; an unused ID needs no cleanup.

Never write a cycle ID yourself or reuse one. `check` reports an
`Active Work.Id` that does not have the generated form, and a cycle that is
active under the ID of a cycle that the last commit ended in `SIGNED_OFF` or
`CANCELLED`: a new cycle always gets a new ID, and a finished cycle is never
reopened.
