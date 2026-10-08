# Tester Artifact Template

The verification report is Tester's concise, resumable assessment for one cycle.
Use the location and provenance defined by **Workflow Artifact Provenance** in
the protocol. Tests remain in the repository's established test locations. Both
modes use this format; do not create a separate preferences or status file.

Create the report with `node .standards/bin/artifact.mjs init VERIFICATION`,
which writes this provenance block and the header below:

```markdown
<!-- STANDARDS
Artifact: VERIFICATION
Cycle: <Active Work.Id>
-->
```

---

# Verification Report

`Cycle`: `<Active Work.Id>` `Mode`: `VERIFY | REVERIFY` `Status`:
`IN_PROGRESS | BLOCKED | COMPLETE` `User Style`: `NONE | <identifier>`
`Assessment Purpose`: `FULL` `Assessment Target`: `NONE`

## Assessed Inputs

- Scope, architecture, context, and development-plan paths with relevant
  revisions/content identities; current acceptance set.
- Active-change source and test boundaries, including committed and dirty-tree
  content, relevant dependencies/configuration, and execution environment.
- Reason for this assessment or reconciliation; session-freshness visibility
  limitation when applicable. Do not claim a machine-certified fresh session.
- Current assessment purpose and target, the relevant increment definition or
  corrective assignment, and the exact implemented outcome being assessed.

## Acceptance Evidence

| AC / technical criterion | Tests or checks          | Disposition and evidence |
| ------------------------ | ------------------------ | ------------------------ |
| AC-NNN / design section  | Scenario/check reference | Result or gap reference  |

Use test paths and scenario names, commands, or inspections. Record verified
with result evidence, blocked/uncovered with the gap, or pending a later role
with its owner and required evidence.

For partial assignments, use `AWAITING_IMPLEMENTATION` and distinguish partial
AC evidence under the protocol's **Acceptance Traceability**.

Account for all current IDs and relevant technical criteria without rewriting
their meaning. Separate criteria sharing an ID when their evidence differs.
Retired references may remain in a concise superseded-evidence note, never as
current coverage. Presence of a test is not a passing result.

## Increment Assessments

Include this section once an increment has been assigned; retain it across
cadence switches and later full verification.

| Increment | Outcome and acceptance scope | Assessed inputs / evidence | Disposition                      |
| --------- | ---------------------------- | -------------------------- | -------------------------------- |
| 1         | Defined outcome / AC-NNN     | Content / result pointers  | VERIFIED, BLOCKED, or SUPERSEDED |

Reference the Developer-owned increment definition and the evidence required by
**Checkpoint Handoffs**. Preserve useful assessment history when outcomes are
revisited or inputs change. Keep current acceptance conclusions in Acceptance
Evidence; this table records assignments and their results.

## Scenario Budget

| Source file | Reused coverage  | Active-change allocations | User additions |
| ----------- | ---------------- | ------------------------- | -------------- |
| Source      | Reused scenarios | Allocations               | Additions      |

Record any user additions with their targeted area/limit and user direction.
Apply the counting rules in `SKILL.md`; record cross-file assignments and
parameter/property counts when relevant. Preserve allocations across sessions,
modes, increments, and cadence switches, including invalidated/replaced or
removed scenarios. Do not list every unrelated existing test. When blocked by
the ceiling, name the unmet criterion, proposed additional scenarios, and
smallest requested increase.

## Execution Evidence

- Exact command and working directory (or precise inspection performed).
- Assessed revision/content and relevant environment/configuration; when run.
- Actual result, exit status if available, counts and log/artifact pointers when
  useful. Separate passes, failures, skips, and unrun/unavailable checks.
- Coverage/limitations: what the result establishes and what it does not.
- Still-valid prior Tester result reused and reason, or superseded result and
  why new evidence is required. Developer self-checks are not formal evidence.

Keep only enough detail to audit and resume; link useful logs rather than
copying terminal transcripts. Never record credentials or sensitive values.

## Open Findings and Dependencies

- Evidence/reproducer, affected AC or technical criterion, owning role, and
  required correction for each unresolved defect.
- Required unrun checks or uncovered obligations and the cause of each gap.
- Explicit later-role dependency, its AC, owner, and evidence still required.
- Blocking user question, if any, matching `Active Work.BlockedOn`.

## Resume or Handoff

Record the next concrete action on interruption, or the completion conclusion
and evidence locations for the receiving role. State which acceptance evidence
remains pending downstream. Supply enough context for the protocol's independent
Reviewer session and identify the requested review kind. Workflow state and
recovery routing remain canonical in `STATE.md`, not in this report.

Store suspended assignments, scoped correction evidence, and remaining work here
under the protocol's **Implementation and Verification Recovery Gates**.

---

## Authoring Rules

Keep Assessed Inputs, Acceptance Evidence, Scenario Budget, Execution Evidence,
and Resume or Handoff. Omit Open Findings and Dependencies only when empty. Use
explicit NONE or not-yet-run explanations rather than empty required tables.

Set `User Style` to the user's explicit selection under the protocol's **User
Styles**, or `NONE`.

Set `Assessment Purpose` independently of `Mode`:

- `FULL`: reconcile the entire current implementation contract; target `NONE`.
- `INCREMENT`: assess the assigned testable outcome and affected completed
  behavior; target `Increment N`, matching the increment assigned at handoff.
  After the return, retain that target as history until a new assessment starts,
  even if Developer changes `Current Increment` or cadence.
- `CORRECTION`: verify a specific owned correction or affected recovery rerun;
  target a concise description of that assignment. Record its frame association
  and interrupted assignment in Resume or Handoff when recovery is active.

`Assessment Purpose` and `Assessment Target` are required. Initialize new
reports with `FULL` and `NONE`, respectively. Missing, invalid, repeated, or
contradictory values are inconsistencies. An increment or correction requires
its explicit target and scoped evidence before its gate can pass. Use `VERIFY`
or `REVERIFY` according to the existing input-change rules; purpose and cadence
do not reset those obligations.

`IN_PROGRESS` means verification is underway; `BLOCKED` means required work
cannot currently complete. Neither declares full completion, although an
`IN_PROGRESS` report may record a passed increment or correction gate.
`COMPLETE` requires purpose `FULL` and the full shared Tester gate, possibly
with explicitly permitted later dependencies, under **Full Verification
Boundary**. Reopen a completed report when reconciliation is needed. Keep
current evidence coherent, preserving useful result and allocation history.
