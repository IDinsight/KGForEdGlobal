# REVERIFY Mode

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "verification-mode",
  "id": "REVERIFY",
  "label": "Reverify",
  "description": "Reconcile an assessment after changes or a defect.",
  "selection": "assessment"
}
```

Reconcile an existing assessment after its inputs change or a verification
defect is reported, even if the earlier pass was incomplete.

Compare the report's assessed inputs with the current scope, design, context,
implementation, tests, and execution conditions. Identify affected coverage and
invalidate only unsupported evidence. Preserve valid tests, scenario
allocations, and evidence with a reason for reuse. A changed current acceptance
set requires reconciling the whole acceptance inventory, including removed and
new IDs, even when implementation is unchanged.

Continue through the shared procedure, template, budget, and **Assignment Gates
and Handoffs** in `../SKILL.md` for the persisted purpose and target. Re-execute
according to regression risk; this mode neither grants another five scenarios
nor limits reruns to previously failing files.
