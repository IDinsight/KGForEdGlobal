# Guided Mode

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "collaboration",
  "id": "GUIDED",
  "label": "Guided",
  "description": "Guide one user-applied step and inspect the saved result.",
  "selection": "user"
}
```

Use the shared Documenter procedure and completion gate. The user applies the
project documentation edits; Documenter persists its record and legal workflow
coordination normally.

1. Choose one coherent documentation step within the selected editing boundary.
   Provide its exact destination/section, copy/paste content, and enough context
   to preserve surrounding work. Persist the pending step, pre-edit identity,
   expected saved result, and next inspection in the documentation record.
2. Set `Active Work.BlockedOn` to the request to apply that step and wait. Do
   not apply it for the user, present a batch of future steps, or mark it
   complete from the snippet alone.
3. After the user reports applying it, inspect the actual saved file and
   relevant checks before continuing. If the file is unavailable, persist that
   evidence gap; if unchanged or partially applied, keep the work pending and
   explain the smallest correction. A user's “done” is not proof of saved
   content.
4. Record the observed result and clear the matching blocker once the step is
   verified. Then offer the next single step, or apply the shared gate if all
   required work is complete. On resume, inspect for an already-applied pending
   step before repeating it; reconcile intervening edits rather than overwriting
   them. An explicit switch to AUTONOMOUS permits direct edits after this
   reconciliation and does not reset evidence or the target.
