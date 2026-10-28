# READMEs, API Contracts, and Documentation Impact

Read when creating or substantially restructuring a README, when a change
affects an API contract or specification, or when inventorying which documents a
change affects. Use the project's established documentation structure first;
this file is a fallback. It never expands the editing boundary, overrides the
protocol, the completed scope, or established project conventions, or permits
describing behavior that has not been established.

## Documentation Impact

Use this mapping to inventory which documents a change may affect. Update the
nearest relevant document rather than creating broad root-level docs, and record
documents intentionally left unchanged with the reason.

| Change                                        | Documents to check                                                                |
| --------------------------------------------- | --------------------------------------------------------------------------------- |
| API route, service, schema, auth, or error    | Backend README and the API contract or specification.                             |
| Internal API refactor with no contract change | Backend README only if architecture, setup, commands, or behavior changed.        |
| Database model or migration                   | README migration and run steps, and any affected API schemas.                     |
| Mobile offline, sync, data, or network        | Mobile README, plus the API contract if client expectations changed.              |
| Mobile UI or design-system pattern            | Mobile README when a feature, navigation pattern, or QA flow changed.             |
| Web route, auth, or data fetching             | Frontend README, plus API docs if backend contracts changed.                      |
| Environment or configuration                  | README configuration section and `.env.example` or sample configuration.          |
| Test, build, or other commands                | README command sections.                                                          |
| Deployment, operations, or security posture   | Operational docs: runbooks, deployment notes, monitoring notes, and release docs. |
| Scope or status                               | Planning or project-management docs that the project keeps.                       |

Make the smallest edits that keep future engineers unblocked. Do not rewrite a
whole README unless it is empty, generated boilerplate, or misleading. Prefer
exact commands and paths over vague prose.

## API Contract Rules

When the project keeps an API contract or specification, such as an OpenAPI
document, treat it as the contract between backend and clients and keep it in
step with established behavior.

- Update it when a change adds, removes, renames, or changes endpoint paths,
  methods, parameters, headers, request or response bodies, status codes,
  schemas, validation, auth or security, pagination, errors, uploads,
  idempotency, or operation descriptions.
- Do not update it for internal refactors that leave the public contract
  unchanged.
- Preserve the existing style and component reuse, and keep stable operation IDs
  unless the operation itself changes.
- Validate the document's structure with the project's tooling when it exists,
  and record the check.
- When the contract and implementation disagree, do not silently change either
  to match the other. Record the discrepancy and route it to its owner.

## README Depth

Match the README's depth to the scope. When the completed scope records a target
maturity level, its documentation acceptance conditions say what is required;
the Documentation section of `../../scoper/references/maturity-levels.md` shows
what is commonly expected at each level. Never describe a capability as
implemented when the scope defers it. List deferred capabilities explicitly when
a reader might otherwise assume them.

## Suggested Structure

Use only the sections the project and scope need, with real paths and commands.

```markdown
# Project Name

One or two sentences explaining what the project does and who it is for.

## Status

- Maturity: prototype, internal, staging, or production, and the target maturity
  level when the scope records one
- Current version
- Build status
- Service health or monitoring, when the project has it

## Tech Stack

- Language, framework, database, messaging or queues, infrastructure, testing,
  and observability, as they actually exist.

## Setup

### Prerequisites

- Required software, libraries, tools, and versions.

### Installation

- Dependency installation.
- Database or data-layer setup, when the project has persistence.
- Environment variables or configuration files.
- Seed scripts, when applicable.
- How to run the application locally.

## Run, Build, and Test

Exact commands from the repository: run, build, automated tests, lint, and type
checks where they exist, or the smoke and manual checks for a prototype.

## Repository Structure

The actual top-level layout with one-line purposes.

## Architecture

The implemented architecture, organized by feature where useful so a reader can
see UI, API or router, service, persistence, database, sync, and operational
boundaries.

## API Contract

The source-of-truth specification path, auth scheme, base URL or route prefix,
pagination and error conventions, and idempotency or versioning policy when the
project has one.

## Configuration

| Name | Required | Purpose |
| ---- | -------- | ------- |

Environment variables and secrets by name only, never real values, with staging
and production differences when they exist.

## Operations

Deployment and staging, migration and rollback, observability and audit logs,
service objectives, incident response, backup and restore, disaster recovery,
and privacy or retention, only as far as the project has them.

## Manual QA

Realistic manual checks that automated tests do not cover, especially for UI,
mobile, offline and sync, security, and deployment behavior.

## Deferred Capabilities

| Capability | Reason deferred |
| ---------- | --------------- |
```

Include a Mermaid diagram only when the repository already uses Mermaid or the
diagram materially improves handoff. Do not paste an example tree unchanged when
it does not match the repository.
