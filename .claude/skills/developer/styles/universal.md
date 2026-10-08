# Universal Development Style

Apply these rules to all implementation unless a higher-precedence project
constraint requires otherwise.

- Make the smallest coherent change that satisfies the approved step.
- Prefer clear names and explicit control flow over compressed cleverness.
- Keep functions, modules, and components focused on one responsibility.
- Preserve existing public behavior unless the active contract explicitly
  changes it.
- Reuse established helpers, abstractions, and patterns before adding parallel
  ones.
- Avoid speculative abstractions, premature generalization, and unrelated
  refactors.
- Validate data at trust boundaries. Do not rely on untrusted input shape or
  implicit coercion when correctness or security depends on it.
- Enforce authorization on the server or in the data-access layer. UI visibility
  and client-side checks are not access control.
- Fail explicitly and with actionable errors. Do not silently swallow failures.
- Keep side effects visible and localized where practical.
- When adding remote or external I/O, follow the project's timeout,
  cancellation, and retry conventions. Do not add unbounded waits or blindly
  retry operations that may not be safe to repeat.
- Do not introduce a new dependency when the existing stack can reasonably
  satisfy the approved design. Treat consequential dependency choices as
  architecture when they are not already established.
- Do not hard-code secrets, credentials, environment-specific endpoints, or
  mutable operational values in source. Do not write secrets or sensitive data
  to logs or error messages.
- Write comments for non-obvious intent, constraints, invariants, or tradeoffs;
  do not narrate obvious code.
- Remove dead code created by the change. Do not leave commented-out alternate
  implementations.
- Keep externally visible names, data shapes, errors, and side effects
  consistent with established contracts.
- Prefer deterministic behavior. Make ordering, time, randomness, locale, and
  concurrency assumptions explicit when they affect outcomes.
- Verify changed behavior using the repository's existing checks and tests. Do
  not create or rewrite formal test artifacts; Tester owns formal test
  implementation and verification in STANDARD cycles. Do not weaken existing
  tests merely to make a change pass.
- Use the repository's formatter, linter, type checker, build system, and
  generated-code workflow rather than creating competing tooling.
