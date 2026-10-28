# S.T.A.N.D.A.R.D.S. Protocol: Expedited Cycles

This chapter is part of `.standards/PROTOCOL.md`. Read that file first; its
reading guide says when this chapter applies.

## Expedited Cycle Contract

An `EXPEDITED` cycle provides a deliberately narrower completion contract:

1. It is valid only in `BROWNFIELD`, and only while the request is a
   sufficiently bounded implementation contract and no omitted role or guarantee
   is required.
2. `Active Work.Request` is the change contract. `Scope` and `Architecture`
   remain `NONE` unless promotion later causes their owners to create them.
   `Active Work.CompletionPolicy` is `NONE`; standard completion policies do not
   change the expedited path or its guarantees.
3. Existing project context may be consulted as prior evidence but is not
   refreshed by default. Prior-cycle non-baseline entries are stale for the
   current cycle.
4. Active-cycle implementation remains tentative and does not become established
   baseline, even after promotion.
5. Developer owns implementation and normal implementation-level self-checks;
   these are not Tester-owned formal verification.
6. Reviewer still owns `REVIEWING_IMPLEMENTATION` and may route implementation
   defects through normal recovery.
7. Scoper, Architect, Auditor, Tester, Documenter, `REVIEWING_FINAL`, and
   Synchronizer are absent from the expedited forward topology. Their missing
   artifacts or gates are not failures. Skipping them transfers none of their
   ownership, artifacts, or completion guarantees to Developer or Reviewer, and
   neither synthesizes the skipped work.
8. The cycle may reach `AWAITING_USER_SIGNOFF` after Developer and
   implementation Reviewer pass their gates, recovery is empty, and no blocking
   user question remains. Scope-level acceptance traceability does not apply.
9. If safe completion requires an omitted role or guarantee, including baseline
   status that needs Auditor-owned context, use **Expedited Promotion** instead
   of assigning that work to Developer, weakening ownership, or fabricating
   skipped work.

## Expedited Promotion

Promotion changes a nonterminal `EXPEDITED` brownfield cycle to `STANDARD` when
safe completion requires formal Scoping, consequential Architecture,
authoritative Auditor-owned context, Tester-owned verification, Documentation,
Final Review, Synchronization, or another intentionally omitted guarantee. It is
a topology change, not a failure handoff, and it is one-way for the active
cycle.

1. An active workflow role may promote when required. At
   `AWAITING_USER_SIGNOFF`, user authorization is required. An explicit promote
   request qualifies; so does an explicit rework request whose changed contract
   necessarily requires an omitted standard role or guarantee.
2. Set `CycleMode: STANDARD`, `WorkflowState: AUDITING`, and
   `Active Work.CompletionPolicy: FULL_DELIVERABLE`. Set
   `Handoff.Kind: PROMOTE`; set `From` to the interrupted state,
   `FailureType: NONE`, and record a concise reason identifying which omitted
   standard guarantee is now required. Persist the same reason in
   `Active Work.PromotionReason`.
3. Preserve cycle `Id`, the current `Request` (including a change made by the
   rework that authorized promotion), and role-owned artifacts. Do not fabricate
   `Scope` or `Architecture`.
4. Auditor establishes or refreshes context without laundering tentative
   expedited work into pre-existing baseline. It distinguishes pre-cycle
   baseline from active-cycle changes using authoritative evidence, and material
   ambiguity requires a user question rather than a guess.
5. Before clearing recovery, persist the frame-to-obligation conversion defined
   in **Outstanding Obligations** below, preserving each converted frame's
   `Owner`, `FailureType`, and `Reason`. Then clear the expedited recovery
   stack; the standard brownfield topology restarts at `AUDITING`.
6. All standard forward, failure, recovery, outstanding-obligation,
   traceability, and sign-off rules apply afterward. Promotion never selects
   `IMPLEMENTATION_REVIEWED`. A separate explicit user choice follows **Change
   completion policy** in `.standards/protocol/user-decisions.md` and cannot
   bypass the obligations preserved by promotion.

## Outstanding Obligations

`Outstanding Obligations` preserves unresolved corrective work when the recovery
routing that carried it is no longer valid. It is ordered oldest to newest. Each
obligation records `Owner`, `FailureType`, and `Reason`; it deliberately has no
`From`, `ResumeAt`, or `RerunThrough`.

Normally the recovery frame itself is the durable corrective obligation and no
duplicate outstanding obligation is created. `RerunThrough: NONE` means the
frame's owner has not yet completed its correction; a non-`NONE` `RerunThrough`
means the owner already passed its corrective gate and the frame remains only to
finish downstream rerun/resume routing. During **Expedited Promotion**, convert
each recovery frame whose `RerunThrough` is `NONE` into one outstanding
obligation before clearing the expedited recovery stack. Do not convert frames
whose `RerunThrough` is non-`NONE`. Preserve converted obligations in
recovery-stack order, keep each distinct defect separate, and do not collapse
defects merely because they share an owner or failure type.

When one or more obligations exist, set `Active: true` and record each as a
numbered `### Obligation N` entry containing `Owner`, `FailureType`, and
`Reason`. When an obligation is removed, renumber the remaining entries 1, 2, 3,
... in their existing order. When the last obligation is removed, set
`Active: false` and remove the numbered entries.

An outstanding obligation remains until its owning state is reached and the
owner corrects and verifies the specific defect recorded by the obligation.
Remove a corrected obligation as soon as that corrective outcome is verified;
removing it records only that the obligation itself is satisfied, not that the
owning role is otherwise complete. The owner must still pass its normal
completion gate, including having no unresolved obligation owned by its current
state, before any normal forward handoff. User sign-off is unavailable while any
outstanding obligation remains.
