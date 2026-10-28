# Android Testing Style

- Test at the layer where the behavior lives, and select high-value cases under
  the shared budget rather than every state, sync status, or failure type.
- Test ViewModel state with fake repositories and a controlled coroutine test
  dispatcher. Assert the exposed UI state for relevant loading, success, error,
  empty, offline, or queued outcomes.
- Test repositories with a fake remote and an in-memory or test database.
  Simulate transient failure with a concrete exception from the fake rather than
  a connectivity flag, and assert persisted fields such as sync status and
  idempotency key, not just record presence.
- Test DAO queries, constraints, transactions, and Flow emissions against a real
  Room in-memory database rather than mocks. Use `MigrationTestHelper` or the
  project's equivalent when a migration is in scope and check that existing rows
  survive.
- Test worker logic directly with the installed `work-testing` helpers such as
  `TestListenableWorkerBuilder`, selecting relevant success, retry, failure, and
  repeated-processing idempotency cases. Check work-request constraints,
  backoff, and unique names separately from worker logic.
- Drive Compose UI tests through semantics and user actions, not layout
  internals. Assert relevant enabled state, content descriptions, and status
  text through semantics; use test tags only when no accessible name identifies
  a node.
- Process death, offline-to-online transitions, reconnect upload, and real
  background scheduling need instrumented, device, or manual evidence. A JVM
  test cannot prove them; record that limit or the manual result.

For deeper Android testing patterns, read `../references/android-testing.md`
when the assignment needs that depth.
