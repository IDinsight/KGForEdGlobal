# Assess Mode

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "conversation-mode",
  "id": "ASSESS",
  "label": "Assess",
  "description": "Informally assess an area against a named checklist.",
  "selection": "user"
}
```

Use the shared Navigator procedure and boundaries. Assess a user-named area,
such as a service, app, pipeline, or the whole project, against a named lens,
and report the result in the conversation. ASSESS is an informal, read-only
assessment. It persists nothing, creates no workflow finding or record, is not a
Tester or Reviewer gate, and never satisfies an acceptance condition, readiness
claim, or sign-off.

Choose the lens from the request:

- Maturity: compare the area with a target level using
  [`../../scoper/references/maturity-levels.md`](../../scoper/references/maturity-levels.md).
  Report the assessed level for each affected area. An area reaches a level only
  when every applicable expectation at and below it has working evidence.
- Security: follow
  [`../references/security-assessment.md`](../references/security-assessment.md),
  including its OWASP Top 10 coverage and severity labels.
- Stack conventions: compare the area with the Developer style files for the
  affected technologies in `../../developer/styles/` and their references.
- A lens the user describes in their own words.

Ask for the area, lens, or target only when the request leaves them unclear and
the answer materially changes the assessment.

1. State the area, lens, target, and inspection boundary before concluding.
2. Inspect the evidence under the shared procedure. Do not credit an expectation
   because of directory names, dependency presence, comments, or documentation
   claims alone; require evidence that the behavior works.
3. Mark each applicable checklist item as met, not met, unverified, or not
   applicable, with exact paths, lines, or observed results. Keep inspected,
   executed, and predicted results distinct.
4. Report the assessed state against the target, gaps ordered by impact with
   evidence and the smallest path to close each, which gaps the target requires
   and which are optional, and the unverified items with the smallest next
   observation that would settle them.
5. To act on the result, the user starts workflow work outside Navigator, for
   example Scoper for "bring this API to Level 4" or Developer for a bounded
   expedited fix. Name the likely owner without starting the cycle, writing a
   report file, or issuing a Reviewer verdict.

Severity and gap labels in ASSESS are conversational guidance. They are not
Reviewer `P0`-`P2` findings and carry no workflow authority.
