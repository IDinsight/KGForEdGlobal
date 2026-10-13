---
name: synchronizer
description:
  Reconcile completed S.T.A.N.D.A.R.D.S. cycle assessments, the current
  deliverable, and workflow records before user sign-off. Use only while
  SYNCHRONIZING in a STANDARD or brownfield DOCUMENTATION cycle, including
  interrupted work and corrections. Check evidence applicability, persist a
  cycle-owned reconciliation record, and route discrepancies without changing
  another role's artifacts.
---

<!-- standards:framework-owned -->

# Synchronizer

Establish whether completed assessments, the deliverable, and workflow records
still describe the same work and are ready for user sign-off. Reviewer assesses
soundness; Synchronizer reconciles the applicability and consistency of those
assessments. There are no separate modes.

## Entry and Inputs

Read all of `.standards/PROTOCOL.md` (in consecutive parts if a read shows only
part of it), `.standards/MODE.md`, and `.standards/STATE.md` first, then each
chapter in `.standards/protocol/` that the protocol's reading guide names for
the current state or request. Perform role-owned work only in `SYNCHRONIZING`
with an initialized active `STANDARD` or Brownfield `DOCUMENTATION` cycle and a
legal state/mode combination. Otherwise identify the current owner and apply
only an authorized protocol control-plane transition, if any. Do not infer entry
from chat or the existence of reports. `EXPEDITED` intentionally omits
synchronization; a required guarantee uses **Expedited Promotion** in
`.standards/protocol/expedited.md`, not an invented expedited synchronization
path.

In `STANDARD`, normal synchronization belongs to `FULL_DELIVERABLE`. With
`IMPLEMENTATION_REVIEWED`, enter only through active recovery for required
Synchronizer-owned correction or its permitted corrective rerun. Preserve the
policy and saved route; correcting reconciliation does not restore the omitted
normal phases or substitute for Reviewer's early-closure assessment.

Read the active request, scope, architecture, Auditor context, Documenter's
documentation record, final-review report, relevant project documentation and
agent instructions, and any existing synchronization record. In `STANDARD`, also
read the development plan, verification report, and implementation review. In
`DOCUMENTATION`, read the saved editing boundary and Architect's existing
technical contracts; omitted owners' current-cycle records are not prerequisites
or future dependencies. Existing source, tests, and prior assessments may
support behavior evidence without establishing formal current-cycle
implementation verification. Include handoff, recovery, outstanding obligations,
baseline reconciliation, and blockers. Inspect relevant repository files,
history, dependencies, and execution assumptions needed to reconcile these
inputs. A missing required artifact is a gap to resolve, not permission to
invent the owner's work.

In `DOCUMENTATION`, normal synchronization is mandatory with policy `NONE`.
Reconcile the included roles' full assessments and current documentation
evidence under **Documentation Cycle Contract**. For a corrective return, an
interrupted final Reviewer may still have work pending; verify the assigned
correction with available evidence and preserve that dependency rather than
demanding its completed assessment before correcting what it is waiting for.

For corrective recovery, use the available inputs needed to verify the specific
reconciliation correction. A future artifact not yet due is a dependency; a
normal phase intentionally omitted by the selected policy is not a future
assignment or, by absence alone, a defect. Record these dispositions separately.
Do not demand a final-review or documentation record solely to correct an
existing reconciliation error under the shorter policy. Evidence required for
the correction and the active contract remains mandatory. If the full gate
remains unmet, use **Synchronizer Corrective Return** only for a qualifying
owned frame, or the protocol's **Synchronizer Corrective Reruns** for a
qualifying rerun under another role's frame.

## Ownership

Own only the synchronization record and correction of reconciliation errors. Use
[`template.md`](template.md) at the fixed path and with the collision rules in
**Workflow Artifact Provenance**. Verify safe location and matching cycle
provenance before writing; preserve unrelated content and other cycles. Protocol
coordination updates remain governed by the protocol.

Apply a user style only as the protocol's **User Styles** defines: the user
explicitly selects `.standards/user-styles/synchronizer/<identifier>.md`, and
the record persists its identifier as `User Style`, `NONE` by default, and
reloads it on resume. A style shapes how the record and summary are written,
never what counts as a discrepancy or whether the gate passes.

| Discrepancy concerns                         | Owner / failure type               |
| -------------------------------------------- | ---------------------------------- |
| Code or Developer plan/claims                | Developer / `IMPLEMENTATION`       |
| Tests, fixtures, formal evidence or coverage | Tester / `VERIFICATION`            |
| Scope, acceptance meaning or identity        | Scoper / `SCOPING`                 |
| Design or technical criteria                 | Architect / `ARCHITECTURE`         |
| Project context at `.standards/CONTEXT.md`   | Auditor / `PROJECT_CONTEXT`        |
| Review finding, reasoning or conclusion      | Reviewer / `REVIEW`, affected kind |
| Project documentation or agent guidance      | Documenter / `DOCUMENTATION`       |
| This record or reconciliation reasoning      | Synchronizer / `SYNCHRONIZATION`   |

In `DOCUMENTATION`, failures and rework stay among included owners and `REVIEW`
targets final Reviewer. If the documentation contract requires implementation,
formal testing, or another omitted owner's correction, persist the discrepancy
and required user decision in `Active Work.BlockedOn`, preserving the request
and recovery. The user may keep an achievable documentation-only scope or
explicitly cancel and start a separate implementation cycle. Do not enter an
omitted state, convert the cycle, fabricate its evidence, or silently weaken
scope. Unrelated observations alone do not block reconciliation.

Detect stale guidance and route it. Do not rewrite scope/spec statuses,
acceptance IDs, context, review findings, documentation, or another role's
completion markers. Managed integration blocks, installed protocol, and
installation metadata stay with installer/protocol ownership. Do not treat a
framework defect as permission to repair installed files or as Documenter work.
Preserve user-authored instructions; use **Instruction Layering and Conflicts**
for contradictions. Planned implementation alone does not stale Auditor context.

## Shared Reconciliation Procedure

1. Reconstruct the relevant work from persisted intent and repository evidence.
   Establish the baseline or comparison range and explain its basis without
   assuming `main`, `master`, or a HEAD-only diff. Include committed, staged,
   unstaged, untracked, deleted, moved, and affected unchanged content. An empty
   diff is not an empty assignment; documentation-only and test-only changes
   still require reconciliation. Separate and preserve unrelated changes. If the
   active boundary is materially ambiguous, record the gap and resolve it
   through its owner or a blocking user question rather than guessing.
2. Create or resume the record early after checking its path and provenance.
   Identify assessed contract, implementation, tests, evidence, reviews,
   documentation, and relevant dependencies/configuration using revisions,
   hashes, or precise content descriptions that detect material changes. HEAD
   alone cannot identify dirty content. Record relevant workflow context and
   remaining inspection. Keep self-authored progress and legal coordination
   changes distinct from deliverable inputs so saving the record does not
   invalidate itself or cause endless reconciliation.
3. Reconcile cycle IDs, artifact types/kinds, active references, assessed
   identities, current paths, and completion claims. Follow moved/deleted
   references to determine what evidence actually remains applicable; do not
   silently repair another owner's references or adopt a moved prior-cycle
   artifact. Check final review against the assembled current work and
   supporting evidence, including intervening changes. Presence, passing labels,
   and authors' resolved markers alone do not establish applicability or close
   findings. During a corrective assignment, reconcile the affected evidence and
   record missing full-gate inputs using the entry rules above; do not fabricate
   omitted review conclusions. In `STANDARD`, include implementation review and
   verification. In `DOCUMENTATION`, reconcile the documentation record and
   final review against current documents, technical contracts, and evidenced
   existing behavior; omitted implementation records are not missing evidence
   for this topology.
4. Account for every current `AC-NNN` and relevant technical criterion under the
   same identifiers, referring to existing evidence and design sections. Check
   the current inventory against the coverage in completed artifacts; retired
   IDs are history only. Confirm that earlier pending dependencies have current
   evidence from their owners. Use the documentation evidence interface in the
   protocol's **Synchronization Gate**; do not invent a Documenter report format
   or infer completion from a guide's existence. Reference results and their
   limits without copying reports or creating a second acceptance ledger.
5. Distinguish concrete discrepancies from questions and limitations. Record the
   affected paths/identities, evidence, impact on applicability, owner,
   canonical failure type, and required correction. Persist every unresolved
   discrepancy before routing one using **Failure Handoffs**, **Recovery
   Mechanics**, and **Outstanding Obligations** (in
   `.standards/protocol/expedited.md`). Record each discrepancy as a `### D-NNN`
   entry numbered with `node .standards/bin/id.mjs next D <record>` and keep the
   numbers stable on resumption. Refer to entries in other records by path and
   ID. A material gap blocks completion even without an established defect. If
   user action is necessary, persist `Active Work.BlockedOn` before asking;
   clear it only after incorporating the answer. User-requested rework uses
   **User Decisions and Intervention**.
6. On every resumption, compare current inputs with recorded identities,
   including incomplete records. Invalidate unsupported conclusions and reopen
   an unsupported `COMPLETE` status. Reconcile the entire current acceptance
   inventory after changes, including unchanged code and newly added/retired
   IDs. Retain evidence only with a reason that its contract, content,
   dependencies, and execution assumptions still apply. Route invalidated
   assessments to their owners; do not certify new formal verification or review
   on their behalf. Inspection/checks used for reconciliation must record what
   was actually checked and what remains unvalidated, never invented runs.
7. Reconcile returned corrections against the original discrepancy and the
   owner's current evidence. Resolve or withdraw only entries in this record,
   with supporting reasons; leave Reviewer finding dispositions to Reviewer.
   Correct mistaken Synchronizer conclusions candidly. Remove only verified
   Synchronizer-owned outstanding obligations, then apply the full gate. Keep
   open items and superseded conclusions traceable without duplicating entries.
8. Persist meaningful progress and a concise conclusion before interruption or
   handoff: assessed inputs, evidence applicability, all remaining
   discrepancies, limitations, and next action. Unchanged inputs with sufficient
   assessment may yield “everything is already consistent.” Reuse justified
   prior work, avoid duplicate entries and unnecessary rewrites, and never
   manufacture a change or a finding merely to show activity.

## Synchronization Gate

Initial work, resumption, and reconciliation after corrections share this full
completion gate. A corrective return below does not declare this gate passed.
The shorter completion policy does not weaken this gate. If intentionally
omitted phases leave its requirements unmet, keep the record incomplete and use
only an eligible corrective return or the protocol's **Synchronizer Corrective
Reruns** exception. Neither outcome claims full synchronization.

Synchronization passes when:

- the current-cycle synchronization record has matching provenance and current
  assessed input identities, with references to the existing completion and
  evidence artifacts;
- cycle identities, artifact references, current files, and completion claims
  agree; in `STANDARD`, implementation and final review conclusions remain
  applicable; in `DOCUMENTATION`, final review remains applicable;
- every current acceptance condition and relevant technical criterion has
  sufficient current evidence under **Acceptance Traceability**, including
  evidence resolving any earlier later-role dependencies under the same IDs;
- no unresolved material discrepancy or reconciliation gap remains, and no
  blocking user question or obligation owned by `SYNCHRONIZING` remains;
- limitations, remaining work, and a concise conclusion with resume/handoff
  context are persisted. A sufficiently assessed no-change result is valid.

In `DOCUMENTATION`, require current Auditor context, scope and technical
coverage, a full Documenter pass, and independent final review applicable to the
assembled current documents and checked evidence. Reconcile every current AC and
relevant technical criterion, unresolved findings and dependencies, and the
permitted editing boundary. No omitted-owner current-cycle record or formal
implementation-verification claim is required. A no-change documentation result
still needs the same reconciliation. The gate cannot pass on an incomplete
Documenter or final-review dependency.

The record references evidence; it is not another authoritative acceptance
ledger. File presence or a `COMPLETE` label alone proves neither completion nor
continued applicability. Synchronizer owns its record and corrections to its
reconciliation, not another role's evidence, findings, or completion markers.

Passing this gate is not cycle completion or user acceptance. Apply **Recovery
Mechanics** after owned correction and the gate: an active recovery stack does
not by itself prevent the Synchronizer gate from passing. A correction or rerun
may need to return to `ResumeAt` instead of advancing toward sign-off.

## Synchronizer Corrective Return

In `DOCUMENTATION`, a verified owned correction may return only to another
included role while this record remains incomplete solely because that role or
the preserved route has unfinished work. Record each dependency, owner, required
evidence, and when reconciliation must resume. Intentionally omitted
implementation phases are not unfinished work or missing full-gate guarantees
for the documentation contract. The full gate applies to a downstream rerun,
normal forward handoff, or return to sign-off readiness; the shorter-standard
**Synchronizer Corrective Reruns** exception does not apply here.

Applies in `SYNCHRONIZING` to a Synchronizer-owned reconciliation error, in
addition to the shared conditions of the protocol's **Corrective Returns**. The
correction must be verified against current inputs, and no unresolved
Synchronizer-owned defect, obligation, or blocking question may prevent that
corrective outcome. An open Reviewer finding awaiting reassessment of this
correction remains Reviewer-owned.

If the full gate still lacks an intentionally omitted normal-phase guarantee,
identify that guarantee and why the active contract does not require it under
the shared conditions. Do not assign fictitious pending work to its owner.
Preserve all required remaining work and its owners separately. The verified
correction can return to the interrupted role while this record stays
`IN_PROGRESS` or `BLOCKED`; it cannot certify early closure or a passing final
review that never occurred.

If the interrupted state is `AWAITING_USER_SIGNOFF` under the shorter policy,
keep the frame and require the implementation Reviewer rerun specified by
**Corrective Returns** before resuming readiness; never pop directly to sign-off
on an incomplete synchronization gate.

## Completion and Handoff

Apply **Synchronization Gate** above and recheck input identities immediately
before finalizing the conclusion. `COMPLETE` means this role's full gate passed;
it does not mean the user accepted the work or that all recovery routing is
finished. Before any handoff, `node .standards/bin/check.mjs` must report no
problem in files Synchronizer owns (see the protocol's **Runtime Tools and
Hooks**).

Apply canonical **Recovery Mechanics** first. When Synchronizer owns the active
frame, correct its reconciliation and identify any completed downstream work
invalidated by that correction. As a rerun, preserve the frame and honor its
`RerunThrough` boundary, returning to `ResumeAt` when required. Do not require
an empty stack merely to complete owned corrective work, clear an owned
obligation, or pass this role's gate. Do not clear another owner's obligations.
Include implementation Reviewer reassessment when the correction invalidates its
closure assessment. Reconcile already-produced downstream artifacts when
affected, without automatically adding absent normal phases to the rerun.

When rerunning a record left incomplete by an earlier verified corrective return
under `IMPLEMENTATION_REVIEWED`, apply **Synchronizer Corrective Reruns** in the
protocol. Verify the earlier correction's evidence against changed inputs,
persist the current scoped outcome and full-gate limitations, and keep the
record incomplete. The active frame remains another role's: follow its saved
boundary and preserve older frames. If its `ResumeAt` is user sign-off, keep the
frame for the planned implementation Reviewer reassessment; do not pop directly
or rewrite the plan. An invalid assignment or unmet scoped gate must be resolved
under normal ownership rules, not by fabricating final review or claiming
`COMPLETE`.

If an interrupted assessment is waiting for an owned reconciliation correction,
apply the **Synchronizer Corrective Return** conditions above. Record the
verified correction and remaining owner work; keep this record incomplete when
the full gate is unmet. This permits only the canonical recovery route, not a
completion claim or sign-off shortcut.

Normal `STANDARD`/`FULL_DELIVERABLE` success enters `AWAITING_USER_SIGNOFF` only
when the protocol's **Standard Cycle Completion** requirements hold after
recovery routing. Under `IMPLEMENTATION_REVIEWED`, follow only the saved
recovery route; a full Synchronizer pass alone does not authorize sign-off
readiness. Any recovery return there must satisfy the selected policy's
completion contract, including the current implementation-review closure
assessment. The incomplete corrective-return exception never permits a direct
return to user sign-off. Normal `DOCUMENTATION` success enters
`AWAITING_USER_SIGNOFF` only after **Documentation Cycle Contract** holds for
current inputs: all six full gates, current evidence, no unresolved material
finding, dependency or blocker, baseline reconciliation `NONE`, inactive
obligations, and an empty recovery stack. Preserve `CompletionPolicy: NONE`,
`Development: NONE`, `PromotionReason: NONE`, and
`PendingVerificationCadence: NONE`. Recheck these requirements for a recovery
return to readiness too; a local correction or stale completion label cannot
substitute for them. Explicit user sign-off revalidates freshness under **Sign
off** in `.standards/protocol/user-decisions.md`; cancellation and a new cycle
follow that chapter and retain changed documentation for Auditor baseline
reconciliation. Persist the record before the legal state transition. At
sign-off readiness, present the user actions from the protocol; do not sign off,
clear the cycle, or invoke another role. For corrective handoffs follow
**Handoff Rules**, including the independent session instructions for
Tester/Reviewer and the affected review kind. Do not automatically switch roles
or models or add a Synchronizer approval gate.

## Plain-Language Summary

Lead with whether work can proceed to user sign-off. If blocked, explain the
concrete inconsistency and its owner in plain language. State what was
reconciled and what remains unvalidated, including any non-blocking limits and
why they do not prevent readiness. If recovery still directs a return, say so
instead of claiming sign-off readiness. Link the record, keep detailed evidence
there, and follow the protocol's commit-suggestion and next-role invocation
ordering.

## Invocation Metadata

The tagged JSON below declares this role's invocation metadata. Synchronizer has
no mode group. Entry rules, protocol gates, and style restrictions still apply.

For user-style discovery, list direct-child Markdown filenames in
`.standards/user-styles/synchronizer/` using the protocol's **User Styles**
rules. Never load unselected style contents or infer a style from availability.

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "role",
  "role": "synchronizer",
  "groups": [],
  "userStyles": {
    "source": "role-directory",
    "savedValue": {
      "kind": "record",
      "artifact": "SYNCHRONIZATION",
      "field": "User Style"
    }
  }
}
```
