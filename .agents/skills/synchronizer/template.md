# Synchronizer Artifact Template

Use this shared record for initial synchronization, interruptions, and corrected
work. The fixed path and preservation rules are defined by **Workflow Artifact
Provenance** in the protocol. No mode or synchronization-path state field is
needed. Create the record with
`node .standards/bin/artifact.mjs init SYNCHRONIZATION`, which writes this
provenance block and the header below:

```markdown
<!-- STANDARDS
Artifact: SYNCHRONIZATION
Cycle: <Active Work.Id>
-->
```

---

# Synchronization Record

`Cycle`: `<Active Work.Id>` `Status`: `IN_PROGRESS | BLOCKED | COMPLETE`
`User Style`: `NONE | <identifier>`

## Assessed Inputs

- Contract, context, development, verification, both review reports, and
  documentation paths with content identities and cycle/kind checks.
- In `DOCUMENTATION`, use current Auditor context, scope, technical contracts,
  documentation record and final review. Include the editing boundary and saved
  documents/checks; distinguish supporting existing source/tests/prior
  assessments from omitted owners' current-cycle evidence.
- Baseline/comparison range and its basis; committed, staged, unstaged,
  untracked, moved/deleted, and affected unchanged content relevant to the work.
- Relevant dependencies/configuration and evidence execution assumptions.
- Workflow context used: completion policy, handoff, recovery, obligations,
  baseline reconciliation, and blockers. State remains authoritative for
  routing; do not copy its stack.
- Assessment boundary and exclusions with reasons. Distinguish deliverable
  identities from this record's own writes and legal coordination updates. For
  corrective work, identify the owned correction and distinguish future
  dependencies from intentionally omitted normal-phase guarantees. Missing
  inputs needed for the correction or active contract remain gaps.

## Completion and Evidence References

Reference existing artifacts/sections/results accounting for every current AC
and relevant technical criterion. Preserve their identifiers and meaning; group
references only when they share the same applicability reasoning. Do not copy
acceptance text, reports, or an authoritative pass/fail ledger here.

- Evidence/completion reference, the ACs and technical criteria it supports, and
  why it remains applicable to the assessed current content.
- Final review's applicability to this assembled deliverable and its supporting
  documentation evidence, plus implementation review and verification in
  `STANDARD`. Omitted implementation records are not documentation-cycle gaps.
- Earlier pending dependencies and the owner's evidence resolving them under the
  same IDs, or a discrepancy reference when still unresolved.
- Reconciliation inspections/checks actually performed, their results and
  limits. For executed commands include command, working directory, assessed
  content, relevant environment, and actual exit/result. Never record secrets.
- Retained evidence with justification; superseded evidence and the change
  invalidating it. Retired acceptance IDs are historical only.

## Discrepancies and Dispositions

### D-001 — concrete inconsistency

`Status`: `OPEN | RESOLVED | WITHDRAWN` `Owner`: `<role or installer/protocol>`
`FailureType`: `<canonical type, or NONE for non-workflow ownership>`

- **Reference:** affected path/section, content identity, related AC/criterion,
  and Reviewer finding reference by path and ID, for example
  `.standards/docs/reviews/<Active Work.Id>/final-deliverable.md#F-001`, when
  applicable.
- **Evidence and impact:** what disagrees or is missing, and why it matters for
  applicability or completion. Separate facts from uncertainty.
- **Required correction:** smallest outcome required from the owner, including
  review kind for Reviewer; record all open items before routing one.
- **Disposition:** owner evidence checked on return, or reason a Synchronizer
  claim was withdrawn. Resolution here never changes another owner's markers.

Use stable record-local IDs numbered with
`node .standards/bin/id.mjs next D <record>`; these are discrepancy references,
not acceptance identifiers. Write `NONE` when assessment found no discrepancies.
Do not leave the example finding in a no-change result or duplicate entries on
repeated runs.

## Limitations and Remaining Work

- Material gaps, unrun/unavailable/inconclusive checks, unresolved dependencies,
  and required owner or user action, or `NONE` with the assessed limits.
- Non-blocking limits and why they do not prevent this conclusion.
- Blocking question, if any, matching `Active Work.BlockedOn`.

## Resume and Synchronization Conclusion

Lead with whether work can proceed to user sign-off and the reason. Record the
next concrete action on interruption or corrective routing and identify input
changes to reconcile next time. State whether the full Synchronizer gate passed
and whether recovery requires a return before sign-off readiness. For a
**Synchronizer Corrective Return**, record the verified owned correction and
remaining dependencies, owners, and required evidence; explicitly state that
full synchronization remains incomplete. Separately identify any intentionally
omitted guarantees allowed by the shared corrective-return conditions; do not
invent future assignments for them. Under `IMPLEMENTATION_REVIEWED`, describe
the saved recovery return and whether implementation Reviewer must reassess
closure. Neither the correction nor this record replaces that assessment.
Reference current state for routing. Readiness is not user acceptance.

In `DOCUMENTATION`, record the full documentation-contract reconciliation and
resulting readiness or recovery route with policy `NONE`. An incomplete owned
corrective return goes only to an included role; record its unfinished
prerequisites and when to revisit them. All six full gates and current evidence
are required before readiness, including after a supported no-change result.

For **Synchronizer Corrective Reruns** under the protocol, also identify the
active frame and its reason, the prior verified correction and content
identities, the upstream changes, revalidation checks/results, and why each
remaining full-gate gap is permitted. Record the saved rerun/resume boundary and
required implementation Reviewer reassessment. Keep the record incomplete;
passing this scoped assignment neither transfers frame ownership nor declares
full synchronization or sign-off readiness.

---

## Authoring Rules

Keep all sections concise and substantive; use `NONE` or an explicit explanation
that work is not yet assessed instead of empty required sections. Persist enough
to resume without chat history. `IN_PROGRESS` means reconciliation remains;
`BLOCKED` means required reconciliation cannot complete; `COMPLETE` means the
full **Synchronization Gate** passed, subject to current input applicability.
Reopen unsupported conclusions and preserve useful superseded reasoning. Set
`User Style` to the user's explicit selection under the protocol's **User
Styles**, or `NONE`.

Reuse unchanged, sufficiently assessed content without cosmetic rewrites or
repeated history entries. Correct only this record; route defects in referenced
artifacts to their owners. This record neither replaces reports nor becomes a
new source of acceptance meaning, formal evidence, or Reviewer findings.
