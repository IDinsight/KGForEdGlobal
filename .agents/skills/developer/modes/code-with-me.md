# Code With Me Mode

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "collaboration",
  "id": "CODE_WITH_ME",
  "label": "Code With Me",
  "description": "Help the user implement approved work on request.",
  "selection": "user"
}
```

Use after the user approves the current development plan when the user wants to
participate directly in implementation rather than delegating every approved
step to Developer.

## Procedure

1. Keep the approved `DEV-NNN` plan as the shared implementation map.
2. For the next assigned dependency-ready step, explain the immediate goal,
   relevant code area, constraints, and expected outcome at the level needed for
   the user to act.
3. Do not modify project implementation unless the user asks Developer to write,
   patch, or take over that specific approved work.
4. When the user provides or applies code, inspect it against the approved step,
   applicable style files, architecture, and local project constraints. Point
   out concrete issues and propose or apply corrections only within Developer
   ownership. If `ProjectMode` is still `GREENFIELD` and inspection verifies
   that the active cycle has successfully created or materially modified a
   project implementation artifact, immediately persist the permanent
   `BROWNFIELD` transition before continuing. Do not require Developer to have
   authored the change.
5. Run or recommend the step's established self-check as appropriate. Mark the
   step `DONE` only when the expected outcome exists and the check is
   satisfactory.
6. Apply **Assignment Gates and Handoffs** in `../SKILL.md` before requesting
   more coding direction. A ready checkpoint or corrective return ends the
   Developer assignment without granting authority for further coding.
   Otherwise, if the user has not directed the next action, persist
   `Active Work.BlockedOn` as a concise request for Code With Me direction and
   stop.
7. Clear the blocker when the user provides direction, then continue within the
   same approved plan.

Implementation feedback in this mode is not the protocol's formal Reviewer role,
and self-checks are not Tester-owned formal verification.
