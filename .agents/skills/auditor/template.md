# Project Context

## Project Baseline

- What the project is and the relevant current state.

## Stack and Tooling

- Languages, runtimes, frameworks, package/build tools, versions or constraints
  when material.

## Structure and Boundaries

- `<path>` — responsibility or boundary relevant to this cycle.

## Commands

- `<command>` — what it does and when to use it.

## Conventions and Constraints

- Established rule, invariant, compatibility requirement, or project-specific
  instruction.

## External Systems and Data

- Relevant service, API, persistence boundary, schema, protocol, or integration.

## Testing and Verification Baseline

- Existing test locations, frameworks, verification mechanisms, or known
  limitations.

## Relevant Existing Behavior

- Behavior downstream work must preserve or intentionally change through owned
  scope/design decisions.

## Active-Cycle Non-Baseline Work

- `Cycle: <Active Work.Id>; <path, commit, or area>` — implementation present in
  the repository for that active cycle that must not be treated as established
  project baseline. Include this section only when material, especially after an
  expedited-to-standard promotion. Every entry must identify the current cycle.

## Known Unknowns

- Non-blocking fact that could not be established, with why it matters.

## Evidence

- `<path or command>` — what it grounds.

---

Omit empty sections. Prefer a few high-value paths and commands over exhaustive
inventories. Do not restate planned technical decisions from
`Active Work.Architecture`. When necessary, note that the active design contains
planned changes not yet represented by the repository and defer to the
Architecture artifact for their contents. When `Active Work.PromotionReason` is
not `NONE`, use **Active-Cycle Non-Baseline Work** to preserve any material
distinction between the pre-cycle baseline and tentative implementation from the
former expedited path. The section is scoped to the current `Active Work.Id` and
is applicable only while that cycle is nonterminal. Once the cycle reaches
`SIGNED_OFF` or `CANCELLED`, its entries are stale cycle-scoped context rather
than permanent baseline exclusions. Never carry an entry from a prior cycle
forward unchanged. On a later audit, reconcile prior-cycle entries against
current evidence and remove, reclassify, or block on them as required by the
Auditor skill. Do not use the section as an audit diary or as a place for
scope/design decisions.
