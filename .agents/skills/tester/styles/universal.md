# Universal Testing Style

- Test observable behavior and contracts, not private structure or a copy of the
  implementation's algorithm. Name scenarios by the behavior they establish.
- Extend the existing suites and use established assertions, fixtures, commands,
  package boundaries, and supported tool versions. Avoid parallel harnesses.
- Keep setup minimal and deterministic. Control clocks, randomness, locale,
  ordering, network boundaries, and asynchronous completion when relevant.
- Isolate state and clean up resources even on failure. A scenario must not
  depend on execution order or leftovers from another run.
- Mock at a controlled boundary, preserving its real contract. Do not mock away
  the behavior being verified or call a mocked integration a real system test.
- Make failure output useful: assert expected values, errors, and side effects,
  including absence of forbidden effects. Avoid assertions that always pass.
- Prefer bounded condition-based waiting over sleeps. Await asynchronous
  assertions and ensure the test runner discovers and executes the intended
  tests.
- Cover meaningful invalid input and failure behavior where relevant. Security
  checks must exercise the enforcement boundary, not just UI visibility.
- Review snapshot changes against intended behavior; a regenerated snapshot is
  not independent proof. Avoid broad snapshots of incidental structure.
- Keep fixtures synthetic or safely sanitized. Do not rely on live accounts,
  production mutation, or secret values for routine verification.
- Preserve unrelated changes and coverage. Refactor test setup only when needed
  for trustworthy evidence or maintainable tests for the active change.
