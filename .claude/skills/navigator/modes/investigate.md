# Investigate Mode

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "conversation-mode",
  "id": "INVESTIGATE",
  "label": "Investigate",
  "description": "Trace a concrete repository question through evidence.",
  "selection": "user"
}
```

Use the shared Navigator procedure and boundaries. Define the concrete question,
follow the relevant evidence, and test possible explanations against it. Seek an
observation that distinguishes plausible causes instead of listing guesses as
facts or running mutation-prone experiments.

Return a supported answer with exact evidence and material limits. If evidence
cannot settle the question, say what is known, what remains uncertain, and the
smallest next observation that would distinguish the remaining possibilities.
Explain a suspected defect and likely owner without fixing it or issuing a
formal assessment. Do not keep investigating indefinitely when the missing
observation is unavailable within Navigator's boundary.
