# Subtree Audit

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "inspection-mode",
  "id": "SUBTREE",
  "label": "Subtree",
  "description": "Inspect an explicit area with a usable project baseline.",
  "selection": "assessment"
}
```

Use when a usable project-level `.standards/CONTEXT.md` already exists and the
audit has an explicitly identified target area whose local facts require deeper
inspection or refresh.

A subtree scan supplements the project baseline; it does not replace the need
for a usable repository-level baseline.

## Procedure

1. Identify the target subtree from persisted active work, an upstream artifact,
   an active `PROJECT_CONTEXT` defect, or `Active Work.AuditTarget`. If the
   target comes only from explicit user direction while Auditor owns `AUDITING`,
   persist it in `Active Work.AuditTarget` before relying on it.
2. Inspect the target subtree and only the external code, shared packages,
   configuration, tests, or integrations needed to understand its real
   boundaries and dependencies.
3. Preserve still-valid global context. Replace or extend only the facts
   materially affected by the targeted scan.
4. Do not infer project-wide conventions from one subtree unless corroborating
   repository-level evidence establishes them.
5. Rewrite `.standards/CONTEXT.md` as one coherent baseline using
   `../template.md`; do not append a separate subtree audit log.

## Mode-specific completion condition

The project-level baseline remains intact, and the target area's relevant
structure, behavior, constraints, dependencies, and evidence are accurate enough
for the active cycle.
