# Maturity Levels

Read when the request names a target maturity level, such as "Level 3", "spike",
"prototype", "staging-ready", or "production-ready", or when the expected rigor
materially changes scope and is not otherwise established.

The ladder is shared vocabulary for writing scope. It is not a workflow state, a
protocol field, a role mode, or a gate. Scoper turns a selected level into
explicit constraints, acceptance conditions, and non-goals; downstream roles act
on those artifacts, not on the level number. Levels are not a compliance
certification.

## Using a Level in Scope

- Use the level the user names. When none is named and rigor materially changes
  scope, infer one and record it as an assumption: Level 1 for a mockup, spike,
  or proof of concept; Level 2 for a demo or prototype with real data; Level 3
  for ordinary maintainable work; Level 4 or 5 only when the request mentions
  staging, external users, public launch, compliance, scale, or operational
  readiness. When the choice materially changes scope and cannot be safely
  assumed, treat it as a blocking scope question.
- Record the level under Constraints, for example
  `Target maturity: Level 3 (internally testable) for the API and dashboard.`
- Use one level across every affected area unless the user sets different
  levels. Record an intentional mismatch as a constraint with its reason, for
  example a Level 2 dashboard on a Level 3 API during early product testing.
- Levels are cumulative: Level 4 includes every applicable Level 1-3
  expectation.
- Select only the expectations that apply to the requested work. A Level 3 API
  endpoint does not import the whole Level 3 column for unrelated areas.
- Write each selected expectation as an observable outcome, not an
  implementation choice. Prefer "Unauthenticated requests to the new endpoints
  are rejected" over naming a framework mechanism, and "Another engineer can run
  the service from its README on a clean checkout" over "write a README".
  Technical shape, such as layering, libraries, or tooling, remains Architect's
  decision.
- List capabilities from higher levels that a reader might assume under
  Non-goals, for example "Rate limiting and audit logging (Level 4) are
  deferred." This keeps them from being built or documented as present.
- For brownfield work, the Auditor's context records what already exists. Scope
  only the delta between the current state and the target, such as "bring this
  API to Level 4". To assess the current level outside a cycle, use Navigator's
  ASSESS mode.
- Keep defect severity separate from level. A missing authorization check is a
  defect at every level, not a Level 3 nicety.

## Shared Ladder

- Level 1, spike: a disposable proof of concept. Static or in-memory data, no
  real persistence or production authentication, minimal smoke checks. It
  validates a route, screen, API shape, or concept quickly and is labeled as not
  suitable for shared use.
- Level 2, connected prototype: the primary workflow runs on real data or a real
  backend with basic structure. Happy-path and at least one failure-path check.
  Suitable for demos and early product validation.
- Level 3, internally testable: the default for maintainable internal work.
  Clear responsibility boundaries, authentication and authorization where the
  product needs them, baseline security, complete user-facing states, meaningful
  automated tests, lint, type, and build checks, and setup documentation.
- Level 4, pre-production: Level 3 plus observability, structured and audit
  logging, abuse controls, staging readiness, migration and rollback procedures,
  and accessibility, performance, and security hardening. Ready for staging and
  controlled external testing.
- Level 5, public production: Level 4 plus service objectives, load and capacity
  testing, idempotency and multi-instance correctness, zero-downtime releases
  and migrations, tested backup restoration, threat modeling, incident runbooks,
  privacy and data-retention controls, secrets-manager integration, and
  production monitoring with named ownership.

## Area Expectations

Each list adds to the levels below it.

### Backend API

- Level 1: endpoints respond with in-memory or dummy data; the API contract is
  published as schema documentation; invalid input is rejected; a smoke check
  passes.
- Level 2: real persistence with managed migrations; separate development and
  test databases; environment-based configuration; the primary workflow stores
  and retrieves data.
- Level 3: HTTP, business, and persistence responsibilities are separated;
  errors map to consistent responses; authentication and authorization exist
  where required; cross-origin access and security headers are restricted; unit,
  integration, and API tests cover edge cases and access failures; lint,
  formatting, and type checks run in CI; another engineer can run the service
  from its documentation.
- Level 4: structured, redacted logs with request or correlation IDs; metrics,
  error tracking, and tracing reach their destinations; token refresh and
  revocation; rate limiting and audit logging; reproducible staging deployment;
  migration and rollback procedures documented and exercised.
- Level 5: service objectives with alerts; representative load tests pass;
  retried writes are idempotent; graceful startup and shutdown; correct under
  multiple instances; automated deploy and rollback; zero-downtime migrations;
  restoration from backup demonstrated; disaster-recovery plan; API versioning
  policy.

### Web Frontend

- Level 1: the route renders with mock or static data and the primary
  interaction works.
- Level 2: real backend data renders through typed API access; basic
  authentication; loading, empty, error, and not-found states are visible; the
  main mutation succeeds and refreshes data; one happy-path end-to-end check.
- Level 3: server-only data access cannot reach the client bundle; every server
  mutation checks authorization; forbidden and unauthorized states exist where
  authentication applies; component tests and end-to-end tests for critical
  flows; CI runs lint, type checks, tests, and a production build; primary flows
  work by keyboard; setup and architecture are documented.
- Level 4: performance budgets and bundle analysis; client error tracking with
  source maps and observability instrumentation; accessibility audit with
  automated, keyboard, and screen-reader checks on critical flows; reproducible
  staging; critical journeys pass in supported browsers.
- Level 5: Core Web Vitals objectives measured with field data; synthetic
  journey monitoring; privacy-safe analytics and consent management; frontend
  security review covering content security policy, cross-origin rules, session
  expiry, and data masking; a browser support policy; search metadata for public
  apps.

### Android App

- Level 1: static screens wired with basic navigation demonstrate a flow; no
  real data or backend.
- Level 2: multiple screens with production-shaped presentation: screen state
  lives outside composables and the navigation graph is defined; data may be
  stubbed.
- Level 3: every intended feature works end to end online, with the remote API
  as the source of truth; loading, success, error, and empty states are
  explicit; navigation is complete, including deep links where needed; state,
  data, and key UI flows have automated tests; ready for internal user testing.
- Level 4: the app stays useful offline: user work is saved locally, queued
  changes sync when connectivity returns, and sync success, retry, failure, and
  conflict are handled and visible to the user.
- Level 5: release security configuration, including code shrinking rules,
  secrets handling, exported component review, and certificate pinning where the
  threat model warrants it; no debug flags in release builds; crash reporting;
  accessibility requirements met; performance baselines verified; ready for UAT,
  beta, or store release.

### Security

- Level 1: no real secrets, sensitive data, or production credentials;
  protected-looking flows are clearly marked as mock; basic input validation and
  safe errors.
- Level 2: basic authentication and authorization for real data;
  environment-based secrets; exact cross-origin rules where browsers send
  credentials; responses return only needed fields; basic dependency hygiene.
- Level 3: server-side authorization in the correct layer, denied by default;
  safe session or token handling; secure headers; CSRF protection where needed;
  input and output allowlists; secrets and sensitive data redacted from logs;
  negative access-control tests; security checks in CI where available.
- Level 4: abuse controls and rate limits; structured audit events and security
  monitoring; hardened staging; least-privilege runtime and deployment; upload
  and server-side request forgery defenses where relevant; documented
  operational ownership.
- Level 5: threat model; verification against OWASP ASVS where appropriate;
  exercised detection and incident response; secrets manager; privacy and
  retention controls; supply-chain provenance and SBOM; independent review for
  high-risk systems.

### CI/CD

- Level 1: lint and tests run on pull requests and default-branch pushes; the
  results are informational.
- Level 2: type checks and an artifact build are added; dependency installs are
  cached; pull requests cannot merge without a passing pipeline; coverage is
  reported.
- Level 3: dependency vulnerability scanning; branch protection requires the
  checks; workflow permissions are least-privilege; superseded runs are
  cancelled; workflow files are validated.
- Level 4: merges to the default branch deploy to staging and pass a health
  check; production deployment behind an approval gate with a staging to
  production promotion path; versioned artifacts; failure notifications; secret
  scanning and static analysis on pull requests.
- Level 5: production smoke tests with automatic rollback on failure;
  zero-downtime deployment; an audit trail of approvals and deployed revisions;
  license scanning and SBOM on releases; infrastructure-as-code validation;
  pipeline duration, failure-rate, and recovery-time tracking; third-party
  actions pinned to commit SHAs with automated updates.

### Documentation

- Level 1: minimal setup and run instructions, prototype status, known
  limitations, mock data callouts, and smoke or manual check notes.
- Level 2: real data or backend setup, environment variables, the primary
  workflow, and basic architecture notes.
- Level 3: setup from a clean checkout, architecture, API contracts,
  authentication behavior, test and CI commands, troubleshooting, and developer
  handoff.
- Level 4: staging and deployment procedures, migration and rollback notes,
  observability and audit logging, rate limits, and operational ownership.
- Level 5: service objectives, incident runbooks, backup and restore, disaster
  recovery, data retention and privacy, secrets-management expectations, support
  escalation, versioning policy, and zero-downtime release procedures.
