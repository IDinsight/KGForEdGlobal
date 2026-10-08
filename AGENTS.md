<!-- standards:start -->

# S.T.A.N.D.A.R.D.S.

This project uses the S.T.A.N.D.A.R.D.S. workflow.

For a question only about a role's available invocation options, use
`node .standards/bin/invocation.mjs <role> --client <client> --json` internally,
with the named skill and active client (`codex` or `claude`). Read the helper's
**Output contract** in `.standards/bin/schemas/invocation-metadata.md` when
needed. Distinguish selectable choices from state-selected or assessed behavior,
preserve saved choices and locks, and qualify unresolved values. Use the
returned metadata and filenames; use only non-null user-style `selector` values
in invocation examples. Never read unselected style contents or perform role
procedures. This is read-only discovery, not role invocation or workflow entry.
If discovery cannot run, report that the current options could not be verified;
do not invent a catalog, repair the installation, or ask the user to run the
helper.

Before acting on an invoked STANDARDS role skill other than Navigator, or on any
other STANDARDS workflow request (such as choosing the next cycle's mode,
changing completion policy, or switching verification cadence; starting,
reworking, promoting, cancelling, or signing off a cycle; or resetting or
uninstalling STANDARDS), read all of `.standards/PROTOCOL.md` (in consecutive
parts if a read shows only part of it) and each chapter in
`.standards/protocol/` that its reading guide names for the current state or
request, and follow them. Otherwise, do not change STANDARDS workflow state or
records.
<!-- standards:end -->
