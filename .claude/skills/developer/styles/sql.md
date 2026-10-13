# SQL Development Style

Apply with `universal.md` for SQL, migrations, and query implementation.

- Follow the project's database engine, migration tool, naming conventions, and
  supported compatibility level.
- Use parameterized queries or the established query builder. Never interpolate
  untrusted values into SQL text.
- Parameters do not make dynamic table names, column names, sort directions, or
  other SQL syntax safe. Map those choices from a fixed allowlist when they must
  be dynamic.
- Name columns explicitly in production queries and data movement. Avoid
  `SELECT *` across durable or external boundaries.
- Make join conditions explicit and verify how many rows each join can produce.
  Do not let accidental many-to-many joins silently duplicate data.
- Make null semantics intentional in predicates, joins, constraints, and writes.
- Use transactions when multiple writes must succeed or fail as one logical
  operation; keep transaction scope no broader than necessary and avoid waiting
  on unrelated network or user work while holding a transaction open.
- When concurrent writers can touch the same rows or resources, follow the
  project's locking and isolation strategy and keep lock acquisition order
  consistent where practical.
- Add or change constraints when they represent an established invariant, not as
  an incidental guess about product behavior.
- Treat destructive schema changes, migration compatibility, online rollout,
  backfills, locking risk, and cross-version coexistence as architecture when
  they are consequential and not already designed.
- Add indexes only when the access pattern and project constraints justify them;
  consider write cost and existing indexes rather than indexing reflexively.
  Check the query plan for performance-sensitive changes instead of guessing.
- Specify ordering whenever result order is part of observable behavior. Use a
  deterministic tie-breaker when stable pagination or repeatable ordering
  matters.
- Use the least-privileged database role that satisfies the application's needs;
  application code should not connect with owner or administrator privileges.
- Keep migrations deterministic and compatible with the repository's deployment
  and rollback expectations.
