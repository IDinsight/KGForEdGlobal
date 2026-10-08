# Data and Service Testing Style

- Use representative schema and established isolated database/service fixtures
  where real persistence or protocol semantics matter. State what substitutes
  omit; do not infer transaction or concurrency correctness from a shallow mock.
- Assert outputs and material effects together: writes, rollback, emitted
  events, retry counts, cleanup, and forbidden changes as required by the
  contract.
- Select relevant empty, malformed, unauthorized, duplicate, conflict, timeout,
  and partial-failure cases. Do not generate a full Cartesian product by
  default.
- Control clocks, identifiers, and external responses. Bound retries and waits;
  avoid tests that rely on wall-clock races or uncontrolled external services.
- Exercise resource and authorization boundaries with synthetic records. Check
  tenant/user isolation or sensitive-error behavior when affected by the change.
- Test migration or query behavior against the project's supported engine when
  that behavior is in scope; preserve unrelated data and established fixtures.
