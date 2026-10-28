# Android Testing

Read when verifying Android, Kotlin, or Jetpack Compose behavior and the
assignment needs deeper patterns than the style file gives: which layer to test,
which cases to select, and how to set up Room, WorkManager, ViewModel, and
Compose UI tests. It supplements `../styles/android.md` and never overrides the
protocol, the active contract and acceptance conditions, the test budget,
repository-enforced configuration, or established project test conventions. When
the version catalog, installed version, or official documentation differs from
this file, follow them.

## Selecting Cases

Test at the layer where the behavior lives, and spend the budget on the cases
the active change and its acceptance conditions make relevant. The budget is at
most five high-value scenarios per source file for the active change, and it is
a ceiling, not a quota. The candidate lists below are menus to select from, not
checklists to complete. Reuse existing coverage first.

Layer priorities, in the order they usually pay off:

- ViewModel tests for UI state, validation, and the success, failure, offline,
  or queued branches the change touches.
- Repository tests with a fake remote and a local test database, when the change
  touches data flow or persistence.
- DAO tests for queries, constraints, and Flow emissions, when the change
  touches Room queries or schema.
- Worker tests for the outcomes the change affects, when the project uses
  WorkManager.
- Compose UI tests for key flows and the semantics the change affects.
- Instrumented, device, or manual evidence for what a JVM test cannot prove,
  such as process death, reconnect upload, and real background scheduling.

Offline and sync scenarios apply only when the design makes the feature
offline-first or gives it durable synchronization. Do not add them to features
that are online-only.

## Test Dependencies

Test-only dependencies are Tester-owned test configuration. Align them with the
version catalog and the existing library versions:

- `androidx.room:room-testing` for `MigrationTestHelper`, matching the Room
  version.
- `androidx.work:work-testing` for `TestListenableWorkerBuilder`,
  `WorkManagerTestInitHelper`, and `TestDriver`, matching the WorkManager
  version.
- `androidx.compose.ui:ui-test-junit4` (managed by the Compose BOM, so declared
  through `platform(...)` for `androidTestImplementation`) and
  `androidx.compose.ui:ui-test-manifest` as `debugImplementation`.
- `org.jetbrains.kotlinx:kotlinx-coroutines-test` for `runTest` and test
  dispatchers.
- `androidx.test:core` and `androidx.test.ext:junit` for instrumented tests.

Adding the matching test artifact for a library the project already uses is test
configuration. Introducing a new test framework or tool the project does not
use, such as Robolectric, a screenshot-testing library, or a new assertion
library, is a consequential tooling decision that belongs to Architect when not
already established.

## Room and DAO Tests

Run DAO tests against Room's in-memory database rather than mocks. They are
usually instrumented; they can run on the JVM when the project already uses
Robolectric.

Candidate scenarios:

- Pending operations are returned in the intended order.
- Uniqueness and foreign-key constraints are enforced.
- A transaction commits or rolls back as a unit.
- Updates and deletes produce the expected local state.
- Flow-backed queries emit new values after writes.
- Paging or filtered queries return the expected pages and ordering.
- Cancellation behaves correctly for long-running queries, where relevant.

When a migration is in scope, use `MigrationTestHelper` (from `room-testing`) or
the project's equivalent. Candidate scenarios:

- The migration runs from each supported earlier schema without crashing.
- Existing rows survive with their data intact.
- Indexes, constraints, and default values are correct after upgrade.
- The exported schema files the migration relies on are committed and match the
  actual schema.

```kotlin
@RunWith(AndroidJUnit4::class)
class PendingSyncDaoTest {
    private lateinit var db: AppDatabase
    private lateinit var dao: PendingSyncDao

    @Before
    fun setup() {
        db = Room.inMemoryDatabaseBuilder(
            ApplicationProvider.getApplicationContext(),
            AppDatabase::class.java,
        )
            // Acceptable in isolated tests only; never in production code,
            // where it hides accidental main-thread database access.
            .allowMainThreadQueries()
            .build()
        dao = db.pendingSyncDao()
    }

    @After
    fun teardown() {
        db.close()
    }

    @Test
    fun pendingOperationsAreReturnedOldestFirst() = runTest {
        dao.insert(operation(id = "newer", createdAt = 2))
        dao.insert(operation(id = "older", createdAt = 1))

        assertThat(dao.pending().first().map { it.id })
            .containsExactly("older", "newer")
            .inOrder()
    }
}
```

Replace placeholders such as `AppDatabase`, `PendingSyncDao`, and
`operation(...)` with the project's real classes and fixture builders.

## Repository Tests

Run repository tests on the JVM with a fake remote API and a fake or in-memory
local data source. Simulate failure by making the fake throw a concrete
exception, not by setting a connectivity flag; the test should assert the
system's response to a real failure, not to a reachability state.

Candidate scenarios:

- A successful remote response is persisted locally and exposed correctly.
- A transient network failure (the fake throws `IOException`) leaves the local
  record pending and requests sync, when the design is offline-first.
- A server validation or conflict error marks the record `FAILED` or `CONFLICT`
  with a useful message, and local data is kept.
- An offline write assigns a stable client ID, sets the pending status, and
  enqueues sync.

Assert fields, not just record presence: sync status, idempotency key, operation
type, and timestamps where the design defines them.

```kotlin
@Test
fun saveWhileOfflinePersistsPendingOperation() = runTest {
    // Throw a concrete failure rather than toggling a reachability flag.
    val remote = FakeAppApi { throw IOException("Simulated network failure") }
    val repository = AppRepository(local, remote, syncScheduler)

    repository.saveDraft(draft)

    val saved = local.drafts().first { it.id == draft.id }
    assertThat(saved.syncStatus).isEqualTo(SyncStatus.PENDING)
    assertThat(saved.idempotencyKey).isNotNull()
    assertThat(local.pendingSyncOperations()).hasSize(1)
    // Assert scheduling here only when the repository schedules directly.
    // When a coordinator owns scheduling, assert that contract instead.
    assertThat(syncScheduler.enqueued).isTrue()
}
```

## ViewModel Tests

Run ViewModel tests on the JVM with fake repositories and a controlled coroutine
test dispatcher. Replace `Dispatchers.Main` so `viewModelScope` runs on the test
scheduler.

Candidate scenarios:

- State moves through loading to success, error, or empty as the change
  requires.
- Validation produces the expected state and prevents forbidden effects.
- Success and failure branches from the fake repository update state correctly.
- Pending or queued-sync status is reflected accurately, when the design has
  one.

```kotlin
// Swaps Dispatchers.Main for a test dispatcher for each test.
class MainDispatcherRule(
    private val dispatcher: TestDispatcher = UnconfinedTestDispatcher(),
) : TestWatcher() {
    override fun starting(description: Description) =
        Dispatchers.setMain(dispatcher)

    override fun finished(description: Description) =
        Dispatchers.resetMain()
}

class EditRecordViewModelTest {
    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    @Test
    fun blankNameShowsValidationErrorAndDoesNotSave() = runTest {
        val repository = FakeRecordRepository()
        val viewModel = EditRecordViewModel(repository)

        viewModel.onNameChanged("")
        viewModel.onSave()

        assertThat(viewModel.uiState.value.nameError).isNotNull()
        // Assert the absence of the forbidden effect, not just the message.
        assertThat(repository.savedRecords).isEmpty()
    }
}
```

When the ViewModel builds `uiState` with
`stateIn(SharingStarted.WhileSubscribed(...))`, `value` does not update without
a subscriber. Start a collector before asserting:

```kotlin
backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) {
    viewModel.uiState.collect()
}
```

## WorkManager Tests

Test worker logic directly with `TestListenableWorkerBuilder` on the JVM. Keep
this separate from WorkManager integration concerns.

Candidate worker scenarios:

- A successful upload returns `Result.success()` and marks the local record
  synced.
- A transient network failure returns `Result.retry()` and leaves local data
  intact.
- An auth failure (401 or 403) returns `Result.failure()` and marks the record
  failed.
- A validation or conflict response returns `Result.failure()` and marks the
  record `FAILED` or `CONFLICT`.
- A malformed local payload returns `Result.failure()` without crashing.
- A defined max-attempt policy returns `Result.failure()` and stops retrying.

```kotlin
@Test
fun transientNetworkFailureReturnsRetry() = runTest {
    val worker = TestListenableWorkerBuilder<SyncWorker>(context)
        .setWorkerFactory(
            // Project test factory that injects fakes into the worker.
            TestWorkerFactory(
                api = FakeApi { throw IOException("Simulated failure") },
                local = local,
            ),
        )
        .build()

    assertThat(worker.doWork()).isEqualTo(ListenableWorker.Result.retry())
    assertThat(local.pendingOperations()).isNotEmpty()
}
```

Check the work request's configuration separately from worker logic: the
intended network constraint, the backoff policy the design specifies, and the
unique-work name where required. The exact inspection API varies by WorkManager
version; `workSpec` is library-restricted, so adapt to the installed
`work-testing` API and the project's lint settings.

```kotlin
@Test
fun syncRequestRequiresNetworkAndUsesExponentialBackoff() {
    val request = buildSyncRequest()

    assertThat(request.workSpec.constraints.requiredNetworkType)
        .isEqualTo(NetworkType.CONNECTED)
    assertThat(request.workSpec.backoffPolicy)
        .isEqualTo(BackoffPolicy.EXPONENTIAL)
}
```

Use `WorkManagerTestInitHelper` with `TestDriver` only for integration concerns
such as constraint satisfaction, chaining, periodic scheduling, and unique-work
replacement, not for the worker's business logic.

### Idempotency

When the design retries creates or uploads, select the idempotency scenario that
carries the most risk:

- Processing the same outbox operation twice, for example a worker restart after
  a successful upload but before local acknowledgement, does not create a
  duplicate server record.
- The idempotency key flows from the local record into the API request.
- Concurrent workers cannot upload the same outbox entry twice, through an
  atomic status update or lock.

## Compose UI Tests

Drive Compose UI tests through semantics and user actions, not the component
tree or layout internals. Use semantic finders such as text and content
description; use test tags only when no accessible name identifies a node.

Candidate scenarios:

- A key user flow completes end to end (fill the form, submit, see the result).
- Loading, success, error, and empty states render the expected accessible
  content.
- Offline or pending-upload status appears under the right conditions, when the
  design has one.
- Enabled and disabled states are reflected in semantics.
- Error messages are accessible through content descriptions, roles, and
  announcements.
- Interactive controls meet the 48dp touch-target minimum.

```kotlin
class RecordScreenTest {
    @get:Rule
    val composeTestRule = createComposeRule()

    // Resolve copy from resources so the test follows localization.
    private val context = ApplicationProvider.getApplicationContext<Context>()

    @Test
    fun pendingRecordShowsSavedLocallyAndRetryIsUsable() {
        var retried = false
        composeTestRule.setContent {
            AppTheme {
                RecordScreen(
                    uiState = RecordUiState(pendingUploads = 1),
                    onRetry = { retried = true },
                )
            }
        }

        composeTestRule
            .onNodeWithText(context.getString(R.string.saved_on_device))
            .assertIsDisplayed()
        composeTestRule
            .onNodeWithText(context.getString(R.string.retry))
            .assertIsEnabled()
            .assertTouchHeightIsAtLeast(48.dp)
            .performClick()
        assertThat(retried).isTrue()
    }
}
```

## Device and Manual Evidence

A JVM test cannot prove process death, offline-to-online transitions, reconnect
upload, or real background scheduling. When the design is offline-first or has
durable sync, select the scenarios the change affects and record the device,
emulator, or manual result, or record that the evidence could not be produced:

1. Start online, load data, go offline, and confirm cached data stays usable.
2. Create or edit data offline and confirm it shows immediately as saved
   locally.
3. Kill and relaunch the app while data is pending and confirm the pending state
   survives.
4. Reconnect and confirm background work uploads the pending operation once.
5. Simulate a transient server or network failure and confirm retry and backoff
   leave local data intact.
6. Simulate a validation or conflict response and confirm the UI offers a useful
   retry or correction path.

## Running Tests

- JVM unit tests: `./gradlew :app:testDebugUnitTest`, narrowed with
  `--tests "com.example.feature.EditRecordViewModelTest"` while iterating.
- Instrumented tests, including Compose UI and most Room tests:
  `./gradlew :app:connectedDebugAndroidTest`. These need a connected device or
  running emulator; record the device or emulator API level with the result.
- Prefer the project's established Gradle tasks and CI commands when they differ
  from these defaults. When no device or emulator is available, record the
  attempted command and the limitation rather than treating the scenario as
  passed.

## Authoritative References

- [Test your Room database](https://developer.android.com/training/data-storage/room/testing-db)
- [Migrate your Room database (testing migrations)](https://developer.android.com/training/data-storage/room/migrating-db-versions)
- [Test Kotlin coroutines on Android](https://developer.android.com/kotlin/coroutines/test)
- [WorkManager: testing worker implementation](https://developer.android.com/develop/background-work/background-tasks/testing/persistent/worker-impl)
- [Test your Compose layout](https://developer.android.com/develop/ui/compose/testing)
- [Accessibility in Compose](https://developer.android.com/develop/ui/compose/accessibility)
- [Android architecture guide](https://developer.android.com/topic/architecture)
