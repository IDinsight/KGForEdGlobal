# Databases

Read when the technical design must choose, configure, or change a database or
other persistence layer. Record the resulting decisions in the technical design;
Developer implements them. Setup steps here are implementation detail for
Developer once the design selects this option. This reference never overrides
the protocol, the completed scope, established project constraints, or official
documentation for the installed version.

These principles apply regardless of stack, ORM, or database engine. The engine
references hold the concrete configuration:

- [`postgres.md`](postgres.md) for PostgreSQL.
- [`pgvector.md`](pgvector.md) for vector similarity search on PostgreSQL.
- [`mongodb.md`](mongodb.md) for MongoDB.
- [`redis.md`](redis.md) for optional caching and ephemeral state.

Where an engine reference conflicts with this file, the engine reference wins
for that engine.

Some sections note the maturity level at which a practice is commonly expected,
using the ladder in
[`../../scoper/references/maturity-levels.md`](../../scoper/references/maturity-levels.md).
Those notes are context, not gates. The completed scope and its acceptance
conditions decide what the design must include.

## Choosing a Data Store

- Relational data with integrity constraints, joins, and transactions:
  PostgreSQL. This is the usual primary store.
- Nearest-neighbour search over embeddings stored alongside relational data:
  pgvector on the same PostgreSQL instance. It extends PostgreSQL rather than
  replacing it.
- Document-shaped data whose structure varies per record or nests deeply, where
  cross-document transactions are rare: MongoDB. It replaces the relational
  migration toolchain with its own approach to schema change.
- Response caching, rate-limit counters, session storage, distributed locks, or
  lightweight pub/sub: Redis, as an optional add-on. It is never the primary
  store, and the application must work correctly when it is unreachable.

Keep one primary store unless the scope needs more. Each additional store adds
provisioning, backup, monitoring, and failure modes that the design must cover.

## Decisions to Record

The technical design should settle, where the scope makes them relevant:

- the engine, its supported major version, and any extension;
- database names per environment;
- the migration tool and how destructive migrations are released;
- the test database contract and isolation strategy, so Tester can rely on it;
- startup behavior when the database is unreachable;
- pool sizing relative to the number of application instances;
- the health check contract;
- where credentials come from in each environment;
- connection security, network access, and backup and restore expectations.

## Naming Convention

| Environment | Pattern               | Example           |
| ----------- | --------------------- | ----------------- |
| Development | `{project_name}_dev`  | `orders_api_dev`  |
| Test        | `{project_name}_test` | `orders_api_test` |
| Production  | `{project_name}_prod` | `orders_api_prod` |

- Replace hyphens with underscores.
- Never use generic names like `dev` or `test`. They collide on shared
  infrastructure.
- Never share a database between environments.

At maturity Level 1 (spike) there is usually no real database. Recording the
names in `.env.example` as placeholders is enough, with no connection
configuration or migrations.

## Connectivity and Migrations

Commonly expected from maturity Level 2 (connected prototype).

### Environment Configuration

Define two database URLs, even locally:

```dotenv
DATABASE_URL=<driver>://[DB_USER]:[DB_PASSWORD]@[DB_HOST]/{project}_dev
TEST_DATABASE_URL=<driver>://[DB_USER]:[DB_PASSWORD]@[DB_HOST]/{project}_test
```

- `.env.test` sets `DATABASE_URL` to the value of `TEST_DATABASE_URL`, so the
  application needs no awareness of tests.
- Production URLs are never stored in any file in the repository. The deployment
  platform injects them.

### Startup Retry

The application must not crash if the database is unreachable at startup.
Container platforms often start the application before the database is ready.

- Retry on startup with exponential backoff.
- Log each retry at WARN level with the elapsed time.
- Exit non-zero only after a configurable timeout.

```dotenv
DB_CONNECT_RETRIES=5
DB_CONNECT_RETRY_INTERVAL_SECONDS=2
```

Do not rely on `depends_on` in Docker Compose for readiness. Without a health
condition it waits for the container to start, not for the database to accept
connections.

### Migrations

- Schema changes always go through migrations. Never use `create_all()`,
  `syncIndexes()`, or auto-sync on startup.
- Create an initial empty migration to prove the toolchain works before writing
  any models.
- Commit migration files with the code change that requires them.
- Destructive migrations (drop column, drop table, drop index) are never run
  automatically, whether at startup, in CI, or in a platform pre-deploy step.
  The design should require that they are presented as a command for a person to
  run manually after a verified backup.

### Test Database Lifecycle

The test database is fully isolated from development. Tester owns the tests; the
design fixes the contract they rely on.

- Migrations run to the latest revision at the start of each test run, not on
  application startup.
- Pick one isolation strategy per project: transaction rollback per test
  (fastest) or truncation between tests.
- Never substitute SQLite for PostgreSQL or MongoDB in tests. Dialects and
  semantics differ, which gives false confidence.

## Constraints, Indexes, and Transactions

Commonly expected from maturity Level 3 (internally testable).

- Enforce NOT NULL, unique, and foreign key constraints in the database, not
  only in application code.
- Index the columns that frequent WHERE, JOIN, and ORDER BY clauses depend on
  when the access pattern justifies the write cost; check the query plan for
  performance-sensitive queries rather than indexing reflexively. Review
  autogenerated migrations for missing or redundant indexes before applying
  them.
- Give each write that spans several tables explicit transaction ownership at
  the service or repository layer.

## Production Pooling and Observability

Commonly expected from maturity Level 4 (pre-production).

### Connection Pooling

Never rely on ORM or driver defaults. They are sized for demos.

| Setting             | Principle                                               |
| ------------------- | ------------------------------------------------------- |
| Min pool size       | Handle baseline traffic from a cold start               |
| Max pool size       | `floor(db_max_connections / app_instances) - 2`         |
| Connection recycle  | Recycle after 30 minutes to prevent stale connections   |
| Pre-ping / validate | Test a connection before use so stale ones drop quietly |
| Acquire timeout     | Fail fast when the pool is exhausted                    |

The `- 2` reserves connections for migrations, admin tools, and monitoring.
PostgreSQL's default `max_connections` is 100, so with two instances the maximum
pool per instance is `floor(100 / 2) - 2 = 48`. Recalculate whenever the
instance count or autoscaling ceiling changes.

```dotenv
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
DB_POOL_RECYCLE=1800
DB_POOL_TIMEOUT=30
```

### Query and Pool Signals

- Log every query that exceeds a configurable threshold at WARN level, with the
  query and its duration.
- Log pool exhaustion events at ERROR level.
- Never leak raw driver error messages to API responses. Catch them at the
  service or repository layer and let the global error handler produce the 500.

```dotenv
DB_SLOW_QUERY_MS=500
```

### Health Check

The `/health` route executes a real query on every call rather than testing TCP
connectivity:

- SQL: `SELECT 1`
- MongoDB: `db.command("ping")`
- Redis: `PING`

Return 503 when the primary database check fails. Do not cache the result. An
optional dependency such as Redis reports a degraded state without failing the
check; see [`redis.md`](redis.md).

## Isolation, Secrets, and Recovery

Commonly expected at maturity Level 5 (public production).

- Schema isolation. Use a dedicated application schema rather than the database
  default (`public` for PostgreSQL). Grant only the privileges the application
  needs.
- Secrets. Database credentials come from a secrets manager, such as GCP Secret
  Manager, AWS Secrets Manager, or HashiCorp Vault, rather than values pasted
  into deployment configuration. Rotate them on a defined schedule.
- Connection security. Require TLS or mTLS for all database connections, and
  restrict network access to private networking where the platform allows it.
- Backup and recovery. Run backups on a tested schedule, and demonstrate
  restoration rather than assume it works.
