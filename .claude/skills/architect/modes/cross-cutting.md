# Cross-Cutting Architecture

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "design-mode",
  "id": "CROSS_CUTTING",
  "label": "Cross-cutting",
  "description": "Design shared technical rules across project boundaries.",
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

Use when the scope requires one technical mechanism, contract, or rule to apply
consistently across multiple project boundaries.

Focus on the canonical behavior and where that behavior begins, ends, and is
enforced.

## Design Emphasis

1. Define the shared mechanism, contract, or rule precisely enough to produce
   consistent implementation across affected areas.
2. Identify adoption boundaries, integration points, ownership, and enforcement
   points.
3. Define material exceptions or extension points only when the scope requires
   them.
4. Address rollout or coexistence with existing behavior when introducing the
   rule into an established project.
5. Avoid turning the design into a broad coding-style or convention document;
   keep it tied to the scoped technical requirement.
