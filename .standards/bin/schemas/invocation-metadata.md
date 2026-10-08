# Invocation metadata contract

Version 1 defines the source metadata for invocation discovery. The adjacent
[JSON Schema](invocation-metadata.schema.json) is the machine-readable shape;
this document defines its meaning and the checks that require multiple files.

Role and mode source metadata and the read-only discovery helper follow this
contract.
[Next-Role Invocation Options](../../PROTOCOL.md#next-role-invocation-options)
governs handoff presentation. Role instructions and protocol rules continue to
govern execution.

## Storage and ownership

Each role has one metadata block in `skills/<role>/SKILL.md`. Each direct child
`skills/<role>/modes/*.md` has one mode block. Installed copies use the active
client's skill directory. Metadata uses a tagged JSON code block in the Markdown
body; it does not change client YAML frontmatter:

````markdown
<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "collaboration",
  "id": "STEPWISE",
  "label": "Stepwise",
  "description": "Implement one step, then wait.",
  "selection": "user"
}
```
````

The marker is followed by one JSON fence, with optional blank lines between
them. Blocks contain strict JSON. A role's `role` must match its directory. A
mode inherits its role and instruction path from its containing file; authors do
not repeat the path or register mode filenames in a separate catalog.

Group definitions live in the role block. A mode declares its `group`,
identifier, label, description, and selection category in its own file. Non-mode
options, such as documentation targets, live in an `inline` group. User-style
identifiers, current values, and discovered availability never belong in source
metadata. There is no maintained `invocation.json` file.

## Role and group fields

| Field              | Meaning                                                                                                                                                 |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `schemaVersion`    | Exactly `1`; unsupported versions are an error.                                                                                                         |
| `kind`             | `role` in `SKILL.md`, or `mode` in a mode file.                                                                                                         |
| `role`             | Lowercase skill identifier, present only in a role block.                                                                                               |
| `groups`           | Single-choice option groups; may be empty, as for Synchronizer.                                                                                         |
| `id`               | Lowercase group identifier, unique within the role.                                                                                                     |
| `label`            | Short human-readable name.                                                                                                                              |
| `source`           | `modes` discovers matching mode blocks; `inline` uses this group's `options`.                                                                           |
| `selectionRules`   | Markdown path and optional heading within the same skill, containing the governing selection rules.                                                     |
| `defaultForNew`    | Fallback option ID for genuinely new work without an applicable selection.                                                                              |
| `inferFromRequest` | When `true`, the role may infer a user-selectable option from intent under `selectionRules`, as Navigator does. The helper does not interpret requests. |
| `savedValue`       | Where an existing choice is recorded, or `conversation` for agent-held context.                                                                         |
| `savedArgument`    | Where the concrete target is recorded when options take an argument.                                                                                    |
| `lockedWhen`       | Optional factual condition preventing changes to an existing selection.                                                                                 |

`defaultForNew` never overrides a current explicit choice, saved record, or
explicit intent retained in the active request or scope. Free-text requests and
conversational choices require the receiving role's interpretation. An absent or
unreadable record does not by itself establish new work.

## Mode and inline-option fields

Both mode blocks and inline options require `id`, `label`, `description`, and
`selection`. Option IDs use uppercase letters, digits, and underscores. Mode
blocks additionally require `schemaVersion`, `kind`, and their group ID.

| `selection`  | Meaning                                                                             |
| ------------ | ----------------------------------------------------------------------------------- |
| `user`       | A user choice, subject to the group rules and any condition or lock.                |
| `state`      | A value determined by persisted facts; `when` is required.                          |
| `assessment` | A value the receiving role chooses after evaluating evidence under the group rules. |

The category is per option. This accommodates Architect's assessment-selected
standard modes and state-selected documentation mode within one group.

`when` contains an optional factual availability condition for a user or
assessment option. For a state option it is the selection condition. A true
condition on an assessment option makes it a candidate, not the selected mode. A
single remaining assessment option still requires the receiving role's
assessment. A state value is established only when exactly one state condition
is true and all other state conditions in the group are false. Multiple true
conditions conflict; any unknown state condition leaves selection unresolved. An
established state value is informational, even if the group also contains
user-selectable options.

`requiresAssessment: true` may be used on a user option whose eligibility
requires judgment, such as an Auditor subtree target. The helper must identify
the outstanding assessment instead of treating the option as unconditionally
available. It is unnecessary for `assessment` options and invalid for `state`
options. Relevant reasoning stays in `selectionRules`.

User options may declare `argument` as `file`, `directory`, `capability`, or
`area`. An `area` is a repository-relative path or area label, as accepted by
Auditor; it is not restricted to a directory. The argument is supplied with the
invocation or recovered from existing intent and records; it is not an option
ID. Target choices do not revise scope or waive the role's required work outside
a selected editing boundary.

A group may declare `savedArgument` without `savedValue` when it exposes a
single argument-taking option. This preserves a target without claiming that it
selects a mode. For example, Auditor retains `Active Work.AuditTarget`, but
still assesses whether a usable baseline permits a subtree audit. A sentinel
such as `NONE` means no saved target under that field's protocol rules; it is
never a literal target or a default option.

## Saved values and factual conditions

A saved-value binding has one of these forms:

- `{"kind":"workflow","field":"WorkflowState"}` reads a named workflow fact.
- `{"kind":"record","artifact":"DEVELOPMENT","field":"Mode"}` reads a
  current-cycle record header.
- `{"kind":"conversation"}` tells the agent to use its conversation context; the
  helper cannot obtain the current value from disk.

Workflow facts supported by this schema are `ProjectMode`, `CycleMode`,
`WorkflowState`, `Active Work.CompletionPolicy`,
`Active Work.PendingVerificationCadence`, `Active Work.AuditTarget`, and
`Handoff.Kind`. Their files and semantics remain defined by the protocol. Adding
another fact requires extending the schema and discovery support.

Record bindings support `DEVELOPMENT`, `VERIFICATION`, `REVIEW`,
`DOCUMENTATION`, and `SYNCHRONIZATION`. Resolve records through existing
protocol locations/references and validate current-cycle provenance before
reading a header. Never select a record by modification time or inherit a
previous cycle's choice. `REVIEW` additionally requires exactly one of
`reviewKind` (a literal review kind) or `reviewKindFromGroup` (a group whose
state-selected value supplies the kind). Other record types reject these keys.

State-derived values are resolved before bindings that use them. A group's saved
record is resume information, not authority to override state or a new
assessment. Reviewer can select its kind from state and then read that kind's
record; resolving the kind must not depend on reading the same record first.

Conditions compare a `fact` binding with an `equals` string, and may combine
conditions with nonempty `all` or `any` arrays. Conversation bindings are not
facts. There are no executable expressions, arbitrary file reads, or natural-
language predicates in the condition language.

Compare canonical parsed field text, including `"true"`, `"false"`, `"NONE"`,
and `"UNSET"`, exactly. Missing, contradictory, malformed, or unreadable inputs
produce **unknown**, not false. `all` is false when any child is false and true
only when every child is true; `any` is true when any child is true and false
only when every child is false. All other combinations are unknown.

A lock that is true prevents changing or clearing the selection, including
`NONE`. An unknown lock is reported as unknown; it does not establish that a
selection can be changed. A missing record's lock can be treated as inapplicable
only after the receiving role establishes that this is genuinely new work.
Discovery explains restrictions; it does not approve work or alter records.

## User styles

`userStyles.source` is exactly `role-directory`. The helper discovers direct
child Markdown files in `.standards/user-styles/<role>/` at each call using the
protocol's identifier and symlink rules. The schema deliberately has no array of
style names and accepts no arbitrary discovery path.

`userStyles.savedValue` is a record binding or a conversation binding.
`lockedWhen` is optional and uses the same factual-condition language. The
actual selected identifier, available identifiers, and lock result belong in
discovery output. An existing selection is not changed merely because a file is
added. Discovery does not load or apply unselected style contents.

An absent directory means no available user-style files. An unreadable directory
means availability is unknown. Missing selected files and invalid selections
retain the protocol's existing handling; discovery does not substitute another
file. Built-in technology styles are outside this inventory.

## Assembly checks

The JSON Schema checks each block's shape and supported vocabulary. The helper
also validates the assembled role before advertising its options:

1. Reject missing or repeated metadata wrappers, unsupported versions, malformed
   JSON, unknown fields, duplicate group IDs, and duplicate option IDs within a
   group. Do not infer metadata for an unannotated mode file.
2. Match each mode to exactly one declared `modes` group. Forbid inline lists in
   a `modes` group and unregistered mode groups. A declared group must resolve
   to at least one option; a role may legitimately declare no groups.
3. Resolve instruction files from their actual source locations and validate
   `selectionRules` paths/headings inside the same installed skill. Reject paths
   or symlinks escaping the skill. Select the active client's installation
   without silently combining mismatched copies from two clients.
4. Require each default to identify a user-selectable option in its group.
   `inferFromRequest` is meaningful only for a user-choice group. Locks apply to
   user choices. Require a `savedArgument` binding's group to contain at least
   one argument-taking option. Without `savedValue`, require exactly one such
   option so the argument has an unambiguous meaning.
5. Resolve review-kind group references only from state-selected review kinds.
   Reject cycles among state-selection conditions, including conditions that
   depend on the group they would select. Saved bindings may refer to their own
   group after its state value is resolved. Report unresolved or conflicting
   state selection without choosing a winner. Unknown conditions must not be
   treated as an available alternative or a reason to apply a default. A
   selected value absent from the current inventory is an inconsistency, not
   permission to substitute another value.
6. Return groups in declared order and mode/style inventories in identifier
   order. Keep any selected item identifiable independently of display order.
   Report incomplete discovery instead of claiming an exhaustive inventory.

The helper implements these assembly checks and validates record provenance and
relevant field values. Source annotations and JSON Schema alone cannot establish
these properties. Workflow eligibility and evidence-based assessments remain the
receiving role's responsibility.

## Discovery helper

Agents call the read-only helper internally. Installation includes it with the
runtime; users do not need a generation, registration, or post-install command.
For example, an agent can discover options with:

```sh
node .standards/bin/invocation.mjs developer --client codex --json
node .standards/bin/invocation.mjs navigator --client claude
```

Omit `--json` for a human-readable summary. `--client` selects exactly one
installed skill directory: Codex uses `.agents/skills/`, and Claude uses
`.claude/skills/`. If exactly one copy of the requested role exists, the helper
can select it automatically. If both exist, the caller must specify the active
client, even when the copies currently match. It never merges their inventories
or falls back to another client after an invalid or unreadable selection.

The installed command finds the project relative to its own location, so it
works from another working directory. `--project <path>` explicitly selects a
different project. `--help` prints usage. The JavaScript entry point
`discoverInvocation(projectRoot, role, { client })` in
`runtime/lib/invocation.mjs` returns the same object as `--json`.
`readInvocationCatalog` returns only validated source metadata and throws if it
cannot assemble the catalog.

Discovery scans files on each call. It reads metadata and the facts needed for
that role, without loading user-style contents, returning full mode procedures,
writing a cache, starting a cycle, applying defaults, or changing a selection.
Navigator's conversation bindings work without protocol or workflow files. A
missing record or unavailable state limits the relevant facts, not unrelated
catalog discovery. This helper does not establish workflow entry eligibility,
approval, evidence sufficiency, or gate completion; roles and the existing
protocol/checker remain authoritative.

### Output contract

The JSON result has `schemaVersion: 1`, `role`, `client`, `skillFile` when
found, `catalogStatus`, `complete`, `groups`, `userStyles`, and `diagnostics`.
Source paths are project-relative.

- `catalogStatus` is `complete` only after every role/mode block and assembly
  check succeeds. Otherwise it is `unavailable`; the helper does not advertise a
  partial role inventory.
- `complete` means no source, inventory, or factual inconsistency was diagnosed.
  It does not mean that a role assessment or conversational choice has been
  made. Inspect individual values and diagnostics even when the catalog itself
  is complete.
- Groups preserve source declaration order. Mode options and style identifiers
  sort by identifier using a locale-independent comparison; inline options
  preserve declaration order. Defaults remain `defaultForNew` annotations and
  are never automatically selected.
- Each option keeps its source meaning, adds its `instructionFile` for a mode,
  and reports `condition` as `true`, `false`, or `unknown`. `assessmentRequired`
  identifies either an assessed mode or a user option that still needs judgment.
- `selectability` is `user`, `assessment-required`, `informational`,
  `unavailable`, `locked`, `state-controlled`, or `unknown`. These describe
  metadata restrictions, not permission to enter a role. Assessment and state
  options are informational; the `selected` value and condition distinguish an
  established state choice from an assessed candidate.
- Each group reports `selected`, `savedValue`, optional `savedArgument`, and
  `locked`. Resolutions contain `status` and `value`, plus a source or reason
  when relevant. Status is `known`, `unknown`, `invalid`, `conversation`,
  `assessment`, `none`, or `conflict`. Saved values remain visible even when
  current state or assessment takes precedence.
- `userStyles` reports its `directory`, inventory `status` (`complete` or
  `unknown`), `options` as identifiers, file paths, and invocation selectors,
  `savedValue`, `selected`, and `locked`. Each option's `id` is the filename
  stem; use its `selector` in invocation examples. With a complete inventory,
  `selector` is the stem or full filename that resolves uniquely under **User
  Styles**, preferring the stem. For a record binding, it must also be nonblank
  and pass the checker's header placeholder rules: it cannot contain `|` or be
  entirely enclosed in `<...>`. Thus `<formal>.md` uses its full filename;
  neither name for `team | compact.md` is usable in a record. Conversation
  bindings do not apply these record-field restrictions. `selector` is null when
  no usable unique name exists or the inventory is incomplete; such entries are
  inventory information, not selectable examples. `selectorReason` is null for a
  usable selector, or explains a null selector: `ambiguous name` when neither
  name is unique, `no record-compatible name` when the unique names fail
  record-field rules, or `selection unverified` for an incomplete inventory.
  Ambiguity and record-syntax restrictions do not make the inventory unknown.
  Preserve a working saved selector; do not shorten it into an ambiguous or
  rejected name. Discovery never supplies file contents. A saved placeholder is
  unknown, and a saved style that does not resolve uniquely is invalid; neither
  is rewritten.
- Locks use the strings `true`, `false`, and `unknown`. `false` means the
  declared lock does not apply, or no lock is declared. A missing or malformed
  lock field, or a false Developer style lock on an approved plan, is unknown. A
  true lock remains true even when a plan returns to PROPOSED.
- Diagnostics have `code`, `source`, and `message`, sorted deterministically.
  Errors never cause a fallback mode, style, default, or client to be applied.

Exit status 0 means the catalog was assembled, including cases with unresolved
context or style diagnostics. Exit status 1 means invalid command arguments or
an unavailable catalog. Consumers must inspect the JSON dispositions and
`complete` flag, rather than treating exit status 0 as workflow readiness.

The installed helper has no npm dependencies. Its validator reads the adjacent
schema and supports only the Draft-07 keywords used there; unsupported schema
keywords fail explicitly. Skill/style directory anchors must be normal
directories, and resolved files must remain inside their allowed folder.
Discovery limits individual Markdown reads to 2 MiB and reports larger inputs as
unavailable instead of silently truncating them.

## Examples

These examples illustrate the source blocks in the role and mode files. The
complete inventories come from those files; this document is not a second
catalog.

### Developer role and one mode

`skills/developer/SKILL.md` declares the group and the style lock:

```json
{
  "schemaVersion": 1,
  "kind": "role",
  "role": "developer",
  "groups": [
    {
      "id": "collaboration",
      "label": "Collaboration",
      "source": "modes",
      "selectionRules": "SKILL.md#mode-selection",
      "defaultForNew": "AUTONOMOUS",
      "savedValue": {
        "kind": "record",
        "artifact": "DEVELOPMENT",
        "field": "Mode"
      }
    }
  ],
  "userStyles": {
    "source": "role-directory",
    "savedValue": {
      "kind": "record",
      "artifact": "DEVELOPMENT",
      "field": "User Style"
    },
    "lockedWhen": {
      "fact": {
        "kind": "record",
        "artifact": "DEVELOPMENT",
        "field": "User Style Locked"
      },
      "equals": "true"
    }
  }
}
```

`skills/developer/modes/stepwise.md` supplies one discovered option. Autonomous
and Code With Me supply their own blocks; the role does not enumerate filenames:

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "collaboration",
  "id": "STEPWISE",
  "label": "Stepwise",
  "description": "Implement one approved step, then wait for the user.",
  "selection": "user"
}
```

### Reviewer state selection

The role declares a review-kind group and reads the corresponding report's
style. This example's group value comes from state rather than a saved field:

```json
{
  "schemaVersion": 1,
  "kind": "role",
  "role": "reviewer",
  "groups": [
    {
      "id": "review-kind",
      "label": "Review kind",
      "source": "modes",
      "selectionRules": "SKILL.md#modes"
    }
  ],
  "userStyles": {
    "source": "role-directory",
    "savedValue": {
      "kind": "record",
      "artifact": "REVIEW",
      "field": "User Style",
      "reviewKindFromGroup": "review-kind"
    }
  }
}
```

`skills/reviewer/modes/implementation.md` supplies its condition:

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "review-kind",
  "id": "IMPLEMENTATION",
  "label": "Implementation review",
  "description": "Assess implementation and the applicable evidence.",
  "selection": "state",
  "when": {
    "fact": {
      "kind": "workflow",
      "field": "WorkflowState"
    },
    "equals": "REVIEWING_IMPLEMENTATION"
  }
}
```

The final-deliverable mode uses `FINAL_DELIVERABLE` and a condition matching
`REVIEWING_FINAL`. Neither option is offered as a freely selectable review kind.

### Architect's mixed selection

Architect declares a `design-mode` group with `source: "modes"` and
`selectionRules: "SKILL.md#mode-selection"`. Its feature file contributes:

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "design-mode",
  "id": "FEATURE",
  "label": "Feature",
  "description": "Design a bounded capability when it is the primary concern.",
  "selection": "assessment",
  "when": {
    "fact": {
      "kind": "workflow",
      "field": "CycleMode"
    },
    "equals": "STANDARD"
  }
}
```

The documentation mode has a mechanical selection rule:

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "design-mode",
  "id": "DOCUMENTATION",
  "label": "Documentation",
  "description": "Establish existing technical contracts for documentation.",
  "selection": "state",
  "when": {
    "fact": {
      "kind": "workflow",
      "field": "CycleMode"
    },
    "equals": "DOCUMENTATION"
  }
}
```

Foundation, Evolution, and Cross-cutting use `selection: "assessment"` with the
same STANDARD condition. The existing design-risk rules choose among them.

### Documenter targets

The role's collaboration group discovers its Autonomous and Guided files. The
separate target group declares its inline choices and saved target detail:

```json
{
  "schemaVersion": 1,
  "kind": "role",
  "role": "documenter",
  "groups": [
    {
      "id": "collaboration",
      "label": "Collaboration",
      "source": "modes",
      "selectionRules": "SKILL.md#collaboration-and-target",
      "defaultForNew": "AUTONOMOUS",
      "savedValue": {
        "kind": "record",
        "artifact": "DOCUMENTATION",
        "field": "Collaboration"
      }
    },
    {
      "id": "target",
      "label": "Target",
      "source": "inline",
      "selectionRules": "SKILL.md#collaboration-and-target",
      "defaultForNew": "ACTIVE_CHANGE",
      "savedValue": {
        "kind": "record",
        "artifact": "DOCUMENTATION",
        "field": "Target"
      },
      "savedArgument": {
        "kind": "record",
        "artifact": "DOCUMENTATION",
        "field": "Target Detail"
      },
      "options": [
        {
          "id": "FILE",
          "label": "File",
          "description": "Focus on a named file within the editing boundary.",
          "selection": "user",
          "argument": "file"
        },
        {
          "id": "FOLDER",
          "label": "Folder",
          "description": "Focus on documentation within a directory.",
          "selection": "user",
          "argument": "directory"
        },
        {
          "id": "VERTICAL_SLICE",
          "label": "Capability",
          "description": "Follow one capability across its documentation.",
          "selection": "user",
          "argument": "capability"
        },
        {
          "id": "ACTIVE_CHANGE",
          "label": "Active change",
          "description": "Cover documentation affected by the active cycle.",
          "selection": "user"
        }
      ]
    }
  ],
  "userStyles": {
    "source": "role-directory",
    "savedValue": {
      "kind": "record",
      "artifact": "DOCUMENTATION",
      "field": "User Style"
    }
  }
}
```

### Navigator and roles without modes

Navigator declares request inference and keeps its selection in the
conversation:

```json
{
  "schemaVersion": 1,
  "kind": "role",
  "role": "navigator",
  "groups": [
    {
      "id": "conversation-mode",
      "label": "Conversation mode",
      "source": "modes",
      "selectionRules": "SKILL.md#modes",
      "defaultForNew": "EXPLAIN",
      "inferFromRequest": true,
      "savedValue": {
        "kind": "conversation"
      }
    }
  ],
  "userStyles": {
    "source": "role-directory",
    "savedValue": {
      "kind": "conversation"
    }
  }
}
```

Its three mode files contribute EXPLAIN, INVESTIGATE, and GRILL_ME as user
options. `selectionRules` retains the requirement that a quiz be requested. The
helper supplies capabilities; Navigator handles intent, help, and resumption
without starting a cycle or depending on a persisted record.

Synchronizer declares `groups: []` and a user-style binding to the
SYNCHRONIZATION record. Scoper, Architect, and Auditor use conversation style
bindings; Tester, Documenter, and Reviewer use their own applicable records.

## Evolution

Changing instructions without changing invocation meaning needs no metadata
change. A new mode needs its own metadata block; a new inline option needs an
entry in its owning group. Adding a personal user-style needs only its file.
Changes to metadata vocabulary or condition semantics require an explicit
schema-version decision and matching helper support. Unknown data is reported
rather than silently interpreted using an older schema.
