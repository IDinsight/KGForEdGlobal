# Autonomous Mode

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "collaboration",
  "id": "AUTONOMOUS",
  "label": "Autonomous",
  "description": "Apply documentation changes within the editing boundary.",
  "selection": "user"
}
```

Use the shared Documenter procedure and completion gate. Apply documentation
changes within the selected editing boundary, inspect saved results, and run
appropriate checks without routine step or plan approval. Persist meaningful
progress and unresolved work normally. Pause only for a genuinely blocking
question, dependency, or permission limit; a selected target does not waive
required work elsewhere in the cycle.
