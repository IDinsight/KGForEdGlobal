# Architect Artifact Template

This file is the authoritative artifact shape and authoring contract for the
persisted technical design owned by Architect.

The design defines **how the completed scope will be satisfied**, which
technical decisions are fixed, and which choices remain implementation details
for Developer. It must be specific enough that Developer does not have to invent
unresolved architecture while coding.

Omit empty sections. Add detail only when it materially reduces implementation
ambiguity.

In `DOCUMENTATION`, use this same shape to establish existing technical
contracts and constraints for Documenter. Context and Decision identify the
current behavior and its repository evidence; Acceptance Coverage accounts for
every current AC with relevant facts or an explicit no-architectural-impact
disposition. Include components, interfaces, flow, and technical criteria only
where they affect the scoped documentation. Record evidence paths and material
uncertainty without claiming Tester verification. Omit Build Plan and proposed
implementation changes. Preserve useful established design in a reused canonical
document; do not convert its historical implementation plan into current-cycle
work. The documentation mode's contract takes precedence over the
implementation-oriented guidance below.

When Architect creates a new STANDARDS-owned technical-design artifact, create
it with `node .standards/bin/artifact.mjs init ARCHITECTURE`, which writes this
block with the active cycle ID:

```markdown
<!-- STANDARDS
Artifact: ARCHITECTURE
Cycle: <Active Work.Id>
-->
```

Do not add this block solely because Architect updates a pre-existing unmarked
project-owned canonical architecture or specification document; that document
remains project-owned.

---

# Technical Design

## Context

Summarize the relevant scope item, established project constraints, existing
architecture, and project context that materially shape the design.

## Decision

State the chosen technical design and the brief rationale for the material
choices. When multiple viable designs exist, choose one and record only
meaningful alternatives or tradeoffs.

## Acceptance Coverage

- `AC-001`: Identify the technical design decisions, contracts, components, or
  existing technical behavior relevant to this scope-level acceptance condition.
- `AC-002`: No architectural impact — identify the established nontechnical
  behavior or later workflow phase on which satisfaction depends.
- `AC-003`, `AC-004`: Shared disposition when the same coverage applies.

## Components

- Major component, module, service, subsystem, or boundary and its
  responsibility.
- Include only components whose responsibilities or relationships matter to
  implementation.

## Interfaces and Contracts

- API, ABI, protocol, schema, file format, event, command, persistence boundary,
  or other material contract.
- Define the source, contract, or governing rule for every material value or
  behavior that crosses a boundary, satisfies scope, or constrains
  implementation.

## Data and Control Flow

Describe how relevant data, state, events, or execution move through the design.
Include lifecycle or state transitions when they materially affect behavior.

## Technical Acceptance Criteria

- `AC-001`: Technical condition that must hold for the related scope-level
  acceptance condition to be satisfied.
- Define what must hold; do not prescribe test implementation or perform
  verification here.

## Build Plan

1. Coarse implementation step, dependency order, or migration sequence.
2. Keep the plan implementation-oriented without turning it into a line-by-line
   coding task list.

## Risks and Follow-up

- Known technical risk, compatibility concern, migration issue, non-blocking
  unresolved item, or later work.

## Alternatives

- Meaningful rejected alternative and why it was not chosen.

## Previous Cycles

Only in a design document reused across cycles, and always the last section: the
earlier cycles' acceptance coverage and technical acceptance criteria, moved
here unchanged, including their headings. Everything below this heading is
history, not current coverage or criteria.

---

## Authoring Rules

- Treat the completed scope and scope-level acceptance conditions as binding. Do
  not weaken, expand, or silently change their intent.
- Reference Scoper-owned acceptance identifiers exactly as written. Do not
  renumber them, create substitute requirement identifiers, or restate their
  meaning as if Architect owned it.
- Account for every current acceptance identifier in **Acceptance Coverage**.
  When technical design is relevant, identify the technical coverage. When no
  Architect-owned technical decision applies, record **No architectural impact**
  and identify the established nontechnical behavior or later workflow phase on
  which satisfaction depends when material. Do not invent architecture or claim
  the technical design satisfies a condition it does not own. Retired acceptance
  identifiers are not current coverage obligations; remove stale references to
  them when revising the design. In a reused design document, keep earlier
  cycles' acceptance references only under **Previous Cycles**.
- Record only material technical decisions. Leave local, easily reversible
  coding choices to Developer.
- Make replacements to established architecture, conventions, dependencies, or
  constraints explicit.
- Define relevant interfaces, contracts, lifecycle/state behavior, error
  behavior, compatibility requirements, security constraints, and
  performance/resource constraints when they materially affect implementation.
- Ensure every material cross-boundary value or behavior has a defined source,
  contract, or governing rule.
- Derive technical acceptance criteria only where a technical condition is
  needed to satisfy a scope-level acceptance condition, and prefix each
  criterion with the relevant acceptance identifier or identifiers. An
  acceptance identifier with no architectural impact does not require an
  invented technical acceptance criterion.
- Keep rationale brief. Preserve only decisions and tradeoffs that help
  implementation or future maintenance.
- Keep the build plan coarse and dependency-aware.
- Do not include production code, test implementation, verification results,
  audit findings, reviews, or user documentation.
- Keep the artifact concise. Add sections or detail only when they prevent
  Developer from having to make an unowned technical decision.
