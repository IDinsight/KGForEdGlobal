# Evolution Architecture

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "design-mode",
  "id": "EVOLUTION",
  "label": "Evolution",
  "description": "Design transitions and compatibility for existing systems.",
  "selection": "assessment",
  "when": {
    "fact": {
      "kind": "workflow",
      "field": "CycleMode"
    },
    "equals": "STANDARD"
  }
}
```

Use when the scoped work materially changes, replaces, migrates, or restructures
an existing technical design and the transition from current to target state
matters.

Focus on both the target design and the safe path from established project
reality to that target.

## Design Emphasis

1. Identify the existing architecture or contract being changed and the target
   design that replaces or modifies it.
2. Define compatibility requirements, coexistence boundaries, migration or
   cutover sequence, and retirement conditions when relevant.
3. Address data migration, rollout, rollback, versioning, or transitional states
   only when they materially affect implementation.
4. Preserve unaffected established behavior and constraints instead of treating
   the change as greenfield design.
5. Make temporary architecture explicit so it is not mistaken for the intended
   steady state.
