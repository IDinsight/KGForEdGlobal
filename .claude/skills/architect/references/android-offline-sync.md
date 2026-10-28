# Android Data Layer and Offline Sync

Read when the technical design must define an Android app's data layer, decide
whether it works offline, or design how queued changes sync. Record the
resulting decisions in the technical design; Developer implements them under
`../../developer/styles/android.md`. This reference never overrides the
protocol, the completed scope, established project constraints, or official
Android documentation for the installed versions.

A common product need for field apps is: use the API when online, stay useful
offline, save user work locally when offline, and sync queued changes when
connectivity returns. Design for it only when the scope requires it; an
online-only app is simpler and often enough. Offline capability is commonly
expected from maturity Level 4 for Android in
`../../scoper/references/maturity-levels.md`, but the completed scope decides.

## Layers

Use this flow for a full offline-first stack, and omit layers the scope does not
need:

```text
Compose Material 3 UI (state and events only)
  -> ViewModel exposing StateFlow (screen state and logic)
  -> Repository or use case
  -> Local data source: Room, cache, outbox       [offline-first only]
  -> Remote data source: API client
  -> Sync worker: WorkManager                     [offline-first only]
```

- For read-only or non-persistent features, the data layer may be just a
  repository wrapping an API client.
- Add a domain layer between UI and data only when business rules are shared
  across screens or complex enough to need their own boundary.
- Keep UI, data, API, and sync responsibilities separate from the start, even in
  small features. Composables never call APIs, Room, or WorkManager.

Package shape, adjusted to existing names:

```text
data/local       Room entities, DAOs, database, local mappers
data/remote      API interfaces and clients, DTOs, auth headers
data/sync        WorkManager workers, outbox processor, connectivity monitor
data/repository  Repository implementations
domain/model     Domain models when DTO and entity separation helps
ui/<feature>     Compose screens, ViewModels, UI state, UI events
ui/components    Shared Material 3 components
ui/theme         Color, typography, shapes, app theme
```

## Authority

Define which side is authoritative per entity or field rather than describing
the database and the API as competing sources of truth.

- In an offline-first design, higher layers read application data from the local
  source. Network responses are persisted locally before they become observable
  UI state.
- The server may stay authoritative for server-owned fields, permissions,
  canonical identifiers, and conflict resolution.
- Different repositories may use different authority models. Record the choice
  for each entity in the design.

## User-Created and Editable Records

- Generate a stable client ID before saving.
- Save locally first with a sync status such as `PENDING`, `SYNCING`, `SYNCED`,
  `FAILED`, or `CONFLICT`.
- When online, attempt an immediate upload after the local save, or enqueue
  one-time sync work. Correctness depends on the operation's result, not on a
  connectivity check.
- On transient network or server failure, keep the record pending and retry
  later.
- When the server rejects the payload, mark the record `FAILED` or `CONFLICT`
  with a useful error. Never delete the user's local data.
- Use idempotency keys for create, commit, and upload operations so retries do
  not duplicate records. The API must honor them; coordinate that contract with
  the backend design.

## Server-Owned Reference Data

- Fetch from the API when online and cache the result locally.
- Render cached data offline, with a clear stale or offline state where it
  matters to the user.
- Do not block navigation or form entry because a refresh failed.

## Outbox

Prefer an outbox for queued mutations once more than one entity or operation
must sync:

<!-- markdownlint-disable MD013 -->

```text
SyncOperation(
  id,
  entityType,
  entityId,
  operation,
  payloadJson,      // or omit and re-read normalized local data at execution time
  status,
  attempts,
  idempotencyKey,
  lastError,
  createdAt,
  updatedAt
)
```

<!-- markdownlint-enable MD013 -->

Two designs are valid; choose one per feature and record why:

- Snapshot the request payload in `payloadJson` at write time. Behavior is
  predictable and survives later local edits, but stored payloads need a
  migration when the request schema changes.
- Store only entity IDs and re-read current local state at sync time. This is
  simpler to maintain, but the worker must handle an entity that was edited or
  deleted before it ran.

Choose by whether mutation ordering and payload immutability matter for the
feature.

## Background Sync

- Use WorkManager for deferrable work that must survive process death or device
  restart: background sync, periodic refresh, and deferred uploads. Immediate
  calls triggered by user actions run directly; WorkManager is the recovery path
  if the app dies mid-flight.
- Enqueue one-time sync after local writes with a connected-network constraint,
  and use unique work names so repeated saves do not stack duplicate jobs.
- Use exponential backoff for transient network or server failures.
- Add periodic sync only when the product needs background catch-up.
- Keep sync idempotent and safe to resume after app kill, reboot, or partial
  failure.
- When several entities sync with ordering or dependency rules, put the policy
  in a dedicated sync coordinator rather than growing every repository.
- Do not use raw services, timers, or manual polling for deferrable sync.
  Foreground services are heavily restricted on modern Android and need a valid
  declared type; check current platform rules for the target SDK first.

## API Integration

- Keep request and response DTOs aligned with the project's API contract, and
  map them to local or domain models instead of passing network models to the
  UI.
- Define consistent handling for auth refresh, `401` and `403`, validation
  errors, rate limits, and correlation IDs.
- Use bounded pagination and deterministic sorting and filtering for lists.
- Never log bearer tokens, one-time passwords, documents, or sensitive personal
  data.

## Authoritative References

- [Guide to app architecture](https://developer.android.com/topic/architecture)
- [Build an offline-first app](https://developer.android.com/topic/architecture/data-layer/offline-first)
- [WorkManager: getting started](https://developer.android.com/develop/background-work/background-tasks/persistent/getting-started)
- [Room](https://developer.android.com/training/data-storage/room)
