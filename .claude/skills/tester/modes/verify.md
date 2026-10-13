# VERIFY Mode

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "verification-mode",
  "id": "VERIFY",
  "label": "Verify",
  "description": "Establish verification or resume with unchanged inputs.",
  "selection": "assessment"
}
```

Establish initial verification for the active cycle. Resume this mode after an
interruption when its assessed inputs are unchanged; a partial report alone is
not a reason to select REVERIFY.

Build the assessment from persisted intent and repository evidence using the
shared procedure in `../SKILL.md`. Reuse existing tests and valid results
already recorded during this pass, then fill only the remaining gaps. Preserve
budget allocations and the next action across sessions and increments.

Use `../template.md` and **Assignment Gates and Handoffs** in `../SKILL.md` for
the persisted purpose and target. This mode introduces no separate approval,
technique restriction, or transition rule.
