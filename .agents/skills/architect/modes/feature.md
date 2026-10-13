# Feature Architecture

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "design-mode",
  "id": "FEATURE",
  "label": "Feature",
  "description": "Design a bounded capability when it is the primary concern.",
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

Use when the scope adds or changes a bounded capability and the capability
itself—not foundational structure, transition/compatibility, or a shared
cross-boundary rule—is the primary design concern.

Focus on the smallest technical design that fully resolves the capability's
material implementation decisions.

## Design Emphasis

1. Define the components and boundaries directly involved in the capability.
2. Specify material interfaces, data/state ownership, control flow, and value
   sources.
3. Resolve integration, failure, security, compatibility, and resource behavior
   only where the scoped capability requires it.
4. Reuse established project architecture and conventions unless the scope
   requires a material change.
5. Leave local, reversible implementation details to Developer.
