# Autonomous Mode

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "collaboration",
  "id": "AUTONOMOUS",
  "label": "Autonomous",
  "description": "Execute approved work without routine pauses.",
  "selection": "user"
}
```

Use after the user approves the current development plan when Developer should
carry implementation through without pausing between atomic steps.

## Procedure

1. Start at the next dependency-ready approved step in the persisted assignment.
2. Set the step to `IN_PROGRESS`, implement it, run its self-check, then set it
   to `DONE` only when the expected outcome exists.
3. Apply **Assignment Gates and Handoffs** in `../SKILL.md` after each ready
   outcome. At a checkpoint or corrective return, persist the handoff and stop;
   otherwise continue directly to the next assigned approved step.
4. Also stop for a blocking user question, a required material plan revision, a
   protocol failure handoff, expedited promotion, cancellation, or completion.
5. Keep progress persisted in the development plan so another session can resume
   from the current assignment. After a Tester return and explicit Developer
   invocation, reconcile the result before continuing approved work.

Do not ask for routine confirmation between approved steps. User approval of the
current material plan is the execution authority for this mode.
