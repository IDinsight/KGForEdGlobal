# S.T.A.N.D.A.R.D.S. Protocol: Installation, Reset, and Uninstall

This chapter is part of `.standards/PROTOCOL.md`. Read that file first; its
reading guide says when this chapter applies.

## Installed Runtime Contract

The user installs, upgrades, resets, and uninstalls STANDARDS with the
`standards` CLI. The CLI's full contract is maintained in the STANDARDS
repository (`INSTALLER.md`) and is not installed. An installed project should
provide:

- `AGENTS.md`: project-facing entrypoint to the protocol and role skills;
- `CLAUDE.md`: Claude Code compatibility entrypoint importing `AGENTS.md`;
- `.standards/PROTOCOL.md`: installed canonical protocol;
- `.standards/protocol/`: the protocol chapters that the reading guide in
  `.standards/PROTOCOL.md` names for specific situations, replaced as a whole on
  every install and upgrade;
- `.standards/VERSION.json`: installed framework version used to check upgrade
  eligibility;
- `.standards/INSTALLATION.json`: installer metadata, not workflow state,
  recording only the client settings, client paths, and hook files the installer
  created, so a reinstall or uninstall never claims or undoes the user's own
  settings;
- `.standards/MODE.md`: current `ProjectMode`;
- `.standards/STATE.md`: current workflow/cycle state and resumable coordination
  context;
- `.standards/bin/`: the runtime tools and hook script described in **Runtime
  Tools and Hooks**, replaced as a whole on every install and upgrade;
- `.standards/docs/`: the role-owned cycle records defined in **Workflow
  Artifact Provenance**, created only by the roles through `artifact init`;
- `.standards/user-styles/`: optional user-owned styles defined in **User
  Styles**;
- the S.T.A.N.D.A.R.D.S. workflow skills installed in the location required by
  the selected coding agent;
- unless the user declines it, the STANDARDS stop hook in
  `.claude/settings.json` for Claude Code and in `.codex/hooks.json` for Codex;
- explicit-invocation controls: Codex adapters use
  `allow_implicit_invocation: false`; Claude Code project settings use
  `skillOverrides.<skill>: "user-invocable-only"` for installed role skills,
  including Navigator despite its position outside the workflow state machine.

`.standards/MODE.md` contains exactly one canonical `ProjectMode`; its
greenfield-to-brownfield transition follows **Project Modes**.
`.standards/STATE.md` contains exactly one canonical `WorkflowState` and
`CycleMode` and follows **Persisted Workflow State**. A reinstall of the same
version, or an upgrade to a newer minor or patch release of the same major
version, keeps workflow state, Auditor context, cycle records, user styles, and
project-owned instructions and settings, and replaces the protocol, tools, skill
files, and managed blocks. STANDARDS provides no migration between major
versions: moving an existing project to a new major version means
`standards uninstall`, which deletes `.standards/`, followed by a fresh
installation.

### Running the CLI

Agents run the `standards` CLI only when the user explicitly asks, except that
they run reset for **Greenfield Bootstrap Cancellation** in
`.standards/protocol/user-decisions.md`, which adds its own steps to these.
`standards reset` and `standards uninstall` delete workflow data, so for either
command:

1. Pass `--project` with the project's absolute path and run the command with
   `--dry-run` first. Show the user the target, the planned changes, any
   warnings, and the exact command with `--yes`.
2. Run that command only after the user explicitly approves it. Outside a
   terminal the CLI does not ask for confirmation, so the approval must come
   from the user in the conversation.
3. If the preview changes before the command runs, show it again and get fresh
   approval. If the command refuses or fails, stop and report it with any
   backups it lists; do not delete or rewrite the files yourself.

### Project Reset

An explicit `standards reset` returns an installed project's workflow to the
state of a fresh installation without reinstalling anything, ending any active
cycle without a terminal state. Agents run it only as **Running the CLI**
allows.

- Default to the current directory; accept `--project <path>` and `--mode`.
  Offer `--dry-run` to report every planned change without writing files, and
  confirm interactively unless `--yes` is given.
- Require the runtime ownership marker and a valid `.standards/VERSION.json`
  that matches the CLI's version, because the fresh files come from the CLI's
  templates. Missing or invalid workflow files do not prevent a reset.
- Delete `.standards/CONTEXT.md` and `.standards/docs/`, warning with the count
  of cycle records, and write fresh `.standards/STATE.md` and
  `.standards/MODE.md`. Take the mode from `--mode`, otherwise choose it from
  the project's contents as a first installation would.
- Keep everything else: `PROTOCOL.md`, `protocol/`, `VERSION.json`,
  `INSTALLATION.json`, `bin/`, `.standards/user-styles/`, the skills, hooks,
  client settings, and managed blocks.
- Refuse symlinks in the paths it deletes. Keep backups during the operation and
  restore them on an ordinary failure. If recovery fails, keep the backups and
  report their location. An interrupted operation blocks install, reset, and
  uninstall until the user resolves it.

### Project Uninstallation

An explicit `standards uninstall` removes all verified STANDARDS skill packages
for Codex and Claude Code, the managed blocks in `AGENTS.md` and `CLAUDE.md`,
the STANDARDS hooks, installer-added settings whose values are unchanged, client
files and folders the installer created once nothing else is in them, and the
entire `.standards/` directory: saved workflow state, Auditor context, cycle
records, user styles, and any other content in it. It keeps project work outside
those paths, including reused scope or design documents, settings the user
changed, and a globally installed CLI. It is allowed in either project mode,
with or without an active cycle, and does not complete, sign off, cancel, or
revert project work. There is no force removal or client-only uninstall.
