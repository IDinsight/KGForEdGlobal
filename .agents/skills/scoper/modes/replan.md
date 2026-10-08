# REPLAN Mode

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "scope-mode",
  "id": "REPLAN",
  "label": "Replan",
  "description": "Reconcile the active cycle's existing scope.",
  "selection": "assessment"
}
```

Use REPLAN when `Active Work.Scope` identifies an existing persisted scope for
the active cycle and that scope must be corrected, revised, or reconciled with
changed inputs.

Typical triggers include a `SCOPING` recovery, user-requested rework, changed
upstream context, or an additional requirement introduced before the active
cycle is complete.

## Inputs

Use the Scoper skill's shared inputs and invariants. Also:

- read the full existing scope before editing it;
- read the active handoff and recovery frame, when present, to understand why
  replanning is required.

## Procedure

1. Before editing, verify the `Active Work.Scope` provenance under the shared
   protocol rules. Continue at the same path when it is either an unmarked
   project-owned document or a STANDARDS scope owned by the current cycle. Never
   edit a STANDARDS scope owned by another cycle. If a substantive reason
   requires relocation, give any newly created scope the current-cycle `SCOPE`
   provenance block and update `Active Work.Scope`.
2. Identify the smallest set of scope statements, boundaries, work items,
   dependencies, or acceptance conditions invalidated by the new information.
3. Preserve valid existing intent and unaffected scope content. Do not rewrite
   the artifact merely for style or completeness. Preserve acceptance
   identifiers whose conditions keep the same meaning; preserve the existing
   retired-identifier record; do not renumber or reuse identifiers.
4. Correct the owned scoping defect or incorporate the approved user-requested
   change without silently expanding, removing, or changing user intent.
5. Re-evaluate affected dependencies and observable completion conditions so the
   revised scope remains internally coherent and every verifiable in-scope
   obligation that must be proven remains covered. Keep separable obligations in
   different acceptance conditions when their satisfaction or evidence will be
   established in different workflow phases. Assign a new, previously unused
   acceptance identifier to each new or materially replaced condition with
   `node .standards/bin/id.mjs next AC <scope>`, writing each condition into the
   scope before requesting the next identifier, and record each removed or
   replaced identifier as retired.
6. If the revision exposes a blocking decision, persist it in
   `Active Work.BlockedOn` and stop until the user resolves it.
7. Apply the Scoper completion gate and the protocol's recovery/invalidation
   rules. If the current acceptance-identifier set changed, treat any completed
   downstream artifact required to account for every current identifier as stale
   even when its underlying technical decisions remain valid.

## Result

REPLAN succeeds when the existing active-cycle scope has been reconciled with
the new information while preserving unaffected intent.

After success, follow the shared Scoper completion, recovery, and handoff rules
in `SKILL.md` and the protocol. REPLAN does not independently choose the next
workflow state.
