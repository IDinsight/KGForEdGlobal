# Documentation Architecture

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "design-mode",
  "id": "DOCUMENTATION",
  "label": "Documentation",
  "description": "Establish existing technical contracts for documentation.",
  "selection": "state",
  "when": {
    "fact": {
      "kind": "workflow",
      "field": "CycleMode"
    },
    "equals": "DOCUMENTATION"
  }
}
```

Use in Brownfield `DOCUMENTATION` after Scoper, including recovery reruns. Use
the shared Architect ownership, gate, and `../template.md`; this mode changes
technical emphasis, not artifact shape or routing.

Establish the existing technical contracts needed for the scoped documentation:

1. Trace relevant interfaces, commands, parameters, outputs, errors,
   prerequisites, and side effects to current source, configuration, or other
   authoritative project evidence. Identify concrete paths or sections and any
   material applicability limits so Documenter can inspect the same basis.
2. Describe relevant components, data/control flow, compatibility, security, and
   operational constraints only where they affect documentation correctness.
   Distinguish observed behavior from an intended contract or an unsupported
   claim in an existing guide; resolve material uncertainty through its owner.
3. Account for every current AC with existing technical coverage or an explicit
   no-architectural-impact disposition identifying Documenter-owned work when
   appropriate. Technical criteria may constrain documentation accuracy but do
   not promise new behavior or prescribe tests.
4. Preserve established design and useful canonical content. Omit a Build Plan
   when no implementation is permitted; do not invent migration, rollout, or
   Developer tasks to fill the template.

An implementation or formal-testing requirement uses the documentation
contract's blocking user decision. Do not redesign the project to make its
documentation true or author user-facing documentation here.
