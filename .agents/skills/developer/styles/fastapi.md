# FastAPI Development Style

Apply with `universal.md` and `python.md` for FastAPI implementation. Also apply
`sql.md` when the affected work materially changes queries, persistence models,
or migrations.

- Follow the project's app layout, router registration, and dependency
  conventions; do not reorganize modules in a feature change. Check the
  installed FastAPI and Pydantic versions before using version-specific APIs.
- Keep route handlers to HTTP concerns: validate input, inject dependencies,
  call the service, and map its result to a status code and response model. Keep
  business rules and queries in the project's service layer when one exists;
  services stay free of `HTTPException` and request objects. Treat adding a
  service or repository layer as architecture when not already established.
- Map outcomes to status codes consistently with existing endpoints, for example
  `201` on create, `204` with no body on delete, `404` when missing, `409` on
  conflict, and `401` versus `403`. Let FastAPI validation return `422`; declare
  constraints in schemas and parameters, with `Literal` or `Enum` for fixed
  value sets.
- Keep the project's error response shape (FastAPI's `detail` by default) rather
  than adding a parallel envelope, and never expose stack traces, SQL, or
  constraint names.
- Authenticate through the project's shared `Depends` or `Security` dependencies
  on every protected route, router, mounted app, WebSocket, and webhook, not in
  handlers or services. Authentication does not grant access to a specific
  object or tenant; check that server-side and never trust client-supplied owner
  or tenant IDs.
- Treat choosing or changing the auth, session, token, or password-hashing
  mechanism as architecture when not already established. In existing JWT code,
  keep an explicit algorithm allowlist and verify signature and expiry.
- Keep request and response schemas separate from persistence models. Response
  models allowlist safe fields and never include password hashes, tokens, or
  internal flags. Map only client-settable fields onto models, and apply only
  set fields on partial updates, for example `exclude_unset=True`.
- Bound every list endpoint, including search and filtered ones, with a
  validated maximum page size such as `Query(le=...)`. Filter, sort, and slice
  in the query, and batch-load related rows, for example with a join or
  `selectinload`, rather than querying per item.
- Get database sessions from the project's dependency, and perform a bulk write
  in one transaction rather than a loop of calls that each commit.
- Follow the project's sync or async handler convention. FastAPI runs plain
  `def` handlers and dependencies in a threadpool, so prefer them over
  `async def` when the call path blocks.
- Read configuration through the project's settings mechanism, such as
  `pydantic-settings`, and leave required secrets without defaults so startup
  fails fast.
- List CORS origins exactly; never combine `allow_origins=["*"]` with
  `allow_credentials=True`. CORS is not CSRF protection; cookie-authenticated
  mutations need the project's CSRF defense.
- When the project uses Alembic autogenerate, review each revision for missed
  renames, server defaults, and enum changes, and do not call `create_all()` at
  startup. Never apply a migration that drops data automatically; report the
  command and a backup step.
- Use the project's logger and request-ID context when they exist. Log business
  decisions and errors rather than requests middleware already records, and
  never log authorization headers or cookies.
- When the project rate-limits routes, apply its limiter to new login, signup,
  and other abuse-prone endpoints; treat adding rate limiting as architecture.

For project setup, deeper patterns, and security detail, read
`../references/fastapi.md` and `../references/security-fastapi.md` when the work
needs that depth.
