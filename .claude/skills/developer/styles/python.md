# Python Development Style

Apply with `universal.md` for Python implementation.

- Follow the repository's supported Python version, formatter, linter, and type
  checker.
- Add type hints at public boundaries and where they materially improve
  correctness. Avoid ornamental annotations that obscure simple code.
- Prefer precise types over `Any`. Narrow unknown or heterogeneous values before
  using them.
- Avoid mutable default arguments. Use `None` or a factory as appropriate.
- Use context managers for files, locks, transactions, and other scoped
  resources.
- Catch the narrowest exception that represents the failure being handled. Catch
  exceptions only when the code can recover, add useful context, translate a
  boundary error, or perform required cleanup; do not use broad silent catches.
- Preserve the original cause when translating an exception when that cause is
  useful for debugging.
- Do not use `assert` for runtime input validation, permissions, or other checks
  that must always run; assertions can be disabled. Reserve them for internal
  invariants.
- Prefer explicit data models or small value objects when data has invariants;
  do not create classes merely to group unrelated functions.
- Keep async code consistently async across the call path. Avoid blocking I/O in
  event-loop code unless the project already provides a safe adapter.
- Avoid surprising work at import time, especially network calls, filesystem
  mutation, process startup, or environment-dependent initialization. Put such
  work behind an explicit application or function boundary.
- Prefer `pathlib` when it fits established project conventions and improves
  path handling clarity.
- Keep comprehensions simple. Use ordinary loops when filtering, branching, or
  side effects make the expression difficult to read.
- Preserve import boundaries and avoid circular-import workarounds that hide a
  structural problem.
