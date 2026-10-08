# IMPLEMENTATION Mode

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "review-kind",
  "id": "IMPLEMENTATION",
  "label": "Implementation review",
  "description": "Assess implementation and the applicable evidence.",
  "selection": "state",
  "when": {
    "fact": {
      "kind": "workflow",
      "field": "WorkflowState"
    },
    "equals": "REVIEWING_IMPLEMENTATION"
  }
}
```

Use only in `REVIEWING_IMPLEMENTATION`. Assess implementation against the active
contract, upstream consistency, and Developer claims. In `STANDARD`, include
Tester coverage and evidence for every current acceptance condition and relevant
technical criterion. In `EXPEDITED`, use the bounded request and Developer
evidence under the narrower protocol contract.

Use the shared procedure, findings, ownership, **Review Gates**, and completion
requirements in `../SKILL.md`, and the shared `../template.md`. Explicit
later-role dependencies may remain only as permitted there. Re-review and
interrupted work use the same mode with input reconciliation.

For `STANDARD` with `IMPLEMENTATION_REVIEWED`, also apply the shared
**Implementation-Reviewed Closure** assessment. A passing ordinary review with a
permitted later dependency cannot establish early-closure eligibility. Keep the
two conclusions distinct and use the policy-specific handoff in `SKILL.md`.
