# Default Stack for New Projects

Read in `FOUNDATION` mode when a new project, or a new service or app inside an
existing project, needs foundational technology choices that the user, the
completed scope, and established project constraints have not already made.

These defaults reflect the stack the IDinsight DSEM team builds and supports.
They are a starting proposal, not a mandate:

- Explicit user choices, scope constraints, organizational platforms, hosting
  requirements, and team skills override any default.
- An established project's stack always wins. Never migrate an existing project
  toward a default as an incidental choice; that is its own scoped change and an
  `EVOLUTION` design.
- Adopt only what the acceptance conditions need. A spike needs no database,
  authentication, or deployment pipeline; do not add components because they
  appear here.
- Record each adopted default in the design's Decision section with a brief
  rationale, and record meaningful rejected alternatives, so Developer reads the
  choice from the design rather than inferring it from this file.
- This file does not pin versions. Check current supported releases when
  deciding and record any version constraint that matters.

## Backend API

- Python managed with `uv`, using a currently supported Python release.
- FastAPI, with Pydantic v2 models and `pydantic-settings` for configuration.
- Relational data: PostgreSQL with SQLModel on SQLAlchemy, Alembic migrations,
  and the psycopg 3 driver (`postgresql+psycopg://`). See `postgres.md`.
- Embeddings or semantic search: pgvector on the same PostgreSQL instance. See
  `pgvector.md`.
- Document-shaped data: MongoDB. See `mongodb.md` for the driver and ODM choice,
  which replaces SQLModel and Alembic.
- Cache, rate-limit state, or lightweight queues, only when needed: Redis. See
  `redis.md`.
- Token authentication, when the design needs it: PyJWT and `pwdlib[argon2]`.
  Avoid `python-jose` and `passlib`, which are unmaintained.
- Rate limiting, when the design needs it: `slowapi`.
- Structured logging: Loguru with request-ID propagation.
- Quality tooling: Ruff for lint and format, mypy, pytest with httpx.
- Layout: feature modules (`app/<feature>/` with model, schema, router, and
  service files), shared `app/utils/` and `app/dependencies/`, and a repository
  layer only when data access is complex or reused. Developer's setup recipe is
  in `../../developer/references/fastapi.md`.

## Web Frontend

- Next.js App Router with TypeScript.
- shadcn/ui primitives styled with Tailwind CSS.
- Server Components by default, Server Actions for mutations, and a server-only
  data-access module for privileged calls.
- Vitest with React Testing Library for components, and Playwright for
  end-to-end flows and async Server Components.

## Android

- Kotlin with Jetpack Compose and Material 3.
- ViewModels exposing `StateFlow` screen state, and Navigation Compose.
- Retrofit with OkHttp, or another agreed HTTP client, with a matching JSON
  library such as Kotlin serialization.
- When the design is offline-capable: Room with KSP for local data and an
  outbox, and WorkManager for background sync. DataStore for small preferences.
  See `android-offline-sync.md`.
- Hilt for dependency injection once construction becomes non-trivial.
- A Gradle version catalog and the Compose BOM for dependency versions.

## CI/CD and Hosting

- GitHub Actions for CI and deployment automation, with Dependabot for action
  and dependency updates.
- Railway as the default deployment target for web apps, APIs, and workers,
  deployed from GitHub Actions. See `deploy-railway.md`.
- Google Cloud Platform, such as Cloud Run, when the organization or
  infrastructure requires it. See `deploy-gcp.md`.
- Other platforms, such as AWS, have no reference here. Record the choice and
  its rationale explicitly in the design.

## Related References

- `databases.md` for choosing between data stores.
- `observability.md` for logging, metrics, tracing, and alerting expectations.
