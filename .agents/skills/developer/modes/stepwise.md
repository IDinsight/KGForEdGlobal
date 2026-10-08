# Stepwise Mode

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "collaboration",
  "id": "STEPWISE",
  "label": "Stepwise",
  "description": "Implement one approved step, then wait for the user.",
  "selection": "user"
}
```

Use after the user approves the current development plan when Developer should
implement one atomic step at a time and let the user control progression.

## Procedure

1. Start at the next dependency-ready approved step in the persisted assignment.
2. Set it to `IN_PROGRESS`, implement it, and run its self-check.
3. Set it to `DONE` only when the expected outcome exists.
4. Summarize the completed `DEV-NNN`, material files or areas changed, and the
   self-check result.
5. Apply **Assignment Gates and Handoffs** in `../SKILL.md`. If an assignment
   gate passes, persist its handoff and stop without adding a next-step blocker.
   Otherwise, if another assigned step remains, persist `Active Work.BlockedOn`
   as a concise request to continue with the next step and stop. A multi-step
   increment does not bypass these per-step pauses.
6. When the user continues, clear that blocker and execute exactly the next
   approved step unless the user revises the plan or changes collaboration mode.

After a Tester return, reconcile the result and retain user control of the next
step. The assessment alone does not authorize continuing implementation.

A request to adjust a future step may be a plan revision. If it materially
changes implementation intent, return the plan to `PROPOSED` and obtain approval
before implementing the changed step.
