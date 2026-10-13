# FINAL_DELIVERABLE Mode

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "review-kind",
  "id": "FINAL_DELIVERABLE",
  "label": "Final deliverable review",
  "description": "Assess the assembled deliverable and current evidence.",
  "selection": "state",
  "when": {
    "fact": {
      "kind": "workflow",
      "field": "WorkflowState"
    },
    "equals": "REVIEWING_FINAL"
  }
}
```

Use only in `REVIEWING_FINAL` during `STANDARD` or `DOCUMENTATION`. Assess the
assembled deliverable after documentation: current acceptance evidence,
documentation accuracy, unresolved findings, and consistency across relevant
artifacts. In `STANDARD`, include implementation and verification and examine
prior implementation-review conclusions against current content rather than
assuming they still hold.

In `DOCUMENTATION`, apply **Documentation Cycle Contract**: assess the saved
documentation and its checks against current scope, technical contracts, Auditor
context, and existing behavior. Independently examine each current AC and
relevant technical criterion. Implementation, formal verification, and
implementation review are intentionally omitted; do not require their
current-cycle records or invent their guarantees. No-change documentation still
needs this full assessment. Normal success hands off to Synchronizer with policy
`NONE`; recovery stays within included roles.

In `STANDARD`, the normal final-review phase belongs to `FULL_DELIVERABLE`.
Under `IMPLEMENTATION_REVIEWED`, this kind is available only through active
recovery for affected final-review work; omission of the normal phase alone is
not a defect. Preserve the selected policy and use the saved recovery route. The
full final-review gate still applies; its correction or passage does not replace
the implementation report's separate closure assessment or start normal
synchronization. If the correction invalidates that closure assessment, include
implementation Reviewer reassessment under **Recovery Mechanics**.

Use the shared procedure, findings, ownership, **Review Gates**, and completion
requirements in `../SKILL.md`, and the shared `../template.md`. Resolve earlier
dependencies with current evidence under the same acceptance IDs. Re-review and
interrupted work use this same mode. Do not run it in `EXPEDITED` or claim that
passing it completes Synchronizer work or user sign-off.
