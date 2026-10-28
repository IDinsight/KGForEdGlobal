# FastAPI Testing Style

Apply with `universal.md` and `data-and-services.md` when testing FastAPI
services.

- Replace auth, database sessions, and external clients through
  `app.dependency_overrides` or the project's equivalent rather than patching
  internals, and clear overrides after each test.
- Run API tests against an isolated test database on the same engine as
  production. Do not substitute SQLite for PostgreSQL when engine semantics
  matter. Apply the project's migrations and roll back or truncate per test.
- Assert the status code and the error body shape together for each selected
  outcome, such as `201`, `204` with no body, `401`, `403`, `404`, `409`, or
  `422`. Select the outcomes the contract and risk call for under the shared
  budget, not a full matrix for every route.
- For protected routes in scope, select relevant missing, malformed, or expired
  credential cases and at least one cross-user or cross-tenant object access
  case when ownership or tenancy matters.
- Assert that responses omit sensitive fields such as password hashes, tokens,
  and internal flags, and that fields clients may not set are ignored or
  rejected rather than written.
- When pagination is in scope, check the enforced maximum, the default limit,
  and stable ordering across pages.
- Exercise service rules directly, and route wiring, dependencies, and
  serialization through `TestClient` or the project's HTTP test client.
- Set required settings before the app is imported. When error handling is in
  scope, check that an unhandled error returns the generic error body without a
  stack trace.

For setup fixtures and worked examples, read `../references/fastapi-testing.md`
when the assignment needs that depth. When the service calls an LLM or embedding
provider, also read `../references/llm-api-testing.md`.
