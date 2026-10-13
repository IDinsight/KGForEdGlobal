# Reviewer Artifact Template

Both review kinds use this concise, resumable report. Use the fixed path and
provenance defined by **Workflow Artifact Provenance** in the protocol; do not
add a review-path field or a separate preferences/status file.

Create the report with
`node .standards/bin/artifact.mjs init REVIEW --kind IMPLEMENTATION` or
`--kind FINAL_DELIVERABLE`, which writes this block and the header below with
the active ID and the kind matching the review state:

```markdown
<!-- STANDARDS
Artifact: REVIEW
Cycle: <Active Work.Id>
ReviewKind: IMPLEMENTATION | FINAL_DELIVERABLE
-->
```

---

# Review Report

`Cycle`: `<Active Work.Id>` `ReviewKind`: `IMPLEMENTATION | FINAL_DELIVERABLE`
`Status`: `IN_PROGRESS | BLOCKED | COMPLETE` `User Style`: `NONE | <identifier>`

## Assessed Inputs and Scope

- Contract paths and content identities; cycle mode, selected completion policy,
  and current acceptance set in STANDARD or DOCUMENTATION, or bounded request in
  EXPEDITED.
- Repository baseline/comparison range and its basis, committed and dirty-tree
  content (including untracked/deleted paths), affected unchanged boundaries.
- Development, verification, context, prior reviews, documentation, relevant
  dependencies/configuration, and environment identities used in this
  assessment.
- In DOCUMENTATION, include the documentation boundary, current Auditor context,
  technical contracts, saved documents, and Documenter checks. Identify existing
  source/tests/prior assessments used as support without requiring omitted
  owners' current-cycle artifacts or claiming formal implementation
  verification.
- Included/excluded areas and why; session/model visibility limitations under
  the protocol, without invented identity, capability, or freshness
  attestations.

## Contract and Evidence Assessment

| Obligation / criterion                      | Assessed evidence        | Current disposition                              |
| ------------------------------------------- | ------------------------ | ------------------------------------------------ |
| AC-NNN / design section, or bounded request | Artifact/check reference | Supported, finding, gap, or permitted dependency |

Account for every current AC and relevant technical criterion in STANDARD or
DOCUMENTATION. Separate criteria sharing an ID when evidence differs. In
EXPEDITED, assess the request without inventing IDs or requiring skipped
artifacts. Retired IDs are historical only. Distinguish Developer self-checks,
Tester formal evidence, and Reviewer diagnostics; explain what each result
actually supports. In DOCUMENTATION, independently assess documentation evidence
and observed existing behavior; no omitted-owner evidence or later
implementation dependency is invented.

## Checks and Results

- Inspection performed or exact command and working directory.
- Assessed content, relevant environment/configuration, and when checked.
- Actual result, exit status when available, and concise useful log pointers.
- What the check establishes and its limits; distinguish unrun, unavailable,
  failed, skipped, interrupted, or inconclusive checks from satisfactory
  results.
- Reused prior evidence with applicability justification, or superseded result
  with the change that invalidates it. Never record secrets or full transcripts.

## Findings

### F-001 — concrete defect

`Severity`: `P0 | P1 | P2` `Status`: `OPEN | RESOLVED | WITHDRAWN` `Owner`:
`<role>` `FailureType`: `<canonical type>`

- **Reference:** exact file/line or artifact section, assessed content identity,
  and relevant AC/technical criterion when applicable.
- **Failure case:** concrete trigger or input and incorrect outcome.
- **Impact:** what breaks or could be lost, exposed, or made unreliable.
- **Evidence:** inspected behavior, reproducer, or actual check result
  supporting the defect; distinguish fact from uncertainty.
- **Smallest correction:** required outcome within the owner's authority.
- **Reassessment:** for resolution/withdrawal, Reviewer-checked evidence and
  reason; preserve useful history and do not accept author status alone.

Use stable report-local finding IDs numbered with
`node .standards/bin/id.mjs next F <report>`. Refer to a finding in another
report by path and ID, for example
`.standards/docs/reviews/<Active Work.Id>/implementation.md#F-003`. Apply
severity and status definitions from Reviewer SKILL.md. Write **No material
findings** when none are established; do not fill a quota.

## Questions, Limitations, and Later Dependencies

Separate unknowns from established defects. For each material gap, record the
missing evidence, consequence for this gate, owner or required user action, and
next check. Explain why a non-material limitation does not prevent completion. A
blocking user question must match `Active Work.BlockedOn`.

Only permitted STANDARD implementation-stage later dependencies may remain at
completion. Record each under the same current AC with its owner and required
evidence. At final review, record the evidence resolving earlier dependencies;
unresolved dependencies cannot support a passing final gate. Use NONE when
empty.

## Implementation-Reviewed Closure

Include this section only in the implementation report when assessing shorter
standard completion. It is separate from the ordinary review `Status`; a
`COMPLETE` review may still have `INELIGIBLE` closure. Omit it for reports that
have never assessed this policy, including expedited and final review. Do not
delete a prior assessment on withdrawal; retain its assessed policy and useful
history. Full-deliverable completion does not rely on this section.

`Policy`: `IMPLEMENTATION_REVIEWED` `Eligibility`: `NOT_ASSESSED` `User Choice`:
`NONE` `User Reason`: `NONE` `Assessed Inputs`: `NONE` `Evidence`: `NONE`
`Unmet Requirements`: `NONE` `Omitted Phases`:
`DOCUMENTING, REVIEWING_FINAL, SYNCHRONIZING` `Omitted Guarantees`:
`Normal documentation, final review, and independent reconciliation omitted.`

Keep each field exactly once with a concrete value. `Policy` records what this
assessment evaluates, even after a later coordination change. `User Choice`
records the explicit instruction and its source; `User Reason` is the user's
stated reason or `NONE` when none was given. Preserve both beyond replacement of
the handoff and active-cycle state. `Assessed Inputs` identifies current content
or points to the exact identities in **Assessed Inputs and Scope**. `Evidence`
references the current AC/technical-criterion assessment and owner-produced
evidence, including resolution of all later dependencies and existing artifacts.

Set `Eligibility` to `NOT_ASSESSED`, `INELIGIBLE`, or `ELIGIBLE` using
`SKILL.md`. An eligible conclusion requires non-`NONE` choice, input identities,
evidence, and omitted guarantees, with `Unmet Requirements: NONE`. An ineligible
conclusion names the unmet requirements, owners, and needed evidence or user
action. Explain the conclusion in prose; the fields do not replace assessment.
Corrective work does not imply that any omitted full phase passed.

Keep exactly one current section with this heading. Before replacing a previous
assessment, preserve its conclusion, inputs, evidence, and reason for
supersession under a separate `## Closure Assessment History` heading. On a
return to this policy or changed inputs, reset current eligibility to
`NOT_ASSESSED` and reconcile it before claiming eligibility again. Do not reuse
a prior `ELIGIBLE` label alone.

## Progress and Conclusion

- Completed inspection and remaining areas/checks; next concrete action on
  pause.
- Changed inputs on resumption, invalidated conclusions, rechecked fixes and
  boundaries, and justification for retaining still-valid evidence.
- Whether this gate passes and why; unresolved findings/gaps must remain
  visible.
- Concise plain-language summary: can work move forward, what is wrong and its
  consequence, who fixes it, what was checked, and what remains unvalidated.
- Report/evidence references needed for handoff. State and recovery routing stay
  canonical in `STATE.md`; this report does not create a parallel workflow.

---

## Authoring Rules

Keep each applicable section; use explicit NONE, no material findings, or
not-yet-assessed explanations rather than empty tables. Set `User Style` to the
user's explicit selection under the protocol's **User Styles**, or `NONE`.
`IN_PROGRESS` means assessment is underway; `BLOCKED` means required correction
or evidence prevents completion; neither is passing. `COMPLETE` requires the
shared Reviewer gate and the applicable review gate in `SKILL.md`, not merely an
empty finding list. Reopen it when changed inputs invalidate that conclusion.
Preserve the other kind's report and other cycles. Keep useful history, but make
current conclusions and next actions unambiguous. The closure section's
eligibility is independent of ordinary review status; only current `ELIGIBLE`
closure together with a passing ordinary review supports the shorter standard
handoff to sign-off readiness.
