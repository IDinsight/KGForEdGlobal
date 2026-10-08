# Greenfield Audit

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "inspection-mode",
  "id": "GREENFIELD",
  "label": "Greenfield",
  "description": "Audit after initial greenfield scope and architecture.",
  "selection": "assessment"
}
```

Use only for the scheduled initial audit while `ProjectMode` is `GREENFIELD`,
after Scope and Architecture are complete.

The goal is to establish the factual baseline of the current repository or
scaffold after scope and architecture are decided but before implementation
begins.

## Procedure

1. Read the completed scope and technical design referenced by
   `.standards/STATE.md`.
2. Inspect the current repository or scaffold, project instructions, manifests,
   configuration, scripts, tests, and documentation needed to understand the
   starting point.
3. Separate existing repository facts and independently established project
   constraints from planned technical design.
4. When the active technical design contains planned changes that are not yet
   represented by the repository, note that distinction without reproducing
   those planned technical decisions in `.standards/CONTEXT.md`; defer to
   `Active Work.Architecture` for their contents.
5. Do not describe planned components, files, interfaces, commands, or behavior
   as existing facts until repository evidence supports them.
6. Capture enough baseline to let Developer distinguish what must be preserved
   from what the design requires it to create, without turning project context
   into a copy of the technical design.
7. Write or refresh `.standards/CONTEXT.md` using `../template.md`.

## Mode-specific completion condition

The baseline must make the pre-implementation state clear enough that downstream
roles cannot confuse planned architecture with existing implementation, while
leaving the contents of planned technical design authoritative only in
`Active Work.Architecture`.
