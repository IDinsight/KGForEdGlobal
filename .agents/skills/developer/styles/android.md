# Android Development Style

Apply with `universal.md` for Android, Kotlin, and Jetpack Compose
implementation. Also apply `sql.md` when the change materially modifies Room
queries or migrations.

- Follow the project's established architecture, DI, navigation, and package
  layout. Check the version catalog and Compose BOM before relying on a library
  API.
- For approved dependency changes, use catalog aliases where a catalog exists,
  keep Compose libraries on the BOM, and use the annotation processor the
  installed Room version supports.
- Composables render state and emit events. They never call APIs, databases, or
  WorkManager directly.
- Keep screen state and business logic in a ViewModel or the project's state
  holder, and ephemeral element state such as focus or expansion in the
  composable. Never make an Activity or composable the sole owner of durable
  data.
- Expose one UI state per screen, typically a `StateFlow`. Use a data class when
  conditions coexist, such as cached content with a refresh error; reserve a
  sealed type for genuinely exclusive states.
- Collect flows in UI with lifecycle-aware collection such as
  `collectAsStateWithLifecycle()`.
- Use `rememberSaveable` for small UI state, `SavedStateHandle` for navigation
  arguments and small ViewModel state, and local persistence for drafts that
  must survive process death.
- Map DTOs to domain or UI models; do not expose network models to the UI.
  Handle auth expiry, 401/403, validation, and rate-limit errors through the
  established client path.
- Keep database and network I/O off the main thread; never use
  `allowMainThreadQueries()` in production code.
- When changing Room entities, add a migration and update exported schemas per
  project convention so existing rows survive.
- Treat offline-first behavior, sync or outbox design, background-work strategy,
  and per-entity source of truth as architecture when not already established.
- When the design is offline-first, read from the local source and persist
  network results before they reach UI state. Save user writes locally with a
  stable client ID and sync status before the network call, and decide outcomes
  from the operation result, not a connectivity check.
- Use idempotency keys on retried creates and uploads. On server rejection, mark
  the record `FAILED` or `CONFLICT` with a useful error; never delete local user
  data.
- When the project uses WorkManager, use unique work names so repeated saves do
  not stack jobs, apply the designed constraints and backoff, and keep workers
  idempotent and safe to resume after process death.
- Do not use ad hoc services, timers, or polling for deferrable work; check
  current foreground-service restrictions for the target SDK before using one.
- When data is saved or cached locally, show saved, pending, failed, and stale
  states distinctly. Do not imply a remote save before sync succeeds or block
  form entry because a refresh failed.
- When the project uses Material 3, prefer `androidx.compose.material3`
  components over custom controls. Confirm the component exists in the installed
  version, make experimental opt-ins explicit, and treat new adaptive artifacts
  as dependency decisions.
- Use the app theme's color, typography, shape, and spacing tokens rather than
  hardcoded colors, raw font sizes, or off-scale `.dp` values.
- Consume `Scaffold` padding or window insets once, and order modifiers
  deliberately since order changes measurement and hit area.
- Use lazy containers with stable keys for unbounded or changing lists, never
  nested inside a non-lazy scrolling parent.
- When the app supports larger windows, adapt with window size classes or the
  project's adaptive layouts rather than stretched phone layouts.
- Keep touch targets at least 48dp. Describe meaningful icon-only controls, pass
  `null` for decorative icons, add semantics to custom controls, and do not
  convey status by color alone.
- Use string resources for visible text and content descriptions, and let text
  wrap or truncate intentionally under font scaling.

For Compose and Material 3 component guidance, read
`../references/android-compose-material.md`. For approved dependency and build
changes, read `../references/android-dependencies.md`.
