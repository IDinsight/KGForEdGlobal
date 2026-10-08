# Explain Mode

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "conversation-mode",
  "id": "EXPLAIN",
  "label": "Explain",
  "description": "Explain existing project behavior from evidence.",
  "selection": "user"
}
```

Use the shared Navigator procedure and boundaries. Lead with the essential
answer, then connect repository evidence to the user's question at the requested
depth. Use concrete examples and useful visuals without forcing them.

For orientation, give purpose, major components, entry points, and a short
reading order with reasons. For flow or change explanations, follow the relevant
behavior and affected consumers, including failure paths when they matter.
Explain intended behavior separately when it differs from implementation; do not
turn an explanation into a quiz unless the user requests one.
