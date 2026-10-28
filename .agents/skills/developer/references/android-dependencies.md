# Android Dependencies and Build Configuration

Read when Android work adds, upgrades, or reconfigures a library, Gradle plugin,
annotation processor, or version-catalog entry. It supplements
`../styles/android.md` and never overrides the protocol, the approved design,
repository-enforced configuration, or established project conventions. When the
version catalog, installed version, or official documentation differs from this
file, follow them.

## Who Decides

- Introducing a library, plugin, or framework the project does not already use
  is a consequential choice. Treat it as architecture when the approved design
  has not already established it, and do not add it on your own judgment.
- Adding a companion artifact from a library family the design already selects
  is an implementation detail, for example `room-ktx` alongside an established
  Room runtime, or a Compose artifact supplied by the existing BOM.
- A major-version upgrade, a change of annotation processor, or a change that
  alters minimum SDK, JDK, or Kotlin requirements is consequential; route it
  through the design when it is not already established.
- Test-only dependencies (`room-testing`, `work-testing`, Compose
  `ui-test-junit4`, `kotlinx-coroutines-test`, and similar) are Tester-owned
  test configuration. Do not add or change them as part of implementation.

## Dependency Rules

- Use the project's existing dependency-management approach. When a version
  catalog exists (typically `gradle/libs.versions.toml`), add entries there and
  reference aliases from the build script rather than pasting version strings
  inline. Prefer a catalog for new projects, but do not introduce one solely for
  a single small dependency change.
- Reuse versions already in the catalog. When the design adds a library, choose
  a version compatible with the current Android Gradle Plugin (AGP), Kotlin,
  Compose BOM, compile SDK, and min SDK.
- Keep Compose library versions managed by the Compose BOM. Declare Compose
  artifacts without versions and let the BOM align them.
- Prefer KSP over KAPT for Room annotation processing in Kotlin projects. Room
  3.0 removes KAPT and Java annotation-processor support entirely, so KSP is the
  forward-compatible choice. Use KAPT only when the installed Room version or
  the project's existing toolchain requires it. Room 3.0 is a new major line;
  check its release notes for coordinate and package changes before moving an
  existing project to it.
- Do not paste dependency blocks from memory, blog posts, or older projects into
  `build.gradle.kts`. Adapt them to the catalog and to versions verified against
  official release notes.

### Catalog shape

Versions below are placeholders; take real values from the catalog or the
official release notes.

<!-- markdownlint-disable MD013 -->

```toml
# gradle/libs.versions.toml
[versions]
composeBom = "[VERSION]"
room = "[VERSION]"
ksp = "[VERSION]"   # must be compatible with the project's Kotlin version

[libraries]
androidx-compose-bom = { group = "androidx.compose", name = "compose-bom", version.ref = "composeBom" }
# No version: the BOM supplies it.
androidx-compose-material3 = { group = "androidx.compose.material3", name = "material3" }
androidx-room-runtime = { group = "androidx.room", name = "room-runtime", version.ref = "room" }
androidx-room-ktx = { group = "androidx.room", name = "room-ktx", version.ref = "room" }
androidx-room-compiler = { group = "androidx.room", name = "room-compiler", version.ref = "room" }

[plugins]
ksp = { id = "com.google.devtools.ksp", version.ref = "ksp" }
room = { id = "androidx.room", version.ref = "room" }
```

<!-- markdownlint-enable MD013 -->

```kotlin
// app/build.gradle.kts
plugins {
    alias(libs.plugins.ksp)
    alias(libs.plugins.room)
}

room {
    // Exported schemas back Room migrations; commit this directory.
    schemaDirectory("$projectDir/schemas")
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.room.runtime)
    implementation(libs.androidx.room.ktx)
    ksp(libs.androidx.room.compiler) // KSP, not kapt
}
```

### Compatibility notes

- Kotlin 2.0 and later ship the Compose compiler as the Kotlin Gradle plugin
  `org.jetbrains.kotlin.plugin.compose`, versioned with Kotlin. Do not pin a
  separate `kotlinCompilerExtensionVersion` on those projects. Older projects
  keep the Compose-compiler-to-Kotlin mapping; check it before upgrading either
  side.
- Choose a KSP release that supports the project's Kotlin version. Older KSP
  versions encode the Kotlin version in their version string.
- Use the JDK version the Android Gradle Plugin requires (JDK 17 for AGP 8.x) or
  the project's configured Gradle toolchain.
- New Material 3 adaptive artifacts (for example
  `material3-adaptive-navigation-suite`) are separate dependencies, not part of
  the core `material3` artifact. Check whether the BOM manages them before
  adding a version.
- When a library needs code-shrinker rules (R8 or ProGuard), prefer the consumer
  rules it ships. Add project rules only for reflection the library cannot
  describe, such as reflective JSON models.

## Likely Dependency Groups

Check the project's established stack before adding anything. For an existing
project, use these only when the project already uses them or the approved
design selects them. For a new project, they are the default-stack choices the
design is likely to select.

- Room: local entities, DAOs, migrations, and pending-sync queries.
- WorkManager: one-time and periodic background work that must survive process
  death.
- Lifecycle Compose (`lifecycle-runtime-compose`): lifecycle-aware state
  collection from ViewModels.
- Navigation Compose: navigation across multiple screens.
- Retrofit with OkHttp, Ktor, or another agreed HTTP client: API calls.
- Kotlin serialization, Moshi, or Gson: JSON mapping that matches the chosen
  HTTP client. Use one; do not register duplicate converters.
- DataStore: small durable preferences such as auth metadata or a last-sync
  timestamp.
- Hilt or another DI approach: wiring repositories, API clients, DAOs, and
  workers once construction becomes non-trivial. When the project uses Hilt with
  WorkManager, workers use `@HiltWorker` with `@AssistedInject`, the
  `Application` provides `HiltWorkerFactory` through `Configuration.Provider`,
  and the default WorkManager initializer is removed from the manifest, as the
  Hilt docs describe.

## After Changing a Dependency

1. Sync Gradle and resolve the dependency graph.
2. Compile the affected module, for example `./gradlew :app:assembleDebug`.
3. Run lint and the module's existing relevant tests as implementation
   self-checks, for example `./gradlew :app:lintDebug :app:testDebugUnitTest`.
   These results do not replace Tester verification.
4. Inspect the resolved graph for duplicate JSON converters, incompatible
   compiler plugins, version conflicts, and unnecessary transitive libraries:

   ```sh
   # Full runtime graph for one variant.
   ./gradlew :app:dependencies --configuration releaseRuntimeClasspath
   # Why a specific module resolved to its version.
   ./gradlew :app:dependencyInsight \
     --dependency okhttp --configuration releaseRuntimeClasspath
   ```

5. Report the added or changed coordinates and versions in your self-check
   evidence.

## Authoritative References

- [Migrate your build to version catalogs](https://developer.android.com/build/migrate-to-catalogs)
- [Compose BOM mapping](https://developer.android.com/develop/ui/compose/bom/bom-mapping)
- [Compose compiler Gradle plugin](https://developer.android.com/develop/ui/compose/compiler)
- [Room 3.0 release notes (KSP-only)](https://developer.android.com/jetpack/androidx/releases/room3)
- [Migrate from KAPT to KSP](https://developer.android.com/build/migrate-to-ksp)
- [Migrate your Room database](https://developer.android.com/training/data-storage/room/migrating-db-versions)
- [WorkManager: getting started](https://developer.android.com/develop/background-work/background-tasks/persistent/getting-started)
- [Dependency injection with Hilt](https://developer.android.com/training/dependency-injection/hilt-android)
