# Jetpack Compose and Material 3 UI

Read when the project uses Jetpack Compose with Material 3 and the work creates
or changes UI: screens, layout, navigation surfaces, Material components,
spacing, typography, theming, accessibility, or screen-level interaction. It
supplements `../styles/android.md` and never overrides the protocol, the
approved design, repository-enforced configuration, or established project
conventions. When the version catalog, installed version, or official
documentation differs from this file, follow them.

This file is a durable summary, not a replacement for the official docs. It was
last checked against official docs on 2026-06-15. The Material 3 spacing and
type-scale pages render client-side and could not be fully re-fetched on that
date; the token values below were cross-checked against the Android Compose
Material 3 docs and the published baseline type scale. Treat them as baseline
values unless the live Material 3 pages say otherwise.

## Sources and Precedence

Before substantial UI, component, or layout work, check the official sources
listed under [Authoritative References](#authoritative-references) when network
is available. If browsing is unavailable, work from the installed Compose BOM,
the installed Material 3 dependency, and this file, and note in your self-check
evidence that the latest docs were not rechecked.

- The Android Compose docs are the authority for Compose API names, dependency
  artifacts, opt-in annotations, and sample code.
- The Material 3 web catalog is the authority for design intent, component
  anatomy, behavior, and usage guidance.
- Use only the components, type tokens, and APIs available through the module's
  version catalog (for example `gradle/libs.versions.toml`) and Compose BOM,
  unless the approved design changes dependencies.
- Keep the project's existing theme, navigation, and shared-component patterns.
- Use this file as a compact memory aid beneath all of the above.

## Screen State and Events

Composables render state and emit events. They do not call APIs, Room, or
WorkManager directly. State changes flow through the ViewModel or the project's
equivalent state holder.

### One state model per screen

Expose a single UI-state model per screen or navigation destination, typically
as a `StateFlow`. Model it as a flat `data class` with independent fields when
conditions can coexist. A sealed root type forces the UI into exactly one state
at a time; a data class lets orthogonal concerns be true together. A real screen
may need to show cached content, a refresh spinner, an offline banner, a
pending-upload count, and a non-blocking error at once:

```kotlin
data class ScreenUiState(
    val content: ScreenContent? = null, // null = initial load not complete
    val isLoading: Boolean = false,     // initial load or hard refresh
    val isRefreshing: Boolean = false,  // background or pull-to-refresh
    val isOffline: Boolean = false,
    val pendingUploads: Int = 0,
    val transientError: String? = null,
)
```

For transient messages, the composable notifies the ViewModel when the message
has been shown (for example `onErrorDismissed()`), and the ViewModel owns the
state update.

Use a sealed type only for genuinely mutually exclusive phases, such as the
initial page-load arc, and embed it as a field on the outer data class rather
than making it the root type:

```kotlin
sealed interface ContentPhase {
    data object Loading : ContentPhase
    data class Ready(val data: ScreenContent) : ContentPhase
    data class Empty(val reason: EmptyReason) : ContentPhase
}

data class ScreenUiState(
    val contentPhase: ContentPhase = ContentPhase.Loading,
    val isRefreshing: Boolean = false,
    val isOffline: Boolean = false,
    val pendingUploads: Int = 0,
    val transientError: String? = null,
)
```

A sealed root fits only when the whole screen is in one of several exclusive
modes, such as a full-screen auth flow
(`Unauthenticated | Authenticated | Locked`). The common failure is a sealed
root on a content screen that later needs to show a refresh failure alongside
cached content; that needs a data class.

### Collecting state and passing events

Collect flows in Android UI with `collectAsStateWithLifecycle()` from
`androidx.lifecycle.compose` so collection stops when the UI is not visible.
This does not apply in previews, tests, or non-Android Compose targets.

For most screens, pass individual typed lambdas and call ViewModel methods
directly. The ViewModel processes the action and reflects the result as a state
update. Google recommends this as the default; a single sealed event funnel adds
indirection without benefit on typical CRUD screens.

```kotlin
@Composable
fun SearchRoute(viewModel: SearchViewModel) {
    // Lifecycle-aware: stops collecting when the screen leaves STARTED.
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    SearchScreen(
        uiState = uiState,
        onQueryChanged = viewModel::onQueryChanged,
        onSubmit = viewModel::onSubmit,
        onRetry = viewModel::onRetry,
    )
}

@Composable
fun SearchScreen(
    uiState: SearchUiState,
    onQueryChanged: (String) -> Unit,
    onSubmit: () -> Unit,
    onRetry: () -> Unit,
)
```

Typed action objects (a sealed `UiIntent`) earn their keep when the screen has a
genuine state machine, a reducer, complex multi-step event chains, or a need for
event replay or logging. Do not use them as a default structure.

### Where state lives

- Keep ephemeral element state (focus, menu expansion, scroll position,
  animation) in the composable or a plain state holder. Hoist it only when it
  affects siblings, navigation, persistence, API calls, or tests.
- Use `rememberSaveable` for small UI state that should survive configuration
  changes.
- Use `SavedStateHandle` for navigation arguments and small ViewModel state.
- Use local persistence for durable drafts and business records. Never make an
  Activity or composable the sole owner of durable data.

## Choosing Components

### Component workflow

For every non-trivial UI addition or redesign:

- Identify the component role first: action, communication, containment,
  navigation, selection, text input, feedback, or layout.
- Check the Android Compose Material components page and the Material 3 catalog
  for the current recommended component and variants.
- Confirm the component exists in `androidx.compose.material3` at the version
  the app's Compose BOM supplies before importing it.
- Prefer stable APIs. When the recommended component is experimental, needs
  `@OptIn`, or lives in a separate artifact such as
  `material3-adaptive-navigation-suite`, make the opt-in explicit and treat a
  new artifact as a dependency decision (see `android-dependencies.md`). Adding
  one is architecture when the design has not already established it.
- When a component appears in Material docs but not in the installed dependency,
  do not hand-roll it by default. Use the closest supported Material 3
  component, or route the dependency update through the approved design.
- Add or update Compose previews for the screen states that matter: loading,
  empty, populated, error, offline or pending, disabled or loading actions,
  compact width, and a wider layout when the screen adapts.

### Component selection

Prefer `androidx.compose.material3` components over custom UI. Use custom
drawing only for genuinely missing visuals or charts.

- App structure: use `Scaffold` to compose app bars, bottom bars, FABs,
  snackbars, and main content. Pass and consume `innerPadding` once; avoid
  double-padding system bars.
- Adaptive structure: use `NavigationSuiteScaffold` when the Material 3 adaptive
  navigation suite artifact is present or the design adds it. Otherwise use
  `NavigationBar` on compact windows, `NavigationRail` on medium and expanded
  widths, and drawers only when destination count or hierarchy requires them.
- App bars: choose `TopAppBar`, `CenterAlignedTopAppBar`, `MediumTopAppBar`, or
  `LargeTopAppBar` by screen hierarchy and scroll behavior. Keep actions
  icon-first with content descriptions.
- Actions: use `Button`, `FilledTonalButton`, `OutlinedButton`, `TextButton`,
  `IconButton`, `FloatingActionButton`, and `ExtendedFloatingActionButton`
  according to emphasis. Reserve a FAB for one primary screen action.
- Segmented choices: use `SingleChoiceSegmentedButtonRow` or
  `MultiChoiceSegmentedButtonRow` for modes, sorting, or compact mutually
  exclusive choices.
- Chips: use `FilterChip`, `AssistChip`, `InputChip`, and `SuggestionChip` for
  filters, lightweight actions, metadata, suggestions, and entered values. Do
  not build ad hoc pill rows.
- Containment: use `Card`, `ElevatedCard`, `OutlinedCard`, `Surface`,
  `ListItem`, and `HorizontalDivider` for grouped content. Use a card for one
  subject or item; avoid nested cards and decorative page shells.
- Bottom sheets and dialogs: use `ModalBottomSheet` for secondary tasks or
  contextual detail, and `AlertDialog` only for blocking confirmation or
  important prompts. Preserve dismissal, back behavior, and focused actions.
- Search and input: use `SearchBar` or `DockedSearchBar` for search surfaces,
  and `TextField` or `OutlinedTextField` for forms. Prefer the current
  state-based text field APIs when the installed dependency supports them and
  the codebase can adopt them cleanly.
- Selection: use checkbox, radio button, switch, slider, menu, date picker, and
  time picker components for their standard roles instead of custom toggles or
  dialogs.
- Feedback: use `LinearProgressIndicator`, `CircularProgressIndicator`, badges,
  snackbars, and tooltips for status and feedback. Do not represent state with
  color alone.
- Refresh: use Material pull-to-refresh only for user-initiated refresh of
  already visible content. Keep offline and local-save flows explicit rather
  than hiding them behind a refresh gesture.
- Carousels: use carousel components only when horizontal browsing of peer items
  is truly useful. Prefer lists and grids for scannable operational data.
- Icons: use Material icons from the existing icon dependency before writing
  custom vector paths.

### Component categories

The official Compose Material components page groups the catalog into these
buckets. When a feature seems to need a custom control, check them first; most
app UI should be assembled from these components plus layout primitives.

- Actions: buttons, FABs, icon buttons, segmented buttons.
- Communication: badges, progress indicators, snackbars, tooltips.
- Containment: bottom sheets, cards, carousels, dialogs, dividers, lists,
  scaffolds.
- Navigation: app bars, navigation bars, navigation drawers, navigation rails,
  tabs.
- Selection: checkboxes, chips, date pickers, menus, radio buttons, sliders,
  switches, time pickers.
- Text input: search and text fields.

## Spacing

Material 3 spacing is a single linear scale on an 8dp base unit, with 4dp
available for fine adjustments on small elements such as icons. Every gap, pad,
and margin should resolve to a value on this scale. Ad hoc `.dp` literals are
the most common cause of screens that feel too big, too tight, or subtly
inconsistent, so reach for a named step rather than guessing a number.

### Scale and tokens

Material 3 expresses spacing as `space` tokens that follow a multiplier pattern
off the 8dp base: the token number divided by 100 is the multiplier, so
`space225` is 8dp × 2.25 = 18dp.

| Token      | Value | Multiplier | Typical use                                                       |
| ---------- | ----- | ---------- | ----------------------------------------------------------------- |
| `space0`   | 0dp   | 0          | No gap; touching surfaces.                                        |
| `space50`  | 4dp   | 0.5        | Icon-to-label, chip internal padding, fine adjustments.           |
| `space100` | 8dp   | 1          | Default gap between closely related elements in one group.        |
| `space150` | 12dp  | 1.5        | Compact list-row vertical padding, dense inner spacing.           |
| `space200` | 16dp  | 2          | Standard compact-width edge margin; card content padding.         |
| `space300` | 24dp  | 3          | Gap between distinct sections or groups on a screen.              |
| `space400` | 32dp  | 4          | Large section separation; edge margin at the 600dp breakpoint.    |
| `space500` | 40dp  | 5          | Generous separation on larger windows.                            |
| `space600` | 48dp  | 6          | Minimum touch target dimension; large empty-state breathing room. |

When the scale does not list a value you need, stay on the multiplier pattern
(for example `space125` = 10dp) rather than inventing an off-grid number.

### Compose has no built-in spacing token

Unlike color and typography, Compose's `MaterialTheme` does not expose a spacing
field; there is no `MaterialTheme.spacing` out of the box. If the app already
defines a spacing object, use it and match its names rather than introducing a
second scale. Otherwise, define spacing on the theme and read it the same way as
`colorScheme` and `typography`:

```kotlin
// ui/theme/Spacing.kt
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

@Immutable
data class Spacing(
    val none: Dp = 0.dp,    // space0
    val xs: Dp = 4.dp,      // space50
    val sm: Dp = 8.dp,      // space100
    val smPlus: Dp = 12.dp, // space150
    val md: Dp = 16.dp,     // space200
    val lg: Dp = 24.dp,     // space300
    val xl: Dp = 32.dp,     // space400
    val xxl: Dp = 48.dp,    // space600
)

// Static local: spacing rarely changes, so readers are not tracked.
val LocalSpacing = staticCompositionLocalOf { Spacing() }

// Read it like the other theme tokens: MaterialTheme.spacing.md
val MaterialTheme.spacing: Spacing
    @Composable @ReadOnlyComposable get() = LocalSpacing.current
```

Provide it inside the app theme alongside the color scheme and typography, then
use `MaterialTheme.spacing.md` instead of `16.dp`:

```kotlin
@Composable
fun AppTheme(content: @Composable () -> Unit) {
    CompositionLocalProvider(LocalSpacing provides Spacing()) {
        MaterialTheme(
            colorScheme = appColorScheme(),
            typography = AppTypography,
            content = content,
        )
    }
}
```

### Applying spacing

- Use `md` (16dp) screen edge margins on compact width. At the 600dp breakpoint,
  edge margins grow to roughly `xl` (32dp). Let margins scale at breakpoints
  rather than locking phone padding onto larger windows.
- Use proximity to communicate grouping: tight spacing (`sm`, 8dp) ties related
  elements together; generous spacing (`lg` or `xl`, 24 to 32dp) separates
  distinct sections. When output feels too large, the usual cause is
  section-level spacing between items that should read as one group, or the
  reverse.
- Express repeated gaps with `Arrangement.spacedBy(MaterialTheme.spacing.sm)` on
  `Column`, `Row`, and lazy containers instead of a manual `Spacer` between
  every child. Reserve `Spacer` for one-off, intentional gaps.
- Apply container padding through `Modifier.padding(...)` with spacing tokens;
  do not bake whitespace into drawables or empty layered composables.
- Keep touch targets at least 48dp (`xxl`) in both dimensions, with at least
  `sm` (8dp) between adjacent targets, even when the visible element is smaller.
- Order modifiers deliberately. Size, padding, background, clip, click, and
  semantics apply in chain order, which changes measurement and hit area:

  ```kotlin
  // Background fills the padded area; padding insets only the content.
  Modifier.background(color).padding(MaterialTheme.spacing.md)
  // Padding is outside the background; background covers the inner area.
  Modifier.padding(MaterialTheme.spacing.md).background(color)
  // Whole padded area is clickable.
  Modifier.clickable(onClick = onOpen).padding(MaterialTheme.spacing.md)
  ```

- Consume `Scaffold` `innerPadding` (or `WindowInsets`, `statusBarsPadding`,
  `navigationBarsPadding`) once, then apply content spacing on top of it. Do not
  double-pad system bars.

## Typography

Material 3 defines a 15-role type scale. Each role is a token on
`MaterialTheme.typography` with a fixed size, line height, letter spacing, and
weight. Apply roles through the `style` parameter, for example
`style = MaterialTheme.typography.titleMedium`. Do not set raw `fontSize` or
`fontWeight` on `Text` for standard UI; roles keep sizing and weight consistent
and let the configured theme stay authoritative.

### Role families

- Display: the largest text, for short, high-impact strings such as a hero
  number or a single prominent headline. Rare in dense operational UI.
- Headline: high-emphasis page and major-section headings.
- Title: medium-emphasis headings for cards, list groups, dialogs, and app bars.
- Body: the default for paragraphs, descriptions, and most readable content.
- Label: compact, utilitarian text for buttons, chips, badges, tabs, and
  metadata.

Each family has Large, Medium, and Small sizes. Match the role to the container,
not to the size you want: a card heading is `titleMedium` because of its role,
even if `bodyLarge` renders at a similar size.

### Baseline token values

These are the Material 3 baseline (Roboto) values. The app's configured
`Typography` may override family, size, or weight; when it does, the configured
theme wins, and these values describe what each role is meant to convey.

| Token            | Size | Line height | Letter spacing | Baseline weight |
| ---------------- | ---- | ----------- | -------------- | --------------- |
| `displayLarge`   | 57sp | 64sp        | -0.25sp        | Normal (400)    |
| `displayMedium`  | 45sp | 52sp        | 0sp            | Normal (400)    |
| `displaySmall`   | 36sp | 44sp        | 0sp            | Normal (400)    |
| `headlineLarge`  | 32sp | 40sp        | 0sp            | Normal (400)    |
| `headlineMedium` | 28sp | 36sp        | 0sp            | Normal (400)    |
| `headlineSmall`  | 24sp | 32sp        | 0sp            | Normal (400)    |
| `titleLarge`     | 22sp | 28sp        | 0sp            | Normal (400)    |
| `titleMedium`    | 16sp | 24sp        | 0.15sp         | Medium (500)    |
| `titleSmall`     | 14sp | 20sp        | 0.1sp          | Medium (500)    |
| `bodyLarge`      | 16sp | 24sp        | 0.5sp          | Normal (400)    |
| `bodyMedium`     | 14sp | 20sp        | 0.25sp         | Normal (400)    |
| `bodySmall`      | 12sp | 16sp        | 0.4sp          | Normal (400)    |
| `labelLarge`     | 14sp | 20sp        | 0.1sp          | Medium (500)    |
| `labelMedium`    | 12sp | 16sp        | 0.5sp          | Medium (500)    |
| `labelSmall`     | 11sp | 16sp        | 0.5sp          | Medium (500)    |

Weight pattern: display, headline, and `titleLarge` are Normal (400);
`titleMedium`, `titleSmall`, and all label roles are Medium (500); body roles
are Normal (400).

### Weight and emphasis

- Express weight through the role token, not a hardcoded `FontWeight` on `Text`.
  The right role gives the right weight.
- Convey emphasis with color and role rather than bolding body text. Use
  `onSurface` for primary text and `onSurfaceVariant` for secondary or
  supporting text on the same surface.
- Material 3 Expressive adds `*Emphasized` type tokens (for example
  `displayLargeEmphasized` and `titleMediumEmphasized`) as part of the
  experimental Expressive API set. Use them only when the installed
  `androidx.compose.material3` version exposes them, the opt-in is explicit, and
  the heavier treatment suits the product tone. Otherwise stay on the stable
  baseline roles.
- Do not add ad hoc sizes between the defined steps. A screen that seems to need
  an off-scale size usually needs a different role.

### Common mappings

- Screen title in a large or medium top app bar: `headlineSmall` or
  `titleLarge`, depending on hierarchy.
- Card or list-group heading: `titleMedium`.
- List item primary text: `bodyLarge`; supporting line: `bodyMedium` in
  `onSurfaceVariant`.
- Paragraph and form field content: `bodyLarge` or `bodyMedium`.
- Button and tab text: `labelLarge`.
- Chip, badge, timestamp, and other metadata: `labelMedium` or `labelSmall`.
- Empty-state headline: `titleLarge`; empty-state description: `bodyMedium`.

## Layout and Adaptive Behavior

- Compose turns state into UI through composition, layout, and drawing. Keep
  composables small enough that the state-to-layout mapping stays obvious, and
  extract a composable when a block gains its own responsibility.
- Build screens from `Column`, `Row`, `Box`, `LazyColumn`, `LazyRow`, lazy
  grids, pagers, and flow layouts. Nested `Row` and `Column` layouts are fine in
  Compose; keep the hierarchy readable.
- Set `Arrangement` and `Alignment` explicitly. Prefer
  `Arrangement.spacedBy(token)` or `Arrangement.SpaceBetween` over spacer-only
  layouts when they express intent better.
- Use lazy containers for unbounded lists, with stable item keys when items can
  change. Preserve scroll state where appropriate, and never put a large
  unbounded list inside a non-lazy scrolling parent:

  ```kotlin
  Scaffold { innerPadding ->
      LazyColumn(
          // Consume Scaffold insets once; content spacing goes on top.
          contentPadding = innerPadding,
          verticalArrangement = Arrangement.spacedBy(MaterialTheme.spacing.sm),
      ) {
          // Stable keys keep item state attached to the right row.
          items(records, key = { record -> record.id }) { record ->
              RecordRow(record)
          }
      }
  }
  ```

- Give fixed-format UI stable dimensions or aspect ratios (status chips, icon
  buttons, progress bars, list rows, counters, dashboards) so text or state
  changes do not shift layout.
- Prefer responsive constraints over device checks: `BoxWithConstraints`, window
  size classes, adaptive navigation, and canonical list-detail or
  supporting-pane layouts when the flow has enough content.
- When the app supports larger windows (tablets, foldables, ChromeOS, split
  screen), replace layout components or reveal additional panes as the window
  grows rather than stretching the compact phone layout. Use navigation bars on
  compact widths, rails on medium and expanded widths, and drawers or permanent
  navigation only when the information architecture benefits.
- Support font scaling and orientation changes on every screen.

## Theming

- Use the app theme: `MaterialTheme.colorScheme`, `MaterialTheme.typography`,
  `MaterialTheme.shapes`, and the app's spacing tokens. Do not introduce
  feature-specific palettes, sizes, or spacing values unless the approved design
  adds new tokens.
- Use semantic color pairs: `primary`/`onPrimary`,
  `primaryContainer`/`onPrimaryContainer`, `errorContainer`/`onErrorContainer`,
  `surfaceContainer*`/`onSurface`, and so on. Pair every custom background with
  a deliberate content color.
- Use tonal elevation and container roles (`Surface`, `CardDefaults.*Colors`,
  `tonalElevation`) before custom shadows. Avoid custom shadows, gradients, and
  decorative surfaces unless they solve a real hierarchy or state problem.
- Keep contrast readable in light, dark, and dynamic color themes. When the
  theme enables dynamic color, do not override it with feature-local hardcoded
  colors.
- Use `stringResource` for visible text and content descriptions. Hardcoded
  display strings belong only in small previews or temporary debug code.
- Current Android docs include Material 3 Expressive guidance. Use expressive
  components, motion, and typography only when they improve the task, match the
  product tone, and the installed dependency set supports them.

## Interaction and Accessibility

- Provide touch targets of at least 48dp for interactive controls.
- Give meaningful icon-only controls a content description; pass `null` for
  decorative icons.
- Use semantics to describe custom controls, status, selected state, errors,
  headings, and merged list or card content.
- Do not convey status by color alone.
- Support font scaling and screen readers. Type roles use `sp`, so text scales
  with the user's font setting; make long labels wrap or truncate intentionally,
  and keep traversal order consistent with the visual and task order.
- Support keyboard, focus, and back behavior for forms, dialogs, drawers, bottom
  sheets, search, and menus.
- Use `AnimatedVisibility`, `animateContentSize`, and Material motion for state
  changes that would otherwise jump. Keep motion short and purposeful; avoid
  decorative animation.

## Screen and Flow Patterns

- Loading: show progress in the surface where work is happening. Keep global
  blocking loaders rare.
- Empty: provide a styled Material state with a concise `titleLarge` headline,
  useful `bodyMedium` copy, and one clear action when an action exists. Follow
  the app's design system rather than a fixed template, but never ship raw
  unstyled text.
- Error: offer an actionable recovery path. Use inline errors for fields, a
  snackbar for short process feedback, and a dialog only when the user must
  decide before continuing.
- Forms: validate inline and keep destructive or irreversible actions behind
  explicit confirmation. Preserve in-progress values across process death; when
  the design calls for draft persistence (multi-step flows, long text entry,
  attachments), auto-save drafts locally.
- Lists: make filtering, sorting, selection, and item status visible with
  standard components rather than invented controls.
- Search: keep query state stable across configuration changes and expose clear
  and submit affordances where appropriate.
- Offline and sync status, when the design saves or caches data locally:
  distinguish "saved on this device", "pending sync", "sync failed", and "server
  data stale". Never imply a remote save before sync succeeds. Use calm,
  explicit copy such as "Saved on this device" or "Will sync when online", and
  give sync errors a clear retry path. Do not block navigation or form entry
  because a refresh failed.

## Self-Check Before Handoff

- Standard controls use current Material 3 components rather than homemade
  equivalents.
- The latest Compose and Material 3 docs were checked when network was
  available, or the self-check evidence says they were not.
- Any new artifact, experimental API, or adaptive Material dependency is
  established by the approved design and compatible with the Compose BOM.
- Spacing comes from the app's tokens on the 8dp scale, with no raw `.dp`
  padding or margin literals other than 0. Compact edge padding is `md` (16dp),
  `sm` (8dp) groups related items, and `lg` or `xl` (24 or 32dp) separates
  sections. Repeated gaps use `Arrangement.spacedBy(token)`.
- All text uses `MaterialTheme.typography` role tokens matched to their
  container, with no hardcoded `fontSize` or `fontWeight` on standard UI and no
  off-scale sizes. Emphasis comes from role and color, not ad hoc bolding.
- Colors, shapes, and elevation come from `MaterialTheme` and the app's token
  objects.
- The layout consumes Scaffold padding once and handles small screens, larger
  windows the app supports, and font scaling.
- Lists use lazy containers with stable keys where items can change.
- Empty, loading, and error states are styled and actionable.
- Interactive elements have 48dp targets, semantics, and visible enabled,
  disabled, and loading states.
- Data flow stays unidirectional: UI to event, to ViewModel, to repository or
  data layer.
- Previews exist for the states most likely to regress: loading, empty,
  populated, error, offline or pending, and disabled or loading actions.

## Authoritative References

- [Android Compose documentation](https://developer.android.com/develop/ui/compose/documentation)
- [Android Compose Material components](https://developer.android.com/develop/ui/compose/components)
- [Compose layout basics](https://developer.android.com/develop/ui/compose/layouts/basics)
- [Get started with adaptive apps](https://developer.android.com/develop/ui/compose/layouts/adaptive/get-started-with-adaptive-apps)
- [Build adaptive navigation](https://developer.android.com/develop/ui/compose/layouts/adaptive/build-adaptive-navigation)
- [Material 3 in Compose](https://developer.android.com/develop/ui/compose/designsystems/material3)
- [Material 3 component catalog](https://m3.material.io/components)
- [Material 3 spacing system](https://m3.material.io/foundations/layout/understanding-layout/spacing)
- [Material 3 applying spacing](https://m3.material.io/styles/spacing/applying-spacing)
- [Material 3 type scale and tokens](https://m3.material.io/styles/typography/type-scale-tokens)
- [Material 3 applying typography](https://m3.material.io/styles/typography/applying-type)
- [State and Jetpack Compose](https://developer.android.com/develop/ui/compose/state)
- [`collectAsStateWithLifecycle`](https://developer.android.com/reference/kotlin/androidx/lifecycle/compose/collectAsStateWithLifecycle.composable)
- [SavedStateHandle guide](https://developer.android.com/topic/libraries/architecture/viewmodel/viewmodel-savedstate)
- [Accessibility in Compose](https://developer.android.com/develop/ui/compose/accessibility)
- [Compose BOM mapping](https://developer.android.com/develop/ui/compose/bom/bom-mapping)
